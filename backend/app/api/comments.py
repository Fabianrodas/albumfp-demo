"""Rutas de comentarios de asset (F04).

- Leer y comentar: cualquier cuenta a la que la autorización CENTRAL
  (`require_asset_permission(..., "read")`) le da acceso al recuerdo. Eso
  incluye al colaborador de solo lectura y a cualquier cuenta en un álbum
  público: comentar no es escribir en el álbum, así que no se pide ninguna
  capacidad.
- Editar y borrar: solo quien escribió el comentario, y solo mientras siga
  teniendo acceso al recuerdo. Conocer un id no da autoridad.
- Un enlace público (anónimo) solo LEE, y solo los comentarios de un recuerdo
  que hoy es miembro de ESE álbum compartido, con la contraseña desbloqueada.
- Todo lo inaccesible responde el mismo 404, exista o no.
"""
from flask import Blueprint, request

from ..comments import (DEFAULT_PER_PAGE, MAX_PER_PAGE, clean_comment_body, insert_comment, list_comments,
                        read_comment, shape_comment)
from ..db.db import db_conn
from ..media.assets import in_album_sql, require_asset_permission
from ..security.permissions import get_token_share
from ..security.rate_limit import client_ip, try_consume
from ..security.sessions import current_user_id, session_required
from ..security.share_unlock import is_unlocked, unlock_cookie_name
from ..utils.env import int_env
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

comments_bp = Blueprint("comments", __name__, url_prefix="/api")

NOT_FOUND = "Recuerdo no encontrado"
COMMENT_NOT_FOUND = "Comentario no encontrado"


def _readable_asset(conn, asset_id: int, user_id: int) -> bool:
    asset, acceso = require_asset_permission(conn, asset_id, user_id, "read")
    return bool(asset and acceso)


def _page(conn, asset_id: int, viewer_id: int | None):
    page, per_page, offset = get_pagination_args(request, default_per_page=DEFAULT_PER_PAGE, max_per_page=MAX_PER_PAGE)
    total, rows = list_comments(conn, asset_id, per_page=per_page, offset=offset)
    return [shape_comment(r, viewer_id) for r in rows], build_pagination_meta(page, per_page, total)


def _body_or_error():
    payload = request.get_json(silent=True)
    try:
        return clean_comment_body((payload or {}).get("body") if isinstance(payload, dict) else None), None
    except ValueError as exc:
        return None, fail(str(exc), status=400, code="invalid_comment")


@comments_bp.get("/media/<int:asset_id>/comments")
@session_required
def list_media_comments(asset_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        if not _readable_asset(conn, asset_id, user_id):
            return fail(NOT_FOUND, status=404)
        items, pagination = _page(conn, asset_id, user_id)
    return ok(data=items, message="Comentarios", pagination=pagination)


@comments_bp.post("/media/<int:asset_id>/comments")
@session_required
def create_media_comment(asset_id: int):
    user_id = current_user_id()
    body, error = _body_or_error()
    if error:
        return error
    limite = int_env("RATE_LIMIT_COMMENTS_PER_10_MIN", 30)
    with db_conn() as conn:
        # Antes de mirar el recuerdo: el cupo cuenta cada intento, llegue a
        # donde llegue, así que tampoco sirve para distinguir ids.
        permitido_cuenta, espera_cuenta = try_consume(conn, "comment_user", f"user:{user_id}", limite, 600)
        permitido_ip, espera_ip = try_consume(conn, "comment_ip", client_ip(), limite * 2, 600)
        if not (permitido_cuenta and permitido_ip):
            respuesta, status = fail("Estás comentando muy deprisa. Espera un momento.", status=429, code="rate_limited")
            respuesta.headers["Retry-After"] = str(max(espera_cuenta, espera_ip))
            return respuesta, status
        if not _readable_asset(conn, asset_id, user_id):
            return fail(NOT_FOUND, status=404)
        comment = read_comment(conn, insert_comment(conn, asset_id, user_id, body))
    return ok(data=shape_comment(comment, user_id), message="Comentario publicado", status=201)


def _own_comment(conn, comment_id: int, user_id: int):
    """El comentario propio cuyo recuerdo sigue siendo accesible, o None."""
    own = execute_safe(conn, "SELECT id, asset_id FROM asset_comments WHERE id = :id AND user_id = :user_id",
                       {"id": comment_id, "user_id": user_id}).mappings().first()
    if not own or not _readable_asset(conn, own["asset_id"], user_id):
        return None
    return own


@comments_bp.patch("/comments/<int:comment_id>")
@session_required
def update_comment(comment_id: int):
    user_id = current_user_id()
    body, error = _body_or_error()
    if error:
        return error
    with db_conn() as conn:
        if not _own_comment(conn, comment_id, user_id):
            return fail(COMMENT_NOT_FOUND, status=404)
        execute_safe(conn, """
            UPDATE asset_comments SET body = :body, updated_at = NOW()
            WHERE id = :id AND user_id = :user_id
        """, {"body": body, "id": comment_id, "user_id": user_id})
        comment = read_comment(conn, comment_id)
    return ok(data=shape_comment(comment, user_id), message="Comentario editado")


@comments_bp.delete("/comments/<int:comment_id>")
@session_required
def delete_comment(comment_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        if not _own_comment(conn, comment_id, user_id):
            return fail(COMMENT_NOT_FOUND, status=404)
        execute_safe(conn, "DELETE FROM asset_comments WHERE id = :id AND user_id = :user_id",
                     {"id": comment_id, "user_id": user_id})
    return ok(message="Comentario eliminado")


@comments_bp.get("/shared/<string:token>/media/<int:asset_id>/comments")
def read_shared_media_comments(token: str, asset_id: int):
    """Solo lectura para el visitante de un enlace público: el mismo filtro
    que la foto (enlace vigente, contraseña desbloqueada, pertenencia ACTUAL
    al álbum de ese enlace, fuera de la papelera)."""
    with db_conn() as conn:
        share = get_token_share(conn, token, "read")
        if not share:
            return fail("Enlace público inválido o expirado", status=404)
        if share["password_hash"] and not is_unlocked(conn, share["id"], request.cookies.get(unlock_cookie_name(token))):
            return fail("Este enlace requiere una contraseña", status=401, code="share_password_required")
        miembro = execute_safe(conn, f"""
            SELECT 1 FROM assets m
            WHERE m.id = :asset_id AND {in_album_sql("album_id")} AND m.deleted_at IS NULL
        """, {"asset_id": asset_id, "album_id": share["album_id"]}).first()
        if not miembro:
            return fail(NOT_FOUND, status=404)
        items, pagination = _page(conn, asset_id, None)
    return ok(data=items, message="Comentarios", pagination=pagination)
