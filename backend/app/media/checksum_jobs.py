"""Operator jobs for legacy checksum backfill and object integrity checks."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..db.db import db_conn
from ..storage.backends import get_storage_backend
from ..utils.sql_security import execute_safe

DEFAULT_BATCH_SIZE = 100
HASH_CHUNK_SIZE = 1024 * 1024


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args(argv, *, allow_apply: bool) -> tuple[bool, int]:
    apply = False
    batch_size = DEFAULT_BATCH_SIZE
    for argument in argv or []:
        if argument == "--apply" and allow_apply:
            apply = True
        elif argument.startswith("--batch-size="):
            try:
                batch_size = int(argument.split("=", 1)[1])
            except ValueError as exc:
                raise ValueError("--batch-size debe ser un entero positivo") from exc
            if batch_size <= 0 or batch_size > 10_000:
                raise ValueError("--batch-size debe estar entre 1 y 10000")
        else:
            raise ValueError(f"argumento no reconocido: {argument}")
    return apply, batch_size


def _count_missing() -> int:
    with db_conn() as conn:
        row = execute_safe(
            conn,
            "SELECT COUNT(*) AS total FROM media_metadata WHERE sha256 IS NULL",
            {},
        ).mappings().first()
    return int(row["total"])


def _fetch_media_batch(*, after_id: int, batch_size: int, missing_only: bool) -> list[dict]:
    missing_clause = "AND mm.sha256 IS NULL" if missing_only else ""
    with db_conn() as conn:
        rows = execute_safe(
            conn,
            f"""
            SELECT m.id, m.storage_path, mm.sha256
            FROM assets m
            JOIN media_metadata mm ON mm.media_id = m.id
            WHERE m.id > :after_id
              {missing_clause}
            ORDER BY m.id
            LIMIT :batch_size
            """,
            {"after_id": after_id, "batch_size": batch_size},
        ).mappings().all()
    return [dict(row) for row in rows]


def _update_checksum(media_id: int, sha256: str) -> bool:
    with db_conn() as conn:
        result = execute_safe(
            conn,
            """
            UPDATE media_metadata
            SET sha256 = :sha256
            WHERE media_id = :media_id AND sha256 IS NULL
            """,
            {"media_id": media_id, "sha256": sha256},
        )
    return result.rowcount == 1


def backfill_media_checksums(argv=None) -> int:
    try:
        apply, batch_size = _parse_args(argv, allow_apply=True)
    except ValueError as exc:
        print(f"Uso: backfill-media-checksums [--apply] [--batch-size=N] ({exc})")
        return 2

    if not apply:
        try:
            missing = _count_missing()
        except Exception as exc:
            print(f"No se pudo contar checksums faltantes: {exc}")
            return 1
        print(f"{missing} media(s) sin SHA-256. Modo informe: no se escribió nada.")
        return 0

    try:
        backend = get_storage_backend()
    except Exception as exc:
        print(f"No se pudo obtener el backend de almacenamiento: {exc}")
        return 1

    after_id = processed = skipped = failed = 0
    while True:
        try:
            rows = _fetch_media_batch(
                after_id=after_id,
                batch_size=batch_size,
                missing_only=True,
            )
        except Exception as exc:
            print(f"No se pudo leer el siguiente lote: {exc}")
            return 1
        if not rows:
            break
        for row in rows:
            after_id = max(after_id, int(row["id"]))
            try:
                with backend.materialize(row["storage_path"]) as path:
                    checksum = sha256_path(path)
                if _update_checksum(int(row["id"]), checksum):
                    processed += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                print(f"  ! media {row['id']}: {type(exc).__name__}")

    print(
        f"Backfill: {processed} actualizado(s), {skipped} ya completado(s), "
        f"{failed} fallido(s)."
    )
    return 1 if failed else 0


def verify_media_integrity(argv=None) -> int:
    try:
        _apply, batch_size = _parse_args(argv, allow_apply=False)
    except ValueError as exc:
        print(f"Uso: verify-media-integrity [--batch-size=N] ({exc})")
        return 2

    try:
        backend = get_storage_backend()
    except Exception as exc:
        print(f"No se pudo obtener el backend de almacenamiento: {exc}")
        return 1

    after_id = checked = missing = mismatched = unreadable = 0
    while True:
        try:
            rows = _fetch_media_batch(
                after_id=after_id,
                batch_size=batch_size,
                missing_only=False,
            )
        except Exception as exc:
            print(f"No se pudo leer el siguiente lote: {exc}")
            return 1
        if not rows:
            break
        for row in rows:
            after_id = max(after_id, int(row["id"]))
            if not row.get("sha256"):
                missing += 1
                continue
            try:
                with backend.materialize(row["storage_path"]) as path:
                    actual = sha256_path(path)
            except Exception as exc:
                unreadable += 1
                print(f"  ! media {row['id']}: {type(exc).__name__}")
                continue
            checked += 1
            if actual != row["sha256"]:
                mismatched += 1

    print(
        "Integridad: "
        f"verificado={checked}, faltante={missing}, mismatch={mismatched}, ilegible={unreadable}."
    )
    return 1 if missing or mismatched or unreadable else 0
