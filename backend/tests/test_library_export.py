"""L13: exportación portable de la biblioteca, sin base de datos.

El ZIP se genera en streaming (stdlib `zipfile` sobre un destino no
buscable): nunca existe un archivo con la biblioteca entera en el servidor y
cada original pasa por `materialize()` de uno en uno. Aquí se fijan las rutas
seguras dentro del ZIP, las opciones y la forma del stream; el comportamiento
contra PostgreSQL real está en `test_library_export_db.py`.
"""
import ast
import io
import json
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


class ArchiveNameTests(unittest.TestCase):
    def test_every_original_lives_under_originals_with_its_id_as_prefix(self):
        from app.library_export import archive_name

        self.assertEqual("albumfp-export/originals/7-playa.jpg", archive_name(7, "playa.jpg", "jpg"))
        self.assertEqual("albumfp-export/originals/8-foto.heic", archive_name(8, "foto.HEIC", "heic"))

    def test_hostile_filenames_never_escape_or_collide(self):
        from app.library_export import archive_name

        hostiles = [
            "../../etc/passwd", "..\\..\\windows\\system32\\x.jpg", "/abs/olute.jpg", "C:\\drive.jpg",
            "C:foto.jpg", "nul\x00byte.jpg", "..", ".", "", None, "a" * 400 + ".jpg", "espacio y ñandú.jpg",
            "con/sub/dir.jpg", "trailing.", ".hidden", "emoji😀.jpg",
        ]
        nombres = [archive_name(100 + i, raw, "jpg") for i, raw in enumerate(hostiles)]
        self.assertEqual(len(nombres), len(set(nombres)), "el id del asset evita colisiones")
        for raw, nombre in zip(hostiles, nombres):
            with self.subTest(raw=raw):
                self.assertTrue(nombre.startswith("albumfp-export/originals/"))
                resto = nombre.removeprefix("albumfp-export/originals/")
                self.assertNotIn("/", resto)
                self.assertNotIn("\\", resto)
                self.assertNotIn("..", resto)
                self.assertNotIn(":", resto)
                self.assertNotIn("\x00", resto)
                self.assertRegex(resto, r"^[0-9]+-[A-Za-z0-9._-]+\.jpg$")
                self.assertLessEqual(len(resto), 100)

    def test_the_extension_comes_from_the_verified_format_not_the_client_name(self):
        from app.library_export import archive_name

        self.assertTrue(archive_name(3, "virus.exe", "png").endswith(".png"))
        self.assertTrue(archive_name(4, "video", "mp4").endswith("4-video.mp4"))
        self.assertTrue(archive_name(5, "sin-formato.jpg", None).endswith(".bin"))


