"""Regression tests for safe local Demo maintenance commands."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cli
import app.storage.media_storage as media_storage
import app.storage.quarantine as quarantine
from app.storage.backends import reset_storage_backend


class ChecksumCommandRegistryTests(unittest.TestCase):
    def test_checksum_jobs_are_explicit_console_commands(self):
        self.assertIn("backfill-media-checksums", cli.COMMANDS)
        self.assertIn("verify-media-integrity", cli.COMMANDS)

    def test_external_ip_reputation_refresh_is_not_registered(self):
        self.assertNotIn("refresh-ip-reputation", cli.COMMANDS)


class CleanupQuarantineTests(unittest.TestCase):
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

    def _crear_archivo(self, nombre: str, antiguedad_horas: float) -> Path:
        root = quarantine.quarantine_root()
        archivo = root / nombre
        archivo.write_bytes(b"huerfano")
        vieja = time.time() - antiguedad_horas * 3600
        __import__("os").utime(archivo, (vieja, vieja))
        return archivo

    def test_borra_solo_lo_mas_viejo_que_el_umbral(self):
        viejo = self._crear_archivo("viejo.tmp", antiguedad_horas=48)
        nuevo = self._crear_archivo("nuevo.tmp", antiguedad_horas=1)
        with patch.dict("os.environ", {"MEDIA_QUARANTINE_MAX_AGE_HOURS": "24"}):
            resultado = cli.cleanup_quarantine()
        self.assertEqual(0, resultado)
        self.assertFalse(viejo.exists())
        self.assertTrue(nuevo.exists())

    def test_sin_huerfanos_no_rompe(self):
        with patch.dict("os.environ", {"MEDIA_QUARANTINE_MAX_AGE_HOURS": "24"}):
            self.assertEqual(0, cli.cleanup_quarantine())


def _resultado_filas(filas: list[dict]):
    resultado = MagicMock()
    resultado.mappings.return_value.all.return_value = filas
    return resultado


class CleanupOrphanedMediaTests(unittest.TestCase):
    """Bugfix real de S05: la promocion del archivo y el INSERT de su fila no
    son atomicos entre si -- un proceso muerto justo en medio (reinicio
    manual, OOM) deja un archivo completo en el arbol final sin ninguna fila
    que lo referencie. Reproducido de verdad matando el backend en ese punto
    exacto con una subida de mas de 500 MB.

    Desde que la fase de local storage design reemplazo el recorrido directo
    del filesystem por `backend.list_objects()` (spec seccion 12), el
    inventario solo incluye objetos con la key canonica (nombre de 32
    caracteres hexadecimales); un nombre legible como "huerfano.mp4" ya no
    es un objeto de storage valido y `LocalStorageBackend` lo salta como
    residuo no canonico. Los fixtures usan keys UUID-like por eso -- el
    mismo criterio que ya usaba `test_cli_storage.py`. `MEDIA_STATE_ROOT` se
    aisla porque `cleanup_orphaned_media` ahora tambien consulta
    `compensation.pending_operations()` de verdad, no solo `_STORAGE_ROOT`.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original_storage = media_storage._STORAGE_ROOT
        media_storage._STORAGE_ROOT = Path(self._tmp.name)
        self._state_patch = patch.dict(
            "os.environ", {"MEDIA_STATE_ROOT": str(Path(self._tmp.name) / "state")}, clear=False
        )
        self._state_patch.start()
        reset_storage_backend()

    def tearDown(self):
        media_storage._STORAGE_ROOT = self._original_storage
        self._state_patch.stop()
        reset_storage_backend()
        self._tmp.cleanup()

    def _crear_archivo(self, relativo: str, antiguedad_horas: float) -> Path:
        archivo = media_storage.storage_root() / relativo
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_bytes(b"contenido")
        vieja = time.time() - antiguedad_horas * 3600
        __import__("os").utime(archivo, (vieja, vieja))
        return archivo

    def _con_referencias(self, medias=(), previews=(), avatares=()):
        """`_referenced_storage_paths` corre mas de una vez por ejecucion
        del GC (una vez al principio y otra vez por lote, spec seccion 12
        paso 4), asi que el doble no puede depender de una lista fija de
        `side_effect`: se dispatchea por el texto de la query, sin importar
        cuantas veces se llame."""
        db_conn_mock = MagicMock()
        db_conn_mock.return_value.__enter__.return_value = MagicMock()

        def _responder(conn, query, params=None):
            if "preview_storage_path" in query:
                return _resultado_filas([{"preview_storage_path": p} for p in previews])
            if "avatar_path" in query:
                return _resultado_filas([{"avatar_path": p} for p in avatares])
            if "storage_path" in query:
                return _resultado_filas([{"storage_path": p} for p in medias])
            raise AssertionError(f"query inesperada en _con_referencias: {query}")

        execute_espia = MagicMock(side_effect=_responder)
        return db_conn_mock, execute_espia

    def test_un_archivo_huerfano_y_viejo_se_borra(self):
        huerfano = self._crear_archivo("user_2/album_2/" + "1" * 32 + ".mp4", antiguedad_horas=48)
        db_conn_mock, execute_espia = self._con_referencias()
        with patch.object(cli, "db_conn", db_conn_mock), patch.object(cli, "execute_safe", execute_espia):
            with patch.dict("os.environ", {"ORPHANED_MEDIA_MAX_AGE_HOURS": "24"}):
                resultado = cli.cleanup_orphaned_media()
        self.assertEqual(0, resultado)
        self.assertFalse(huerfano.exists())

    def test_un_archivo_huerfano_pero_reciente_se_conserva(self):
        """El margen de edad es lo que protege una subida que esta pasando
        AHORA MISMO: su fila todavia no comitea, pero no por eso es basura."""
        reciente = self._crear_archivo("user_2/album_2/" + "2" * 32 + ".mp4", antiguedad_horas=1)
        db_conn_mock, execute_espia = self._con_referencias()
        with patch.object(cli, "db_conn", db_conn_mock), patch.object(cli, "execute_safe", execute_espia):
            with patch.dict("os.environ", {"ORPHANED_MEDIA_MAX_AGE_HOURS": "24"}):
                resultado = cli.cleanup_orphaned_media()
        self.assertEqual(0, resultado)
        self.assertTrue(reciente.exists())

    def test_un_archivo_referenciado_en_media_se_conserva_aunque_sea_viejo(self):
        archivo = self._crear_archivo("user_2/album_2/" + "3" * 32 + ".jpg", antiguedad_horas=999)
        relativo = archivo.relative_to(media_storage.storage_root()).as_posix()
        db_conn_mock, execute_espia = self._con_referencias(medias=[relativo])
        with patch.object(cli, "db_conn", db_conn_mock), patch.object(cli, "execute_safe", execute_espia):
            with patch.dict("os.environ", {"ORPHANED_MEDIA_MAX_AGE_HOURS": "24"}):
                cli.cleanup_orphaned_media()
        self.assertTrue(archivo.exists())

    def test_una_vista_previa_referenciada_se_conserva(self):
        archivo = self._crear_archivo("user_2/album_2/" + "4" * 32 + ".webp", antiguedad_horas=999)
        relativo = archivo.relative_to(media_storage.storage_root()).as_posix()
        db_conn_mock, execute_espia = self._con_referencias(previews=[relativo])
        with patch.object(cli, "db_conn", db_conn_mock), patch.object(cli, "execute_safe", execute_espia):
            with patch.dict("os.environ", {"ORPHANED_MEDIA_MAX_AGE_HOURS": "24"}):
                cli.cleanup_orphaned_media()
        self.assertTrue(archivo.exists())

    def test_un_avatar_referenciado_se_conserva(self):
        archivo = self._crear_archivo("user_2/avatar/" + "5" * 32 + ".jpg", antiguedad_horas=999)
        relativo = archivo.relative_to(media_storage.storage_root()).as_posix()
        db_conn_mock, execute_espia = self._con_referencias(avatares=[relativo])
        with patch.object(cli, "db_conn", db_conn_mock), patch.object(cli, "execute_safe", execute_espia):
            with patch.dict("os.environ", {"ORPHANED_MEDIA_MAX_AGE_HOURS": "24"}):
                cli.cleanup_orphaned_media()
        self.assertTrue(archivo.exists())

    def test_sin_archivos_no_rompe(self):
        db_conn_mock, execute_espia = self._con_referencias()
        with patch.object(cli, "db_conn", db_conn_mock), patch.object(cli, "execute_safe", execute_espia):
            self.assertEqual(0, cli.cleanup_orphaned_media())


if __name__ == "__main__":
    unittest.main()
