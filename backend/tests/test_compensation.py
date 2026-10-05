"""Registros durables de operaciones de storage (spec S7).

El registro de compensacion es lo que permite reconciliar despues de un
crash entre "el objeto ya se escribio en storage" y "la fila de la base se
confirmo". Ademas del camino feliz (crear/confirmar/revertir), este modulo
fija cinco propiedades que el borrador original omitia y que son el motivo
de que este archivo exista:

* un tope de bytes y de cantidad de registros (spec: 100.000 o 256 MiB);
* bloqueo interproceso real, no solo en memoria (varios procesos de trabajo
  y timers son procesos distintos, igual que en `test_workspace.py`);
* version de esquema y version de objeto conocida por key;
* fsync del directorio contenedor tras cada rename, no solo del archivo;
* un registro que no se puede interpretar falla CERRADO: nunca se descarta
  en silencio ni se trata como si no tuviera nada pendiente.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.storage.workspace as workspace_mod
from app.storage.compensation import (
    CompensationLogFull,
    CorruptOperationRecord,
    OPERATION_KINDS,
    discard_operation,
    mark_committed,
    mark_rolled_back,
    pending_operations,
    read_operation,
    record_pending,
)
from app.storage.workspace import WorkspaceBusy

_RAIZ_BACKEND = str(Path(__file__).resolve().parents[1])

KEY = "user_1/album_2/" + "a" * 32 + ".jpg"
PREVIEW = "user_1/album_2/" + "b" * 32 + ".webp"


class _EntornoAislado(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch = patch.dict(os.environ, {
            "MEDIA_STATE_ROOT": str(Path(self._tmp.name) / "state"),
            "MEDIA_WORK_ROOT": str(Path(self._tmp.name) / "work"),
        }, clear=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _estado(self) -> Path:
        return Path(os.environ["MEDIA_STATE_ROOT"]) / "storage-operations"


class CompensationTests(_EntornoAislado):
    def test_registrar_devuelve_un_identificador_y_deja_el_registro_pendiente(self):
        operacion = record_pending("upload", [KEY, PREVIEW])
        self.assertRegex(operacion, r"^[0-9a-f]{32}$")
        registro = read_operation(operacion)
        self.assertEqual("pending", registro["state"])
        self.assertEqual([KEY, PREVIEW], registro["keys"])
        self.assertEqual("upload", registro["kind"])

    def test_el_registro_no_guarda_usuario_token_ni_nombre_original(self):
        registro = read_operation(record_pending("upload", [KEY]))
        prohibidos = {"user_id", "username", "token", "original_filename", "url"}
        self.assertEqual(set(), prohibidos & set(registro))

    def test_confirmar_saca_la_operacion_de_pendientes(self):
        operacion = record_pending("upload", [KEY])
        mark_committed(operacion)
        # Confirmar borra el registro: ya no hay nada que compensar.
        self.assertIsNone(read_operation(operacion))
        self.assertEqual([], [r["operation_id"] for r in pending_operations()])

    def test_confirmar_no_deja_el_registro_acumulandose_para_siempre(self):
        """Una operacion confirmada ya no tiene NADA que compensar: su objeto
        esta escrito y su fila commiteada. Nadie vuelve a leerla nunca, y el
        reconciliador solo mira `pending`/`rolled_back`, asi que si se
        conservara se acumularia una por subida hasta agotar el tope del log y
        bloquear TODAS las subidas y borrados siguientes."""
        for _ in range(6):
            mark_committed(record_pending("upload", [KEY]))
        self.assertEqual([], list(self._estado().glob("*.json")))
        # Y el log sigue aceptando trabajo nuevo despues de esas seis.
        self.assertIsNotNone(record_pending("upload", [KEY]))

    def test_marcar_rollback_la_deja_lista_para_el_reconciliador(self):
        operacion = record_pending("upload", [KEY])
        mark_rolled_back(operacion)
        registro = read_operation(operacion)
        self.assertEqual("rolled_back", registro["state"])
        self.assertIn(operacion, [r["operation_id"] for r in pending_operations()])

    def test_pendientes_incluye_pending_y_rolled_back_pero_no_committed(self):
        a, b, c = record_pending("upload", [KEY]), record_pending("delete", [PREVIEW]), record_pending("upload", [KEY])
        mark_rolled_back(b)
        mark_committed(c)
        ids = {r["operation_id"] for r in pending_operations()}
        self.assertIn(a, ids)
        self.assertIn(b, ids)
        self.assertNotIn(c, ids)

    def test_un_tipo_desconocido_se_rechaza(self):
        with self.assertRaises(ValueError):
            record_pending("lo-que-sea", [KEY])

    def test_una_key_invalida_se_rechaza_al_registrar(self):
        with self.assertRaises(ValueError):
            record_pending("upload", ["../etc/passwd"])

    def test_una_lista_de_keys_vacia_se_rechaza(self):
        with self.assertRaises(ValueError):
            record_pending("upload", [])

    def test_leer_una_operacion_inexistente_devuelve_none(self):
        self.assertIsNone(read_operation("0" * 32))

    def test_descartar_borra_el_registro(self):
        operacion = record_pending("delete", [KEY])
        discard_operation(operacion)
        self.assertIsNone(read_operation(operacion))

    def test_descartar_una_operacion_inexistente_no_falla(self):
        discard_operation("f" * 32)  # idempotente, sin lanzar

    def test_marcar_una_operacion_ya_descartada_no_falla(self):
        operacion = record_pending("upload", [KEY])
        discard_operation(operacion)
        mark_committed(operacion)  # nada que marcar, no debe lanzar
        self.assertIsNone(read_operation(operacion))

    def test_el_archivo_es_json_valido_y_no_queda_ningun_temporal(self):
        operacion = record_pending("upload", [KEY])
        estado = self._estado()
        archivos = sorted(p.name for p in estado.iterdir())
        self.assertEqual([f"{operacion}.json"], archivos)
        json.loads((estado / f"{operacion}.json").read_text(encoding="utf-8"))

    @unittest.skipIf(os.name != "posix", "los bits de permiso de Windows no son comparables")
    def test_el_registro_es_privado(self):
        operacion = record_pending("upload", [KEY])
        archivo = self._estado() / f"{operacion}.json"
        self.assertEqual(0o600, archivo.stat().st_mode & 0o777)


class CompensationVersioningTests(_EntornoAislado):
    """Spec S7: 'keys, versiones conocidas, fecha UTC y estado'."""

    def test_el_registro_trae_una_version_conocida_por_key_y_version_de_esquema(self):
        registro = read_operation(record_pending("upload", [KEY, PREVIEW]))
        self.assertIn("schema_version", registro)
        self.assertEqual([None, None], registro["versions"])
        self.assertEqual(len(registro["keys"]), len(registro["versions"]))

    def test_un_schema_version_desconocido_falla_cerrado_no_se_ignora(self):
        operacion = record_pending("upload", [KEY])
        archivo = self._estado() / f"{operacion}.json"
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        datos["schema_version"] = 999
        archivo.write_text(json.dumps(datos), encoding="utf-8")
        with self.assertRaises(CorruptOperationRecord):
            read_operation(operacion)


class CompensationCapTests(_EntornoAislado):
    """Spec S7: 'Maximo 100.000 registros o 256 MiB'. Los pendientes no se
    descartan al llegar al tope, solo se rechazan mutaciones nuevas."""

    def test_alcanzar_el_tope_de_registros_rechaza_uno_nuevo_sin_perder_los_existentes(self):
        with patch("app.storage.compensation._MAX_RECORDS", 2):
            record_pending("upload", [KEY])
            record_pending("upload", [KEY])
            with self.assertRaises(CompensationLogFull):
                record_pending("upload", [KEY])
        self.assertEqual(2, len(pending_operations()))

    def test_alcanzar_el_tope_de_bytes_rechaza_uno_nuevo_sin_perder_los_existentes(self):
        with patch("app.storage.compensation._MAX_TOTAL_BYTES", 1):
            primera = record_pending("upload", [KEY])
            with self.assertRaises(CompensationLogFull):
                record_pending("upload", [KEY])
        self.assertIn(primera, [r["operation_id"] for r in pending_operations()])

    def test_confirmar_una_operacion_no_cuenta_como_mutacion_nueva_bajo_el_tope(self):
        with patch("app.storage.compensation._MAX_RECORDS", 1):
            operacion = record_pending("upload", [KEY])
            mark_committed(operacion)  # libera el hueco en vez de ocuparlo para siempre
            self.assertIsNone(read_operation(operacion))
            # Y con el tope a 1, el hueco liberado admite la siguiente.
            self.assertIsNotNone(record_pending("upload", [KEY]))


class CompensationDirectoryFsyncTests(_EntornoAislado):
    """No hay rama por `os.name`: el intento de abrir el directorio se hace
    igual en los dos sistemas y es el propio `OSError` el que distingue
    POSIX (fd real, fsync de verdad) de Windows (`PermissionError`,
    comprobado en este equipo, y se ignora con gracia). Por eso este test
    corre -y da evidencia real- en los dos sistemas operativos, sin `skip`.
    """

    def test_escribir_un_registro_intenta_sincronizar_el_directorio_contenedor(self):
        # `.resolve()`: en Windows el nombre corto 8.3 del temporal
        # (`ASUSVI~1`) y el largo (`test workstation Vivobook`) son la misma carpeta pero
        # distinta cadena; `state_root()` ya resuelve al construir la raiz,
        # asi que la comparacion debe resolver tambien para no comparar dos
        # representaciones validas del mismo path como si fueran distintas.
        estado = str(self._estado().resolve())
        with patch("os.open", wraps=os.open) as espia_open, \
                patch("os.fsync", wraps=os.fsync) as espia_fsync:
            operacion = record_pending("upload", [KEY])
        rutas_abiertas = [str(args[0]) for args, _ in espia_open.call_args_list]
        self.assertIn(estado, rutas_abiertas)
        if os.name == "posix":
            # Fd real: una fsync del temporal y una del directorio tras el
            # rename.
            self.assertEqual(2, espia_fsync.call_count)
        else:
            # `os.open` sobre un directorio falla en Windows (PermissionError):
            # el intento se hizo, pero solo queda la fsync del temporal.
            self.assertEqual(1, espia_fsync.call_count)
        # En cualquiera de los dos sistemas la operacion sigue siendo valida.
        self.assertEqual("pending", read_operation(operacion)["state"])


class CompensationCorruptRecordTests(_EntornoAislado):
    """'Fallar cerrado' es la propiedad completa: ni se descarta en silencio
    ni se trata como si el registro no tuviera nada pendiente."""

    def _corromper(self, operacion: str, contenido: str) -> Path:
        archivo = self._estado() / f"{operacion}.json"
        archivo.write_text(contenido, encoding="utf-8")
        return archivo

    def test_json_ilegible_lanza_en_vez_de_devolver_none_o_vacio(self):
        operacion = record_pending("upload", [KEY])
        self._corromper(operacion, "{no es json")
        with self.assertRaises(CorruptOperationRecord):
            read_operation(operacion)

    def test_un_campo_obligatorio_ausente_lanza(self):
        operacion = record_pending("upload", [KEY])
        archivo = self._estado() / f"{operacion}.json"
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        del datos["keys"]
        archivo.write_text(json.dumps(datos), encoding="utf-8")
        with self.assertRaises(CorruptOperationRecord):
            read_operation(operacion)

    def test_operation_id_que_no_coincide_con_el_nombre_de_archivo_lanza(self):
        operacion = record_pending("upload", [KEY])
        archivo = self._estado() / f"{operacion}.json"
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        datos["operation_id"] = "0" * 32
        archivo.write_text(json.dumps(datos), encoding="utf-8")
        with self.assertRaises(CorruptOperationRecord):
            read_operation(operacion)

    def test_pending_operations_falla_cerrado_no_omite_el_registro_corrupto(self):
        bueno = record_pending("upload", [KEY])
        malo = record_pending("delete", [PREVIEW])
        self._corromper(malo, "{no es json")
        with self.assertRaises(CorruptOperationRecord):
            pending_operations()
        # El registro bueno sigue intacto (no se toco ni se perdio).
        self.assertEqual("pending", read_operation(bueno)["state"])

    def test_un_archivo_borrado_mientras_se_lista_no_es_corrupcion(self):
        # Race benigna: otro proceso confirma/descarta justo mientras se
        # recorre el directorio. Eso no es lo mismo que un registro
        # ilegible y no debe fallar cerrado.
        vivo = record_pending("upload", [KEY])
        efimero = record_pending("upload", [PREVIEW])
        archivo_efimero = self._estado() / f"{efimero}.json"
        original_read_text = Path.read_text

        def _leer_y_borrar(self_path, *args, **kwargs):
            resultado = original_read_text(self_path, *args, **kwargs)
            if self_path == archivo_efimero:
                archivo_efimero.unlink(missing_ok=True)
            return resultado

        with patch.object(Path, "read_text", _leer_y_borrar):
            pendientes = pending_operations()
        ids = {r["operation_id"] for r in pendientes}
        self.assertIn(vivo, ids)

    def test_marcar_committed_sobre_un_registro_corrupto_falla_cerrado(self):
        operacion = record_pending("upload", [KEY])
        self._corromper(operacion, "{no es json")
        with self.assertRaises(CorruptOperationRecord):
            mark_committed(operacion)


# Entra al lock de compensacion y lo mantiene tomado hasta que lo maten: es
# el "otro worker" que demuestra que el bloqueo es interproceso, no un lock
# en memoria que solo serializaria hilos del mismo proceso.
_HIJO_LOCK = """
import sys, time
sys.path.insert(0, sys.argv[1])
from app.storage import compensation
with compensation._held(compensation._lock_path()):
    sys.stdout.write("locked\\n")
    sys.stdout.flush()
    time.sleep(300)
