"""Registros durables de operaciones de storage (spec S7).

La persistencia del archivo y el COMMIT de PostgreSQL son dos transacciones
distintas. Esto no promete atomicidad distribuida: deja escrito que keys se
iban a tocar, para que el reconciliador pueda decidir despues comprobando
las referencias reales de la base.

Reutiliza el lock de archivo interproceso que ya establecio Task 6
(`workspace._held`) en vez de reinventar una segunda forma de serializar
entre procesos: varios workers y los timers son procesos
aparte, asi que un lock en memoria no protegeria nada.

El lock vive como hermano de `storage-operations/`, no dentro: la carpeta de
registros solo debe contener registros (`*.json`), nunca artefactos de
control, para que un listado de esa carpeta siga siendo exactamente el
inventario de operaciones.

Fallar cerrado es el contrato completo de este modulo, no un detalle: un
registro que no se puede interpretar (JSON roto, campo ausente, id que no
coincide con el nombre de archivo, version de esquema desconocida) nunca se
descarta en silencio ni se trata como si no tuviera nada pendiente -- eso
dejaria un objeto huerfano o una fila de DB sin compensar y nadie se
enteraria. `pending_operations()` revienta entero antes que omitir uno malo.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import StorageError, WorkspaceCapacityExceeded
from .object_keys import validate_storage_key
from .workspace import _held, state_root

OPERATION_KINDS = frozenset({"upload", "delete"})
_STATES = frozenset({"pending", "committed", "rolled_back"})
_PENDING_STATES = frozenset({"pending", "rolled_back"})
_SCHEMA_VERSION = 1
# Spec S7: "Maximo 100.000 registros o 256 MiB": lo que se alcance primero.
_MAX_RECORDS = 100_000
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_LOCK_NAME = ".storage-operations.lock"
_REQUIRED_FIELDS = ("schema_version", "operation_id", "kind", "keys", "versions", "state", "created_at")


class CompensationLogFull(WorkspaceCapacityExceeded):
    """El registro de operaciones alcanzo su tope: 503 recuperable, no 507.

    No es que falte disco: hay demasiados registros sin reconciliar todavia.
    Ningun registro pendiente se descarta por esto -- solo se rechazan
    mutaciones nuevas hasta que el reconciliador libere espacio. Igual que
    `WorkspaceBusy`, quien traduzca a HTTP debe capturar esto ANTES que el
    `WorkspaceCapacityExceeded` generico (507).
    """


class CorruptOperationRecord(StorageError):
    """Un registro existe pero no se puede interpretar con confianza.

    Nunca se convierte en `None` ni en un registro vacio: quien lo reciba
    debe detenerse, no seguir como si la operacion no tuviera nada pendiente.
    """


def _operations_dir() -> Path:
    directorio = state_root() / "storage-operations"
    directorio.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directorio, 0o700)
    except OSError:
        pass
    return directorio


def _lock_path() -> Path:
    # Hermano de storage-operations/, nunca dentro: esa carpeta solo debe
    # contener registros, para que iterarla sea el inventario exacto.
    return state_root() / _LOCK_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fsync_directory(path: Path) -> None:
    """Fuerza a disco la entrada de directorio tras un rename.

    Sin esto, un crash justo despues de `os.replace()` puede dejar el
    metadato del directorio sin persistir aunque el archivo ya este escrito
    -- ext4/XFS no garantizan que un rename sobreviva a un crash sin este
    fsync. Windows no expone un descriptor de directorio equivalente y
    `os.open()` sobre un directorio ahi devuelve `PermissionError`
    (comprobado en este equipo) en vez de un fd utilizable: no hace falta
    ramificar por `os.name` para saberlo, el propio `OSError` ya lo dice, asi
    que el intento se hace igual en los dos sistemas y se ignora con gracia
    donde no hay nada portable que hacer -- mismo criterio que ya usa este
    modulo para los `os.chmod` que tampoco son criticos ahi.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(destino: Path, contenido: dict) -> None:
    temporal = destino.with_name(f".{destino.name}.{uuid.uuid4().hex}.tmp")
    with open(temporal, "w", encoding="utf-8") as salida:
        json.dump(contenido, salida)
        salida.flush()
        os.fsync(salida.fileno())
    try:
        os.chmod(temporal, 0o600)
    except OSError:
        pass
    os.replace(temporal, destino)
    _fsync_directory(destino.parent)


def _usage(directorio: Path) -> tuple[int, int]:
    """Cantidad de registros y bytes ocupados. O(n) a proposito.

    ponytail: un stat por archivo en cada `record_pending` es aceptable
    hasta el tope de 100.000 (un puñado de ms); si esto llega a pesar de
    verdad, la salida es un contador cacheado junto al ledger, no reinventar
    la contabilidad en memoria que Task 6 ya descarto para el mismo problema.
    """
    total_bytes = 0
    total_archivos = 0
    for archivo in directorio.glob("*.json"):
        try:
            total_bytes += archivo.stat().st_size
        except OSError:
            continue
        total_archivos += 1
    return total_archivos, total_bytes


