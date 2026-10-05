"""GC fail-closed y comandos de storage del CLI (spec S12, S20).

`cleanup_orphaned_media` es SEGURIDAD CRITICA: borra objetos que storage
tiene pero la base no referencia. Si el inventario sobre el que razona esta
incompleto, borra archivos que siguen vivos. Por eso el contrato no es
"borra lo que se alcanzo a ver": es "un inventario a medias no es permiso
para borrar nada" -- ver `PartialInventoryIsNotPermissionToDeleteTests`.

`OrphanCleanupTests`/`ReconcileStorageOperationsTests` aislan
`MEDIA_STATE_ROOT` en un directorio temporal: `cleanup_orphaned_media` y
`reconcile_storage_operations` consultan `compensation.pending_operations()`
de verdad (no mockeada), y este equipo de desarrollo ya tiene decenas de
registros de compensacion reales bajo `backend/storage/state/` de sesiones
de QA anteriores -- sin aislar, estos tests dependerian del estado ambiental
de la maquina en vez de ser deterministas.
"""
import ast
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.cli as cli
from app.storage.contracts import ObjectPage, ObjectStat, StorageUnavailable

CODIGO = (Path(__file__).resolve().parents[1] / "app" / "cli.py").read_text(encoding="utf-8")
KEY = "user_1/album_2/" + "a" * 32 + ".jpg"
HUERFANA = "user_1/album_2/" + "b" * 32 + ".jpg"


def pagina(objetos, *, completa=True, cursor=None) -> ObjectPage:
    return ObjectPage("s1", 10_000_000.0, tuple(objetos), cursor, completa)


def objeto(key: str, mtime_ns: int) -> ObjectStat:
    return ObjectStat(key, 10, mtime_ns, "v1", "image/jpeg")


class CommandRegistryTests(unittest.TestCase):
    def test_los_comandos_nuevos_estan_registrados(self):
        for nombre in ("cleanup-workspace", "reconcile-storage-operations",
                       "storage-key-lookup", "check-storage"):
            with self.subTest(nombre=nombre):
                self.assertIn(nombre, cli.COMMANDS)

    def test_no_hay_endpoint_http_para_estos_comandos(self):
        api = (Path(__file__).resolve().parents[1] / "app" / "api").rglob("*.py")
        for archivo in api:
            texto = archivo.read_text(encoding="utf-8")
            for prohibido in ("cleanup-workspace", "reconcile-storage-operations",
                              "cleanup_orphaned_media"):
                with self.subTest(archivo=archivo.name, prohibido=prohibido):
                    self.assertNotIn(prohibido, texto)


