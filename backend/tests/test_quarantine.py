"""S05 -- cuarentena de subidas antes de tocar el arbol final de media.

Todo con archivos temporales reales (mismo criterio que test_previews.py /
test_external_copy.py): lo que se comprueba aqui -- que nunca queda un
huerfano, que el hash es correcto, que el nombre generado nunca sale del
nombre del cliente -- no se puede mockear sin dejar de probarlo.
"""
import hashlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.storage.media_storage as media_storage
import app.storage.quarantine as quarantine
from app.storage.quarantine import promote_quarantined, quarantined_upload


class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "demo.jpg"):
        self.stream = io.BytesIO(data)
        self.filename = filename


class _RaisingStream(io.BytesIO):
    """Devuelve `before` bytes y despues revienta -- simula un cliente que
    se desconecta o un disco que falla a mitad de la copia."""

    def __init__(self, before: bytes):
        super().__init__(before)

    def read(self, n=-1):
        chunk = super().read(n)
        if chunk:
            return chunk
        raise OSError("simulated stream failure")


class QuarantineRootTests(unittest.TestCase):
    def test_rejects_a_root_nested_inside_final_storage(self):
        original_storage = media_storage._STORAGE_ROOT
        original_quarantine = quarantine._QUARANTINE_ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                media_storage._STORAGE_ROOT = Path(tmp)
                quarantine._QUARANTINE_ROOT = Path(tmp) / "quarantine"
                with self.assertRaisesRegex(RuntimeError, "MEDIA_QUARANTINE_ROOT"):
                    quarantine.quarantine_root()
        finally:
            media_storage._STORAGE_ROOT = original_storage
            quarantine._QUARANTINE_ROOT = original_quarantine

    def test_rejects_a_root_that_contains_final_storage(self):
        original_storage = media_storage._STORAGE_ROOT
        original_quarantine = quarantine._QUARANTINE_ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                media_storage._STORAGE_ROOT = Path(tmp) / "media"
                quarantine._QUARANTINE_ROOT = Path(tmp)
                with self.assertRaisesRegex(RuntimeError, "MEDIA_QUARANTINE_ROOT"):
                    quarantine.quarantine_root()
        finally:
            media_storage._STORAGE_ROOT = original_storage
            quarantine._QUARANTINE_ROOT = original_quarantine

    def test_a_sibling_root_is_accepted(self):
        original_storage = media_storage._STORAGE_ROOT
        original_quarantine = quarantine._QUARANTINE_ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                media_storage._STORAGE_ROOT = Path(tmp) / "media"
                quarantine._QUARANTINE_ROOT = Path(tmp) / "quarantine"
                root = quarantine.quarantine_root()
                self.assertTrue(root.is_dir())
        finally:
            media_storage._STORAGE_ROOT = original_storage
            quarantine._QUARANTINE_ROOT = original_quarantine


class QuarantinedUploadTests(unittest.TestCase):
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

    def test_hashes_and_sizes_correctly_without_reading_the_whole_file_at_once(self):
        contenido = b"hola mundo" * 300000  # ~2.8 MiB: mas de un bloque de 1 MiB, para de verdad ejercitar el streaming
        upload = _FakeUpload(contenido)
        with quarantined_upload(upload) as q:
            self.assertEqual(len(contenido), q.size_bytes)
            self.assertEqual(hashlib.sha256(contenido).hexdigest(), q.sha256)
            self.assertTrue(q.path.exists())
        # Al salir del `with`, el archivo de cuarentena desaparece siempre.
        self.assertFalse(q.path.exists())

    def test_the_quarantine_filename_is_generated_never_derived_from_the_client(self):
        upload = _FakeUpload(b"x", filename="../../etc/passwd")
        with quarantined_upload(upload) as q:
            # El nombre EN DISCO de la cuarentena es siempre un uuid: la
            # ruta del cliente nunca participa en construirlo.
            self.assertNotIn("etc", q.path.name)
            self.assertEqual(".tmp", q.path.suffix)
            # El nombre original se conserva como metadato de exhibicion,
            # pero saneado a solo su nombre base -- sin separadores de ruta
            # ni ".." con los que se pudiera reconstruir una travesia.
            self.assertNotIn("/", q.original_filename)
            self.assertNotIn("..", q.original_filename)
            self.assertEqual("passwd", q.original_filename)

    def test_zero_byte_upload_does_not_crash(self):
        upload = _FakeUpload(b"")
        with quarantined_upload(upload) as q:
            self.assertEqual(0, q.size_bytes)
            self.assertEqual(hashlib.sha256(b"").hexdigest(), q.sha256)

    def test_over_limit_streaming_is_rejected_and_cleaned_up(self):
        upload = _FakeUpload(b"x" * 1000)
        with self.assertRaises(ValueError):
            with quarantined_upload(upload, max_bytes=10):
                pass
        archivos = list(quarantine.quarantine_root().rglob("*"))
        self.assertEqual([], [f for f in archivos if f.is_file()])

    def test_a_stream_failure_mid_copy_cleans_up_the_partial_file(self):
        upload = _FakeUpload(b"")
        upload.stream = _RaisingStream(b"algunos bytes")
        with self.assertRaises(OSError):
            with quarantined_upload(upload):
                pass
        archivos = list(quarantine.quarantine_root().rglob("*"))
        self.assertEqual([], [f for f in archivos if f.is_file()])

    @unittest.skipUnless(os.name == "posix", "los bits de permiso de Windows no son comparables")
    def test_the_quarantine_file_is_not_world_or_group_readable(self):
        import stat
        upload = _FakeUpload(b"contenido")
        with quarantined_upload(upload) as q:
            modo = stat.S_IMODE(q.path.stat().st_mode)
            self.assertEqual(0, modo & (stat.S_IRWXG | stat.S_IRWXO))


class PromoteQuarantinedTests(unittest.TestCase):
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

    def test_promotion_copies_content_and_cleans_up_the_quarantine_copy(self):
        contenido = b"\xff\xd8\xff\xe0demo"
        upload = _FakeUpload(contenido, filename="Mi Foto.jpg")
        with quarantined_upload(upload) as q:
            promoted = promote_quarantined(q, owner_id=9, target_scope="album_3", extension="jpg")
            # La copia de cuarentena SIGUE viva mientras el `with` no termina
            # (promote_quarantined copia, no mueve).
            self.assertTrue(q.path.exists())

        destino = media_storage.resolve_storage_path(promoted["storage_path"])
        self.assertTrue(destino.exists())
        self.assertEqual(contenido, destino.read_bytes())
        self.assertEqual(len(contenido), promoted["file_size"])
        self.assertEqual(hashlib.sha256(contenido).hexdigest(), promoted["sha256"])
        self.assertFalse(q.path.exists(), "la copia de cuarentena debe desaparecer al salir del with")
        self.assertEqual("user_9/album_3", destino.parent.relative_to(
            media_storage.storage_root()).as_posix())

    def test_the_final_filename_is_generated_not_the_original(self):
        upload = _FakeUpload(b"x", filename="secreto.jpg")
        with quarantined_upload(upload) as q:
            promoted = promote_quarantined(q, owner_id=1, target_scope="avatar", extension="jpg")
        self.assertNotIn("secreto", promoted["storage_path"])


if __name__ == "__main__":
    unittest.main()
