from flask import Blueprint, request
from ..security.sessions import current_user_id, session_required
from ..db.db import db_conn
from ..domain.rules import validate_tag_ids
from ..media.assets import require_asset_capability
from ..security.permissions import require_album_capability, require_album_permission
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

tags_bp = Blueprint("tags", __name__, url_prefix="/api")


def link_tags(conn, media_id: int, tag_ids: list[int]) -> None:
    """Asocia varias etiquetas a una foto en UNA sentencia.

    Estaba copiado en dos sitios (subir media y asignar tags), los dos
    insertando de uno en uno dentro de un bucle. `unnest` hace lo mismo con un
    solo viaje a la base y deja el `ON CONFLICT` donde estaba.
    """
    if not tag_ids:
        return
    execute_safe(
        conn,
        """
        INSERT INTO media_tags (media_id, tag_id)
        SELECT :media_id, tag_id FROM unnest(CAST(:tag_ids AS INTEGER[])) AS tag_id
        ON CONFLICT DO NOTHING
        """,
        {"media_id": media_id, "tag_ids": list(tag_ids)},
    )


def tags_belong_to(conn, owner_id: int, tag_ids: list[int]) -> bool:
    """Cada etiqueta debe pertenecer al dueno del album que se esta tocando.

    Es la frontera de S00 y el unico sitio que la comprueba: sin `owner_id` en
    el WHERE, cualquier cuenta podia pegar a sus fotos una etiqueta ajena con
    solo adivinar su id, y de paso confirmar que ese id existe.
    """
    if not tag_ids:
        return True
    row = execute_safe(
        conn,
        "SELECT COUNT(*) AS total FROM tags WHERE owner_id = :owner_id AND id = ANY(:tag_ids)",
        {"owner_id": owner_id, "tag_ids": list(tag_ids)},
    ).mappings().first()
    return int(row["total"] or 0) == len(tag_ids)


def _album_vocabulary(conn, album_id: int, user_id: int):
    """Resuelve de quien es el vocabulario que toca usar en este album.

    Devuelve `(owner_id, solo_las_de_este_album)`, o `(None, False)` si esta
    cuenta no puede ni ver el album.

    Listar pide **read**, no `organize`: el desplegable de filtrar por tag lo
    usa tambien quien solo mira. Lo que cambia es el alcance — el dueno ve su
    vocabulario entero, y cualquier otro solo las etiquetas ya puestas en ESE
    album, que son las que de todos modos vienen con cada foto. Asi compartir
    una carpeta no entrega el indice de temas de toda la biblioteca del dueno.
    """
    access = require_album_permission(conn, album_id, user_id, "read")
    if not access:
        return None, False
    owner_id = access["album"]["user_id"]
    return owner_id, owner_id != user_id


@tags_bp.get("/tags")
@session_required
def list_tags():
    user_id = current_user_id()
    q = (request.args.get("q") or "").strip().lower()
    album_id_raw = (request.args.get("album_id") or "").strip()
    page, per_page, offset = get_pagination_args(request)

    if album_id_raw:
        try:
            album_id = int(album_id_raw)
        except ValueError:
            return fail("album_id debe ser entero", status=400)
    else:
        album_id = None

    params = {"limit": per_page, "offset": offset, "owner_id": user_id}
    where = ["owner_id = :owner_id"]
    if q:
        where.append("LOWER(name) LIKE :q")
        params["q"] = f"%{q}%"

    with db_conn() as conn:
        if album_id is not None:
            owner_id, solo_del_album = _album_vocabulary(conn, album_id, user_id)
            if owner_id is None:
                return fail("No autorizado para ver este álbum", status=403)
            params["owner_id"] = owner_id
            if solo_del_album:
                params["album_id"] = album_id
                where.append(
                    "EXISTS (SELECT 1 FROM media_tags mt JOIN assets m ON m.id = mt.media_id"
                    " JOIN album_assets aa ON aa.asset_id = m.id"
                    " WHERE mt.tag_id = tags.id AND aa.album_id = :album_id AND m.deleted_at IS NULL)"
                )
        where_sql = " AND ".join(where)

        total = execute_safe(
            conn,
            f"""
            SELECT COUNT(*) AS total
            FROM tags
            WHERE {where_sql}
            """,
            params,
        ).mappings().first()["total"]

        rows = execute_safe(
            conn,
            f"""
            SELECT id, name, created_at, created_by
            FROM tags
            WHERE {where_sql}
            ORDER BY name ASC
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).mappings().all()

    return ok(
        data=[dict(r) for r in rows],
        message="Tags",
        pagination=build_pagination_meta(page, per_page, total),
    )


@tags_bp.post("/tags")
@session_required
def create_tag():
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip().lower()

    if not name:
        return fail("name es obligatorio", status=400)

    album_id_raw = payload.get("album_id")
    owner_id = user_id

    with db_conn() as conn:
        if album_id_raw not in (None, ""):
            try:
                album_id = int(album_id_raw)
            except (TypeError, ValueError):
                return fail("album_id debe ser entero", status=400)
            # Crear si exige `organize`, no solo `read`: quien pasa por aqui
            # esta escribiendo en el vocabulario del dueno. Y la etiqueta nace
            # DEL dueno -- si naciera del colaborador, asignarla fallaria acto
            # seguido por no pertenecer al vocabulario del album.
            access = require_album_capability(conn, album_id, user_id, "organize")
            if not access:
                return fail("No autorizado para organizar este álbum", status=403)
            owner_id = access["album"]["user_id"]

        tag = execute_safe(
            conn,
            """
            INSERT INTO tags (name, created_by, owner_id)
            VALUES (:name, :created_by, :owner_id)
            ON CONFLICT (owner_id, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, name, created_at, created_by
            """,
            {"name": name, "created_by": user_id, "owner_id": owner_id},
        ).mappings().first()

    return ok(data=dict(tag), message="Tag disponible", status=201)


@tags_bp.post("/media/<int:media_id>/tags")
@session_required
def assign_tags(media_id: int):
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    try:
        tag_ids = validate_tag_ids(payload.get("tag_ids") or [])
    except ValueError as exc:
        return fail(str(exc), status=400)

    with db_conn() as conn:
        asset, access = require_asset_capability(conn, media_id, user_id, "organize")
        if not asset:
            return fail("Media no encontrada", status=404)
        if not access:
            return fail("No autorizado para etiquetar", status=403)

        # El vocabulario es el del dueño del asset (el mismo que el de sus albumes).
        if not tags_belong_to(conn, access["owner_id"], tag_ids):
            return fail("Uno o más tags no existen", status=400)

        link_tags(conn, media_id, tag_ids)

    return ok(message="Tags asignados")


@tags_bp.delete("/media/<int:media_id>/tags/<int:tag_id>")
@session_required
def unassign_tag(media_id: int, tag_id: int):
    user_id = current_user_id()

    with db_conn() as conn:
        asset, access = require_asset_capability(conn, media_id, user_id, "organize")
        if not asset:
            return fail("Media no encontrada", status=404)
        if not access:
            return fail("No autorizado para quitar tags", status=403)

        execute_safe(
            conn,
            """
            DELETE FROM media_tags
            WHERE media_id = :media_id
              AND tag_id = :tag_id
            """,
            {"media_id": media_id, "tag_id": tag_id},
        )

    return ok(message="Tag removido")
