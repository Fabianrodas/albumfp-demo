"""L14: códigos de recuperación sin email, sin base de datos.

Formato, entropía y normalización, más la forma del código que garantiza lo
importante: la ruta pública comparte los cubos de límite del login, nunca
registra un código y nunca contesta algo distinto según exista el usuario o
el código. El comportamiento real (consumo atómico, carrera, sesiones) está en
`test_recovery_codes_db.py`.
"""
import ast
import math
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CodeFormatTests(unittest.TestCase):
    def test_ten_distinct_codes_of_at_least_128_bits_each(self):
        from app.security.recovery_codes import ALPHABET, CODE_COUNT, generate_codes

        codes = generate_codes()
        self.assertEqual(10, CODE_COUNT)
        self.assertEqual(10, len(set(codes)))
        for code in codes:
            with self.subTest(code=code):
                self.assertRegex(code, r"^[A-Z2-7]{5}(-[A-Z2-7]{5}){5}$")
                simbolos = len(code.replace("-", ""))
                self.assertGreaterEqual(simbolos * math.log2(len(ALPHABET)), 128)

    def test_what_people_type_is_normalised_before_hashing(self):
        from app.security.recovery_codes import generate_codes, hash_code, normalize_code

        code = generate_codes()[0]
        canonico = code.replace("-", "")
        for escrito in (code, code.lower(), f"  {code} ", code.replace("-", " "), canonico):
            with self.subTest(escrito=escrito):
                self.assertEqual(canonico, normalize_code(escrito))
                self.assertEqual(hash_code(code), hash_code(escrito))
        self.assertRegex(hash_code(code), r"^[0-9a-f]{64}$")
        self.assertNotIn(canonico, hash_code(code))

    def test_anything_that_is_not_a_code_normalises_to_none(self):
        from app.security.recovery_codes import normalize_code

        for malo in ("", None, "ABCDE", "A" * 31, "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-Z2341", "ÁBCDE" * 6, 12345):
            with self.subTest(malo=malo):
                self.assertIsNone(normalize_code(malo))


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function(path: str, name: str) -> ast.FunctionDef:
    for nodo in ast.walk(ast.parse(_source(path))):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == name:
            return nodo
    raise AssertionError(f"{name} no existe en {path}")


class RecoveryRouteShapeTests(unittest.TestCase):
    def test_recovery_shares_the_login_buckets_and_the_high_risk_tightening(self):
        cubos = " ".join(ast.unparse(_function("app/api/auth.py", nombre)) for nombre in
                          ("_consume_login_budget", "consume_username_budget", "consume_ip_budget"))
        self.assertIn("consume_username_budget(", cubos)
        self.assertIn("consume_ip_budget(", cubos)
        self.assertIn("'login_username'", cubos)
        self.assertIn("'login_ip'", cubos)
        self.assertIn("is_high_risk(", cubos)
        self.assertIn("_consume_login_budget(", ast.unparse(_function("app/api/auth.py", "login_user")))
        fuente = ast.unparse(_function("app/api/auth.py", "recover_account"))
        # El límite se consume ANTES de mirar el usuario o el código.
        self.assertLess(fuente.index("_consume_login_budget("), fuente.index("consume_code("))
        # La contraseña nueva se valida ANTES de gastar el código.
        self.assertLess(fuente.index("validate_new_password("), fuente.index("consume_code("))

    def test_every_recovery_failure_answers_the_same_thing(self):
        fuente = ast.unparse(_function("app/api/auth.py", "recover_account"))
        mensajes = set(re.findall(r"fail\((RECOVERY_FAILED|'[^']*')", fuente))
        self.assertEqual({"RECOVERY_FAILED"}, mensajes - {"'Faltan datos para recuperar la cuenta'"})

    def test_a_successful_recovery_revokes_every_session_and_every_code(self):
        fuente = ast.unparse(_function("app/api/auth.py", "recover_account"))
        self.assertIn("revoke_all_sessions(conn, user['id'])", fuente)
        self.assertIn("delete_all_codes(conn, user['id'])", fuente)

    def test_codes_are_never_logged_printed_or_stored_in_clear(self):
        for path in ("app/security/recovery_codes.py", "app/api/auth.py"):
            arbol = ast.parse(_source(path))
            for nodo in ast.walk(arbol):
                if isinstance(nodo, ast.Call):
                    nombre = ast.unparse(nodo.func)
                    if nombre.startswith(("logging.", "logger.", "print")):
                        with self.subTest(path=path, call=nombre):
                            self.assertNotRegex(ast.unparse(nodo), r"\bcodes?\b")
        fuente = _source("app/security/recovery_codes.py")
        self.assertIn("hash_token(", fuente)
        self.assertNotIn("INSERT INTO recovery_codes (user_id, code)", fuente)


if __name__ == "__main__":
    unittest.main()
