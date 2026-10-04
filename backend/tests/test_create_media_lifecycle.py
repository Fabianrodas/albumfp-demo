"""insert_media_record ya no hace I/O de objetos (spec S12), y create_media
compensa cuando el PUT tuvo exito pero la fila de DB no llego a confirmarse
(spec S7). Las dos primeras clases son AST estatico (forma del codigo); el
resto exige comportamiento real y por eso mezcla dos estilos, igual que ya
hace el repo:

* orquestacion (quien llama a quien) con todo mockeado -- rapido, aislado;
* el backend local de storage de verdad (mismo patron que
  test_upload_orchestration.py) para la unica prueba que de verdad importa:
  que ningun objeto sobreviva o desaparezca en el momento equivocado.

Sin Postgres real en ningun caso -- el patron establecido de este repo.
"""
import ast
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from PIL import Image

import app.storage.media_storage as media_storage
import app.storage.quarantine as quarantine
from app.api import media
from app.storage.backends import reset_storage_backend
from app.storage.compensation import (
    mark_committed,
    mark_rolled_back,
    pending_operations,
    read_operation,
    record_pending,
)

FUENTE = Path(__file__).resolve().parents[1] / "app" / "api" / "media.py"
_RAIZ_BACKEND = str(Path(__file__).resolve().parents[1])
KEY = "user_1/album_5/" + "a" * 32 + ".jpg"


