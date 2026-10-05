"""L13 contra PostgreSQL de verdad: la exportación portable de la biblioteca.

Reutiliza el arnés de L10A: clon desechable de `ALBUMFP_L10A_TEMPLATE_DB`
migrado a head, app Flask real, sesiones reales y objetos reales en un
`MEDIA_STORAGE_ROOT` temporal. Sin la variable se omite.

Fixtures: OWNER tiene A (900201 también en B, 900202 papelera, 900203
archivada, 900204 video), B (900205), P (900206) e I inactivo (900207);
OTHER tiene X (900208). 900201 trae EXIF con GPS, contexto de lugar, OCR y la
etiqueta `playa-l10a`.
"""
import io
import json
import os
import unittest
import zipfile
from unittest.mock import patch

try:
    from tests.test_asset_membership_db import (
        ALBUM_A, ALBUM_B, ALBUM_I, ALBUM_P, ALBUM_X, COLLAB, OTHER, OWNER, PUBLIC_TOKEN,
        _AppCase, _skip_reason, storage_key,
    )
except ImportError:  # discover -s tests importa los módulos sin el paquete
    from test_asset_membership_db import (
        ALBUM_A, ALBUM_B, ALBUM_I, ALBUM_P, ALBUM_X, COLLAB, OTHER, OWNER, PUBLIC_TOKEN,
        _AppCase, _skip_reason, storage_key,
    )

OWNER_LIVE = {900201, 900203, 900204, 900205, 900206, 900207}   # todo lo suyo salvo la papelera
PREFIX = "albumfp-export/"