class _EstadoAislado(unittest.TestCase):
    """MEDIA_STATE_ROOT temporal, mismo patron que test_compensation.py."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch = patch.dict(os.environ, {
            "MEDIA_STATE_ROOT": str(Path(self._tmp.name) / "state"),
            "ORPHANED_MEDIA_MAX_AGE_HOURS": "24",
        }, clear=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)


class OrphanCleanupTests(_EstadoAislado):
    def test_ya_no_recorre_el_filesystem_directamente(self):
        arbol = ast.parse(CODIGO)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "cleanup_orphaned_media":
                fuente = ast.get_source_segment(CODIGO, nodo)
                self.assertNotIn("rglob", fuente)
                self.assertNotIn("storage_root", fuente)
                self.assertIn("list_objects", fuente)
                return
        self.fail("no se encontro cleanup_orphaned_media")

    def test_un_listado_incompleto_no_borra_absolutamente_nada(self):
        """El caso central de esta suite: un inventario a medias NO es
        permiso para borrar nada, ni siquiera lo que si se alcanzo a ver."""
        borrados = []

        class BackendTruncado:
            def list_objects(self, *, cursor=None, limit=500):
                # `complete=False` y sin cursor: el inventario se corto.
                return pagina([objeto(HUERFANA, 1)], completa=False, cursor=None)

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=BackendTruncado()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            codigo = cli.cleanup_orphaned_media()
        self.assertNotEqual(0, codigo)
        self.assertEqual([], borrados)

    def test_si_el_snapshot_cambia_a_mitad_del_recorrido_no_borra_nada(self):
        """Dos paginas de snapshots distintos no son un inventario, son dos
        mitades de dos recorridos: una key puede faltar en la segunda solo
        porque la primera ya paso por su directorio. El spec exige el MISMO
        snapshot en todas las paginas justamente por esto."""
        borrados = []

        class BackendQueCambiaDeSnapshot:
            def __init__(self):
                self.llamadas = 0

            def list_objects(self, *, cursor=None, limit=500):
                self.llamadas += 1
                if self.llamadas == 1:
                    return ObjectPage("s1", 10_000_000.0, (objeto(HUERFANA, 1),), "c2", False)
                # Mismo cursor prometido, pero ya es OTRO recorrido.
                return ObjectPage("s2", 10_000_000.0, (), None, True)

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=BackendQueCambiaDeSnapshot()),              patch("app.cli._referenced_storage_paths", return_value=set()),              patch("app.cli.db_conn"):
            codigo = cli.cleanup_orphaned_media()
        self.assertNotEqual(0, codigo)
        self.assertEqual([], borrados)

    def test_un_corte_al_listar_no_borra_nada_y_sale_con_error(self):
        borrados = []

        class BackendCaido:
            def list_objects(self, *, cursor=None, limit=500):
                raise StorageUnavailable("almacenamiento local no disponible")

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=BackendCaido()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            self.assertNotEqual(0, cli.cleanup_orphaned_media())
        self.assertEqual([], borrados)

    def test_un_fallo_de_la_base_no_borra_nada(self):
        borrados = []

        class Backend:
            def list_objects(self, *, cursor=None, limit=500):
                return pagina([objeto(HUERFANA, 1)])

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", side_effect=RuntimeError("base caida")), \
             patch("app.cli.db_conn"):
            self.assertNotEqual(0, cli.cleanup_orphaned_media())
        self.assertEqual([], borrados)

    def test_una_key_referenciada_no_se_borra_aunque_sea_vieja(self):
        borrados = []

        class Backend:
            def list_objects(self, *, cursor=None, limit=500):
                return pagina([objeto(KEY, 1)])

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value={KEY}), \
             patch("app.cli.db_conn"):
            cli.cleanup_orphaned_media()
        self.assertEqual([], borrados)

    def test_una_huerfana_reciente_se_conserva(self):
        borrados = []
        reciente = int(time.time() * 1e9)

        class Backend:
            def list_objects(self, *, cursor=None, limit=500):
                return ObjectPage("s1", time.time(), (objeto(HUERFANA, reciente),), None, True)

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            cli.cleanup_orphaned_media()
        self.assertEqual([], borrados)

    def test_una_huerfana_vieja_y_sin_referencias_se_borra(self):
        """Camino feliz: sin esto, un guardia fail-closed demasiado ancho
        podria dejar de borrar huerfanos de verdad."""
        borrados = []
        vieja_ns = int((time.time() - 48 * 3600) * 1e9)

        class Backend:
            def list_objects(self, *, cursor=None, limit=500):
                return ObjectPage("s1", time.time(), (objeto(HUERFANA, vieja_ns),), None, True)

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            codigo = cli.cleanup_orphaned_media()
        self.assertEqual(0, codigo)
        self.assertEqual([HUERFANA], borrados)

    def test_una_key_con_operacion_de_subida_pendiente_no_se_borra(self):
        """Spec S12 paso 4: antes de cada lote se consultan tambien
        'registros/leases activos', no solo la base. Una key que ya se
        subio al origin pero cuyo INSERT todavia no comitea (o cuyo delete
        todavia no se reconcilio) esta protegida por su propio registro de
        compensacion, no solo por el margen de edad."""
        from app.storage.compensation import record_pending

        record_pending("upload", [HUERFANA])
        vieja_ns = int((time.time() - 48 * 3600) * 1e9)
        borrados = []

        class Backend:
            def list_objects(self, *, cursor=None, limit=500):
                return ObjectPage("s1", time.time(), (objeto(HUERFANA, vieja_ns),), None, True)

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            cli.cleanup_orphaned_media()
        self.assertEqual([], borrados)

    def test_un_registro_de_compensacion_illegible_no_borra_nada(self):
        """Mismo contrato fail-closed que compensation.pending_operations():
        un registro que no se puede interpretar hace fallar la funcion
        entera, nunca se ignora en silencio."""
        from app.storage.compensation import _operations_dir

        directorio = _operations_dir()
        (directorio / "corrupto.json").write_text("{no es json", encoding="utf-8")
        vieja_ns = int((time.time() - 48 * 3600) * 1e9)
        borrados = []

        class Backend:
            def list_objects(self, *, cursor=None, limit=500):
                return ObjectPage("s1", time.time(), (objeto(HUERFANA, vieja_ns),), None, True)

            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            codigo = cli.cleanup_orphaned_media()
        self.assertNotEqual(0, codigo)
        self.assertEqual([], borrados)

    def test_el_delete_es_condicional_por_version(self):
        arbol = ast.parse(CODIGO)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "cleanup_orphaned_media":
                self.assertIn("expected_version", ast.get_source_segment(CODIGO, nodo))
                return
        self.fail("no se encontro cleanup_orphaned_media")

    def test_las_tres_columnas_de_referencias_siguen_consultandose(self):
        arbol = ast.parse(CODIGO)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "_referenced_storage_paths":
                fuente = ast.get_source_segment(CODIGO, nodo)
                for columna in ("storage_path", "preview_storage_path", "avatar_path"):
                    self.assertIn(columna, fuente)
                return
        self.fail("no se encontro _referenced_storage_paths")


class KeyLookupTests(unittest.TestCase):
    def test_de_una_key_calcula_su_hash(self):
        from app.storage.object_keys import key_hash

        with patch("builtins.print") as impreso:
            cli.storage_key_lookup([KEY])
        salida = " ".join(str(a.args[0]) for a in impreso.call_args_list if a.args)
        self.assertIn(key_hash(KEY), salida)

    def test_de_un_hash_resuelve_la_key_consultando_las_referencias(self):
        from app.storage.object_keys import key_hash

        with patch("app.cli._referenced_storage_paths", return_value={KEY}), \
             patch("app.cli.db_conn"), patch("builtins.print") as impreso:
            cli.storage_key_lookup([key_hash(KEY)])
        salida = " ".join(str(a.args[0]) for a in impreso.call_args_list if a.args)
        self.assertIn(KEY, salida)

    def test_sin_argumentos_no_falla_y_devuelve_error(self):
        with patch("builtins.print"):
            self.assertNotEqual(0, cli.storage_key_lookup([]))


class WorkspaceCleanupTests(unittest.TestCase):
    def test_limpia_solo_residuos_locales_y_nunca_llama_al_origin(self):
        arbol = ast.parse(CODIGO)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "cleanup_workspace":
                fuente = ast.get_source_segment(CODIGO, nodo)
                self.assertIn("sweep_workspaces", fuente)
                self.assertNotIn("get_storage_backend", fuente)
                return
        self.fail("no se encontro cleanup_workspace")


class ReconcileStorageOperationsTests(_EstadoAislado):
    def _crear_registro(self, kind, keys, *, antiguedad_horas):
        from app.storage.compensation import _operations_dir, record_pending

        operacion = record_pending(kind, keys)
        archivo = _operations_dir() / f"{operacion}.json"
        registro = json.loads(archivo.read_text(encoding="utf-8"))
        creado = datetime.now(timezone.utc) - timedelta(hours=antiguedad_horas)
        registro["created_at"] = creado.isoformat()
        archivo.write_text(json.dumps(registro), encoding="utf-8")
        return operacion

    def test_una_operacion_pendiente_reciente_no_se_toca(self):
        from app.storage.compensation import read_operation

        operacion = self._crear_registro("upload", [HUERFANA], antiguedad_horas=1)
        borrados = []

        class Backend:
            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            resultado = cli.reconcile_storage_operations()
        self.assertEqual(0, resultado)
        self.assertEqual([], borrados)
        self.assertIsNotNone(read_operation(operacion))

    def test_una_operacion_vencida_y_sin_referencia_se_reconcilia(self):
        from app.storage.compensation import read_operation

        operacion = self._crear_registro("upload", [HUERFANA], antiguedad_horas=48)

        class Backend:
            def delete(self, key, *, expected_version=None):
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            resultado = cli.reconcile_storage_operations()
        self.assertEqual(0, resultado)
        self.assertIsNone(read_operation(operacion))

    def test_una_operacion_vencida_pero_ya_referenciada_no_borra_su_key(self):
        """La fila SI comiteo mientras tanto: la key ya no es huerfana, y la
        operacion se descarta sin llamar a delete."""
        from app.storage.compensation import read_operation

        operacion = self._crear_registro("upload", [HUERFANA], antiguedad_horas=48)
        borrados = []

        class Backend:
            def delete(self, key, *, expected_version=None):
                borrados.append(key)
                return True

        with patch("app.cli.get_storage_backend", return_value=Backend()), \
             patch("app.cli._referenced_storage_paths", return_value={HUERFANA}), \
             patch("app.cli.db_conn"):
            resultado = cli.reconcile_storage_operations()
        self.assertEqual(0, resultado)
        self.assertEqual([], borrados)
        self.assertIsNone(read_operation(operacion))

    def test_si_el_backend_falla_la_operacion_se_conserva_y_sale_con_error(self):
        from app.storage.compensation import read_operation
        from app.storage.contracts import StorageUnavailable

        operacion = self._crear_registro("upload", [HUERFANA], antiguedad_horas=48)

        class BackendCaido:
            def delete(self, key, *, expected_version=None):
                raise StorageUnavailable("almacenamiento local no disponible")

        with patch("app.cli.get_storage_backend", return_value=BackendCaido()), \
             patch("app.cli._referenced_storage_paths", return_value=set()), \
             patch("app.cli.db_conn"):
            resultado = cli.reconcile_storage_operations()
        self.assertNotEqual(0, resultado)
        self.assertIsNotNone(read_operation(operacion))


if __name__ == "__main__":
    unittest.main()
