import logging
import os
from datetime import datetime, timedelta

from flask import Blueprint, make_response, request
from ..db.db import db_conn
from ..media.assets import ACTIVE_SCOPE_SQL
from ..security.hashing import hash_password, verify_password
from ..security.ip_reputation import is_high_risk
from ..security.rate_limit import client_ip, try_consume
from ..security.sessions import (attach_session_cookies, clear_session_cookies, create_session,
                                 current_session_id, current_user_id, hash_token, list_sessions,
                                 revoke_all_sessions, revoke_owned_session, revoke_session,
                                 rotate_session, session_required)
from ..security.password_policy import validate_new_password
from ..security.recovery_codes import code_status, consume_code, delete_all_codes, replace_codes
from ..domain.rules import registration_mode, validate_registration_fields
from ..storage.media_storage import detect_media_signature, remove_stored_file, save_upload, upload_size
from ..storage.compensation import mark_committed, mark_rolled_back, record_pending
from ..storage.contracts import StorageError
from .media import account_storage_used_bytes
from ..utils.env import int_env
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# 0 = sin limite. `MAX_AVATAR_BYTES` (constante) paso a `MAX_AVATAR_MB`
# (variable, S03) por la misma razon que el resto de topes de esta fase: el
# valor por defecto (5) preserva el comportamiento de siempre, configurable
# sin tocar codigo.
DEFAULT_MAX_AVATAR_MB = 5


def _rate_limited(retry_after: int):
    """429 uniforme para login/registro, con `Retry-After` en la respuesta."""
    respuesta, status = fail(
        "Demasiados intentos. Espera un momento antes de volver a intentarlo.",
        status=429, code="rate_limited",
    )
    respuesta.headers["Retry-After"] = str(retry_after)
    return respuesta, status


def _shape_profile(row) -> dict:
    """Perfil tal como lo consume el frontend.

    `avatar_path` nunca sale del servidor: el cliente solo necesita saber si hay
    foto, y la pide por su endpoint. `full_name` cae al username para que
    ninguna vista tenga que manejar el caso nulo.
    """
    data = dict(row)
    data["has_avatar"] = bool(data.pop("avatar_path", None))
    data["full_name"] = data.get("full_name") or data["username"]
    return data


def _load_profile(conn, user_id: int):
    return execute_safe(
        conn,
        """
        SELECT id, username, full_name, avatar_path, active, last_login, created_at, updated_at
        FROM users
        WHERE id = :user_id
        """,
        {"user_id": user_id},
    ).mappings().first()


@auth_bp.post("/register")
def register_user():
    payload = request.get_json(silent=True) or {}
    password = payload.get("password") or ""
    try:
        identity = validate_registration_fields(payload.get("username"), payload.get("full_name"))
    except ValueError as exc:
        return fail(str(exc), status=400)
    username = identity["username"]
    full_name = identity["full_name"]

    if not password:
        return fail("password es obligatorio", status=400)

    valid, error = validate_new_password(password)
    if not valid:
        return fail(error, status=400)

    modo = registration_mode()
    if modo == "closed":
        return fail("El registro de nuevas cuentas está cerrado", status=403, code="registration_closed")

    raw_invite = (payload.get("invite_token") or "").strip() if modo == "invite_only" else None
    if modo == "invite_only" and not raw_invite:
        return fail("Necesitas una invitación para crear una cuenta", status=403, code="registration_invite_required")

    try:
        with db_conn() as conn:
            ip = client_ip()
            limite_registro = int_env("RATE_LIMIT_REGISTER_PER_HOUR", 5)
            if is_high_risk(conn, ip):
                limite_registro = int_env("RATE_LIMIT_REGISTER_PER_HOUR_HIGH_RISK", 1)
                logging.warning("Registro desde IP de mala reputacion (AbuseIPDB): %s", ip)
            permitido, retry_after = try_consume(conn, "register", f"ip:{ip}", limite_registro, 3600)
            if not permitido:
                return _rate_limited(retry_after)

            # La invitacion se busca (sin consumir) ANTES de escribir nada:
            # si no es valida, no debe quedar ningun rastro de intento.
            invite_id = None
            if modo == "invite_only":
                invite = execute_safe(
                    conn,
                    """
                    SELECT id FROM registration_invites
                    WHERE token_hash = :token_hash AND used_at IS NULL AND expires_at > NOW()
                    """,
                    {"token_hash": hash_token(raw_invite)},
                ).mappings().first()
                if not invite:
                    return fail("La invitación no es válida o ya expiró", status=403, code="registration_invite_invalid")
                invite_id = invite["id"]

            exists = execute_safe(
                conn,
                "SELECT id FROM users WHERE username = :username LIMIT 1",
                {"username": username},
            ).first()
            if exists:
                return fail("El nombre de usuario ya existe", status=409)

            created = execute_safe(
                conn,
                """
                INSERT INTO users (username, full_name, password_hash, active, created_by)
                VALUES (:username, :full_name, :password_hash, TRUE, NULL)
                RETURNING id, username, full_name, avatar_path, active, created_at
                """,
                {"username": username, "full_name": full_name, "password_hash": hash_password(password)},
            ).mappings().first()

            if invite_id is not None:
                # UPDATE atomico con el mismo `used_at IS NULL` que ya
                # comprobo el SELECT de arriba: si dos registros a la vez
                # llegaron con el MISMO token, solo uno gana esta fila. El
                # perdedor lanza y con el se deshace TODA la transaccion
                # (incluido el usuario que acababa de crear) -- no hay forma
                # de que una invitacion de un solo uso sirva para dos cuentas.
                consumida = execute_safe(
                    conn,
                    """
                    UPDATE registration_invites
                    SET used_at = NOW(), used_by_user_id = :user_id
                    WHERE id = :invite_id AND used_at IS NULL
                    RETURNING id
                    """,
                    {"invite_id": invite_id, "user_id": created["id"]},
                ).first()
                if consumida is None:
                    raise ValueError("La invitación acaba de ser utilizada por otra persona")
    except ValueError as exc:
        return fail(str(exc), status=409)

    return ok(data=_shape_profile(created), message="Usuario registrado", status=201)


