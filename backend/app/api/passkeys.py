"""Passkeys (WebAuthn) — L15.

Toda la verificación la hace `py_webauthn`: aquí no hay criptografía propia.
Lo que sí decide este módulo es la política:

- El RP ID y los orígenes esperados salen de `webauthn_relying_party()`
  (la misma autoridad que CSRF), nunca del navegador. Si no se pueden
  derivar sin ambigüedad, las passkeys responden 503.
- Challenges en servidor: se guarda su SHA-256, viven `CHALLENGE_SECONDS`,
  se consumen con un DELETE ... RETURNING (un solo uso aunque la
  verificación falle) y los de registro quedan atados a la cuenta y a la
  sesión que los pidió.
- Login sin usuario (discoverable): `allowCredentials` vacío, así que pedir
  opciones no revela qué cuentas existen.
- Verificación de usuario OBLIGATORIA (B0.2) en registro y en login: una
  passkey sustituye a la contraseña, así que no basta con tener el
  dispositivo; hace falta la huella, la cara o el PIN. Un autenticador que
  solo acredita presencia se rechaza. El `userHandle` que devuelve el
  autenticador tiene que ser el de la dueña de la credencial.
- Todo fallo del login responde lo mismo (`LOGIN_FAILED`, 400 y no 401: un
  401 haría que el interceptor del frontend tratara a un visitante como una
  sesión caducada). El cupo es el del login por contraseña.
"""
import hmac
import logging
from datetime import datetime, timedelta

from flask import Blueprint, make_response, request
from webauthn import (generate_authentication_options, generate_registration_options,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers import (base64url_to_bytes, options_to_json_dict, parse_authentication_credential_json,
                              parse_client_data_json, parse_registration_credential_json)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, AuthenticatorTransport,
                                      PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                      UserVerificationRequirement)

from ..db.db import db_conn
from ..security.hashing import verify_password
from ..security.rate_limit import try_consume
from ..security.sessions import (attach_session_cookies, create_session, current_session_id, current_user_id,
                                 hash_token, session_required)
from ..utils.origins import webauthn_relying_party
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe
from .auth import _rate_limited, _shape_profile, consume_ip_budget, consume_username_budget

passkeys_bp = Blueprint("passkeys", __name__, url_prefix="/auth/passkeys")

RP_NAME = "AlbumFP"
CHALLENGE_SECONDS = 300
NICKNAME_MAX = 60
LOGIN_FAILED = "No pudimos verificar la passkey."
REGISTER_FAILED = "No pudimos registrar la passkey. Vuelve a intentarlo."
DISABLED = "Las passkeys no están disponibles en este servidor."
TRANSPORTS = {t.value for t in AuthenticatorTransport}


def clean_nickname(value) -> str:
    if not isinstance(value, str):
        raise ValueError("El nombre de la passkey es obligatorio.")
    nickname = value.strip()
    if not nickname or len(nickname) > NICKNAME_MAX:
        raise ValueError(f"El nombre de la passkey debe tener entre 1 y {NICKNAME_MAX} caracteres.")
    if any(not ch.isprintable() for ch in nickname):
        raise ValueError("El nombre de la passkey no puede llevar caracteres de control.")
    return nickname


def user_handle(user_id: int) -> bytes:
    """Identificador estable y sin datos personales: el id interno, nunca el
    username. El login lo compara con la dueña de la credencial."""
    return user_id.to_bytes(8, "big")


def _store_challenge(conn, challenge: bytes, purpose: str, user_id=None, session_id=None) -> None:
    # Limpieza oportunista: cada challenge nuevo se lleva los vencidos, así la
    # tabla no crece aunque nadie corra el comando de purga.
    purge_expired_challenges(conn)
    execute_safe(conn, """
        INSERT INTO webauthn_challenges (challenge_hash, purpose, user_id, session_id, expires_at)
        VALUES (:h, :purpose, :user_id, :session_id, :expires_at)
    """, {"h": hash_token(challenge.hex()), "purpose": purpose, "user_id": user_id, "session_id": session_id,
          "expires_at": datetime.now() + timedelta(seconds=CHALLENGE_SECONDS)})


def _consume_challenge(conn, client_data_b64: str, purpose: str):
    """Gasta el challenge que el navegador firmó. Devuelve (challenge, fila) o
    None. Un challenge vencido o de otro propósito también se gasta."""
    try:
        challenge = parse_client_data_json(base64url_to_bytes(client_data_b64)).challenge
    except Exception:
        return None
    fila = execute_safe(conn, """
        DELETE FROM webauthn_challenges WHERE challenge_hash = :h
        RETURNING purpose, user_id, session_id, expires_at
    """, {"h": hash_token(challenge.hex())}).mappings().first()
    if not fila or fila["purpose"] != purpose or fila["expires_at"] <= datetime.now():
        return None
    return challenge, fila


