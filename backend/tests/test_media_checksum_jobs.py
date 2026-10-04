import hashlib
import io
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from app.media import checksum_jobs


class Sha256PathTests(unittest.TestCase):
    def test_hashes_the_file_by_streaming(self):
        payload = (b"albumfp-checksum" * 100_000) + b"end"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            path.write_bytes(payload)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), checksum_jobs.sha256_path(path))


class ChecksumJobsTests(unittest.TestCase):
    def test_backfill_defaults_to_report_only(self):
        with patch.object(checksum_jobs, "_count_missing", return_value=3), \
             patch.object(checksum_jobs, "_fetch_media_batch") as fetch, \
             patch.object(checksum_jobs, "_update_checksum") as update, \
             patch.object(checksum_jobs, "get_storage_backend") as backend, \
             redirect_stdout(io.StringIO()) as output:
            result = checksum_jobs.backfill_media_checksums([])

        self.assertEqual(0, result)
        self.assertIn("3", output.getvalue())
        fetch.assert_not_called()
        update.assert_not_called()
        backend.assert_not_called()

    def test_apply_processes_missing_rows_in_resumable_id_batches(self):
        contents = {"one": b"uno", "two": b"dos", "three": b"tres"}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths = {}
        for key, content in contents.items():
            paths[key] = Path(tmp.name) / key
            paths[key].write_bytes(content)

        backend = MagicMock()

        @contextmanager
        def materialize(key):
            yield paths[key]

        backend.materialize.side_effect = materialize
        batches = [
            [{"id": 2, "storage_path": "one"}, {"id": 7, "storage_path": "two"}],
            [{"id": 9, "storage_path": "three"}],
            [],
        ]
        with patch.object(checksum_jobs, "get_storage_backend", return_value=backend), \
             patch.object(checksum_jobs, "_fetch_media_batch", side_effect=batches) as fetch, \
             patch.object(checksum_jobs, "_update_checksum") as update, \
             redirect_stdout(io.StringIO()):
            result = checksum_jobs.backfill_media_checksums(["--apply", "--batch-size=2"])

        self.assertEqual(0, result)
        self.assertEqual([0, 7, 9], [item.kwargs["after_id"] for item in fetch.call_args_list])
        self.assertTrue(all(item.kwargs["missing_only"] for item in fetch.call_args_list))
        self.assertEqual([
            call(2, hashlib.sha256(contents["one"]).hexdigest()),
            call(7, hashlib.sha256(contents["two"]).hexdigest()),
            call(9, hashlib.sha256(contents["three"]).hexdigest()),
        ], update.call_args_list)

    def test_backfill_is_fail_closed_for_unreadable_objects_but_remains_resumable(self):
        backend = MagicMock()
        backend.materialize.side_effect = OSError("origin unavailable")
        with patch.object(checksum_jobs, "get_storage_backend", return_value=backend), \
             patch.object(checksum_jobs, "_fetch_media_batch", side_effect=[
                 [{"id": 4, "storage_path": "missing"}], [],
             ]), \
             patch.object(checksum_jobs, "_update_checksum") as update, \
             redirect_stdout(io.StringIO()):
            result = checksum_jobs.backfill_media_checksums(["--apply"])

        self.assertEqual(1, result)
        update.assert_not_called()

    def test_integrity_returns_nonzero_for_missing_mismatch_and_unreadable(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        good = Path(tmp.name) / "good"
        bad = Path(tmp.name) / "bad"
        good.write_bytes(b"good")
        bad.write_bytes(b"changed")
        expected_good = hashlib.sha256(b"good").hexdigest()
        expected_bad = hashlib.sha256(b"original").hexdigest()
        rows = [
            {"id": 1, "storage_path": "good", "sha256": expected_good},
            {"id": 2, "storage_path": "bad", "sha256": expected_bad},
            {"id": 3, "storage_path": "legacy", "sha256": None},
            {"id": 4, "storage_path": "unreadable", "sha256": expected_good},
        ]
        backend = MagicMock()

        @contextmanager
        def materialize(key):
            if key == "unreadable":
                raise OSError("origin unavailable")
            yield {"good": good, "bad": bad}[key]

        backend.materialize.side_effect = materialize
        with patch.object(checksum_jobs, "get_storage_backend", return_value=backend), \
             patch.object(checksum_jobs, "_fetch_media_batch", side_effect=[rows, []]), \
             redirect_stdout(io.StringIO()) as output:
            result = checksum_jobs.verify_media_integrity(["--batch-size=10"])

        self.assertEqual(1, result)
        self.assertIn("faltante=1", output.getvalue())
        self.assertIn("mismatch=1", output.getvalue())
        self.assertIn("ilegible=1", output.getvalue())

    def test_batch_query_skips_completed_rows_when_backfilling(self):
        result = MagicMock()
        result.mappings.return_value.all.return_value = []
        with patch.object(checksum_jobs, "db_conn") as db, \
             patch.object(checksum_jobs, "execute_safe", return_value=result) as execute:
            checksum_jobs._fetch_media_batch(after_id=10, batch_size=25, missing_only=True)

        sql = execute.call_args.args[1]
        params = execute.call_args.args[2]
        self.assertIn("mm.sha256 IS NULL", sql)
        self.assertIn("m.id > :after_id", sql)
        self.assertIn("ORDER BY m.id", sql)
        self.assertEqual({"after_id": 10, "batch_size": 25}, params)

    def test_update_reports_when_another_worker_already_filled_the_checksum(self):
        results = [MagicMock(rowcount=0), MagicMock(rowcount=1)]
        with patch.object(checksum_jobs, "db_conn"), \
             patch.object(checksum_jobs, "execute_safe", side_effect=results):
            self.assertFalse(checksum_jobs._update_checksum(4, "a" * 64))
            self.assertTrue(checksum_jobs._update_checksum(5, "b" * 64))


if __name__ == "__main__":
    unittest.main()
