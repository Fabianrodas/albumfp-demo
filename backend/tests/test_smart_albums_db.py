"""L11 contra PostgreSQL de verdad: migración 0025 y álbumes inteligentes.

Reutiliza el arnés de L10A (`test_asset_membership_db.py`): cada clase clona la
plantilla DESECHABLE de `ALBUMFP_L10A_TEMPLATE_DB` (en 0023), siembra sus
fixtures, migra con Alembic en un subproceso (0023 -> 0024 -> 0025) y borra el
clon al terminar. Nunca abre la base de `.env`. Sin la variable se omite.

Fixtures del dueño tras migrar (ver el arnés): 900201 Playa (A y B, favorita,
tag 900301), 900202 en papelera, 900203 archivada, 900204 video, 900205 Rio
(B), 900206 Publica (P), 900207 en un álbum inactivo (fuera de alcance).
"""
import unittest

try:
    from tests.test_asset_membership_db import (
        ALBUM_A, ALBUM_B, ALBUM_I, ALBUM_P, ALBUM_X, COLLAB, OTHER, OWNER, STRANGER,
        Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023,
    )
except ImportError:  # discover -s tests importa los módulos sin el paquete
    from test_asset_membership_db import (
        ALBUM_A, ALBUM_B, ALBUM_I, ALBUM_P, ALBUM_X, COLLAB, OTHER, OWNER, STRANGER,
        Scratch, _AppCase, _skip_reason, admin_database_engine, demo_database_url, seed_0023,
    )

# Las pruebas de migración de L11 fijan su propia revisión: desde L12 head es
# 0026, y la evidencia de 0025 no debe depender de lo que venga después.
HEAD = "0025_smart_albums"
PREVIOUS = "0024_asset_album_membership"
STALE = "smart_album_stale_reference"
L10A_TITLES = {900201, 900203, 900204, 900205, 900206}  # todo lo "L10A" en alcance y fuera de papelera


