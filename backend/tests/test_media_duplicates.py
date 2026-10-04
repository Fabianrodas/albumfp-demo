import unittest
from unittest.mock import patch

from app.media.checksums import (
    duplicate_lock_key,
    find_exact_duplicate,
    lock_exact_duplicate_scope,
)


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class ExactDuplicateScopeTests(unittest.TestCase):
    def test_query_is_owner_scoped_active_and_only_returns_visible_media(self):
        calls = []

        def execute(conn, sql, params):
            calls.append((sql, params))
            return _Result({"id": 41, "album_id": 8})

        with patch("app.media.checksums.execute_safe", side_effect=execute):
            found = find_exact_duplicate(
                object(), owner_id=7, requester_id=9, target_album_id=8,
                sha256="a" * 64,
            )

        self.assertEqual({"id": 41, "album_id": 8}, found)
        sql, params = calls[0]
        self.assertIn("m.user_id = :owner_id", sql)
        self.assertIn("m.deleted_at IS NULL", sql)
        self.assertIn(":requester_id = :owner_id", sql)
        self.assertIn("m.album_id = :target_album_id", sql)
        self.assertEqual(7, params["owner_id"])
        self.assertEqual(9, params["requester_id"])
        self.assertEqual(8, params["target_album_id"])
        self.assertEqual("a" * 64, params["sha256"])

    def test_another_owner_can_never_match_the_same_bytes(self):
        captured = {}

        def execute(conn, sql, params):
            captured.update(params)
            return _Result(None)

        with patch("app.media.checksums.execute_safe", side_effect=execute):
            self.assertIsNone(find_exact_duplicate(
                object(), owner_id=22, requester_id=22, target_album_id=3,
                sha256="b" * 64,
            ))
        self.assertEqual(22, captured["owner_id"])


class DuplicateLockTests(unittest.TestCase):
    def test_lock_key_is_stable_signed_and_scoped_by_owner_and_hash(self):
        first = duplicate_lock_key(7, "a" * 64)
        self.assertEqual(first, duplicate_lock_key(7, "a" * 64))
        self.assertNotEqual(first, duplicate_lock_key(8, "a" * 64))
        self.assertNotEqual(first, duplicate_lock_key(7, "b" * 64))
        self.assertGreaterEqual(first, -(2**63))
        self.assertLess(first, 2**63)

    def test_lock_uses_a_bound_parameter(self):
        calls = []
        with patch("app.media.checksums.execute_safe", side_effect=lambda c, s, p: calls.append((s, p))):
            lock_exact_duplicate_scope(object(), owner_id=7, sha256="c" * 64)
        self.assertIn("pg_advisory_xact_lock(:lock_key)", calls[0][0])
        self.assertEqual(duplicate_lock_key(7, "c" * 64), calls[0][1]["lock_key"])


if __name__ == "__main__":
    unittest.main()
