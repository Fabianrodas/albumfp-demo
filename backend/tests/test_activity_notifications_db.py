"""L12 contra PostgreSQL de verdad: migración 0026, actividad y notificaciones in-app.

Reutiliza el arnés de L10A (`test_asset_membership_db.py`): cada clase clona la
plantilla DESECHABLE de `ALBUMFP_L10A_TEMPLATE_DB` (en 0023), siembra, migra
con Alembic en un subproceso hasta head (0024 -> 0025 -> 0026) y borra el clon.
Nunca abre la base de `.env`. Sin la variable se omite.

Fixtures del arnés: OWNER tiene A (privado; COLLAB colabora con `organize`),
B (privado) y P (público, con enlace público); OTHER tiene X; STRANGER no tiene
nada. Nadie tiene fila de preferencias salvo que la prueba la cree.
"""
import io
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

try:
    from tests.test_asset_membership_db import (
        ALBUM_A, ALBUM_B, ALBUM_P, COLLAB, OTHER, OWNER, STRANGER,
        Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023, small_jpeg,
    )
except ImportError:  # discover -s tests importa los módulos sin el paquete
    from test_asset_membership_db import (
        ALBUM_A, ALBUM_B, ALBUM_P, COLLAB, OTHER, OWNER, STRANGER,
        Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023, small_jpeg,
    )

# Las pruebas de migración de L12 fijan su propia revisión: desde L14 head es
# 0027, y la evidencia de 0026 no debe depender de lo que venga después.
HEAD = "0026_activity_notifications"
PREVIOUS = "0025_smart_albums"
COLLAB_SHARE = 900401
PUBLIC_SHARE = 900402
TABLES = ("album_activity", "notifications", "user_notification_preferences")


