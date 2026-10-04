"""Lo personal que queda en Inicio: «En este día».

Desde F02 Inicio no duplica Biblioteca («Añadidos recientemente» vive en
`GET /api/media/library/recent`), Mis álbumes ni Compartido: el feed público
(Explorar) es su contenido principal. No llama proveedores.
"""
from datetime import date

from flask import Blueprint, request

from ..db.db import db_conn
from ..media.assets import ACTIVE_SCOPE_SQL, context_album_join
from ..security.sessions import current_user_id, session_required
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe


home_bp = Blueprint("personal_home", __name__, url_prefix="/api")

ON_THIS_DAY_PER_YEAR = 12


def parse_local_date(value: str | None, *, today: date | None = None) -> date:
    """Parse an exact browser calendar date without inventing timezone math."""
    if value is None or value == "":
        return today or date.today()
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise ValueError("local_date debe usar YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("local_date debe ser una fecha real") from exc
    if parsed.isoformat() != value:
        raise ValueError("local_date debe usar YYYY-MM-DD")
    return parsed


# Constante de módulo para que `scripts/profile_on_this_day.py` perfile esta
# misma sentencia y no una copia que pueda divergir.
ON_THIS_DAY_SQL = f"""
        WITH ranked AS (
            SELECT m.id, m.user_id, ctx.album_id, m.file_type, m.title,
                   m.caption, m.is_favorite, m.taken_at, m.created_at,
                   ctx.album_titulo,
                   EXTRACT(YEAR FROM m.taken_at)::INTEGER AS memory_year,
                   COUNT(*) OVER () AS total_count,
                   COUNT(*) OVER (
                       PARTITION BY EXTRACT(YEAR FROM m.taken_at)
                   ) AS year_total,
                   ROW_NUMBER() OVER (
                       PARTITION BY EXTRACT(YEAR FROM m.taken_at)
                       ORDER BY m.taken_at DESC, m.id DESC
                   ) AS year_rank
            FROM assets m {context_album_join()}
            WHERE m.user_id = :user_id
              AND {ACTIVE_SCOPE_SQL}
              AND m.deleted_at IS NULL
              AND m.archived_at IS NULL
              AND m.taken_at IS NOT NULL
              AND EXTRACT(MONTH FROM m.taken_at) = :local_month
              AND EXTRACT(DAY FROM m.taken_at) = :local_day
              AND EXTRACT(YEAR FROM m.taken_at) < :local_year
        )
        SELECT id, user_id, album_id, file_type, title, caption, is_favorite,
               taken_at, created_at, album_titulo, memory_year,
               (:local_year - memory_year) AS years_ago,
               year_total, total_count
        FROM ranked
        WHERE year_rank <= :per_year_limit
        ORDER BY memory_year DESC, taken_at DESC, id DESC
"""


def _memory_rows(conn, user_id: int, local_date: date):
    return execute_safe(
        conn,
        ON_THIS_DAY_SQL,
        {
            "user_id": user_id,
            "local_year": local_date.year,
            "local_month": local_date.month,
            "local_day": local_date.day,
            "per_year_limit": ON_THIS_DAY_PER_YEAR,
        },
    ).mappings().all()


@home_bp.get("/home")
@session_required
def personal_home():
    user_id = current_user_id()
    try:
        local_date = parse_local_date(request.args.get("local_date"))
    except ValueError as exc:
        return fail(str(exc), status=400)

    with db_conn() as conn:
        memories = [dict(row) for row in _memory_rows(conn, user_id, local_date)]

    total = int(memories[0]["total_count"]) if memories else 0
    for memory in memories:
        memory.pop("total_count", None)

    return ok(
        data={
            "local_date": local_date.isoformat(),
            "on_this_day": {
                "items": memories,
                "total": total,
                "per_year_limit": ON_THIS_DAY_PER_YEAR,
            },
        },
        message="Inicio personal",
    )
