"""Directorio de usuarios y perfiles públicos.

Todo lo que se expone aquí es público por definición: nombre, usuario, foto y
álbumes marcados como no privados. Ningún endpoint devuelve álbumes privados,
contenido en papelera ni conteos que los incluyan.
"""

from flask import Blueprint, request
from ..security.sessions import current_user_id, session_required

from ..db.db import db_conn
from ..media.assets import in_active_album_sql
from ..storage.media_delivery import deliver_stored_file
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

users_bp = Blueprint("users", __name__, url_prefix="/api/users")

_PUBLIC_ALBUMS_COUNT = """
    SELECT COUNT(*) FROM albums a
     WHERE a.user_id = u.id AND a.is_private = FALSE AND a.active = TRUE
"""


@users_bp.get("")
@session_required
def list_users():
    viewer_id = current_user_id()
    search = (request.args.get("q") or "").strip().lower()
    page, per_page, offset = get_pagination_args(request)

    where = ["u.active = TRUE", "u.id <> :viewer_id"]
    params = {"viewer_id": viewer_id, "limit": per_page, "offset": offset}
    if search:
        where.append("(LOWER(u.username) LIKE :q OR LOWER(COALESCE(u.full_name, '')) LIKE :q)")
        params["q"] = f"%{search}%"
    where_sql = " AND ".join(where)

    with db_conn() as conn:
        total = execute_safe(
            conn,
            f"SELECT COUNT(*) AS total FROM users u WHERE {where_sql}",
            params,
        ).mappings().first()["total"]

        rows = execute_safe(
            conn,
            f"""
            SELECT u.id, u.username, u.full_name, u.avatar_path, u.created_at,
                   ({_PUBLIC_ALBUMS_COUNT}) AS public_albums
            FROM users u
            WHERE {where_sql}
            ORDER BY public_albums DESC, u.username ASC
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).mappings().all()

    data = [
        {
            "id": row["id"],
            "username": row["username"],
            "full_name": row["full_name"] or row["username"],
            "has_avatar": bool(row["avatar_path"]),
            "created_at": row["created_at"],
            "public_albums": row["public_albums"],
        }
        for row in rows
    ]
    return ok(data=data, message="Usuarios", pagination=build_pagination_meta(page, per_page, total))


# Declarada antes de "/<string:username>" para que un id numérico no se lea
# como nombre de usuario.
#
# Deliberadamente pública, sin JWT: una etiqueta <img> no pasa por el
# interceptor de Angular, así que nunca podría mandar el token. Lo único que
# expone es la foto que el usuario eligió para su perfil público, que ya se ve
# en el directorio y en el feed. El contenido de los álbumes sigue exigiendo
# permiso; esto no.
@users_bp.get("/<int:user_id>/avatar")
def read_user_avatar(user_id: int):
    with db_conn() as conn:
        row = execute_safe(
            conn,
            "SELECT avatar_path FROM users WHERE id = :user_id AND active = TRUE",
            {"user_id": user_id},
        ).mappings().first()

    if not row or not row["avatar_path"]:
        return fail("Este usuario no tiene foto de perfil", status=404)

    response = deliver_stored_file(row["avatar_path"])
    if response is None:
        return fail("La foto ya no existe en almacenamiento", status=404)
    return response


@users_bp.get("/<string:username>")
@session_required
def get_user_profile(username: str):
    with db_conn() as conn:
        row = execute_safe(
            conn,
            f"""
            SELECT u.id, u.username, u.full_name, u.avatar_path, u.created_at,
                   ({_PUBLIC_ALBUMS_COUNT}) AS public_albums,
                   (SELECT COUNT(*)
                      FROM assets m
                     WHERE m.user_id = u.id
                       AND m.deleted_at IS NULL
                       AND {in_active_album_sql("AND ia.is_private = FALSE")}) AS public_media
            FROM users u
            WHERE LOWER(u.username) = :username AND u.active = TRUE
            """,
            {"username": username.strip().lower()},
        ).mappings().first()

    if not row:
        return fail("No encontramos a esa persona", status=404)

    return ok(
        data={
            "id": row["id"],
            "username": row["username"],
            "full_name": row["full_name"] or row["username"],
            "has_avatar": bool(row["avatar_path"]),
            "created_at": row["created_at"],
            "public_albums": row["public_albums"],
            "public_media": row["public_media"],
        },
        message="Perfil público",
    )


@users_bp.get("/<string:username>/albums")
@session_required
def list_user_public_albums(username: str):
    page, per_page, offset = get_pagination_args(request)
    params = {"username": username.strip().lower(), "limit": per_page, "offset": offset}

    with db_conn() as conn:
        owner = execute_safe(
            conn,
            "SELECT id FROM users WHERE LOWER(username) = :username AND active = TRUE",
            {"username": params["username"]},
        ).mappings().first()
        if not owner:
            return fail("No encontramos a esa persona", status=404)

        total = execute_safe(
            conn,
            """
            SELECT COUNT(*) AS total FROM albums
            WHERE user_id = :owner_id AND is_private = FALSE AND active = TRUE
            """,
            {"owner_id": owner["id"]},
        ).mappings().first()["total"]

        rows = execute_safe(
            conn,
            """
            SELECT a.id, a.titulo, a.descripcion, a.cover_media_id, a.created_at,
                   cm.file_type AS cover_file_type
            FROM albums a
            LEFT JOIN assets cm
              ON cm.id = a.cover_media_id
             AND cm.deleted_at IS NULL
            WHERE a.user_id = :owner_id AND a.is_private = FALSE AND a.active = TRUE
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT :limit OFFSET :offset
            """,
            {"owner_id": owner["id"], "limit": per_page, "offset": offset},
        ).mappings().all()

    return ok(
        data=[dict(row) for row in rows],
        message="Álbumes públicos",
        pagination=build_pagination_meta(page, per_page, total),
    )
