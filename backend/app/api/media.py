import os
import re
import zipfile
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file
from ..security.sessions import current_user_id, session_required

from ..db.db import db_conn
from ..domain.rules import validate_media_update_fields, validate_media_upload_fields
from ..integrations.locationiq import LocationIQError, fetch_static_map, search_places
from ..integrations.usage_budget import try_reserve
from ..library_timeline import (
    LibraryCursorError,
    decode_library_cursor,
    encode_library_cursor,
    get_library_limit,
)
from ..media.assets import (
    ACTIVE_SCOPE_SQL,
    add_membership,
    asset_albums,
    context_album_join,
    in_active_album_sql,
    in_album_sql,
    require_asset_capability,
    require_asset_in_album,
    require_asset_permission,
)
from ..media.checksums import find_exact_duplicate, lock_exact_duplicate_scope
from ..media.context import (_clear_date_dependent_context, _context_coordinates, _fetch_weather,
                            _read_context, _try_auto_enrich, _try_auto_holiday,
                            _try_auto_solar)
from ..activity import record_activity
from ..notifications import notify_album_upload
from ..security.permissions import require_album_capability, require_album_permission
from ..search import (
    MAX_PLACE_LENGTH,
    MAX_QUERY_LENGTH,
    run_media_search,
    build_prefix_tsquery,
    normalize_search_text,
    parse_search_filters,
)
from ..storage.compensation import mark_committed, record_pending
from ..storage.media_delivery import deliver_stored_file
from .tags import link_tags, tags_belong_to
from ..storage.media_storage import remove_stored_file, safe_filename, save_upload, upload_size
from ..utils.env import int_env
from ..utils.pagination import build_pagination_meta, get_pagination_args
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe, safe_identifier

media_bp = Blueprint("media", __name__, url_prefix="/api")

LOCATION_PROVIDER = "locationiq"


class ExactDuplicate(Exception):
    def __init__(self, match: dict):
        super().__init__("Este archivo exacto ya existe en tu biblioteca")
        self.match = match


class UploadAccessRevoked(PermissionError):
    pass


def _duplicate_response(match: dict):
    return jsonify({
        "ok": False,
        "message": "Este archivo exacto ya existe en tu biblioteca",
        "code": "exact_duplicate",
        "existing_media_id": match["id"],
        "existing_album_id": match["album_id"],
    }), 409


def _shape_exif(row) -> dict | None:
    """NUMERIC vuelve de psycopg2 como Decimal, y eso no es serializable a JSON."""
    if not row:
        return None
    datos = dict(row)
    for campo in ("latitude", "longitude", "altitude_m"):
        if datos.get(campo) is not None:
            datos[campo] = float(datos[campo])
    return datos


def _read_exif(conn, media_id: int):
    return execute_safe(
        conn,
        """
        SELECT taken_at_original, latitude, longitude, altitude_m,
               camera_make, camera_model, orientation
        FROM media_exif WHERE media_id = :media_id
        """,
        {"media_id": media_id},
    ).mappings().first()




























