"""v1.1 portadas de video contra PostgreSQL de verdad.

Arnés de L10A (`test_asset_membership_db.py`): 900204 es un VIDEO del dueño
subido por COLLAB en el álbum A (COLLAB tiene `write` + capacidades en A);
900201 es una foto. La portada se guarda como vista previa del video.
"""
import io
import unittest

from PIL import Image

try:
    from tests.test_asset_membership_db import COLLAB, OTHER, OWNER, STRANGER, _AppCase, _skip_reason
except ImportError:  # discover -s tests importa los módulos sin el paquete
    from test_asset_membership_db import COLLAB, OTHER, OWNER, STRANGER, _AppCase, _skip_reason

VIDEO, PHOTO, SHARE = 900204, 900201, 900401


def jpeg(color=(40, 120, 80), size=(640, 360)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


@unittest.skipIf(_skip_reason(), _skip_reason())
class VideoPosterTests(_AppCase):
    label = "poster"

    def setUp(self):
        self.original = self.scratch.rows(
            "SELECT preview_storage_path, preview_file_size FROM media_metadata WHERE media_id = :v", {"v": VIDEO})[0]
        self.caps = self.scratch.scalar("SELECT capabilities::text FROM album_shares WHERE id = :s", {"s": SHARE})
        self.addCleanup(self.restore)

    def restore(self):
        self.scratch.execute(
            "UPDATE media_metadata SET preview_storage_path = :p, preview_file_size = :s WHERE media_id = :v",
            {"p": self.original["preview_storage_path"], "s": self.original["preview_file_size"], "v": VIDEO})
        self.scratch.execute("UPDATE album_shares SET capabilities = CAST(:c AS text[]) WHERE id = :s",
                             {"c": self.caps, "s": SHARE})
        self.scratch.execute("UPDATE assets SET created_by = :c WHERE id = :v", {"c": COLLAB, "v": VIDEO})

    def put(self, user, media_id=VIDEO, data=None, name="poster.jpg", mime="image/jpeg"):
        payload = {"poster": (io.BytesIO(jpeg() if data is None else data), name, mime)}
        return self.call(user, "put", f"/api/media/{media_id}/poster", data=payload, content_type="multipart/form-data")

    def preview_path(self):
        return self.scratch.scalar("SELECT preview_storage_path FROM media_metadata WHERE media_id = :v", {"v": VIDEO})

    def test_owner_sets_a_poster_that_becomes_the_video_preview(self):
        old = self.preview_path()
        response = self.put(OWNER)
        self.assertEqual(200, response.status_code, response.get_json())
        new = self.preview_path()
        self.assertNotEqual(old, new)
        self.assertTrue(new.endswith(".webp") and new.startswith(f"user_{OWNER}/"))
        self.assertTrue((self.media_root / new).is_file())
        self.assertFalse((self.media_root / old).exists(), "the previous poster object is deleted after commit")
        preview = self.call(OWNER, "get", f"/api/media/{VIDEO}/preview")
        self.assertEqual(200, preview.status_code)
        body = preview.get_data()
        self.assertEqual((b"RIFF", b"WEBP"), (body[:4], body[8:12]), "re-encoded to WebP, never the uploaded bytes")

    def test_only_videos_have_posters(self):
        self.assertEqual(400, self.put(OWNER, media_id=PHOTO).status_code)

    def test_access_matrix(self):
        self.assertEqual(404, self.put(STRANGER).status_code, "no read access: indistinguishable from missing")
        self.assertEqual(404, self.put(OTHER).status_code)
        self.assertEqual(404, self.put(OWNER, media_id=987654321).status_code)
        # The uploader may set the poster of their own video while they can still upload...
        self.scratch.execute("UPDATE album_shares SET capabilities = '{upload}' WHERE id = :s", {"s": SHARE})
        self.assertEqual(200, self.put(COLLAB).status_code)
        # ...but not of someone else's video without edit_media.
        self.scratch.execute("UPDATE assets SET created_by = :o WHERE id = :v", {"o": OWNER, "v": VIDEO})
        self.assertEqual(403, self.put(COLLAB).status_code)
        self.scratch.execute("UPDATE album_shares SET capabilities = '{edit_media}' WHERE id = :s", {"s": SHARE})
        self.assertEqual(200, self.put(COLLAB).status_code)
        # Organize alone never reaches it.
        self.scratch.execute("UPDATE album_shares SET capabilities = '{organize}' WHERE id = :s", {"s": SHARE})
        self.assertEqual(403, self.put(COLLAB).status_code)

    def test_rejects_non_images_and_leaves_no_trace(self):
        before = self.preview_path()
        objects = {p for p in self.media_root.rglob("*") if p.is_file()}
        for data, name, mime in ((b"\xff\xd8\xff" + b"not really a jpeg", "poster.jpg", "image/jpeg"),
                                 (b"\x1aE\xdf\xa3" + b"\0" * 64, "poster.webm", "video/webm"),
                                 (b"GIF89a" + b"\0" * 64, "poster.gif", "image/gif")):
            with self.subTest(name=name):
                self.assertEqual(400, self.put(OWNER, data=data, name=name, mime=mime).status_code)
        self.assertEqual(before, self.preview_path())
        self.assertEqual(objects, {p for p in self.media_root.rglob("*") if p.is_file()})

    def test_requires_csrf(self):
        from app.security.sessions import session_cookie_name

        client = self.app.test_client()
        client.set_cookie(session_cookie_name(), self.sessions[OWNER]["session_token"])
        response = client.put(f"/api/media/{VIDEO}/poster", data={"poster": (io.BytesIO(jpeg()), "p.jpg", "image/jpeg")},
                              content_type="multipart/form-data")
        self.assertEqual(403, response.status_code)

    def test_a_video_without_poster_never_falls_back_to_the_original(self):
        self.scratch.execute("UPDATE media_metadata SET preview_storage_path = NULL WHERE media_id = :v", {"v": VIDEO})
        self.assertEqual(404, self.call(OWNER, "get", f"/api/media/{VIDEO}/preview").status_code)
        # The explicit original still streams, and photos keep their historic fallback.
        self.assertEqual(200, self.call(OWNER, "get", f"/api/media/{VIDEO}/file").status_code)

    def ranged(self, user, path, byte_range):
        from app.security.sessions import session_cookie_name

        client = self.app.test_client()
        if user is not None:
            client.set_cookie(session_cookie_name(), self.sessions[user]["session_token"])
        return client.get(path, headers={"Range": byte_range})

    def test_original_supports_range_requests_for_progressive_playback(self):
        path = f"/api/media/{VIDEO}/file"
        full = self.call(OWNER, "get", path)
        self.assertEqual(200, full.status_code)
        total = len(full.get_data())
        partial = self.ranged(OWNER, path, "bytes=2-5")
        self.assertEqual(206, partial.status_code)
        self.assertEqual(f"bytes 2-5/{total}", partial.headers["Content-Range"])
        self.assertEqual("bytes", partial.headers.get("Accept-Ranges"))
        self.assertEqual(full.get_data()[2:6], partial.get_data())
        # A Range request is authorized exactly like a full one, every time.
        self.assertEqual(403, self.ranged(STRANGER, path, "bytes=0-3").status_code)
        self.assertEqual(401, self.ranged(None, path, "bytes=0-3").status_code)
        # Revoking the collaborator's share takes effect on the very next range.
        self.assertEqual(206, self.ranged(COLLAB, path, "bytes=0-3").status_code)
        self.scratch.execute("UPDATE album_shares SET active = FALSE WHERE id = :s", {"s": SHARE})
        self.addCleanup(self.scratch.execute, "UPDATE album_shares SET active = TRUE WHERE id = :s", {"s": SHARE})
        self.assertEqual(403, self.ranged(COLLAB, path, "bytes=4-7").status_code)

if __name__ == "__main__":
    unittest.main()
