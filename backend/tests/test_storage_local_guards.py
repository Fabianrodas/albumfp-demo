"""Las utilidades de filesystem de `media_storage` son SOLO locales (spec §12).

En modo `remote` no hay arbol final en este host: `storage_root()` y
`resolve_storage_path()` tienen que negarse ANTES de resolver o crear una
raiz, no devolver un `Path` que apunta a un directorio vacio del VPS. Y el
borrado deja de ser un `except (OSError, ValueError)` que convierte cualquier
fallo en "no habia nada que borrar": eso, con un origin caido, dejaria al
llamador creyendo que los bytes ya no existen.
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

REMOTE = {
    "MEDIA_STORAGE_BACKEND": "remote",
    "MEDIA_ORIGIN_BASE_URL": "https://demo-media.invalid",
    "MEDIA_ORIGIN_TOKEN": "x" * 43,
    "MEDIA_STORAGE_ROOT": "",
    "MEDIA_EXPECTED_MOUNTPOINT": "",
    "USE_X_ACCEL_REDIRECT": "true",
    "NGINX_INTERNAL_MEDIA_URI": "/_protected_media",
}
KEY = "user_1/album_2/" + "a" * 32 + ".jpg"


class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "demo.jpg"):
        self.stream = io.BytesIO(data)
        self.filename = filename


class RemoteModeRefusesLocalRootTests(unittest.TestCase):
    def setUp(self):
        reset_storage_backend()
        self.addCleanup(reset_storage_backend)
        self._anterior = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = None
        self.addCleanup(lambda: setattr(media_storage, "_STORAGE_ROOT", self._anterior))

    def test_storage_root_lanza_en_remote(self):
        with patch.dict(os.environ, REMOTE, clear=False):
            with self.assertRaises(StorageConfigurationError):
                media_storage.storage_root()

    def test_resolve_storage_path_lanza_en_remote(self):
        with patch.dict(os.environ, REMOTE, clear=False):
            with self.assertRaises(StorageConfigurationError):
                media_storage.resolve_storage_path(KEY)

    def test_storage_root_no_resuelve_ninguna_raiz_en_remote(self):
        """El guard va ANTES de resolver la raiz, no despues.

        Comprobar "no se creo /srv/albumfp/media" no distingue nada: en remote
        `MEDIA_STORAGE_ROOT` esta vacia, asi que la raiz que se resolveria es
        el `storage/media` de desarrollo, que ya existe. Lo que si prueba el
        orden es que `_configured_storage_root()` no llegue a llamarse.
        """
        with patch.dict(os.environ, REMOTE, clear=False), patch.object(
            media_storage, "_configured_storage_root"
        ) as espia:
            with self.assertRaises(StorageConfigurationError):
                media_storage.storage_root()
        espia.assert_not_called()


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
        # una caida del origin pareciera "no habia nada que borrar".
        class BackendCaido:
            def delete(self, key, *, expected_version=None):
                raise StorageUnavailable("origin caido")

        with patch("app.storage.media_storage.get_storage_backend", return_value=BackendCaido()):
            with self.assertRaises(StorageUnavailable):
                media_storage.remove_stored_file(KEY)


class CapacityComesFromTheBackendTests(unittest.TestCase):
    """La politica se calcula sobre `backend.capacity()`, no sobre un `df`.

    Consultar capacidad en remote no puede significar medir el disco vacio
    del VPS (spec §6), asi que `storage_capacity_status()` ya no llama a
    `shutil.disk_usage()`: pregunta al backend seleccionado, y respeta las
    dos senales que solo el conoce.
    """

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
