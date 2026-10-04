"""L15: passkeys (WebAuthn), sin base de datos.

El RP ID y los orígenes esperados salen de la única autoridad de orígenes
(`app/utils/origins.py`), nunca del navegador. La verificación la hace
`py_webauthn`; aquí se fija que nadie la sustituya por cripto propia y que
las rutas públicas estén limitadas. El protocolo completo contra PostgreSQL
real está en `test_passkeys_db.py`.
"""
import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _env(**values):
    base = {"APP_ENV": "development", "CORS_ORIGINS": "", "PUBLIC_ORIGIN": ""}
    base.update(values)
    return patch.dict(os.environ, base)


class RelyingPartyTests(unittest.TestCase):
    def test_development_uses_the_angular_dev_origin_on_localhost(self):
        from app.utils.origins import webauthn_relying_party

        with _env():
            self.assertEqual(("localhost", ["http://localhost:4200"]), webauthn_relying_party())
        with _env(CORS_ORIGINS="http://localhost:4300"):
            self.assertIsNone(webauthn_relying_party())

    def test_external_origins_never_create_a_relying_party(self):
        from app.utils.origins import webauthn_relying_party

        with _env(PUBLIC_ORIGIN="https://demo.invalid"):
            self.assertIsNone(webauthn_relying_party())
        with _env(CORS_ORIGINS="http://localhost:4300"):
            self.assertIsNone(webauthn_relying_party())

    def test_anything_ambiguous_or_insecure_disables_passkeys_instead_of_guessing(self):
        from app.utils.origins import webauthn_relying_party

        casos = [
            dict(APP_ENV="production"),                                              # sin origen
            dict(APP_ENV="production", PUBLIC_ORIGIN="http://demo.invalid"),          # HTTP no local
            dict(APP_ENV="production", PUBLIC_ORIGIN="https://demo.invalid",
                 CORS_ORIGINS="https://external.invalid"),                            # otro origen
            dict(CORS_ORIGINS="http://localhost:4200,http://127.0.0.1:4200"),        # dos hosts
            dict(CORS_ORIGINS="http://127.0.0.1:4200"),                              # IP, no dominio
        ]
        for caso in casos:
            with self.subTest(caso=caso), _env(**caso):
                self.assertIsNone(webauthn_relying_party())


class NicknameTests(unittest.TestCase):
    def test_nicknames_are_short_visible_text(self):
        from app.api.passkeys import clean_nickname

        self.assertEqual("Portátil", clean_nickname("  Portátil  "))
        for malo in ("", "   ", "x" * 61, "tab\there", "nul\x00", None, 5):
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                clean_nickname(malo)


def _function(path: str, name: str) -> ast.FunctionDef:
    for nodo in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == name:
            return nodo
    raise AssertionError(f"{name} no existe en {path}")


class RouteShapeTests(unittest.TestCase):
    def test_verification_is_always_delegated_to_py_webauthn(self):
        fuente = (ROOT / "app/api/passkeys.py").read_text(encoding="utf-8")
        for importado in ("verify_registration_response", "verify_authentication_response",
                          "generate_registration_options", "generate_authentication_options"):
            self.assertIn(importado, fuente)
        for cripto in ("hazmat", "ecdsa", "hmac.new", "cbor2"):
            self.assertNotIn(cripto, fuente, "nada de verificación WebAuthn hecha a mano")

    def test_public_login_ceremony_is_rate_limited_and_answers_generically(self):
        opciones = ast.unparse(_function("app/api/passkeys.py", "passkey_login_options"))
        verificar = ast.unparse(_function("app/api/passkeys.py", "passkey_login_verify"))
        self.assertIn("consume_ip_budget(", opciones)
        self.assertIn("consume_ip_budget(", verificar)
        self.assertIn("consume_username_budget(", verificar)
        self.assertIn("create_session(", verificar)
        self.assertIn("LOGIN_FAILED", verificar)

    def test_both_ceremonies_require_user_verification(self):
        fuente = (ROOT / "app/api/passkeys.py").read_text(encoding="utf-8")
        self.assertEqual(2, fuente.count("UserVerificationRequirement.REQUIRED"))
        self.assertNotIn("UserVerificationRequirement.PREFERRED", fuente)
        self.assertEqual(2, fuente.count("require_user_verification=True"))
        self.assertNotIn("require_user_verification=False", fuente)

    def test_registration_needs_the_session_and_the_current_password(self):
        opciones = ast.unparse(_function("app/api/passkeys.py", "passkey_register_options"))
        self.assertIn("verify_password(", opciones)
        for nombre in ("passkey_register_options", "passkey_register_verify", "list_passkeys",
                       "rename_passkey", "delete_passkey"):
            decoradores = {ast.unparse(d) for d in _function("app/api/passkeys.py", nombre).decorator_list}
            with self.subTest(ruta=nombre):
                self.assertIn("session_required", decoradores)

    def test_the_dependency_is_declared_with_a_bounded_supported_range(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("webauthn>=3,<4", requirements)


if __name__ == "__main__":
    unittest.main()
