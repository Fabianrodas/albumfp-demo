"""Canonical grammar for relative media object keys."""
from __future__ import annotations

import hashlib
import re
import uuid

from .contracts import InvalidStorageKey

__all__ = [
    "InvalidStorageKey", "validate_storage_key", "key_hash", "build_object_key",
    "ALLOWED_EXTENSIONS", "KEY_MAX_BYTES", "KEY_HASH_LENGTH",
]

KEY_MAX_BYTES = 128
KEY_HASH_LENGTH = 16
ALLOWED_EXTENSIONS = frozenset({
    "jpg", "png", "gif", "webp", "avif", "heic", "heif",
    "mp4", "mov", "webm", "ogg",
})

_KEY_RE = re.compile(
    r"^user_(?P<owner>[1-9][0-9]{0,18})/(?P<scope>album_[1-9][0-9]{0,18}|avatar)/"
    r"(?P<name>[0-9a-f]{32})\.(?P<ext>[a-z0-9]{2,4})$"
)
_SCOPE_RE = re.compile(r"^(album_[1-9][0-9]{0,18}|avatar)$")


def validate_storage_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise InvalidStorageKey("invalid storage key")
    if not key.isascii():
        raise InvalidStorageKey("invalid storage key")
    if len(key) > KEY_MAX_BYTES:
        raise InvalidStorageKey("invalid storage key")
    match = _KEY_RE.fullmatch(key)
    if not match or match.group("ext") not in ALLOWED_EXTENSIONS:
        raise InvalidStorageKey("invalid storage key")
    return key


def key_hash(key) -> str:
    """Return the short, key-free identifier used for correlation logs."""
    material = key if isinstance(key, str) else repr(key)
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:KEY_HASH_LENGTH]


def build_object_key(owner_id: int, scope: str, extension: str) -> str:
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or not 0 < owner_id <= 10**19 - 1:
        raise InvalidStorageKey("invalid owner id")
    if not isinstance(scope, str) or not _SCOPE_RE.fullmatch(scope):
        raise InvalidStorageKey("invalid storage scope")
    if not isinstance(extension, str) or extension not in ALLOWED_EXTENSIONS:
        raise InvalidStorageKey("invalid storage extension")
    return validate_storage_key(f"user_{owner_id}/{scope}/{uuid.uuid4().hex}.{extension}")
