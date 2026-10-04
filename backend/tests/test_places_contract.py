"""Contrato de portada para que Lugares no descargue videos completos."""
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import places


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


@contextmanager
def _connection():
    yield object()


class PlacesCoverContractTests(unittest.TestCase):
    def test_list_places_returns_the_selected_cover_file_type(self):
        app = Flask(__name__)
        rows = [{
            "country_code": "EC",
            "country_name": "Ecuador",
            "locality": "Quito",
            "region": "Pichincha",
            "media_count": 1,
            "cover_media_id": 8,
            "cover_file_type": "video",
        }]
        sql_seen = []

        def execute(_conn, sql, params):
            sql_seen.append(sql)
            self.assertEqual({"user_id": 9}, params)
            return _Rows(rows)

        with app.test_request_context("/api/places"), patch.object(
            places, "current_user_id", return_value=9
        ), patch.object(places, "db_conn", _connection), patch.object(
            places, "execute_safe", side_effect=execute
        ):
            response, status = places.list_places.__wrapped__()
            payload = response.get_json()

        self.assertEqual(200, status)
        self.assertEqual("video", payload["data"][0]["cover_file_type"])
        self.assertIn(
            "MAX(CASE WHEN rn = 1 THEN file_type END) AS cover_file_type",
            sql_seen[0],
        )


if __name__ == "__main__":
    unittest.main()
