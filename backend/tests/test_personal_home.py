"""Contrato del Inicio personal y sus recuerdos históricos."""
import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import home
from app.media.assets import ACTIVE_SCOPE_SQL


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


class HomeDateTests(unittest.TestCase):
    def test_local_date_accepts_real_iso_dates_and_defaults_explicitly(self):
        self.assertEqual(date(2024, 2, 29), home.parse_local_date("2024-02-29"))
        self.assertEqual(date(2026, 9, 19), home.parse_local_date(None, today=date(2026, 9, 19)))

    def test_local_date_rejects_bad_shape_and_impossible_dates(self):
        for value in ("2026-9-19", "2026/09/19", "2026-02-29", "x" * 40):
            with self.subTest(value=value), self.assertRaises(ValueError):
                home.parse_local_date(value)


class PersonalHomeEndpointTests(unittest.TestCase):
    def _call(self, path, results):
        app = Flask(__name__)
        calls = []

        def execute(_conn, sql, params):
            calls.append((sql, dict(params)))
            return _Rows(results[len(calls) - 1])

        with app.test_request_context(path), patch.object(
            home, "current_user_id", return_value=9
        ), patch.object(home, "db_conn", _connection), patch.object(
            home, "execute_safe", side_effect=execute
        ):
            response, status = home.personal_home.__wrapped__()
        return response.get_json(), status, calls

    def test_home_is_only_on_this_day_in_one_owner_scoped_query(self):
        """F02: Inicio ya no duplica Biblioteca, Mis álbumes ni Compartido. Lo
        personal que queda es «En este día»: UNA consulta."""
        payload, status, calls = self._call("/api/home?local_date=2026-09-19", [[]])
        self.assertEqual(200, status)
        self.assertEqual(1, len(calls))
        self.assertEqual({"local_date", "on_this_day"}, set(payload["data"]))
        for gone in ("recent_media", "recent_albums", "shared_albums", "limits"):
            self.assertNotIn(gone, payload["data"])

    def test_returns_all_personal_sections_from_owner_scoped_queries(self):
        memory = {
            "id": 71,
            "user_id": 9,
            "album_id": 4,
            "file_type": "image",
            "title": "Cumpleaños",
            "caption": None,
            "is_favorite": True,
            "taken_at": datetime(2021, 9, 19, 10, 30),
            "created_at": datetime(2021, 9, 20, 8, 0),
            "album_titulo": "Familia",
            "memory_year": 2021,
            "years_ago": 5,
            "year_total": 114,
            "total_count": 127,
        }
        payload, status, calls = self._call("/api/home?local_date=2026-09-19", [[memory]])

        self.assertEqual(200, status)
        self.assertEqual("2026-09-19", payload["data"]["local_date"])
        self.assertEqual(127, payload["data"]["on_this_day"]["total"])
        self.assertEqual(12, payload["data"]["on_this_day"]["per_year_limit"])
        self.assertEqual(71, payload["data"]["on_this_day"]["items"][0]["id"])
        self.assertNotIn("total_count", payload["data"]["on_this_day"]["items"][0])
        self.assertEqual(1, len(calls))

        memory_sql, memory_params = calls[0]
        # L10A: el dueño del asset es el de todos sus albumes (FK compuesta),
        # y el alcance activo sale de sus pertenencias.
        self.assertIn("m.user_id = :user_id", memory_sql)
        self.assertIn(ACTIVE_SCOPE_SQL, memory_sql)
        self.assertIn("m.deleted_at IS NULL", memory_sql)
        self.assertIn("m.taken_at IS NOT NULL", memory_sql)
        self.assertIn("EXTRACT(MONTH FROM m.taken_at) = :local_month", memory_sql)
        self.assertIn("EXTRACT(DAY FROM m.taken_at) = :local_day", memory_sql)
        self.assertIn("EXTRACT(YEAR FROM m.taken_at) < :local_year", memory_sql)
        self.assertIn("ROW_NUMBER() OVER", memory_sql)
        self.assertIn("year_rank <= :per_year_limit", memory_sql)
        self.assertEqual(2026, memory_params["local_year"])
        self.assertEqual(9, memory_params["local_month"])
        self.assertEqual(19, memory_params["local_day"])
        self.assertEqual(12, memory_params["per_year_limit"])

    def test_empty_memories_report_zero_without_hiding_other_sections(self):
        payload, status, _calls = self._call(
            "/api/home?local_date=2026-09-19", [[]]
        )

        self.assertEqual(200, status)
        self.assertEqual(0, payload["data"]["on_this_day"]["total"])
        self.assertEqual([], payload["data"]["on_this_day"]["items"])

    def test_invalid_local_date_returns_before_touching_the_database(self):
        app = Flask(__name__)
        with app.test_request_context("/api/home?local_date=2026-02-29"), patch.object(
            home, "current_user_id", return_value=9
        ), patch.object(home, "execute_safe") as database_query:
            response, status = home.personal_home.__wrapped__()

        self.assertEqual(400, status)
        self.assertFalse(response.get_json()["ok"])
        database_query.assert_not_called()


class HomeProviderIsolationTests(unittest.TestCase):
    """Inicio solo lee datos ya persistidos: ni proveedores, ni red, ni clima."""

    def test_home_module_imports_no_provider_or_network_code(self):
        import ast

        source = Path(home.__file__).read_text(encoding="utf-8")
        modules = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(("." * node.level) + (node.module or ""))
        forbidden = ("integrations", "media.context", "urllib", "requests", "http.client", "socket")
        for module in modules:
            for fragment in forbidden:
                self.assertNotIn(fragment, module, f"home.py importa {module}")
        self.assertNotIn("weather", source.lower())


if __name__ == "__main__":
    unittest.main()
