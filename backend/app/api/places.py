"""Vista `Lugares`: agrupa los recuerdos del usuario por donde ocurrieron.

Usa lo que la fase 03 ya guardo en `media_context`. No llama a ningun
proveedor ni agrega ninguna tabla: es pura agregacion SQL sobre datos que ya
existen.
"""
from flask import Blueprint, request
from ..security.sessions import current_user_id, session_required

from ..db.db import db_conn
from ..media.assets import ACTIVE_SCOPE_SQL, context_album_join
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

places_bp = Blueprint("places", __name__, url_prefix="/api")

# Un lugar es el grupo (country_code, locality, region): NULL agrupa junto en
# SQL igual que cualquier otro valor, asi que una foto sin ciudad cae sola en
# region/pais sin necesidad de inventarle un nombre.
_GROUPED = f"""
WITH located AS (
    SELECT m.id, m.file_type, m.created_at,
           mc.country_code, mc.country_name, mc.locality, mc.region
    FROM assets m
    JOIN media_context mc ON mc.media_id = m.id
    WHERE m.user_id = :user_id AND {ACTIVE_SCOPE_SQL} AND m.deleted_at IS NULL
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY country_code, locality, region
               ORDER BY (file_type = 'image') DESC, created_at DESC
           ) AS rn
    FROM located
)
"""


@places_bp.get("/places")
@session_required
def list_places():
    """Solo cuenta albumes que el usuario posee: lo compartido con el, o por
    el, no se mezcla en sus propios totales de lugar."""
    user_id = current_user_id()
    with db_conn() as conn:
        rows = execute_safe(
            conn,
            _GROUPED + """
            SELECT country_code, country_name, locality, region,
                   COUNT(*) AS media_count,
                   MAX(CASE WHEN rn = 1 THEN id END) AS cover_media_id,
                   MAX(CASE WHEN rn = 1 THEN file_type END) AS cover_file_type
            FROM ranked
            GROUP BY country_code, country_name, locality, region
            ORDER BY media_count DESC, country_name ASC, locality ASC
            """,
            {"user_id": user_id},
        ).mappings().all()
    return ok(data=[dict(r) for r in rows], message="Lugares")


@places_bp.get("/places/media")
@session_required
def list_place_media():
    """La media de un grupo concreto de /places. Los tres parametros deben
    coincidir exactamente con los que devolvio ese grupo, NULL incluido: por
    eso la comparacion es `IS NOT DISTINCT FROM`, no `=`."""
    user_id = current_user_id()
    country_code = request.args.get("country_code") or None
    locality = request.args.get("locality") or None
    region = request.args.get("region") or None
    if country_code is None and locality is None and region is None:
        return fail("Indica al menos un lugar", status=400)

    page, per_page, offset = get_pagination_args(request)
    params = {
        "user_id": user_id, "country_code": country_code, "locality": locality,
        "region": region, "limit": per_page, "offset": offset,
    }
    where = f"""
        m.user_id = :user_id AND {ACTIVE_SCOPE_SQL} AND m.deleted_at IS NULL
        AND mc.country_code IS NOT DISTINCT FROM :country_code
        AND mc.locality IS NOT DISTINCT FROM :locality
        AND mc.region IS NOT DISTINCT FROM :region
    """
    with db_conn() as conn:
        total = execute_safe(
            conn,
            f"""
            SELECT COUNT(*) AS total
            FROM assets m
            JOIN media_context mc ON mc.media_id = m.id
            WHERE {where}
            """,
            params,
        ).mappings().first()["total"]
        rows = execute_safe(
            conn,
            f"""
            SELECT m.id, m.user_id, ctx.album_id, m.file_type, m.title, m.caption,
                   m.is_favorite, m.taken_at, m.created_at, m.created_by, m.deleted_at
            FROM assets m
            {context_album_join()}
            JOIN media_context mc ON mc.media_id = m.id
            WHERE {where}
            ORDER BY m.created_at DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).mappings().all()
    return ok(data=[dict(r) for r in rows], message="Media del lugar", pagination=build_pagination_meta(page, per_page, total))
