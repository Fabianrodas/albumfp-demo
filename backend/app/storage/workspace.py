"""Directorios temporales gestionados, con presupuesto y leases (spec §11).

Tres raíces disjuntas y con propósitos distintos: `MEDIA_QUARANTINE_ROOT`
para bytes sin validar, `MEDIA_WORK_ROOT` para procesamiento temporal
(spool del multipart, previews aún temporales, materializaciones, JPEG para
proveedores externos, ZIP) y `MEDIA_STATE_ROOT` para registros durables. El
barredor solo limpia la segunda, y para que eso sea cierto las raíces se
comprueban disjuntas al resolverlas: una raíz de trabajo colocada dentro del
árbol final de media convertiría al barredor en un borrador de fotos.

Dos decisiones son el módulo entero:

* **Un lease es un lock del sistema operativo sobre `.lease`, no el archivo
  presente.** El kernel suelta el lock cuando el proceso muere, así que un
  worker matado a la fuerza deja un residuo *reclamable*: el barredor puede
  borrarlo por edad y su reserva vuelve al presupuesto. Con la mera presencia
  del archivo, ese residuo quedaría protegido para siempre.
* **La contabilidad vive en un ledger en disco con lock interproceso, no en
  memoria.** Varios workers y los timers son procesos
  distintos: dos de ellos no pueden creerse dueños del mismo presupuesto.

`WorkspaceBusy` es subclase de `WorkspaceCapacityExceeded` porque agotar las
ranuras no es quedarse sin disco: la spec pide 503 recuperable para lo
primero y 507 para lo segundo. Quien traduzca a HTTP debe capturar
`WorkspaceBusy` ANTES que `WorkspaceCapacityExceeded`.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..utils.env import int_env
from .contracts import StorageConfigurationError, WorkspaceCapacityExceeded

if os.name == "nt":  # pragma: no cover - la otra rama corre en producción
    import msvcrt
else:  # pragma: no cover - esta rama corre en producción, no en este equipo
    import fcntl

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

WORKSPACE_PURPOSES = frozenset({"ingress", "upload-preview", "materialize",
                                "external-copy", "zip", "inventory"})

_LEASE_NAME = ".lease"
_LEDGER_NAME = ".ledger.json"
_LEDGER_LOCK_NAME = ".ledger.lock"
# Spec §11: hasta 2 s esperando una ranura o el ledger, y solo después 503.
_SLOT_WAIT_SECONDS = 2.0
_POLL_SECONDS = 0.01

_ROOT_DEFAULTS = {
    "MEDIA_WORK_ROOT": "storage/work",
    "MEDIA_STATE_ROOT": "storage/state",
    "MEDIA_QUARANTINE_ROOT": "storage/quarantine",
    "MEDIA_STORAGE_ROOT": "storage/media",
}


class WorkspaceBusy(WorkspaceCapacityExceeded):
    """No hay ranura libre ni acceso al ledger: 503 recuperable, no 507."""


# --------------------------------------------------------------------------
# Locks de archivo del sistema operativo
# --------------------------------------------------------------------------

def _try_lock(handle) -> bool:
    """Intenta el lock exclusivo sin bloquear. El SO lo suelta al morir el
    proceso, que es justo lo que hace reclamable un residuo tras un crash."""
    try:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    try:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _open_lock_file(path: Path):
    handle = open(path, "a+b")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return handle


@contextmanager
def _held(path: Path):
    """Sección crítica interproceso. Dos hilos del mismo proceso también
    contienden: cada llamada abre su propio descriptor."""
    limite = time.monotonic() + _SLOT_WAIT_SECONDS
    handle = _open_lock_file(path)
    try:
        while not _try_lock(handle):
            if time.monotonic() >= limite:
                raise WorkspaceBusy("El almacenamiento local está ocupado; reinténtalo en unos segundos")
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def _lease_is_held(directory: Path) -> bool:
    """True si algún proceso vivo tiene el lease de este workspace."""
    lease = directory / _LEASE_NAME
    if not lease.is_file():
        return False
    try:
        # "r+b", nunca "a+b": sondear no puede crear el archivo ni tocar el
        # mtime del residuo, o ningún residuo cumpliría la edad jamás.
        handle = open(lease, "r+b")
    except OSError:
        # Sin poder comprobarlo se trata como activo: nunca se borra algo que
        # no se ha demostrado muerto.
        return True
    try:
        if _try_lock(handle):
            _unlock(handle)
            return False
        return True
    finally:
        handle.close()


# --------------------------------------------------------------------------
# Raíces
# --------------------------------------------------------------------------

def _configured_root(name: str) -> Path:
    configured = (os.getenv(name) or "").strip() or _ROOT_DEFAULTS[name]
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = _BACKEND_ROOT / candidate
    return candidate.resolve()


def _other_roots(name: str) -> dict[str, Path]:
    otras = ["MEDIA_WORK_ROOT", "MEDIA_STATE_ROOT", "MEDIA_QUARANTINE_ROOT"]
    if not (os.getenv("MEDIA_STORAGE_ROOT") or "").strip():
        from .backends import storage_backend_mode

        storage_backend_mode()
    otras.append("MEDIA_STORAGE_ROOT")
    return {otra: _configured_root(otra) for otra in otras if otra != name}


def _root(name: str) -> Path:
    root = _configured_root(name)
    for otro_nombre, otro in _other_roots(name).items():
        if root == otro or otro in root.parents or root in otro.parents:
            raise StorageConfigurationError(
                f"{name} no puede estar dentro de (ni contener a) {otro_nombre}"
            )
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def workspace_root() -> Path:
    """Raíz de los temporales gestionados. Lo único que barre el sweeper."""
    return _root("MEDIA_WORK_ROOT")


def state_root() -> Path:
    """Raíz de los registros durables. El sweeper no la toca nunca."""
    return _root("MEDIA_STATE_ROOT")


# --------------------------------------------------------------------------
# Presupuesto
# --------------------------------------------------------------------------

def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise StorageConfigurationError(f"{name} debe ser numérico") from exc


def _fits(root: Path, used_bytes: int, extra: int) -> bool:
    """Presupuesto agregado y espacio físico proyectado, el menor de los dos."""
    budget = int(_float_env("MEDIA_LOCAL_TEMP_MAX_GB", 0) * 1024**3)
    if budget and used_bytes + extra > budget:
        return False

    min_free = int(_float_env("MEDIA_LOCAL_MIN_FREE_GB", 0) * 1024**3)
    max_usage = _float_env("MEDIA_LOCAL_MAX_USAGE_PERCENT", 100)
    if min_free or max_usage < 100:
        # `0` en el presupuesto significa "sin presupuesto configurado", no
        # "sin guard": el disco real sigue mandando.
        usage = shutil.disk_usage(root)
        free_after = usage.free - extra
        if min_free and free_after < min_free:
            return False
        if max_usage < 100 and (usage.total - free_after) > usage.total * max_usage / 100:
            return False
    return True


# --------------------------------------------------------------------------
# Ledger (todas estas funciones asumen el lock del ledger ya tomado)
# --------------------------------------------------------------------------

def _read_ledger(root: Path) -> dict[str, int]:
    try:
        data = json.loads((root / _LEDGER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, int] = {}
    for key, value in data.items():
        try:
            clean[str(key)] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return clean


def _write_ledger(root: Path, data: dict[str, int]) -> None:
    temporary = root / f".ledger.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, root / _LEDGER_NAME)


def _live_names(root: Path) -> set[str]:
    return {item.name for item in root.iterdir() if item.is_dir() and _lease_is_held(item)}


def _recalculated(root: Path) -> dict[str, int]:
    """Ledger sin las entradas de workspaces que ya no tienen lease vivo."""
    live = _live_names(root)
    return {key: value for key, value in _read_ledger(root).items() if key in live}


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------

@dataclass
class Workspace:
    identifier: str
    directory: Path
    _reserved: int = 0
    # Atributo de clase, no campo: es el descriptor con el lock puesto.
    _lease = None

    @property
    def _root(self) -> Path:
        return self.directory.parent

    def reserve_bytes(self, n: int) -> None:
        """Reserva `n` bytes del presupuesto compartido antes de escribirlos.

        ponytail: cada llamada toma el lock y reescribe el ledger entero. Con
        reservas por bloque de subida eso es un puñado de operaciones de disco
        por MiB; si alguna vez pesa, la salida es reservar en tramos mayores
        desde el llamador, no un contador en memoria que perdería la carrera.
        """
        n = int(n)
        if n <= 0:
            return
        root = self._root
        with _held(root / _LEDGER_LOCK_NAME):
            ledger = _read_ledger(root)
            if not _fits(root, sum(ledger.values()), n):
                # Recalcular cuesta un probe por directorio, así que solo se
                # paga cuando el presupuesto parece agotado: puede estarlo por
                # residuos de un proceso muerto.
                ledger = _recalculated(root)
                if not _fits(root, sum(ledger.values()), n):
                    raise WorkspaceCapacityExceeded(
                        "No hay espacio temporal disponible para completar la operación"
                    )
            ledger[self.identifier] = ledger.get(self.identifier, 0) + n
            _write_ledger(root, ledger)
        self._reserved += n

    def release_bytes(self, n: int) -> None:
        """Devuelve al presupuesto lo que ya no se está ocupando."""
        n = min(max(0, int(n)), self._reserved)
        if n == 0:
            return
        root = self._root
        with _held(root / _LEDGER_LOCK_NAME):
            ledger = _read_ledger(root)
            ledger[self.identifier] = max(0, ledger.get(self.identifier, 0) - n)
            _write_ledger(root, ledger)
        self._reserved -= n

    def _release_lease(self) -> None:
        if self._lease is None:
            return
        _unlock(self._lease)
        self._lease.close()
        self._lease = None


def _create(root: Path, purpose: str) -> Workspace:
    identifier = f"{purpose}-{uuid.uuid4().hex}"
    directory = root / identifier
    directory.mkdir(parents=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    lease = _open_lock_file(directory / _LEASE_NAME)
    if not _try_lock(lease):  # pragma: no cover - imposible en un nombre nuevo
        lease.close()
        shutil.rmtree(directory, ignore_errors=True)
        raise WorkspaceBusy("No se pudo tomar el lease del workspace")
    lease.write(str(os.getpid()).encode("ascii"))
    lease.flush()
    workspace = Workspace(identifier, directory)
    workspace._lease = lease
    return workspace


def _claim(root: Path, purpose: str) -> Workspace:
    maximum = max(1, int_env("MEDIA_WORK_MAX_CONCURRENT", 4))
    deadline = time.monotonic() + _SLOT_WAIT_SECONDS
    while True:
        with _held(root / _LEDGER_LOCK_NAME):
            # Los residuos de un proceso muerto se recuperan bajo lock y el
            # ledger se recalcula ANTES de aceptar trabajo nuevo.
            ledger = _recalculated(root)
            if len(ledger) < maximum:
                workspace = _create(root, purpose)
                ledger[workspace.identifier] = 0
                _write_ledger(root, ledger)
                return workspace
            _write_ledger(root, ledger)
        if time.monotonic() >= deadline:
            raise WorkspaceBusy("Hay demasiadas operaciones de almacenamiento en curso")
        time.sleep(_POLL_SECONDS)


def _dispose(workspace: Workspace) -> None:
    root = workspace._root
    try:
        with _held(root / _LEDGER_LOCK_NAME):
            ledger = _read_ledger(root)
            ledger.pop(workspace.identifier, None)
            _write_ledger(root, ledger)
    except (WorkspaceBusy, OSError):
        # Sin ledger accesible la entrada queda huérfana, y una entrada sin
        # lease vivo la poda la siguiente operación: nunca se pierde el
        # presupuesto, y limpiar los bytes es más urgente que anotarlo.
        pass
    finally:
        # Cerrar antes de borrar: en Windows no se borra un archivo abierto.
        workspace._release_lease()
        shutil.rmtree(workspace.directory, ignore_errors=True)


@contextmanager
def local_workspace(purpose: str):
    """Directorio privado con lease y presupuesto, borrado siempre al salir."""
    if purpose not in WORKSPACE_PURPOSES:
        raise ValueError(f"Propósito de workspace desconocido: {purpose}")
    workspace = _claim(workspace_root(), purpose)
    try:
        yield workspace
    finally:
        _dispose(workspace)


def sweep_workspaces(max_age_hours: int = 24) -> int:
    """Borra residuos de un proceso muerto a la fuerza. Nunca toca un
    workspace con lease vivo, por viejo que sea, ni el árbol de estado."""
    root = workspace_root()
    cutoff = time.time() - max(1, int(max_age_hours)) * 3600
    removed = 0
    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        try:
            if item.stat().st_mtime >= cutoff:
                continue
        except OSError:  # pragma: no cover - desapareció mientras se barría
            continue
        if _lease_is_held(item):
            continue
        shutil.rmtree(item, ignore_errors=True)
        if not item.exists():
            removed += 1
    if removed:
        try:
            with _held(root / _LEDGER_LOCK_NAME):
                _write_ledger(root, _recalculated(root))
        except (WorkspaceBusy, OSError):
            pass
    return removed