@unittest.skipIf(_skip_reason(), _skip_reason())
class LibraryExportTests(_AppCase):
    label = "l13"

    def setUp(self):
        self.addCleanup(self.scratch.execute, "DELETE FROM rate_limit_counters WHERE scope = 'library_export'")

    def export(self, user=OWNER, query=""):
        respuesta = self.call(user, "get", f"/api/export/download{query}")
        if respuesta.status_code != 200:
            self.fail(f"{respuesta.status_code}: {respuesta.get_data(as_text=True)[:300]}")
        archivo = zipfile.ZipFile(io.BytesIO(respuesta.get_data()))
        self.addCleanup(archivo.close)
        return archivo, json.loads(archivo.read(PREFIX + "metadata/albumfp.json"))

    def originals(self, archivo):
        return [n for n in archivo.namelist() if n.startswith(PREFIX + "originals/")]

    # --- contenido -------------------------------------------------------
    def test_each_live_owner_asset_is_exported_once_with_explicit_memberships(self):
        archivo, manifest = self.export()
        self.assertIsNone(archivo.testzip())
        self.assertEqual(("albumfp-export", 1), (manifest["schema"], manifest["schema_version"]))
        self.assertEqual({"username": "l10a_owner", "full_name": "L10A Owner"}, manifest["account"])

        ids = [a["id"] for a in manifest["assets"]]
        self.assertEqual(sorted(OWNER_LIVE), ids, "sin papelera ni assets ajenos, ordenado por id")
        self.assertEqual(len(OWNER_LIVE), len(self.originals(archivo)), "un original por asset, nunca por álbum")

        por_id = {a["id"]: a for a in manifest["assets"]}
        for asset_id, asset in por_id.items():
            with self.subTest(asset=asset_id):
                owner, album = OWNER, {900201: ALBUM_A, 900203: ALBUM_A, 900204: ALBUM_A, 900205: ALBUM_B,
                                       900206: ALBUM_P, 900207: ALBUM_I}[asset_id]
                esperado = (self.media_root / storage_key(asset_id, owner, album)).read_bytes()
                self.assertEqual(esperado, archivo.read(asset["archive_path"]))
                self.assertTrue(asset["archive_path"].startswith(PREFIX + f"originals/{asset_id}-"))

        miembros = sorted((m["album_id"], m["asset_id"]) for m in manifest["memberships"])
        self.assertEqual(sorted((r["album_id"], r["asset_id"]) for r in self.scratch.rows(
            "SELECT album_id, asset_id FROM album_assets WHERE owner_id = :o AND asset_id <> 900202", {"o": OWNER})),
            miembros)
        self.assertIn((ALBUM_A, 900201), miembros)
        self.assertIn((ALBUM_B, 900201), miembros)

        self.assertEqual({ALBUM_A, ALBUM_B, ALBUM_P, ALBUM_I}, {a["id"] for a in manifest["albums"]})
        self.assertEqual(900201, next(a for a in manifest["albums"] if a["id"] == ALBUM_A)["cover_asset_id"])
        self.assertTrue(por_id[900201]["favorite"])
        self.assertIsNotNone(por_id[900203]["archived_at"])
        self.assertEqual(["playa-l10a"], [t["name"] for t in manifest["tags"]])
        self.assertEqual([900301], por_id[900201]["tag_ids"])
        self.assertEqual("video", por_id[900204]["media_type"])
        self.assertEqual({"exif": False, "location": False, "ocr": False}, manifest["options"])
        for sensible in ("exif", "location", "ocr"):
            self.assertNotIn(sensible, por_id[900201])

    def test_nothing_secret_or_internal_leaves_in_the_archive(self):
        archivo, _manifest = self.export(query="?exif=true&location=true&ocr=true")
        texto = b"".join(archivo.read(n) for n in archivo.namelist() if not n.startswith(PREFIX + "originals/"))
        texto = texto.decode("utf-8")
        for secreto in ("storage_path", "preview_storage_path", "password", "token", "csrf", "session",
                        PUBLIC_TOKEN, storage_key(900201, OWNER, ALBUM_A), "l10a_collab", "L10A X", "Ajena"):
            self.assertNotIn(secreto, texto)
        for nombre in archivo.namelist():
            self.assertTrue(nombre.startswith(PREFIX))
            self.assertNotIn("..", nombre)

    def test_sensitive_metadata_travels_only_when_asked_for(self):
        _archivo, manifest = self.export(query="?exif=true&location=true&ocr=true")
        playa = next(a for a in manifest["assets"] if a["id"] == 900201)
        self.assertEqual({"exif": True, "location": True, "ocr": True}, manifest["options"])
        self.assertEqual("L10A Cam", playa["exif"]["camera_make"])
        self.assertEqual("Salinas", playa["location"]["locality"])
        self.assertAlmostEqual(-2.1, playa["location"]["latitude"])
        self.assertEqual("Texto L10A", playa["ocr"]["text"])

        _archivo, solo_exif = self.export(query="?exif=true")
        playa = next(a for a in solo_exif["assets"] if a["id"] == 900201)
        self.assertIn("exif", playa)
        self.assertNotIn("latitude", json.dumps(playa["exif"]), "el GPS va en location, no en exif")
        self.assertNotIn("location", playa)
        self.assertNotIn("ocr", playa)

    def test_an_unassigned_asset_is_exported_without_memberships(self):
        self.scratch.execute("DELETE FROM album_assets WHERE asset_id = 900205")
        self.addCleanup(self.scratch.execute,
                        "INSERT INTO album_assets (album_id, asset_id, owner_id, added_at) VALUES (:b, 900205, :o, NOW())"
                        " ON CONFLICT DO NOTHING; UPDATE albums SET cover_media_id = 900205 WHERE id = :b",
                        {"b": ALBUM_B, "o": OWNER})
        archivo, manifest = self.export()
        self.assertIn(900205, [a["id"] for a in manifest["assets"]])
        self.assertNotIn(900205, [m["asset_id"] for m in manifest["memberships"]])
        self.assertEqual(1, sum(1 for n in self.originals(archivo) if "/900205-" in n))

    def test_smart_album_definitions_travel_as_saved(self):
        self.scratch.execute("INSERT INTO smart_albums (user_id, titulo, filters) VALUES (:o, 'Favoritas', "
                             "'{\"favorite\": true}')", {"o": OWNER})
        self.addCleanup(self.scratch.execute, "DELETE FROM smart_albums")
        _archivo, manifest = self.export()
        self.assertEqual([("Favoritas", {"favorite": True})],
                         [(s["title"], s["filters"]) for s in manifest["smart_albums"]])

    def test_a_missing_object_is_reported_and_the_rest_still_exports(self):
        ruta = self.media_root / storage_key(900205, OWNER, ALBUM_B)
        contenido = ruta.read_bytes()
        ruta.unlink()
        self.addCleanup(ruta.write_bytes, contenido)
        archivo, _manifest = self.export()
        self.assertEqual({"missing_asset_ids": [900205]},
                         json.loads(archivo.read(PREFIX + "metadata/export-report.json")))
        self.assertEqual(len(OWNER_LIVE) - 1, len(self.originals(archivo)))

    # --- frontera --------------------------------------------------------
    def test_each_account_exports_only_its_own_library(self):
        _archivo, otro = self.export(OTHER)
        self.assertEqual([900208], [a["id"] for a in otro["assets"]])
        self.assertEqual([ALBUM_X], [a["id"] for a in otro["albums"]])
        _archivo, colaborador = self.export(COLLAB)
        self.assertEqual(([], []), (colaborador["assets"], colaborador["albums"]),
                         "colaborar en A no mete A en su export")

    def test_the_response_is_a_private_streamed_attachment(self):
        respuesta = self.call(OWNER, "get", "/api/export/download")
        self.assertEqual("application/zip", respuesta.mimetype)
        self.assertTrue(respuesta.is_streamed)
        self.assertIn("attachment", respuesta.headers["Content-Disposition"])
        self.assertIn("albumfp-export-l10a_owner-", respuesta.headers["Content-Disposition"])
        self.assertIn("no-store", respuesta.headers["Cache-Control"])
        respuesta.close()

    def test_no_session_bad_options_and_rate_limit_are_refused_before_streaming(self):
        cliente = self.app.test_client()
        for ruta in ("/api/export/summary", "/api/export/download"):
            self.assertEqual(401, cliente.get(ruta).status_code)
        self.assertEqual(400, self.call(OWNER, "get", "/api/export/download?gps=true").status_code)
        self.assertEqual(400, self.call(OWNER, "get", "/api/export/summary?exif=maybe").status_code)

        with patch.dict(os.environ, {"RATE_LIMIT_EXPORT_PER_HOUR": "2"}):
            resumen = self.call(OWNER, "get", "/api/export/summary").get_json()["data"]
            self.assertEqual((True, 2), (resumen["allowed"], resumen["remaining"]))
            for _ in range(2):
                self.call(OWNER, "get", "/api/export/download").close()
            bloqueada = self.call(OWNER, "get", "/api/export/download")
            self.assertEqual(429, bloqueada.status_code)
            self.assertIn("Retry-After", bloqueada.headers)
            self.assertFalse(self.call(OWNER, "get", "/api/export/summary").get_json()["data"]["allowed"])
            self.assertEqual(200, self.call(OTHER, "get", "/api/export/download").status_code, "el cupo es por cuenta")

    def test_the_summary_counts_without_streaming_anything(self):
        resumen = self.call(OWNER, "get", "/api/export/summary").get_json()["data"]
        self.assertEqual(len(OWNER_LIVE), resumen["assets"])
        self.assertEqual(4, resumen["albums"])
        self.assertEqual(100 * len(OWNER_LIVE), resumen["total_bytes"])
        self.assertEqual(0, self.scratch.scalar("SELECT count(*) FROM rate_limit_counters WHERE scope = 'library_export'"),
                         "mirar el resumen no gasta cupo")

if __name__ == "__main__":
    unittest.main()
