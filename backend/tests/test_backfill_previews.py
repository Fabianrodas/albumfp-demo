"""The local preview backfill does not overwrite a winning preview."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FUENTE = Path(__file__).resolve().parents[1] / "scripts" / "backfill_previews.py"
CODIGO = FUENTE.read_text(encoding="utf-8")


class BackfillTests(unittest.TestCase):
    def test_no_toca_el_filesystem_directamente(self):
        for prohibido in ("storage_root", "resolve_storage_path", "rglob", "os.replace"):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, CODIGO)

    def test_el_update_es_condicional_sobre_preview_null(self):
        self.assertIn("preview_storage_path IS NULL", CODIGO)

    def test_comprueba_cuantas_filas_afecto(self):
        self.assertIn("rowcount", CODIGO)

    def test_compensa_su_propia_preview_si_pierde_la_carrera(self):
        self.assertIn("remove_stored_file", CODIGO)

    def test_un_fallo_de_almacenamiento_devuelve_codigo_distinto_de_cero(self):
        self.assertIn("StorageError", CODIGO)
        arbol = ast.parse(CODIGO)
        retornos = [n.value.value for n in ast.walk(arbol)
                    if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)]
        self.assertIn(1, retornos)

    def test_sigue_siendo_explicito_y_no_corre_al_arrancar_la_app(self):
        self.assertIn('if __name__ == "__main__"', CODIGO)
        raiz = Path(__file__).resolve().parents[1]
        for modulo in (raiz / "application.py", raiz / "app" / "api" / "__init__.py"):
            with self.subTest(modulo=modulo.name):
                self.assertNotIn("backfill_previews", modulo.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
