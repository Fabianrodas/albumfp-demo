"""Local filesystem storage selection for AlbumFP Demo."""

import os
from pathlib import Path

from .contracts import StorageConfigurationError


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_backend = None


def storage_backend_mode() -> str:
    configured = (os.getenv("MEDIA_STORAGE_BACKEND") or "local").strip().lower()
    if configured != "local":
        raise StorageConfigurationError("AlbumFP Demo supports local filesystem storage only")
    return "local"


def validate_storage_configuration() -> None:
    """Reject non-local modes and media roots that could enter Git."""
    storage_backend_mode()
    from .media_storage import _configured_storage_root

    root = _configured_storage_root()
    if root == _REPOSITORY_ROOT or _REPOSITORY_ROOT in root.parents:
        raise StorageConfigurationError("MEDIA_STORAGE_ROOT must be outside the Demo repository")


def get_storage_backend():
    global _backend
    validate_storage_configuration()
    if _backend is None:
        from .local_backend import LocalStorageBackend

        _backend = LocalStorageBackend()
    return _backend


def set_storage_backend(backend, mode: str = "local") -> None:
    """Inject a local-compatible backend for isolated tests only."""
    global _backend
    if mode != "local":
        raise StorageConfigurationError("AlbumFP Demo supports local filesystem storage only")
    _backend = backend


def reset_storage_backend() -> None:
    global _backend
    _backend = None
