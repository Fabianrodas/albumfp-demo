"""Opaque, stable cursors for the owner's chronological media library."""
import base64
import binascii
import json
import re
from datetime import datetime


DEFAULT_LIBRARY_LIMIT = 60
MAX_LIBRARY_LIMIT = 100
MAX_LIBRARY_CURSOR_LENGTH = 512
_NAIVE_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$"
)


class LibraryCursorError(ValueError):
    """Raised when a client supplies a cursor we did not issue."""


def get_library_limit(raw_value) -> int:
    """Return the API page size, forgiving bad input but enforcing bounds."""
    if isinstance(raw_value, bool):
        return DEFAULT_LIBRARY_LIMIT
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_LIBRARY_LIMIT
    return max(1, min(value, MAX_LIBRARY_LIMIT))


def _validate_cursor_values(effective_date, media_id):
    if not isinstance(effective_date, datetime) or effective_date.tzinfo is not None:
        raise LibraryCursorError("Fecha de cursor invalida")
    if isinstance(media_id, bool) or not isinstance(media_id, int) or media_id <= 0:
        raise LibraryCursorError("Identificador de cursor invalido")


def encode_library_cursor(effective_date: datetime, media_id: int) -> str:
    _validate_cursor_values(effective_date, media_id)
    payload = json.dumps(
        {"d": effective_date.isoformat(), "i": media_id},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_library_cursor(value: str | None):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > MAX_LIBRARY_CURSOR_LENGTH:
        raise LibraryCursorError("Cursor invalido")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(
            (value + padding).encode("ascii"), altchars=b"-_", validate=True
        )
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        raise LibraryCursorError("Cursor invalido") from None
    if not isinstance(payload, dict) or set(payload) != {"d", "i"}:
        raise LibraryCursorError("Cursor invalido")
    raw_date = payload["d"]
    if not isinstance(raw_date, str) or not _NAIVE_ISO_DATETIME.fullmatch(raw_date):
        raise LibraryCursorError("Fecha de cursor invalida")
    try:
        effective_date = datetime.fromisoformat(raw_date)
    except ValueError:
        raise LibraryCursorError("Fecha de cursor invalida") from None
    media_id = payload["i"]
    _validate_cursor_values(effective_date, media_id)
    return effective_date, media_id
