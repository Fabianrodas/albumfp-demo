"""S03 -- cuota de almacenamiento por cuenta y topes de tamano por archivo.

Sin Postgres (patron del repo): se fija la FORMA del SQL y el contrato de las
funciones puras, no el resultado contra datos reales -- eso lo cubre el QA en
vivo contra una base desechable.
"""
import ast
import inspect
import unittest
from flask import Flask
from pathlib import Path
from unittest.mock import patch

from app.api import media as media_api

ROOT = Path(__file__).resolve().parents[1]


def _sql_de(funcion) -> str:
    arbol = ast.parse(inspect.getsource(funcion))
    return "\n".join(
        n.value for n in ast.walk(arbol)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


class AccountStorageUsedBytesTests(unittest.TestCase):
    def test_it_counts_originals_and_previews_together(self):
        fuente = _sql_de(media_api.account_storage_used_bytes)
        self.assertIn("SUM(mm.file_size)", fuente)
        self.assertIn("SUM(mm.preview_file_size)", fuente)

    def test_it_scopes_by_the_owner_not_the_uploader(self):
        # media.user_id es siempre el dueño del album (asi lo escribe
        # insert_media_record), nunca quien subio si es un colaborador.
        fuente = _sql_de(media_api.account_storage_used_bytes)
        self.assertIn("m.user_id = :owner_id", fuente)

    def test_it_does_not_exclude_trashed_media(self):
        """La papelera sigue ocupando disco hasta que se purga de verdad, asi
        que debe seguir contando contra la cuota. Si alguien añade un filtro
        `deleted_at IS NULL` aqui, la cuota dejaria de reflejar el uso real."""
        fuente = _sql_de(media_api.account_storage_used_bytes)
        self.assertNotIn("deleted_at IS NULL", fuente)

    def test_it_returns_zero_not_none_when_the_account_has_no_media(self):
        class Resultado:
            def mappings(self_inner):
                return self_inner

            def first(self_inner):
                return {"total": None}

        with patch.object(media_api, "execute_safe", lambda *a, **k: Resultado()):
            self.assertEqual(0, media_api.account_storage_used_bytes(object(), 1))


class ReserveStorageQuotaTests(unittest.TestCase):
    def test_a_quota_of_zero_or_less_means_unlimited_and_skips_the_database(self):
        with patch.dict("os.environ", {"USER_STORAGE_QUOTA_GB": "0"}):
            with patch.object(media_api, "execute_safe") as espia:
                permitido = media_api.reserve_storage_quota(object(), 1, 10**12)
        self.assertTrue(permitido)
        espia.assert_not_called()

    def test_it_takes_an_advisory_lock_scoped_by_owner_before_summing(self):
        fuente = _sql_de(media_api.reserve_storage_quota)
        self.assertIn("pg_advisory_xact_lock(:namespace, :owner_id)", fuente)

    def test_it_denies_when_the_addition_would_exceed_the_quota(self):
        with patch.dict("os.environ", {"USER_STORAGE_QUOTA_GB": "1"}):  # 1 GiB
            with patch.object(media_api, "execute_safe"):
                with patch.object(media_api, "account_storage_used_bytes", return_value=1024**3 - 100):
                    permitido = media_api.reserve_storage_quota(object(), 1, 200)
        self.assertFalse(permitido)

    def test_it_allows_when_there_is_still_room(self):
        with patch.dict("os.environ", {"USER_STORAGE_QUOTA_GB": "1"}):
            with patch.object(media_api, "execute_safe"):
                with patch.object(media_api, "account_storage_used_bytes", return_value=100):
                    permitido = media_api.reserve_storage_quota(object(), 1, 200)
        self.assertTrue(permitido)


class MaxUploadBytesTests(unittest.TestCase):
    def test_it_is_unlimited_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            for var in ("MAX_IMAGE_MB", "MAX_VIDEO_MB"):
                __import__("os").environ.pop(var, None)
            self.assertEqual(0, media_api._max_upload_bytes("image"))
            self.assertEqual(0, media_api._max_upload_bytes("video"))

    def test_configuring_it_produces_the_matching_byte_ceiling(self):
        with patch.dict("os.environ", {"MAX_IMAGE_MB": "50"}):
            self.assertEqual(50 * 1024 * 1024, media_api._max_upload_bytes("image"))


class RegistrationModeTests(unittest.TestCase):
    def test_defaults_to_open(self):
        from app.domain.rules import registration_mode
        with patch.dict("os.environ", {}, clear=False):
            __import__("os").environ.pop("REGISTRATION_MODE", None)
            self.assertEqual("open", registration_mode())

    def test_an_unknown_value_fails_closed(self):
        from app.domain.rules import registration_mode
        with patch.dict("os.environ", {"REGISTRATION_MODE": "algo-mal-escrito"}):
            self.assertEqual("closed", registration_mode())

    def test_recognizes_the_three_documented_modes(self):
        from app.domain.rules import registration_mode
        for modo in ("open", "invite_only", "closed"):
            with patch.dict("os.environ", {"REGISTRATION_MODE": modo}):
                self.assertEqual(modo, registration_mode())

    def test_health_reports_the_fail_closed_decision_for_an_unknown_value(self):
        from app.api.health import health_bp
        app = Flask(__name__)
        app.register_blueprint(health_bp)
        with patch.dict("os.environ", {"REGISTRATION_MODE": "typo"}):
            response = app.test_client().get("/api/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual("closed", response.get_json()["data"]["registration_mode"])


class RegistrationInviteAtomicityTests(unittest.TestCase):
    """El endpoint completo se verifica en QA en vivo; aqui se fija solo la
    forma que hace la atomicidad posible."""

    FUENTE = (ROOT / "app/api/auth.py").read_text(encoding="utf-8")

    def _cuerpo(self, nombre: str) -> str:
        arbol = ast.parse(self.FUENTE)
        for nodo in arbol.body:
            if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
                return ast.get_source_segment(self.FUENTE, nodo)
        self.fail(f"no existe {nombre} en auth.py")

    def test_the_invite_is_looked_up_before_any_write(self):
        cuerpo = self._cuerpo("register_user")
        # El SELECT de la invitacion aparece ANTES del INSERT de usuarios.
        pos_select = cuerpo.index("FROM registration_invites")
        pos_insert = cuerpo.index("INSERT INTO users")
        self.assertLess(pos_select, pos_insert)

    def test_a_losing_consume_race_raises_instead_of_returning(self):
        """Si dos registros consumen la MISMA invitacion a la vez, el que
        pierde debe lanzar (para deshacer TODA la transaccion, incluido el
        usuario ya creado) y no simplemente `return fail(...)`, que dejaria
        el usuario a medio crear con una transaccion que igual comitea."""
        cuerpo = self._cuerpo("register_user")
        self.assertIn("if consumida is None:", cuerpo)
        seccion = cuerpo[cuerpo.index("if consumida is None:"):]
        seccion = seccion[:seccion.index("\n\n")] if "\n\n" in seccion else seccion
        self.assertIn("raise ValueError", seccion)

    def test_the_consume_update_still_guards_on_used_at_is_null(self):
        cuerpo = self._cuerpo("register_user")
        self.assertIn("WHERE id = :invite_id AND used_at IS NULL", cuerpo)


if __name__ == "__main__":
    unittest.main()
