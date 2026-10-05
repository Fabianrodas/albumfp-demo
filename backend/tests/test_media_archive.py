"""P09: archivar conserva sin mostrar en el flujo diario; no es la papelera."""
import sys
import unittest

from app.media.assets import ACTIVE_SCOPE_SQL
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import search
from app.api import home, media
from app.media import assets


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


def _call(view, path, results, *, method="GET", json=None, capability=True, readable=False, module=media):
    """Run a view with the DB mocked; return (payload, status, [(sql, params)])."""
    app = Flask(__name__)
    calls = []

    def execute(_conn, sql, params=None):
        calls.append((sql, dict(params or {})))
        return _Rows(results[len(calls) - 1] if len(calls) <= len(results) else [])

    access = ({"role": "owner", "capabilities": {"organize"} if capability else set(), "album": {"user_id": 9},
               "album_id": 3, "owner_id": 9} if capability or readable else None)
    # L10A: las rutas de un asset lo buscan con los helpers de `app.media.assets`
    # (su SQL entra en `calls` con el mismo orden de resultados); solo se simula
    # el calculo de accesos por pertenencia.
    with app.test_request_context(path, method=method, json=json), \
            patch.object(module, "current_user_id", return_value=9), \
            patch.object(module, "db_conn", _connection), \
            patch.object(module, "execute_safe", side_effect=execute), \
            patch.object(assets, "execute_safe", side_effect=execute), \
            patch.object(search, "execute_safe", side_effect=execute), \
            patch.object(assets, "asset_accesses", return_value=[access] if access else []), \
            patch.object(media, "require_asset_capability", wraps=assets.require_asset_capability) as capability_check, \
            patch.object(media, "require_album_capability", return_value=access), \
            patch.object(media, "require_album_permission", return_value=access):
        response, status = view.__wrapped__(*[int(p) for p in path.split("?")[0].split("/") if p.isdigit()])
    return response.get_json(), status, calls, capability_check


class ArchiveEndpointTests(unittest.TestCase):
    def test_archived_must_be_a_boolean_and_is_checked_before_the_database(self):
        for body in ({}, {"archived": "true"}, {"archived": 1}, {"archived": None}):
            with self.subTest(body=body):
                payload, status, calls, _ = _call(media.set_media_archived, "/api/media/5/archive", [], method="PATCH", json=body)
                self.assertEqual(400, status)
                self.assertEqual([], calls)

    def test_trashed_or_missing_media_is_404_because_the_trash_wins(self):
        payload, status, calls, _ = _call(media.set_media_archived, "/api/media/5/archive", [[]], method="PATCH", json={"archived": True})
        self.assertEqual(404, status)
        self.assertIn("deleted_at IS NULL", calls[0][0])
        self.assertEqual(1, len(calls))

    def test_archiving_requires_the_organize_capability(self):
        payload, status, calls, capability_check = _call(
            media.set_media_archived, "/api/media/5/archive", [[{"id": 5, "album_id": 3}]],
            method="PATCH", json={"archived": True}, capability=False, readable=True,
        )
        self.assertEqual(403, status)
        capability_check.assert_called_once()
        self.assertEqual("organize", capability_check.call_args.args[-1])
        self.assertFalse(any(sql.lstrip().upper().startswith("UPDATE") for sql, _ in calls))

    def test_archive_is_idempotent_and_keeps_the_first_archive_date(self):
        archived_at = datetime(2026, 9, 1, 10, 0)
        payload, status, calls, _ = _call(
            media.set_media_archived, "/api/media/5/archive",
            [[{"id": 5, "album_id": 3}], [{"id": 5, "album_id": 3}], [{"id": 5, "album_id": 3, "archived_at": archived_at}]],
            method="PATCH", json={"archived": True},
        )
        self.assertEqual(200, status)
        update_sql = calls[2][0]
        self.assertIn("archived_at = COALESCE(archived_at, NOW())", update_sql)
        self.assertIn("deleted_at IS NULL", update_sql)
        self.assertEqual(5, payload["data"]["id"])
        self.assertIn("archived_at", payload["data"])

    def test_unarchive_clears_the_date(self):
        payload, status, calls, _ = _call(
            media.set_media_archived, "/api/media/5/archive",
            [[{"id": 5, "album_id": 3}], [{"id": 5, "album_id": 3}], [{"id": 5, "album_id": 3, "archived_at": None}]],
            method="PATCH", json={"archived": False},
        )
        self.assertEqual(200, status)
        self.assertIn("archived_at = NULL", calls[2][0])
        self.assertIsNone(payload["data"]["archived_at"])


class TrashInteractionTests(unittest.TestCase):
    def test_restoring_from_trash_never_touches_the_archive_state(self):
        _, status, calls, _ = _call(media.restore_media, "/api/media/5/restore", [[{"id": 5, "album_id": 3}], []], method="POST")
        self.assertEqual(200, status)
        update = [sql for sql, _ in calls if "UPDATE" in sql.upper()]
        self.assertEqual(1, len(update))
        self.assertNotIn("archived_at", update[0])

        _, status, calls, _ = _call(media.restore_all_trash, "/api/trash/restore-all", [[]], method="POST")
        self.assertEqual(200, status)
        self.assertNotIn("archived_at", calls[0][0])


