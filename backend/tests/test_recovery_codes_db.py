"""L14 contra PostgreSQL de verdad: migración 0027 y códigos de recuperación.

Reutiliza el arnés de L10A (`test_asset_membership_db.py`). Las cuentas del
arnés tienen `password_hash = 'x'`; aquí se les pone una contraseña real para
poder regenerar y comprobar el login después de recuperar.
"""
import threading
import time
import unittest
from unittest.mock import patch

try:
    from tests.test_asset_membership_db import OTHER, OWNER, STRANGER, Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023
except ImportError:  # discover -s tests importa los módulos sin el paquete
    from test_asset_membership_db import OTHER, OWNER, STRANGER, Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023

HEAD = "0027_recovery_codes"
PREVIOUS = "0026_activity_notifications"
PASSWORD = "Contrasena antigua de prueba 2026"
NEW_PASSWORD = "Una frase nueva y bastante larga 2026"
GENERIC = "No pudimos recuperar la cuenta con esos datos."


@unittest.skipIf(_skip_reason(), _skip_reason())
class RecoveryCodeMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch("l14mig")
        try:
            seed_0023(cls.scratch)
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

    def test_1_the_table_keeps_only_hashes_and_dies_with_the_account(self):
        self.assertEqual(HEAD, self.scratch.version())
        columnas = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in self.scratch.rows(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'recovery_codes'")}
        self.assertEqual({"id": ("integer", "NO"), "user_id": ("integer", "NO"), "code_hash": ("character", "NO"),
                          "created_at": ("timestamp without time zone", "NO"),
                          "used_at": ("timestamp without time zone", "YES")}, columnas)
        with self.assertRaises(Exception):
            self.scratch.execute("INSERT INTO recovery_codes (user_id, code_hash) VALUES (:u, 'PLAINTEXT')", {"u": OWNER})
        self.scratch.execute("INSERT INTO recovery_codes (user_id, code_hash) VALUES (:u, :h)",
                             {"u": STRANGER, "h": "a" * 64})
        with self.assertRaises(Exception):
            self.scratch.execute("INSERT INTO recovery_codes (user_id, code_hash) VALUES (:u, :h)",
                                 {"u": OTHER, "h": "a" * 64})
        self.scratch.execute("DELETE FROM users WHERE id = :u", {"u": STRANGER})
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM recovery_codes"))

    def test_2_downgrade_refuses_while_codes_exist_then_round_trips(self):
        self.scratch.execute("INSERT INTO recovery_codes (user_id, code_hash) VALUES (:u, :h)", {"u": OWNER, "h": "b" * 64})
        bajada = self.scratch.alembic("downgrade", PREVIOUS)
        self.assertNotEqual(0, bajada.returncode)
        self.assertIn("recovery_codes", bajada.stdout + bajada.stderr)
        self.assertEqual(HEAD, self.scratch.version())
        self.scratch.execute("DELETE FROM recovery_codes")
        self.assertEqual(0, self.scratch.alembic("downgrade", PREVIOUS).returncode)
        self.assertIsNone(self.scratch.scalar("SELECT to_regclass('recovery_codes')"))
        self.assertEqual(0, self.scratch.alembic("upgrade", HEAD).returncode)
        self.assertEqual(HEAD, self.scratch.version())

    def test_3_schema_sql_and_the_migration_chain_agree(self):
        from pathlib import Path

        from sqlalchemy import create_engine, text
        from app.db import db as appdb

        fresh_name = f"{self.scratch.name}_fresh"
        admin = admin_database_engine(appdb)
        consultas = {
            "columns": "SELECT column_name, data_type, is_nullable, COALESCE(column_default, '') FROM information_schema.columns WHERE table_name = 'recovery_codes'",
            "constraints": "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'recovery_codes'::regclass",
            "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'recovery_codes'",
        }
        try:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
                c.execute(text(
                    f'CREATE DATABASE "{fresh_name}" WITH TEMPLATE template0 OWNER "albumfp_demo"'
                ))
            fresh = create_engine(demo_database_url(appdb, fresh_name))
            with fresh.begin() as c:
                c.exec_driver_sql((Path(__file__).resolve().parents[1] / "schemas/schema.sql").read_text(encoding="utf-8"))
            for nombre, consulta in consultas.items():
                with self.subTest(nombre):
                    def catalogo(engine):
                        with engine.connect() as c:
                            return sorted(tuple(str(v).strip() for v in r) for r in c.execute(text(consulta)).all())
                    self.assertEqual(catalogo(self.scratch.engine), catalogo(fresh))
            fresh.dispose()
        finally:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
            admin.dispose()


@unittest.skipIf(_skip_reason(), _skip_reason())
class RecoveryCodeApiTests(_AppCase):
    label = "l14"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.security.hashing import hash_password

        cls.scratch.execute("UPDATE users SET password_hash = :h", {"h": hash_password(PASSWORD)})

    def setUp(self):
        from app.security.hashing import hash_password

        self.addCleanup(self.scratch.execute, "DELETE FROM recovery_codes")
        self.addCleanup(self.scratch.execute, "DELETE FROM rate_limit_counters")
        self.addCleanup(self.scratch.execute, "UPDATE users SET password_hash = :h", {"h": hash_password(PASSWORD)})
        self.addCleanup(self.scratch.execute, "UPDATE user_sessions SET revoked_at = NULL")
        # HIBP no se consulta nunca desde las pruebas.
        parche = patch("app.security.password_policy.is_password_pwned", return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def generate(self, user=OWNER, password=PASSWORD):
        return self.call(user, "post", "/auth/recovery-codes", json={"current_password": password})

    def codes(self, user=OWNER) -> list[str]:
        respuesta = self.generate(user)
        self.assertEqual(201, respuesta.status_code, respuesta.get_json())
        return respuesta.get_json()["data"]["codes"]

    def recover(self, code, username="l10a_owner", password=NEW_PASSWORD, ip="203.0.113.7"):
        return self.app.test_client().post("/auth/recover", json={
            "username": username, "code": code, "new_password": password,
        }, environ_base={"REMOTE_ADDR": ip})

    def login(self, password, username="l10a_owner"):
        return self.app.test_client().post("/auth/login", json={"username": username, "password": password},
                                           environ_base={"REMOTE_ADDR": "198.51.100.9"})

    # --- generar / ver / revocar ------------------------------------------
    def test_codes_are_shown_once_and_only_their_hashes_are_stored(self):
        self.assertEqual({"total": 0, "remaining": 0, "created_at": None},
                         self.call(OWNER, "get", "/auth/recovery-codes").get_json()["data"])
        codes = self.codes()
        self.assertEqual(10, len(codes))
        filas = self.scratch.rows("SELECT code_hash, used_at FROM recovery_codes WHERE user_id = :u", {"u": OWNER})
        self.assertEqual(10, len(filas))
        guardado = " ".join(r["code_hash"] for r in filas)
        for code in codes:
            self.assertNotIn(code.replace("-", ""), guardado)
        estado = self.call(OWNER, "get", "/auth/recovery-codes").get_json()["data"]
        self.assertEqual((10, 10), (estado["total"], estado["remaining"]))
        self.assertNotIn("codes", estado, "la lectura posterior nunca devuelve los códigos")

    def test_generating_requires_the_current_password_and_replaces_the_whole_set(self):
        self.assertEqual(400, self.generate(password="no es la contraseña").status_code)
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM recovery_codes"))
        primeros = self.codes()
        segundos = self.codes()
        self.assertEqual(10, self.scratch.scalar("SELECT count(*) FROM recovery_codes WHERE user_id = :u", {"u": OWNER}))
        self.assertEqual(400, self.recover(primeros[0]).status_code, "el juego viejo ya no vale")
        self.assertEqual(200, self.recover(segundos[0]).status_code)

    def test_revoking_removes_every_code(self):
        codes = self.codes()
        self.assertEqual(200, self.call(OWNER, "delete", "/auth/recovery-codes").status_code)
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM recovery_codes"))
        self.assertEqual(400, self.recover(codes[0]).status_code)

    def test_the_endpoints_are_private_to_the_session(self):
        cliente = self.app.test_client()
        self.assertEqual(401, cliente.get("/auth/recovery-codes").status_code)
        self.assertEqual(401, cliente.post("/auth/recovery-codes", json={"current_password": PASSWORD}).status_code)
        self.codes(OTHER)
        self.assertEqual(0, self.call(OWNER, "get", "/auth/recovery-codes").get_json()["data"]["total"])

    # --- recuperar --------------------------------------------------------
    def test_a_code_resets_the_password_revokes_every_session_and_every_code(self):
        codes = self.codes()
        vivas = self.scratch.scalar("SELECT count(*) FROM user_sessions WHERE user_id = :u AND revoked_at IS NULL",
                                    {"u": OWNER})
        self.assertGreater(vivas, 0)
        respuesta = self.recover(codes[3].lower().replace("-", " "))
        self.assertEqual(200, respuesta.status_code, respuesta.get_json())
        self.assertNotIn("Set-Cookie", respuesta.headers, "recuperar no inicia sesión")
        self.assertEqual(0, self.scratch.scalar(
            "SELECT count(*) FROM user_sessions WHERE user_id = :u AND revoked_at IS NULL", {"u": OWNER}))
        self.assertEqual(401, self.call(OWNER, "get", "/auth/me").status_code, "la sesión vieja ya no vale")
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM recovery_codes WHERE user_id = :u", {"u": OWNER}))
        self.assertEqual(400, self.recover(codes[3]).status_code, "el mismo código no vuelve a servir")
        self.assertEqual(400, self.recover(codes[4]).status_code, "ni los que quedaban")
        self.assertEqual(401, self.login(PASSWORD).status_code)
        self.assertEqual(200, self.login(NEW_PASSWORD).status_code)

    def test_every_failure_is_the_same_generic_answer(self):
        codes = self.codes()
        otros = self.codes(OTHER)
        casos = [
            ("usuario inexistente", {"username": "nadie_aqui", "code": codes[0]}),
            ("código inventado", {"code": "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF"}),
            ("código de otra cuenta", {"code": otros[0]}),
            ("código con mal formato", {"code": "hola"}),
        ]
        for nombre, extra in casos:
            with self.subTest(nombre):
                respuesta = self.recover(**{"code": codes[0], **extra})
                self.assertEqual(400, respuesta.status_code)
                self.assertEqual(GENERIC, respuesta.get_json()["message"])
        self.assertEqual(200, self.recover(codes[1]).status_code)
        usado = self.recover(codes[1])
        self.assertEqual((400, GENERIC), (usado.status_code, usado.get_json()["message"]))
        self.assertEqual(10, self.scratch.scalar("SELECT count(*) FROM recovery_codes WHERE user_id = :u", {"u": OTHER}),
                         "probar con el código de otra cuenta no toca esa cuenta")

    def test_a_weak_new_password_is_refused_without_spending_the_code(self):
        codes = self.codes()
        corta = self.recover(codes[0], password="corta")
        self.assertEqual(400, corta.status_code)
        self.assertNotEqual(GENERIC, corta.get_json()["message"], "el error de la política sí se explica")
        self.assertIsNone(self.scratch.scalar("SELECT max(used_at) FROM recovery_codes"))
        self.assertEqual(200, self.recover(codes[0]).status_code)

    def test_a_wrong_current_password_is_a_400_that_keeps_the_session(self):
        """F06 (Macro A finding 3): un 401 aquí hacía que el interceptor diera
        por caducada una sesión válida y echara a la persona por un despiste."""
        respuesta = self.call(OWNER, "post", "/auth/me/password",
                              json={"current_password": "no es la mía", "new_password": NEW_PASSWORD})
        self.assertEqual((400, "invalid_current_password"), (respuesta.status_code, respuesta.get_json().get("code")))
        self.assertEqual(200, self.call(OWNER, "get", "/auth/me").status_code, "la sesión sigue viva")

    def test_recovery_spends_the_same_budget_as_login(self):
        codes = self.codes()
        with patch.dict("os.environ", {"RATE_LIMIT_LOGIN_PER_15_MIN": "3"}):
            for _ in range(3):
                self.assertEqual(400, self.recover("AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA").status_code)
            bloqueada = self.recover(codes[0])
            self.assertEqual(429, bloqueada.status_code)
            self.assertIn("Retry-After", bloqueada.headers)
            self.assertEqual(429, self.login(PASSWORD).status_code, "el mismo cubo por usuario que el login")
        self.assertIsNone(self.scratch.scalar("SELECT max(used_at) FROM recovery_codes"),
                          "el límite corta antes de mirar el código")

    def test_two_simultaneous_uses_of_one_code_let_exactly_one_through(self):
        """La carrera se cierra en PostgreSQL: el segundo UPDATE espera al
        bloqueo de fila del primero y, tras su COMMIT, ya no encuentra la fila
        sin usar."""
        from app.security.recovery_codes import consume_code

        code = self.codes()[0]
        resultados = {}
        with self.scratch.engine.connect() as primera:
            tx = primera.begin()
            resultados["primera"] = consume_code(primera, OWNER, code)

            def segunda():
                with self.scratch.engine.begin() as conn:
                    resultados["segunda"] = consume_code(conn, OWNER, code)

            hilo = threading.Thread(target=segunda)
            hilo.start()
            time.sleep(0.5)
            self.assertTrue(hilo.is_alive(), "la segunda espera el bloqueo de la primera")
            tx.commit()
        hilo.join(10)
        self.assertTrue(resultados["primera"])
        self.assertFalse(resultados["segunda"])
        self.assertEqual(1, self.scratch.scalar("SELECT count(*) FROM recovery_codes WHERE used_at IS NOT NULL"))

    def test_two_simultaneous_http_recoveries_with_one_code_let_exactly_one_through(self):
        code = self.codes()[0]
        respuestas = []
        barrera = threading.Barrier(2)

        def intento(ip):
            barrera.wait()
            respuestas.append(self.recover(code, ip=ip).status_code)

        hilos = [threading.Thread(target=intento, args=(f"203.0.113.{i}",)) for i in (20, 21)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(30)
        self.assertEqual([200, 400], sorted(respuestas))


if __name__ == "__main__":
    unittest.main()
