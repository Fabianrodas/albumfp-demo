"""El ZIP se construye materializando una fuente a la vez (spec §11)."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FUENTE = Path(__file__).resolve().parents[1] / "app" / "api" / "media.py"
CODIGO = FUENTE.read_text(encoding="utf-8")


def fuente_de(nombre: str) -> str:
    arbol = ast.parse(CODIGO)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
            return ast.get_source_segment(CODIGO, nodo)
    raise AssertionError(f"no se encontró {nombre}")


class AlbumZipTests(unittest.TestCase):
    def setUp(self):
        self.fuente = fuente_de("download_album")

    def test_ya_no_resuelve_rutas_del_almacenamiento(self):
        self.assertNotIn("resolve_storage_path", self.fuente)

    def test_materializa_dentro_del_bucle_no_todas_las_fuentes_a_la_vez(self):
        indice_bucle = self.fuente.index("for row in rows")
        indice_materialize = self.fuente.index("materialize")
        self.assertGreater(indice_materialize, indice_bucle,
                           "una materialización viva a la vez, no todas juntas")

    def test_el_zip_se_construye_en_el_workspace_gestionado(self):
        self.assertIn("local_workspace", self.fuente)
        self.assertIn('"zip"', self.fuente)
        self.assertNotIn("NamedTemporaryFile", self.fuente)

    def test_el_lifetime_del_zip_termina_al_cerrar_la_respuesta_no_con_after_this_request(self):
        self.assertNotIn("after_this_request", self.fuente)

    def test_conserva_la_deduplicacion_de_nombres_y_zip64(self):
        self.assertIn("used_names", self.fuente)
        self.assertIn("safe_filename", self.fuente)

    def test_un_objeto_ausente_se_omite_pero_un_corte_aborta_el_zip(self):
        self.assertIn("ObjectNotFound", self.fuente)
        self.assertIn("StorageError", self.fuente)

    def test_si_no_queda_ninguna_entrada_responde_404_en_vez_de_un_zip_vacio(self):
        self.assertIn("status=404", self.fuente)

    def test_conserva_la_autorizacion_previa(self):
        self.assertIn("No autorizado para descargar este álbum", self.fuente)


if __name__ == "__main__":
    unittest.main()
