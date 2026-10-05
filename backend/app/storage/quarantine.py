"""Cuarentena de subidas: bytes no confiables nunca tocan el arbol final de
media hasta que pasan validacion y se promueven (fase S05 del roadmap de
seguridad).

Para cuando la ruta HTTP corre, Werkzeug ya recibio la subida completa (eso
lo decide el propio servidor, no este modulo) y la dejo accesible como
`file.stream`. Lo que faltaba era que copiar ESE stream a disco leyera el
archivo entero en memoria o escribiera directo bajo el arbol final antes de
validar nada. `quarantined_upload()` copia por bloques -- nunca carga el
archivo entero en RAM --, calcula el SHA-256 sobre la marcha, y corta y
limpia el archivo parcial en cuanto se supera `max_bytes`, sin dejar nunca un
huerfano si el cliente se desconecta a mitad de la subida.

`MEDIA_QUARANTINE_ROOT` vive FUERA de `MEDIA_STORAGE_ROOT` a proposito: si
algo fallara entre cuarentena y promocion, un archivo en cuarentena nunca
debe volverse indistinguible de uno ya validado y servible. La cuarentena
usa el filesystem local configurado y queda separada del arbol final.

La promocion LEE de cuarentena y nunca mueve: entrega el archivo al backend
seleccionado (`put_from_path`), que decide donde viven los bytes y como se
escriben de forma atomica. En local eso sigue siendo un temporal DENTRO del
directorio final mas `os.replace()` -- lo que hacia esta funcion antes de que
el almacenamiento pasara por el contrato --, y la copia de cuarentena
sobrevive hasta que termina el `with quarantined_upload(...)` completo, que
es lo que permite decidir NO promover sin haber movido nada todavia.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
# Igual patron que _STORAGE_ROOT en media_storage.py: los tests lo sustituyen
# directamente por un directorio temporal.
_QUARANTINE_ROOT: Path | None = None
_CHUNK_SIZE = 1024 * 1024  # 1 MiB: no tan chico que el bucle domine, no tan grande que gaste RAM de mas


@dataclass(frozen=True)
class QuarantinedFile:
    path: Path
    size_bytes: int
    sha256: str
    original_filename: str


def quarantine_root() -> Path:
    if _QUARANTINE_ROOT is not None:
        root = Path(_QUARANTINE_ROOT).expanduser().resolve()
    else:
        configured = (os.getenv("MEDIA_QUARANTINE_ROOT") or "storage/quarantine").strip()
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = _BACKEND_ROOT / candidate
        root = candidate.resolve()

    from .backends import storage_backend_mode

    storage_backend_mode()
    from .media_storage import storage_root

    final_root = storage_root()
    if root == final_root or final_root in root.parents or root in final_root.parents:
        raise RuntimeError("MEDIA_QUARANTINE_ROOT no puede estar dentro de (ni contener a) MEDIA_STORAGE_ROOT")

    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def quarantined_upload(file, *, max_bytes: int = 0):
    """Copia `file.stream` a cuarentena por bloques y entrega un
    `QuarantinedFile`. `max_bytes <= 0` significa sin tope adicional (mismo
    criterio que el resto de la app: los limites de producto ya se
    comprobaron antes de llegar aqui).

    El archivo de cuarentena se borra SIEMPRE al salir del `with` -- lo haya
    promovido quien llama o no -- porque `promote_quarantined()` copia hacia
    el destino final en vez de mover.
    """
    if not file or not getattr(file, "filename", None):
        raise ValueError("Debes seleccionar un archivo")

    from .media_storage import safe_filename

    destino = quarantine_root() / f"{uuid.uuid4().hex}.tmp"
    hasher = hashlib.sha256()
    total = 0
    file.stream.seek(0)
    try:
        with open(destino, "wb") as salida:
            os.chmod(destino, 0o600)  # restrictivo: solo el proceso del backend puede leerlo
            while True:
                bloque = file.stream.read(_CHUNK_SIZE)
                if not bloque:
                    break
                total += len(bloque)
                if max_bytes and total > max_bytes:
                    raise ValueError("El archivo supera el límite de tamaño permitido")
                hasher.update(bloque)
                salida.write(bloque)
        yield QuarantinedFile(
            path=destino,
            size_bytes=total,
            sha256=hasher.hexdigest(),
            original_filename=safe_filename(file.filename),
        )
    finally:
        destino.unlink(missing_ok=True)


def promote_quarantined(quarantined: QuarantinedFile, owner_id: int, target_scope: str, extension: str) -> dict:
    """Persiste un archivo YA VALIDADO bajo una key generada por el servidor.

    Ya no copia ni renombra: donde viven los bytes lo decide el backend
    seleccionado. `target_scope` sigue siendo el `folder o album_<id>` que
    recibe `save_upload` (p.ej. "avatar" o "album_12"), pero ahora pasa por
    `build_object_key()`, que rechaza cualquier cosa que no cumpla la
    gramatica canonica en vez de interpolarla en una ruta.
    """
    from .backends import get_storage_backend
    from .media_storage import ensure_storage_capacity
    from .object_keys import build_object_key

    ensure_storage_capacity(quarantined.size_bytes)
    key = build_object_key(owner_id, target_scope, extension)
    recibo = get_storage_backend().put_from_path(key, quarantined.path)
    return {
        "storage_path": recibo.key,
        "file_size": recibo.size_bytes,
        "sha256": recibo.sha256,
        "original_filename": quarantined.original_filename,
    }
