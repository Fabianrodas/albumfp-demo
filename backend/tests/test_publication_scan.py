from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import publication_scan


def test_user_path_scanner_distinguishes_routes_from_home_directories() -> None:
    assert publication_scan.USER_PATH.search("/api/users/alice/avatar") is None
    assert publication_scan.USER_PATH.search("./pages/users/user-profile") is None
    windows_home = "C:" + "\\Users\\" + "alice\\Pictures\\photo.jpg"
    linux_home = "/" + "home/alice/Pictures/photo.jpg"
    macos_home = "/" + "Users/alice/Pictures/photo.jpg"
    assert publication_scan.USER_PATH.search(windows_home)
    assert publication_scan.USER_PATH.search(linux_home)
    assert publication_scan.USER_PATH.search(macos_home)


def test_spec_and_test_files_are_classified_as_tests() -> None:
    assert publication_scan.is_test("frontend/src/app/example.spec.ts")
    assert publication_scan.is_test("frontend/src/app/example.test.ts")
    assert publication_scan.is_test("backend/tests/test_example.py")
    assert not publication_scan.is_test("frontend/src/app/example.ts")


def test_ignored_artifact_matching_does_not_match_env_template() -> None:
    assert publication_scan.is_ignored_path(".env")
    assert not publication_scan.is_ignored_path(".env.example")
    assert publication_scan.is_ignored_path("frontend/dist/main.js")
    assert not publication_scan.is_ignored_path("frontend/src/main.ts")
