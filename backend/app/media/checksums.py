"""Exact-content identity without turning hashes into a cross-account oracle."""
from __future__ import annotations

import hashlib
import re

from ..utils.sql_security import execute_safe
from .assets import context_album_join, in_album_sql


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCK_DOMAIN = b"albumfp:exact-media-duplicate:v1\0"


def _validated_sha256(value: str) -> str:
    digest = str(value or "")
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("sha256 debe ser hexadecimal lowercase de 64 caracteres")
    return digest


def duplicate_lock_key(owner_id: int, sha256: str) -> int:
    """Signed BIGINT advisory key stable for one owner+content pair."""
    digest = _validated_sha256(sha256)
    material = _LOCK_DOMAIN + str(int(owner_id)).encode("ascii") + b"\0" + digest.encode("ascii")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)


def lock_exact_duplicate_scope(conn, *, owner_id: int, sha256: str) -> None:
    execute_safe(
        conn,
        "SELECT pg_advisory_xact_lock(:lock_key)",
        {"lock_key": duplicate_lock_key(owner_id, sha256)},
    )


def find_exact_duplicate(
    conn,
    *,
    owner_id: int,
    requester_id: int,
    target_album_id: int,
    sha256: str,
) -> dict | None:
    """Return an active exact match only when the requester may know its id.

    The owner may see matches across their account. A collaborator is limited
    to the destination album, preventing a checksum oracle over the owner's
    other private albums.
    """
    # El album que se informa es el destino si el asset ya esta en el; si no,
    # su pertenencia activa mas antigua (NULL si esta suelto).
    row = execute_safe(
        conn,
        f"""
        SELECT m.id, ctx.album_id
        FROM media_metadata mm
        JOIN assets m ON m.id = mm.media_id
        {context_album_join(prefer_param="target_album_id")}
        WHERE mm.sha256 = :sha256
          AND m.user_id = :owner_id
          AND m.deleted_at IS NULL
          AND (:requester_id = :owner_id OR {in_album_sql("target_album_id")})
        ORDER BY m.id
        LIMIT 1
        """,
        {
            "sha256": _validated_sha256(sha256),
            "owner_id": int(owner_id),
            "requester_id": int(requester_id),
            "target_album_id": int(target_album_id),
        },
    ).mappings().first()
    return {"id": row["id"], "album_id": row["album_id"]} if row else None