class ViewVisibilityTests(unittest.TestCase):
    def test_library_hides_archived_by_default_and_the_archive_page_shows_only_them(self):
        _, status, calls, _ = _call(media.list_library_media, "/api/media/library", [[{"total": 0}], []])
        self.assertEqual(200, status)
        for sql, _params in calls:
            self.assertIn("m.archived_at IS NULL", sql)
        self.assertIn("m.archived_at", calls[1][0].split("FROM")[0])

        _, status, calls, _ = _call(media.list_library_media, "/api/media/library?archived=only", [[{"total": 0}], []])
        self.assertEqual(200, status)
        for sql, _params in calls:
            self.assertIn("m.archived_at IS NOT NULL", sql)
            self.assertIn(assets.ACTIVE_SCOPE_SQL, sql)
            self.assertIn("m.user_id = :user_id", sql)

    def test_library_rejects_unknown_archive_modes(self):
        for value in ("all", "true", "ONLY", "x" * 50):
            with self.subTest(value=value):
                _, status, calls, _ = _call(media.list_library_media, f"/api/media/library?archived={value}", [])
                self.assertEqual(400, status)
                self.assertEqual([], calls)

    def test_home_memories_and_library_recent_hide_archived(self):
        self.assertIn("m.archived_at IS NULL", home.ON_THIS_DAY_SQL)
        _, status, calls, _ = _call(home.personal_home, "/api/home?local_date=2026-09-19", [[]], module=home)
        self.assertEqual(200, status)
        self.assertIn("m.archived_at IS NULL", calls[0][0])
        # F02: «Añadidos recientemente» vive en Biblioteca, con el MISMO alcance.
        _, status, calls, _ = _call(media.list_recent_library_media, "/api/media/library/recent", [[]])
        self.assertEqual(200, status)
        self.assertEqual(1, len(calls))
        sql = calls[0][0]
        for fragment in ("m.user_id = :user_id", ACTIVE_SCOPE_SQL, "m.deleted_at IS NULL", "m.archived_at IS NULL",
                         "ORDER BY m.created_at DESC, m.id DESC", "LIMIT :limit"):
            self.assertIn(fragment, sql)
        self.assertEqual(8, calls[0][1]["limit"])

    def test_explore_feed_excludes_archived_media(self):
        _, status, calls, _ = _call(media.list_public_media, "/api/media/public", [[{"total": 0}], []])
        self.assertEqual(200, status)
        for sql, _params in calls:
            self.assertIn("m.archived_at IS NULL", sql)

    def test_album_favorites_and_detail_keep_archived_and_expose_the_badge_field(self):
        _, status, calls, _ = _call(media.list_album_media, "/api/albums/3/media", [[{"total": 0}], []])
        self.assertEqual(200, status)
        self.assertNotIn("archived_at IS", calls[0][0])
        self.assertIn("archived_at", calls[1][0])

        _, status, calls, _ = _call(media.list_favorites, "/api/media/favorites", [[{"total": 0}], []])
        self.assertEqual(200, status)
        self.assertNotIn("archived_at IS", calls[0][0])
        self.assertIn("archived_at", calls[1][0])

        _, _, calls, _ = _call(media.get_media_detail, "/api/media/5", [
            [{"id": 5, "user_id": 9, "deleted_at": None}],
            [{"id": 5, "user_id": 9, "archived_at": None}],
        ])
        detalle = [sql for sql, _ in calls if "LEFT JOIN users creator" in sql]
        self.assertEqual(1, len(detalle))
        self.assertIn("m.archived_at", detalle[0])


class SearchArchiveFilterTests(unittest.TestCase):
    def test_archived_filter_is_allowlisted_and_counts_as_a_criterion(self):
        self.assertEqual("only", search.parse_search_filters({"archived": "only"}).archived)
        self.assertEqual("exclude", search.parse_search_filters({"archived": "exclude", "q": "playa"}).archived)
        self.assertIsNone(search.parse_search_filters({"q": "playa"}).archived)
        for value in ("all", "true", "1", "ONLY; DROP TABLE media"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                search.parse_search_filters({"archived": value})

    def test_archived_filter_builds_a_fixed_clause_without_interpolating_input(self):
        only = search.build_media_search(search.parse_search_filters({"archived": "only"}), owner_id=9)
        self.assertIn("m.archived_at IS NOT NULL", only.where_sql)
        exclude = search.build_media_search(search.parse_search_filters({"archived": "exclude", "q": "x"}), owner_id=9)
        self.assertIn("m.archived_at IS NULL", exclude.where_sql)
        default = search.build_media_search(search.parse_search_filters({"q": "x"}), owner_id=9)
        self.assertNotIn("archived_at", default.where_sql)

    def test_search_results_carry_the_badge_field(self):
        _, status, calls, _ = _call(media.search_media, "/api/media/search?q=playa", [[{"total": 0}], []])
        self.assertEqual(200, status)
        self.assertIn("archived_at", calls[1][0])


if __name__ == "__main__":
    unittest.main()
