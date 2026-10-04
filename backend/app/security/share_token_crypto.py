"""Cifrado reversible del token de un enlace, para poder volver a mostrarlo.

Distinto de `hash_token()` (sesiones, S01) y del hash de `token_hash` en
`album_shares` (S08): esos son de una sola via a proposito -- ni la propia
app necesita releer una sesion o un token de enlace, solo compararlo. Un
enlace es distinto: el dueno SI necesita poder volver a copiarlo o sacar su
QR sin invalidarlo, y un hash de una sola via nunca permite eso.

`token_hash` (SHA-256) sigue siendo la unica columna que se usa para
RESOLVER un enlace entrante (`get_token_share`) -- no cambia con esto, y
sigue siendo irreversible. `token_encrypted` es una copia APARTE, cifrada con
Fernet (AES-128-CBC autenticado con HMAC-SHA256, nonce aleatorio por
mensaje), que solo se lee cuando el dueno pide expresamente "ver de nuevo".

La clave vive en `SHARE_TOKEN_ENCRYPTION_KEY`, fuera de la base -- un volcado
de la base por si solo no alcanza para descifrar nada, igual criterio que
cualquier otro secreto que vive en el entorno y no en una tabla. Sin la clave configurada, cifrar/descifrar devuelve
`None` en vez de reventar: un enlace se sigue creando igual (con su
`token_hash` de siempre), solo que "ver de nuevo" no esta disponible hasta
que se configure la clave y se regenere ese enlace una vez.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None
_fernet_cargado = False


def _cliente() -> Fernet | None:
    global _fernet, _fernet_cargado
    if not _fernet_cargado:
        clave = (os.getenv("SHARE_TOKEN_ENCRYPTION_KEY") or "").strip()
        _fernet = Fernet(clave.encode()) if clave else None
        _fernet_cargado = True
    return _fernet


def encrypt_share_token(token: str) -> str | None:
    cliente = _cliente()
    if cliente is None:
        return None
    return cliente.encrypt(token.encode()).decode()


def decrypt_share_token(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    cliente = _cliente()
    if cliente is None:
        return None
    try:
        return cliente.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None
