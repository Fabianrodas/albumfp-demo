"""Serve authorized media from the Demo's local filesystem."""

from pathlib import Path

from flask import send_file

from .backends import get_storage_backend
from .contracts import InvalidStorageKey, ObjectNotFound


def deliver_stored_file(storage_key: str, mime_type: str | None = None, download_name: str | None = None):
    """Return a local conditional response, including byte ranges for video."""
    try:
        with get_storage_backend().materialize(storage_key) as path:
            if not Path(path).is_file():
                return None
            return send_file(
                path,
                mimetype=mime_type,
                as_attachment=False,
                download_name=download_name,
                conditional=True,
                etag=True,
                max_age=0,
            )
    except (InvalidStorageKey, ObjectNotFound):
        return None