def purge_expired_challenges(conn) -> int:
    return execute_safe(conn, "DELETE FROM webauthn_challenges WHERE expires_at <= NOW()").rowcount


def _client_data(credential) -> str | None:
    response = credential.get("response") if isinstance(credential, dict) else None
    value = response.get("clientDataJSON") if isinstance(response, dict) else None
    return value if isinstance(value, str) else None


# --- registro (con sesión) ---------------------------------------------------

@passkeys_bp.post("/register/options")
@session_required
def passkey_register_options():
    rp = webauthn_relying_party()
    if not rp:
        return fail(DISABLED, status=503, code="passkeys_disabled")
    payload = request.get_json(silent=True) or {}
    user_id = current_user_id()
    with db_conn() as conn:
        user = execute_safe(conn, "SELECT username, full_name, password_hash FROM users WHERE id = :u",
                            {"u": user_id}).mappings().first()
        # Mismo criterio que los códigos de recuperación: añadir una forma de
        # entrar exige la contraseña, no basta una sesión abierta.
        if not user or not verify_password(str(payload.get("current_password") or ""), user["password_hash"]):
            return fail("La contraseña actual no es correcta.", status=400, code="invalid_password")
        existentes = execute_safe(conn, "SELECT credential_id FROM webauthn_credentials WHERE user_id = :u",
                                  {"u": user_id}).scalars().all()
        opciones = generate_registration_options(
            rp_id=rp[0], rp_name=RP_NAME, user_id=user_handle(user_id), user_name=user["username"],
            user_display_name=user["full_name"] or user["username"], timeout=CHALLENGE_SECONDS * 1000,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=bytes(c)) for c in existentes],
        )
        _store_challenge(conn, opciones.challenge, "registration", user_id, current_session_id())
    return ok(data=options_to_json_dict(opciones))


@passkeys_bp.post("/register/verify")
@session_required
def passkey_register_verify():
    rp = webauthn_relying_party()
    if not rp:
        return fail(DISABLED, status=503, code="passkeys_disabled")
    payload = request.get_json(silent=True) or {}
    try:
        nickname = clean_nickname(payload.get("nickname"))
    except ValueError as exc:
        return fail(str(exc), status=400)
    credential = payload.get("credential")
    client_data = _client_data(credential)
    if not client_data:
        return fail(REGISTER_FAILED, status=400, code="passkey_failed")
    user_id = current_user_id()
    with db_conn() as conn:
        gastado = _consume_challenge(conn, client_data, "registration")
        if not gastado or gastado[1]["user_id"] != user_id or gastado[1]["session_id"] != current_session_id():
            return fail(REGISTER_FAILED, status=400, code="passkey_failed")
        try:
            verificada = verify_registration_response(
                credential=parse_registration_credential_json(credential), expected_challenge=gastado[0],
                expected_rp_id=rp[0], expected_origin=rp[1], require_user_verification=True)
        except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
            logging.info("Registro de passkey rechazado: %s", exc)
            return fail(REGISTER_FAILED, status=400, code="passkey_failed")
        transports = credential["response"].get("transports") or []
        creada = execute_safe(conn, """
            INSERT INTO webauthn_credentials (user_id, credential_id, public_key, sign_count, transports, nickname)
            VALUES (:u, :cid, :pk, :n, :t, :nick)
            ON CONFLICT (credential_id) DO NOTHING
            RETURNING id, nickname, created_at, last_used_at, transports
        """, {"u": user_id, "cid": verificada.credential_id, "pk": verificada.credential_public_key,
              "n": verificada.sign_count, "nick": nickname,
              "t": sorted({t for t in transports if t in TRANSPORTS})}).mappings().first()
        if not creada:
            return fail(REGISTER_FAILED, status=400, code="passkey_failed")
    return ok(data=dict(creada), message="Passkey añadida", status=201)


# --- login (público) ---------------------------------------------------------

@passkeys_bp.post("/login/options")
def passkey_login_options():
    rp = webauthn_relying_party()
    if not rp:
        return fail(DISABLED, status=503, code="passkeys_disabled")
    with db_conn() as conn:
        permitido, retry_after = consume_ip_budget(conn)
        if not permitido:
            return _rate_limited(retry_after)
        opciones = generate_authentication_options(
            rp_id=rp[0], timeout=CHALLENGE_SECONDS * 1000,
            user_verification=UserVerificationRequirement.REQUIRED)
        _store_challenge(conn, opciones.challenge, "authentication")
    return ok(data=options_to_json_dict(opciones))


