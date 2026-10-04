"""Contrato de la biblioteca personal ordenada con cursor estable."""
import base64
import json
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import media
from app.media.assets import ACTIVE_SCOPE_SQL
from app.library_timeline import (
    LibraryCursorError,
    decode_library_cursor,
    encode_library_cursor,
    get_library_limit,
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


@contextmanager
def _connection():
    yield object()


def _raw_cursor(payload):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


class LibraryCursorTests(unittest.TestCase):
    def test_cursor_round_trip_preserves_microseconds_and_id(self):
        effective_date = datetime(2026, 9, 18, 14, 30, 0, 123456)

        encoded = encode_library_cursor(effective_date, 42)

        self.assertNotIn("=", encoded)
        self.assertEqual((effective_date, 42), decode_library_cursor(encoded))

    def test_cursor_rejects_malformed_or_unsafe_values(self):
        invalid = (
            "not-base64!",
            "x" * 513,
            _raw_cursor({"d": "2026-09-18T14:30:00+00:00", "i": 4}),
            _raw_cursor({"d": "2026-09-18T14:30:00", "i": 0}),
            _raw_cursor({"d": "2026-09-18T14:30:00", "i": True}),
            _raw_cursor({"d": "2026-09-18T14:30:00", "i": 4, "extra": 1}),
        )

        for value in invalid:
            with self.subTest(value=value[:40]), self.assertRaises(LibraryCursorError):
                decode_library_cursor(value)

        with self.assertRaises(LibraryCursorError):
            encode_library_cursor(datetime.now(timezone.utc), 1)

    def test_limit_defaults_and_clamps_without_accepting_booleans(self):
        self.assertEqual(60, get_library_limit(None))
        self.assertEqual(60, get_library_limit("abc"))
        self.assertEqual(1, get_library_limit("0"))
        self.assertEqual(100, get_library_limit("1000"))
        self.assertEqual(25, get_library_limit("25"))


class LibraryEndpointTests(unittest.TestCase):
    def test_first_page_is_owner_scoped_and_returns_cursor_from_last_item(self):
        app = Flask(__name__)
        dates = [
            datetime(2026, 9, 18, 10, 0),
            datetime(2026, 9, 17, 10, 0),
            datetime(2026, 9, 16, 10, 0),
        ]
        rows = [
            {
                "id": index,
                "user_id": 9,
                "album_id": 100 + index,
                "file_type": "image",
                "title": f"Foto {index}",
                "caption": None,
                "is_favorite": False,
                "taken_at": date,
                "created_at": date,
                "effective_date": date,
                "album_titulo": f"Album {index}",
            }
            for index, date in zip((30, 20, 10), dates)
        ]
        calls = []

        def execute(_conn, sql, params):
            calls.append((sql, dict(params)))
            return _Rows([{"total": 3}]) if len(calls) == 1 else _Rows(rows)

        with app.test_request_context("/api/media/library?limit=2"), patch.object(
            media, "current_user_id", return_value=9
        ), patch.object(media, "db_conn", _connection), patch.object(
            media, "execute_safe", side_effect=execute
        ):
            response, status = media.list_library_media.__wrapped__()
            payload = response.get_json()

        self.assertEqual(200, status)
        self.assertEqual([30, 20], [item["id"] for item in payload["data"]])
        self.assertEqual(3, payload["meta"]["total"])
        self.assertEqual(2, payload["meta"]["limit"])
        self.assertEqual((dates[1], 20), decode_library_cursor(payload["meta"]["next_cursor"]))
        count_sql, count_params = calls[0]
        page_sql, page_params = calls[1]
        for sql in (count_sql, page_sql):
            # L10A: el dueño del asset es el de todos sus albumes (FK compuesta),
            # y el alcance activo sale de sus pertenencias.
            self.assertIn("m.user_id = :user_id", sql)
            self.assertIn(ACTIVE_SCOPE_SQL, sql)
            self.assertIn("m.deleted_at IS NULL", sql)
        self.assertEqual(9, count_params["user_id"])
        self.assertEqual(9, page_params["user_id"])
        self.assertEqual(3, page_params["limit"])
        self.assertIn("ORDER BY effective_date DESC, m.id DESC", page_sql)

    def test_next_page_uses_both_cursor_keys_and_rejects_bad_cursor_early(self):
        app = Flask(__name__)
        cursor_date = datetime(2026, 9, 17, 10, 0)
        cursor = encode_library_cursor(cursor_date, 20)
        calls = []

        def execute(_conn, sql, params):
            calls.append((sql, dict(params)))
            if len(calls) == 1:
                return _Rows([{"total": 3}])
            return _Rows([])

        with app.test_request_context(f"/api/media/library?cursor={cursor}"), patch.object(
            media, "current_user_id", return_value=9
        ), patch.object(media, "db_conn", _connection), patch.object(
            media, "execute_safe", side_effect=execute
        ):
            response, status = media.list_library_media.__wrapped__()
            payload = response.get_json()

        self.assertEqual(200, status)
        self.assertIsNone(payload["meta"]["next_cursor"])
        page_sql, page_params = calls[1]
        self.assertIn("COALESCE(m.taken_at, m.created_at) < :cursor_date", page_sql)
        self.assertIn("m.id < :cursor_id", page_sql)
        self.assertEqual(cursor_date, page_params["cursor_date"])
        self.assertEqual(20, page_params["cursor_id"])
        self.assertEqual(61, page_params["limit"])

        with app.test_request_context("/api/media/library?cursor=bad!"), patch.object(
            media, "current_user_id", return_value=9
        ), patch.object(media, "db_conn", _connection), patch.object(
            media, "execute_safe"
        ) as database_query:
            response, status = media.list_library_media.__wrapped__()

        self.assertEqual(400, status)
        self.assertFalse(response.get_json()["ok"])
        database_query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