def _consume_login_budget(conn, username: str) -> tuple[bool, int]:
    """El cupo de entrada, compartido por el login y la recuperación con
    código (L14): quien no puede probar contraseñas tampoco puede probar
    códigos. Por username Y por IP -- las dos deben tener cupo, no basta con
    una. Solo por username dejaria que un atacante con muchas IPs siguiera
    probando sin limite; solo por IP dejaria que probara muchas cuentas
    distintas desde la misma IP sin que ninguna se agotara nunca. Se cuenta
    CADA intento, acierte o falle: si solo se contaran los fallidos, el limite
    mismo delataria si un intento acerto o no."""
    permitido_usuario, retry_usuario = consume_username_budget(conn, username)
    permitido_ip, retry_ip = consume_ip_budget(conn)
    return permitido_usuario and permitido_ip, max(retry_usuario, retry_ip)


def consume_username_budget(conn, username: str) -> tuple[bool, int]:
    return try_consume(conn, "login_username", username.lower(),
                       int_env("RATE_LIMIT_LOGIN_PER_15_MIN", 10), 900)


def consume_ip_budget(conn) -> tuple[bool, int]:
    """El cupo de entrada por IP; lo comparten login, recuperación y las dos
    mitades de la ceremonia de passkey (L15).

    La reputacion es una propiedad de la IP, no de la cuenta atacada: solo
    achica el cupo del bucket "login_ip", nunca el de "login_username" -- de
    lo contrario una IP de mala fama podria agotarle el cupo a una cuenta
    legitima consultada desde otra parte."""
    ip = client_ip()
    limite_ip = int_env("RATE_LIMIT_LOGIN_PER_15_MIN", 10)
    if is_high_risk(conn, ip):
        limite_ip = int_env("RATE_LIMIT_LOGIN_PER_15_MIN_HIGH_RISK", 3)
        logging.warning("Entrada desde IP de mala reputacion (AbuseIPDB): %s", ip)
    return try_consume(conn, "login_ip", ip, limite_ip, 900)


