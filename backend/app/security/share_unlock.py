"""Sesion anonima minima para un enlace publico con contrasena -- fase S08.

Probar la contrasena una vez y llevarse una cookie evita tener que mandarla
en cada peticion siguiente (o, peor, meterla en la URL de cada `<img>`).
Mismo patron que `user_sessions`/`registration_invites`: la tabla
`share_unlocks` solo guarda sha256(token) de la cookie, nunca su valor --
`hash_token()` es el mismo de `app/security/sessions.py`, reutilizado tal
cual para no tener dos algoritmos de hash de token en la misma app.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from .sessions import _secure_cookies, hash_token
from ..utils.env import int_env
from ..utils.sql_security import execute_safe

_COOKIE_PREFIX = "albumfp_share_"


def unlock_cookie_name(token: str) -> str:
    """El nombre de cookie se deriva del token PUBLICO (ya visible en la
    URL), no de nada secreto -- lo unico que debe ser impredecible es el
    VALOR de la cookie. Derivarlo del token evita que dos enlaces distintos
    abiertos a la vez en el mismo navegador compartan o se pisen el estado
    de desbloqueo entre si."""
    prefijo = "__Host-" if _secure_cookies() else ""
    return f"{prefijo}{_COOKIE_PREFIX}{hash_token(token)[:16]}"


def create_unlock(conn, share_id: int) -> tuple[str, datetime]:
    """Crea la fila y devuelve el token en claro -- la unica vez que existe
    fuera del navegador -- mas su caducidad."""
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=int_env("SHARE_UNLOCK_HOURS", 24))
    execute_safe(
        conn,
        "INSERT INTO share_unlocks (share_id, token_hash, expires_at) VALUES (:share_id, :token_hash, :expires_at)",
        {"share_id": share_id, "token_hash": hash_token(raw), "expires_at": expires_at},
    )
    return raw, expires_at


def is_unlocked(conn, share_id: int, cookie_value: str | None) -> bool:
    if not cookie_value:
        return False
    fila = execute_safe(
        conn,
        "SELECT id FROM share_unlocks WHERE share_id = :share_id AND token_hash = :token_hash AND expires_at > NOW()",
        {"share_id": share_id, "token_hash": hash_token(cookie_value)},
    ).mappings().first()
    return fila is not None


def attach_unlock_cookie(response, token: str, raw_unlock: str, expires_at: datetime):
    response.set_cookie(
        unlock_cookie_name(token),
        raw_unlock,
        httponly=True,
        secure=_secure_cookies(),
        samesite="Strict",
        path="/",
        max_age=int((expires_at - datetime.now()).total_seconds()),
    )
    return response


def purge_expired_unlocks(conn) -> int:
    resultado = execute_safe(conn, "DELETE FROM share_unlocks WHERE expires_at < NOW()", {})
    return resultado.rowcount or 0