_LATLON_RE = re.compile(r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$")


def _valid_latlon(value: str) -> bool:
    return bool(_LATLON_RE.match(value))


def insert_media_record(conn, album_id: int, owner_id: int, created_by: int | None, clean: dict, stored: dict):
    # EXIF y vista previa vienen ya calculados desde `save_upload`, que los
    # generó mientras el archivo seguía en cuarentena local (spec S12): aquí
    # no se abre ningún archivo ni se resuelve ninguna ruta, solo se escriben
    # filas.
    exif = stored.get("exif") or {}
    preview = stored.get("preview")
    if preview:
        stored["preview_storage_path"] = preview["storage_path"]

    # La fecha que escribio el usuario manda; la del EXIF solo rellena el hueco.
    taken_at = clean["taken_at"] or exif.get("taken_at_original")

    media = execute_safe(
        conn,
        """
        INSERT INTO assets (
            user_id, storage_path, file_type, title, caption,
            is_favorite, taken_at, created_by, deleted_at
        )
        VALUES (
            :owner_id, :storage_path, :file_type, :title, :caption,
            :is_favorite, :taken_at, :created_by, NULL
        )
        RETURNING id, user_id, file_type, title, caption,
                  is_favorite, taken_at, created_at, created_by
        """,
        {
            "owner_id": owner_id,
            "storage_path": stored["storage_path"],
            "file_type": stored["file_type"],
            "title": clean.get("title") or Path(stored["original_filename"]).stem[:120] or "Archivo",
            "caption": clean["caption"],
            "is_favorite": clean["is_favorite"],
            "taken_at": taken_at,
            "created_by": created_by,
        },
    ).mappings().first()
    # El asset nace en el album al que se subio; su key sigue nombrando ese
    # album para siempre, pero la pertenencia vive aqui (L10A).
    add_membership(conn, album_id=album_id, asset_id=media["id"], owner_id=owner_id)
    media = {**dict(media), "album_id": album_id}

    # Sin fila cuando no hay nada que guardar: la mayoria de las fotos de
    # WhatsApp o de captura de pantalla llegan sin un solo campo.
    if any(valor is not None for valor in exif.values()):
        execute_safe(
            conn,
            """
            INSERT INTO media_exif (
                media_id, taken_at_original, latitude, longitude, altitude_m,
                camera_make, camera_model, orientation
            )
            VALUES (
                :media_id, :taken_at_original, :latitude, :longitude, :altitude_m,
                :camera_make, :camera_model, :orientation
            )
            """,
            {"media_id": media["id"], **exif},
        )

    execute_safe(
        conn,
        """
        INSERT INTO media_metadata (
            media_id, file_size, resolution, duration, format,
            original_filename, mime_type, sha256,
            preview_storage_path, preview_mime_type, preview_width, preview_height,
            preview_file_size
        )
        VALUES (
            :media_id, :file_size, :resolution, :duration, :format,
            :original_filename, :mime_type, :sha256,
            :preview_storage_path, :preview_mime_type, :preview_width, :preview_height,
            :preview_file_size
        )
        """,
        {
            "media_id": media["id"],
            "file_size": stored["file_size"],
            # Para fotos, la resolucion SERVIDOR-decodificada con Pillow
            # siempre gana sobre la que haya declarado el cliente en el
            # formulario -- mismo criterio que canonical_mime_type en S05.
            # El video no tiene validacion propia (ver nota de portabilidad
            # en CLAUDE.md), asi que sigue confiando en el dato del cliente.
            "resolution": stored.get("resolution") or clean["resolution"],
            "duration": clean["duration"],
            "format": stored["format"],
            "original_filename": stored["original_filename"],
            "mime_type": stored["mime_type"],
            "sha256": stored["sha256"],
            "preview_storage_path": (preview or {}).get("storage_path"),
            "preview_mime_type": (preview or {}).get("mime_type"),
            "preview_width": (preview or {}).get("width"),
            "preview_height": (preview or {}).get("height"),
            "preview_file_size": (preview or {}).get("file_size"),
        },
    )

    # El pin que pone el dueño en el mapa manda, igual que la fecha manual
    # gana al EXIF. Coordenadas manuales o de EXIF, cualquiera de las dos
    # termina en el mismo _try_auto_enrich: no hace falta un camino aparte.
    lat = clean.get("latitude")
    lon = clean.get("longitude")
    if lat is None or lon is None:
        lat, lon = exif.get("latitude"), exif.get("longitude")
    if lat is not None and lon is not None:
        _try_auto_enrich(conn, media["id"], lat, lon, taken_at)

    link_tags(conn, media["id"], clean["tag_ids"])

    return media


# 0 = sin limite (S03): decision del producto -- "app reservada, sin limite
# de tamaño por archivo" (ver README). Las variables existen y funcionan si
# se configuran, pero no restringen nada mientras sigan en 0.
def _max_upload_bytes(file_type: str) -> int:
    variable = "MAX_IMAGE_MB" if file_type == "image" else "MAX_VIDEO_MB"
    mb = int_env(variable, 0)
    return mb * 1024 * 1024 if mb > 0 else 0


def _upload_clean(file, form):
    if not file or not file.filename:
        raise ValueError("Debes seleccionar una foto o video")
    file.stream.seek(0)
    header = file.stream.read(64)
    file.stream.seek(0)
    from ..storage.media_storage import detect_media_signature
    signature = detect_media_signature(header, file.filename, file.mimetype)
    file_type = signature[0]
    limite = _max_upload_bytes(file_type)
    if limite and upload_size(file) > limite:
        etiqueta = "la foto" if file_type == "image" else "el video"
        raise ValueError(f"{etiqueta.capitalize()} supera el límite permitido de {limite // (1024 * 1024)} MB")
    return validate_media_upload_fields(form, file_type=file_type), signature


# Namespace fijo para no chocar con otro futuro uso de advisory locks sobre el
# mismo espacio de enteros: la clave real es (ESPACIO, owner_id).
_STORAGE_QUOTA_LOCK_NAMESPACE = 1001


def account_storage_used_bytes(conn, owner_id: int) -> int:
    """Bytes ya ocupados por esta cuenta: originales + vistas previas de TODA
    su media, INCLUIDA la que esta en la papelera -- sigue en disco hasta que
    se purga de verdad, asi que cuenta igual. No incluye el avatar (tope de
    unos pocos MB, ~0.02% de una cuota de 25GB): anadir una columna de tamaño
    solo para eso no vale la pena.
    """
    fila = execute_safe(
        conn,
        """
        SELECT COALESCE(SUM(mm.file_size), 0) + COALESCE(SUM(mm.preview_file_size), 0) AS total
        FROM assets m
        JOIN media_metadata mm ON mm.media_id = m.id
        WHERE m.user_id = :owner_id
        """,
        {"owner_id": owner_id},
    ).mappings().first()
    return int(fila["total"] or 0)


def reserve_storage_quota(conn, owner_id: int, additional_bytes: int) -> bool:
    """True si la cuenta tiene sitio para `additional_bytes` mas.

    `USER_STORAGE_QUOTA_GB <= 0` es "sin limite" (mismo criterio que el resto
    de topes de esta fase). Con limite, un advisory lock TRANSACCIONAL sobre
    (namespace, owner_id) serializa dos subidas concurrentes AL MISMO dueño
    -- la fila que suma el uso y el INSERT que la sube despues no pueden
    colarse las dos por debajo de la misma cuota. Subidas de dueños distintos
    no se bloquean entre si: la clave es distinta. Se libera solo al terminar
    la transaccion (aunque falle), nunca hay que soltarlo a mano.
    """
    quota_gb = int_env("USER_STORAGE_QUOTA_GB", 0)
    if quota_gb <= 0:
        return True
    execute_safe(
        conn,
        "SELECT pg_advisory_xact_lock(:namespace, :owner_id)",
        {"namespace": _STORAGE_QUOTA_LOCK_NAMESPACE, "owner_id": owner_id},
    )
    usado = account_storage_used_bytes(conn, owner_id)
    return usado + additional_bytes <= quota_gb * 1024 ** 3


def _purge_media_rows(conn, where_sql: str, params: dict) -> list[dict]:
    """Borra de verdad los assets que cumplan `where_sql` (alias `m`).

    Ya no pasa por el album: un asset suelto (sin album) en la papelera tiene
    que poder purgarse igual. La vista previa se lee ANTES del DELETE:
    `media_metadata` cae por cascada con el asset, así que un `RETURNING` ya no
    la alcanzaría. Las dos sentencias van en la misma transacción, y quien
    llama borra los archivos después de que cierre — el orden de siempre.
    """
    previews = {
        row["media_id"]: row["preview_storage_path"]
        for row in execute_safe(
            conn,
            f"""
            SELECT mm.media_id, mm.preview_storage_path
            FROM assets m
            JOIN media_metadata mm ON mm.media_id = m.id
            WHERE mm.preview_storage_path IS NOT NULL AND {where_sql}
            """,
            params,
        ).mappings().all()
    }
    rows = execute_safe(
        conn,
        f"""
        DELETE FROM assets AS m
        WHERE {where_sql}
        RETURNING m.id, m.storage_path
        """,
        params,
    ).mappings().all()
    return [{**dict(r), "preview_storage_path": previews.get(r["id"])} for r in rows]


def remove_media_files(items) -> tuple[int, int]:
    """Borra original y vista previa de cada elemento.

    Devuelve `(borrados, pendientes)`. Un fallo de infraestructura cuenta como
    pendiente, no como "no había nada": el reconciliador lo retomará.
    """
    from ..storage.contracts import StorageError

    borrados = pendientes = 0
    for item in items:
        for clave in (item.get("preview_storage_path"), item.get("storage_path")):
            if not clave:
                continue
            try:
                if remove_stored_file(clave):
                    borrados += 1
            except StorageError:
                pendientes += 1
    return borrados, pendientes


@media_bp.get("/media/<int:media_id>")
@session_required
def get_media_detail(media_id: int):
    user_id = current_user_id()
    # `album_id` opcional: el album desde el que se abre (L10B). Sin el, la
    # vista es la del asset (el dueño puede abrir uno suelto, sin album).
    album_raw = request.args.get("album_id")
    try:
        album_id = int(album_raw) if album_raw is not None else None
    except ValueError:
        return fail("album_id debe ser entero", status=400)
    with db_conn() as conn:
        if album_id is None:
            asset, access = require_asset_permission(conn, media_id, user_id, "read")
        else:
            asset, access = require_asset_in_album(conn, media_id, album_id, user_id)
        if not asset:
            return fail("Media no encontrada", status=404)
        if not access:
            return fail("No autorizado para ver esta media", status=403)
        media = execute_safe(
            conn,
            """
            SELECT m.id, m.user_id, m.file_type, m.title, m.caption,
                   m.is_favorite, m.taken_at, m.created_at, m.created_by, m.deleted_at,
                   m.archived_at, creator.username AS created_by_username
            FROM assets m
            LEFT JOIN users creator ON creator.id = m.created_by
            WHERE m.id = :media_id
            """,
            {"media_id": media_id},
        ).mappings().first()
        # El album de contexto: por el que esta cuenta llega al asset (NULL si
        # es el dueño y el asset esta suelto).
        media = {**dict(media), "album_id": access["album_id"]}
        metadata = execute_safe(
            conn,
            """
            SELECT id, media_id, file_size, resolution, duration, format,
                   original_filename, mime_type
            FROM media_metadata WHERE media_id = :media_id
            """,
            {"media_id": media_id},
        ).mappings().first()
        exif = _read_exif(conn, media_id)
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
        datos = {"media": dict(media), "metadata": dict(metadata) if metadata else None, "exif": _shape_exif(exif), "tags": [dict(t) for t in tags], "album_role": access["role"], "album_capabilities": sorted(access["capabilities"])}
        # Sus albumes solo los ve el dueño: a un colaborador le revelarian los
        # titulos de albumes privados que no le compartieron.
        if asset["user_id"] == user_id:
            datos["albums"] = asset_albums(conn, media_id)
    return ok(data=datos, message="Detalle media")


























@media_bp.get("/location/search")
@session_required
def location_search():
    """Busca lugares por texto para el selector de pin manual, asi el dueño
    encuentra su direccion en vez de tener que hacer zoom a mano por todo el
    mapa. No hay media_id todavia (corre antes de subir), asi que la unica
    comprobacion de acceso es estar autenticado.
    """
    query = (request.args.get("q") or "").strip()
    if not query:
        return fail("q es obligatorio", status=400)
    if len(query) > 200:
        return fail("q no puede superar 200 caracteres", status=400)

    country = (request.args.get("country") or "").strip().upper()
    if country and (len(country) != 2 or not country.isalpha()):
        return fail("country debe ser un código ISO de 2 letras", status=400)

    api_key = (os.getenv("LOCATIONIQ_API_KEY") or "").strip()
    if not api_key:
        return fail(
            "Esta función aún no está configurada en este servidor.",
            status=503,
            code="integration_not_configured",
        )

    daily_budget = int_env("LOCATIONIQ_DAILY_BUDGET", 4500)
    with db_conn() as conn:
        if not try_reserve(conn, LOCATION_PROVIDER, daily_budget):
            return fail(
                "La cuota gratuita de esta función se alcanzó por hoy. Intenta de nuevo mañana.",
                status=429,
                code="integration_quota_exhausted",
            )

    try:
        resultados = search_places(query, api_key=api_key, country_codes=country.lower() or None)
    except LocationIQError as exc:
        status = 429 if exc.reason == "quota" else 503
        return fail(
            "No pudimos buscar el lugar ahora.",
            status=status,
            code=f"locationiq_{exc.reason}",
        )

    return ok(data=resultados, message="Resultados de búsqueda")


@media_bp.patch("/media/<int:media_id>")
@session_required
def update_media(media_id: int):
    """Edita metadatos ya subidos: titulo, descripcion, fecha y/o ubicacion.

    Fecha y lugar tienen la misma regla que al subir: **lo que escribe el
    usuario gana sobre lo que trae la foto, y vaciarlo devuelve el mando al
    EXIF** en vez de dejar el dato en blanco. Por eso `taken_at: null` y
    `clear_location: true` no borran nada: vuelven a leer el EXIF y, solo si
    ahi tampoco hay nada, queda vacio.

    Cambiar el lugar no escribe coordenadas en `media` (no existen esas
    columnas): vuelve a correr `_try_auto_enrich`, que resuelve el lugar y
    guarda las coordenadas en `media_context`.
    """
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    try:
        updates = validate_media_update_fields(payload)
    except ValueError as exc:
        return fail(str(exc), status=400)
    if not updates:
        return fail("No hay campos para actualizar", status=400)

    latitude = updates.pop("latitude", None)
    longitude = updates.pop("longitude", None)
    reset_taken_at = updates.pop("reset_taken_at", False)
    reset_location = updates.pop("reset_location", False)

    with db_conn() as conn:
        asset, access = require_asset_capability(conn, media_id, user_id, "edit_media")
        if not asset: return fail("Media no encontrada", status=404)
        if not access: return fail("No autorizado para editar esta foto", status=403)
        media = execute_safe(conn, "SELECT id, taken_at FROM assets WHERE id = :media_id", {"media_id": media_id}).mappings().first()

        exif = execute_safe(
            conn,
            "SELECT taken_at_original, latitude, longitude FROM media_exif WHERE media_id = :media_id",
            {"media_id": media_id},
        ).mappings().first()

        # Vaciar la fecha devuelve la del EXIF; si la foto no traia ninguna,
        # se queda de verdad sin fecha, igual que si se hubiera subido asi.
        if reset_taken_at:
            updates["taken_at"] = exif["taken_at_original"] if exif else None

        if updates:
            set_clause = ", ".join(f"{field} = :{field}" for field in updates)
            execute_safe(conn, f"UPDATE assets SET {set_clause} WHERE id = :media_id", {**updates, "media_id": media_id})

        # Vaciar el lugar devuelve el del EXIF. Sin GPS propio no hay a que
        # volver, asi que se borra el contexto entero en vez de dejar colgado
        # el lugar viejo, que ya no describe a esta foto.
        if reset_location:
            if exif and exif["latitude"] is not None and exif["longitude"] is not None:
                latitude, longitude = float(exif["latitude"]), float(exif["longitude"])
            else:
                execute_safe(conn, "DELETE FROM media_context WHERE media_id = :media_id", {"media_id": media_id})

        if latitude is not None:
            # La fecha que vale es la que queda tras este PATCH, no la de
            # antes: mover una foto de sitio Y de dia debe recalcular el sol
            # del dia nuevo.
            fecha = updates["taken_at"] if "taken_at" in updates else media["taken_at"]
            _try_auto_enrich(conn, media_id, latitude, longitude, fecha)
        elif "taken_at" in updates and not reset_location:
            # Solo cambio la fecha (el lugar no se tocó en este PATCH): sol,
            # festivo y clima dependen del día, así que hay que recalcularlos
            # para el día nuevo aunque las coordenadas sigan siendo las
            # mismas. Antes esto solo pasaba si TAMBIÉN cambiaba el lugar —
            # bug real: cambiar solo la fecha dejaba el sol/festivo/clima
            # pegados al día viejo, sin ninguna señal de que estaban mal.
            nueva_fecha = updates["taken_at"]
            coordenadas = _context_coordinates(conn, media_id)
            if not coordenadas:
                pass
            elif not nueva_fecha:
                _clear_date_dependent_context(conn, media_id)
            else:
                _try_auto_solar(conn, media_id, coordenadas[0], coordenadas[1], nueva_fecha)
                contexto_actual = _read_context(conn, media_id)
                if contexto_actual and contexto_actual["country_code"]:
                    _try_auto_holiday(conn, media_id, contexto_actual["country_code"], nueva_fecha)
                _fetch_weather(conn, media_id, coordenadas[0], coordenadas[1], nueva_fecha)

        updated = execute_safe(
            conn,
            "SELECT id, title, caption, is_favorite, taken_at FROM assets WHERE id = :media_id",
            {"media_id": media_id},
        ).mappings().first()

    return ok(data={**dict(updated), "album_id": access["album_id"]}, message="Foto actualizada")


@media_bp.get("/location/staticmap")
@session_required
def location_staticmap():
    """Proxy del Static Maps de LocationIQ para el selector de pin manual del
    panel de subida. La clave nunca llega al navegador; solo esta URL, que no
    la lleva. No hay media_id todavia (esto corre antes de subir el archivo),
    asi que la unica comprobacion de acceso es estar autenticado.
    """
    center = (request.args.get("center") or "").strip()
    marker = (request.args.get("marker") or "").strip() or None
    zoom_raw = (request.args.get("zoom") or "2").strip()

    if not _valid_latlon(center):
        return fail("center debe tener el formato 'lat,lon'", status=400)
    if marker and not _valid_latlon(marker):
        return fail("marker debe tener el formato 'lat,lon'", status=400)
    try:
        zoom = int(zoom_raw)
    except ValueError:
        return fail("zoom debe ser un entero", status=400)
    if not 0 <= zoom <= 18:
        return fail("zoom debe estar entre 0 y 18", status=400)

    api_key = (os.getenv("LOCATIONIQ_API_KEY") or "").strip()
    if not api_key:
        return fail(
            "Esta función aún no está configurada en este servidor.",
            status=503,
            code="integration_not_configured",
        )

    daily_budget = int_env("LOCATIONIQ_DAILY_BUDGET", 4500)
    with db_conn() as conn:
        if not try_reserve(conn, LOCATION_PROVIDER, daily_budget):
            return fail(
                "La cuota gratuita de esta función se alcanzó por hoy. Intenta de nuevo mañana.",
                status=429,
                code="integration_quota_exhausted",
            )

    try:
        image_bytes, content_type = fetch_static_map(center, zoom, marker, api_key=api_key)
    except LocationIQError as exc:
        status = 429 if exc.reason == "quota" else 503
        return fail(
            "No pudimos cargar el mapa ahora.",
            status=status,
            code=f"locationiq_{exc.reason}",
        )

    return Response(image_bytes, mimetype=content_type, headers={"Cache-Control": "no-store"})


def _authorized_media_file(media_id: int, user_id: int):
    """(fila, error) de una media que este usuario puede leer.

    Lo comparten la entrega del original y la de la vista previa: son el
    mismo archivo con la misma autorizacion, solo cambia cual de las dos
    rutas se sirve al final.
    """
    with db_conn() as conn:
        media = execute_safe(
            conn,
            """
            SELECT m.id, m.storage_path, m.deleted_at,
                   mm.original_filename, mm.mime_type,
                   mm.preview_storage_path, mm.preview_mime_type
            FROM assets m
            LEFT JOIN media_metadata mm ON mm.media_id = m.id
            WHERE m.id = :media_id
            """,
            {"media_id": media_id},
        ).mappings().first()
        if not media:
            return None, fail("Media no encontrada", status=404)
        required = "owner" if media["deleted_at"] is not None else "read"
        _asset, access = require_asset_permission(conn, media_id, user_id, required, trashed=None)
        if not access:
            return None, fail("No autorizado para ver este archivo", status=403)
    return media, None


def _deliver_original(media):
    response = deliver_stored_file(
        media["storage_path"],
        media["mime_type"] or None,
        media["original_filename"] or None,
    )
    if response is None:
        return fail("El archivo ya no existe en almacenamiento", status=404)
    return response


def _deliver_preview(media):
    """La vista previa si existe; si no, el original.

    Caer al original en vez de dar 404 es lo que deja que la cuadricula pida
    siempre la vista previa sin saber cuales fotos la tienen: los videos y
    todo lo subido antes de la fase 13 siguen funcionando igual, solo que sin
    el ahorro de bytes.
    """
    if media["preview_storage_path"]:
        response = deliver_stored_file(
            media["preview_storage_path"],
            media["preview_mime_type"] or None,
            media["original_filename"] or None,
        )
        if response is not None:
            return response
    return _deliver_original(media)


@media_bp.get("/media/<int:media_id>/file")
@session_required
def read_media_file(media_id: int):
    media, error = _authorized_media_file(media_id, current_user_id())
    return error or _deliver_original(media)


@media_bp.get("/media/<int:media_id>/preview")
@session_required
def read_media_preview(media_id: int):
    media, error = _authorized_media_file(media_id, current_user_id())
    return error or _deliver_preview(media)


@media_bp.get("/albums/<int:album_id>/media")
@session_required
def list_album_media(album_id: int):
    user_id = current_user_id()
    file_type = (request.args.get("file_type") or "").strip().lower()
    raw_q = (request.args.get("q") or "").strip()
    taken_from = request.args.get("taken_from")
    taken_to = request.args.get("taken_to")
    tag_id = request.args.get("tag_id")
    raw_place = (request.args.get("place") or "").strip()
    country_code = (request.args.get("country_code") or "").strip().upper()
    is_favorite_raw = (request.args.get("is_favorite") or "").strip().lower()
    sort_by = (request.args.get("sort_by") or "created_at").strip().lower()
    sort_dir = (request.args.get("sort_dir") or "desc").strip().lower()
    page, per_page, offset = get_pagination_args(request)
    if len(raw_q) > MAX_QUERY_LENGTH:
        return fail(f"q no puede superar {MAX_QUERY_LENGTH} caracteres", status=400)
    if len(raw_place) > MAX_PLACE_LENGTH:
        return fail(f"place no puede superar {MAX_PLACE_LENGTH} caracteres", status=400)
    q = normalize_search_text(raw_q)
    place = normalize_search_text(raw_place)
    if file_type and file_type not in {"image", "video"}:
        return fail("file_type debe ser image o video", status=400)
    if sort_dir not in {"asc", "desc"}:
        return fail("sort_dir debe ser asc o desc", status=400)
    try:
        sort_by_sql = safe_identifier(sort_by, {"created_at", "taken_at"})
    except ValueError:
        return fail("sort_by inválido", status=400)
    if country_code and (len(country_code) != 2 or not country_code.isalpha()):
        return fail("country_code debe ser un código ISO de 2 letras", status=400)
    where_clauses = [in_album_sql("album_id"), "m.deleted_at IS NULL"]
    params = {"album_id": album_id, "limit": per_page, "offset": offset}
    if file_type:
        where_clauses.append("m.file_type = :file_type"); params["file_type"] = file_type
    if q:
        # El texto detectado por OCR entra en la MISMA busqueda de siempre, sin
        # caja nueva: buscar "cumpleaños" tiene que encontrar tanto la foto
        # titulada asi como aquella en la que se lee en un cartel.
        where_clauses.append("m.search_vector @@ to_tsquery('simple', :search_tsquery)")
        params["search_tsquery"] = build_prefix_tsquery(q)
    if taken_from:
        where_clauses.append("m.taken_at >= :taken_from"); params["taken_from"] = taken_from
    if taken_to:
        where_clauses.append("m.taken_at <= :taken_to"); params["taken_to"] = taken_to
    if tag_id is not None and str(tag_id).strip() != "":
        try: params["tag_id"] = int(tag_id)
        except ValueError: return fail("tag_id debe ser entero", status=400)
        where_clauses.append("EXISTS (SELECT 1 FROM media_tags mt WHERE mt.media_id = m.id AND mt.tag_id = :tag_id)")
    if is_favorite_raw:
        if is_favorite_raw not in {"true", "false", "1", "0"}: return fail("is_favorite debe ser true/false", status=400)
        params["is_favorite"] = is_favorite_raw in {"true", "1"}; where_clauses.append("m.is_favorite = :is_favorite")
    if place:
        where_clauses.append("m.search_place_vector @@ to_tsquery('simple', :place_tsquery)")
        params["place_tsquery"] = build_prefix_tsquery(place)
    if country_code:
        where_clauses.append("EXISTS (SELECT 1 FROM media_context mc2 WHERE mc2.media_id = m.id AND mc2.country_code = :country_code)")
        params["country_code"] = country_code
    where_sql = " AND ".join(where_clauses)
    with db_conn() as conn:
        access = require_album_permission(conn, album_id, user_id, "read")
        if not access: return fail("No autorizado para ver media", status=403)
        total = execute_safe(conn, f"SELECT COUNT(*) AS total FROM assets m WHERE {where_sql}", params).mappings().first()["total"]
        rows = execute_safe(conn, f"""
            SELECT id, user_id, :album_id AS album_id, file_type, title, caption,
                   is_favorite, taken_at, created_at, created_by, deleted_at, archived_at
            FROM assets m WHERE {where_sql}
            ORDER BY {sort_by_sql} {sort_dir.upper()} NULLS LAST, created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
        """, params).mappings().all()
    return ok(data=[dict(r) for r in rows], message="Media del álbum", pagination=build_pagination_meta(page, per_page, total))


@media_bp.get("/albums/<int:album_id>/download")
@session_required
def download_album(album_id: int):
    """Descarga el album entero como un zip. Mismo nivel que verlo ("read"):
    quien ya puede ver cada foto ya podia guardarla una por una, esto es una
    comodidad, no una capacidad nueva. La papelera queda fuera, igual que en
    el listado normal del album."""
    user_id = current_user_id()
    with db_conn() as conn:
        access = require_album_permission(conn, album_id, user_id, "read")
        if not access:
            return fail("No autorizado para descargar este álbum", status=403)
        rows = execute_safe(conn, f"""
            SELECT m.id, m.storage_path, mm.original_filename
            FROM assets m
            LEFT JOIN media_metadata mm ON mm.media_id = m.id
            WHERE {in_album_sql("album_id")} AND m.deleted_at IS NULL
            ORDER BY m.created_at ASC
        """, {"album_id": album_id}).mappings().all()

    if not rows:
        return fail("El álbum no tiene archivos para descargar", status=400)

    from ..storage.backends import get_storage_backend
    from ..storage.contracts import ObjectNotFound, StorageError
    from ..storage.workspace import local_workspace

    backend = get_storage_backend()
    espacio_cm = local_workspace("zip")
    espacio = espacio_cm.__enter__()
    zip_path = espacio.directory / "album.zip"
    incluidos = 0
    try:
        used_names: set[str] = set()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for row in rows:
                try:
                    # Una fuente materializada a la vez: se escribe su entrada
                    # y se libera antes de pedir la siguiente. El original
                    # puede haber desaparecido del almacen pero seguir en la
                    # fila (el mismo caso que ya cubre deliver_stored_file
                    # para una sola foto); aqui simplemente se omite del zip
                    # en vez de fallar la descarga entera por un archivo.
                    with backend.materialize(row["storage_path"]) as real_path:
                        name = safe_filename(row["original_filename"] or "") or \
                            f"media_{row['id']}{Path(real_path).suffix}"
                        arcname = name
                        if arcname in used_names:
                            stem, ext = os.path.splitext(name)
                            arcname = f"{stem}_{row['id']}{ext}"
                        used_names.add(arcname)
                        zf.write(real_path, arcname=arcname)
                        incluidos += 1
                except (ObjectNotFound, ValueError):
                    continue
        if incluidos == 0:
            espacio_cm.__exit__(None, None, None)
            return fail("El álbum no tiene archivos disponibles para descargar", status=404)
    except StorageError:
        espacio_cm.__exit__(None, None, None)
        return fail("El almacenamiento no está disponible ahora", status=503,
                    code="media_storage_unavailable")
    except Exception:
        espacio_cm.__exit__(None, None, None)
        raise

    # safe_filename() empieza por Path(...).name: correcto para el nombre de un
    # archivo subido (quedarse solo con la base), pero un titulo de album no es
    # una ruta -- "Antes/Despues" perderia "Antes" si se le aplicara tal cual.
    title_as_name = (access["album"]["titulo"] or "").replace("/", "-").replace("\\", "-")
    download_name = f"{safe_filename(title_as_name) or 'album'}.zip"
    respuesta = send_file(zip_path, mimetype="application/zip", as_attachment=True,
                          download_name=download_name, conditional=False)
    # El lease del workspace termina al cerrar el iterable WSGI de la
    # respuesta, incluida una desconexion del cliente: un hook que corre
    # antes de esta respuesta se dispararia antes de que los bytes salieran
    # de verdad (spec §11).
    respuesta.call_on_close(lambda: espacio_cm.__exit__(None, None, None))
    return respuesta


def _stored_for_cleanup(stored: dict) -> dict:
    """Da forma al dict que `remove_media_files` espera, leyendo la vista
    previa de `stored["preview"]` directamente -- no puede depender de que
    `insert_media_record` haya corrido (justo el caso en que hace falta
    limpiar), a diferencia del `stored["preview_storage_path"]` que esa
    función deja anotado como efecto secundario cuando sí llega a ejecutar.
    """
    return {
        "storage_path": stored.get("storage_path"),
        "preview_storage_path": (stored.get("preview") or {}).get("storage_path"),
    }


@media_bp.post("/albums/<int:album_id>/media")
@session_required
def create_media(album_id: int):
    from ..storage.compensation import mark_committed, mark_rolled_back

    user_id = current_user_id()
    file = request.files.get("file")
    try:
        clean, signature = _upload_clean(file, request.form)
    except ValueError as exc:
        return fail(str(exc), status=400)

    with db_conn() as conn:
        access = require_album_capability(conn, album_id, user_id, "upload")
        if not access: return fail("No autorizado para subir media", status=403)
        if "organize" not in access["capabilities"] and clean["is_favorite"]: return fail("No autorizado para marcar favoritos", status=403)
        if "organize" not in access["capabilities"] and clean["tag_ids"]: return fail("No autorizado para organizar media con tags", status=403)
        if not tags_belong_to(conn, access["album"]["user_id"], clean["tag_ids"]): return fail("Uno o más tags no existen", status=400)
        owner_id = access["album"]["user_id"]
        # Admisión temprana con la medición barata (spec S7 paso 1: la
        # transacción no se mantiene abierta durante la transferencia).
        if not reserve_storage_quota(conn, owner_id, upload_size(file)):
            return fail("Se alcanzó el límite de almacenamiento de esta cuenta", status=413, code="storage_quota_exceeded")

    def reject_visible_duplicate(sha256: str):
        with db_conn() as conn:
            current_access = require_album_capability(conn, album_id, user_id, "upload")
            if not current_access or current_access["album"]["user_id"] != owner_id:
                raise UploadAccessRevoked("permiso revocado durante la subida")
            match = find_exact_duplicate(
                conn,
                owner_id=owner_id,
                requester_id=user_id,
                target_album_id=album_id,
                sha256=sha256,
            )
        if match:
            raise ExactDuplicate(match)

    # El PUT (cuarentena, validación, EXIF/preview, subida al backend) corre
    # SIN ninguna transacción abierta. `stored["storage_operation_id"]` ya
    # tiene su registro de compensación escrito en disco antes del primer PUT
    # (spec S7 paso 6): un crash de aquí en adelante es recuperable aunque
    # este proceso nunca llegue a las líneas de abajo.
    try:
        stored = save_upload(
            file,
            owner_id,
            album_id,
            signature,
            max_bytes=_max_upload_bytes(signature[0]),
            prepare_image_metadata=True,
            before_store=None if clean["force_duplicate"] else reject_visible_duplicate,
        )
    except ExactDuplicate as exc:
        return _duplicate_response(exc.match)
    except UploadAccessRevoked:
        return fail("No autorizado para subir media", status=403)

    try:
        with db_conn() as conn:
            # Con los receipts del PUT ya confirmados, se vuelve a comprobar
            # que el permiso siga vigente (spec S7 paso 7): pudo revocarse
            # mientras el archivo viajaba.
            access = require_album_capability(conn, album_id, user_id, "upload")
            if not access:
                raise PermissionError("permiso revocado durante la subida")
            lock_exact_duplicate_scope(conn, owner_id=owner_id, sha256=stored["sha256"])
            if not clean["force_duplicate"]:
                match = find_exact_duplicate(
                    conn,
                    owner_id=owner_id,
                    requester_id=user_id,
                    target_album_id=album_id,
                    sha256=stored["sha256"],
                )
                if match:
                    raise ExactDuplicate(match)
            derivado = (stored.get("preview") or {}).get("file_size", 0)
            # Admisión definitiva: ahora se conoce el tamaño real del
            # original Y del derivado, que también ocupa disco y también
            # cuenta -- la admisión temprana solo miraba el original.
            if not reserve_storage_quota(conn, owner_id, stored["file_size"] + derivado):
                raise ValueError("Se alcanzó el límite de almacenamiento de esta cuenta")
            media = insert_media_record(conn, album_id, owner_id, user_id, clean, stored)
            # L12: la actividad y los avisos viajan en ESTA transacción. Si el
            # COMMIT no llega, no queda ni el asset ni un evento que lo anuncie;
            # la compensación del objeto sigue siendo la de siempre.
            record_activity(conn, album_id, user_id, "asset_uploaded", subject_asset_id=media["id"])
            notify_album_upload(conn, album_id, user_id)
    except ExactDuplicate as exc:
        mark_rolled_back(stored["storage_operation_id"])
        remove_media_files([_stored_for_cleanup(stored)])
        return _duplicate_response(exc.match)
    except (ValueError, PermissionError) as exc:
        # Las dos las lanzamos nosotros DENTRO de la transacción, antes de
        # que `db_conn()` intente su commit implícito: para cuando llegan
        # aquí, la transacción ya se deshizo -- no hay ambigüedad sobre si el
        # INSERT quedó escrito. Es seguro compensar ya: revertir el registro
        # durable y borrar lo que el PUT dejó escrito.
        mark_rolled_back(stored["storage_operation_id"])
        remove_media_files([_stored_for_cleanup(stored)])
        if isinstance(exc, PermissionError):
            return fail("No autorizado para subir media", status=403)
        return fail(str(exc), status=400)
    except Exception:
        # Cualquier otra excepción puede venir del COMMIT implícito de
        # `db_conn()` fallando DESPUÉS de que el INSERT ya viajó al servidor
        # (p. ej. la conexión se corta justo al confirmar): un resultado
        # incierto, no un rollback conocido (spec S7, "Registro durable y
        # resultados inciertos"). Borrar el objeto aquí podría dejar una fila
        # real apuntando a algo que ya no existe -- justo el invariante que
        # este módulo no puede romper. El registro se queda "pending": el
        # reconciliador decide después, mirando la base.
        #
        # ponytail: esto trata TODA excepción que no sea ValueError/
        # PermissionError como potencialmente ambigua, aunque algunas (un
        # fallo de `link_tags` a mitad de la transacción, por ejemplo) son en
        # realidad un rollback tan conocido como los de arriba. El costo es
        # solo velocidad de limpieza -- el registro queda "pending" en vez de
        # "rolled_back" un rato más -- nunca corrección: jamás se borra un
        # objeto que podría seguir referenciado. Afinar el criterio exigiría
        # distinguir "excepción nuestra dentro del cuerpo" de "excepción del
        # propio commit", algo que `db_conn()` no expone hoy; hacerlo mejor
        # es trabajo del reconciliador (procesa "pending" igual que
        # "rolled_back"), no de este endpoint.
        raise

    mark_committed(stored["storage_operation_id"])
    return ok(data=dict(media), message="Archivo subido", status=201)


@media_bp.delete("/media/<int:media_id>")
@session_required
def delete_media(media_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        asset, access = require_asset_capability(conn, media_id, user_id, "delete_media")
        if not asset: return fail("Media no encontrada", status=404)
        if not access: return fail("No autorizado para eliminar media", status=403)
        # La papelera es global al asset: sus pertenencias se conservan y
        # restaurar lo devuelve a todos sus albumes.
        execute_safe(conn, "UPDATE assets SET deleted_at = NOW() WHERE id = :media_id AND deleted_at IS NULL", {"media_id": media_id})
    return ok(message="Media enviada a papelera")


@media_bp.patch("/media/<int:media_id>/favorite")
@session_required
def toggle_favorite(media_id: int):
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    is_favorite = payload.get("is_favorite")
    if not isinstance(is_favorite, bool): return fail("is_favorite debe ser boolean", status=400)
    with db_conn() as conn:
        asset, access = require_asset_capability(conn, media_id, user_id, "organize")
        if not asset: return fail("Media no encontrada", status=404)
        if not access: return fail("No autorizado para gestionar favoritos", status=403)
        updated = execute_safe(conn, "UPDATE assets SET is_favorite = :is_favorite WHERE id = :media_id RETURNING id, is_favorite", {"is_favorite": is_favorite, "media_id": media_id}).mappings().first()
    return ok(data={**dict(updated), "album_id": access["album_id"]}, message="Favorito actualizado")


@media_bp.patch("/media/<int:media_id>/archive")
@session_required
def set_media_archived(media_id: int):
    """Archivar conserva sin mostrar en Biblioteca/Inicio; no es la papelera."""
    user_id = current_user_id()
    payload = request.get_json(silent=True) or {}
    archived = payload.get("archived")
    if not isinstance(archived, bool): return fail("archived debe ser boolean", status=400)
    with db_conn() as conn:
        # Solo media activa: sobre la papelera, la papelera gana.
        asset, access = require_asset_capability(conn, media_id, user_id, "organize")
        if not asset: return fail("Media no encontrada", status=404)
        if not access: return fail("No autorizado para archivar media", status=403)
        # COALESCE: archivar dos veces conserva la fecha del primer archivado.
        state = "COALESCE(archived_at, NOW())" if archived else "NULL"
        updated = execute_safe(conn, f"UPDATE assets SET archived_at = {state} WHERE id = :media_id AND deleted_at IS NULL RETURNING id, archived_at", {"media_id": media_id}).mappings().first()
    if not updated: return fail("Media no encontrada", status=404)
    return ok(data={**dict(updated), "album_id": access["album_id"]}, message="Media archivada" if archived else "Media desarchivada")


@media_bp.get("/trash")
@session_required
def list_trash():
    user_id = current_user_id()
    file_type = (request.args.get("file_type") or "").strip().lower(); q = (request.args.get("q") or "").strip().lower()
    page, per_page, offset = get_pagination_args(request)
    if file_type and file_type not in {"image", "video"}: return fail("file_type debe ser image o video", status=400)
    where_clauses = ["m.user_id = :user_id", ACTIVE_SCOPE_SQL, "m.deleted_at IS NOT NULL"]
    params = {"user_id": user_id, "limit": per_page, "offset": offset}
    if file_type: where_clauses.append("m.file_type = :file_type"); params["file_type"] = file_type
    if q: where_clauses.append("(LOWER(COALESCE(m.title, '')) LIKE :q OR LOWER(COALESCE(m.caption, '')) LIKE :q)"); params["q"] = f"%{q}%"
    where_sql = " AND ".join(where_clauses)
    with db_conn() as conn:
        total = execute_safe(conn, f"SELECT COUNT(*) AS total FROM assets m WHERE {where_sql}", params).mappings().first()["total"]
        rows = execute_safe(conn, f"""
            SELECT m.id, ctx.album_id, m.file_type, m.title, m.caption, m.deleted_at,
                   (m.deleted_at + INTERVAL '30 days') AS purge_at
            FROM assets m {context_album_join()}
            WHERE {where_sql} ORDER BY m.deleted_at DESC LIMIT :limit OFFSET :offset
        """, params).mappings().all()
    return ok(data=[dict(r) for r in rows], message="Papelera", pagination=build_pagination_meta(page, per_page, total))


LIBRARY_RECENT_LIMIT = 8


def _library_scope(archived_clause: str) -> str:
    """El alcance ÚNICO de Biblioteca: una fila por asset aunque esté en
    varios álbumes, los sueltos también, nada de la papelera. Lo comparten la
    cronología y «Añadidos recientemente» (F02) para que nunca diverjan."""
    return f"""
        m.user_id = :user_id
        AND {ACTIVE_SCOPE_SQL}
        AND m.deleted_at IS NULL
        AND {archived_clause}
    """


@media_bp.get("/media/library/recent")
@session_required
def list_recent_library_media():
    """«Añadidos recientemente» de Biblioteca (F02; antes en Inicio): lo
    último que entró en la biblioteca propia, por fecha de subida. Mismo
    alcance que la cronología, sin lo archivado. Tamaño fijo, sin cursor."""
    with db_conn() as conn:
        rows = execute_safe(
            conn,
            f"""
            SELECT m.id, m.user_id, ctx.album_id, m.file_type, m.title,
                   m.caption, m.is_favorite, m.taken_at, m.created_at, m.archived_at,
                   ctx.album_titulo
            FROM assets m {context_album_join()}
            WHERE {_library_scope("m.archived_at IS NULL")}
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT :limit
            """,
            {"user_id": current_user_id(), "limit": LIBRARY_RECENT_LIMIT},
        ).mappings().all()
    return ok(data=[dict(row) for row in rows], message="Añadidos recientemente")


@media_bp.get("/media/library")
@session_required
def list_library_media():
    """Return the owner's active media in a stable chronological timeline."""
    user_id = current_user_id()
    limit = get_library_limit(request.args.get("limit"))
    try:
        cursor = decode_library_cursor(request.args.get("cursor"))
    except LibraryCursorError:
        return fail("Cursor de biblioteca invalido", status=400)

    archived_mode = request.args.get("archived")
    if archived_mode not in (None, "", "only"):
        return fail("archived solo admite only", status=400)
    # Biblioteca oculta lo archivado; la pagina Archivo es esta misma lectura
    # con solo lo archivado. Dos literales fijos, nada de la peticion.
    archived_clause = "m.archived_at IS NOT NULL" if archived_mode == "only" else "m.archived_at IS NULL"

    cursor_clause = ""
    page_params = {"user_id": user_id, "limit": limit + 1}
    if cursor is not None:
        cursor_date, cursor_id = cursor
        page_params.update(cursor_date=cursor_date, cursor_id=cursor_id)
        cursor_clause = """
            AND (
                COALESCE(m.taken_at, m.created_at) < :cursor_date
                OR (
                    COALESCE(m.taken_at, m.created_at) = :cursor_date
                    AND m.id < :cursor_id
                )
            )
        """

    owner_scope = _library_scope(archived_clause)
    with db_conn() as conn:
        total = execute_safe(
            conn,
            f"""
            SELECT COUNT(*) AS total
            FROM assets m
            WHERE {owner_scope}
            """,
            {"user_id": user_id},
        ).mappings().first()["total"]
        rows = execute_safe(
            conn,
            f"""
            SELECT m.id, m.user_id, ctx.album_id, m.file_type, m.title,
                   m.caption, m.is_favorite, m.taken_at, m.created_at, m.archived_at,
                   COALESCE(m.taken_at, m.created_at) AS effective_date,
                   ctx.album_titulo
            FROM assets m {context_album_join()}
            WHERE {owner_scope} {cursor_clause}
            ORDER BY effective_date DESC, m.id DESC
            LIMIT :limit
            """,
            page_params,
        ).mappings().all()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_library_cursor(last["effective_date"], last["id"])
    return ok(
        data=[dict(row) for row in page_rows],
        message="Biblioteca",
        next_cursor=next_cursor,
        total=total,
        limit=limit,
    )


@media_bp.get("/media/search")
@session_required
def search_media():
    """Busca en el índice privado del dueño y aplica filtros estructurados.

    El documento se mantiene al escribir título, archivo, EXIF, contexto,
    OCR o tags. Esta lectura no llama proveedores ni arma SQL con valores de
    la petición.
    """
    user_id = current_user_id()
    try:
        filters = parse_search_filters(request.args)
    except ValueError as exc:
        return fail(str(exc), status=400)
    page, per_page, offset = get_pagination_args(request)
    with db_conn() as conn:
        total, rows = run_media_search(conn, filters, user_id, limit=per_page, offset=offset)
    return ok(data=rows, message="Resultados", pagination=build_pagination_meta(page, per_page, total))


@media_bp.get("/media/favorites")
@session_required
def list_favorites():
    user_id = current_user_id(); page, per_page, offset = get_pagination_args(request)
    params = {"user_id": user_id, "limit": per_page, "offset": offset}
    where_sql = f"m.user_id=:user_id AND {ACTIVE_SCOPE_SQL} AND m.deleted_at IS NULL AND m.is_favorite=TRUE"
    with db_conn() as conn:
        total = execute_safe(conn, f"SELECT COUNT(*) AS total FROM assets m WHERE {where_sql}", params).mappings().first()["total"]
        rows = execute_safe(conn, f"""
            SELECT m.id, ctx.album_id, m.file_type, m.title, m.caption, m.taken_at, m.created_at, m.is_favorite, m.archived_at
            FROM assets m {context_album_join()}
            WHERE {where_sql}
            ORDER BY m.created_at DESC LIMIT :limit OFFSET :offset
        """, params).mappings().all()
    return ok(data=[dict(r) for r in rows], message="Favoritos", pagination=build_pagination_meta(page, per_page, total))


@media_bp.get("/media/public")
@session_required
def list_public_media():
    """Fotos y videos recientes de álbumes públicos de cualquier cuenta, para
    el feed del inicio. Es la versión a nivel de foto de los álbumes públicos
    — el usuario pidió ver las fotos en sí, no solo la portada de cada álbum."""
    sort_by = (request.args.get("sort_by") or "created_at").strip().lower()
    sort_dir = (request.args.get("sort_dir") or "desc").strip().lower()
    if sort_dir not in {"asc", "desc"}:
        return fail("sort_dir debe ser asc o desc", status=400)
    try:
        sort_by_sql = safe_identifier(sort_by, {"created_at", "taken_at"})
    except ValueError:
        return fail("sort_by inválido", status=400)
    page, per_page, offset = get_pagination_args(request)
    params = {"limit": per_page, "offset": offset}

    # Lo archivado sale del flujo comunitario; el album publico lo sigue mostrando.
    # Una fila por asset aunque este en varios albumes publicos.
    where_sql = (
        "m.deleted_at IS NULL AND m.archived_at IS NULL AND "
        + in_active_album_sql("AND ia.is_private = FALSE")
    )
    with db_conn() as conn:
        total = execute_safe(
            conn,
            f"SELECT COUNT(*) AS total FROM assets m WHERE {where_sql}",
            {},
        ).mappings().first()["total"]

        rows = execute_safe(
            conn,
            f"""
            SELECT m.id, ctx.album_id, m.file_type, m.title, m.caption, m.taken_at, m.created_at,
                   u.id AS owner_id, u.username AS owner_username,
                   u.full_name AS owner_full_name, u.avatar_path AS owner_avatar_path
            FROM assets m
            {context_album_join(extra="AND ca.is_private = FALSE")}
            JOIN users u ON u.id = m.user_id AND u.active = TRUE
            WHERE {where_sql}
            ORDER BY m.{sort_by_sql} {sort_dir.upper()} NULLS LAST, m.id DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).mappings().all()

    data = [
        {
            "id": row["id"],
            "album_id": row["album_id"],
            "file_type": row["file_type"],
            "title": row["title"],
            "caption": row["caption"],
            "taken_at": row["taken_at"],
            "created_at": row["created_at"],
            "owner": {
                "id": row["owner_id"],
                "username": row["owner_username"],
                "full_name": row["owner_full_name"] or row["owner_username"],
                "has_avatar": bool(row["owner_avatar_path"]),
            },
        }
        for row in rows
    ]
    return ok(data=data, message="Fotos públicas recientes", pagination=build_pagination_meta(page, per_page, total))


@media_bp.post("/media/<int:media_id>/restore")
@session_required
def restore_media(media_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        asset, access = require_asset_permission(conn, media_id, user_id, "owner", trashed=True)
        if not asset: return fail("Media no encontrada en papelera", status=404)
        if not access: return fail("Solo el dueño puede restaurar", status=403)
        # Las pertenencias nunca se tocaron: vuelve a todos sus albumes.
        execute_safe(conn, "UPDATE assets SET deleted_at=NULL WHERE id=:media_id", {"media_id": media_id})
    return ok(message="Media restaurada")


@media_bp.delete("/media/<int:media_id>/permanent")
@session_required
def delete_media_permanently(media_id: int):
    user_id = current_user_id()
    with db_conn() as conn:
        deleted = _purge_media_rows(
            conn,
            f"m.id = :media_id AND m.deleted_at IS NOT NULL AND m.user_id = :user_id AND {ACTIVE_SCOPE_SQL}",
            {"media_id": media_id, "user_id": user_id},
        )
        if not deleted:
            return fail("Media no encontrada en tu papelera", status=404)
        # Intención durable antes de tocar objetos: si el proceso muere entre
        # el commit y el borrado físico, el reconciliador sabe qué falta.
        claves = [c for i in deleted for c in (i.get("storage_path"), i.get("preview_storage_path")) if c]
        operacion = record_pending("delete", claves) if claves else None

    borrados, pendientes = remove_media_files(deleted)
    if operacion and not pendientes:
        mark_committed(operacion)
    datos = {"removed_files": borrados}
    if pendientes:
        datos["storage_cleanup_pending"] = True
        return ok(data=datos, message="Media eliminada permanentemente", status=202)
    return ok(data=datos, message="Media eliminada permanentemente")


@media_bp.post("/trash/restore-all")
@session_required
def restore_all_trash():
    user_id = current_user_id()
    with db_conn() as conn:
        restored = execute_safe(
            conn,
            f"""
            UPDATE assets AS m
            SET deleted_at = NULL
            WHERE m.user_id = :user_id
              AND {ACTIVE_SCOPE_SQL}
              AND m.deleted_at IS NOT NULL
            RETURNING m.id
            """,
            {"user_id": user_id},
        ).mappings().all()
    return ok(data={"restored_count": len(restored)}, message="Papelera restaurada")


@media_bp.delete("/trash")
@session_required
def delete_all_trash():
    user_id = current_user_id()
    with db_conn() as conn:
        deleted = _purge_media_rows(
            conn,
            f"m.user_id = :user_id AND {ACTIVE_SCOPE_SQL} AND m.deleted_at IS NOT NULL",
            {"user_id": user_id},
        )
        # Intención durable antes de tocar objetos: si el proceso muere entre
        # el commit y el borrado físico, el reconciliador sabe qué falta.
        claves = [c for i in deleted for c in (i.get("storage_path"), i.get("preview_storage_path")) if c]
        operacion = record_pending("delete", claves) if claves else None

    borrados, pendientes = remove_media_files(deleted)
    if operacion and not pendientes:
        mark_committed(operacion)
    datos = {"deleted_count": len(deleted), "removed_files": borrados}
    if pendientes:
        datos["storage_cleanup_pending"] = True
        return ok(data=datos, message="Papelera vaciada permanentemente", status=202)
    return ok(data=datos, message="Papelera vaciada permanentemente")
