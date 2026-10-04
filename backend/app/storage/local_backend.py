"""Backend de almacenamiento sobre el filesystem local.

Es el comportamiento que la app ya tenia, puesto detras del protocolo de
`contracts.py` (spec seccion 6). Sigue siendo el modo de desarrollo y el que
usan casi todos los tests con `TemporaryDirectory`, asi que `_STORAGE_ROOT`
de `media_storage.py` sigue siendo la costura por la que se sustituye la raiz.

Los imports de `media_storage` son diferidos a proposito: ese modulo pasa a
consultar el backend seleccionado en una fase posterior, y un import de nivel
de modulo en los dos sentidos seria un ciclo.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat as stat_module
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .contracts import (
    InvalidStorageKey,
    ObjectConflict,
    ObjectNotFound,
    StorageIntegrityError,
    ObjectPage,
    ObjectStat,
    PutReceipt,
    StorageCapacity,
)
from .object_keys import validate_storage_key

_MAX_PAGE = 500
# "No existe", en cualquiera de sus formas. El resto de OSError son fallos de
# infraestructura y el contrato exige que se propaguen en vez de confundirse
# con una ausencia.
_MISSING = (FileNotFoundError, NotADirectoryError)


def local_object_version(info: os.stat_result) -> str:
    """Identidad observada del archivo, opaca para quien la recibe.

    No es el checksum del contenido ni un ETag: solo sirve para detectar que
    el objeto de una key dejo de ser el que se habia observado.
    """
    material = f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}"
    return hashlib.sha256(material.encode("ascii")).hexdigest()[:32]


def _sha256_of(path: Path) -> str:
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _offset_from(cursor: str | None) -> int:
    """Traduce el cursor opaco a un offset, o falla dentro del contrato."""
    if not cursor:
        return 0
    # Un ValueError pelado se escaparia de cualquier `except StorageError`, y el
    # GC (fase 26) tiene que poder fallar cerrado en vez de reventar.
    if not cursor.isdigit():
        raise StorageIntegrityError("El cursor de inventario no tiene la forma esperada")
    return int(cursor)


class LocalStorageBackend:
    """Filesystem configurado, con la misma API que el origin remoto."""

    def _absolute(self, relative_key: str) -> Path:
        # La key se valida SIEMPRE antes de resolver nada: ninguna operacion
        # toca el disco con una key que no cumple la gramatica. Despues se
        # reutiliza `resolve_storage_path()`, que conserva la comprobacion de
        # contencion que ya existia (spec seccion 16); el descenso con
        # O_NOFOLLOW es garantia del origin, no de este backend de desarrollo.
        key = validate_storage_key(relative_key)
        from .media_storage import resolve_storage_path

        return resolve_storage_path(key)

    def _stat_at(self, absolute: Path, relative_key: str) -> ObjectStat:
        from .media_storage import canonical_mime_type

        try:
            # `follow_symlinks=False`: un symlink no es un objeto de este
            # almacen, aunque apunte a un archivo real dentro de la raiz.
            info = os.stat(absolute, follow_symlinks=False)
        except _MISSING as exc:
            raise ObjectNotFound(relative_key) from exc
        if not stat_module.S_ISREG(info.st_mode):
            raise ObjectNotFound(relative_key)
        return ObjectStat(
            key=relative_key,
            size_bytes=info.st_size,
            modified_at_ns=info.st_mtime_ns,
            version=local_object_version(info),
            mime_type=canonical_mime_type(relative_key.rsplit(".", 1)[-1]),
        )

    def put_from_path(self, relative_key: str, local_source_path: Path) -> PutReceipt:
        target = self._absolute(relative_key)
        source = Path(local_source_path)
        digest = _sha256_of(source)
        size = source.stat().st_size

        try:
            current = self._stat_at(target, relative_key)
        except ObjectNotFound:
            current = None
        if current is not None:
            # Repeticion identica: `created=False`. Distinta: conflicto. Un
            # objeto ya publicado no se sobrescribe nunca.
            if current.size_bytes != size or _sha256_of(target) != digest:
                raise ObjectConflict(f"La clave ya existe con otro contenido: {relative_key}")
            return PutReceipt(relative_key, size, digest, current.version, created=False)

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            shutil.copyfile(source, temporary)
            os.chmod(temporary, 0o640)
            # Se reabre en lectura/escritura porque `fsync` sobre un
            # descriptor de solo lectura no es portable.
            with open(temporary, "r+b") as written:
                os.fsync(written.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return PutReceipt(
            relative_key, size, digest, self._stat_at(target, relative_key).version, created=True
        )

    @contextmanager
    def materialize(self, relative_key: str) -> Iterator[Path]:
        """Cede el archivo real: en local no hay temporal que limpiar."""
        target = self._absolute(relative_key)
        self._stat_at(target, relative_key)
        yield target

    def exists(self, relative_key: str) -> bool:
        try:
            self.stat(relative_key)
        except ObjectNotFound:
            return False
        return True

    def stat(self, relative_key: str) -> ObjectStat:
        return self._stat_at(self._absolute(relative_key), relative_key)

    def delete(self, relative_key: str, *, expected_version: str | None = None) -> bool:
        target = self._absolute(relative_key)
        try:
            current = self._stat_at(target, relative_key)
        except ObjectNotFound:
            return False
        if expected_version is not None and current.version != expected_version:
            raise ObjectConflict(f"La version del objeto ya no es la esperada: {relative_key}")
        try:
            os.unlink(target)
        except _MISSING:
            return False
        return True

    def capacity(self) -> StorageCapacity:
        from .media_storage import storage_root

        # `storage_root()` ya comprueba MEDIA_EXPECTED_MOUNTPOINT y lanza si
        # el volumen esperado no esta montado: llegar aqui ES la senal de
        # montaje, no se inventa.
        root = storage_root()
        usage = shutil.disk_usage(root)
        return StorageCapacity(
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            writable=os.access(root, os.W_OK),
            mount_ok=True,
            checked_at=time.time(),
        )

    def list_objects(self, *, cursor: str | None = None, limit: int = 500) -> ObjectPage:
        from .media_storage import storage_root

        root = storage_root()
        keys = sorted(
            item.relative_to(root).as_posix()
            for item in root.rglob("*")
            if item.is_file() and not item.name.startswith(".")
        )
        # ponytail: el listado local re-escanea en cada pagina y el cursor es
        # un offset; no es el snapshot inmutable del origin. Basta porque las
        # decisiones destructivas del GC vuelven a comprobar referencia y
        # version antes de borrar. Si el GC local necesitara consistencia
        # entre paginas, aqui va un manifiesto como el del local workstation.
        offset = _offset_from(cursor)
        page = keys[offset:offset + max(1, min(limit, _MAX_PAGE))]
        objects = []
        for key in page:
            try:
                objects.append(self.stat(key))
            except (ObjectNotFound, InvalidStorageKey):
                continue  # residuo o nombre no canonico: no es inventario
        following = offset + len(page)
        complete = following >= len(keys)
        return ObjectPage(
            snapshot_id="local",
            created_at=time.time(),
            objects=objects,
            next_cursor=None if complete else str(following),
            complete=complete,
        )
