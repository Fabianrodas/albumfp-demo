"""Protocolo, tipos de resultado y errores del almacenamiento de objetos."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Protocol, runtime_checkable


class StorageError(Exception):
    """Raíz de todo fallo de almacenamiento."""


class InvalidStorageKey(StorageError, ValueError):
    """Key que no cumple la gramática canónica."""


class StorageConfigurationError(StorageError):
    """Configuración ausente, contradictoria o prohibida."""


class ObjectNotFound(StorageError):
    """El objeto no existe; no es un fallo de infraestructura."""


class ObjectConflict(StorageError):
    """La key existe con otro contenido o cambió la versión esperada."""


class StorageUnavailable(StorageError):
    """El almacenamiento local no está disponible o una operación fue rechazada."""


class StorageTimeout(StorageUnavailable):
    """Se agotó un presupuesto de tiempo finito."""


class StorageCapacityExceeded(StorageError):
    """No hay espacio o la política de capacidad lo impide."""


class StorageIntegrityError(StorageError):
    """La respuesta no coincide en longitud, hash, versión o forma."""


class WorkspaceCapacityExceeded(StorageError):
    """El presupuesto de temporales locales está agotado."""


@dataclass(frozen=True)
class ObjectStat:
    key: str
    size_bytes: int
    modified_at_ns: int
    version: str
    mime_type: str


@dataclass(frozen=True)
class PutReceipt:
    key: str
    size_bytes: int
    sha256: str
    version: str
    created: bool


@dataclass(frozen=True)
class StorageCapacity:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    writable: bool
    mount_ok: bool
    checked_at: float


@dataclass(frozen=True)
class ObjectPage:
    snapshot_id: str
    created_at: float
    objects: list[ObjectStat]
    next_cursor: str | None
    complete: bool


@runtime_checkable
class StorageBackend(Protocol):
    def put_from_path(self, relative_key: str, local_source_path: Path) -> PutReceipt: ...

    def materialize(self, relative_key: str) -> ContextManager[Path]: ...

    def exists(self, relative_key: str) -> bool: ...

    def stat(self, relative_key: str) -> ObjectStat: ...

    def delete(self, relative_key: str, *, expected_version: str | None = None) -> bool: ...

    def capacity(self) -> StorageCapacity: ...

    def list_objects(self, *, cursor: str | None = None, limit: int = 500) -> ObjectPage: ...
