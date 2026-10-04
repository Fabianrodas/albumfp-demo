"""L10A: el backend ya no puede hablar con la tabla `media` ni con `media.album_id`.

`0024` convierte `media` en `assets` y mueve la pertenencia a `album_assets`.
Una sola sentencia olvidada que siga diciendo `FROM media` o `m.album_id` no
falla al importar: revienta con un 500 la primera vez que alguien abre esa
pantalla. Estas pruebas leen cada literal de texto de `app/` y `scripts/` con
el AST y fallan nombrando el fichero y la linea.
"""
import ast
import re
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
SOURCES = [*sorted((BACKEND / "app").rglob("*.py")), *sorted((BACKEND / "scripts").rglob("*.py"))]
MIGRATION = BACKEND / "migrations" / "versions" / "0024_asset_album_membership.py"

LEGACY_TABLE = re.compile(r"\b(FROM|JOIN|INTO|UPDATE|TABLE|ON)\s+media\b(?!_)", re.IGNORECASE)
LEGACY_COLUMN = re.compile(r"\b(m|media|cm)\.album_id\b")


def _string_literals(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


class NoLegacyMediaSqlTests(unittest.TestCase):
    def test_no_sql_targets_the_renamed_media_table(self):
        hallazgos = [
            f"{path.relative_to(BACKEND)}:{lineno}"
            for path in SOURCES
            for lineno, texto in _string_literals(path)
            if LEGACY_TABLE.search(texto)
        ]
        self.assertEqual([], hallazgos, "SQL que todavia apunta a la tabla media")

    def test_no_sql_reads_the_single_album_column(self):
        hallazgos = [
            f"{path.relative_to(BACKEND)}:{lineno}"
            for path in SOURCES
            for lineno, texto in _string_literals(path)
            if LEGACY_COLUMN.search(texto)
        ]
        self.assertEqual([], hallazgos, "SQL que todavia lee media.album_id")


class MembershipMigrationShapeTests(unittest.TestCase):
    def test_history_stays_a_single_linear_head(self):
        script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
        self.assertEqual(1, len(script.get_heads()), script.get_heads())

    def test_0024_follows_0023(self):
        script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
        revision = script.get_revision("0024_asset_album_membership")
        self.assertEqual("0023_media_archive", revision.down_revision)

    def test_migration_cannot_touch_physical_media(self):
        """La migracion es identidad y pertenencia en la base: ni un modulo de
        storage, ni de ficheros, ni de sistema operativo."""
        importados = set()
        for node in ast.walk(ast.parse(MIGRATION.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                importados.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                importados.add((node.module or "").split(".")[0])
        self.assertEqual(set(), importados - {"alembic", "sqlalchemy"})


if __name__ == "__main__":
    unittest.main()
