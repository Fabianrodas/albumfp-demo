"""S06 (ajustada) -- decodificacion de imagenes con Pillow dentro de
`save_upload()`, entre cuarentena y promocion.

`validate_image` ya tiene sus propias pruebas puras (test_image_validation.py);
aqui se fija la ORQUESTACION -- que una imagen corrupta nunca promueva nada,
que la resolucion servidor-decodificada llegue hasta el dict que devuelve
save_upload(), y que un video pase de largo sin ninguna validacion propia
(decision de portabilidad: ver CLAUDE.md sobre por que esta app no depende
de ClamAV ni ffprobe).
"""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pillow_heif import from_pillow

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.storage.media_storage as media_storage
import app.storage.quarantine as quarantine


class _FakeUpload:
    def __init__(self, data: bytes, filename: str, mimetype: str):
        self.stream = io.BytesIO(data)
        self.filename = filename
        self.mimetype = mimetype


def _jpeg_bytes(size=(50, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "purple").save(buffer, format="JPEG")
    return buffer.getvalue()


def _heic_bytes(size=(50, 40)) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.heic"
        from_pillow(Image.new("RGB", size, "purple")).save(path, quality=90)
        return path.read_bytes()


class UploadValidationPipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original_storage = media_storage._STORAGE_ROOT
        self._original_quarantine = quarantine._QUARANTINE_ROOT
        media_storage._STORAGE_ROOT = Path(self._tmp.name) / "media"
        quarantine._QUARANTINE_ROOT = Path(self._tmp.name) / "quarantine"

    def tearDown(self):
        media_storage._STORAGE_ROOT = self._original_storage
        quarantine._QUARANTINE_ROOT = self._original_quarantine
        self._tmp.cleanup()

    def _sin_archivos_en_disco(self) -> bool:
        final = list(media_storage.storage_root().rglob("*"))
        cuarentena = list(quarantine.quarantine_root().rglob("*"))
        return not any(p.is_file() for p in final + cuarentena)

    def test_una_imagen_corrupta_se_rechaza_y_no_promueve_nada(self):
        upload = _FakeUpload(b"\xff\xd8\xff\xe0" + b"esto no es un jpeg real", "foto.jpg", "image/jpeg")
        with self.assertRaises(ValueError):
            media_storage.save_upload(upload, owner_id=1, album_id=1)
        self.assertTrue(self._sin_archivos_en_disco())

    def test_la_resolucion_del_resultado_es_la_decodificada_por_el_servidor(self):
        upload = _FakeUpload(_jpeg_bytes(size=(333, 222)), "foto.jpg", "image/jpeg")
        stored = media_storage.save_upload(upload, owner_id=1, album_id=1)
        self.assertEqual("333x222", stored["resolution"])

    def test_heic_recorre_firma_cuarentena_validacion_y_storage_sin_transcodificar(self):
        contenido = _heic_bytes(size=(63, 41))
        upload = _FakeUpload(contenido, "iphone.heic", "image/heic")

        stored = media_storage.save_upload(upload, owner_id=1, album_id=1)

        self.assertEqual("image", stored["file_type"])
        self.assertEqual("heic", stored["format"])
        self.assertEqual("image/heic", stored["mime_type"])
        self.assertEqual("63x41", stored["resolution"])
        self.assertEqual(contenido, media_storage.resolve_storage_path(stored["storage_path"]).read_bytes())

    def test_un_video_no_tiene_validacion_propia_y_se_promueve_sin_tocar_pillow(self):
        """Deliberado: esta app no depende de ffprobe ni de ningun otro
        binario del sistema para validar video (ver CLAUDE.md). Un video
        solo pasa por la firma de bytes (ya verificada antes de aqui) y la
        cuarentena; `validate_image` no debe ni llamarse."""
        contenido = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
        upload = _FakeUpload(contenido, "video.mp4", "video/mp4")
        with patch.object(media_storage, "validate_image") as espia_pillow:
            stored = media_storage.save_upload(upload, owner_id=1, album_id=1)
        espia_pillow.assert_not_called()
        self.assertNotIn("resolution", stored)
        self.assertTrue(media_storage.resolve_storage_path(stored["storage_path"]).exists())


if __name__ == "__main__":
    unittest.main()
