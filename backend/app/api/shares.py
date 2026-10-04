import secrets

from flask import Blueprint, request
from ..security.sessions import current_user_id, hash_token, session_required, summarize_user_agent

from ..db.db import db_conn
from ..domain.rules import ALBUM_CAPABILITIES, normalize_share_request, validate_share_permission_update
from ..activity import record_activity
from ..notifications import notify
from ..security.hashing import hash_password, verify_password
from ..media.assets import in_album_sql
from ..security.permissions import get_token_share, require_album_permission
from ..security.rate_limit import client_ip, try_consume
from ..security.share_token_crypto import decrypt_share_token, encrypt_share_token
from ..security.share_unlock import attach_unlock_cookie, create_unlock, is_unlocked, unlock_cookie_name
from ..storage.media_delivery import deliver_stored_file
from ..utils.env import int_env
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

shares_bp = Blueprint("shares", __name__, url_prefix="/api")


def _rate_limited(retry_after: int):
    """429 uniforme, con `Retry-After` -- mismo criterio que login/registro."""
    respuesta, status = fail(
        "Demasiados intentos. Espera un momento antes de volver a intentarlo.",
        status=429, code="rate_limited",
    )
    respuesta.headers["Retry-After"] = str(retry_after)
    return respuesta, status


def _share_target_type(row) -> str:
    if row.get("shared_with_user_id") is not None:
        return "user"
    if row.get("share_type") == "public_link":
        return "public_link"
    if row.get("has_token"):
        return "pending_account"
    return "account"


def _shape_share(row) -> dict:
    """`has_password`/`has_token` ya llegan calculados desde SQL -- el hash
    y el propio token nunca se seleccionan, así que no hay nada secreto que
    recordar quitar aquí (mismo criterio que las sesiones)."""
    item = dict(row)
    item["target_type"] = _share_target_type(item)
    item["capabilities"] = list(item.get("capabilities") or [])
    # `has_token` se conserva a proposito: es un booleano, no un secreto, y es
    # lo que le dice a la pantalla de gestion si este acceso todavia tiene un
    # enlace que se pueda regenerar (una invitacion ya reclamada no lo tiene).
    item["has_token"] = bool(item.get("has_token"))
    return item


def shape_shared_media(media, show_metadata: bool) -> dict:
    """Forma pública de una foto, aplicada antes de formar la respuesta.

    Sin metadatos, el detalle aún necesita identificar y pintar el archivo,
    título y descripción. Autor, fechas y cualquier otro dato técnico se
    descartan en el servidor; ocultar la pestaña en el cliente no basta.
    """
    full = dict(media)
    if show_metadata:
        return full
    return {field: full[field] for field in ('id', 'album_id', 'file_type', 'title', 'caption')}


def _touch_share_access(conn, share_id: int) -> None:
    """Se llama al abrir un enlace público (S08): deja constancia de cuándo
    y desde qué dispositivo, para que el dueño pueda distinguir enlaces sin
    etiqueta entre sí -- nunca la IP ni una ubicación, mismo criterio que
    `user_sessions.user_agent_summary`."""
    execute_safe(
        conn,
        "UPDATE album_shares SET last_accessed_at = NOW(), last_accessed_user_agent = :ua WHERE id = :share_id",
        {"share_id": share_id, "ua": summarize_user_agent(request.headers.get("User-Agent"))},
    )


