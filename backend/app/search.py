"""Validación y SQL parametrizado de la búsqueda avanzada.

PostgreSQL indexa texto ya normalizado por AlbumFP. La misma normalización se
aplica aquí a la consulta para que mayúsculas y acentos no cambien el resultado
sin depender de extensiones del servidor como ``unaccent`` o ``pg_trgm``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping

from .media.assets import ACTIVE_SCOPE_SQL, context_album_join, in_active_album_sql
from .utils.sql_security import execute_safe


MAX_QUERY_LENGTH = 200
MAX_PLACE_LENGTH = 120
MAX_QUERY_TERMS = 12
SEARCH_PARAM_NAMES = frozenset({
    "q", "media_type", "date_from", "date_to", "year", "album_id",
    "favorite", "tag_id", "place", "sort", "direction", "page", "per_page",
    "archived",
})
ARCHIVED_MODES = frozenset({"only", "exclude"})
SORTS = frozenset({"relevance", "taken_at", "created_at", "title"})
DIRECTIONS = frozenset({"asc", "desc"})

# Contrato deliberadamente explicito y replicable con PostgreSQL `translate`.
# No usamos `lower` ni NFKD: sus tablas/collation difieren entre runtimes (por
# ejemplo İ, K y ǅ), y NFKD ademas altera letras como й.
_CASE_PAIRS = (
    ("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"),
    ("ÆŒKİǅ", "æœkiǆ"),
    (
        "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ),
)
_ACCENT_GROUPS = {
    "a": "áàäâãåāăąÁÀÄÂÃÅĀĂĄ",
    "c": "çćčÇĆČ",
    "d": "ďđĎĐ",
    "e": "éèëêēĕėęěÉÈËÊĒĔĖĘĚ",
    "g": "ğĞ",
    "i": "íìïîīĭįıÍÌÏÎĪĬĮ",
    "l": "łŁ",
    "n": "ñńňÑŃŇ",
    "o": "óòöôõøōŏőÓÒÖÔÕØŌŎŐ",
    "r": "řŘ",
    "s": "śšşŚŠŞ",
    "t": "ťŤ",
    "u": "úùüûūŭůűųÚÙÜÛŪŬŮŰŲ",
    "y": "ýÿÝŸ",
    "z": "žźżŽŹŻ",
}
_COMBINING_MARKS = "\u0300\u0301\u0302\u0303\u0304\u0306\u0307\u0308\u030a\u030b\u030c\u0327\u0328"
_NORMALIZE_TRANSLATION = str.maketrans({
    **{source: target for sources, targets in _CASE_PAIRS for source, target in zip(sources, targets)},
    **{char: replacement for replacement, chars in _ACCENT_GROUPS.items() for char in chars},
    **{char: None for char in _COMBINING_MARKS},
})


def normalize_search_text(value: str | None) -> str:
    """Replica el contrato SQL: minúsculas, tabla explícita y separadores."""
    translated = str(value or "").translate(_NORMALIZE_TRANSLATION)
    return " ".join(re.sub(r"[^\w]+", " ", translated, flags=re.UNICODE).split())


def _positive_int(raw, name: str) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} debe ser un entero positivo") from exc
    if isinstance(raw, bool) or value <= 0:
        raise ValueError(f"{name} debe ser un entero positivo")
    return value


def _date(raw, name: str) -> date | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ValueError(f"{name} debe ser una fecha YYYY-MM-DD válida") from exc


def build_prefix_tsquery(value: str) -> str | None:
    terms = value.split()[:MAX_QUERY_TERMS]
    return " & ".join(f"{term}:*" for term in terms) or None


@dataclass(frozen=True)
class SearchFilters:
    query: str | None = None
    tsquery: str | None = None
    media_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    year: int | None = None
    album_id: int | None = None
    favorite: bool | None = None
    tag_id: int | None = None
    place: str | None = None
    place_tsquery: str | None = None
    sort: str = "created_at"
    direction: str = "desc"
    archived: str | None = None


@dataclass(frozen=True)
class BuiltSearch:
    where_sql: str
    params: dict
    order_sql: str


def parse_search_filters(args: Mapping[str, str]) -> SearchFilters:
    unknown = set(args.keys()) - SEARCH_PARAM_NAMES
    if unknown:
        raise ValueError(f"Filtros desconocidos: {', '.join(sorted(unknown))}")

    raw_query = str(args.get("q") or "").strip()
    if len(raw_query) > MAX_QUERY_LENGTH:
        raise ValueError(f"q no puede superar {MAX_QUERY_LENGTH} caracteres")
    query = normalize_search_text(raw_query) or None
    tsquery = build_prefix_tsquery(query) if query else None

    media_type = str(args.get("media_type") or "").strip().lower() or None
    if media_type not in {None, "image", "video"}:
        raise ValueError("media_type debe ser image o video")

    date_from = _date(args.get("date_from"), "date_from")
    date_to = _date(args.get("date_to"), "date_to")
    if date_to == date.max:
        raise ValueError("date_to no puede ser posterior a 9998-12-31")
    if date_from and date_to and date_from > date_to:
        raise ValueError("La fecha desde no puede ser posterior a la fecha hasta")

    raw_year = args.get("year")
    year = None
    if raw_year not in (None, ""):
        try:
            year = int(raw_year)
        except (TypeError, ValueError) as exc:
            raise ValueError("year debe ser un año válido") from exc
        if not 1 <= year <= 9998:
            raise ValueError("year debe estar entre 1 y 9998")

    favorite = None
    raw_favorite = str(args.get("favorite") or "").strip().lower()
    if raw_favorite:
        if raw_favorite not in {"true", "false"}:
            raise ValueError("favorite debe ser true o false")
        favorite = raw_favorite == "true"

    raw_place = str(args.get("place") or "").strip()
    if len(raw_place) > MAX_PLACE_LENGTH:
        raise ValueError(f"place no puede superar {MAX_PLACE_LENGTH} caracteres")
    place = normalize_search_text(raw_place) or None
    place_tsquery = build_prefix_tsquery(place) if place else None

    archived = str(args.get("archived") or "").strip() or None
    if archived is not None and archived not in ARCHIVED_MODES:
        raise ValueError("archived debe ser only o exclude")

    sort = str(args.get("sort") or ("relevance" if query else "created_at")).strip().lower()
    direction = str(args.get("direction") or "desc").strip().lower()
    if sort not in SORTS:
        raise ValueError(f"sort debe ser uno de: {', '.join(sorted(SORTS))}")
    if direction not in DIRECTIONS:
        raise ValueError("direction debe ser asc o desc")

    filters = SearchFilters(
        query=query,
        tsquery=tsquery,
        media_type=media_type,
        date_from=date_from,
        date_to=date_to,
        year=year,
        album_id=_positive_int(args.get("album_id"), "album_id"),
        favorite=favorite,
        tag_id=_positive_int(args.get("tag_id"), "tag_id"),
        place=place,
        place_tsquery=place_tsquery,
        sort=sort,
        direction=direction,
        archived=archived,
    )
    if not any((
        filters.query, filters.media_type, filters.date_from, filters.date_to,
        filters.year, filters.album_id, filters.favorite is not None,
        filters.tag_id, filters.place, filters.archived,
    )):
        raise ValueError("Indica al menos un criterio de búsqueda")
    return filters


# L11: lo que un álbum inteligente puede guardar. Son los criterios de la
# búsqueda global y nada más: orden y página son de cada ejecución, no de la
# definición.
SAVED_FILTER_NAMES = SEARCH_PARAM_NAMES - {"sort", "direction", "page", "per_page"}
_SAVED_TEXT = ("q", "place")
_SAVED_WORDS = ("media_type", "archived", "date_from", "date_to")
_SAVED_INTEGERS = ("year", "album_id", "tag_id")


def _saved_as_args(clean: Mapping) -> dict[str, str]:
    return {key: ("true" if value else "false") if isinstance(value, bool) else str(value)
            for key, value in clean.items()}


def normalize_saved_filters(raw) -> dict:
    """Valida y canoniza la definición JSON de un álbum inteligente.

    Tipos estrictos (JSON de verdad, no cadenas de query string), sin claves
    extra y sin valores vacíos. Los rangos, enums, fechas y longitudes los
    decide `parse_search_filters`, la misma regla que la búsqueda global, para
    que las dos no puedan divergir.
    """
    if not isinstance(raw, dict):
        raise ValueError("filters debe ser un objeto")
    unknown = set(raw) - SAVED_FILTER_NAMES
    if unknown:
        raise ValueError(f"Filtros no permitidos: {', '.join(sorted(map(str, unknown)))}")
    clean: dict = {}
    for key, value in raw.items():
        if value is None:
            continue
        if key in _SAVED_TEXT or key in _SAVED_WORDS:
            if not isinstance(value, str):
                raise ValueError(f"{key} debe ser texto")
            value = value.strip()
            if not value or (key in _SAVED_TEXT and not normalize_search_text(value)):
                continue
        elif key == "favorite":
            if not isinstance(value, bool):
                raise ValueError("favorite debe ser true o false")
        elif isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} debe ser un entero")
        clean[key] = value
    filters = parse_search_filters(_saved_as_args(clean))
    for key in ("date_from", "date_to"):
        if key in clean:
            clean[key] = getattr(filters, key).isoformat()
    return dict(sorted(clean.items()))


def saved_filters_to_search(stored) -> SearchFilters:
    """La definición guardada, revalidada, como filtros de la búsqueda global."""
    return parse_search_filters(_saved_as_args(normalize_saved_filters(stored)))


def build_media_search(filters: SearchFilters, owner_id: int) -> BuiltSearch:
    # El dueño primero: acota los candidatos antes que cualquier otro filtro.
    # Todo se evalua sobre el asset, una fila por asset aunque este en varios
    # albumes; el alcance activo conserva el filtro `albums.active` de siempre.
    media_clauses = [
        "m.user_id = :user_id",
        ACTIVE_SCOPE_SQL,
        "m.deleted_at IS NULL",
    ]
    params: dict = {"user_id": owner_id}

    if filters.query:
        media_clauses.append("m.search_vector @@ to_tsquery('simple', :tsquery)")
        params.update({
            "query": filters.query,
            "title_prefix": f"{filters.query}%",
            "tsquery": filters.tsquery,
        })
    if filters.media_type:
        media_clauses.append("m.file_type = :media_type")
        params["media_type"] = filters.media_type
    if filters.date_from:
        media_clauses.append("m.taken_at >= :date_from")
        params["date_from"] = filters.date_from
    if filters.date_to:
        media_clauses.append("m.taken_at < :date_to_exclusive")
        params["date_to_exclusive"] = filters.date_to + timedelta(days=1)
    if filters.year:
        media_clauses.extend(("m.taken_at >= :year_start", "m.taken_at < :year_end"))
        params.update({
            "year_start": f"{filters.year:04d}-01-01",
            "year_end": f"{filters.year + 1:04d}-01-01",
        })
    if filters.album_id:
        media_clauses.append(in_active_album_sql("AND ia.id = :album_id"))
        params["album_id"] = filters.album_id
    if filters.favorite is not None:
        media_clauses.append("m.is_favorite = :favorite")
        params["favorite"] = filters.favorite
    if filters.tag_id:
        media_clauses.append(
            "EXISTS (SELECT 1 FROM media_tags mt "
            "WHERE mt.media_id = m.id AND mt.tag_id = :tag_id)"
        )
        params["tag_id"] = filters.tag_id
    if filters.place:
        media_clauses.append("m.search_place_vector @@ to_tsquery('simple', :place_tsquery)")
        params["place_tsquery"] = filters.place_tsquery
    if filters.archived == "only":
        media_clauses.append("m.archived_at IS NOT NULL")
    elif filters.archived == "exclude":
        media_clauses.append("m.archived_at IS NULL")

    direction = filters.direction.upper()
    if filters.sort == "relevance" and filters.query:
        order_sql = (
            "CASE WHEN m.search_title = :query THEN 2 "
            "WHEN m.search_title LIKE :title_prefix THEN 1 ELSE 0 END DESC, "
            "ts_rank_cd(m.search_vector, to_tsquery('simple', :tsquery), 32) DESC, "
            "m.created_at DESC, m.id DESC"
        )
    else:
        column = {
            "relevance": "m.created_at",
            "taken_at": "m.taken_at",
            "created_at": "m.created_at",
            "title": "m.search_title",
        }[filters.sort]
        order_sql = f"{column} {direction} NULLS LAST, m.id DESC"

    return BuiltSearch(
        where_sql=" AND ".join(media_clauses),
        params=params,
        order_sql=order_sql,
    )


def run_media_search(conn, filters: SearchFilters, owner_id: int, *, limit: int, offset: int) -> tuple[int, list[dict]]:
    """Ejecuta una búsqueda ya validada: `(total, filas de la página)`.

    La comparten la búsqueda global y los álbumes inteligentes (L11), así que
    los dos listan exactamente lo mismo: assets del dueño, una fila por asset
    aunque esté en varios álbumes, y nunca la papelera.
    """
    built = build_media_search(filters, owner_id=owner_id)
    params = {**built.params, "limit": limit, "offset": offset}
    contexto = context_album_join(prefer_param="album_id" if filters.album_id else None)
    total = execute_safe(
        conn,
        f"SELECT COUNT(*) AS total FROM assets m WHERE {built.where_sql}",
        params,
    ).mappings().first()["total"]
    rows = execute_safe(
        conn,
        f"""
        WITH candidates AS MATERIALIZED (
            SELECT m.id, m.file_type, m.title, m.caption,
                   m.is_favorite, m.taken_at, m.created_at, m.archived_at,
                   m.search_title, m.search_vector
            FROM assets m
            WHERE {built.where_sql}
        )
        SELECT m.id, ctx.album_id, m.file_type, m.title, m.caption,
               m.is_favorite, m.taken_at, m.created_at, m.archived_at,
               ctx.album_titulo
        FROM candidates m {contexto}
        ORDER BY {built.order_sql}
        LIMIT :limit OFFSET :offset
        """,
        params,
    ).mappings().all()
    return total, [dict(r) for r in rows]
