"""LocalStorageBackend: el filesystem de siempre, tras el protocolo (spec §6)."""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.storage.backends as backends
import app.storage.media_storage as media_storage
from app.storage.contracts import (
    InvalidStorageKey,
    ObjectConflict,
    ObjectNotFound,
    StorageError,
    StorageBackend,
    StorageConfigurationError,
)
from app.storage.local_backend import LocalStorageBackend

KEY = "user_1/album_2/" + "a" * 32 + ".jpg"


class LocalBackendTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._anterior = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = Path(self._tmp.name) / "media"
        self.origen = Path(self._tmp.name) / "fuente.bin"
        self.origen.write_bytes(b"contenido de prueba")
        self.backend = LocalStorageBackend()

    def tearDown(self):
        media_storage._STORAGE_ROOT = self._anterior
        self._tmp.cleanup()

    def test_cumple_el_protocolo(self):
        self.assertIsInstance(self.backend, StorageBackend)

    def test_put_copia_y_devuelve_recibo_con_hash_real(self):
        recibo = self.backend.put_from_path(KEY, self.origen)
        self.assertTrue(recibo.created)
        self.assertEqual(len(b"contenido de prueba"), recibo.size_bytes)
        self.assertEqual(hashlib.sha256(b"contenido de prueba").hexdigest(), recibo.sha256)
        self.assertEqual(KEY, recibo.key)

    def test_put_no_borra_ni_modifica_la_fuente(self):
        self.backend.put_from_path(KEY, self.origen)
        self.assertTrue(self.origen.is_file())
        self.assertEqual(b"contenido de prueba", self.origen.read_bytes())

    def test_put_repetido_con_el_mismo_contenido_no_es_creacion(self):
        self.backend.put_from_path(KEY, self.origen)
        recibo = self.backend.put_from_path(KEY, self.origen)
        self.assertFalse(recibo.created)

    def test_put_sobre_una_key_existente_con_otro_contenido_es_conflicto(self):
        self.backend.put_from_path(KEY, self.origen)
        distinto = Path(self._tmp.name) / "otro.bin"
        distinto.write_bytes(b"otra cosa")
        with self.assertRaises(ObjectConflict):
            self.backend.put_from_path(KEY, distinto)

    def test_put_no_deja_archivo_part_al_terminar(self):
        self.backend.put_from_path(KEY, self.origen)
        raiz = media_storage.storage_root()
        self.assertEqual([], [p.name for p in raiz.rglob("*.part")])

    def test_un_fallo_a_mitad_no_deja_nombre_final_ni_temporal(self):
        inexistente = Path(self._tmp.name) / "no-existe.bin"
        with self.assertRaises(OSError):
            self.backend.put_from_path(KEY, inexistente)
        raiz = media_storage.storage_root()
        self.assertEqual([], [p.name for p in raiz.rglob("*") if p.is_file()])

    def test_exists_stat_y_delete(self):
        self.assertFalse(self.backend.exists(KEY))
        self.backend.put_from_path(KEY, self.origen)
        self.assertTrue(self.backend.exists(KEY))
        stat = self.backend.stat(KEY)
        self.assertEqual(len(b"contenido de prueba"), stat.size_bytes)
        self.assertEqual("image/jpeg", stat.mime_type)
        self.assertTrue(self.backend.delete(KEY))
        self.assertFalse(self.backend.exists(KEY))

    def test_stat_de_un_objeto_ausente_lanza_object_not_found(self):
        with self.assertRaises(ObjectNotFound):
            self.backend.stat(KEY)

    def test_exists_propaga_un_fallo_de_infraestructura_en_vez_de_negar(self):
        # Contrato del protocolo: `False` es exclusivamente ObjectNotFound. Un
        # permiso denegado que se leyera como ausencia dejaria al GC borrando
        # objetos vivos. El objeto existe de verdad, asi que solo se deniega
        # el stat del objeto: los de los directorios siguen pasando.
        self.backend.put_from_path(KEY, self.origen)
        stat_real = os.stat

        def denegado(path, *args, **kwargs):
            if os.fspath(path).endswith(".jpg"):
                raise PermissionError(13, "denegado")
            return stat_real(path, *args, **kwargs)

        with patch("os.stat", side_effect=denegado):
            with self.assertRaises(PermissionError):
                self.backend.exists(KEY)

    def test_delete_de_una_key_ausente_devuelve_false_sin_lanzar(self):
        self.assertFalse(self.backend.delete(KEY))

    def test_delete_con_version_distinta_es_conflicto_y_no_borra(self):
        self.backend.put_from_path(KEY, self.origen)
        with self.assertRaises(ObjectConflict):
            self.backend.delete(KEY, expected_version="version-que-no-es")
        self.assertTrue(self.backend.exists(KEY))

    def test_delete_con_la_version_correcta_si_borra(self):
        self.backend.put_from_path(KEY, self.origen)
        version = self.backend.stat(KEY).version
        self.assertTrue(self.backend.delete(KEY, expected_version=version))

    def test_materialize_cede_el_path_real_y_no_lo_borra_al_salir(self):
        self.backend.put_from_path(KEY, self.origen)
        with self.backend.materialize(KEY) as path:
            self.assertTrue(path.is_file())
            self.assertEqual(b"contenido de prueba", path.read_bytes())
            guardado = path
        self.assertTrue(guardado.is_file())

    def test_materialize_de_un_objeto_ausente_lanza_object_not_found(self):
        with self.assertRaises(ObjectNotFound):
            with self.backend.materialize(KEY):
                pass

    def test_capacity_informa_mount_ok_y_writable(self):
        cap = self.backend.capacity()
        self.assertTrue(cap.mount_ok)
        self.assertTrue(cap.writable)
        self.assertGreater(cap.total_bytes, 0)

    def test_list_objects_pagina_y_marca_complete_solo_al_final(self):
        for i in range(3):
            fuente = Path(self._tmp.name) / f"f{i}.bin"
            fuente.write_bytes(bytes([i]) * 10)
            self.backend.put_from_path(f"user_1/album_2/{str(i) * 32}.jpg", fuente)
        pagina = self.backend.list_objects(limit=2)
        self.assertEqual(2, len(pagina.objects))
        self.assertFalse(pagina.complete)
        self.assertIsNotNone(pagina.next_cursor)
        ultima = self.backend.list_objects(cursor=pagina.next_cursor, limit=2)
        self.assertEqual(1, len(ultima.objects))
        self.assertTrue(ultima.complete)
        self.assertIsNone(ultima.next_cursor)

    def test_list_objects_rechaza_un_cursor_malformado_con_error_tipado(self):
        # El GC de la fase 26 debe fallar cerrado ante un inventario dudoso, y
        # solo puede hacerlo si el error pertenece a la jerarquia del contrato:
        # un ValueError pelado se escapa de cualquier `except StorageError`.
        for cursor in ("abc", "1e3", "  ", "1.0", "+"):
            with self.subTest(cursor=cursor):
                with self.assertRaises(StorageError):
                    self.backend.list_objects(cursor=cursor)

    def test_list_objects_ignora_archivos_part_y_ocultos(self):
        self.backend.put_from_path(KEY, self.origen)
        raiz = media_storage.storage_root()
        (raiz / "user_1" / "album_2" / ".basura.part").write_bytes(b"x")
        claves = [o.key for o in self.backend.list_objects(limit=500).objects]
        self.assertEqual([KEY], claves)


class LocalBackendKeyValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._anterior = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = Path(self._tmp.name) / "media"
        self.origen = Path(self._tmp.name) / "fuente.bin"
        self.origen.write_bytes(b"x")
        self.backend = LocalStorageBackend()

    def tearDown(self):
        media_storage._STORAGE_ROOT = self._anterior
        self._tmp.cleanup()

    def test_toda_operacion_valida_la_key_antes_de_tocar_el_disco(self):
        malas = ["user_1/../etc/passwd", "/etc/passwd", "user_1/album_2/%2e%2e.jpg", ""]
        for mala in malas:
            with self.subTest(key=mala):
                with self.assertRaises(InvalidStorageKey):
                    self.backend.exists(mala)
                with self.assertRaises(InvalidStorageKey):
                    self.backend.stat(mala)
                with self.assertRaises(InvalidStorageKey):
                    self.backend.delete(mala)
                with self.assertRaises(InvalidStorageKey):
                    self.backend.put_from_path(mala, self.origen)


class LocalBackendSelectionTests(unittest.TestCase):
    """La rama `local` de `get_storage_backend()` ya tiene implementación."""

    def setUp(self):
        backends.reset_storage_backend()
        self.addCleanup(backends.reset_storage_backend)

    def test_el_modo_local_resuelve_al_backend_local_real(self):
        with patch.dict(
            os.environ, {"MEDIA_STORAGE_BACKEND": "local", "APP_ENV": "development"}, clear=False
        ):
            self.assertIsInstance(backends.get_storage_backend(), LocalStorageBackend)

    def test_el_backend_local_se_reutiliza_entre_llamadas(self):
        with patch.dict(
            os.environ, {"MEDIA_STORAGE_BACKEND": "local", "APP_ENV": "development"}, clear=False
        ):
            self.assertIs(backends.get_storage_backend(), backends.get_storage_backend())

    def test_no_local_storage_mode_is_rejected(self):
        with patch.dict(os.environ, {"MEDIA_STORAGE_BACKEND": "network"}, clear=False):
            with self.assertRaises(StorageConfigurationError):
                backends.get_storage_backend()


if __name__ == "__main__":
    unittest.main()
