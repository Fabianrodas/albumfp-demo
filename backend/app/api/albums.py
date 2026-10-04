from flask import Blueprint, request
from ..security.sessions import current_user_id, session_required

from ..activity import list_album_activity as fetch_album_activity, record_activity
from ..db.db import db_conn
from ..security.permissions import (is_album_collaborator, require_album_capability,
                                    require_album_collaborator, require_album_permission)
from ..search import MAX_QUERY_LENGTH, build_prefix_tsquery, normalize_search_text
from ..media.assets import add_membership, asset_albums, find_asset, in_album_sql, remove_membership
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

albums_bp = Blueprint("albums", __name__, url_prefix="/api/albums")


@albums_bp.get("")
@session_required
def list_albums():
    user_id = current_user_id()
    role_filter = (request.args.get("role") or "").strip().lower()
    raw_search = (request.args.get("q") or "").strip()
    if len(raw_search) > MAX_QUERY_LENGTH:
        return fail(f"q no puede superar {MAX_QUERY_LENGTH} caracteres", status=400)
    search = normalize_search_text(raw_search)
    page, per_page, offset = get_pagination_args(request)

    if role_filter and role_filter not in {"owner", "read", "write"}:
        return fail("role debe ser owner, read o write", status=400)

    where_clauses = [
        "a.active = TRUE",
        "(a.user_id = :user_id OR s.permission IS NOT NULL)",
    ]
    params = {"user_id": user_id, "limit": per_page, "offset": offset}

    if role_filter == "owner":
        where_clauses.append("a.user_id = :user_id")
    elif role_filter in {"read", "write"}:
        where_clauses.append("a.user_id <> :user_id")
        where_clauses.append("s.permission = :role_filter")
        params["role_filter"] = role_filter

    if search:
        where_clauses.append("a.search_vector @@ to_tsquery('simple', :search_tsquery)")
        params.update({
            "search": search,
            "search_prefix": f"{search}%",
            "search_tsquery": build_prefix_tsquery(search),
        })

    where_sql = " AND ".join(where_clauses)
    share_join = """
        LEFT JOIN LATERAL (
            SELECT sh.permission
            FROM album_shares sh
            WHERE sh.album_id = a.id
              AND sh.shared_with_user_id = :user_id
              AND sh.share_type = 'account'
              AND sh.active = TRUE
              AND (sh.expires_at IS NULL OR sh.expires_at > NOW())
            ORDER BY sh.created_at DESC
            LIMIT 1
        ) s ON TRUE
    """

    with db_conn() as conn:
        total = execute_safe(
            conn,
            f"""
            SELECT COUNT(*) AS total
            FROM albums a
            {share_join}
            WHERE {where_sql}
            """,
            params,
        ).mappings().first()["total"]

        rows = execute_safe(
            conn,
            f"""
            SELECT a.id, a.user_id, a.titulo, a.descripcion, a.is_private,
                   a.cover_media_id, a.created_at, a.updated_at,
                   CASE WHEN a.user_id = :user_id THEN 'owner' ELSE s.permission END AS role,
                   cm.file_type AS cover_file_type
            FROM albums a
            {share_join}
            LEFT JOIN assets cm
              ON cm.id = a.cover_media_id
             AND cm.deleted_at IS NULL
            WHERE {where_sql}
            ORDER BY
                {"CASE WHEN a.search_title = :search THEN 2 WHEN a.search_title LIKE :search_prefix THEN 1 ELSE 0 END DESC, ts_rank_cd(a.search_vector, to_tsquery('simple', :search_tsquery), 32) DESC," if search else ""}
                a.created_at DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).mappings().all()

    return ok(
        data=[dict(r) for r in rows],
        message="Álbumes",
        pagination=build_pagination_meta(page, per_page, total),
    )


@albums_bp.post("")
@session_required
def create_album():
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    titulo = (payload.get("titulo") or "").strip()
    descripcion = payload.get("descripcion")
    is_private = payload.get("is_private", True)

    if not titulo:
        return fail("titulo es obligatorio", status=400)
    if not isinstance(is_private, bool):
        return fail("is_private debe ser boolean", status=400)

    with db_conn() as conn:
        album = execute_safe(
            conn,
            """
            INSERT INTO albums (user_id, titulo, descripcion, is_private, active, created_by)
            VALUES (:user_id, :titulo, :descripcion, :is_private, TRUE, :created_by)
            RETURNING id, user_id, titulo, descripcion, is_private, active, created_at
            """,
            {
                "user_id": user_id,
                "titulo": titulo,
                "descripcion": descripcion,
                "is_private": is_private,
                "created_by": user_id,
            },
        ).mappings().first()

    data = dict(album)
    data.update({"role": "owner", "cover_media_id": None, "cover_file_type": None})
    return ok(data=data, message="Álbum creado", status=201)


@albums_bp.get("/<int:album_id>")
@session_required
def get_album(album_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        access = require_album_permission(conn, album_id, user_id, "read")

    if not access:
        return fail("No autorizado para ver este álbum", status=403)

    album = dict(access["album"])
    album["role"] = access["role"]
    album["capabilities"] = sorted(access["capabilities"])
    # Solo pinta el botón: la ruta de actividad vuelve a decidirlo sola.
    album["can_view_activity"] = is_album_collaborator(access)
    return ok(data=album, message="Detalle álbum")


@albums_bp.get("/<int:album_id>/activity")
@session_required
def list_album_activity(album_id: int):
    """Historial de colaboración del álbum (L12), más nuevo primero. Dueño o
    acceso de cuenta activo: quien solo LEE un álbum público no lo ve, y quien
    fue revocado lo pierde junto con el álbum. Un enlace público no llega aquí:
    esta ruta exige sesión."""
    user_id = current_user_id()
    page, per_page, offset = get_pagination_args(request)
    with db_conn() as conn:
        if not require_album_collaborator(conn, album_id, user_id):
            return fail("No autorizado para ver la actividad de este álbum", status=403)
        total, entries = fetch_album_activity(conn, album_id, limit=per_page, offset=offset)
    return ok(data=entries, message="Actividad del álbum", pagination=build_pagination_meta(page, per_page, total))


@albums_bp.get("/<int:album_id>/stats")
@session_required
def get_album_stats(album_id: int):
    user_id = current_user_id()

    with db_conn() as conn:
        access = require_album_permission(conn, album_id, user_id, "read")
        if not access:
            return fail("No autorizado para ver estadísticas", status=403)

        stats = execute_safe(
            conn,
            f"""
            SELECT
                COUNT(m.id) AS total_media,
                COUNT(*) FILTER (WHERE m.is_favorite = TRUE) AS total_favorites,
                COALESCE(SUM(mm.file_size), 0) AS total_file_size
            FROM assets m
            LEFT JOIN media_metadata mm ON mm.media_id = m.id
            WHERE {in_album_sql("album_id")}
              AND m.deleted_at IS NULL
            """,
            {"album_id": album_id},
        ).mappings().first()

    return ok(
        data={
            "album_id": album_id,
            "role": access["role"],
            "total_media": stats["total_media"],
            "total_favorites": stats["total_favorites"],
            "total_file_size": int(stats["total_file_size"] or 0),
        },
        message="Estadísticas del álbum",
    )


@albums_bp.patch("/<int:album_id>")
@session_required
def update_album(album_id: int):
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}

    with db_conn() as conn:
        access = require_album_capability(conn, album_id, user_id, "edit_album")
        if not access:
            return fail("No autorizado para modificar el álbum", status=403)

        updates = []
        params = {"album_id": album_id, "user_id": user_id}

        if "titulo" in payload:
            titulo = str(payload.get("titulo") or "").strip()
            if not titulo:
                return fail("titulo no puede estar vacío", status=400)
            updates.append("titulo = :titulo")
            params["titulo"] = titulo

        if "descripcion" in payload:
            updates.append("descripcion = :descripcion")
            params["descripcion"] = payload.get("descripcion")

        if "is_private" in payload:
            if not isinstance(payload.get("is_private"), bool):
                return fail("is_private debe ser boolean", status=400)
            updates.append("is_private = :is_private")
            params["is_private"] = payload["is_private"]

        cover_changed = False
        if "cover_media_id" in payload:
            cover_media_id = payload.get("cover_media_id")
            if cover_media_id is not None:
                if isinstance(cover_media_id, bool):
                    return fail("cover_media_id debe ser entero o null", status=400)
                try:
                    cover_media_id = int(cover_media_id)
                except (TypeError, ValueError):
                    return fail("cover_media_id debe ser entero o null", status=400)
                # La portada tiene que ser miembro del album (tambien lo exige
                # el esquema, fk_album_cover_member) y no estar en la papelera.
                cover = execute_safe(
                    conn,
                    f"""
                    SELECT m.id
                    FROM assets m
                    WHERE m.id = :cover_media_id
                      AND {in_album_sql("album_id")}
                      AND m.deleted_at IS NULL
                    """,
                    {"cover_media_id": cover_media_id, "album_id": album_id},
                ).mappings().first()
                if not cover:
                    return fail("La portada debe pertenecer a este álbum y estar activa", status=400)
            updates.append("cover_media_id = :cover_media_id")
            params["cover_media_id"] = cover_media_id
            # La portada que hay AHORA, bloqueada: dos PATCH a la vez no pueden
            # registrar los dos un cambio que solo uno hizo.
            anterior = execute_safe(
                conn, "SELECT cover_media_id FROM albums WHERE id = :album_id FOR UPDATE", {"album_id": album_id},
            ).mappings().first()["cover_media_id"]
            cover_changed = anterior != cover_media_id

        if not updates:
            return fail("No hay cambios válidos para aplicar", status=400)

        updates.extend(["updated_at = NOW()", "updated_by = :user_id"])
        album = execute_safe(
            conn,
            f"""
            UPDATE albums
            SET {', '.join(updates)}
            WHERE id = :album_id
            RETURNING id, user_id, titulo, descripcion, is_private, cover_media_id, updated_at
            """,
            params,
        ).mappings().first()

        if cover_changed:
            record_activity(conn, album_id, user_id, "cover_changed", subject_asset_id=cover_media_id,
                            metadata=None if cover_media_id is not None else {"cleared": True})

        cover_file_type = None
        if album["cover_media_id"] is not None:
            cover = execute_safe(
                conn,
                "SELECT file_type FROM assets WHERE id = :media_id AND deleted_at IS NULL",
                {"media_id": album["cover_media_id"]},
            ).mappings().first()
            if cover:
                cover_file_type = cover["file_type"]

    data = dict(album)
    data.update({"role": "owner", "cover_file_type": cover_file_type})
    return ok(data=data, message="Álbum actualizado")


@albums_bp.delete("/<int:album_id>")
@session_required
def delete_album(album_id: int):
    """Borra el album y sus pertenencias, nunca sus assets (L10A).

    Cada asset sigue en la biblioteca del dueño: en sus otros albumes, o suelto
    si este era el unico. Aqui no se borra ni una fila de `assets` ni un solo
    objeto de almacenamiento, asi que ya no hay intencion durable que registrar
    ni archivos que borrar despues del commit. Antes de L10A este DELETE caia
    en cascada sobre la media y borraba sus archivos: con un asset en varios
    albumes, eso lo habria destruido tambien en los demas.
    """
    user_id = current_user_id()

    with db_conn() as conn:
        access = require_album_permission(conn, album_id, user_id, "owner")
        if not access:
            return fail("Solo el dueño puede eliminar el álbum", status=403)

        # Pertenencias primero: la portada se vacia sola (fk_album_cover_member)
        # antes de que desaparezca el album. Comparticiones y desbloqueos caen
        # por cascada con el album.
        quitadas = execute_safe(
            conn,
            "DELETE FROM album_assets WHERE album_id = :album_id RETURNING asset_id",
            {"album_id": album_id},
        ).mappings().all()
        execute_safe(conn, "DELETE FROM albums WHERE id = :album_id", {"album_id": album_id})

    return ok(
        data={"removed_memberships": len(quitadas)},
        message="Álbum eliminado. Sus fotos y videos siguen en tu biblioteca.",
    )


@albums_bp.put("/<int:album_id>/assets/<int:asset_id>")
@session_required
def add_album_asset(album_id: int, asset_id: int):
    """Mete en otro album un recuerdo que el dueño ya tiene (L10B).

    Solo relacional: no se copia, mueve ni renombra ningun objeto, y la key
    sigue nombrando el album donde se subio. Es idempotente (PUT): repetirlo
    no duplica nada. Solo el dueño: gestionar pertenencias no es ninguna de
    las capacidades de un colaborador.
    """
    user_id = current_user_id()
    with db_conn() as conn:
        if not require_album_permission(conn, album_id, user_id, "owner"):
            return fail("Solo el dueño puede organizar sus álbumes", status=403)
        asset = find_asset(conn, asset_id)
        if not asset or asset["user_id"] != user_id:
            return fail("Recuerdo no encontrado", status=404)
        added = add_membership(conn, album_id=album_id, asset_id=asset_id, owner_id=user_id)
        # Repetir el PUT no añade nada, así que tampoco registra nada.
        if added:
            record_activity(conn, album_id, user_id, "asset_added", subject_asset_id=asset_id)
    return ok(
        data={"album_id": album_id, "asset_id": asset_id, "added": added},
        message="Añadido al álbum." if added else "Ya estaba en este álbum.",
    )


@albums_bp.delete("/<int:album_id>/assets/<int:asset_id>")
@session_required
def remove_album_asset(album_id: int, asset_id: int):
    """Saca un recuerdo de un album sin borrarlo (L10B).

    El asset, sus objetos y sus otras pertenencias no se tocan; si era el
    ultimo album, queda suelto en la biblioteca. Si era la portada, el esquema
    la vacia (fk_album_cover_member).
    """
    user_id = current_user_id()
    with db_conn() as conn:
        if not require_album_permission(conn, album_id, user_id, "owner"):
            return fail("Solo el dueño puede organizar sus álbumes", status=403)
        if not remove_membership(conn, album_id=album_id, asset_id=asset_id):
            return fail("Ese recuerdo no está en este álbum", status=404)
        record_activity(conn, album_id, user_id, "asset_removed", subject_asset_id=asset_id)
        quedan = len(asset_albums(conn, asset_id))
    return ok(
        data={"album_id": album_id, "asset_id": asset_id, "remaining_albums": quedan},
        message="Quitado del álbum. El recuerdo sigue en tu biblioteca.",
    )
