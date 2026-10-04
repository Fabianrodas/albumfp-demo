"""F04 contra PostgreSQL de verdad: migración 0029 y comentarios de asset.

Arnés de L10A (`test_asset_membership_db.py`). Mapa de acceso del fixture:
- OWNER es dueño de todo salvo 900208 (de OTHER, álbum privado X);
- COLLAB tiene `write` (organize) sobre el álbum A: 900201 (también en B),
  900204 (video);
- el álbum P es público (lo lee cualquier cuenta) y tiene el enlace público
  PUBLIC_TOKEN: 900206;
- el álbum B es privado del dueño: 900205.
"""
import io
import json
import unittest
import zipfile
from unittest.mock import patch

try:
    from tests.test_asset_membership_db import (ALBUM_B, ALBUM_P, COLLAB, OTHER, OWNER, PUBLIC_TOKEN, STRANGER,
                                                Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url,
                                                seed_0023)
except ImportError:  # discover -s tests importa los módulos sin el paquete
    from test_asset_membership_db import (ALBUM_B, ALBUM_P, COLLAB, OTHER, OWNER, PUBLIC_TOKEN, STRANGER,
                                          Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url,
                                          seed_0023)

HEAD = "0029_asset_comments"
PREVIOUS = "0028_webauthn_passkeys"
NOT_FOUND = "Recuerdo no encontrado"