@auth_bp.post("/login")
def login_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    # "Recuerdame" ya no decide en que almacen del navegador se guarda nada:
    # decide cuanto vive la sesion en el servidor y si la cookie sobrevive a
    # cerrar el navegador.
    remember = payload.get("remember") is True

    if not username or not password:
        return fail("Credenciales incompletas", status=400)

    with db_conn() as conn:
        permitido, retry_after = _consume_login_budget(conn, username)
        if not permitido:
            return _rate_limited(retry_after)

        user = execute_safe(
            conn,
            """
            SELECT id, username, full_name, avatar_path, password_hash, active
            FROM users
            WHERE username = :username
            LIMIT 1
            """,
            {"username": username},
        ).mappings().first()

        if not user or not user["active"]:
            return fail("Credenciales inválidas", status=401)

        if not verify_password(password, user["password_hash"]):
            return fail("Credenciales inválidas", status=401)

        execute_safe(
            conn,
            """
            UPDATE users
            SET last_login = NOW(), updated_at = NOW(), updated_by = :user_id
            WHERE id = :user_id
            """,
            {"user_id": user["id"]},
        )

        sesion = create_session(conn, user["id"], remember, request.headers.get("User-Agent"))

    # El cuerpo NO lleva ninguna credencial: la sesion viaja en una cookie
    # HttpOnly que el JavaScript de la app no puede leer. Lo unico que se
    # devuelve es el perfil, que la app necesita para pintar.
    cuerpo, status = ok(
        data={
            "user": _shape_profile({
                "id": user["id"],
                "username": user["username"],
                "full_name": user["full_name"],
                "avatar_path": user["avatar_path"],
                "active": user["active"],
            }),
        },
        message="Login exitoso",
    )
    return attach_session_cookies(make_response(cuerpo, status), sesion)


@auth_bp.post("/logout")
@session_required
def logout_user():
    """Revoca ESTA sesion. A diferencia del logout con JWT, que solo borraba la
    copia del navegador, aqui la credencial deja de valer en el servidor."""
    with db_conn() as conn:
        revoke_session(conn, current_session_id())
    cuerpo, status = ok(message="Sesión cerrada")
    return clear_session_cookies(make_response(cuerpo, status))


@auth_bp.post("/logout-all")
@session_required
def logout_all_sessions():
    """Echa a todos los dispositivos, este incluido. Es lo que se usa cuando
    sospechas que alguien mas entro."""
    with db_conn() as conn:
        revocadas = revoke_all_sessions(conn, current_user_id())
    cuerpo, status = ok(data={"revoked": revocadas}, message="Todas las sesiones cerradas")
    return clear_session_cookies(make_response(cuerpo, status))


def _shape_session(row, current_id: int) -> dict:
    """NUNCA incluye `token_hash` ni `csrf_hash`: son secretos, y esta fila
    la ve el propio dueño en su pantalla de sesiones, no un log ni un panel
    interno."""
    data = dict(row)
    data["current"] = data["id"] == current_id
    return data


@auth_bp.get("/sessions")
@session_required
def list_my_sessions():
    user_id = current_user_id()
    actual = current_session_id()
    with db_conn() as conn:
        filas = list_sessions(conn, user_id)
    return ok(data=[_shape_session(f, actual) for f in filas], message="Sesiones activas")


@auth_bp.delete("/sessions/<int:session_id>")
@session_required
def revoke_my_session(session_id: int):
    """Cierra UNA sesion propia. Si es la sesion desde la que se llama, no
    basta con revocarla en la base: hay que limpiar tambien la cookie de este
    navegador, o la siguiente peticion daria un 401 confuso en vez de un
    cierre de sesion limpio."""
    user_id = current_user_id()
    era_la_actual = session_id == current_session_id()
    with db_conn() as conn:
        revocada = revoke_owned_session(conn, user_id, session_id)
    if not revocada:
        return fail("Sesión no encontrada", status=404)
    cuerpo, status = ok(message="Sesión cerrada")
    respuesta = make_response(cuerpo, status)
    if era_la_actual:
        respuesta = clear_session_cookies(respuesta)
    return respuesta


@auth_bp.post("/sessions/revoke-others")
@session_required
def revoke_other_sessions():
    """A diferencia de `/auth/logout-all`, esta NO te saca a ti: es el botón
    "cerrar sesión en los demás dispositivos" -- sigues dentro desde este."""
    user_id = current_user_id()
    with db_conn() as conn:
        revocadas = revoke_all_sessions(conn, user_id, keep_session_id=current_session_id())
    return ok(data={"revoked": revocadas}, message="Sesiones cerradas en los demás dispositivos")


@auth_bp.get("/me")
@session_required
def me():
    user_id = current_user_id()
    with db_conn() as conn:
        user = _load_profile(conn, user_id)

    if not user:
        return fail("Usuario no encontrado", status=404)

    return ok(data=_shape_profile(user), message="Perfil")