@passkeys_bp.post("/login/verify")
def passkey_login_verify():
    rp = webauthn_relying_party()
    if not rp:
        return fail(DISABLED, status=503, code="passkeys_disabled")
    payload = request.get_json(silent=True) or {}
    credential = payload.get("credential")
    remember = payload.get("remember") is True
    client_data = _client_data(credential)
    with db_conn() as conn:
        permitido, retry_after = consume_ip_budget(conn)
        if not permitido:
            return _rate_limited(retry_after)
        # Todo lo que sigue gasta el challenge aunque falle: un challenge vale
        # para UN intento, acierte o no.
        gastado = _consume_challenge(conn, client_data, "authentication") if client_data else None
        try:
            parsed = parse_authentication_credential_json(credential) if gastado else None
        except (WebAuthnException, ValueError, TypeError, KeyError):
            parsed = None
        user = parsed and execute_safe(conn, """
            SELECT c.id AS credential_pk, c.public_key, c.sign_count, u.id, u.username, u.full_name,
                   u.avatar_path, u.active
            FROM webauthn_credentials c JOIN users u ON u.id = c.user_id
            WHERE c.credential_id = :cid
        """, {"cid": parsed.raw_id}).mappings().first()
        if not user or not user["active"]:
            return fail(LOGIN_FAILED, status=400, code="passkey_failed")
        permitido, retry_after = consume_username_budget(conn, user["username"])
        if not permitido:
            return _rate_limited(retry_after)
        # Login sin usuario: el userHandle es obligatorio y tiene que ser el de
        # la dueña de la credencial.
        if not parsed.response.user_handle or not hmac.compare_digest(parsed.response.user_handle,
                                                                       user_handle(user["id"])):
            return fail(LOGIN_FAILED, status=400, code="passkey_failed")
        try:
            verificada = verify_authentication_response(
                credential=parsed, expected_challenge=gastado[0], expected_rp_id=rp[0], expected_origin=rp[1],
                credential_public_key=bytes(user["public_key"]),
                credential_current_sign_count=user["sign_count"], require_user_verification=True)
        except WebAuthnException as exc:
            if "sign count" in str(exc):
                # Un contador que retrocede puede ser un autenticador clonado.
                # Se rechaza sin tocar la credencial y queda la señal en el log.
                logging.warning("Passkey %s rechazada: el contador retrocedió (%s)", user["credential_pk"], exc)
            else:
                logging.info("Passkey rechazada: %s", exc)
            return fail(LOGIN_FAILED, status=400, code="passkey_failed")
        execute_safe(conn, """
            UPDATE webauthn_credentials SET sign_count = :n, last_used_at = NOW() WHERE id = :id
        """, {"n": verificada.new_sign_count, "id": user["credential_pk"]})
        execute_safe(conn, "UPDATE users SET last_login = NOW(), updated_at = NOW() WHERE id = :u",
                     {"u": user["id"]})
        # Siempre una sesión NUEVA: nunca se adopta la cookie que trajera el
        # navegador.
        sesion = create_session(conn, user["id"], remember, request.headers.get("User-Agent"))
    cuerpo, status = ok(data={"user": _shape_profile({
        "id": user["id"], "username": user["username"], "full_name": user["full_name"],
        "avatar_path": user["avatar_path"], "active": user["active"],
    })}, message="Login exitoso")
    return attach_session_cookies(make_response(cuerpo, status), sesion)


# --- gestión (con sesión) ----------------------------------------------------

@passkeys_bp.get("")
@session_required
def list_passkeys():
    with db_conn() as conn:
        filas = execute_safe(conn, """
            SELECT id, nickname, created_at, last_used_at, transports FROM webauthn_credentials
            WHERE user_id = :u ORDER BY created_at, id
        """, {"u": current_user_id()}).mappings().all()
    return ok(data=[dict(f) for f in filas])


@passkeys_bp.patch("/<int:passkey_id>")
@session_required
def rename_passkey(passkey_id: int):
    try:
        nickname = clean_nickname((request.get_json(silent=True) or {}).get("nickname"))
    except ValueError as exc:
        return fail(str(exc), status=400)
    with db_conn() as conn:
        cambiada = execute_safe(conn, """
            UPDATE webauthn_credentials SET nickname = :nick WHERE id = :id AND user_id = :u RETURNING id
        """, {"nick": nickname, "id": passkey_id, "u": current_user_id()}).first()
    if not cambiada:
        return fail("Passkey no encontrada", status=404)
    return ok(message="Passkey renombrada")


@passkeys_bp.delete("/<int:passkey_id>")
@session_required
def delete_passkey(passkey_id: int):
    with db_conn() as conn:
        borrada = execute_safe(conn, "DELETE FROM webauthn_credentials WHERE id = :id AND user_id = :u RETURNING id",
                               {"id": passkey_id, "u": current_user_id()}).first()
    if not borrada:
        return fail("Passkey no encontrada", status=404)
    return ok(message="Passkey eliminada")
