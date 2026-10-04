"""S00 — el vocabulario de etiquetas pertenece a un dueno.

Estas pruebas no abren Postgres (el patron de este repo): fijan la **frontera
en el codigo**, que es lo que un refactor puede tirar sin que nadie lo note.
El comportamiento contra una base real se verifica en QA en vivo.

La regla que vigilan: ninguna sentencia SQL puede tocar `tags` sin decir de
quien son. Cuando `name` era UNIQUE global, `GET /api/tags` devolvia el
catalogo entero de la instalacion a cualquier cuenta.
"""
import ast
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'

TOCA_TAGS = re.compile(r'\b(from|into|update|join)\s+tags\b', re.IGNORECASE)


def sql_literals(path: Path):
    """Cada string literal completo del fichero que toque `tags`.

    Los trozos de una f-string se descartan: ahi el WHERE se monta aparte y no
    hay nada que leer estaticamente. `list_tags`, que es la unica consulta asi,
    tiene su propia prueba mas abajo.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    partes_de_fstring = {
        id(hijo)
        for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
        for hijo in ast.walk(node)
    }
    for node in ast.walk(tree):
        if id(node) in partes_de_fstring:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and TOCA_TAGS.search(node.value):
            yield node.value


class TagOwnershipBoundaryTests(unittest.TestCase):
    def test_every_tags_statement_names_an_owner(self):
        sin_dueno = []
        for path in sorted(APP.rglob('*.py')):
            for sql in sql_literals(path):
                if 'owner_id' in sql:
                    continue
                # Unica exencion: las etiquetas de UNA foto concreta. Ahi el
                # dueno ya lo fijo la autorizacion del album antes de llegar
                # aqui, y por construccion todas son suyas. Cualquier otra
                # consulta debe nombrarlo, tambien las nuevas.
                if 'mt.media_id = :media_id' in sql:
                    continue
                sin_dueno.append(f'{path.relative_to(ROOT)}: {" ".join(sql.split())[:90]}')
        self.assertEqual([], sin_dueno, f'SQL sobre tags sin frontera de dueno: {sin_dueno}')

    def test_tag_listing_always_starts_from_the_owner(self):
        source = (APP / 'api' / 'tags.py').read_text(encoding='utf-8')
        # El WHERE de `list_tags` se monta en un f-string, asi que lo que se
        # fija es su semilla: nunca se lista sin acotar por dueno.
        self.assertIn('where = ["owner_id = :owner_id"]', source)
        # Y el dueno del listado sale del album cuando se pide uno, no del
        # usuario que pregunta.
        self.assertIn('params["owner_id"] = owner_id', source)

    def test_creating_a_tag_collides_per_owner_not_globally(self):
        source = (APP / 'api' / 'tags.py').read_text(encoding='utf-8')
        self.assertIn('ON CONFLICT (owner_id, name)', source)
        self.assertNotIn('ON CONFLICT (name)', source)

    def test_assigning_tags_checks_them_against_the_album_owner(self):
        source = (APP / 'api' / 'tags.py').read_text(encoding='utf-8')
        # No basta con que el tag exista: tiene que ser del dueno del album.
        # L10A: asignar actua sobre un asset; su dueño es el de sus albumes.
        self.assertIn('tags_belong_to(conn, access["owner_id"], tag_ids)', source)
        media = (APP / 'api' / 'media.py').read_text(encoding='utf-8')
        self.assertIn('tags_belong_to(conn, access["album"]["user_id"], clean["tag_ids"])', media)
        # La version vieja solo miraba `id = ANY(:tag_ids)`.
        self.assertNotIn('_tags_exist', media)

    def test_creating_inside_someone_elses_album_requires_organize(self):
        source = (APP / 'api' / 'tags.py').read_text(encoding='utf-8')
        self.assertIn('require_album_capability(conn, album_id, user_id, "organize")', source)
        # Listar, en cambio, solo pide lectura: el filtro por tag lo usa
        # tambien quien solo mira el album.
        self.assertIn('require_album_permission(conn, album_id, user_id, "read")', source)


class TagSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = (ROOT / 'schemas' / 'schema.sql').read_text(encoding='utf-8')
        bloque = re.search(r'CREATE TABLE tags \((.*?)\n\);', self.schema, re.DOTALL)
        self.assertIsNotNone(bloque, 'no encontre CREATE TABLE tags')
        self.tabla = bloque.group(1)

    def test_tag_names_are_unique_per_owner_not_globally(self):
        self.assertIn('UNIQUE (owner_id, name)', self.tabla)
        self.assertNotIn('name VARCHAR(50) UNIQUE', self.tabla)

    def test_owner_is_mandatory_and_cascades_with_the_account(self):
        self.assertIn('owner_id INTEGER NOT NULL', self.tabla)
        self.assertIn('REFERENCES users(id) ON DELETE CASCADE', self.tabla)


class TagMigrationTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / 'migrations' / 'versions' / '0012_private_tags.py').read_text(encoding='utf-8')

    def test_it_hangs_off_the_previous_revision(self):
        self.assertIn('revision = "0012_private_tags"', self.source)
        self.assertIn('down_revision = "0011_media_previews"', self.source)

    def test_the_backfill_derives_the_owner_from_the_album(self):
        # El dueno sale de donde de verdad vive el dato, no de `created_by`.
        self.assertIn('JOIN albums a ON a.id = m.album_id', self.source)
        self.assertIn('UPDATE media_tags mt', self.source)

    def test_a_lossy_downgrade_raises_instead_of_merging_two_libraries(self):
        self.assertIn('HAVING COUNT(*) > 1', self.source)
        self.assertIn('raise RuntimeError', self.source)


if __name__ == '__main__':
    unittest.main()