@unittest.skipIf(_skip_reason(), _skip_reason())
class SmartAlbumMigrationTests(unittest.TestCase):
    """0024 -> 0025, la tabla, la bajada que se niega y la paridad con schema.sql."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch("l11mig")
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

    def test_1_head_is_0025_and_the_table_exists_empty(self):
        self.assertEqual(HEAD, self.scratch.version())
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM smart_albums"))
        columnas = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in self.scratch.rows(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'smart_albums'")}
        self.assertEqual({
            "id": ("integer", "NO"), "user_id": ("integer", "NO"),
            "titulo": ("character varying", "NO"), "descripcion": ("text", "YES"),
            "filters": ("jsonb", "NO"), "created_at": ("timestamp without time zone", "NO"),
            "updated_at": ("timestamp without time zone", "YES"),
        }, columnas)

    def test_2_the_database_refuses_filters_that_are_not_an_object(self):
        for invalido in ("[]", '"year"', "2026", "null"):
            with self.subTest(invalido):
                with self.assertRaises(Exception):
                    self.scratch.execute(
                        "INSERT INTO smart_albums (user_id, titulo, filters) VALUES (:o, 'x', CAST(:f AS jsonb))",
                        {"o": OWNER, "f": invalido})

    def test_3_downgrade_refuses_while_definitions_exist_then_succeeds_when_empty(self):
        assets = self.scratch.scalar("SELECT count(*) FROM assets")
        self.scratch.execute(
            "INSERT INTO smart_albums (user_id, titulo, filters) VALUES (:o, 'Guardada', '{\"year\": 2024}')",
            {"o": OWNER})
        bajada = self.scratch.alembic("downgrade", PREVIOUS)
        self.assertNotEqual(0, bajada.returncode)
        self.assertIn("smart_albums", bajada.stdout + bajada.stderr)
        self.assertEqual(HEAD, self.scratch.version())
        self.assertEqual(1, self.scratch.scalar("SELECT count(*) FROM smart_albums"), "no perdió la definición")

        self.scratch.execute("DELETE FROM smart_albums")
        bajada = self.scratch.alembic("downgrade", PREVIOUS)
        self.assertEqual(0, bajada.returncode, bajada.stderr)
        self.assertEqual(PREVIOUS, self.scratch.version())
        self.assertIsNone(self.scratch.scalar("SELECT to_regclass('smart_albums')"))
        self.assertEqual(assets, self.scratch.scalar("SELECT count(*) FROM assets"), "la bajada no toca assets")

        subida = self.scratch.alembic("upgrade", HEAD)
        self.assertEqual(0, subida.returncode, subida.stderr)
        self.assertEqual(HEAD, self.scratch.version())
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM smart_albums"))

    def test_4_deleting_the_owner_cascades_only_their_definitions(self):
        self.scratch.execute(
            "INSERT INTO smart_albums (user_id, titulo, filters) VALUES (:o, 'Del dueño', '{\"year\": 2024}'), "
            "(:x, 'Del otro', '{\"year\": 2024}')", {"o": STRANGER, "x": OTHER})
        self.scratch.execute("DELETE FROM users WHERE id = :s", {"s": STRANGER})
        self.assertEqual(["Del otro"], [r["titulo"] for r in self.scratch.rows(
            "SELECT titulo FROM smart_albums WHERE user_id IN (:s, :x)", {"s": STRANGER, "x": OTHER})])
        self.scratch.execute("DELETE FROM smart_albums")

    CATALOG = {
        "columns": """SELECT column_name, data_type, is_nullable, COALESCE(column_default, '')
                      FROM information_schema.columns WHERE table_name = 'smart_albums'""",
        "constraints": """SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                          WHERE conrelid = 'smart_albums'::regclass""",
        "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'smart_albums'",
    }

    def test_5_schema_sql_and_the_migration_chain_agree(self):
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
                            return sorted(tuple(str(v).strip() for v in r) for r in c.execute(text(consulta)).all())
                    self.assertEqual(catalogo(self.scratch.engine), catalogo(fresh))
            fresh.dispose()
        finally:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
            admin.dispose()


@unittest.skipIf(_skip_reason(), _skip_reason())
class SmartAlbumApiTests(_AppCase):
    label = "smart"

    # --- helpers ---------------------------------------------------------
    def create(self, user=OWNER, titulo="Inteligente", filters=None, **extra):
        body = {"titulo": titulo, "filters": filters if filters is not None else {"year": 2024}, **extra}
        return self.call(user, "post", "/api/smart-albums", json=body)

    def created_id(self, **kwargs) -> int:
        respuesta = self.create(**kwargs)
        self.assertEqual(201, respuesta.status_code, respuesta.get_json())
        smart_id = respuesta.get_json()["data"]["id"]
        self.addCleanup(self.scratch.execute, "DELETE FROM smart_albums WHERE id = :id", {"id": smart_id})
        return smart_id

    def results(self, smart_id: int, user=OWNER, query: str = "") -> list[int]:
        return self.ids(self.call(user, "get", f"/api/smart-albums/{smart_id}/media{query}"))

    def stored(self, smart_id: int) -> dict:
        return self.scratch.rows("SELECT titulo, descripcion, filters, updated_at FROM smart_albums WHERE id = :id",
                                 {"id": smart_id})[0]

    def object_files(self) -> list[str]:
        return sorted(p.relative_to(self.media_root).as_posix() for p in self.media_root.rglob("*") if p.is_file())

    def counts(self) -> dict:
        return {tabla: self.scratch.scalar(f"SELECT count(*) FROM {tabla}")
                for tabla in ("assets", "album_assets", "albums", "media_tags", "tags")}

    # --- CRUD + ownership -----------------------------------------------
    def test_owner_crud_round_trip_with_a_canonical_definition(self):
        respuesta = self.create(titulo="  Favoritos 2024 ", descripcion="Las mejores",
                                filters={"year": 2024, "favorite": True, "q": " playa "})
        self.assertEqual(201, respuesta.status_code, respuesta.get_json())
        creado = respuesta.get_json()["data"]
        smart_id = creado["id"]
        self.addCleanup(self.scratch.execute, "DELETE FROM smart_albums WHERE id = :id", {"id": smart_id})
        self.assertEqual("Favoritos 2024", creado["titulo"])
        self.assertEqual({"favorite": True, "q": "playa", "year": 2024}, creado["filters"])
        self.assertEqual({"favorite": True, "q": "playa", "year": 2024}, self.stored(smart_id)["filters"])

        lista = self.call(OWNER, "get", "/api/smart-albums")
        self.assertEqual(200, lista.status_code)
        self.assertIn(smart_id, [s["id"] for s in lista.get_json()["data"]])
        self.assertIn("pagination", lista.get_json()["meta"])

        detalle = self.call(OWNER, "get", f"/api/smart-albums/{smart_id}").get_json()["data"]
        self.assertEqual("Las mejores", detalle["descripcion"])
        self.assertEqual([], detalle["stale_references"])

        cambio = self.call(OWNER, "patch", f"/api/smart-albums/{smart_id}",
                           json={"titulo": "Playas", "filters": {"q": "playa"}})
        self.assertEqual(200, cambio.status_code, cambio.get_json())
        guardado = self.stored(smart_id)
        self.assertEqual(("Playas", {"q": "playa"}), (guardado["titulo"], guardado["filters"]))
        self.assertIsNotNone(guardado["updated_at"])

        self.assertEqual(200, self.call(OWNER, "delete", f"/api/smart-albums/{smart_id}").status_code)
        self.assertEqual(404, self.call(OWNER, "get", f"/api/smart-albums/{smart_id}").status_code)

    def test_invalid_definitions_and_titles_are_rejected(self):
        casos = {
            "sin título": {"titulo": "  ", "filters": {"year": 2024}},
            "título largo": {"titulo": "x" * 101, "filters": {"year": 2024}},
            "sin filtros": {"titulo": "x"},
            "filtros vacíos": {"titulo": "x", "filters": {}},
            "filtros no objeto": {"titulo": "x", "filters": [1]},
            "orden guardado": {"titulo": "x", "filters": {"year": 2024, "sort": "title"}},
            "clave desconocida": {"titulo": "x", "filters": {"country_code": "EC"}},
            "descripción no texto": {"titulo": "x", "filters": {"year": 2024}, "descripcion": 5},
            "álbum de otro": {"titulo": "x", "filters": {"album_id": ALBUM_X}},
            "álbum inactivo": {"titulo": "x", "filters": {"album_id": ALBUM_I}},
            "álbum inexistente": {"titulo": "x", "filters": {"album_id": 999999}},
            "tag inexistente": {"titulo": "x", "filters": {"tag_id": 999999}},
        }
        antes = self.scratch.scalar("SELECT count(*) FROM smart_albums")
        for etiqueta, body in casos.items():
            with self.subTest(etiqueta):
                respuesta = self.call(OWNER, "post", "/api/smart-albums", json=body)
                self.assertEqual(400, respuesta.status_code, respuesta.get_json())
                self.assertFalse(respuesta.get_json()["ok"])
        self.assertEqual(antes, self.scratch.scalar("SELECT count(*) FROM smart_albums"))

    def test_another_owners_tag_cannot_be_stored(self):
        self.scratch.execute("INSERT INTO tags (id, name, owner_id, created_by) VALUES (900390, 'ajena-l11', :x, :x)",
                             {"x": OTHER})
        self.addCleanup(self.scratch.execute, "DELETE FROM tags WHERE id = 900390")
        self.assertEqual(400, self.create(filters={"tag_id": 900390}).status_code)

    def test_update_revalidates_the_whole_resulting_definition(self):
        smart_id = self.created_id(filters={"q": "rio"})
        for body in ({"filters": {"album_id": ALBUM_X}}, {"filters": {"page": 2}}, {"filters": {}},
                     {"titulo": ""}, {"titulo": "x" * 101}):
            with self.subTest(body):
                self.assertEqual(400, self.call(OWNER, "patch", f"/api/smart-albums/{smart_id}", json=body).status_code)
        self.assertEqual({"q": "rio"}, self.stored(smart_id)["filters"], "nada inválido quedó guardado")

    def test_the_owner_comes_from_the_session_never_from_the_body(self):
        smart_id = self.created_id(user_id=OTHER, owner_id=OTHER)
        self.assertEqual(OWNER, self.scratch.scalar("SELECT user_id FROM smart_albums WHERE id = :id", {"id": smart_id}))

    def test_no_other_account_can_list_read_edit_delete_or_run_it(self):
        smart_id = self.created_id(filters={"album_id": ALBUM_A})
        for user in (COLLAB, STRANGER, OTHER):
            with self.subTest(user=user):
                self.assertNotIn(smart_id, [s["id"] for s in self.call(user, "get", "/api/smart-albums").get_json()["data"]])
                for method, path, kwargs in (
                    ("get", f"/api/smart-albums/{smart_id}", {}),
                    ("get", f"/api/smart-albums/{smart_id}/media", {}),
                    ("patch", f"/api/smart-albums/{smart_id}", {"json": {"titulo": "mío"}}),
                    ("delete", f"/api/smart-albums/{smart_id}", {}),
                ):
                    self.assertEqual(404, self.call(user, method, path, **kwargs).status_code, (method, path))
        self.assertEqual("Inteligente", self.stored(smart_id)["titulo"])

    def test_a_collaborator_cannot_build_one_over_the_album_they_collaborate_on(self):
        respuesta = self.create(user=COLLAB, filters={"album_id": ALBUM_A})
        self.assertEqual(400, respuesta.status_code, respuesta.get_json())

    def test_there_is_no_public_access(self):
        smart_id = self.created_id()
        cliente = self.app.test_client()
        for path in ("/api/smart-albums", f"/api/smart-albums/{smart_id}", f"/api/smart-albums/{smart_id}/media"):
            with self.subTest(path):
                self.assertEqual(401, cliente.get(path).status_code)

    def test_regular_album_listings_never_include_smart_albums(self):
        self.created_id(titulo="Nunca es un álbum")
        titulos = [a["titulo"] for a in self.call(OWNER, "get", "/api/albums?per_page=100").get_json()["data"]]
        self.assertNotIn("Nunca es un álbum", titulos)

    # --- execution --------------------------------------------------------
    def test_results_are_owner_assets_once_each_with_normal_search_defaults(self):
        smart_id = self.created_id(filters={"favorite": True})
        self.assertEqual([900201], self.results(smart_id), "900201 está en A y B y sale una vez")
        fila = self.call(OWNER, "get", f"/api/smart-albums/{smart_id}/media").get_json()["data"][0]
        self.assertTrue({"id", "file_type", "title", "is_favorite", "archived_at", "album_id", "album_titulo"} <= set(fila))

    def test_archived_default_only_and_exclude_and_the_trash_never(self):
        esperado = {
            None: L10A_TITLES,
            "only": {900203},
            "exclude": L10A_TITLES - {900203},
        }
        for modo, ids in esperado.items():
            with self.subTest(archived=modo):
                filtros = {"q": "l10a", **({"archived": modo} if modo else {})}
                resultados = self.results(self.created_id(filters=filtros), query="?per_page=100")
                self.assertEqual(ids, set(resultados))
                self.assertNotIn(900202, resultados, "la papelera nunca entra")
                self.assertNotIn(900207, resultados, "un álbum inactivo saca al asset del alcance")
                self.assertNotIn(900208, resultados, "nunca assets de otra cuenta")

    def test_dynamic_favorite_without_touching_the_definition(self):
        smart_id = self.created_id(filters={"favorite": True, "q": "rio"})
        antes = self.stored(smart_id)
        self.assertEqual([], self.results(smart_id))
        self.assertEqual(200, self.call(OWNER, "patch", "/api/media/900205/favorite", json={"is_favorite": True}).status_code)
        try:
            self.assertEqual([900205], self.results(smart_id))
        finally:
            self.assertEqual(200, self.call(OWNER, "patch", "/api/media/900205/favorite", json={"is_favorite": False}).status_code)
        self.assertEqual([], self.results(smart_id))
        self.assertEqual(antes, self.stored(smart_id), "la definición no cambió")

    def test_dynamic_album_membership_without_moving_bytes(self):
        smart_id = self.created_id(filters={"album_id": ALBUM_B})
        archivos = self.object_files()
        self.assertEqual({900201, 900205}, set(self.results(smart_id)))
        self.assertEqual(200, self.call(OWNER, "put", f"/api/albums/{ALBUM_B}/assets/900206").status_code)
        try:
            self.assertEqual({900201, 900205, 900206}, set(self.results(smart_id)))
        finally:
            self.assertEqual(200, self.call(OWNER, "delete", f"/api/albums/{ALBUM_B}/assets/900206").status_code)
        self.assertEqual({900201, 900205}, set(self.results(smart_id)))
        self.assertEqual(archivos, self.object_files())

    def test_a_multi_album_asset_appears_once_and_an_unassigned_one_appears(self):
        self.assertEqual(200, self.call(OWNER, "put", f"/api/albums/{ALBUM_P}/assets/900201").status_code)
        self.addCleanup(self.call, OWNER, "delete", f"/api/albums/{ALBUM_P}/assets/900201")
        self.assertEqual([900201], self.results(self.created_id(filters={"q": "playa"})), "en A, B y P: una vez")

        self.assertEqual(200, self.call(OWNER, "delete", f"/api/albums/{ALBUM_B}/assets/900205").status_code)
        self.addCleanup(self.call, OWNER, "put", f"/api/albums/{ALBUM_B}/assets/900205")
        self.assertEqual([900205], self.results(self.created_id(filters={"q": "rio"})), "suelto y aun así aparece")
        self.assertEqual([], self.results(self.created_id(filters={"q": "rio", "album_id": ALBUM_B})),
                         "con album_id hace falta la pertenencia")

    def test_pagination_is_bounded_and_uses_the_normal_envelope(self):
        smart_id = self.created_id(filters={"q": "l10a"})
        primera = self.call(OWNER, "get", f"/api/smart-albums/{smart_id}/media?page=1&per_page=2").get_json()
        self.assertEqual(2, len(primera["data"]))
        self.assertEqual({"page": 1, "per_page": 2, "total": 5, "total_pages": 3}, primera["meta"]["pagination"])
        ultima = self.call(OWNER, "get", f"/api/smart-albums/{smart_id}/media?page=3&per_page=2").get_json()
        self.assertEqual(1, len(ultima["data"]))
        todas = self.call(OWNER, "get", f"/api/smart-albums/{smart_id}/media?per_page=5000").get_json()
        self.assertEqual(100, todas["meta"]["pagination"]["per_page"], "el tope normal del servidor")
        ids = [i["id"] for i in primera["data"] + self.call(
            OWNER, "get", f"/api/smart-albums/{smart_id}/media?page=2&per_page=2").get_json()["data"] + ultima["data"]]
        self.assertEqual(len(ids), len(set(ids)), "las páginas no se solapan")

    def test_deleting_the_definition_leaves_every_asset_membership_and_file(self):
        smart_id = self.created_id(filters={"album_id": ALBUM_A})
        antes, archivos = self.counts(), self.object_files()
        self.assertEqual(200, self.call(OWNER, "delete", f"/api/smart-albums/{smart_id}").status_code)
        self.assertEqual(antes, self.counts())
        self.assertEqual(archivos, self.object_files())
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM assets WHERE deleted_at IS NOT NULL AND id <> 900202"))

    # --- stale references ------------------------------------------------
    def assert_fails_closed(self, smart_id: int):
        respuesta = self.call(OWNER, "get", f"/api/smart-albums/{smart_id}/media")
        cuerpo = respuesta.get_json()
        self.assertEqual(409, respuesta.status_code, cuerpo)
        self.assertEqual(STALE, cuerpo["code"])
        self.assertFalse(cuerpo["ok"])
        self.assertNotIn("data", cuerpo, "ningún resultado de repuesto")
        self.assertIn("ya no existe", cuerpo["message"])

    def test_a_deleted_tag_fails_closed_until_the_owner_repairs_it(self):
        creada = self.call(OWNER, "post", "/api/tags", json={"name": "l11-temporal", "album_id": ALBUM_B})
        self.assertIn(creada.status_code, (200, 201), creada.get_json())
        tag_id = creada.get_json()["data"]["id"]
        self.assertEqual(200, self.call(OWNER, "post", "/api/media/900205/tags", json={"tag_ids": [tag_id]}).status_code)
        smart_id = self.created_id(filters={"tag_id": tag_id})
        self.assertEqual([900205], self.results(smart_id))

        self.scratch.execute("DELETE FROM tags WHERE id = :t", {"t": tag_id})
        antes = self.stored(smart_id)
        self.assert_fails_closed(smart_id)
        self.assertEqual(antes, self.stored(smart_id), "leer no reescribe la definición")
        detalle = self.call(OWNER, "get", f"/api/smart-albums/{smart_id}").get_json()["data"]
        self.assertEqual(["tag_id"], detalle["stale_references"])

        arreglo = self.call(OWNER, "patch", f"/api/smart-albums/{smart_id}", json={"filters": {"q": "rio"}})
        self.assertEqual(200, arreglo.status_code, arreglo.get_json())
        self.assertEqual([900205], self.results(smart_id))

    def test_a_deleted_album_fails_closed_until_the_owner_repairs_it(self):
        album = self.call(OWNER, "post", "/api/albums", json={"titulo": "L11 temporal"}).get_json()["data"]["id"]
        self.assertEqual(200, self.call(OWNER, "put", f"/api/albums/{album}/assets/900205").status_code)
        smart_id = self.created_id(filters={"album_id": album})
        self.assertEqual([900205], self.results(smart_id))

        self.assertEqual(200, self.call(OWNER, "delete", f"/api/albums/{album}").status_code)
        self.assert_fails_closed(smart_id)
        self.assertEqual(["album_id"], self.call(OWNER, "get", f"/api/smart-albums/{smart_id}").get_json()["data"]["stale_references"])

        arreglo = self.call(OWNER, "patch", f"/api/smart-albums/{smart_id}", json={"filters": {"album_id": ALBUM_B}})
        self.assertEqual(200, arreglo.status_code, arreglo.get_json())
        self.assertEqual({900201, 900205}, set(self.results(smart_id)))

    def test_a_reference_that_changed_owner_also_fails_closed(self):
        """Si la fila cambia por fuera (un id reutilizado, una migración a medias),
        el id guardado no se vuelve a creer: se revalida contra el dueño."""
        smart_id = self.created_id(filters={"album_id": ALBUM_A})
        self.scratch.execute("UPDATE albums SET active = FALSE WHERE id = :a", {"a": ALBUM_A})
        try:
            self.assert_fails_closed(smart_id)
        finally:
            self.scratch.execute("UPDATE albums SET active = TRUE WHERE id = :a", {"a": ALBUM_A})

    def test_list_cards_resolve_names_in_bulk_and_carry_no_count(self):
        smart_id = self.created_id(filters={"album_id": ALBUM_A, "tag_id": 900301})
        tarjeta = next(s for s in self.call(OWNER, "get", "/api/smart-albums").get_json()["data"] if s["id"] == smart_id)
        self.assertEqual({"album": {"id": ALBUM_A, "titulo": "L10A A"}, "tag": {"id": 900301, "name": "playa-l10a"}},
                         tarjeta["references"])
        self.assertNotIn("count", tarjeta)


if __name__ == "__main__":
    unittest.main()
