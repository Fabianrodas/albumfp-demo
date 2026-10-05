from __future__ import annotations

import io

import pytest
from PIL import Image
from scripts.seed_synthetic_gallery import read_password_stdin

from app.synthetic_gallery_seed import (
    SYNTHETIC_USERNAME,
    require_open_registration_for_seed,
    synthetic_scene_jpeg,
    validate_configured_target,
    validate_server_identity,
)


def test_seed_identity_accepts_only_the_dedicated_loopback_demo_database():
    validate_server_identity("albumfp_demo", "127.0.0.1", "55432")
    validate_server_identity("albumfp_demo", "::1", "55432")
    validate_server_identity("albumfp_demo", "127.0.0.1/32", "55432")
    validate_server_identity("albumfp_demo", "::1/128", "55432")

    for identity in (
        ("albumfp_demo_test", "127.0.0.1", "55432"),
        ("albumfp_disposable_qa", "127.0.0.1", "55432"),
        ("albumfp_demo", "192.0.2.10", "55432"),
        ("albumfp_demo", "127.0.0.1", "5432"),
    ):
        with pytest.raises(ValueError):
            validate_server_identity(*identity)


def test_configured_target_accepts_only_localhost_and_the_dedicated_demo_database():
    for host in ("localhost", "127.0.0.1", "::1"):
        validate_configured_target("albumfp_demo", host, 55432)

    for identity in (
        ("albumfp_demo_test", "127.0.0.1", 55432),
        ("albumfp_demo", "remote.example", 55432),
        ("albumfp_demo", "127.0.0.1", 5432),
    ):
        with pytest.raises(ValueError):
            validate_configured_target(*identity)


def test_seed_creates_only_exif_free_synthetic_jpegs():
    assert SYNTHETIC_USERNAME == "albumfp_demo_synthetic_gallery"
    for scene in range(4):
        payload = synthetic_scene_jpeg(scene)
        with Image.open(io.BytesIO(payload)) as image:
            assert image.format == "JPEG"
            assert image.size == (1200, 900)
            assert not image.getexif()


def test_seed_rejects_unknown_scene_indexes():
    with pytest.raises(ValueError):
        synthetic_scene_jpeg(999)


def test_cli_reads_repeated_password_from_stdin_without_reformatting_it():
    password = "AlbumFP synthetic gallery local 2026!!"
    assert read_password_stdin(io.StringIO(f"{password}\n{password}\n")) == (password, password)

    with pytest.raises(ValueError):
        read_password_stdin(io.StringIO("only one line\n"))


def test_seed_only_creates_an_account_when_registration_is_explicitly_open():
    require_open_registration_for_seed("open")
    for mode in ("closed", "invite_only", "typo", None):
        with pytest.raises(RuntimeError):
            require_open_registration_for_seed(mode)