"""


class CompensationInterprocessLockTests(_EntornoAislado):
    def _arrancar_hijo(self) -> subprocess.Popen:
        proceso = subprocess.Popen(
            [sys.executable, "-c", _HIJO_LOCK, _RAIZ_BACKEND],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=dict(os.environ),
        )
        self.addCleanup(self._matar, proceso)
        linea = proceso.stdout.readline().strip()
        if linea != "locked":
            self.fail(f"el hijo no tomo el lock: {linea!r} / {proceso.stdout.read()}")
        return proceso

    def _matar(self, proceso: subprocess.Popen) -> None:
        if proceso.poll() is None:
            proceso.kill()
        proceso.wait(30)
        proceso.stdout.close()

    def test_otro_proceso_con_el_lock_bloquea_el_registro_y_morir_lo_libera(self):
        proceso = self._arrancar_hijo()

        with patch.object(workspace_mod, "_SLOT_WAIT_SECONDS", 0.2):
            with self.assertRaises(WorkspaceBusy):
                record_pending("upload", [KEY])
        # Nada se escribio mientras el lock estaba disputado.
        self.assertEqual([], pending_operations())

        # Muere sin correr ningun `finally`: SIGKILL/TerminateProcess directo.
        proceso.kill()
        proceso.wait(30)

        # El SO suelta el lock al morir el proceso: la siguiente operacion no
        # se queda colgada esperando a un proceso que ya no existe.
        operacion = record_pending("upload", [KEY])
        self.assertEqual("pending", read_operation(operacion)["state"])


if __name__ == "__main__":
    unittest.main()
