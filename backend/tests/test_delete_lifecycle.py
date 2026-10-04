"""Borrar: recoger keys, registrar intención, borrar filas, borrar objetos."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RAIZ = Path(__file__).resolve().parents[1]


def fuente_de(archivo: str, nombre: str) -> str:
    codigo = (RAIZ / archivo).read_text(encoding="utf-8")
    arbol = ast.parse(codigo)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
            return ast.get_source_segment(codigo, nodo)
    raise AssertionError(f"no se encontró {nombre} en {archivo}")


def nodo_de(archivo: str, nombre: str) -> ast.FunctionDef:
    arbol = ast.parse((RAIZ / archivo).read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
            return nodo
    raise AssertionError(f"no se encontró {nombre} en {archivo}")


def llama_a(nodo, nombre: str) -> bool:
    return any(
        isinstance(h, ast.Call)
        and getattr(h.func, "id", getattr(h.func, "attr", None)) == nombre
        for h in ast.walk(nodo)
    )

class RemoveMediaFilesTests(unittest.TestCase):
    def test_devuelve_borrados_y_pendientes(self):
        fuente = fuente_de("app/api/media.py", "remove_media_files")
        self.assertIn("StorageError", fuente)
        self.assertIn("pendientes", fuente)

    def test_sigue_borrando_la_vista_previa_antes_que_el_original(self):
        fuente = fuente_de("app/api/media.py", "remove_media_files")
        self.assertLess(fuente.index("preview_storage_path"), fuente.rindex("storage_path"))


class PurgeRowsTests(unittest.TestCase):
    def test_sigue_leyendo_la_preview_antes_del_delete_en_cascada(self):
        fuente = fuente_de("app/api/media.py", "_purge_media_rows")
        self.assertLess(fuente.index("SELECT mm.media_id, mm.preview_storage_path"),
                        fuente.index("RETURNING m.id, m.storage_path"))


class DeleteEndpointsTests(unittest.TestCase):
    CASOS = [
        ("app/api/media.py", "delete_media_permanently"),
        ("app/api/media.py", "delete_all_trash"),
        # L10A: borrar un album ya no es una via que borre objetos (ver
        # AlbumDeleteKeepsAssetsTests); solo quita el album y sus pertenencias.
        # S12: dejo de ser una ruta HTTP y paso a ser un comando de consola.
        ("app/cli.py", "purge_expired_trash"),
        # La quinta via. Su cobertura vivia en test_avatar_storage.py con el
        # mismo patron por indices de texto que casi deja pasar la mutacion
        # de arriba, asi que entra aqui para compartir la comprobacion
        # estructural en vez de repetir la debil.
        ("app/api/auth.py", "delete_me"),
    ]

    USUARIO = [c for c in CASOS if c[1] != "purge_expired_trash"]

    def test_todos_registran_la_intencion_antes_de_borrar_objetos(self):
        for archivo, nombre in self.CASOS:
            fuente = fuente_de(archivo, nombre)
            with self.subTest(nombre=nombre):
                self.assertIn("record_pending", fuente)

    def test_la_intencion_se_registra_DENTRO_de_la_transaccion_no_despues(self):
        """`record_pending` tiene que correr DENTRO del `with db_conn()`.

        Comparar indices de texto no basta y por poco se cuela: moviendo la
        llamada apenas fuera del bloque -- despues del commit implicito, pero
        todavia antes de borrar los objetos -- el orden textual sigue siendo el
        mismo y una prueba por indices pasa igual. La unica forma de decir
        "antes del commit" es estructural: la llamada debe colgar del `with`.

        Si corriera despues del commit, un proceso muerto en esa ventana
        dejaria los objetos en almacenamiento sin ninguna fila que los
        referencie Y sin registro que avise al reconciliador: huerfanos
        invisibles para siempre."""
        for archivo, nombre in self.CASOS:
            with self.subTest(nombre=nombre):
                funcion = nodo_de(archivo, nombre)
                bloques = [
                    n for n in ast.walk(funcion)
                    if isinstance(n, ast.With) and llama_a(n, "db_conn")
                ]
                self.assertTrue(bloques, f"{nombre} no abre ninguna transaccion")
                self.assertTrue(
                    any(llama_a(bloque, "record_pending") for bloque in bloques),
                    f"{nombre} registra la intencion FUERA de la transaccion",
                )

    def test_no_se_confirma_la_operacion_si_quedo_algun_archivo_sin_borrar(self):
        """`mark_committed` borra el registro, asi que confirmarlo con
        archivos pendientes tira la unica pista de que esos objetos siguen en
        disco sin fila: el reconciliador ya no los encontraria."""
        for archivo, nombre in self.CASOS:
            fuente = fuente_de(archivo, nombre)
            with self.subTest(nombre=nombre):
                self.assertRegex(fuente, r"not pendientes[\s\S]{0,80}mark_committed")

    def test_los_endpoints_de_usuario_informan_si_queda_limpieza_pendiente(self):
        for archivo, nombre in self.USUARIO:
            fuente = fuente_de(archivo, nombre)
            with self.subTest(nombre=nombre):
                self.assertIn("storage_cleanup_pending", fuente)
                self.assertIn("202", fuente)

    def test_el_job_interno_reporta_pendientes_por_consola_sin_reinsertar_filas(self):
        fuente = fuente_de("app/cli.py", "purge_expired_trash")
        self.assertIn("pendientes", fuente)
        self.assertNotIn("INSERT", fuente)

    def test_el_borrado_de_album_sigue_exigiendo_owner(self):
        self.assertIn('"owner"', fuente_de("app/api/albums.py", "delete_album"))


class AlbumDeleteKeepsAssetsTests(unittest.TestCase):
    """L10A: un asset puede estar en varios albumes, asi que borrar un album
    borra el album y sus pertenencias, nunca assets ni archivos. Si alguien
    vuelve a borrar objetos aqui, destruiria fotos que siguen en otros albumes.
    La prueba con PostgreSQL real vive en test_asset_membership_db.py."""

    def test_borrar_un_album_nunca_toca_assets_ni_objetos(self):
        funcion = nodo_de("app/api/albums.py", "delete_album")
        fuente = fuente_de("app/api/albums.py", "delete_album")
        for llamada in ("record_pending", "remove_media_files", "remove_stored_file",
                        "_purge_media_rows", "mark_committed"):
            with self.subTest(llamada=llamada):
                self.assertFalse(llama_a(funcion, llamada), f"delete_album llama a {llamada}")
        self.assertNotRegex(fuente, r"(?i)DELETE\s+FROM\s+(assets|media)\b")
        self.assertIn("DELETE FROM album_assets WHERE album_id = :album_id", fuente)

    def test_anadir_o_quitar_de_un_album_es_solo_relacional(self):
        """L10B: ni copia, ni mueve, ni borra objetos; solo filas de album_assets."""
        for nombre, helper in (("add_album_asset", "add_membership"), ("remove_album_asset", "remove_membership")):
            funcion = nodo_de("app/api/albums.py", nombre)
            with self.subTest(ruta=nombre):
                self.assertTrue(llama_a(funcion, helper))
                for llamada in ("record_pending", "remove_media_files", "remove_stored_file", "save_upload",
                                "_purge_media_rows", "mark_committed", "get_storage_backend"):
                    self.assertFalse(llama_a(funcion, llamada), f"{nombre} llama a {llamada}")
                self.assertNotRegex(fuente_de("app/api/albums.py", nombre), r"(?i)(DELETE\s+FROM|UPDATE)\s+(assets|media)\b")


if __name__ == "__main__":
    unittest.main()
