"""Orden del pipeline de subida y compensación (spec §7)."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import app.storage.media_storage as media_storage
import app.storage.quarantine as quarantine
from app.storage.backends import reset_storage_backend
from app.storage.compensation import read_operation
from app.storage.contracts import StorageUnavailable
from app.storage.media_storage import save_upload


class _Subida:
    def __init__(self, datos: bytes, filename: str = "foto.jpg", mimetype: str = "image/jpeg"):
        self.stream = io.BytesIO(datos)
        self.filename = filename
        self.mimetype = mimetype


def foto(ancho=2400, alto=1600) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (ancho, alto), (10, 120, 90)).save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


class UploadOrchestrationTests(unittest.TestCase):
    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._anterior = (media_storage._STORAGE_ROOT, quarantine._QUARANTINE_ROOT)
        media_storage._STORAGE_ROOT = Path(self._tmp.name) / "media"
        quarantine._QUARANTINE_ROOT = Path(self._tmp.name) / "quarantine"
        self.addCleanup(lambda: (
            setattr(media_storage, "_STORAGE_ROOT", self._anterior[0]),
            setattr(quarantine, "_QUARANTINE_ROOT", self._anterior[1]),
        ))
        self._patch = patch.dict(os.environ, {
            "MEDIA_WORK_ROOT": str(Path(self._tmp.name) / "work"),
            "MEDIA_STATE_ROOT": str(Path(self._tmp.name) / "state"),
            "MEDIA_LOCAL_TEMP_MAX_GB": "0",
            "MEDIA_WORK_MAX_CONCURRENT": "4",
        }, clear=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_una_subida_valida_devuelve_key_hash_y_operacion(self):
        resultado = save_upload(_Subida(foto()), 1, 2)
        self.assertTrue(resultado["storage_path"].startswith("user_1/album_2/"))
        self.assertEqual(64, len(resultado["sha256"]))
        self.assertRegex(resultado["storage_operation_id"], r"^[0-9a-f]{32}$")
        self.assertEqual("image", resultado["file_type"])

    def test_el_hook_de_duplicado_corre_antes_de_preview_operacion_o_put(self):
        observed = []

        def before_store(digest):
            observed.append(digest)
            self.assertEqual([], list(media_storage.storage_root().rglob("*")))
            state = Path(self._tmp.name) / "state"
            self.assertFalse(state.exists())

        result = save_upload(
            _Subida(foto()), 1, 2, prepare_image_metadata=True,
            before_store=before_store,
        )
        self.assertEqual([result["sha256"]], observed)

    def test_si_el_hook_rechaza_no_queda_objeto_operacion_preview_ni_cuarentena(self):
        class Duplicate(Exception):
            pass

        with self.assertRaises(Duplicate):
            save_upload(
                _Subida(foto()), 1, 2, prepare_image_metadata=True,
                before_store=lambda digest: (_ for _ in ()).throw(Duplicate(digest)),
            )

        self.assertEqual([], list(media_storage.storage_root().rglob("*")))
        self.assertEqual([], list(quarantine.quarantine_root().iterdir()))
        self.assertFalse((Path(self._tmp.name) / "state").exists())

    def test_la_resolucion_es_la_decodificada_por_el_servidor(self):
        self.assertEqual("2400x1600", save_upload(_Subida(foto()), 1, 2)["resolution"])

    def test_con_prepare_image_metadata_devuelve_exif_y_preview_ya_hechos(self):
        resultado = save_upload(_Subida(foto()), 1, 2, prepare_image_metadata=True)
        self.assertIn("exif", resultado)
        self.assertIn("preview", resultado)
        self.assertIsNotNone(resultado["preview"])
        self.assertTrue(resultado["preview"]["storage_path"].endswith(".webp"))

    def test_el_registro_de_compensacion_lista_original_y_preview(self):
        resultado = save_upload(_Subida(foto()), 1, 2, prepare_image_metadata=True)
        registro = read_operation(resultado["storage_operation_id"])
        self.assertEqual("pending", registro["state"])
        self.assertIn(resultado["storage_path"], registro["keys"])
        self.assertIn(resultado["preview"]["storage_path"], registro["keys"])

    def test_los_derivados_se_generan_ANTES_de_persistir_el_original(self):
        # El original recién subido no se vuelve a descargar del backend.
        llamadas = []
        real = media_storage.get_storage_backend()

        class Espia:
            def __getattr__(self, nombre):
                atributo = getattr(real, nombre)
                if nombre in ("materialize", "put_from_path"):
                    def envuelto(*args, **kwargs):
                        llamadas.append(nombre)
                        return atributo(*args, **kwargs)
                    return envuelto
                return atributo

        with patch("app.storage.media_storage.get_storage_backend", return_value=Espia()), \
             patch("app.media.previews.get_storage_backend", create=True, return_value=Espia()):
            save_upload(_Subida(foto()), 1, 2, prepare_image_metadata=True)
        self.assertNotIn("materialize", llamadas,
                         "no se descarga del backend un original que sigue en cuarentena")

    def test_una_imagen_corrupta_se_rechaza_y_no_persiste_nada(self):
        with self.assertRaises(ValueError):
            save_upload(_Subida(b"\xff\xd8\xff\xe0no soy una foto"), 1, 2)
        self.assertEqual([], list(media_storage.storage_root().rglob("*.jpg")))

    def test_un_fallo_del_backend_no_deja_cuarentena_ni_workspace(self):
        from app.storage.workspace import workspace_root

        class BackendCaido:
            def put_from_path(self, key, path):
                raise StorageUnavailable("almacenamiento local no disponible")

            def capacity(self):
                from app.storage.contracts import StorageCapacity
                import time
                return StorageCapacity(10**12, 0, 10**12, True, True, time.time())

        with patch("app.storage.media_storage.get_storage_backend", return_value=BackendCaido()), \
             patch("app.storage.quarantine.get_storage_backend", create=True,
                   return_value=BackendCaido()):
            with self.assertRaises(StorageUnavailable):
                save_upload(_Subida(foto()), 1, 2)
        self.assertEqual([], list(quarantine.quarantine_root().iterdir()))
        self.assertEqual([], [p for p in workspace_root().iterdir() if p.is_dir()])

    def test_el_avatar_no_genera_vista_previa(self):
        resultado = save_upload(_Subida(foto()), 7, folder="avatar", prepare_image_metadata=True)
        self.assertTrue(resultado["storage_path"].startswith("user_7/avatar/"))
        self.assertIsNone(resultado["preview"])

    def test_un_video_no_genera_preview_ni_exif(self):
        datos = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200
        resultado = save_upload(_Subida(datos, "clip.mp4", mimetype="video/mp4"), 1, 2,
                                prepare_image_metadata=True)
        self.assertEqual("video", resultado["file_type"])
        self.assertIsNone(resultado["preview"])
        self.assertEqual({}, resultado["exif"])

    def test_sin_prepare_image_metadata_el_dict_no_gana_esas_claves(self):
        resultado = save_upload(_Subida(foto()), 1, 2)
        self.assertNotIn("exif", resultado)
        self.assertNotIn("preview", resultado)

    def test_un_fallo_de_capacidad_marca_la_operacion_como_rolled_back(self):
        # Un fallo ANTES del primer PUT (aqui, sin espacio) ya tiene un
        # registro de compensacion escrito (record_pending corre antes) y no
        # debe dejarlo colgado en "pending" para siempre.
        from app.storage.compensation import pending_operations
        from app.storage.contracts import StorageCapacity

        class BackendSinEspacio:
            def capacity(self):
                return StorageCapacity(100, 100, 0, True, True, 0.0)

            def put_from_path(self, key, path):
                raise AssertionError("no debe intentar un PUT sin espacio suficiente")

        with patch("app.storage.media_storage.get_storage_backend",
                   return_value=BackendSinEspacio()):
            with self.assertRaises(ValueError):
                save_upload(_Subida(foto()), 1, 2)
        colgadas = [op for op in pending_operations() if op["state"] == "pending"]
        self.assertEqual([], colgadas)


if __name__ == "__main__":
    unittest.main()