@shares_bp.get("/albums/<int:album_id>/shares")
@session_required
def list_album_shares(album_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        if not require_album_permission(conn, album_id, user_id, "owner"):
            return fail("Solo el dueño puede ver comparticiones", status=403)

        rows = execute_safe(
            conn,
            """
            SELECT s.id, s.album_id, s.shared_by, s.shared_with_user_id,
                   (s.token_hash IS NOT NULL) AS has_token,
                   (s.token_encrypted IS NOT NULL) AS can_reveal,
                   s.share_type, s.permission, s.capabilities,
                   (s.password_hash IS NOT NULL) AS has_password,
                   s.allow_original_download, s.show_metadata,
                   s.last_accessed_at, s.last_accessed_user_agent,
                   s.active, s.expires_at, s.claimed_at, s.created_at,
                   u.username AS shared_with_username, u.full_name AS shared_with_full_name
            FROM album_shares s
            LEFT JOIN users u ON u.id = s.shared_with_user_id
            WHERE s.album_id = :album_id
              AND s.active = TRUE
              AND (s.expires_at IS NULL OR s.expires_at > NOW())
            ORDER BY s.created_at DESC
            """,
            {"album_id": album_id},
        ).mappings().all()

    return ok(data=[_shape_share(row) for row in rows], message="Comparticiones")


@shares_bp.post("/albums/<int:album_id>/shares")
@session_required
def create_album_share(album_id: int):
    user_id = current_user_id()
    try:
        share_input = normalize_share_request(request.get_json(silent=True) or {})
    except ValueError as exc:
        return fail(str(exc), status=400)

    with db_conn() as conn:
        access = require_album_permission(conn, album_id, user_id, "owner")
        if not access:
            return fail("Solo el dueño puede compartir", status=403)

        target_user_id = share_input["shared_with_user_id"]
        target_type = share_input["target_type"]
        share_type = share_input["share_type"]
        token = None

        if target_type == "user":
            target_user = execute_safe(
                conn,
                "SELECT id FROM users WHERE id = :user_id AND active = TRUE",
                {"user_id": target_user_id},
            ).mappings().first()
            if not target_user:
                return fail("El usuario destino no existe o está inactivo", status=404)
            if target_user["id"] == user_id:
                return fail("No puedes compartir un álbum contigo mismo", status=400)
            share_type = "account"
        else:
            token = secrets.token_urlsafe(32)

        if target_user_id is not None:
            duplicate = execute_safe(
                conn,
                """
                SELECT id FROM album_shares
                WHERE album_id = :album_id
                  AND shared_with_user_id = :shared_with_user_id
                  AND active = TRUE
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                {"album_id": album_id, "shared_with_user_id": target_user_id},
            ).mappings().first()
            if duplicate:
                return fail("Este usuario ya tiene una compartición activa", status=409)

        share = execute_safe(
            conn,
            """
            INSERT INTO album_shares (
                album_id, shared_by, shared_with_user_id,
                token_hash, token_encrypted, share_type, permission, capabilities, active, expires_at,
                password_hash, allow_original_download, show_metadata
            )
            VALUES (
                :album_id, :shared_by, :shared_with_user_id,
                :token_hash, :token_encrypted, :share_type, :permission, :capabilities, TRUE, :expires_at,
                :password_hash, :allow_original_download, :show_metadata
            )
            RETURNING id, album_id, shared_by, shared_with_user_id,
                      share_type, permission, capabilities,
                      allow_original_download, show_metadata,
                      (password_hash IS NOT NULL) AS has_password,
                      (token_encrypted IS NOT NULL) AS can_reveal,
                      active, expires_at, claimed_at, created_at
            """,
            {
                "album_id": album_id,
                "shared_by": user_id,
                "shared_with_user_id": target_user_id,
                "token_hash": hash_token(token) if token else None,
                "token_encrypted": encrypt_share_token(token) if token else None,
                "share_type": share_type,
                "permission": share_input["permission"],
                "capabilities": share_input["capabilities"],
                "expires_at": share_input["expires_at"],
                "password_hash": hash_password(share_input["password"]) if share_input["password"] else None,
                "allow_original_download": share_input["allow_original_download"],
                "show_metadata": share_input["show_metadata"],
            },
        ).mappings().first()

        # L12, en la misma transacción que el INSERT: una invitación de cuenta
        # (a una persona o por enlace de cuenta) queda en la actividad; el aviso
        # solo existe si ya hay destinatario. Un enlace público no invita a nadie.
        if share_type == "account":
            record_activity(conn, album_id, user_id, "album_invite_created", metadata={
                "target_user_id": target_user_id,
                "permission": share_input["permission"],
                "capabilities": share_input["capabilities"],
            })
            if target_user_id is not None:
                notify(conn, target_user_id, "album_invite", album_id=album_id, actor_user_id=user_id)

    data = _shape_share({**dict(share), "has_token": token is not None})
    # El token en claro solo existe aquí, en esta respuesta -- la base ya
    # solo tiene su hash, igual que una invitación de registro.
    if token:
        data["token"] = token
    return ok(data=data, message="Acceso creado", status=201)


@shares_bp.post("/shared/<string:token>/claim")
@session_required
def claim_account_share(token: str):
    user_id = current_user_id()
    with db_conn() as conn:
        pending = execute_safe(
            conn,
            """
            SELECT s.id, s.album_id, s.permission, s.capabilities, s.expires_at,
                   a.user_id AS album_owner_id, a.active AS album_active
            FROM album_shares s
            JOIN albums a ON a.id = s.album_id
            WHERE s.token_hash = :token_hash
              AND s.share_type = 'account'
              AND s.shared_with_user_id IS NULL
              AND s.active = TRUE
              AND a.active = TRUE
              AND (s.expires_at IS NULL OR s.expires_at > NOW())
            FOR UPDATE
            """,
            {"token_hash": hash_token(token)},
        ).mappings().first()
        if not pending:
            return fail("La invitación no existe, ya fue utilizada o expiró", status=404)
        if pending["album_owner_id"] == user_id:
            return fail("El propietario no puede reclamar su propia invitación", status=400)

        existing = execute_safe(
            conn,
            """
            SELECT id, permission, capabilities, expires_at
            FROM album_shares
            WHERE album_id = :album_id
              AND shared_with_user_id = :user_id
              AND active = TRUE
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
            """,
            {"album_id": pending["album_id"], "user_id": user_id},
        ).mappings().first()

        if existing:
            # El colaborador ya tenia acceso: se queda con la UNION de lo que
            # ya podia y lo que trae la invitacion nueva, para no perder nada
            # por reclamar un enlace. Con capacidades sueltas esto es la union
            # de conjuntos, no "el nivel mas alto": una invitacion que solo
            # concede `upload` no puede quitarle el `organize` que ya tenia.
            unidas = {c for c in (existing["capabilities"] or []) if c in ALBUM_CAPABILITIES}
            unidas |= {c for c in (pending["capabilities"] or []) if c in ALBUM_CAPABILITIES}
            final_capabilities = [c for c in ALBUM_CAPABILITIES if c in unidas]
            final_permission = "write" if final_capabilities else "read"
            execute_safe(
                conn,
                "UPDATE album_shares SET permission = :permission, capabilities = :capabilities WHERE id = :share_id",
                {"permission": final_permission, "capabilities": final_capabilities, "share_id": existing["id"]},
            )
            execute_safe(
                conn,
                """
                UPDATE album_shares
                SET active = FALSE, token_hash = NULL, token_encrypted = NULL, claimed_at = NOW()
                WHERE id = :pending_id
                """,
                {"pending_id": pending["id"]},
            )
            share_id = existing["id"]
        else:
            final_permission = pending["permission"]
            final_capabilities = list(pending["capabilities"] or [])
            claimed = execute_safe(
                conn,
                """
                UPDATE album_shares
                SET shared_with_user_id = :user_id,
                    token_hash = NULL,
                    token_encrypted = NULL,
                    claimed_at = NOW()
                WHERE id = :share_id
                RETURNING id
                """,
                {"user_id": user_id, "share_id": pending["id"]},
            ).mappings().first()
            share_id = claimed["id"]

        record_activity(conn, pending["album_id"], user_id, "share_claimed")
        notify(conn, pending["album_owner_id"], "share_claimed", album_id=pending["album_id"], actor_user_id=user_id)

    return ok(
        data={
            "share_id": share_id,
            "album_id": pending["album_id"],
            "permission": final_permission,
            "capabilities": final_capabilities,
        },
        message="Álbum guardado en Compartido conmigo",
    )


@shares_bp.patch("/shares/<int:share_id>")
@session_required
def update_share_permission(share_id: int):
    """Cambia lo que puede hacer un colaborador ya activo (o una invitacion
    pendiente de aceptar), sin tener que revocar y volver a invitar. Para un
    enlace público, el mismo cuerpo también puede traer password/
    allow_original_download/show_metadata -- un PATCH parcial encima del
    permission/capabilities obligatorios (que para un enlace público son
    siempre 'read'/[] de todas formas, así que no es una carga extra)."""
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    try:
        permission, capabilities, public_link_updates = validate_share_permission_update(payload)
    except ValueError as exc:
        return fail(str(exc), status=400)

    with db_conn() as conn:
        share = execute_safe(
            conn,
            """
            SELECT s.id, s.album_id, s.share_type, s.active, s.shared_with_user_id,
                   s.permission, s.capabilities, a.user_id AS album_owner
            FROM album_shares s
            JOIN albums a ON a.id = s.album_id
            WHERE s.id = :share_id
            FOR UPDATE OF s
            """,
            {"share_id": share_id},
        ).mappings().first()
        if not share:
            return fail("Compartición no encontrada", status=404)
        if share["album_owner"] != user_id:
            return fail("Solo el dueño puede cambiar permisos", status=403)
        if not share["active"]:
            return fail("Esta compartición ya no está activa", status=400)
        if share["share_type"] == "public_link" and permission != "read":
            return fail("Un enlace público solo puede ser de lectura", status=400)
        if public_link_updates and share["share_type"] != "public_link":
            return fail("Estos controles solo aplican a un enlace público", status=400)

        campos = {"permission": permission, "capabilities": capabilities, "share_id": share_id}
        sets = ["permission = :permission", "capabilities = :capabilities"]
        if "password" in public_link_updates:
            password = public_link_updates["password"]
            campos["password_hash"] = hash_password(password) if password else None
            sets.append("password_hash = :password_hash")
        if "allow_original_download" in public_link_updates:
            campos["allow_original_download"] = public_link_updates["allow_original_download"]
            sets.append("allow_original_download = :allow_original_download")
        if "show_metadata" in public_link_updates:
            campos["show_metadata"] = public_link_updates["show_metadata"]
            sets.append("show_metadata = :show_metadata")

        updated = execute_safe(
            conn,
            f"""
            UPDATE album_shares SET {', '.join(sets)}
            WHERE id = :share_id
            RETURNING id, album_id, permission, capabilities, allow_original_download, show_metadata,
                      (password_hash IS NOT NULL) AS has_password
            """,
            campos,
        ).mappings().first()

        # Solo un cambio EFECTIVO del acceso de una cuenta: repetir los mismos
        # valores, o tocar contraseña/descarga/metadatos de un enlace público,
        # no es un cambio de permisos.
        antes = (share["permission"], sorted(share["capabilities"] or []))
        if share["share_type"] == "account" and antes != (permission, sorted(capabilities)):
            record_activity(conn, share["album_id"], user_id, "share_permission_changed", metadata={
                "target_user_id": share["shared_with_user_id"],
                "permission": permission,
                "capabilities": capabilities,
            })

    data = dict(updated)
    data["capabilities"] = list(data.get("capabilities") or [])
    return ok(data=data, message="Permisos actualizados")


@shares_bp.delete("/shares/<int:share_id>")
@session_required
def disable_share(share_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        share = execute_safe(
            conn,
            """
            SELECT s.id, a.user_id AS album_owner
            FROM album_shares s
            JOIN albums a ON a.id = s.album_id
            WHERE s.id = :share_id
            """,
            {"share_id": share_id},
        ).mappings().first()
        if not share:
            return fail("Compartición no encontrada", status=404)
        if share["album_owner"] != user_id:
            return fail("Solo el dueño puede desactivar comparticiones", status=403)
        execute_safe(conn, "UPDATE album_shares SET active = FALSE WHERE id = :share_id", {"share_id": share_id})
    return ok(message="Acceso revocado")


@shares_bp.post("/shared/<string:token>/unlock")
def unlock_shared_link(token: str):
    """Prueba la contraseña de un enlace público UNA vez y, si es correcta,
    deja una cookie que autoriza las peticiones siguientes -- nunca hay que
    volver a mandar la contraseña, y nunca viaja en la URL."""
    payload = request.get_json(silent=True) or {}
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        return fail("Contraseña requerida", status=400)

    with db_conn() as conn:
        share = get_token_share(conn, token, "read")
        if not share:
            return fail("Enlace público inválido o expirado", status=404)
        if not share["password_hash"]:
            return fail("Este enlace no tiene contraseña", status=400)

        ip = client_ip()
        limite = int_env("RATE_LIMIT_SHARE_PASSWORD_PER_15_MIN", 10)
        # Mismo criterio que login: por el share Y por la IP a la vez, y se
        # cuenta cada intento acierte o falle -- si no, el propio contador
        # delataria si una contraseña era correcta.
        permitido_share, retry_share = try_consume(conn, "share_password_share", str(share["id"]), limite, 900)
        permitido_ip, retry_ip = try_consume(conn, "share_password_ip", ip, limite, 900)
        if not (permitido_share and permitido_ip):
            return _rate_limited(max(retry_share, retry_ip))

        if not verify_password(password, share["password_hash"]):
            return fail("Contraseña incorrecta", status=401)

        raw_unlock, expires_at = create_unlock(conn, share["id"])

    cuerpo, status = ok(message="Enlace desbloqueado")
    attach_unlock_cookie(cuerpo, token, raw_unlock, expires_at)
    return cuerpo, status


@shares_bp.get("/shared/<string:token>/media")
def read_shared_media_by_token(token: str):
    with db_conn() as conn:
        share = get_token_share(conn, token, "read")
        if not share:
            return fail("Enlace público inválido o expirado", status=404)

        if share["password_hash"]:
            cookie = request.cookies.get(unlock_cookie_name(token))
            if not is_unlocked(conn, share["id"], cookie):
                return fail("Este enlace requiere una contraseña", status=401, code="share_password_required")

        # Se registra AQUI, no en /file ni /preview: esta es la ruta que se
        # pide una vez por visita (abrir el álbum), mientras que file/preview
        # se piden una vez POR FOTO -- contarlas ahí inflaría "última vez" en
        # cada miniatura de la cuadrícula sin decir nada nuevo.
        _touch_share_access(conn, share["id"])

        # Solo los miembros del album del enlace: que el mismo asset este en
        # otros albumes del dueño no lo expone por aqui.
        media = execute_safe(
            conn,
            f"""
            SELECT m.id, :album_id AS album_id, m.file_type, m.title, m.caption, m.taken_at,
                   m.created_at, m.created_by
            FROM assets m
            WHERE {in_album_sql("album_id")} AND m.deleted_at IS NULL
            ORDER BY m.created_at DESC
            """,
            {"album_id": share["album_id"]},
        ).mappings().all()

    album = {
        "id": share["album_id"],
        "titulo": share["titulo"],
        "descripcion": share["descripcion"],
        "is_private": share["is_private"],
        "cover_media_id": share["cover_media_id"],
    }
    share_data = {
        "id": share["id"],
        "album_id": share["album_id"],
        "share_type": share["share_type"],
        "permission": "read",
        "expires_at": share["expires_at"],
        "target_type": "public_link",
        "allow_original_download": share["allow_original_download"],
        "show_metadata": share["show_metadata"],
        "has_password": bool(share["password_hash"]),
    }
    return ok(data={"share": share_data, "album": album,
                    "media": [shape_shared_media(row, share["show_metadata"]) for row in media]},
              message="Contenido compartido")


@shares_bp.get("/shared/<string:token>/media/<int:media_id>")
def read_shared_media_detail(token: str, media_id: int):
    """El MISMO detalle que ve una cuenta con acceso de solo lectura.

    Devuelve la forma exacta de `GET /api/media/<id>` (media, metadata, exif,
    tags) más el contexto y el OCR, que en la ruta autenticada son dos
    peticiones aparte -- aquí van juntos porque un visitante anónimo no tiene
    sesión con la que encadenarlas. `album_role`/`album_capabilities` van
    fijos a solo lectura, así que el frontend apaga TODA acción de escritura
    con las mismas comprobaciones que ya usa.

    `show_metadata` (S08) por fin hace algo: apagado, el visitante ve la foto
    con su título y descripción, y nada de EXIF, lugar, etiquetas ni texto.
    """
    from ..media.context import _read_context, _shape_context
    from .media import _read_exif, _shape_exif
    from .media_context import _read_ocr

    with db_conn() as conn:
        share = get_token_share(conn, token, "read")
        if not share:
            return fail("Enlace público inválido o expirado", status=404)
        if share["password_hash"]:
            cookie = request.cookies.get(unlock_cookie_name(token))
            if not is_unlocked(conn, share["id"], cookie):
                return fail("Este enlace requiere una contraseña", status=401, code="share_password_required")

        media = execute_safe(
            conn,
            f"""
            SELECT m.id, m.user_id, :album_id AS album_id, m.file_type, m.title, m.caption,
                   m.is_favorite, m.taken_at, m.created_at, m.created_by,
                   creator.username AS created_by_username
            FROM assets m
            LEFT JOIN users creator ON creator.id = m.created_by
            WHERE m.id = :media_id AND {in_album_sql("album_id")} AND m.deleted_at IS NULL
            """,
            {"media_id": media_id, "album_id": share["album_id"]},
        ).mappings().first()
        if not media:
            return fail("Media no encontrada", status=404)

        detalle = {
            "media": shape_shared_media(media, share["show_metadata"]),
            "metadata": None,
            "exif": None,
            "tags": [],
            "context": None,
            "ocr": None,
            # Un enlace público NUNCA concede escritura: el frontend reutiliza
            # el detalle autenticado y estas dos claves apagan sus botones.
            "album_role": "read",
            "album_capabilities": [],
            "allow_original_download": share["allow_original_download"],
            "show_metadata": share["show_metadata"],
        }

        if share["show_metadata"]:
            metadata = execute_safe(
                conn,
                """
                SELECT id, media_id, file_size, resolution, duration, format,
                       original_filename, mime_type
                FROM media_metadata WHERE media_id = :media_id
                """,
                {"media_id": media_id},
            ).mappings().first()
            tags = execute_safe(
                conn,
                """
                SELECT t.id, t.name
                FROM media_tags mt
                JOIN tags t ON t.id = mt.tag_id
                WHERE mt.media_id = :media_id
                ORDER BY t.name ASC
                """,
                {"media_id": media_id},
            ).mappings().all()
            ocr = _read_ocr(conn, media_id)
            detalle.update({
                "metadata": dict(metadata) if metadata else None,
                "exif": _shape_exif(_read_exif(conn, media_id)),
                "tags": [dict(t) for t in tags],
                "context": _shape_context(_read_context(conn, media_id)),
                "ocr": dict(ocr) if ocr else None,
            })

    return ok(data=detalle, message="Detalle compartido")


@shares_bp.post("/shares/<int:share_id>/regenerate")
@session_required
def regenerate_share_token(share_id: int):
    """Emite un token NUEVO para un enlace ya creado, invalidando el anterior.

    Sigue existiendo aunque el token ya se pueda VER (`reveal_share_token`,
    abajo): son dos acciones distintas. Regenerar es para cuando el enlace
    pudo filtrarse (un chat, un log) y hay que matarlo sin dejar de compartir
    el álbum; reveal es para el caso normal de "quiero volver a copiarlo".
    """
    user_id = current_user_id()
    with db_conn() as conn:
        share = execute_safe(
            conn,
            "SELECT id, album_id, share_type, shared_with_user_id FROM album_shares "
            "WHERE id = :share_id AND active = TRUE",
            {"share_id": share_id},
        ).mappings().first()
        if not share:
            return fail("Compartición no encontrada", status=404)
        if not require_album_permission(conn, share["album_id"], user_id, "owner"):
            return fail("Solo el dueño puede regenerar un enlace", status=403)
        if share["shared_with_user_id"] is not None:
            return fail("Esta invitación ya fue reclamada por una cuenta; no hay enlace que regenerar", status=400)

        token = secrets.token_urlsafe(32)
        execute_safe(
            conn,
            "UPDATE album_shares SET token_hash = :token_hash, token_encrypted = :token_encrypted WHERE id = :share_id",
            {"token_hash": hash_token(token), "token_encrypted": encrypt_share_token(token), "share_id": share_id},
        )
    return ok(data={"id": share_id, "share_type": share["share_type"], "token": token},
              message="Enlace regenerado")


@shares_bp.post("/shares/<int:share_id>/reveal")
@session_required
def reveal_share_token(share_id: int):
    """Descifra y devuelve el token de un enlace YA CREADO, sin tocar nada.

    Existe porque `token_encrypted` (arriba, `create_album_share`/
    `regenerate_share_token`) es reversible a propósito -- a diferencia de
    `token_hash`, que sigue siendo de una sola vía y es lo único que se usa
    para resolver un enlace entrante. Un enlace creado ANTES de esta
    columna (o con `SHARE_TOKEN_ENCRYPTION_KEY` sin configurar en ese
    momento) no tiene nada que descifrar: hay que regenerarlo una vez.
    """
    user_id = current_user_id()
    with db_conn() as conn:
        share = execute_safe(
            conn,
            "SELECT id, album_id, share_type, token_encrypted FROM album_shares "
            "WHERE id = :share_id AND active = TRUE",
            {"share_id": share_id},
        ).mappings().first()
        if not share:
            return fail("Compartición no encontrada", status=404)
        if not require_album_permission(conn, share["album_id"], user_id, "owner"):
            return fail("Solo el dueño puede ver este enlace", status=403)

        token = decrypt_share_token(share["token_encrypted"])
        if not token:
            return fail(
                "Este enlace no se puede volver a mostrar; regenéralo para poder copiarlo.",
                status=404, code="share_token_unavailable",
            )
    return ok(data={"id": share_id, "share_type": share["share_type"], "token": token},
              message="Enlace")


def _shared_media_file(token: str, media_id: int):
    """(fila de media + fila de share, error) de una media alcanzable por
    este enlace público. Misma comprobación para el original y para la
    vista previa: el enlace (contraseña incluida, si tiene) manda, y la
    vista previa nunca alcanza una media que el original no alcanzaría.
    """
    with db_conn() as conn:
        share = get_token_share(conn, token, "read")
        if not share:
            return None, fail("Enlace público inválido o expirado", status=404)
        if share["password_hash"]:
            cookie = request.cookies.get(unlock_cookie_name(token))
            if not is_unlocked(conn, share["id"], cookie):
                return None, fail("Este enlace requiere una contraseña", status=401, code="share_password_required")
        media = execute_safe(
            conn,
            f"""
            SELECT m.id, m.storage_path, mm.original_filename, mm.mime_type,
                   mm.preview_storage_path, mm.preview_mime_type
            FROM assets m
            LEFT JOIN media_metadata mm ON mm.media_id = m.id
            WHERE m.id = :media_id AND {in_album_sql("album_id")} AND m.deleted_at IS NULL
            """,
            {"media_id": media_id, "album_id": share["album_id"]},
        ).mappings().first()
        if not media:
            return None, fail("Media no encontrada", status=404)
    return (media, share), None


@shares_bp.get("/shared/<string:token>/media/<int:media_id>/file")
def read_shared_file(token: str, media_id: int):
    result, error = _shared_media_file(token, media_id)
    if error:
        return error
    media, share = result
    # Apagado por defecto (S08): antes cualquier enlace de solo lectura
    # podia bajar el original completo sin que el dueño lo decidiera.
    if not share["allow_original_download"]:
        return fail("El propietario no permite descargar el archivo original de este enlace", status=403)
    response = deliver_stored_file(
        media["storage_path"],
        media["mime_type"] or None,
        media["original_filename"] or None,
    )
    if response is None:
        return fail("El archivo ya no existe en almacenamiento", status=404)
    return response


@shares_bp.get("/shared/<string:token>/media/<int:media_id>/preview")
def read_shared_preview(token: str, media_id: int):
    result, error = _shared_media_file(token, media_id)
    if error:
        return error
    media, share = result
    if media["preview_storage_path"]:
        response = deliver_stored_file(
            media["preview_storage_path"],
            media["preview_mime_type"] or None,
            media["original_filename"] or None,
        )
        if response is not None:
            return response
    # Sin vista previa (video, foto anterior a la fase 13, o generación
    # fallida): a diferencia de la ruta autenticada, aquí NO se cae al
    # original salvo que el dueño permita explícitamente descargarlo -- si
    # no, un enlace "sin original" quedaría igual de expuesto por esta vía.
    if not share["allow_original_download"]:
        return fail("No hay una vista previa disponible para este archivo", status=404)
    response = deliver_stored_file(
        media["storage_path"],
        media["mime_type"] or None,
        media["original_filename"] or None,
    )
    if response is None:
        return fail("El archivo ya no existe en almacenamiento", status=404)
    return response
