"""L12: actividad del álbum + notificaciones in-app, sin base de datos.

Las reglas puras (metadatos por evento, retención, preferencias) y la forma
del código que garantiza lo transaccional: cada evento se escribe DENTRO de la
transacción de la operación que lo causa, nunca después de confirmarla. El
comportamiento contra PostgreSQL real está en `test_activity_notifications_db.py`.
"""
import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def _function(path: Path, name: str) -> ast.FunctionDef:
    arbol = ast.parse(path.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == name:
            return nodo
    raise AssertionError(f"{name} no existe en {path.name}")


def _calls(nodo) -> list[str]:
    nombres = []
    for sub in ast.walk(nodo):
        if isinstance(sub, ast.Call):
            f = sub.func
            nombres.append(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "")
    return nombres


def _calls_inside_db_transaction(funcion: ast.FunctionDef) -> set[str]:
    """Nombres llamados dentro de algún `with db_conn() as conn:`."""
    dentro = set()
    for nodo in ast.walk(funcion):
        if isinstance(nodo, ast.With) and any(
            isinstance(item.context_expr, ast.Call) and getattr(item.context_expr.func, "id", "") == "db_conn"
            for item in nodo.items
        ):
            dentro.update(_calls(nodo))
    return dentro


class ActivityMetadataTests(unittest.TestCase):
    def test_the_event_allowlist_is_exactly_the_seven_l12_events(self):
        from app.activity import ACTIVITY_EVENTS

        self.assertEqual({
            "album_invite_created", "share_claimed", "asset_uploaded", "asset_added",
            "asset_removed", "cover_changed", "share_permission_changed",
        }, set(ACTIVITY_EVENTS))

    def test_invite_and_permission_metadata_keep_only_ids_and_normalized_enums(self):
        from app.activity import normalize_activity_metadata

        for evento in ("album_invite_created", "share_permission_changed"):
            with self.subTest(evento):
                self.assertEqual(
                    {"target_user_id": 7, "permission": "write", "capabilities": ["upload", "organize"]},
                    normalize_activity_metadata(evento, {
                        "target_user_id": 7, "permission": "write",
                        "capabilities": ["organize", "upload", "organize"],
                    }),
                )
                # Una invitación de enlace de cuenta todavía no tiene destinatario.
                self.assertEqual(
                    {"target_user_id": None, "permission": "read", "capabilities": []},
                    normalize_activity_metadata(evento, {"target_user_id": None, "permission": "read",
                                                         "capabilities": []}),
                )

    def test_cover_metadata_is_only_the_cleared_flag(self):
        from app.activity import normalize_activity_metadata

        self.assertEqual({"cleared": True}, normalize_activity_metadata("cover_changed", {"cleared": True}))
        self.assertEqual({}, normalize_activity_metadata("cover_changed", {}))
        for evento in ("share_claimed", "asset_uploaded", "asset_added", "asset_removed"):
            with self.subTest(evento):
                self.assertEqual({}, normalize_activity_metadata(evento, None))

    def test_anything_outside_the_per_event_schema_is_refused(self):
        from app.activity import normalize_activity_metadata

        hostiles = [
            ("event_from_the_browser", {}),
            ("asset_uploaded", {"caption": "privado"}),
            ("asset_uploaded", {"storage_path": "u/1/a.jpg"}),
            ("album_invite_created", {"target_user_id": 1, "permission": "write",
                                      "capabilities": ["upload"], "token": "secreto"}),
            ("album_invite_created", {"target_user_id": True, "permission": "read", "capabilities": []}),
            ("album_invite_created", {"target_user_id": "7", "permission": "read", "capabilities": []}),
            ("album_invite_created", {"target_user_id": 7, "permission": "owner", "capabilities": []}),
            ("album_invite_created", {"target_user_id": 7, "permission": "write", "capabilities": ["delete_album"]}),
            ("album_invite_created", {"target_user_id": 7, "permission": "write"}),
            ("cover_changed", {"cleared": "yes"}),
            ("cover_changed", {"cleared": False}),
            ("share_claimed", {"password": "x"}),
        ]
        for evento, metadatos in hostiles:
            with self.subTest(evento=evento, metadatos=metadatos), self.assertRaises(ValueError):
                normalize_activity_metadata(evento, metadatos)


class NotificationCategoryTests(unittest.TestCase):
    def test_three_categories_each_backed_by_one_preference_column(self):
        from app.notifications import DEFAULTS, NOTIFICATION_EVENTS

        self.assertEqual({
            "album_invite": "notify_album_invites",
            "share_claimed": "notify_share_claimed",
            "shared_album_upload": "notify_shared_album_uploads",
        }, NOTIFICATION_EVENTS)
        # Sin interruptor maestro: sin fila de preferencias, todo llega.
        self.assertEqual({"notify_album_invites": True, "notify_share_claimed": True,
                          "notify_shared_album_uploads": True}, DEFAULTS)

    def test_an_unknown_category_never_reaches_sql(self):
        from app import notifications

        with patch.object(notifications, "execute_safe") as execute:
            with self.assertRaises(ValueError):
                notifications.notify(object(), 1, "browser_push", album_id=1, actor_user_id=2)
        execute.assert_not_called()


class RetentionConfigTests(unittest.TestCase):
    def test_defaults_are_90_days_for_notifications_and_365_for_activity(self):
        from app.notifications import retention_days

        with patch.dict(os.environ, {"NOTIFICATION_RETENTION_DAYS": "", "ALBUM_ACTIVITY_RETENTION_DAYS": ""}):
            self.assertEqual((90, 365), retention_days())
        with patch.dict(os.environ, {"NOTIFICATION_RETENTION_DAYS": "30", "ALBUM_ACTIVITY_RETENTION_DAYS": "730"}):
            self.assertEqual((30, 730), retention_days())

    def test_a_retention_that_would_purge_everything_or_never_fails_closed(self):
        from app.notifications import retention_days

        for valor in ("0", "-1", "3651", "abc", "1.5"):
            for nombre in ("NOTIFICATION_RETENTION_DAYS", "ALBUM_ACTIVITY_RETENTION_DAYS"):
                with self.subTest(nombre=nombre, valor=valor), patch.dict(os.environ, {nombre: valor}):
                    with self.assertRaises(RuntimeError):
                        retention_days()


class TransactionalHookShapeTests(unittest.TestCase):
    """Un evento después del commit sería un evento fantasma si el commit falla,
    o uno perdido si el proceso muere entre los dos pasos. Todos van dentro."""

    def test_invite_claim_and_permission_events_are_written_inside_the_share_transaction(self):
        shares = APP / "api/shares.py"
        for funcion, esperado in (
            ("create_album_share", {"record_activity", "notify"}),
            ("claim_account_share", {"record_activity", "notify"}),
            ("update_share_permission", {"record_activity"}),
        ):
            with self.subTest(funcion):
                self.assertLessEqual(esperado, _calls_inside_db_transaction(_function(shares, funcion)))

    def test_upload_events_are_written_inside_the_asset_transaction_not_after_commit(self):
        crear = _function(APP / "api/media.py", "create_media")
        dentro = _calls_inside_db_transaction(crear)
        self.assertIn("record_activity", dentro)
        self.assertIn("notify_album_upload", dentro)
        # Nada de avisos detrás de `mark_committed`: ese era el patrón retirado.
        fuente = ast.unparse(crear)
        despues = fuente.split("mark_committed(stored['storage_operation_id'])", 1)[1]
        self.assertNotIn("notify", despues)
        self.assertNotIn("activity", despues)

    def test_membership_and_cover_events_are_written_inside_the_album_transaction(self):
        albums = APP / "api/albums.py"
        for funcion in ("add_album_asset", "remove_album_asset", "update_album"):
            with self.subTest(funcion):
                self.assertIn("record_activity", _calls_inside_db_transaction(_function(albums, funcion)))

    def test_event_helpers_never_open_their_own_transaction(self):
        for modulo in ("activity.py", "notifications.py"):
            with self.subTest(modulo):
                texto = (APP / modulo).read_text(encoding="utf-8")
                self.assertNotIn("db_conn", texto)
                self.assertNotIn(".commit(", texto)

    def test_smart_albums_emit_no_activity_and_no_notifications(self):
        texto = (APP / "api/smart_albums.py").read_text(encoding="utf-8")
        for token in ("record_activity", "notify", "album_activity", "notifications"):
            self.assertNotIn(token, texto)


class InAppOnlyTests(unittest.TestCase):
    def test_the_old_post_commit_hooks_are_gone(self):
        # `send_push`/`web_push_enabled` los vigila test_external_push_provider_removed.
        for archivo in APP.rglob("*.py"):
            texto = archivo.read_text(encoding="utf-8")
            for token in ("send_album_invited", "send_share_claimed", "send_album_upload",
                          "album_upload_notification_targets", "should_notify_"):
                with self.subTest(archivo=archivo.name, token=token):
                    self.assertNotIn(token, texto)

    def test_the_purge_is_a_cli_command_that_touches_no_media(self):
        from app import cli

        self.assertIn("purge-expired-activity-notifications", cli.COMMANDS)
        fuente = ast.unparse(_function(APP / "cli.py", "purge_expired_activity_notifications"))
        for token in ("storage", "remove_media_files", "unlink", "requests", "urlopen"):
            self.assertNotIn(token, fuente)


if __name__ == "__main__":
    unittest.main()
