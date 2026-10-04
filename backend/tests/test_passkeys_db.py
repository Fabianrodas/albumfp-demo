"""L15 contra PostgreSQL de verdad: migración 0028 y el protocolo WebAuthn.

Las ceremonias usan `soft_authenticator.SoftAuthenticator`, que produce
respuestas WebAuthn reales, así que lo que se ejercita es la verificación de
verdad de py_webauthn: origen, RP ID, challenge, contador y firma. El arnés es
el de L10A (`test_asset_membership_db.py`), sin CORS_ORIGINS: el origen de
desarrollo es http://localhost:4200 y el RP ID `localhost`.
"""
import unittest
from unittest.mock import patch

try:
    from tests.soft_authenticator import SoftAuthenticator, b64u
    from tests.test_asset_membership_db import OTHER, OWNER, STRANGER, Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023
except ImportError:  # discover -s tests importa los módulos sin el paquete
    from soft_authenticator import SoftAuthenticator, b64u
    from test_asset_membership_db import OTHER, OWNER, STRANGER, Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023

HEAD = "0028_webauthn_passkeys"
PREVIOUS = "0027_recovery_codes"
PASSWORD = "Contrasena de prueba para passkeys 2026"
GENERIC = "No pudimos verificar la passkey."


@unittest.skipIf(_skip_reason(), _skip_reason())
class PasskeyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch("l15mig")
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

    def insert(self, user, cred=b"c" * 32):
        self.scratch.execute("INSERT INTO webauthn_credentials (user_id, credential_id, public_key, nickname) "
                             "VALUES (:u, :c, :k, 'Llave')", {"u": user, "c": cred, "k": b"k" * 77})

    def test_1_credentials_are_unique_public_data_and_die_with_the_account(self):
        self.assertEqual(HEAD, self.scratch.version())
        columnas = {r["column_name"] for r in self.scratch.rows(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'webauthn_credentials'")}
        self.assertEqual({"id", "user_id", "credential_id", "public_key", "sign_count", "transports", "nickname",
                          "created_at", "last_used_at"}, columnas)
        self.insert(STRANGER)
        with self.assertRaises(Exception):
            self.insert(OTHER)  # el mismo credential_id no puede ser de dos cuentas
        self.scratch.execute("DELETE FROM users WHERE id = :u", {"u": STRANGER})
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM webauthn_credentials"))

    def test_2_downgrade_refuses_while_passkeys_exist_then_round_trips(self):
        self.insert(OWNER, b"d" * 32)
        self.scratch.execute("INSERT INTO webauthn_challenges (challenge_hash, purpose, expires_at) "
                             "VALUES (:h, 'authentication', NOW())", {"h": "e" * 64})
        bajada = self.scratch.alembic("downgrade", PREVIOUS)
        self.assertNotEqual(0, bajada.returncode)
        self.assertIn("webauthn_credentials", bajada.stdout + bajada.stderr)
        self.scratch.execute("DELETE FROM webauthn_credentials")
        # Los challenges son efímeros: no bloquean la bajada.
        self.assertEqual(0, self.scratch.alembic("downgrade", PREVIOUS).returncode)
        self.assertIsNone(self.scratch.scalar("SELECT to_regclass('webauthn_credentials')"))
        self.assertIsNone(self.scratch.scalar("SELECT to_regclass('webauthn_challenges')"))
        self.assertEqual(0, self.scratch.alembic("upgrade", HEAD).returncode)

    def test_3_schema_sql_and_the_migration_chain_agree(self):
        from pathlib import Path

        from sqlalchemy import create_engine, text
        from app.db import db as appdb

        fresh_name = f"{self.scratch.name}_fresh"
        admin = admin_database_engine(appdb)
        tablas = ["webauthn_credentials", "webauthn_challenges"]
        consultas = {
            "columns": "SELECT table_name, column_name, data_type, is_nullable, COALESCE(column_default, '') FROM information_schema.columns WHERE table_name = ANY(:t)",
            "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid::regclass::text = ANY(:t)",
            "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE tablename = ANY(:t)",
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
                            return sorted(tuple(str(v).strip() for v in r) for r in c.execute(text(consulta), {"t": tablas}).all())
                    self.assertEqual(catalogo(self.scratch.engine), catalogo(fresh))
            fresh.dispose()
        finally:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
            admin.dispose()


@unittest.skipIf(_skip_reason(), _skip_reason())
class PasskeyApiTests(_AppCase):
    label = "l15"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.security.hashing import hash_password

        cls.scratch.execute("UPDATE users SET password_hash = :h", {"h": hash_password(PASSWORD)})

    def setUp(self):
        for tabla in ("webauthn_credentials", "webauthn_challenges", "rate_limit_counters", "recovery_codes"):
            self.addCleanup(self.scratch.execute, f"DELETE FROM {tabla}")
        self.addCleanup(self.scratch.execute, "UPDATE user_sessions SET revoked_at = NULL")
        parche = patch("app.security.password_policy.is_password_pwned", return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    # --- helpers ---------------------------------------------------------
    def register_options(self, user=OWNER, password=PASSWORD):
        return self.call(user, "post", "/auth/passkeys/register/options", json={"current_password": password})

    def register(self, user=OWNER, authenticator=None, nickname="Portátil", **create_kwargs) -> SoftAuthenticator:
        authenticator = authenticator or SoftAuthenticator()
        opciones = self.register_options(user)
        self.assertEqual(200, opciones.status_code, opciones.get_json())
        credencial = authenticator.create(opciones.get_json()["data"], **create_kwargs)
        respuesta = self.call(user, "post", "/auth/passkeys/register/verify",
                              json={"credential": credencial, "nickname": nickname})
        self.assertEqual(201, respuesta.status_code, respuesta.get_json())
        return authenticator

    def login_options(self, client=None):
        client = client or self.app.test_client()
        respuesta = client.post("/auth/passkeys/login/options", environ_base={"REMOTE_ADDR": "198.51.100.30"})
        self.assertEqual(200, respuesta.status_code, respuesta.get_json())
        return respuesta.get_json()["data"]

    def login_verify(self, assertion, client=None):
        client = client or self.app.test_client()
        return client.post("/auth/passkeys/login/verify", json={"credential": assertion, "remember": False},
                           environ_base={"REMOTE_ADDR": "198.51.100.30"})

    def stored(self, authenticator):
        return self.scratch.rows("SELECT * FROM webauthn_credentials WHERE credential_id = :c",
                                 {"c": authenticator.credential_id})

    # --- registro ----------------------------------------------------------
    def test_registration_needs_a_session_and_the_current_password(self):
        self.assertEqual(401, self.app.test_client().post("/auth/passkeys/register/options",
                                                          json={"current_password": PASSWORD}).status_code)
        self.assertEqual(400, self.register_options(password="otra cosa").status_code)
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM webauthn_challenges"))

    def test_registration_options_are_bound_server_side_and_carry_no_secret(self):
        opciones = self.register_options().get_json()["data"]
        self.assertEqual("localhost", opciones["rp"]["id"])
        self.assertEqual("none", opciones.get("attestation", "none"))
        self.assertEqual("required", opciones["authenticatorSelection"]["residentKey"])
        self.assertEqual("required", opciones["authenticatorSelection"]["userVerification"])
        fila = self.scratch.rows("SELECT * FROM webauthn_challenges")[0]
        self.assertEqual(("registration", OWNER), (fila["purpose"], fila["user_id"]))
        self.assertIsNotNone(fila["session_id"])
        self.assertNotIn(opciones["challenge"], str(fila), "solo se guarda el hash del challenge")

    def test_a_registered_passkey_stores_only_public_data_and_is_listed(self):
        soft = self.register()
        fila = self.stored(soft)[0]
        self.assertEqual((OWNER, "Portátil", 0, ["internal"]),
                         (fila["user_id"], fila["nickname"], fila["sign_count"], list(fila["transports"])))
        self.assertGreater(len(bytes(fila["public_key"])), 60)
        lista = self.call(OWNER, "get", "/auth/passkeys").get_json()["data"]
        self.assertEqual(["Portátil"], [p["nickname"] for p in lista])
        self.assertEqual({"id", "nickname", "created_at", "last_used_at", "transports"}, set(lista[0]))
        self.assertEqual([], self.call(OTHER, "get", "/auth/passkeys").get_json()["data"])

    def test_registration_verify_rejects_replays_wrong_origins_rps_and_foreign_challenges(self):
        soft = SoftAuthenticator()
        opciones = self.register_options().get_json()["data"]
        malas = [
            ("otro origen", soft.create(opciones, origin="https://evil.example")),
            ("otro RP", soft.create(opciones, rp_id="evil.example")),
        ]
        for nombre, credencial in malas:
            with self.subTest(nombre):
                opciones = self.register_options().get_json()["data"]
                credencial = soft.create(opciones, **({"origin": "https://evil.example"} if nombre == "otro origen"
                                                     else {"rp_id": "evil.example"}))
                respuesta = self.call(OWNER, "post", "/auth/passkeys/register/verify",
                                      json={"credential": credencial, "nickname": "x"})
                self.assertEqual(400, respuesta.status_code)

        # El challenge de OWNER no sirve desde la sesión de OTHER.
        opciones = self.register_options().get_json()["data"]
        ajena = self.call(OTHER, "post", "/auth/passkeys/register/verify",
                          json={"credential": soft.create(opciones), "nickname": "x"})
        self.assertEqual(400, ajena.status_code)

        # Un challenge inventado, y el mismo challenge dos veces.
        inventado = soft.create({**opciones, "challenge": b64u(b"x" * 32)})
        self.assertEqual(400, self.call(OWNER, "post", "/auth/passkeys/register/verify",
                                        json={"credential": inventado, "nickname": "x"}).status_code)
        opciones = self.register_options().get_json()["data"]
        credencial = soft.create(opciones)
        self.assertEqual(201, self.call(OWNER, "post", "/auth/passkeys/register/verify",
                                        json={"credential": credencial, "nickname": "x"}).status_code)
        self.assertEqual(400, self.call(OWNER, "post", "/auth/passkeys/register/verify",
                                        json={"credential": credencial, "nickname": "x"}).status_code)
        self.assertEqual(1, self.scratch.scalar("SELECT count(*) FROM webauthn_credentials"))

    def test_an_expired_challenge_is_useless(self):
        soft = SoftAuthenticator()
        opciones = self.register_options().get_json()["data"]
        self.scratch.execute("UPDATE webauthn_challenges SET expires_at = NOW() - INTERVAL '1 second'")
        respuesta = self.call(OWNER, "post", "/auth/passkeys/register/verify",
                              json={"credential": soft.create(opciones), "nickname": "x"})
        self.assertEqual(400, respuesta.status_code)

    # --- verificación de usuario (B0.2) --------------------------------------
    def test_registration_requires_user_verification(self):
        soft = SoftAuthenticator()
        sin_uv = soft.create(self.register_options().get_json()["data"], uv=False)
        respuesta = self.call(OWNER, "post", "/auth/passkeys/register/verify",
                              json={"credential": sin_uv, "nickname": "Sin UV"})
        self.assertEqual(400, respuesta.status_code)
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM webauthn_credentials"))
        self.register(authenticator=soft)  # la misma llave, con UV, sí entra
        self.assertEqual(1, self.scratch.scalar("SELECT count(*) FROM webauthn_credentials"))

    def test_login_requires_user_verification(self):
        soft = self.register()
        opciones = self.login_options()
        self.assertEqual("required", opciones["userVerification"])
        sin_uv = self.login_verify(soft.get(opciones, uv=False))
        self.assertEqual((400, GENERIC), (sin_uv.status_code, sin_uv.get_json()["message"]))
        self.assertIsNone(self.stored(soft)[0]["last_used_at"], "un intento sin UV no toca la credencial")
        self.assertEqual(200, self.login_verify(soft.get(self.login_options())).status_code)

    # --- login -------------------------------------------------------------
    def test_a_passkey_logs_in_with_a_fresh_session_and_updates_its_counter(self):
        soft = self.register()
        cliente = self.app.test_client()
        cliente.set_cookie("albumfp_session", "valor-que-eligio-un-atacante")
        opciones = self.login_options(cliente)
        self.assertEqual([], opciones.get("allowCredentials", []), "login sin usuario: no revela cuentas")
        respuesta = self.login_verify(soft.get(opciones), cliente)
        self.assertEqual(200, respuesta.status_code, respuesta.get_json())
        self.assertEqual("l10a_owner", respuesta.get_json()["data"]["user"]["username"])
        galletas = " ".join(respuesta.headers.getlist("Set-Cookie"))
        self.assertIn("albumfp_session=", galletas)
        self.assertNotIn("valor-que-eligio-un-atacante", galletas, "nunca se adopta una sesión fijada")
        self.assertEqual(200, cliente.get("/auth/me").status_code)
        fila = self.stored(soft)[0]
        self.assertEqual(1, fila["sign_count"])
        self.assertIsNotNone(fila["last_used_at"])

    def test_the_login_challenge_is_single_use_and_bound_to_the_origin_and_rp(self):
        soft = self.register()
        opciones = self.login_options()
        afirmacion = soft.get(opciones)
        self.assertEqual(200, self.login_verify(afirmacion).status_code)
        repetida = self.login_verify(afirmacion)
        self.assertEqual((400, GENERIC), (repetida.status_code, repetida.get_json()["message"]))
        for nombre, kwargs in (("origen", {"origin": "https://evil.example"}), ("rp", {"rp_id": "evil.example"})):
            with self.subTest(nombre):
                respuesta = self.login_verify(soft.get(self.login_options(), **kwargs))
                self.assertEqual((400, GENERIC), (respuesta.status_code, respuesta.get_json()["message"]))

    def test_unknown_revoked_and_foreign_credentials_all_fail_the_same_way(self):
        soft = self.register()
        desconocida = SoftAuthenticator()
        desconocida.user_handle = soft.user_handle
        self.assertEqual(GENERIC, self.login_verify(desconocida.get(self.login_options())).get_json()["message"])

        # Un userHandle que no es el de la dueña de la credencial.
        otro = self.register(OTHER, nickname="De otra")
        cruzada = self.login_verify(soft.get(self.login_options(), user_handle=otro.user_handle))
        self.assertEqual((400, GENERIC), (cruzada.status_code, cruzada.get_json()["message"]))

        pk = self.call(OWNER, "get", "/auth/passkeys").get_json()["data"][0]["id"]
        self.assertEqual(404, self.call(OTHER, "delete", f"/auth/passkeys/{pk}").status_code)
        self.assertEqual(200, self.call(OWNER, "delete", f"/auth/passkeys/{pk}").status_code)
        revocada = self.login_verify(soft.get(self.login_options()))
        self.assertEqual((400, GENERIC), (revocada.status_code, revocada.get_json()["message"]))

    def test_a_counter_regression_fails_and_a_zero_counter_authenticator_keeps_working(self):
        soft = self.register()
        self.assertEqual(200, self.login_verify(soft.get(self.login_options(), sign_count=5)).status_code)
        with self.assertLogs(level="WARNING") as registro:
            atras = self.login_verify(soft.get(self.login_options(), sign_count=3))
        self.assertEqual(400, atras.status_code)
        self.assertTrue(any("contador" in linea for linea in registro.output))
        self.assertEqual(5, self.stored(soft)[0]["sign_count"], "un fallo no toca la credencial")

        cero = self.register(OTHER, SoftAuthenticator(counter=False), nickname="Sin contador")
        for _ in range(2):
            self.assertEqual(200, self.login_verify(cero.get(self.login_options())).status_code)

    def test_login_options_and_verify_are_rate_limited(self):
        with patch.dict("os.environ", {"RATE_LIMIT_LOGIN_PER_15_MIN": "2"}):
            cliente = self.app.test_client()
            for _ in range(2):
                self.assertEqual(200, cliente.post("/auth/passkeys/login/options",
                                                   environ_base={"REMOTE_ADDR": "203.0.113.50"}).status_code)
            bloqueada = cliente.post("/auth/passkeys/login/options", environ_base={"REMOTE_ADDR": "203.0.113.50"})
            self.assertEqual(429, bloqueada.status_code)
            self.assertIn("Retry-After", bloqueada.headers)

    # --- gestión ----------------------------------------------------------
    def test_rename_validates_and_stays_private(self):
        self.register()
        pk = self.call(OWNER, "get", "/auth/passkeys").get_json()["data"][0]["id"]
        self.assertEqual(400, self.call(OWNER, "patch", f"/auth/passkeys/{pk}", json={"nickname": " "}).status_code)
        self.assertEqual(400, self.call(OWNER, "patch", f"/auth/passkeys/{pk}", json={"nickname": "x" * 61}).status_code)
        self.assertEqual(404, self.call(OTHER, "patch", f"/auth/passkeys/{pk}", json={"nickname": "Mía"}).status_code)
        self.assertEqual(200, self.call(OWNER, "patch", f"/auth/passkeys/{pk}", json={"nickname": "Llave USB"}).status_code)
        self.assertEqual(["Llave USB"], [p["nickname"] for p in self.call(OWNER, "get", "/auth/passkeys").get_json()["data"]])

    def test_password_and_recovery_codes_still_work_next_to_a_passkey(self):
        self.register()
        cliente = self.app.test_client()
        self.assertEqual(200, cliente.post("/auth/login", json={"username": "l10a_owner", "password": PASSWORD},
                                           environ_base={"REMOTE_ADDR": "198.51.100.31"}).status_code)
        codigos = self.call(OWNER, "post", "/auth/recovery-codes", json={"current_password": PASSWORD}).get_json()["data"]["codes"]
        recuperada = self.app.test_client().post("/auth/recover", json={
            "username": "l10a_owner", "code": codigos[0], "new_password": "Frase nueva tras la passkey 2026"},
            environ_base={"REMOTE_ADDR": "198.51.100.32"})
        self.assertEqual(200, recuperada.status_code, recuperada.get_json())
        from app.security.hashing import hash_password
        self.scratch.execute("UPDATE users SET password_hash = :h WHERE id = :u", {"h": hash_password(PASSWORD), "u": OWNER})


if __name__ == "__main__":
    unittest.main()
