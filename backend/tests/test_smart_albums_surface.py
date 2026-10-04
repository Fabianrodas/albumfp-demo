"""L11: fronteras estructurales de los álbumes inteligentes (sin base de datos).

Un álbum inteligente es una búsqueda guardada, no un contenedor: no tiene fila
en `albums`, no tiene pertenencias en `album_assets` y no toca bytes. Estas
pruebas leen el código para que esas fronteras no se borren sin que nadie lo
note; el comportamiento contra PostgreSQL real está en
`test_smart_albums_db.py`.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "app/api"
MODULE = API / "smart_albums.py"


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{path.name} no define {name}")


class SmartAlbumIsNotAPhysicalAlbumTests(unittest.TestCase):
    def test_regular_albums_never_learn_about_smart_albums(self):
        """Ni subir, ni pertenencias, ni compartir, ni portada pueden apuntar a
        uno: los módulos que hacen eso ni siquiera nombran la tabla."""
        for nombre in ("albums.py", "media.py", "media_context.py", "shares.py", "tags.py",
                       "home.py", "places.py", "users.py"):
            with self.subTest(nombre):
                self.assertNotIn("smart_album", (API / nombre).read_text(encoding="utf-8"))

    def test_there_is_no_is_smart_shortcut_on_regular_albums(self):
        for path in [*(ROOT / "app").rglob("*.py"), ROOT / "schemas/schema.sql"]:
            with self.subTest(path.name):
                self.assertNotIn("is_smart", path.read_text(encoding="utf-8"))

    def test_the_smart_album_module_never_writes_memberships_assets_or_bytes(self):
        source = MODULE.read_text(encoding="utf-8")
        for forbidden in ("album_assets", "INSERT INTO assets", "UPDATE assets", "DELETE FROM assets",
                          "INSERT INTO albums", "UPDATE albums", "DELETE FROM albums",
                          "storage", "remove_stored_file", "remove_media_files", "save_upload",
                          "record_pending", "mark_committed"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, source)

    def test_deleting_a_smart_album_deletes_only_its_definition(self):
        source = _function_source(MODULE, "delete_smart_album")
        self.assertIn("DELETE FROM smart_albums", source)
        self.assertIn(":user_id", source)
        self.assertEqual(1, source.count("DELETE FROM"), "solo borra la definición")


class OneSearchContractTests(unittest.TestCase):
    def test_execution_goes_through_the_shared_search_builder(self):
        source = _function_source(MODULE, "list_smart_album_media")
        self.assertIn("saved_filters_to_search", source)
        self.assertIn("run_media_search", source)

    def test_the_global_search_uses_the_same_executor(self):
        self.assertIn("run_media_search", _function_source(API / "media.py", "search_media"))

    def test_no_parallel_filter_sql_lives_in_the_smart_album_module(self):
        source = MODULE.read_text(encoding="utf-8")
        for column in ("is_favorite", "search_vector", "search_place_vector", "taken_at",
                       "archived_at", "file_type", "media_tags"):
            with self.subTest(column):
                self.assertNotIn(column, source)

    def test_runtime_rechecks_both_references_against_the_owner(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("owner_id = :user_id", source, "la etiqueta se revalida contra su dueño")
        self.assertIn("a.user_id = :user_id", source, "el álbum se revalida contra su dueño")
        self.assertIn("a.active = TRUE", source)


if __name__ == "__main__":
    unittest.main()
