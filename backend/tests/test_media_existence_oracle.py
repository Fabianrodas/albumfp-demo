"""Fast route-level checks for media lookup privacy without a database."""
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from flask import Flask

from app.api import media as media_api
from app.api import media_context as context_api


class _Mappings:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return _Mappings(self.row)


class MediaExistenceOracleTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def invoke(self, view, path, *, asset, access):
        raw_view = getattr(view, "__wrapped__", view)
        with self.app.test_request_context(path):
            with patch.object(media_api, "db_conn", return_value=nullcontext(object())), \
                 patch.object(context_api, "db_conn", return_value=nullcontext(object())), \
                 patch.object(media_api, "current_user_id", return_value=55), \
                 patch.object(context_api, "current_user_id", return_value=55), \
                 patch.object(media_api, "require_asset_permission", return_value=(asset, access)), \
                 patch.object(media_api, "require_asset_in_album", return_value=(asset, access)), \
                 patch.object(context_api, "require_asset_permission", return_value=(asset, access)):
                result = raw_view(7)
        response, status = result
        return status, response.get_json()

    def test_detail_returns_not_found_for_an_existing_but_inaccessible_asset(self):
        status, payload = self.invoke(
            media_api.get_media_detail, "/api/media/7", asset={"id": 7, "user_id": 99}, access=None
        )
        self.assertEqual(404, status)
        self.assertFalse(payload["ok"])

    def test_context_and_ocr_return_not_found_for_inaccessible_assets(self):
        for view, path in (
            (context_api.get_media_context, "/api/media/7/context"),
            (context_api.get_media_ocr, "/api/media/7/ocr"),
        ):
            with self.subTest(path=path):
                status, _ = self.invoke(view, path, asset={"id": 7, "user_id": 99}, access=None)
                self.assertEqual(404, status)

    def test_file_helper_returns_not_found_for_an_existing_but_inaccessible_asset(self):
        row = {"id": 7, "storage_path": "private/7", "deleted_at": None,
               "file_type": "image", "original_filename": "x.jpg", "mime_type": "image/jpeg",
               "preview_storage_path": None, "preview_mime_type": None}
        with self.app.app_context():
            with patch.object(media_api, "db_conn", return_value=nullcontext(object())), \
                 patch.object(media_api, "execute_safe", return_value=_Result(row)), \
                 patch.object(media_api, "require_asset_permission", return_value=({"id": 7}, None)):
                _, error = media_api._authorized_media_file(7, 55)
        self.assertEqual(404, error[1])


if __name__ == "__main__":
    unittest.main()