@unittest.skipIf(_skip_reason(), _skip_reason())
class CommentMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch("f04mig")
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

    def insert(self, body, asset=900205, user=OWNER):
        self.scratch.execute("INSERT INTO asset_comments (asset_id, user_id, body) VALUES (:a, :u, :b)",
                             {"a": asset, "u": user, "b": body})

    def test_1_shape_constraints_and_cascades(self):
        self.assertEqual(HEAD, self.scratch.version())
        columnas = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in self.scratch.rows(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'asset_comments'")}
        self.assertEqual({
            "id": ("bigint", "NO"), "asset_id": ("integer", "NO"), "user_id": ("integer", "NO"),
            "body": ("text", "NO"), "created_at": ("timestamp without time zone", "NO"),
            "updated_at": ("timestamp without time zone", "YES"),
        }, columnas)
        for malo in ("", " hola", "hola ", "x" * 2001):
            with self.subTest(malo=malo[:10]), self.assertRaises(Exception):
                self.insert(malo)
        self.insert("x" * 2000)
        self.insert("Del colaborador", asset=900201, user=COLLAB)
        self.insert("Ajeno", asset=900208, user=OTHER)
        # Borrar el asset para siempre se lleva sus comentarios; borrar una cuenta, los suyos.
        self.scratch.execute("DELETE FROM assets WHERE id = 900208")
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM asset_comments WHERE body = 'Ajeno'"))
        self.scratch.execute("DELETE FROM users WHERE id = :u", {"u": COLLAB})
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM asset_comments WHERE body = 'Del colaborador'"))
        indices = {r["indexname"] for r in self.scratch.rows("SELECT indexname FROM pg_indexes WHERE tablename = 'asset_comments'")}
        self.assertIn("idx_asset_comments_asset", indices)

    def test_2_downgrade_refuses_with_comments_then_round_trips(self):
        self.insert("Se queda")
        bajada = self.scratch.alembic("downgrade", PREVIOUS)
        self.assertNotEqual(0, bajada.returncode)
        self.assertIn("asset_comments", bajada.stdout + bajada.stderr)
        self.assertEqual(HEAD, self.scratch.version())
        self.scratch.execute("DELETE FROM asset_comments")
        self.assertEqual(0, self.scratch.alembic("downgrade", PREVIOUS).returncode)
        self.assertEqual(PREVIOUS, self.scratch.version())
        self.assertIsNone(self.scratch.scalar("SELECT to_regclass('asset_comments')"))
        self.assertEqual(0, self.scratch.alembic("upgrade", HEAD).returncode)
        self.assertEqual(HEAD, self.scratch.version())

    def test_3_schema_sql_and_the_migration_agree(self):
        from pathlib import Path

        from sqlalchemy import create_engine, text
        from app.db import db as appdb

        fresh_name = f"{self.scratch.name}_fresh"
        admin = admin_database_engine(appdb)
        consultas = {
            "columns": "SELECT column_name, data_type, is_nullable, COALESCE(column_default, '') FROM information_schema.columns WHERE table_name = 'asset_comments'",
            "constraints": "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'asset_comments'::regclass",
            "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'asset_comments'",
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

            def catalogo(engine, consulta):
                with engine.connect() as c:
                    return sorted(tuple(str(v).strip() for v in r) for r in c.execute(text(consulta)).all())
            for nombre, consulta in consultas.items():
                with self.subTest(nombre):
                    self.assertEqual(catalogo(self.scratch.engine, consulta), catalogo(fresh, consulta))
            fresh.dispose()
        finally:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
            admin.dispose()


@unittest.skipIf(_skip_reason(), _skip_reason())
class CommentApiTests(_AppCase):
    label = "f04"

    def setUp(self):
        self.addCleanup(self.scratch.execute, "DELETE FROM asset_comments")
        self.addCleanup(self.scratch.execute, "DELETE FROM rate_limit_counters")

    # --- helpers --------------------------------------------------------------
    def post(self, user, asset, body):
        return self.call(user, "post", f"/api/media/{asset}/comments", json={"body": body})

    def listing(self, user, asset, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.call(user, "get", f"/api/media/{asset}/comments" + (f"?{query}" if query else ""))

    def public(self, asset, client=None):
        return (client or self.app.test_client()).get(f"/api/shared/{PUBLIC_TOKEN}/media/{asset}/comments")

    # --- create / list / edit / delete ----------------------------------------
    def test_the_full_lifecycle_for_the_author(self):
        creado = self.post(OWNER, 900205, "  Hola\r\nmundo  ")
        self.assertEqual(201, creado.status_code, creado.get_json())
        comentario = creado.get_json()["data"]
        self.assertEqual("Hola\nmundo", comentario["body"])
        self.assertEqual({"id", "username", "full_name", "has_avatar"}, set(comentario["author"]))
        self.assertEqual("l10a_owner", comentario["author"]["username"])
        self.assertFalse(comentario["edited"])
        self.assertTrue(comentario["is_own"])

        lista = self.listing(OWNER, 900205).get_json()
        self.assertEqual([comentario["id"]], [c["id"] for c in lista["data"]])
        self.assertEqual(1, lista["meta"]["pagination"]["total"])
        self.assertNotIn("avatar_path", json.dumps(lista))

        editado = self.call(OWNER, "patch", f"/api/comments/{comentario['id']}", json={"body": "Hola otra vez"})
        self.assertEqual(200, editado.status_code)
        self.assertTrue(editado.get_json()["data"]["edited"])
        self.assertIsNotNone(editado.get_json()["data"]["updated_at"])

        self.assertEqual(200, self.call(OWNER, "delete", f"/api/comments/{comentario['id']}").status_code)
        self.assertEqual([], self.listing(OWNER, 900205).get_json()["data"])

    def test_only_the_author_can_edit_or_delete(self):
        propio = self.post(COLLAB, 900201, "Del colaborador").get_json()["data"]
        for intruso in (OWNER, STRANGER, OTHER):
            with self.subTest(intruso=intruso):
                self.assertEqual(404, self.call(intruso, "patch", f"/api/comments/{propio['id']}", json={"body": "x"}).status_code)
                self.assertEqual(404, self.call(intruso, "delete", f"/api/comments/{propio['id']}").status_code)
        dueño = self.listing(OWNER, 900201).get_json()["data"][0]
        self.assertFalse(dueño["is_own"], "el dueño del recuerdo lo ve, pero no es suyo")
        self.assertEqual("Del colaborador", dueño["body"])
        self.assertEqual(404, self.call(COLLAB, "patch", "/api/comments/99999999", json={"body": "x"}).status_code)

    # --- quién puede leer y comentar -----------------------------------------
    def test_account_collaborators_read_and_write_including_read_only_ones(self):
        self.assertEqual(201, self.post(COLLAB, 900201, "desde A").status_code)
        self.assertEqual(201, self.post(COLLAB, 900204, "en el video").status_code)
        self.scratch.execute(
            "INSERT INTO album_shares (id, album_id, shared_by, share_type, shared_with_user_id, permission, active) "
            "VALUES (900499, :b, :o, 'account', :x, 'read', TRUE)", {"b": ALBUM_B, "o": OWNER, "x": OTHER})
        self.addCleanup(self.scratch.execute, "DELETE FROM album_shares WHERE id = 900499")
        self.assertEqual(201, self.post(OTHER, 900205, "solo lectura, pero comento").status_code)
        self.assertEqual(["solo lectura, pero comento"], [c["body"] for c in self.listing(OTHER, 900205).get_json()["data"]])

    def test_strangers_and_inaccessible_assets_fail_closed_identically(self):
        self.post(OWNER, 900205, "privado")
        casos = [(STRANGER, 900205), (COLLAB, 900205), (OWNER, 900208), (OWNER, 99999999), (OWNER, 900202),
                 (OWNER, 900207)]
        for user, asset in casos:
            with self.subTest(user=user, asset=asset):
                lectura = self.listing(user, asset)
                escritura = self.post(user, asset, "intento")
                self.assertEqual((404, NOT_FOUND), (lectura.status_code, lectura.get_json()["message"]))
                self.assertEqual((404, NOT_FOUND), (escritura.status_code, escritura.get_json()["message"]))
        self.assertEqual(401, self.app.test_client().get("/api/media/900205/comments").status_code)
        self.assertEqual(401, self.app.test_client().post("/api/media/900206/comments", json={"body": "x"}).status_code)

    def test_a_public_album_is_readable_and_commentable_by_any_account(self):
        self.assertEqual(201, self.post(STRANGER, 900206, "desde fuera").status_code)
        self.assertEqual(["desde fuera"], [c["body"] for c in self.listing(OTHER, 900206).get_json()["data"]])

    # --- enlace público: solo lectura ----------------------------------------
    def test_anonymous_public_share_reads_comments_and_can_never_write(self):
        self.post(OWNER, 900206, "visible por el enlace")
        self.post(OWNER, 900205, "fuera del álbum compartido")
        respuesta = self.public(900206)
        self.assertEqual(200, respuesta.status_code, respuesta.get_json())
        self.assertEqual(["visible por el enlace"], [c["body"] for c in respuesta.get_json()["data"]])
        self.assertNotIn("is_own", respuesta.get_json()["data"][0])
        self.assertEqual(404, self.public(900205).status_code, "otra pertenencia del mismo dueño no se ve")
        self.assertEqual(404, self.app.test_client().get("/api/shared/token-falso/media/900206/comments").status_code)
        for metodo in ("post", "patch", "delete"):
            with self.subTest(metodo=metodo):
                self.assertEqual(405, getattr(self.app.test_client(), metodo)(
                    f"/api/shared/{PUBLIC_TOKEN}/media/900206/comments", json={"body": "anónimo"}).status_code)
        self.assertEqual(1, self.scratch.scalar("SELECT count(*) FROM asset_comments WHERE asset_id = 900206"))

    def test_removing_the_membership_immediately_hides_asset_and_comments(self):
        self.post(OWNER, 900206, "antes de sacarlo")
        self.assertEqual(200, self.call(OWNER, "delete", f"/api/albums/{ALBUM_P}/assets/900206").status_code)
        self.addCleanup(self.call, OWNER, "put", f"/api/albums/{ALBUM_P}/assets/900206")
        self.assertEqual(404, self.public(900206).status_code)
        # El comentario sigue atado al asset: el dueño lo sigue viendo.
        self.assertEqual(["antes de sacarlo"], [c["body"] for c in self.listing(OWNER, 900206).get_json()["data"]])

    def test_a_password_protected_share_needs_the_unlock(self):
        from app.security.hashing import hash_password

        self.post(OWNER, 900206, "tras la contraseña")
        self.scratch.execute("UPDATE album_shares SET password_hash = :h WHERE id = 900402", {"h": hash_password("clave-enlace")})
        self.addCleanup(self.scratch.execute, "UPDATE album_shares SET password_hash = NULL WHERE id = 900402")
        cliente = self.app.test_client()
        bloqueado = self.public(900206, cliente)
        self.assertEqual((401, "share_password_required"), (bloqueado.status_code, bloqueado.get_json().get("code")))
        self.assertEqual(200, cliente.post(f"/api/shared/{PUBLIC_TOKEN}/unlock", json={"password": "clave-enlace"}).status_code)
        self.assertEqual(200, self.public(900206, cliente).status_code)

    # --- ciclo de vida del asset ---------------------------------------------
    def test_comments_survive_archive_trash_restore_and_membership_changes(self):
        self.post(OWNER, 900205, "persistente")
        archivar = lambda valor: self.call(OWNER, "patch", "/api/media/900205/archive", json={"archived": valor})
        self.assertEqual(200, archivar(True).status_code)
        self.assertEqual(1, len(self.listing(OWNER, 900205).get_json()["data"]))
        self.assertEqual(200, archivar(False).status_code)

        self.assertEqual(200, self.call(OWNER, "delete", "/api/media/900205").status_code)
        self.assertEqual(404, self.listing(OWNER, 900205).status_code, "en la papelera no hay detalle ni comentarios")
        self.assertEqual(1, self.scratch.scalar("SELECT count(*) FROM asset_comments WHERE asset_id = 900205"))
        self.assertEqual(200, self.call(OWNER, "post", "/api/media/900205/restore").status_code)
        self.assertEqual(1, len(self.listing(OWNER, 900205).get_json()["data"]))

        self.assertEqual(200, self.call(OWNER, "delete", f"/api/albums/{ALBUM_B}/assets/900205").status_code)
        self.assertEqual(1, len(self.listing(OWNER, 900205).get_json()["data"]), "suelto sigue siendo del dueño")
        self.assertIn(self.call(OWNER, "put", f"/api/albums/{ALBUM_B}/assets/900205").status_code, (200, 201))
        self.assertEqual(1, len(self.listing(OWNER, 900205).get_json()["data"]))

    def test_permanent_delete_removes_the_comments(self):
        self.scratch.execute("INSERT INTO asset_comments (asset_id, user_id, body) VALUES (900202, :o, 'en papelera')",
                             {"o": OWNER})
        self.assertEqual(200, self.call(OWNER, "delete", "/api/media/900202/permanent").status_code)
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM asset_comments WHERE asset_id = 900202"))

    # --- texto ----------------------------------------------------------------
    def test_text_contract(self):
        xss = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        self.assertEqual(xss, self.post(OWNER, 900205, xss).get_json()["data"]["body"])
        self.assertEqual("Ñandú 🦤 日本語", self.post(OWNER, 900205, "Ñandú 🦤 日本語").get_json()["data"]["body"])
        self.assertEqual(201, self.post(OWNER, 900205, "x" * 2000).status_code)
        for malo in ("x" * 2001, "   ", "", "nul\x00"):
            with self.subTest(malo=repr(malo[:8])):
                self.assertEqual(400, self.post(OWNER, 900205, malo).status_code)
        crudo = self.app.test_client()
        from app.security.sessions import session_cookie_name
        sesion = self.sessions[OWNER]
        crudo.set_cookie(session_cookie_name(), sesion["session_token"])
        solitario = crudo.post("/api/media/900205/comments", data='{"body": "\\ud800"}',
                               content_type="application/json", headers={"X-CSRF-Token": sesion["csrf_token"]})
        self.assertEqual(400, solitario.status_code)
        self.assertEqual(400, self.call(OWNER, "post", "/api/media/900205/comments", json={"body": 5}).status_code)

    # --- paginación y consultas ----------------------------------------------
    def test_pagination_is_bounded_stable_and_free_of_n_plus_one(self):
        from sqlalchemy import event

        self.scratch.execute(
            "INSERT INTO asset_comments (asset_id, user_id, body) "
            "SELECT 900201, CASE WHEN g % 2 = 0 THEN :o ELSE :c END, 'c' || g FROM generate_series(1, 25) g",
            {"o": OWNER, "c": COLLAB})
        tercera = self.listing(OWNER, 900201, page=3, per_page=10).get_json()
        self.assertEqual(["c21", "c22", "c23", "c24", "c25"], [c["body"] for c in tercera["data"]])
        self.assertEqual({"page": 3, "per_page": 10, "total": 25, "total_pages": 3},
                         {k: tercera["meta"]["pagination"][k] for k in ("page", "per_page", "total", "total_pages")})
        self.assertEqual(50, self.listing(OWNER, 900201, per_page=500).get_json()["meta"]["pagination"]["per_page"])

        def contar(**params):
            sentencias = []
            oyente = lambda *args: sentencias.append(args[2])
            event.listen(self.scratch.engine, "before_cursor_execute", oyente)
            try:
                self.assertEqual(200, self.listing(OWNER, 900201, **params).status_code)
            finally:
                event.remove(self.scratch.engine, "before_cursor_execute", oyente)
            return len([s for s in sentencias if "asset_comments" in s])
        self.assertEqual(contar(per_page=1), contar(per_page=25), "author data comes with the page, not per comment")
        self.assertLessEqual(contar(per_page=25), 2)

    def test_rapid_creation_is_rate_limited(self):
        with patch.dict("os.environ", {"RATE_LIMIT_COMMENTS_PER_10_MIN": "3"}):
            for i in range(3):
                self.assertEqual(201, self.post(OWNER, 900205, f"rápido {i}").status_code)
            bloqueado = self.post(OWNER, 900205, "uno más")
        self.assertEqual(429, bloqueado.status_code)
        self.assertIn("Retry-After", bloqueado.headers)

    # --- exportación ----------------------------------------------------------
    def test_the_portable_export_carries_the_comments_of_the_exported_assets(self):
        self.post(COLLAB, 900201, "del colaborador en mi recuerdo")
        self.post(OWNER, 900206, "mío")
        self.post(OWNER, 900205, "en un recuerdo que irá a la papelera")
        self.assertEqual(200, self.call(OWNER, "delete", "/api/media/900205").status_code)
        self.addCleanup(self.call, OWNER, "post", "/api/media/900205/restore")
        self.scratch.execute("INSERT INTO asset_comments (asset_id, user_id, body) VALUES (900208, :x, 'ajeno')",
                             {"x": OTHER})
        respuesta = self.call(OWNER, "get", "/api/export/download")
        self.assertEqual(200, respuesta.status_code)
        with zipfile.ZipFile(io.BytesIO(respuesta.get_data())) as z:
            manifest = json.loads(z.read("albumfp-export/metadata/albumfp.json"))
            readme = z.read("albumfp-export/README.txt").decode()
        self.assertEqual(1, manifest["schema_version"])
        cuerpos = sorted(c["body"] for c in manifest["comments"])
        self.assertEqual(["del colaborador en mi recuerdo", "mío"], cuerpos)
        self.assertEqual(2, manifest["counts"]["comments"])
        uno = next(c for c in manifest["comments"] if c["asset_id"] == 900201)
        self.assertEqual({"id", "asset_id", "author", "body", "created_at", "updated_at"}, set(uno))
        self.assertEqual({"username": "l10a_collab", "full_name": "L10A Collab"}, uno["author"])
        self.assertIn("comentarios", readme)


if __name__ == "__main__":
    unittest.main()
