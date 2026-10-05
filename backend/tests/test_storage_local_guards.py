"""Las utilidades de filesystem usan almacenamiento local y fallan cerradas
si se configura un modo que el Demo no admite.
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.storage.media_storage as media_storage
import app.storage.quarantine as quarantine
from app.storage.backends import reset_storage_backend
from app.storage.contracts import (
    InvalidStorageKey,
    ObjectConflict,
    StorageCapacity,
    StorageConfigurationError,
    StorageUnavailable,
)

UNSUPPORTED_BACKEND = {
    "MEDIA_STORAGE_BACKEND": "network",
    "MEDIA_STORAGE_ROOT": "",
}
KEY = "user_1/album_2/" + "a" * 32 + ".jpg"


class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "demo.jpg"):
        self.stream = io.BytesIO(data)
        self.filename = filename


class UnsupportedModeRefusesLocalRootTests(unittest.TestCase):
    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)
        self._anterior = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = None
        self.addCleanup(lambda: setattr(media_storage, "_STORAGE_ROOT", self._anterior))

    def test_storage_root_rejects_an_unsupported_mode(self):
        with patch.dict(os.environ, UNSUPPORTED_BACKEND, clear=False):
            with self.assertRaises(StorageConfigurationError):
                media_storage.storage_root()

    def test_resolve_storage_path_rejects_an_unsupported_mode(self):
        with patch.dict(os.environ, UNSUPPORTED_BACKEND, clear=False):
            with self.assertRaises(StorageConfigurationError):
                media_storage.resolve_storage_path(KEY)

    def test_rejects_unsupported_mode_before_resolving_a_root(self):
        with patch.dict(os.environ, UNSUPPORTED_BACKEND, clear=False), patch.object(
            media_storage, "_configured_storage_root"
        ) as espia:
            with self.assertRaises(StorageConfigurationError):
                media_storage.storage_root()
        espia.assert_not_called()


class RequiredLocalMountTests(unittest.TestCase):
    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)
        self._anterior = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = None
        self.addCleanup(lambda: setattr(media_storage, "_STORAGE_ROOT", self._anterior))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _settings(self, root: Path, mount: Path):
        return patch.dict(os.environ, {
            "MEDIA_STORAGE_BACKEND": "local",
            "MEDIA_STORAGE_ROOT": str(root),
            "MEDIA_STORAGE_REQUIRED_MOUNTPOINT": str(mount),
        }, clear=False)

    def test_missing_required_mount_stops_before_creating_storage(self):
        root = Path(self._tmp.name) / "media"
        mount = Path(self._tmp.name) / "not-mounted"
        with self._settings(root, mount), patch("app.storage.media_storage.os.path.ismount", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "no está montado"):
                media_storage.storage_root()
        self.assertFalse(root.exists())

    def test_storage_root_must_remain_inside_required_mount(self):
        mount = Path(self._tmp.name) / "mounted-volume"
        mount.mkdir()
        root = Path(self._tmp.name) / "outside-media"
        with self._settings(root, mount), patch("app.storage.media_storage.os.path.ismount", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "MEDIA_STORAGE_ROOT"):
                media_storage.storage_root()
        self.assertFalse(root.exists())

    def test_storage_root_inside_required_mount_is_allowed(self):
        mount = Path(self._tmp.name) / "mounted-volume"
        mount.mkdir()
        root = mount / "media"
        with self._settings(root, mount), patch("app.storage.media_storage.os.path.ismount", return_value=True):
            self.assertEqual(root.resolve(), media_storage.storage_root())
        self.assertTrue(root.is_dir())


class RemoveStoredFileTests(unittest.TestCase):
    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._anterior = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = Path(self._tmp.name) / "media"
        self.addCleanup(lambda: setattr(media_storage, "_STORAGE_ROOT", self._anterior))

    def test_none_sigue_devolviendo_false(self):
        self.assertFalse(media_storage.remove_stored_file(None))

    def test_una_key_invalida_devuelve_false_sin_lanzar(self):
        self.assertFalse(media_storage.remove_stored_file("../etc/passwd"))

    def test_un_archivo_ausente_devuelve_false(self):
        self.assertFalse(media_storage.remove_stored_file(KEY))

    def test_un_archivo_presente_se_borra_y_devuelve_true(self):
        destino = media_storage.storage_root() / KEY
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"x")
        self.assertTrue(media_storage.remove_stored_file(KEY))
        self.assertFalse(destino.exists())

    def test_un_conflicto_de_version_no_es_un_borrado(self):
        class BackendEnConflicto:
            def delete(self, key, *, expected_version=None):
                raise ObjectConflict(key)

        with patch("app.storage.media_storage.get_storage_backend", return_value=BackendEnConflicto()):
            self.assertFalse(media_storage.remove_stored_file(KEY))

    def test_un_fallo_de_infraestructura_NO_se_traga_como_false(self):
        # Regresion de un hueco real: `except (OSError, ValueError)` hacia que
        # una caída del almacenamiento pareciera "no había nada que borrar".
        class BackendCaido:
            def delete(self, key, *, expected_version=None):
                raise StorageUnavailable("almacenamiento local no disponible")

        with patch("app.storage.media_storage.get_storage_backend", return_value=BackendCaido()):
            with self.assertRaises(StorageUnavailable):
                media_storage.remove_stored_file(KEY)


class CapacityComesFromTheBackendTests(unittest.TestCase):
    """La política usa las métricas que entrega el backend local."""

    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)

    def _con_capacidad(self, **campos):
        base = dict(
            total_bytes=1000, used_bytes=100, free_bytes=900,
            writable=True, mount_ok=True, checked_at=0.0,
        )
        capacidad = StorageCapacity(**{**base, **campos})
        backend = SimpleNamespace(capacity=lambda: capacidad)
        return patch("app.storage.media_storage.get_storage_backend", return_value=backend)

    def test_espacio_de_sobra_con_todo_en_orden_esta_permitido(self):
        with self._con_capacidad():
            self.assertTrue(media_storage.storage_capacity_status(10)["allowed"])

    def test_un_volumen_de_solo_lectura_no_esta_permitido_aunque_sobre_espacio(self):
        with self._con_capacidad(writable=False):
            self.assertFalse(media_storage.storage_capacity_status(10)["allowed"])

    def test_un_volumen_sin_montar_no_esta_permitido_aunque_sobre_espacio(self):
        with self._con_capacidad(mount_ok=False):
            self.assertFalse(media_storage.storage_capacity_status(10)["allowed"])


class PromotionUsesAGeneratedCanonicalKeyTests(unittest.TestCase):
    """La key la genera el servidor con la gramatica canonica (spec §16)."""

    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._raiz = media_storage._STORAGE_ROOT
        self._cuarentena = quarantine._QUARANTINE_ROOT
        media_storage._STORAGE_ROOT = Path(self._tmp.name) / "media"
        quarantine._QUARANTINE_ROOT = Path(self._tmp.name) / "quarantine"
        self.addCleanup(lambda: setattr(quarantine, "_QUARANTINE_ROOT", self._cuarentena))
        self.addCleanup(lambda: setattr(media_storage, "_STORAGE_ROOT", self._raiz))

    def test_un_scope_que_no_es_album_ni_avatar_se_rechaza_sin_escribir_nada(self):
        with quarantine.quarantined_upload(_FakeUpload(b"x")) as q:
            with self.assertRaises(InvalidStorageKey):
                quarantine.promote_quarantined(q, owner_id=9, target_scope="../etc", extension="jpg")
        final = media_storage.storage_root()
        self.assertEqual([], [p for p in final.rglob("*") if p.is_file()])


if __name__ == "__main__":
    unittest.main()
