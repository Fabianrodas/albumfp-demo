"""F04: comentarios de asset, sin base de datos.

El contrato de texto (validación del servidor) y la forma de las rutas. El
comportamiento contra PostgreSQL real está en `test_asset_comments_db.py`.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CommentTextTests(unittest.TestCase):
    def test_trims_and_keeps_useful_newlines(self):
        from app.comments import clean_comment_body

        self.assertEqual("Hola\nmundo", clean_comment_body("  Hola\nmundo \n "))
        self.assertEqual("línea 1\nlínea 2", clean_comment_body("línea 1\r\nlínea 2"))
        self.assertEqual("con\ttab", clean_comment_body("con\ttab"))

    def test_plain_text_is_stored_verbatim_not_sanitized(self):
        from app.comments import clean_comment_body

        for texto in ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>', 'a & b "c"', 'Ñandú 🦤 日本語'):
            with self.subTest(texto=texto):
                self.assertEqual(texto, clean_comment_body(texto))

    def test_length_is_counted_after_trimming_with_a_2000_ceiling(self):
        from app.comments import MAX_COMMENT_LENGTH, clean_comment_body

        self.assertEqual(2000, MAX_COMMENT_LENGTH)
        self.assertEqual(2000, len(clean_comment_body("  " + "x" * 2000 + "  ")))
        with self.assertRaises(ValueError):
            clean_comment_body("x" * 2001)

    def test_rejects_blank_non_text_control_characters_and_lone_surrogates(self):
        from app.comments import clean_comment_body

        for malo in ("", "   ", "\n\t \n", None, 5, ["hola"], "nul\x00", "bell\x07", "esc\x1b[31m", "\ud800"):
            with self.subTest(malo=repr(malo)), self.assertRaises(ValueError):
                clean_comment_body(malo)


def _function(path: str, name: str) -> ast.FunctionDef:
    for nodo in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == name:
            return nodo
    raise AssertionError(f"{name} no existe en {path}")


class CommentRouteShapeTests(unittest.TestCase):
    MODULE = "app/api/comments.py"

    def test_authenticated_routes_require_a_session(self):
        for nombre in ("list_media_comments", "create_media_comment", "update_comment", "delete_comment"):
            decoradores = {ast.unparse(d) for d in _function(self.MODULE, nombre).decorator_list}
            with self.subTest(ruta=nombre):
                self.assertIn("session_required", decoradores)

    def test_access_comes_from_the_central_asset_helper(self):
        fuente = (ROOT / self.MODULE).read_text(encoding="utf-8")
        self.assertIn("require_asset_permission(", fuente)
        self.assertNotIn("album_shares", fuente, "no independent album authorization SQL")
        for nombre in ("update_comment", "delete_comment"):
            with self.subTest(ruta=nombre):
                self.assertIn("user_id = :user_id", ast.unparse(_function(self.MODULE, nombre)))

    def test_public_share_list_is_read_only_and_uses_the_token_share_helper(self):
        publica = _function(self.MODULE, "read_shared_media_comments")
        self.assertNotIn("session_required", {ast.unparse(d) for d in publica.decorator_list})
        cuerpo = ast.unparse(publica)
        for pieza in ("get_token_share(", "is_unlocked(", "in_album_sql("):
            self.assertIn(pieza, cuerpo)
        rutas = [ast.unparse(d) for d in publica.decorator_list]
        self.assertTrue(any(".get(" in r for r in rutas), rutas)

    def test_creation_is_rate_limited_per_account_and_ip(self):
        cuerpo = ast.unparse(_function(self.MODULE, "create_media_comment"))
        self.assertEqual(2, cuerpo.count("try_consume("))

    def test_comments_emit_no_activity_or_notification(self):
        fuente = (ROOT / self.MODULE).read_text(encoding="utf-8") + (ROOT / "app/comments.py").read_text(encoding="utf-8")
        for prohibido in ("record_activity", "notify(", "mention", "reply", "like"):
            self.assertNotIn(prohibido, fuente)

    def test_author_shape_never_carries_avatar_paths(self):
        fuente = (ROOT / "app/comments.py").read_text(encoding="utf-8")
        self.assertNotIn("avatar_path AS", fuente)
        self.assertIn("has_avatar", fuente)


class CommentMigrationShapeTests(unittest.TestCase):
    PATH = ROOT / "migrations/versions/0029_asset_comments.py"

    def test_0029_follows_0028_and_schema_sql_agrees(self):
        migration = self.PATH.read_text(encoding="utf-8")
        self.assertIn('revision = "0029_asset_comments"', migration)
        self.assertIn('down_revision = "0028_webauthn_passkeys"', migration)
        schema = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")
        for texto in (migration, schema):
            for fragmento in (
                "CREATE TABLE asset_comments",
                "asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE",
                "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE",
                "chk_asset_comments_body",
                "idx_asset_comments_asset",
            ):
                self.assertIn(fragmento, texto)
        self.assertNotIn("album_id", migration.split("def downgrade")[0])

    def test_0029_downgrade_refuses_while_comments_exist(self):
        downgrade = self.PATH.read_text(encoding="utf-8").split("def downgrade():", 1)[1]
        self.assertIn("SELECT COUNT(*) FROM asset_comments", downgrade)
        self.assertLess(downgrade.index("raise RuntimeError"), downgrade.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