@auth_bp.patch("/me")
@session_required
def update_me():
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}

    with db_conn() as conn:
        current = _load_profile(conn, user_id)
        if not current:
            return fail("Usuario no encontrado", status=404)

        username = payload.get("username", current["username"])
        full_name = payload.get("full_name", current["full_name"])
        try:
            identity = validate_registration_fields(username, full_name)
        except ValueError as exc:
            return fail(str(exc), status=400)

        taken = execute_safe(
            conn,
            "SELECT id FROM users WHERE username = :username AND id <> :user_id LIMIT 1",
            {"username": identity["username"], "user_id": user_id},
        ).first()
        if taken:
            return fail("El nombre de usuario ya existe", status=409)

        updated = execute_safe(
            conn,
            """
            UPDATE users
            SET username = :username, full_name = :full_name,
                updated_at = NOW(), updated_by = :user_id
            WHERE id = :user_id
            RETURNING id, username, full_name, avatar_path, active, last_login, created_at, updated_at
            """,
            {"username": identity["username"], "full_name": identity["full_name"], "user_id": user_id},
        ).mappings().first()

    return ok(data=_shape_profile(updated), message="Perfil actualizado")


@auth_bp.post("/me/password")
@session_required
def change_my_password():
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password") or ""
    new_password = payload.get("new_password") or ""

    if not current_password or not new_password:
        return fail("Debes ingresar la contraseña actual y la nueva", status=400)

    valid, error = validate_new_password(new_password)
    if not valid:
        return fail(error, status=400)

    with db_conn() as conn:
        user = execute_safe(
            conn,
            "SELECT id, password_hash FROM users WHERE id = :user_id AND active = TRUE",
            {"user_id": user_id},
        ).mappings().first()
        if not user:
            return fail("Usuario no encontrado", status=404)
        if not verify_password(current_password, user["password_hash"]):
            # 400 y no 401: la sesión es válida. Un 401 hace que el frontend
            # la dé por caducada y eche a la persona por un despiste (F06).
            return fail("La contraseña actual no es correcta", status=400, code="invalid_current_password")

        execute_safe(
            conn,
            """
            UPDATE users
            SET password_hash = :password_hash, updated_at = NOW(), updated_by = :user_id
            WHERE id = :user_id
            """,
            {"password_hash": hash_password(new_password), "user_id": user_id},
        )

        # Cambiar la contraseña echa a los demas dispositivos. Con JWT esto no
        # se podia: un token robado seguia valiendo hasta caducar aunque la
        # victima cambiara la clave, que es justo cuando mas falta hace.
        sesion_vieja = current_session_id()
        revocadas = revoke_all_sessions(conn, user_id, keep_session_id=sesion_vieja)

        # Y la sesion ACTUAL tambien rota: no se echa a quien acaba de
        # cambiar la clave, pero su token y su CSRF viejos dejan de servir
        # para nada. Si alguno se hubiera filtrado justo antes del cambio (un
        # log de proxy, por ejemplo), este es el momento en que se invalida.
        nueva_sesion = rotate_session(conn, sesion_vieja, user_id, request.headers.get("User-Agent"))

    cuerpo, status = ok(data={"revoked_sessions": revocadas}, message="Contraseña actualizada")
    return attach_session_cookies(make_response(cuerpo, status), nueva_sesion)


# --- Códigos de recuperación (L14) -------------------------------------------

RECOVERY_FAILED = "No pudimos recuperar la cuenta con esos datos."


@auth_bp.get("/recovery-codes")
@session_required
def recovery_code_status():
    """Cuántos quedan y desde cuándo. Nunca los códigos: solo se ven al generarlos."""
    with db_conn() as conn:
        return ok(data=code_status(conn, current_user_id()))


@auth_bp.post("/recovery-codes")
@session_required
def generate_recovery_codes():
    """Un juego nuevo de diez, que invalida el anterior entero. Pide la
    contraseña actual, como cambiarla: con una sesión robada no se pueden
    fabricar códigos para quedarse con la cuenta. Un 400 y no un 401 en la
    contraseña equivocada: el 401 es «tu sesión expiró» para el frontend."""
    user_id = current_user_id()
    current_password = (request.get_json(silent=True) or {}).get("current_password") or ""
    with db_conn() as conn:
        user = execute_safe(conn, "SELECT password_hash FROM users WHERE id = :user_id AND active = TRUE",
                            {"user_id": user_id}).mappings().first()
        if not user or not verify_password(current_password, user["password_hash"]):
            return fail("La contraseña actual no es correcta", status=400)
        codes = replace_codes(conn, user_id)
        status = code_status(conn, user_id)
    return ok(data={"codes": codes, "created_at": status["created_at"]},
              message="Guarda estos códigos ahora: no se vuelven a mostrar", status=201)