class ExportOptionsTests(unittest.TestCase):
    def test_sensitive_groups_are_off_unless_asked_for(self):
        from app.library_export import parse_export_options

        self.assertEqual((False, False, False), tuple(vars(parse_export_options({})).values()))
        opciones = parse_export_options({"exif": "true", "location": "false", "ocr": "1"})
        self.assertEqual((True, False, True), (opciones.exif, opciones.location, opciones.ocr))

    def test_unknown_or_malformed_options_are_refused(self):
        from app.library_export import parse_export_options

        for args in ({"gps": "true"}, {"exif": "yes"}, {"exif": ""}, {"storage_path": "1"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                parse_export_options(args)


class _FakeBackend:
    """materialize() como el local: cede el archivo real. Cuenta aperturas y cierres."""

    def __init__(self, root: Path, missing=()):
        self.root, self.missing = root, set(missing)
        self.opened, self.closed = [], []

    @contextmanager
    def materialize(self, key):
        from app.storage.contracts import ObjectNotFound

        if key in self.missing:
            raise ObjectNotFound(key)
        self.opened.append(key)
        try:
            yield self.root / key
        finally:
            self.closed.append(key)


def _entry(asset_id, key, path):
    from datetime import datetime

    return {"asset_id": asset_id, "storage_path": key, "archive_path": path,
            "created_at": datetime(2026, 9, 1, 10, 0)}


class StreamTests(unittest.TestCase):
    def build(self, sizes, missing=()):
        from app.library_export import stream_export

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        entries = []
        for i, size in enumerate(sizes, start=1):
            key = f"u/1/a/{i}.jpg"
            (root / key).parent.mkdir(parents=True, exist_ok=True)
            (root / key).write_bytes(bytes([i % 251]) * size)
            entries.append(_entry(i, key, f"albumfp-export/originals/{i}-f.jpg"))
        backend = _FakeBackend(root, {f"u/1/a/{i}.jpg" for i in missing})
        manifest = {"schema": "albumfp-export", "schema_version": 1, "assets": []}
        return root, backend, stream_export(entries, manifest, backend)

    def test_the_stream_is_a_valid_zip_with_each_original_exactly_once(self):
        root, backend, stream = self.build([10, 2_500_000, 0])
        data = b"".join(stream)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertIsNone(zf.testzip())
            names = zf.namelist()
            self.assertEqual(["albumfp-export/README.txt", "albumfp-export/metadata/albumfp.json",
                              "albumfp-export/originals/1-f.jpg", "albumfp-export/originals/2-f.jpg",
                              "albumfp-export/originals/3-f.jpg", "albumfp-export/metadata/export-report.json"],
                             names)
            for i in (1, 2, 3):
                self.assertEqual((root / f"u/1/a/{i}.jpg").read_bytes(),
                                 zf.read(f"albumfp-export/originals/{i}-f.jpg"))
            self.assertEqual({"missing_asset_ids": []},
                             json.loads(zf.read("albumfp-export/metadata/export-report.json")))
        self.assertEqual(backend.opened, backend.closed, "cada materialize se libera")

    def test_chunks_stay_bounded_so_a_big_original_is_never_held_in_memory(self):
        from app.library_export import CHUNK_BYTES

        _root, _backend, stream = self.build([9 * CHUNK_BYTES + 17])
        tamanos = [len(chunk) for chunk in stream]
        self.assertGreater(len(tamanos), 9)
        self.assertLessEqual(max(tamanos), 2 * CHUNK_BYTES)

    def test_a_missing_object_is_skipped_and_reported_not_fatal(self):
        _root, _backend, stream = self.build([5, 5, 5], missing=[2])
        with zipfile.ZipFile(io.BytesIO(b"".join(stream))) as zf:
            self.assertNotIn("albumfp-export/originals/2-f.jpg", zf.namelist())
            self.assertEqual({"missing_asset_ids": [2]},
                             json.loads(zf.read("albumfp-export/metadata/export-report.json")))

    def test_a_client_disconnect_releases_the_object_being_copied(self):
        from app.library_export import CHUNK_BYTES

        _root, backend, stream = self.build([6 * CHUNK_BYTES, 6 * CHUNK_BYTES])
        for i, _chunk in enumerate(stream):
            if backend.opened and i > 3:
                break
        stream.close()  # lo que hace el servidor WSGI al cortarse la conexión
        self.assertEqual(backend.opened, backend.closed)


class ModuleShapeTests(unittest.TestCase):
    """El export no puede degenerar en «armar el ZIP en disco y servirlo»."""

    def test_the_export_never_writes_an_archive_file_or_a_workspace(self):
        fuente = (ROOT / "app/library_export.py").read_text(encoding="utf-8")
        for token in ("local_workspace", "send_file", "NamedTemporaryFile", "mkstemp", ".write_bytes(",
                      '"wb"', "'wb'", "password", "token_hash", "session"):
            self.assertNotIn(token, fuente)
        self.assertIn("materialize(", fuente)

    def test_the_route_streams_and_disables_proxy_buffering(self):
        fuente = (ROOT / "app/api/export.py").read_text(encoding="utf-8")
        self.assertIn("X-Accel-Buffering", fuente)
        self.assertIn("no-store", fuente)
        self.assertIn("try_consume(", fuente)
        arbol = ast.parse(fuente)
        nombres = {n.name for n in arbol.body if isinstance(n, ast.FunctionDef)}
        self.assertLessEqual({"export_summary", "download_export"}, nombres)


if __name__ == "__main__":
    unittest.main()
