"""Exportación de la propia biblioteca (L13).

`GET /api/export/summary` cuenta lo que se exportaría y cuánto cupo queda, sin
gastar nada. `GET /api/export/download` genera el ZIP en streaming: la sesión
decide de quién es la biblioteca, así que no hay id ni token que manipular
para pedir la de otra cuenta, y no existe ningún artefacto intermedio que
guardar, expirar ni limpiar.
"""
import re
from datetime import datetime

from flask import Blueprint, Response, request

from ..db.db import db_conn
from ..library_export import build_manifest, parse_export_options, stream_export
from ..security.rate_limit import peek, try_consume
from ..security.sessions import current_user_id, session_required
from ..storage.backends import get_storage_backend
from ..utils.env import int_env
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

export_bp = Blueprint("export", __name__, url_prefix="/api/export")

SCOPE = "library_export"
WINDOW_SECONDS = 3600


def _limit() -> int:
    # Cada descarga ocupa un hilo del servidor mientras dura; el cupo por cuenta
    # es lo que impide que una sola cuenta los acapare.
    return int_env("RATE_LIMIT_EXPORT_PER_HOUR", 3)


def _sizes(conn, user_id: int):
    return execute_safe(conn, """
        SELECT COUNT(*) AS assets, COALESCE(SUM(mm.file_size), 0) AS total_bytes,
               COALESCE(MAX(mm.file_size), 0) AS largest_bytes
        FROM assets m LEFT JOIN media_metadata mm ON mm.media_id = m.id
        WHERE m.user_id = :user_id AND m.deleted_at IS NULL
    """, {"user_id": user_id}).mappings().first()


@export_bp.get("/summary")
@session_required
def export_summary():
    user_id = current_user_id()
    try:
        parse_export_options(request.args)
    except ValueError as exc:
        return fail(str(exc), status=400)
    with db_conn() as conn:
        sizes = _sizes(conn, user_id)
        albums = execute_safe(conn, "SELECT COUNT(*) FROM albums WHERE user_id = :user_id",
                              {"user_id": user_id}).scalar()
        remaining, retry_after = peek(conn, SCOPE, f"user:{user_id}", _limit(), WINDOW_SECONDS)
    return ok(data={
        "assets": sizes["assets"], "albums": albums, "total_bytes": int(sizes["total_bytes"]),
        "largest_bytes": int(sizes["largest_bytes"]), "allowed": remaining > 0, "remaining": remaining,
        "retry_after": retry_after,
    })


@export_bp.get("/download")
@session_required
def download_export():
    """Todo lo que puede fallar con un mensaje claro falla ANTES de enviar el
    primer byte: opciones, cupo y espacio de trabajo. A partir de ahí, un fallo
    del almacenamiento corta la conexión y el navegador marca la descarga como
    fallida; nunca se entrega un ZIP a medias como si estuviera completo."""
    user_id = current_user_id()
    try:
        options = parse_export_options(request.args)
    except ValueError as exc:
        return fail(str(exc), status=400)

    generated_at = datetime.now().replace(microsecond=0)
    with db_conn() as conn:
        allowed, retry_after = try_consume(conn, SCOPE, f"user:{user_id}", _limit(), WINDOW_SECONDS)
        if not allowed:
            respuesta, status = fail("Ya pediste varias exportaciones hace poco. Inténtalo más tarde.",
                                     status=429, code="rate_limited")
            respuesta.headers["Retry-After"] = str(retry_after)
            return respuesta, status
        manifest, entries = build_manifest(conn, user_id, options, generated_at)

    username = re.sub(r"[^A-Za-z0-9_-]+", "_", manifest["account"]["username"]) or "cuenta"
    filename = f"albumfp-export-{username}-{generated_at:%Y%m%d-%H%M}.zip"
    return Response(
        stream_export(entries, manifest, get_storage_backend()),
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