def cuerpo_de(nombre: str) -> ast.FunctionDef:
    arbol = ast.parse(FUENTE.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
            return nodo
    raise AssertionError(f"no se encontró {nombre} en media.py")


def llamadas_en(nodo) -> set[str]:
    nombres = set()
    for hijo in ast.walk(nodo):
        if isinstance(hijo, ast.Call):
            objetivo = hijo.func
            if isinstance(objetivo, ast.Name):
                nombres.add(objetivo.id)
            elif isinstance(objetivo, ast.Attribute):
                nombres.add(objetivo.attr)
    return nombres


class InsertMediaRecordTests(unittest.TestCase):
    def test_ya_no_resuelve_rutas_ni_genera_derivados(self):
        llamadas = llamadas_en(cuerpo_de("insert_media_record"))
        for prohibida in ("resolve_storage_path", "extract_image_exif",
                          "create_preview_for_stored", "create_image_preview"):
            with self.subTest(prohibida=prohibida):
                self.assertNotIn(prohibida, llamadas)

    def test_lee_los_derivados_de_stored(self):
        fuente = ast.get_source_segment(FUENTE.read_text(encoding="utf-8"),
                                        cuerpo_de("insert_media_record"))
        self.assertIn('stored.get("exif"', fuente)
        self.assertIn('stored.get("preview"', fuente)

    def test_persiste_el_sha256_calculado_por_cuarentena(self):
        fuente = ast.get_source_segment(FUENTE.read_text(encoding="utf-8"),
                                        cuerpo_de("insert_media_record"))
        self.assertIn("sha256", fuente)
        self.assertIn('stored["sha256"]', fuente)


class CreateMediaTests(unittest.TestCase):
    def test_pide_los_derivados_al_subir(self):
        fuente = ast.get_source_segment(FUENTE.read_text(encoding="utf-8"),
                                        cuerpo_de("create_media"))
        self.assertIn("prepare_image_metadata=True", fuente)

    def test_confirma_o_deshace_el_registro_de_compensacion(self):
        fuente = ast.get_source_segment(FUENTE.read_text(encoding="utf-8"),
                                        cuerpo_de("create_media"))
        self.assertIn("mark_committed", fuente)
        self.assertIn("mark_rolled_back", fuente)

    def test_la_cuota_reserva_original_mas_derivado(self):
        # Hueco real que esta fase cierra: antes se estimaba solo el original
        # aunque la cuota contabilizara también las vistas previas.
        fuente = ast.get_source_segment(FUENTE.read_text(encoding="utf-8"),
                                        cuerpo_de("create_media"))
        self.assertIn("reserve_storage_quota", fuente)
        indice_subida = fuente.index("save_upload")
        indice_cuota = fuente.rindex("reserve_storage_quota")
        self.assertGreater(indice_cuota, indice_subida,
                           "la reserva definitiva ocurre con el tamaño real ya conocido")


# ---------------------------------------------------------------------------
# Comportamiento real: orquestacion con collaboradores mockeados
# ---------------------------------------------------------------------------

def foto(ancho=64, alto=48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (ancho, alto), (10, 120, 90)).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _peticion(album_id=5, contenido=None, **fields):
    app = Flask(__name__)
    return app.test_request_context(
        f"/albums/{album_id}/media", method="POST",
        data={"file": (io.BytesIO(contenido or foto()), "foto.jpg"), **fields},
        content_type="multipart/form-data",
    )


@contextmanager
def _db_ok():
    yield object()


class CreateMediaOrchestrationTests(unittest.TestCase):
    """Todo mockeado: solo importa el ORDEN de llamadas, no el storage real."""

    def setUp(self):
        self.acceso = {"album": {"user_id": 1, "titulo": "Viaje"},
                       "capabilities": {"upload", "organize"}}
        self.stored = {
            "storage_path": "user_1/album_5/aaa.jpg",
            "file_type": "image",
            "file_size": 1000,
            "format": "jpg",
            "mime_type": "image/jpeg",
            "original_filename": "foto.jpg",
            "sha256": "a" * 64,
            "storage_operation_id": "op-123",
            "exif": {},
            "preview": None,
        }
        for objetivo, valor in (
            ("current_user_id", lambda: 9),
            ("db_conn", _db_ok),
            ("require_album_capability", lambda *a, **k: self.acceso),
            ("tags_belong_to", lambda *a, **k: True),
            ("save_upload", lambda *a, **k: dict(self.stored)),
            ("record_activity", lambda *a, **k: None),
            ("notify_album_upload", lambda *a, **k: 0),
            ("find_exact_duplicate", lambda *a, **k: None),
            ("lock_exact_duplicate_scope", lambda *a, **k: None),
        ):
            p = patch.object(media, objetivo, valor)
            p.start()
            self.addCleanup(p.stop)
        self.mark_committed = patch("app.storage.compensation.mark_committed").start()
        self.addCleanup(patch.stopall)
        self.mark_rolled_back = patch("app.storage.compensation.mark_rolled_back").start()

    def test_permission_revocado_tras_el_put_revierte_y_limpia(self):
        # Primera lectura de permisos (temprana) pasa; la segunda (con los
        # receipts del PUT ya confirmados, spec S7 paso 7) ya no.
        secuencia = [self.acceso, None]
        with patch.object(media, "require_album_capability", side_effect=lambda *a, **k: secuencia.pop(0)), \
             patch.object(media, "insert_media_record") as insertar, \
             patch.object(media, "remove_media_files") as limpiar:
            with _peticion():
                respuesta, status = media.create_media.__wrapped__(5)
        self.assertEqual(403, status)
        insertar.assert_not_called()
        self.mark_rolled_back.assert_called_once_with("op-123")
        self.mark_committed.assert_not_called()
        limpiar.assert_called_once()

    def test_conflicto_temprano_devuelve_solo_un_match_visible_y_no_hace_put(self):
        duplicate = {"id": 71, "album_id": 5}

        def save(*args, before_store=None, **kwargs):
            self.assertIsNotNone(before_store)
            before_store("a" * 64)
            self.fail("un duplicado temprano no debe llegar al PUT")

        with patch.object(media, "save_upload", side_effect=save), \
             patch.object(media, "find_exact_duplicate", return_value=duplicate), \
             patch.object(media, "insert_media_record") as insert:
            with _peticion():
                response, status = media.create_media.__wrapped__(5)

        self.assertEqual(409, status)
        self.assertEqual("exact_duplicate", response.get_json()["code"])
        self.assertEqual(71, response.get_json()["existing_media_id"])
        self.assertEqual(5, response.get_json()["existing_album_id"])
        insert.assert_not_called()
        self.mark_committed.assert_not_called()
        self.mark_rolled_back.assert_not_called()

    def test_revocacion_antes_del_precheck_no_revela_el_duplicado(self):
        access = [self.acceso, None]

        def save(*args, before_store=None, **kwargs):
            before_store("a" * 64)
            self.fail("un acceso revocado no debe llegar al PUT")

        with patch.object(media, "require_album_capability", side_effect=lambda *a, **k: access.pop(0)), \
             patch.object(media, "save_upload", side_effect=save), \
             patch.object(media, "find_exact_duplicate") as find, \
             patch.object(media, "insert_media_record") as insert:
            with _peticion():
                response, status = media.create_media.__wrapped__(5)

        self.assertEqual(403, status)
        self.assertNotIn("existing_media_id", response.get_json())
        find.assert_not_called()
        insert.assert_not_called()
        self.mark_rolled_back.assert_not_called()

    def test_recheck_bajo_lock_cierra_la_carrera_y_compensa_el_put_perdedor(self):
        duplicate = {"id": 72, "album_id": 5}

        def save(*args, before_store=None, **kwargs):
            before_store("a" * 64)
            return dict(self.stored, sha256="a" * 64)

        with patch.object(media, "save_upload", side_effect=save), \
             patch.object(media, "find_exact_duplicate", side_effect=[None, duplicate]), \
             patch.object(media, "lock_exact_duplicate_scope") as lock, \
             patch.object(media, "insert_media_record") as insert, \
             patch.object(media, "remove_media_files") as cleanup:
            with _peticion():
                response, status = media.create_media.__wrapped__(5)

        self.assertEqual(409, status)
        self.assertEqual(72, response.get_json()["existing_media_id"])
        lock.assert_called_once()
        insert.assert_not_called()
        self.mark_rolled_back.assert_called_once_with("op-123")
        cleanup.assert_called_once()

    def test_force_duplicate_skips_rejection_but_still_uses_the_race_lock(self):
        stored = dict(self.stored, sha256="a" * 64)
        inserted = {"id": 73, "album_id": 5, "file_type": "image"}
        with patch.object(media, "save_upload", return_value=stored) as save, \
             patch.object(media, "find_exact_duplicate") as find, \
             patch.object(media, "lock_exact_duplicate_scope") as lock, \
             patch.object(media, "insert_media_record", return_value=inserted):
            with _peticion(force_duplicate="true"):
                response, status = media.create_media.__wrapped__(5)

        self.assertEqual(201, status)
        self.assertEqual(73, response.get_json()["data"]["id"])
        self.assertIsNone(save.call_args.kwargs["before_store"])
        find.assert_not_called()
        lock.assert_called_once()
        self.mark_committed.assert_called_once_with("op-123")


# ---------------------------------------------------------------------------
# Comportamiento real: backend local de verdad (objetos reales en disco)
# ---------------------------------------------------------------------------

class CreateMediaObjectInvariantTests(unittest.TestCase):
    """El invariante que esta tarea existe para proteger: ninguna fila de DB
    puede terminar apuntando a un objeto que no existe. Se prueba en las tres
    direcciones posibles con el backend LOCAL real (sin mockear save_upload):
    exito (objeto vive, fila existiria), fallo determinista tras el PUT
    (objeto se borra, no hay fila) y fallo ambiguo (objeto se conserva,
    porque no se puede descartar que la fila SI llegara a confirmarse)."""

    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._raiz_anterior = (media_storage._STORAGE_ROOT, quarantine._QUARANTINE_ROOT)
        media_storage._STORAGE_ROOT = Path(self._tmp.name) / "media"
        quarantine._QUARANTINE_ROOT = Path(self._tmp.name) / "quarantine"
        self.addCleanup(lambda: (
            setattr(media_storage, "_STORAGE_ROOT", self._raiz_anterior[0]),
            setattr(quarantine, "_QUARANTINE_ROOT", self._raiz_anterior[1]),
        ))
        entorno = patch.dict(os.environ, {
            "MEDIA_WORK_ROOT": str(Path(self._tmp.name) / "work"),
            "MEDIA_STATE_ROOT": str(Path(self._tmp.name) / "state"),
            "MEDIA_LOCAL_TEMP_MAX_GB": "0",
            "MEDIA_WORK_MAX_CONCURRENT": "4",
            "USER_STORAGE_QUOTA_GB": "0",
        }, clear=False)
        entorno.start()
        self.addCleanup(entorno.stop)

        acceso = {"album": {"user_id": 1, "titulo": "Viaje"},
                  "capabilities": {"upload", "organize"}}
        for objetivo, valor in (
            ("current_user_id", lambda: 9),
            ("require_album_capability", lambda *a, **k: acceso),
            ("tags_belong_to", lambda *a, **k: True),
            ("record_activity", lambda *a, **k: None),
            ("notify_album_upload", lambda *a, **k: 0),
            ("find_exact_duplicate", lambda *a, **k: None),
            ("lock_exact_duplicate_scope", lambda *a, **k: None),
        ):
            p = patch.object(media, objetivo, valor)
            p.start()
            self.addCleanup(p.stop)

    def _objetos_en_disco(self):
        raiz = media_storage.storage_root()
        return list(raiz.rglob("*.jpg")) + list(raiz.rglob("*.webp"))

    def test_exito_el_objeto_sobrevive_y_la_operacion_queda_committed(self):
        with patch.object(media, "db_conn", _db_ok), \
             patch.object(media, "insert_media_record", lambda *a, **k: {"id": 42}):
            with _peticion():
                respuesta, status = media.create_media.__wrapped__(5)
        self.assertEqual(201, status)
        self.assertGreaterEqual(len(self._objetos_en_disco()), 1)
        # Ni pending ni rolled_back deberia quedar colgado: se confirmo.
        self.assertEqual([], pending_operations())

    def test_fallo_deterministico_tras_el_put_borra_el_objeto_y_revierte(self):
        # insert_media_record lanza DENTRO de la segunda transaccion, antes
        # de que `db_conn()` intente su commit implicito: para cuando el
        # `except` de create_media lo recibe, el rollback YA ocurrio -- no
        # hay ambiguedad, es seguro borrar el objeto que el PUT dejo escrito.
        with patch.object(media, "db_conn", _db_ok), \
             patch.object(media, "insert_media_record",
                          side_effect=ValueError("Se alcanzó el límite de almacenamiento de esta cuenta")):
            with _peticion():
                respuesta, status = media.create_media.__wrapped__(5)
        self.assertEqual(400, status)
        self.assertEqual([], self._objetos_en_disco())
        pendientes = pending_operations()
        self.assertEqual(1, len(pendientes))
        self.assertEqual("rolled_back", pendientes[0]["state"])

    def test_fallo_ambiguo_nunca_borra_un_objeto_que_podria_estar_referenciado(self):
        # Simula el commit implicito de `db_conn()` reventando DESPUES de que
        # el cuerpo de la transaccion (insert_media_record incluido) ya
        # corrio sin lanzar -- exactamente el hueco historico de este
        # proyecto (ver CLAUDE.md: proceso matado entre save_upload() e
        # insert_media_record()). Aqui no se sabe si la fila SI llego a
        # confirmarse del lado del servidor, asi que el objeto NO se borra.
        llamadas = {"n": 0}

        @contextmanager
        def _db_secuencial():
            llamadas["n"] += 1
            yield object()
            # La consulta temprana de duplicado añade una segunda transacción
            # corta antes del PUT. El commit ambiguo que protege este test
            # sigue siendo el de la tercera: la que inserta la fila final.
            if llamadas["n"] == 3:
                raise ConnectionError("se perdio la conexion justo al confirmar")

        with patch.object(media, "db_conn", _db_secuencial), \
             patch.object(media, "insert_media_record", lambda *a, **k: {"id": 42}), \
             patch.object(media, "remove_media_files") as limpiar:
            with _peticion():
                with self.assertRaises(ConnectionError):
                    media.create_media.__wrapped__(5)
        self.assertGreaterEqual(len(self._objetos_en_disco()), 1)
        limpiar.assert_not_called()
        pendientes = pending_operations()
        self.assertEqual(1, len(pendientes))
        self.assertEqual("pending", pendientes[0]["state"])


# ---------------------------------------------------------------------------
# Reconciliacion de un registro "pending": demuestra que lo que se persiste
# (keys + estado) basta para resolver correctamente en las dos direcciones.
# El reconciliador de verdad (comando de CLI) es una fase posterior; esto
# prueba que la informacion que esta tarea deja escrita es suficiente.
# ---------------------------------------------------------------------------

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        entorno = patch.dict(os.environ, {
            "MEDIA_STATE_ROOT": str(Path(self._tmp.name) / "state"),
            "MEDIA_WORK_ROOT": str(Path(self._tmp.name) / "work"),
        }, clear=False)
        entorno.start()
        self.addCleanup(entorno.stop)

    def _reconciliar(self, operation_id: str, key_referenciada: bool) -> None:
        """Lo que un reconciliador comprobaria (spec S7, "Registro durable y
        resultados inciertos"): si la key terminó referenciada en la base, el
        registro se confirma; si no, se revierte."""
        if key_referenciada:
            mark_committed(operation_id)
        else:
            mark_rolled_back(operation_id)

    def test_una_key_que_termino_referenciada_se_resuelve_a_committed(self):
        operacion = record_pending("upload", [KEY])
        self._reconciliar(operacion, key_referenciada=True)
        # Confirmar borra el registro: la key quedo referenciada, asi que ya
        # no hay nada que compensar y conservarlo solo llenaria el log.
        self.assertIsNone(read_operation(operacion))
        self.assertEqual([], pending_operations())

    def test_una_key_nunca_referenciada_se_resuelve_a_rolled_back(self):
        operacion = record_pending("upload", [KEY])
        self._reconciliar(operacion, key_referenciada=False)
        self.assertEqual("rolled_back", read_operation(operacion)["state"])
        # rolled_back sigue en pendientes: es el reconciliador de verdad
        # (fase posterior) quien de verdad borra las keys y descarta el
        # registro; esta prueba solo fija que la decision se puede tomar.
        self.assertIn(operacion, [r["operation_id"] for r in pending_operations()])


# ---------------------------------------------------------------------------
# El escenario historico real, reproducido matando el proceso de verdad:
# save_upload() termina (el objeto YA esta escrito y el registro queda
# "pending") y el proceso muere antes de poder llamar a insert_media_record/
# mark_committed. Mismo patron de subprocess+kill que ya usan
# test_compensation.py y test_workspace.py para el lock interproceso.
# ---------------------------------------------------------------------------

_HIJO_SUBIDA_INTERRUMPIDA = r"""
import io, sys, time
sys.path.insert(0, sys.argv[1])
from app.storage.media_storage import save_upload

class Subida:
    def __init__(self, datos):
        self.stream = io.BytesIO(datos)
        self.filename = "foto.jpg"
        self.mimetype = "image/jpeg"

datos = open(sys.argv[2], "rb").read()
resultado = save_upload(Subida(datos), 1, 5, prepare_image_metadata=True)
sys.stdout.write(resultado["storage_operation_id"] + "\n")
sys.stdout.write(resultado["storage_path"] + "\n")
sys.stdout.flush()
time.sleep(300)
"""


class CrashBetweenPutAndCommitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._entorno = {
            "MEDIA_STORAGE_ROOT": str(Path(self._tmp.name) / "media"),
            "MEDIA_QUARANTINE_ROOT": str(Path(self._tmp.name) / "quarantine"),
            "MEDIA_WORK_ROOT": str(Path(self._tmp.name) / "work"),
            "MEDIA_STATE_ROOT": str(Path(self._tmp.name) / "state"),
            "MEDIA_LOCAL_TEMP_MAX_GB": "0",
            "MEDIA_WORK_MAX_CONCURRENT": "4",
        }
        self._parche = patch.dict(os.environ, self._entorno, clear=False)
        self._parche.start()
        self.addCleanup(self._parche.stop)

        self._foto = Path(self._tmp.name) / "foto.jpg"
        self._foto.write_bytes(foto())

    def _matar(self, proceso: subprocess.Popen) -> None:
        if proceso.poll() is None:
            proceso.kill()
        proceso.wait(30)
        proceso.stdout.close()

    def test_un_proceso_matado_entre_el_put_y_el_commit_deja_todo_recuperable(self):
        proceso = subprocess.Popen(
            [sys.executable, "-c", _HIJO_SUBIDA_INTERRUMPIDA, _RAIZ_BACKEND, str(self._foto)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=dict(os.environ),
        )
        self.addCleanup(self._matar, proceso)
        operacion = proceso.stdout.readline().strip()
        storage_path = proceso.stdout.readline().strip()
        if not operacion or not storage_path:
            self.fail(f"el hijo no llego a subir nada: {proceso.stdout.read()}")

        # SIGKILL/TerminateProcess directo: nunca corre un `finally`, igual
        # que el postmortem real (proceso matado entre save_upload() e
        # insert_media_record()).
        proceso.kill()
        proceso.wait(30)

        # El objeto sobrevivio (os.replace ya es atomico y durable) y el
        # registro de compensacion quedo "pending", nunca "committed": es
        # exactamente la informacion que un reconciliador necesita.
        objeto = media_storage.storage_root() / storage_path
        self.assertTrue(objeto.is_file())
        registro = read_operation(operacion)
        self.assertEqual("pending", registro["state"])
        self.assertIn(storage_path, registro["keys"])

        # Reconciliar "en frio": como ninguna fila real llego a referenciar
        # esta key, se revierte y se libera el objeto.
        mark_rolled_back(operacion)
        media_storage.remove_stored_file(storage_path)
        self.assertFalse(objeto.is_file())


if __name__ == "__main__":
    unittest.main()