@auth_bp.delete("/recovery-codes")
@session_required
def revoke_recovery_codes():
    with db_conn() as conn:
        borrados = delete_all_codes(conn, current_user_id())
    return ok(data={"revoked": borrados}, message="Códigos revocados")


@auth_bp.post("/recover")
def recover_account():
    """Usuario + un código + contraseña nueva, sin email. Todo fallo que
    dependa de la cuenta o del código responde lo mismo (`RECOVERY_FAILED`),
    así que no delata si el usuario existe ni si el código existió o ya se
    usó. Gasta el mismo cupo que el login. Si sale bien: el código se gasta,
    la contraseña cambia, TODAS las sesiones se revocan y TODOS los códigos
    que quedaban desaparecen, en la misma transacción. No inicia sesión."""
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    code = payload.get("code")
    new_password = payload.get("new_password") or ""
    if not username or not code or not new_password:
        return fail("Faltan datos para recuperar la cuenta", status=400)

    with db_conn() as conn:
        permitido, retry_after = _consume_login_budget(conn, username)
        if not permitido:
            return _rate_limited(retry_after)

    # La política se comprueba ANTES de gastar el código: una contraseña débil
    # no puede quemar una de las diez salidas de emergencia.
    valid, error = validate_new_password(new_password)
    if not valid:
        return fail(error, status=400)

    with db_conn() as conn:
        user = execute_safe(conn, "SELECT id FROM users WHERE username = :username AND active = TRUE",
                            {"username": username}).mappings().first()
        if not user or not consume_code(conn, user["id"], code):
            return fail(RECOVERY_FAILED, status=400, code="recovery_failed")
        execute_safe(conn, """
            UPDATE users SET password_hash = :password_hash, updated_at = NOW(), updated_by = :user_id
            WHERE id = :user_id
        """, {"password_hash": hash_password(new_password), "user_id": user["id"]})
        revoke_all_sessions(conn, user["id"])
        delete_all_codes(conn, user["id"])
    return ok(message="Contraseña restablecida. Ya puedes iniciar sesión con la nueva.")


@auth_bp.post("/me/avatar")
@session_required
def upload_my_avatar():
    user_id = current_user_id()
    file = request.files.get("file")
    if not file or not file.filename:
        return fail("Debes seleccionar una imagen", status=400)

    file.stream.seek(0)
    header = file.stream.read(64)
    file.stream.seek(0)
    try:
        signature = detect_media_signature(header, file.filename, file.mimetype)
    except ValueError as exc:
        return fail(str(exc), status=400)
    if signature[0] != "image":
        return fail("La foto de perfil debe ser una imagen", status=400)
    # Los HEIC/HEIF de album siempre ganan una preview WebP para navegadores.
    # El avatar, en cambio, se sirve directamente y no tiene derivado: aceptar
    # aqui el original produciria una foto de perfil rota en navegadores sin
    # soporte HEIF nativo.
    if signature[1] in {"heic", "heif"}:
        return fail("HEIC/HEIF se admite en álbumes, no como foto de perfil", status=400)
    max_avatar_mb = int_env("MAX_AVATAR_MB", DEFAULT_MAX_AVATAR_MB)
    max_avatar_bytes = max_avatar_mb * 1024 * 1024 if max_avatar_mb > 0 else 0
    if max_avatar_bytes and upload_size(file) > max_avatar_bytes:
        return fail(f"La foto de perfil no puede superar {max_avatar_mb} MB", status=400)

    try:
        stored = save_upload(file, user_id, signature=signature, folder="avatar", max_bytes=max_avatar_bytes)
    except ValueError as exc:
        return fail(str(exc), status=400)

    try:
        with db_conn() as conn:
            previous = execute_safe(
                conn,
                "SELECT avatar_path FROM users WHERE id = :user_id",
                {"user_id": user_id},
            ).mappings().first()
            execute_safe(
                conn,
                """
                UPDATE users
                SET avatar_path = :avatar_path, updated_at = NOW(), updated_by = :user_id
                WHERE id = :user_id
                """,
                {"avatar_path": stored["storage_path"], "user_id": user_id},
            )
    except Exception:
        mark_rolled_back(stored["storage_operation_id"])
        remove_stored_file(stored["storage_path"])
        raise

    mark_committed(stored["storage_operation_id"])
    # La foto anterior se borra recién cuando la fila ya apunta a la nueva, para
    # que un fallo no deje al usuario sin ninguna de las dos.
    if previous and previous["avatar_path"]:
        try:
            remove_stored_file(previous["avatar_path"])
        except StorageError:
            # El avatar nuevo ya es el bueno; el viejo queda para el GC. Un
            # fallo al limpiar el anterior no revierte la subida confirmada.
            pass

    return ok(data={"has_avatar": True}, message="Foto de perfil actualizada")