def _validate_record(data, expected_id: str) -> dict:
    error = CorruptOperationRecord(f"registro de operacion de almacenamiento ilegible: {expected_id}")
    if not isinstance(data, dict) or any(campo not in data for campo in _REQUIRED_FIELDS):
        raise error
    if data.get("schema_version") != _SCHEMA_VERSION:
        raise error
    if data.get("operation_id") != expected_id:
        raise error
    if data.get("kind") not in OPERATION_KINDS:
        raise error
    if data.get("state") not in _STATES:
        raise error
    keys = data.get("keys")
    versions = data.get("versions")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
        raise error
    if not isinstance(versions, list) or len(versions) != len(keys) \
            or not all(v is None or isinstance(v, str) for v in versions):
        raise error
    if not isinstance(data.get("created_at"), str):
        raise error
    return data


def record_pending(kind: str, keys: list[str]) -> str:
    if kind not in OPERATION_KINDS:
        raise ValueError(f"Tipo de operacion de almacenamiento desconocido: {kind}")
    if not keys:
        raise ValueError("Una operacion de almacenamiento necesita al menos una key")
    limpias = [validate_storage_key(k) for k in keys]
    directorio = _operations_dir()
    identificador = uuid.uuid4().hex
    registro = {
        "schema_version": _SCHEMA_VERSION,
        "operation_id": identificador,
        "kind": kind,
        "keys": limpias,
        # Versiones aun no confirmadas: no hay PUT todavia en este punto del
        # ciclo (spec S7 paso 6, "antes del primer PUT").
        "versions": [None] * len(limpias),
        "state": "pending",
        "created_at": _now_iso(),
    }
    with _held(_lock_path()):
        cantidad, ocupados = _usage(directorio)
        if cantidad >= _MAX_RECORDS or ocupados >= _MAX_TOTAL_BYTES:
            raise CompensationLogFull(
                "El registro de operaciones de almacenamiento alcanzo su tope; "
                "reintenta cuando el reconciliador libere espacio"
            )
        _write_atomic(directorio / f"{identificador}.json", registro)
    return identificador


def read_operation(operation_id: str) -> dict | None:
    ruta = _operations_dir() / f"{operation_id}.json"
    try:
        crudo = ruta.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        datos = json.loads(crudo)
    except ValueError as exc:
        raise CorruptOperationRecord(
            f"registro de operacion de almacenamiento ilegible: {operation_id}"
        ) from exc
    return _validate_record(datos, operation_id)


def _set_state(operation_id: str, estado: str) -> None:
    with _held(_lock_path()):
        registro = read_operation(operation_id)
        if registro is None:
            return
        registro["state"] = estado
        registro["updated_at"] = _now_iso()
        _write_atomic(_operations_dir() / f"{operation_id}.json", registro)


def mark_committed(operation_id: str) -> None:
    """Confirmar es BORRAR el registro, no marcarlo y conservarlo.

    Una operacion confirmada ya no tiene nada que compensar: su objeto esta
    escrito y su fila commiteada, y `pending_operations()` solo devuelve
    `pending`/`rolled_back`, asi que el reconciliador jamas volveria a mirarla.
    Conservarla dejaba un JSON por cada subida y cada borrado para siempre,
    hasta agotar el tope del log y bloquear TODAS las operaciones siguientes.
    Un registro corrupto sigue fallando cerrado: `read_operation` lanza antes
    de que aqui se borre nada.
    """
    with _held(_lock_path()):
        if read_operation(operation_id) is None:
            return
        (_operations_dir() / f"{operation_id}.json").unlink(missing_ok=True)


def mark_rolled_back(operation_id: str) -> None:
    _set_state(operation_id, "rolled_back")


def discard_operation(operation_id: str) -> None:
    with _held(_lock_path()):
        (_operations_dir() / f"{operation_id}.json").unlink(missing_ok=True)


def pending_operations() -> list[dict]:
    pendientes = []
    for archivo in sorted(_operations_dir().glob("*.json")):
        try:
            crudo = archivo.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Otro proceso confirmo o descarto justo mientras se listaba:
            # una carrera benigna, no una corrupcion.
            continue
        try:
            datos = json.loads(crudo)
        except ValueError as exc:
            raise CorruptOperationRecord(
                f"registro de operacion de almacenamiento ilegible: {archivo.name}"
            ) from exc
        registro = _validate_record(datos, archivo.stem)
        if registro["state"] in _PENDING_STATES:
            pendientes.append(registro)
    return pendientes
