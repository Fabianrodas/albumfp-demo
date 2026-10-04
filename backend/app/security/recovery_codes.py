"""Códigos de recuperación sin email (L14).

Diez códigos de un solo uso por cuenta, cada uno de 30 símbolos base32
(RFC 4648, sin 0/1/8/9) = 150 bits aleatorios de `secrets`, agrupados de 5 en
5 para poder copiarlos a mano. En la base solo vive `hash_token(código)`: con
150 bits no hay diccionario que precomputar, así que el SHA-256 sin sal basta
y no hace falta un secreto nuevo que custodiar (S13 sigue sin resolver dónde
guardarlo).

Ninguna función de aquí registra un código ni abre su propia transacción:
quien llama pasa la conexión de su operación.
"""
import re
import secrets

from ..utils.sql_security import execute_safe
from .sessions import hash_token

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
CODE_COUNT = 10
GROUPS = 6
GROUP_LEN = 5
_CODE_RE = re.compile(rf"^[{ALPHABET}]{{{GROUPS * GROUP_LEN}}}$")


def generate_codes() -> list[str]:
    codes = set()
    while len(codes) < CODE_COUNT:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
        codes.add("-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN)))
    return sorted(codes)


def normalize_code(value) -> str | None:
    """Lo que la persona escribe (minúsculas, espacios, guiones) a la forma
    canónica, o None si no puede ser un código."""
    if not isinstance(value, str):
        return None
    canonical = re.sub(r"[\s-]+", "", value).upper()
    return canonical if _CODE_RE.fullmatch(canonical) else None


def hash_code(value: str) -> str:
    return hash_token(normalize_code(value) or "")


def replace_codes(conn, user_id: int) -> list[str]:
    """Un juego nuevo: todo lo anterior (usado o no) deja de existir."""
    codes = generate_codes()
    execute_safe(conn, "DELETE FROM recovery_codes WHERE user_id = :user_id", {"user_id": user_id})
    execute_safe(
        conn,
        "INSERT INTO recovery_codes (user_id, code_hash) SELECT :user_id, unnest(CAST(:hashes AS TEXT[]))",
        {"user_id": user_id, "hashes": [hash_code(c) for c in codes]},
    )
    return codes


def code_status(conn, user_id: int) -> dict:
    row = execute_safe(conn, """
        SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE used_at IS NULL) AS remaining, MIN(created_at) AS created_at
        FROM recovery_codes WHERE user_id = :user_id
    """, {"user_id": user_id}).mappings().first()
    return {"total": row["total"], "remaining": row["remaining"], "created_at": row["created_at"]}


def delete_all_codes(conn, user_id: int) -> int:
    return execute_safe(conn, "DELETE FROM recovery_codes WHERE user_id = :user_id",
                        {"user_id": user_id}).rowcount or 0


def consume_code(conn, user_id: int, code) -> bool:
    """Gasta UN código de esa cuenta, o nada. Un solo UPDATE condicionado a
    `used_at IS NULL`: si dos peticiones llegan a la vez, la segunda espera el
    bloqueo de fila de la primera y, tras su COMMIT, ya no encuentra la fila
    sin usar. Nunca pueden pasar las dos."""
    canonical = normalize_code(code)
    if canonical is None:
        return False
    return execute_safe(conn, """
        UPDATE recovery_codes SET used_at = NOW()
        WHERE user_id = :user_id AND code_hash = :code_hash AND used_at IS NULL
        RETURNING id
    """, {"user_id": user_id, "code_hash": hash_token(canonical)}).first() is not None