@auth_bp.get("/me/stats")
@session_required
def my_stats():
    user_id = current_user_id()
    with db_conn() as conn:
        stats = execute_safe(
            conn,
            f"""
            SELECT
                (SELECT COUNT(*) FROM albums
                  WHERE user_id = :user_id AND active = TRUE) AS total_albums,
                (SELECT COUNT(*) FROM album_shares s
                  WHERE s.shared_with_user_id = :user_id
                    AND s.active = TRUE
                    AND s.share_type = 'account'
                    AND (s.expires_at IS NULL OR s.expires_at > NOW())) AS total_shared,
                (SELECT COUNT(*) FROM assets m
                  WHERE m.user_id = :user_id AND {ACTIVE_SCOPE_SQL}
                    AND m.deleted_at IS NULL AND m.is_favorite = TRUE) AS total_favorites
            """,
            {"user_id": user_id},
        ).mappings().first()
        used = account_storage_used_bytes(conn, user_id)

    quota_gb = int_env("USER_STORAGE_QUOTA_GB", 0)
    data = dict(stats)
    data["storage_used_bytes"] = used
    # None y no 0: "0" leeria como "tu cuota es cero bytes", cuando en
    # realidad es "sin limite" -- la ausencia del dato dice eso mejor que
    # cualquier numero. El frontend no pinta nada de cuota con quota=None.
    data["storage_quota_bytes"] = quota_gb * 1024 ** 3 if quota_gb > 0 else None

    return ok(data=data, message="Estadísticas de la cuenta")


@auth_bp.delete("/me")
@session_required
def delete_me():
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    confirmation = (payload.get("username") or "").strip()

    with db_conn() as conn:
        user = _load_profile(conn, user_id)
        if not user:
            return fail("Usuario no encontrado", status=404)
        if confirmation != user["username"]:
            return fail("Escribe tu nombre de usuario para confirmar", status=400)

        # La vista previa va en la misma lista: se borra igual que el
        # original, y hay que leerla antes del DELETE porque `media_metadata`
        # cae por cascada con la cuenta.
        paths = []
        for row in execute_safe(
            conn,
            """
            SELECT m.storage_path, mm.preview_storage_path
            FROM assets m
            LEFT JOIN media_metadata mm ON mm.media_id = m.id
            WHERE m.user_id = :user_id
            """,
            {"user_id": user_id},
        ).mappings().all():
            paths.append(row["storage_path"])
            if row["preview_storage_path"]:
                paths.append(row["preview_storage_path"])
        if user["avatar_path"]:
            paths.append(user["avatar_path"])

        # Las claves foráneas en cascada se llevan álbumes, media, metadata,
        # tags y comparticiones.
        # Intención durable antes de tocar la base: si el proceso muere entre
        # el commit y el borrado físico, el reconciliador sabe qué falta.
        operacion = record_pending("delete", paths) if paths else None

        execute_safe(conn, "DELETE FROM users WHERE id = :user_id", {"user_id": user_id})

    # Los archivos se borran recién con la transacción cerrada: si hubiera
    # fallado, la cuenta seguiría existiendo y sus archivos también.
    removed = 0
    pendientes = 0
    for path in paths:
        try:
            if remove_stored_file(path):
                removed += 1
        except StorageError:
            # La cuenta ya no existe; lo que quede en disco es trabajo del GC.
            pendientes += 1
    if operacion and not pendientes:
        mark_committed(operacion)
    datos = {"removed_files": removed}
    if pendientes:
        datos["storage_cleanup_pending"] = True
        return ok(data=datos, message="Cuenta eliminada", status=202)
    return ok(data=datos, message="Cuenta eliminada")
