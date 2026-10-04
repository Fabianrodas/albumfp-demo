"""Álbumes inteligentes (L11): búsquedas guardadas del dueño.

Un álbum inteligente guarda título, descripción y un objeto de filtros con las
mismas claves que la búsqueda global. No es un álbum: no es un destino de
subida, de pertenencias ni de compartir, y no guarda assets. Sus resultados se
calculan cada vez con `run_media_search`, el mismo ejecutor de la búsqueda
global, así que lo que aparece es exactamente lo que esa búsqueda devolvería.

Todo es solo del dueño: cada consulta filtra por `user_id = :user_id`, y un id
ajeno responde 404 igual que uno inexistente. Los ids de álbum y etiqueta que
guarda una definición se revalidan contra el dueño en cada lectura: si ya no
existen, la ejecución falla cerrada (409) en vez de ampliar el resultado.
"""
import json

from flask import Blueprint, request

from ..db.db import db_conn
from ..search import normalize_saved_filters, run_media_search, saved_filters_to_search
from ..security.sessions import current_user_id, session_required
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe

smart_albums_bp = Blueprint("smart_albums", __name__, url_prefix="/api/smart-albums")

MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2000
STALE_CODE = "smart_album_stale_reference"
STALE_MESSAGE = "Este álbum inteligente usa un filtro que ya no existe. Edita sus filtros para continuar."
NOT_FOUND = "Álbum inteligente no encontrado"
_COLUMNS = "id, titulo, descripcion, filters, created_at, updated_at"


def _title(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("titulo es obligatorio")
    titulo = raw.strip()
    if len(titulo) > MAX_TITLE_LENGTH:
        raise ValueError(f"titulo no puede superar {MAX_TITLE_LENGTH} caracteres")
    return titulo


def _description(raw):
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("descripcion debe ser texto")
    descripcion = raw.strip()
    if len(descripcion) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"descripcion no puede superar {MAX_DESCRIPTION_LENGTH} caracteres")
    return descripcion or None