@unittest.skipIf(_skip_reason(), _skip_reason())
class ActivityNotificationMigrationTests(unittest.TestCase):
    """0025 -> 0026: las tablas, las preferencias sin push y la bajada que se niega."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch("l12mig")
        try:
            seed_0023(cls.scratch)
            # Preferencias con el significado viejo: push activado, dos categorías apagadas.
            cls.scratch.execute(
                """INSERT INTO user_notification_preferences
                   (user_id, web_push_enabled, notify_album_invites, notify_share_claimed, notify_shared_album_uploads)
                   VALUES (:c, TRUE, FALSE, TRUE, FALSE), (:s, FALSE, TRUE, FALSE, TRUE)""",
                {"c": COLLAB, "s": STRANGER})
            for target in (PREVIOUS, HEAD):
                resultado = cls.scratch.alembic("upgrade", target)
                if resultado.returncode != 0:
                    raise AssertionError(f"upgrade {target}:\n{resultado.stdout}\n{resultado.stderr}")
        except Exception:
            cls.scratch.drop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.scratch.drop()

    def columns(self, table: str) -> dict:
        return {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in self.scratch.rows(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = :t",
            {"t": table})}

    def prefs(self) -> dict:
        return {r["user_id"]: (r["notify_album_invites"], r["notify_share_claimed"], r["notify_shared_album_uploads"])
                for r in self.scratch.rows("SELECT * FROM user_notification_preferences WHERE user_id IN (:c, :s)",
                                           {"c": COLLAB, "s": STRANGER})}

    def test_1_head_is_0026_with_both_tables_empty(self):
        self.assertEqual(HEAD, self.scratch.version())
        self.assertEqual({
            "id": ("bigint", "NO"), "album_id": ("integer", "NO"), "actor_user_id": ("integer", "YES"),
            "event_type": ("character varying", "NO"), "subject_asset_id": ("integer", "YES"),
            "metadata_json": ("jsonb", "NO"), "created_at": ("timestamp without time zone", "NO"),
        }, self.columns("album_activity"))
        self.assertEqual({
            "id": ("bigint", "NO"), "user_id": ("integer", "NO"), "event_type": ("character varying", "NO"),
            "album_id": ("integer", "YES"), "actor_user_id": ("integer", "YES"),
            "read_at": ("timestamp without time zone", "YES"), "created_at": ("timestamp without time zone", "NO"),
        }, self.columns("notifications"))
        for tabla in ("album_activity", "notifications"):
            self.assertEqual(0, self.scratch.scalar(f"SELECT count(*) FROM {tabla}"))

    def test_2_preferences_lose_push_and_keep_every_category_value(self):
        self.assertNotIn("web_push_enabled", self.columns("user_notification_preferences"))
        self.assertEqual({COLLAB: (False, True, False), STRANGER: (True, False, True)}, self.prefs())

    def test_3_the_database_refuses_unknown_events_and_non_object_metadata(self):
        malos = [
            ("INSERT INTO album_activity (album_id, event_type) VALUES (:a, 'browser_push')", {"a": ALBUM_A}),
            ("INSERT INTO album_activity (album_id, event_type, metadata_json) VALUES (:a, 'asset_added', '[]')",
             {"a": ALBUM_A}),
            ("INSERT INTO notifications (user_id, event_type) VALUES (:u, 'asset_added')", {"u": OWNER}),
        ]
        for sql, params in malos:
            with self.subTest(sql), self.assertRaises(Exception):
                self.scratch.execute(sql, params)

    def test_4_foreign_keys_keep_history_valid_when_things_disappear(self):
        s = self.scratch
        s.execute("INSERT INTO users (id, username, full_name, password_hash) VALUES (900009, 'l12_gone', 'Gone', 'x')")
        s.execute("INSERT INTO albums (id, user_id, titulo, is_private, active) VALUES (900109, :o, 'L12 tmp', TRUE, TRUE)",
                  {"o": OWNER})
        s.execute("""INSERT INTO album_activity (album_id, actor_user_id, event_type, subject_asset_id)
                     VALUES (:a, 900009, 'asset_added', 900205), (900109, :o, 'asset_added', NULL)""",
                  {"a": ALBUM_A, "o": OWNER})
        s.execute("""INSERT INTO notifications (user_id, event_type, album_id, actor_user_id)
                     VALUES (:o, 'share_claimed', 900109, 900009), (900009, 'album_invite', :a, :o)""",
                  {"o": OWNER, "a": ALBUM_A})
        try:
            s.execute("DELETE FROM users WHERE id = 900009")          # actor/destinatario borrado
            s.execute("DELETE FROM albums WHERE id = 900109")         # álbum borrado
            s.execute("DELETE FROM media_tags WHERE media_id = 900205")
            s.execute("DELETE FROM assets WHERE id = 900205")         # asset borrado
            self.assertEqual([(ALBUM_A, None, None)], [
                (r["album_id"], r["actor_user_id"], r["subject_asset_id"])
                for r in s.rows("SELECT * FROM album_activity")])
            self.assertEqual([(OWNER, None, None)], [
                (r["user_id"], r["album_id"], r["actor_user_id"]) for r in s.rows("SELECT * FROM notifications")])
        finally:
            s.execute("DELETE FROM album_activity")
            s.execute("DELETE FROM notifications")

    def test_5_downgrade_refuses_with_history_then_round_trips_when_empty(self):
        s = self.scratch
        for sql, tabla in (
            ("INSERT INTO album_activity (album_id, event_type) VALUES (:a, 'asset_added')", "album_activity"),
            ("INSERT INTO notifications (user_id, event_type) VALUES (:o, 'album_invite')", "notifications"),
        ):
            with self.subTest(tabla):
                s.execute(sql, {"a": ALBUM_A, "o": OWNER})
                bajada = s.alembic("downgrade", PREVIOUS)
                self.assertNotEqual(0, bajada.returncode)
                self.assertIn(tabla, bajada.stdout + bajada.stderr)
                self.assertEqual(HEAD, s.version())
                self.assertEqual(1, s.scalar(f"SELECT count(*) FROM {tabla}"), "no perdió el historial")
                s.execute(f"DELETE FROM {tabla}")

        bajada = s.alembic("downgrade", PREVIOUS)
        self.assertEqual(0, bajada.returncode, bajada.stderr)
        self.assertEqual(PREVIOUS, s.version())
        self.assertIsNone(s.scalar("SELECT to_regclass('album_activity')"))
        self.assertIsNone(s.scalar("SELECT to_regclass('notifications')"))
        self.assertEqual(("boolean", "NO"), self.columns("user_notification_preferences")["web_push_enabled"])
        self.assertEqual(0, s.scalar("SELECT count(*) FROM user_notification_preferences WHERE web_push_enabled"))
        self.assertEqual({COLLAB: (False, True, False), STRANGER: (True, False, True)}, self.prefs())

        subida = s.alembic("upgrade", HEAD)
        self.assertEqual(0, subida.returncode, subida.stderr)
        self.assertEqual(HEAD, s.version())
        self.assertNotIn("web_push_enabled", self.columns("user_notification_preferences"))

    CATALOG = {
        "columns": """SELECT table_name, column_name, data_type, is_nullable, COALESCE(column_default, '')
                      FROM information_schema.columns WHERE table_name = ANY(:t)""",
        "constraints": """SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint
                          WHERE conrelid::regclass::text = ANY(:t)""",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE tablename = ANY(:t)",
    }

    def test_6_schema_sql_and_the_migration_chain_agree(self):
        from pathlib import Path

        from sqlalchemy import create_engine, text
        from app.db import db as appdb

        fresh_name = f"{self.scratch.name}_fresh"
        admin = admin_database_engine(appdb)
        try:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
                c.execute(text(
                    f'CREATE DATABASE "{fresh_name}" WITH TEMPLATE template0 OWNER "albumfp_demo"'
                ))
            fresh = create_engine(demo_database_url(appdb, fresh_name))
            sql = (Path(__file__).resolve().parents[1] / "schemas/schema.sql").read_text(encoding="utf-8")
            with fresh.begin() as c:
                c.exec_driver_sql(sql)
            for nombre, consulta in self.CATALOG.items():
                with self.subTest(nombre):
                    def catalogo(engine):
                        with engine.connect() as c:
                            filas = c.execute(text(consulta), {"t": list(TABLES)}).all()
                            return sorted(tuple(str(v).strip() for v in r) for r in filas)
                    self.assertEqual(catalogo(self.scratch.engine), catalogo(fresh))
            fresh.dispose()
        finally:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
            admin.dispose()


@unittest.skipIf(_skip_reason(), _skip_reason())
class ActivityNotificationApiTests(_AppCase):
    label = "l12"

    # --- helpers ---------------------------------------------------------
    def setUp(self):
        self.addCleanup(self.scratch.execute, "DELETE FROM album_activity")
        self.addCleanup(self.scratch.execute, "DELETE FROM notifications")
        self.addCleanup(self.scratch.execute, "DELETE FROM user_notification_preferences")

    def activity(self, album: int | None = None) -> list[dict]:
        where = "WHERE album_id = :a" if album else ""
        return self.scratch.rows(
            f"SELECT album_id, actor_user_id, event_type, subject_asset_id, metadata_json FROM album_activity {where} ORDER BY id",
            {"a": album})

    def notifications_of(self, user: int) -> list[dict]:
        return self.scratch.rows(
            "SELECT event_type, album_id, actor_user_id, read_at FROM notifications WHERE user_id = :u ORDER BY id",
            {"u": user})

    def counts(self) -> tuple[int, int]:
        return (self.scratch.scalar("SELECT count(*) FROM album_activity"),
                self.scratch.scalar("SELECT count(*) FROM notifications"))

    def set_pref(self, user: int, **values):
        respuesta = self.call(user, "patch", "/api/notifications/preferences", json=values)
        self.assertEqual(200, respuesta.status_code, respuesta.get_json())

    def share_with(self, album: int, user: int, permission="read", capabilities=()):
        respuesta = self.call(OWNER, "post", f"/api/albums/{album}/shares", json={
            "shared_with_user_id": user, "permission": permission, "capabilities": list(capabilities)})
        self.assertEqual(201, respuesta.status_code, respuesta.get_json())
        share_id = respuesta.get_json()["data"]["id"]
        self.addCleanup(self.scratch.execute, "DELETE FROM album_shares WHERE id = :id", {"id": share_id})
        return share_id

    def invite_link(self, album: int) -> str:
        respuesta = self.call(OWNER, "post", f"/api/albums/{album}/shares", json={
            "create_link": True, "share_type": "account", "permission": "write", "capabilities": ["upload"]})
        self.assertEqual(201, respuesta.status_code, respuesta.get_json())
        data = respuesta.get_json()["data"]
        # Reclamar actualiza esa misma fila (o la desactiva y fusiona en el
        # acceso que ya existía): basta con borrar exactamente esa.
        self.addCleanup(self.scratch.execute, "DELETE FROM album_shares WHERE id = :id", {"id": data["id"]})
        return data["token"]

    def upload(self, user: int, album: int, color, **form):
        respuesta = self.call(user, "post", f"/api/albums/{album}/media",
                              data={"file": (io.BytesIO(small_jpeg(color)), "l12.jpg", "image/jpeg"), **form},
                              content_type="multipart/form-data")
        if respuesta.status_code == 201:
            asset_id = respuesta.get_json()["data"]["id"]
            self.addCleanup(self._forget_asset, asset_id)
        return respuesta

    def _forget_asset(self, asset_id: int):
        self.scratch.execute("UPDATE albums SET cover_media_id = NULL WHERE cover_media_id = :id", {"id": asset_id})
        for tabla in ("album_assets", "media_metadata", "media_exif", "media_tags", "media_context"):
            columna = "asset_id" if tabla == "album_assets" else "media_id"
            self.scratch.execute(f"DELETE FROM {tabla} WHERE {columna} = :id", {"id": asset_id})
        self.scratch.execute("DELETE FROM assets WHERE id = :id", {"id": asset_id})

    # --- preferencias ----------------------------------------------------
    def test_preferences_default_to_every_category_and_patch_partially(self):
        respuesta = self.call(STRANGER, "get", "/api/notifications/preferences")
        self.assertEqual(200, respuesta.status_code)
        self.assertEqual({"notify_album_invites": True, "notify_share_claimed": True,
                          "notify_shared_album_uploads": True}, respuesta.get_json()["data"])

        self.set_pref(STRANGER, notify_share_claimed=False)
        releida = self.call(STRANGER, "get", "/api/notifications/preferences").get_json()["data"]
        self.assertEqual({"notify_album_invites": True, "notify_share_claimed": False,
                          "notify_shared_album_uploads": True}, releida)
        self.assertNotIn("web_push_enabled", json.dumps(releida))

        for cuerpo in ({"web_push_enabled": True}, {"is_admin": True}, {"notify_album_invites": "false"},
                       {"notify_album_invites": 0}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(400, self.call(STRANGER, "patch", "/api/notifications/preferences",
                                                json=cuerpo).status_code)
        self.assertEqual(401, self.app.test_client().get("/api/notifications/preferences").status_code)

    # --- invitación ------------------------------------------------------
    def test_an_account_invite_writes_one_activity_and_one_unread_notification(self):
        self.share_with(ALBUM_B, STRANGER)
        self.assertEqual([{
            "album_id": ALBUM_B, "actor_user_id": OWNER, "event_type": "album_invite_created",
            "subject_asset_id": None,
            "metadata_json": {"target_user_id": STRANGER, "permission": "read", "capabilities": []},
        }], self.activity())
        self.assertEqual([{"event_type": "album_invite", "album_id": ALBUM_B, "actor_user_id": OWNER,
                           "read_at": None}], self.notifications_of(STRANGER))

        # Sin la categoría: la actividad se escribe igual, el aviso no.
        self.set_pref(STRANGER, notify_album_invites=False)
        self.share_with(ALBUM_P, STRANGER, "write", ["upload"])
        self.assertEqual(2, len(self.activity()))
        self.assertEqual(1, len(self.notifications_of(STRANGER)))

    def test_failed_unauthorized_and_public_link_invites_leave_no_event(self):
        antes = self.counts()
        # Colaborador que intenta compartir, destinatario inexistente, duplicado.
        self.assertEqual(403, self.call(COLLAB, "post", f"/api/albums/{ALBUM_A}/shares",
                                        json={"shared_with_user_id": STRANGER, "permission": "read"}).status_code)
        self.assertEqual(404, self.call(OWNER, "post", f"/api/albums/{ALBUM_A}/shares",
                                        json={"shared_with_user_id": 999999, "permission": "read"}).status_code)
        self.assertEqual(409, self.call(OWNER, "post", f"/api/albums/{ALBUM_A}/shares",
                                        json={"shared_with_user_id": COLLAB, "permission": "read"}).status_code)
        self.assertEqual(antes, self.counts())
        # Un enlace público no invita a nadie: ni actividad de invitación ni aviso.
        respuesta = self.call(OWNER, "post", f"/api/albums/{ALBUM_B}/shares",
                              json={"create_link": True, "share_type": "public_link", "permission": "read"})
        self.assertEqual(201, respuesta.status_code)
        self.addCleanup(self.scratch.execute, "DELETE FROM album_shares WHERE id = :id",
                        {"id": respuesta.get_json()["data"]["id"]})
        self.assertEqual(antes, self.counts())

    # --- reclamar --------------------------------------------------------
    def test_claiming_an_invite_records_the_claimer_and_tells_the_owner(self):
        token = self.invite_link(ALBUM_B)
        self.assertEqual([None], [a["metadata_json"]["target_user_id"] for a in self.activity()])
        self.assertEqual([], self.notifications_of(OWNER))

        self.assertEqual(200, self.call(STRANGER, "post", f"/api/shared/{token}/claim").status_code)
        reclamado = [a for a in self.activity() if a["event_type"] == "share_claimed"]
        self.assertEqual([(ALBUM_B, STRANGER, {})],
                         [(a["album_id"], a["actor_user_id"], a["metadata_json"]) for a in reclamado])
        self.assertEqual([{"event_type": "share_claimed", "album_id": ALBUM_B, "actor_user_id": STRANGER,
                           "read_at": None}], self.notifications_of(OWNER))

        self.set_pref(OWNER, notify_share_claimed=False)
        otro = self.invite_link(ALBUM_B)
        self.assertEqual(200, self.call(OTHER, "post", f"/api/shared/{otro}/claim").status_code)
        self.assertEqual(2, len([a for a in self.activity() if a["event_type"] == "share_claimed"]))
        self.assertEqual(1, len(self.notifications_of(OWNER)))

    def test_a_failed_claim_leaves_no_event(self):
        token = self.invite_link(ALBUM_B)
        antes = self.counts()
        self.assertEqual(404, self.call(STRANGER, "post", "/api/shared/no-such-token/claim").status_code)
        self.assertEqual(400, self.call(OWNER, "post", f"/api/shared/{token}/claim").status_code)
        self.assertEqual(antes, self.counts())

    # --- subida ----------------------------------------------------------
    def test_an_upload_notifies_active_account_collaborators_by_default(self):
        # STRANGER: acceso revocado; OTHER: acceso vencido. Ninguno recibe nada.
        for user, extra in ((STRANGER, "active = FALSE"), (OTHER, "expires_at = NOW() - INTERVAL '1 day'")):
            share_id = self.share_with(ALBUM_A, user)
            self.scratch.execute(f"UPDATE album_shares SET {extra} WHERE id = :id", {"id": share_id})
        self.scratch.execute("DELETE FROM notifications")
        self.scratch.execute("DELETE FROM album_activity")

        respuesta = self.upload(OWNER, ALBUM_A, (200, 10, 10))
        self.assertEqual(201, respuesta.status_code, respuesta.get_json())
        asset_id = respuesta.get_json()["data"]["id"]
        self.assertEqual([(ALBUM_A, OWNER, "asset_uploaded", asset_id)], [
            (a["album_id"], a["actor_user_id"], a["event_type"], a["subject_asset_id"]) for a in self.activity()])
        # COLLAB no tiene fila de preferencias: el aviso llega igual.
        self.assertEqual([{"event_type": "shared_album_upload", "album_id": ALBUM_A, "actor_user_id": OWNER,
                           "read_at": None}], self.notifications_of(COLLAB))
        for nadie in (OWNER, STRANGER, OTHER):
            self.assertEqual([], self.notifications_of(nadie))

        self.set_pref(COLLAB, notify_shared_album_uploads=False)
        self.assertEqual(201, self.upload(OWNER, ALBUM_A, (10, 200, 10)).status_code)
        self.assertEqual(2, len(self.activity()))
        self.assertEqual(1, len(self.notifications_of(COLLAB)))

    def test_the_uploader_never_notifies_themselves_and_public_links_are_not_recipients(self):
        self.scratch.execute("UPDATE album_shares SET permission = 'write', capabilities = '{upload,organize}' WHERE id = :s",
                             {"s": COLLAB_SHARE})
        self.addCleanup(self.scratch.execute,
                        "UPDATE album_shares SET permission = 'write', capabilities = '{organize}' WHERE id = :s",
                        {"s": COLLAB_SHARE})
        self.assertEqual(201, self.upload(COLLAB, ALBUM_A, (20, 20, 220)).status_code)
        self.assertEqual([COLLAB], [a["actor_user_id"] for a in self.activity()])
        self.assertEqual((1, 0), self.counts(), "ni el propio colaborador ni el dueño (que no es un acceso)")

        # P solo tiene un enlace público: nadie a quien avisar.
        self.assertEqual(201, self.upload(OWNER, ALBUM_P, (220, 220, 20)).status_code)
        self.assertEqual((2, 0), self.counts())

    def test_a_rejected_duplicate_leaves_no_event(self):
        primera = self.upload(OWNER, ALBUM_A, (90, 90, 90))
        self.assertEqual(201, primera.status_code)
        antes = self.counts()
        duplicada = self.upload(OWNER, ALBUM_A, (90, 90, 90))
        self.assertEqual(409, duplicada.status_code, duplicada.get_json())
        self.assertEqual(antes, self.counts())

    def test_an_upload_that_rolls_back_leaves_no_activity_and_no_notification(self):
        from app.api import media

        real = media.notify_album_upload

        def falla_despues(conn, *args, **kwargs):
            real(conn, *args, **kwargs)  # la actividad y los avisos YA viajaron
            raise ValueError("fallo simulado dentro de la transacción")

        antes = self.counts()
        assets = self.scratch.scalar("SELECT count(*) FROM assets")
        with patch.object(media, "notify_album_upload", side_effect=falla_despues):
            respuesta = self.upload(OWNER, ALBUM_A, (5, 120, 200))
        self.assertEqual(400, respuesta.status_code, respuesta.get_json())
        self.assertEqual(antes, self.counts())
        self.assertEqual(assets, self.scratch.scalar("SELECT count(*) FROM assets"))

    # --- pertenencias ----------------------------------------------------
    def test_membership_add_is_recorded_once_and_remove_is_recorded(self):
        self.addCleanup(self.scratch.execute,
                        "DELETE FROM album_assets WHERE album_id = :b AND asset_id = 900204", {"b": ALBUM_B})
        self.assertEqual(403, self.call(COLLAB, "put", f"/api/albums/{ALBUM_A}/assets/900205").status_code)
        self.assertEqual([], self.activity())

        self.assertTrue(self.call(OWNER, "put", f"/api/albums/{ALBUM_B}/assets/900204").get_json()["data"]["added"])
        self.assertFalse(self.call(OWNER, "put", f"/api/albums/{ALBUM_B}/assets/900204").get_json()["data"]["added"])
        self.assertEqual([("asset_added", 900204)], [(a["event_type"], a["subject_asset_id"]) for a in self.activity()])

        self.assertEqual(200, self.call(OWNER, "delete", f"/api/albums/{ALBUM_B}/assets/900204").status_code)
        self.assertEqual(404, self.call(OWNER, "delete", f"/api/albums/{ALBUM_B}/assets/900204").status_code)
        self.assertEqual([("asset_added", 900204), ("asset_removed", 900204)],
                         [(a["event_type"], a["subject_asset_id"]) for a in self.activity()])
        self.assertEqual([], self.notifications_of(COLLAB))

    # --- portada ---------------------------------------------------------
    def test_cover_changes_are_recorded_only_when_the_cover_really_changes(self):
        self.addCleanup(self.scratch.execute, "UPDATE albums SET cover_media_id = 900201 WHERE id = :a", {"a": ALBUM_A})

        def patch_album(body):
            respuesta = self.call(OWNER, "patch", f"/api/albums/{ALBUM_A}", json=body)
            self.assertEqual(200, respuesta.status_code, respuesta.get_json())

        patch_album({"cover_media_id": 900201})          # ya era la portada
        patch_album({"titulo": "L10A A"})                # sin tocar la portada
        self.assertEqual([], self.activity())
        patch_album({"cover_media_id": 900204})
        patch_album({"cover_media_id": 900204, "descripcion": "otra"})
        patch_album({"cover_media_id": None})
        patch_album({"cover_media_id": None})
        self.assertEqual([("cover_changed", 900204, {}), ("cover_changed", None, {"cleared": True})],
                         [(a["event_type"], a["subject_asset_id"], a["metadata_json"]) for a in self.activity()])
        # Una portada inválida falla sin dejar nada.
        self.assertEqual(400, self.call(OWNER, "patch", f"/api/albums/{ALBUM_A}",
                                        json={"cover_media_id": 900208}).status_code)
        self.assertEqual(2, len(self.activity()))

    # --- permisos --------------------------------------------------------
    def test_permission_changes_are_recorded_only_when_the_effective_access_changes(self):
        self.addCleanup(self.scratch.execute,
                        "UPDATE album_shares SET permission = 'write', capabilities = '{organize}' WHERE id = :s",
                        {"s": COLLAB_SHARE})
        self.addCleanup(self.scratch.execute,
                        "UPDATE album_shares SET show_metadata = TRUE, allow_original_download = FALSE WHERE id = :s",
                        {"s": PUBLIC_SHARE})

        def patch_share(share, body):
            respuesta = self.call(OWNER, "patch", f"/api/shares/{share}", json=body)
            self.assertEqual(200, respuesta.status_code, respuesta.get_json())

        patch_share(COLLAB_SHARE, {"permission": "write", "capabilities": ["organize"]})
        patch_share(PUBLIC_SHARE, {"permission": "read", "show_metadata": False, "allow_original_download": True})
        self.assertEqual([], self.activity())
        patch_share(COLLAB_SHARE, {"permission": "write", "capabilities": ["organize", "upload"]})
        patch_share(COLLAB_SHARE, {"permission": "write", "capabilities": ["upload", "organize"]})
        self.assertEqual([("share_permission_changed", OWNER,
                           {"target_user_id": COLLAB, "permission": "write", "capabilities": ["upload", "organize"]})],
                         [(a["event_type"], a["actor_user_id"], a["metadata_json"]) for a in self.activity()])
        self.assertEqual(403, self.call(COLLAB, "patch", f"/api/shares/{COLLAB_SHARE}",
                                        json={"permission": "read", "capabilities": []}).status_code)
        self.assertEqual(1, len(self.activity()))

    # --- API de actividad --------------------------------------------------
    def test_activity_is_newest_first_paginated_and_resolves_labels_in_bulk(self):
        base = datetime(2026, 9, 1, 10, 0)
        for n in range(5):
            self.scratch.execute(
                "INSERT INTO album_activity (album_id, actor_user_id, event_type, subject_asset_id, created_at)"
                " VALUES (:a, :o, 'asset_added', 900201, :t)", {"a": ALBUM_A, "o": OWNER, "t": base + timedelta(hours=n)})
        # Mismo instante: desempata el id, de forma estable.
        self.scratch.execute(
            "INSERT INTO album_activity (album_id, actor_user_id, event_type, created_at) VALUES"
            " (:a, NULL, 'share_claimed', :t), (:b, :o, 'asset_added', :t)",
            {"a": ALBUM_A, "b": ALBUM_B, "o": OWNER, "t": base + timedelta(hours=9)})

        paginas = []
        for page in (1, 2, 3):
            respuesta = self.call(OWNER, "get", f"/api/albums/{ALBUM_A}/activity?page={page}&per_page=2")
            self.assertEqual(200, respuesta.status_code, respuesta.get_json())
            cuerpo = respuesta.get_json()
            self.assertEqual(6, cuerpo["meta"]["pagination"]["total"])
            paginas.extend(cuerpo["data"])
        self.assertEqual(6, len({e["id"] for e in paginas}), "sin huecos ni repetidos entre páginas")
        fechas = [e["created_at"] for e in paginas]
        self.assertEqual(sorted(fechas, reverse=True), fechas)
        primero = paginas[0]
        self.assertEqual(("share_claimed", None), (primero["event_type"], primero["actor"]))
        self.assertEqual({"id": OWNER, "username": "l10a_owner", "full_name": "L10A Owner"}, paginas[1]["actor"])
        self.assertEqual({"id": 900201, "file_type": "image", "available": True}, paginas[1]["subject"])

    def test_activity_is_for_the_owner_and_active_account_collaborators_only(self):
        self.share_with(ALBUM_A, OTHER, "read")
        for user in (OWNER, COLLAB, OTHER):
            with self.subTest(user=user):
                self.assertEqual(200, self.call(user, "get", f"/api/albums/{ALBUM_A}/activity").status_code)
        self.assertEqual(403, self.call(STRANGER, "get", f"/api/albums/{ALBUM_A}/activity").status_code)
        # P es público: STRANGER puede ver el álbum, pero no su historial de colaboración.
        self.assertEqual(200, self.call(STRANGER, "get", f"/api/albums/{ALBUM_P}").status_code)
        self.assertEqual(403, self.call(STRANGER, "get", f"/api/albums/{ALBUM_P}/activity").status_code)
        self.assertEqual(401, self.app.test_client().get(f"/api/albums/{ALBUM_A}/activity").status_code)

        # Revocar quita el historial igual que quita el álbum.
        share = self.scratch.scalar("SELECT id FROM album_shares WHERE album_id = :a AND shared_with_user_id = :u",
                                    {"a": ALBUM_A, "u": OTHER})
        self.assertEqual(200, self.call(OWNER, "delete", f"/api/shares/{share}").status_code)
        self.assertEqual(403, self.call(OTHER, "get", f"/api/albums/{ALBUM_A}/activity").status_code)

        detalle = {u: self.call(u, "get", f"/api/albums/{ALBUM_A}").get_json()["data"]["can_view_activity"]
                   for u in (OWNER, COLLAB)}
        self.assertEqual({OWNER: True, COLLAB: True}, detalle)
        self.assertFalse(self.call(STRANGER, "get", f"/api/albums/{ALBUM_P}").get_json()["data"]["can_view_activity"])

    def test_activity_never_leaks_other_albums_or_secrets_and_survives_deleted_references(self):
        self.share_with(ALBUM_B, STRANGER, "write", ["upload"])
        self.invite_link(ALBUM_A)
        self.scratch.execute(
            "INSERT INTO album_activity (album_id, actor_user_id, event_type, subject_asset_id) VALUES"
            " (:a, NULL, 'asset_removed', NULL)", {"a": ALBUM_A})
        respuesta = self.call(COLLAB, "get", f"/api/albums/{ALBUM_A}/activity")
        self.assertEqual(200, respuesta.status_code)
        eventos = respuesta.get_json()["data"]
        self.assertEqual({"album_invite_created", "asset_removed"}, {e["event_type"] for e in eventos})
        texto = json.dumps(respuesta.get_json())
        for secreto in ("token", "hash", "password", "storage", "caption", "Leyenda", "L10A B", "session"):
            self.assertNotIn(secreto, texto)
        huerfano = next(e for e in eventos if e["event_type"] == "asset_removed")
        self.assertEqual((None, None), (huerfano["actor"], huerfano["subject"]))

    # --- API de notificaciones -------------------------------------------
    def test_notifications_are_private_to_their_recipient(self):
        self.share_with(ALBUM_B, STRANGER)
        self.share_with(ALBUM_P, STRANGER)
        self.share_with(ALBUM_B, OTHER)
        mia = self.scratch.scalar("SELECT id FROM notifications WHERE user_id = :u ORDER BY id LIMIT 1", {"u": STRANGER})
        ajena = self.scratch.scalar("SELECT id FROM notifications WHERE user_id = :u", {"u": OTHER})

        lista = self.call(STRANGER, "get", "/api/notifications").get_json()
        self.assertEqual(2, lista["meta"]["pagination"]["total"])
        primera = lista["data"][-1]
        self.assertEqual({"id", "event_type", "album_id", "album_title", "actor", "read_at", "created_at"},
                         set(primera))
        self.assertEqual(("album_invite", ALBUM_B, "L10A B"),
                         (primera["event_type"], primera["album_id"], primera["album_title"]))
        self.assertEqual({"id": OWNER, "username": "l10a_owner", "full_name": "L10A Owner"}, primera["actor"])
        self.assertEqual(2, self.call(STRANGER, "get", "/api/notifications/unread-count").get_json()["data"]["unread"])

        self.assertEqual(404, self.call(STRANGER, "patch", f"/api/notifications/{ajena}/read").status_code)
        leida = self.call(STRANGER, "patch", f"/api/notifications/{mia}/read").get_json()["data"]["read_at"]
        self.assertIsNotNone(leida)
        self.assertEqual(leida, self.call(STRANGER, "patch", f"/api/notifications/{mia}/read").get_json()["data"]["read_at"])
        self.assertEqual(1, self.call(STRANGER, "get", "/api/notifications/unread-count").get_json()["data"]["unread"])

        self.assertEqual(1, self.call(STRANGER, "post", "/api/notifications/read-all").get_json()["data"]["updated"])
        self.assertEqual(0, self.call(STRANGER, "post", "/api/notifications/read-all").get_json()["data"]["updated"])
        self.assertEqual(0, self.call(STRANGER, "get", "/api/notifications/unread-count").get_json()["data"]["unread"])
        self.assertIsNone(self.notifications_of(OTHER)[0]["read_at"], "read-all no toca a nadie más")
        self.assertEqual(1, self.call(OTHER, "get", "/api/notifications/unread-count").get_json()["data"]["unread"])

        cliente = self.app.test_client()
        for metodo, ruta in (("get", "/api/notifications"), ("get", "/api/notifications/unread-count"),
                             ("patch", f"/api/notifications/{mia}/read"), ("post", "/api/notifications/read-all")):
            with self.subTest(ruta=ruta):
                self.assertEqual(401, getattr(cliente, metodo)(ruta).status_code)

    def test_a_notification_outlives_its_album_without_pointing_at_it(self):
        self.scratch.execute("INSERT INTO albums (id, user_id, titulo, is_private, active) VALUES (900110, :o, 'L12 borrable', TRUE, TRUE)",
                             {"o": OWNER})
        self.addCleanup(self.scratch.execute, "DELETE FROM albums WHERE id = 900110")
        self.share_with(900110, STRANGER)
        self.assertEqual(200, self.call(OWNER, "delete", "/api/albums/900110").status_code)
        aviso = self.call(STRANGER, "get", "/api/notifications").get_json()["data"][0]
        self.assertEqual(("album_invite", None, None), (aviso["event_type"], aviso["album_id"], aviso["album_title"]))

    # --- retención -------------------------------------------------------
    def test_the_purge_removes_only_rows_older_than_each_window(self):
        from app.cli import purge_expired_activity_notifications

        ahora = datetime.now()
        for dias, leida in ((91, None), (89, None), (200, ahora)):
            self.scratch.execute("INSERT INTO notifications (user_id, event_type, created_at, read_at) "
                                 "VALUES (:u, 'album_invite', :t, :r)",
                                 {"u": STRANGER, "t": ahora - timedelta(days=dias), "r": leida})
        for dias in (366, 364):
            self.scratch.execute("INSERT INTO album_activity (album_id, event_type, created_at) VALUES (:a, 'asset_added', :t)",
                                 {"a": ALBUM_A, "t": ahora - timedelta(days=dias)})
        with patch("builtins.print") as impreso:
            self.assertEqual(0, purge_expired_activity_notifications())
        salida = " ".join(str(c.args[0]) for c in impreso.call_args_list)
        self.assertIn("2 notificación(es)", salida)
        self.assertIn("1 evento(s) de actividad", salida)
        self.assertEqual([None], [r["read_at"] for r in self.notifications_of(STRANGER)])
        self.assertEqual(1, self.scratch.scalar("SELECT count(*) FROM album_activity"))


if __name__ == "__main__":
    unittest.main()