def _ref_id(filters, key: str):
    value = filters.get(key) if isinstance(filters, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _references(conn, user_id: int, rows) -> dict:
    """{smart_id: {"album": {...}|None, "tag": {...}|None}} en dos consultas,
    sea cual sea el tamaño de la página. Solo resuelve lo que es del dueño
    y sigue activo: lo demás queda en None, y eso es lo que marca un filtro roto."""
    album_ids = sorted({i for r in rows if (i := _ref_id(r["filters"], "album_id"))})
    tag_ids = sorted({i for r in rows if (i := _ref_id(r["filters"], "tag_id"))})
    albums = {a["id"]: dict(a) for a in execute_safe(
        conn,
        "SELECT a.id, a.titulo FROM albums a WHERE a.id = ANY(:ids) AND a.user_id = :user_id AND a.active = TRUE",
        {"ids": album_ids, "user_id": user_id},
    ).mappings().all()} if album_ids else {}
    tags = {t["id"]: dict(t) for t in execute_safe(
        conn,
        "SELECT id, name FROM tags WHERE id = ANY(:ids) AND owner_id = :user_id",
        {"ids": tag_ids, "user_id": user_id},
    ).mappings().all()} if tag_ids else {}
    return {r["id"]: {"album": albums.get(_ref_id(r["filters"], "album_id")),
                      "tag": tags.get(_ref_id(r["filters"], "tag_id"))} for r in rows}


def _stale(filters, refs: dict) -> list[str]:
    stale = []
    if isinstance(filters, dict) and "album_id" in filters and refs["album"] is None:
        stale.append("album_id")
    if isinstance(filters, dict) and "tag_id" in filters and refs["tag"] is None:
        stale.append("tag_id")
    return stale


def _shape(row, refs: dict) -> dict:
    return {**dict(row), "references": refs, "stale_references": _stale(row["filters"], refs)}


def _load(conn, smart_id: int, user_id: int):
    return execute_safe(
        conn,
        f"SELECT {_COLUMNS} FROM smart_albums WHERE id = :id AND user_id = :user_id",
        {"id": smart_id, "user_id": user_id},
    ).mappings().first()


def _validated_filters(conn, user_id: int, raw) -> dict:
    """Normaliza y comprueba que cada id guardado sea del dueño hoy."""
    filters = normalize_saved_filters(raw)
    stale = _stale(filters, _references(conn, user_id, [{"id": 0, "filters": filters}])[0])
    if stale:
        raise ValueError("El filtro usa un álbum o una etiqueta que no existe o no es tuyo")
    return filters


@smart_albums_bp.get("")
@session_required
def list_smart_albums():
    user_id = current_user_id()
    page, per_page, offset = get_pagination_args(request)
    with db_conn() as conn:
        total = execute_safe(
            conn, "SELECT COUNT(*) AS total FROM smart_albums WHERE user_id = :user_id", {"user_id": user_id},
        ).mappings().first()["total"]
        rows = execute_safe(
            conn,
            f"""SELECT {_COLUMNS} FROM smart_albums WHERE user_id = :user_id
                ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset""",
            {"user_id": user_id, "limit": per_page, "offset": offset},
        ).mappings().all()
        refs = _references(conn, user_id, rows)
    return ok(data=[_shape(r, refs[r["id"]]) for r in rows], message="Álbumes inteligentes",
              pagination=build_pagination_meta(page, per_page, total))


@smart_albums_bp.post("")
@session_required
def create_smart_album():
    user_id = current_user_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return fail("El cuerpo debe ser un objeto JSON", status=400)
    with db_conn() as conn:
        try:
            titulo = _title(payload.get("titulo"))
            descripcion = _description(payload.get("descripcion"))
            filters = _validated_filters(conn, user_id, payload.get("filters"))
        except ValueError as exc:
            return fail(str(exc), status=400)
        row = execute_safe(
            conn,
            f"""INSERT INTO smart_albums (user_id, titulo, descripcion, filters)
                VALUES (:user_id, :titulo, :descripcion, CAST(:filters AS jsonb))
                RETURNING {_COLUMNS}""",
            {"user_id": user_id, "titulo": titulo, "descripcion": descripcion, "filters": json.dumps(filters)},
        ).mappings().first()
        refs = _references(conn, user_id, [row])
    return ok(data=_shape(row, refs[row["id"]]), message="Álbum inteligente creado", status=201)


@smart_albums_bp.get("/<int:smart_id>")
@session_required
def get_smart_album(smart_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        row = _load(conn, smart_id, user_id)
        if not row:
            return fail(NOT_FOUND, status=404)
        refs = _references(conn, user_id, [row])
    return ok(data=_shape(row, refs[row["id"]]))


@smart_albums_bp.patch("/<int:smart_id>")
@session_required
def update_smart_album(smart_id: int):
    user_id = current_user_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not {"titulo", "descripcion", "filters"} & set(payload):
        return fail("Indica titulo, descripcion o filters", status=400)
    with db_conn() as conn:
        current = _load(conn, smart_id, user_id)
        if not current:
            return fail(NOT_FOUND, status=404)
        try:
            titulo = _title(payload["titulo"]) if "titulo" in payload else current["titulo"]
            descripcion = _description(payload["descripcion"]) if "descripcion" in payload else current["descripcion"]
            # La definición se reemplaza entera y se valida entera: nunca se
            # guarda una mezcla de claves viejas y nuevas.
            filters = (_validated_filters(conn, user_id, payload["filters"])
                       if "filters" in payload else current["filters"])
        except ValueError as exc:
            return fail(str(exc), status=400)
        row = execute_safe(
            conn,
            f"""UPDATE smart_albums
                SET titulo = :titulo, descripcion = :descripcion, filters = CAST(:filters AS jsonb),
                    updated_at = NOW()
                WHERE id = :id AND user_id = :user_id
                RETURNING {_COLUMNS}""",
            {"id": smart_id, "user_id": user_id, "titulo": titulo, "descripcion": descripcion,
             "filters": json.dumps(filters)},
        ).mappings().first()
        refs = _references(conn, user_id, [row])
    return ok(data=_shape(row, refs[row["id"]]), message="Álbum inteligente actualizado")


@smart_albums_bp.delete("/<int:smart_id>")
@session_required
def delete_smart_album(smart_id: int):
    """Borra solo la definición guardada: ninguna foto, video, álbum ni archivo."""
    user_id = current_user_id()
    with db_conn() as conn:
        deleted = execute_safe(
            conn,
            "DELETE FROM smart_albums WHERE id = :id AND user_id = :user_id RETURNING id",
            {"id": smart_id, "user_id": user_id},
        ).first()
    if not deleted:
        return fail(NOT_FOUND, status=404)
    return ok(message="Álbum inteligente eliminado")


@smart_albums_bp.get("/<int:smart_id>/media")
@session_required
def list_smart_album_media(smart_id: int):
    """Calcula los resultados ahora, con la búsqueda global del dueño."""
    user_id = current_user_id()
    page, per_page, offset = get_pagination_args(request)
    with db_conn() as conn:
        row = _load(conn, smart_id, user_id)
        if not row:
            return fail(NOT_FOUND, status=404)
        try:
            filters = saved_filters_to_search(row["filters"])
        except ValueError:
            return fail(STALE_MESSAGE, status=409, code=STALE_CODE)
        if _stale(row["filters"], _references(conn, user_id, [row])[row["id"]]):
            return fail(STALE_MESSAGE, status=409, code=STALE_CODE)
        total, rows = run_media_search(conn, filters, user_id, limit=per_page, offset=offset)
    return ok(data=rows, message="Resultados", pagination=build_pagination_meta(page, per_page, total))
