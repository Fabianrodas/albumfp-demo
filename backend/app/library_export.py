"""Exportación portable de la biblioteca (L13).

Una cuenta se lleva sus datos en un ZIP que se genera **mientras se descarga**:
stdlib `zipfile` escribe sobre un destino no buscable y cada trozo sale por la
respuesta HTTP en cuanto existe. No hay un archivo con la biblioteca entera en
ningún disco del servidor, ni en RAM: los originales pasan por
`StorageBackend.materialize()` de uno en uno (en el perfil remoto, un objeto a
la vez dentro del área de trabajo acotada; en el local, el archivo real) y se
copian por bloques de `CHUNK_BYTES`.

Estructura:

    albumfp-export/README.txt
    albumfp-export/metadata/albumfp.json         (manifiesto versionado)
    albumfp-export/originals/<id>-<nombre>.<ext> (cada asset UNA vez)
    albumfp-export/metadata/export-report.json   (lo que faltó al copiar)

Un asset en varios álbumes aparece una sola vez: las pertenencias van aparte,
en `memberships`. La papelera no se exporta; lo archivado sí, marcado. EXIF,
ubicación y OCR solo viajan si la persona los pide.
"""
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime

from .comments import export_comments
from .storage.contracts import ObjectNotFound
from .utils.sql_security import execute_safe

SCHEMA = "albumfp-export"
SCHEMA_VERSION = 1
ROOT = "albumfp-export/"
CHUNK_BYTES = 1024 * 1024
_OPTION_NAMES = ("exif", "location", "ocr")
_BOOLEANS = {"true": True, "1": True, "false": False, "0": False}
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

README = """AlbumFP — copia de tu biblioteca
=================================

Esta carpeta es una copia de tus datos en AlbumFP, generada en el momento de
la descarga.

originals/
    Cada foto o video UNA sola vez, con su archivo original tal como lo
    subiste. El nombre empieza por el id del recuerdo, así nunca se pisan dos.
    Un recuerdo que está en varios álbumes no se repite.

metadata/albumfp.json
    Todo lo demás, en JSON: tus álbumes, a qué álbumes pertenece cada
    recuerdo (lista "memberships"), títulos, descripciones, fechas, favoritos,
    archivados, etiquetas, tus álbumes inteligentes y los comentarios de tus
    recuerdos (lista "comments", con el nombre de usuario de quien escribió
    cada uno). EXIF, ubicación y el texto detectado (OCR) solo aparecen si
    los elegiste al exportar.

metadata/export-report.json
    Recuerdos cuyo original no se pudo copiar (lista "missing_asset_ids").
    Normalmente está vacía.

No se incluyen la papelera, las contraseñas, las sesiones, los enlaces
compartidos ni nada de otras cuentas.
"""


@dataclass(frozen=True)
class ExportOptions:
    exif: bool = False
    location: bool = False
    ocr: bool = False


def parse_export_options(args) -> ExportOptions:
    """Allowlist estricta: una clave desconocida o un valor ambiguo es 400,
    nunca «lo ignoro y exporto de más»."""
    extras = set(args) - set(_OPTION_NAMES)
    if extras:
        raise ValueError(f"Opciones de exportación no permitidas: {', '.join(sorted(extras))}")
    values = {}
    for name in _OPTION_NAMES:
        raw = args.get(name)
        if raw is None:
            continue
        if str(raw).lower() not in _BOOLEANS:
            raise ValueError(f"{name} debe ser true o false")
        values[name] = _BOOLEANS[str(raw).lower()]
    return ExportOptions(**values)


def archive_name(asset_id: int, original_filename, file_format) -> str:
    """Ruta dentro del ZIP generada por el servidor. El nombre que mandó el
    cliente solo aporta un trozo legible: sin separadores, sin `..`, sin
    unidad ni NUL, recortado. El id delante evita cualquier colisión y la
    extensión sale del formato ya verificado, no del nombre."""
    base = str(original_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base.strip(".") else base
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_-")[:60] or "asset"
    ext = str(file_format or "").lower()
    if not re.fullmatch(r"[a-z0-9]{1,5}", ext):
        ext = "bin"
    return f"{ROOT}originals/{int(asset_id)}-{stem}.{ext}"


def _iso(value):
    return value.isoformat() if value is not None else None


def _number(value):
    return float(value) if value is not None else None


def build_manifest(conn, user_id: int, options: ExportOptions, generated_at: datetime):
    """(manifiesto, entradas a copiar). Todo acotado al dueño: cada consulta
    lleva `user_id = :user_id` y la papelera queda fuera. Las entradas llevan
    la clave de almacenamiento para el copiado, pero esa clave NUNCA entra en
    el manifiesto."""
    params = {"user_id": user_id}
    account = execute_safe(
        conn, "SELECT username, full_name FROM users WHERE id = :user_id", params,
    ).mappings().first()

    albums = [{
        "id": r["id"], "title": r["titulo"], "description": r["descripcion"],
        "is_private": r["is_private"], "active": r["active"], "cover_asset_id": r["cover_media_id"],
        "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
    } for r in execute_safe(conn, """
        SELECT id, titulo, descripcion, is_private, active, cover_media_id, created_at, updated_at
        FROM albums WHERE user_id = :user_id ORDER BY id
    """, params).mappings().all()]

    smart_albums = [{
        "id": r["id"], "title": r["titulo"], "description": r["descripcion"], "filters": r["filters"],
        "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]),
    } for r in execute_safe(conn, """
        SELECT id, titulo, descripcion, filters, created_at, updated_at
        FROM smart_albums WHERE user_id = :user_id ORDER BY id
    """, params).mappings().all()]

    tags = [dict(r) for r in execute_safe(
        conn, "SELECT id, name FROM tags WHERE owner_id = :user_id ORDER BY id", params,
    ).mappings().all()]

    rows = execute_safe(conn, """
        SELECT m.id, m.storage_path, m.file_type, m.title, m.caption, m.is_favorite, m.taken_at,
               m.created_at, m.archived_at,
               mm.original_filename, mm.format, mm.mime_type, mm.file_size, mm.sha256, mm.resolution,
               mm.duration,
               ex.taken_at_original, ex.camera_make, ex.camera_model, ex.orientation,
               ex.latitude AS exif_latitude, ex.longitude AS exif_longitude, ex.altitude_m,
               ctx.place_display_name, ctx.locality, ctx.region, ctx.country_code, ctx.country_name,
               ctx.latitude AS place_latitude, ctx.longitude AS place_longitude,
               ctx.holiday_name, ctx.sunrise_at, ctx.sunset_at, ctx.weather_temp_c, ctx.weather_condition,
               ocr.extracted_text, ocr.detected_language,
               COALESCE(
                   (SELECT array_agg(mt.tag_id ORDER BY mt.tag_id) FROM media_tags mt WHERE mt.media_id = m.id),
                   '{}'
               ) AS tag_ids
        FROM assets m
        LEFT JOIN media_metadata mm ON mm.media_id = m.id
        LEFT JOIN media_exif ex ON ex.media_id = m.id
        LEFT JOIN media_context ctx ON ctx.media_id = m.id
        LEFT JOIN media_ocr ocr ON ocr.media_id = m.id
        WHERE m.user_id = :user_id AND m.deleted_at IS NULL
        ORDER BY m.id
    """, params).mappings().all()

    assets, entries = [], []
    for r in rows:
        path = archive_name(r["id"], r["original_filename"], r["format"])
        asset = {
            "id": r["id"], "media_type": r["file_type"], "title": r["title"], "description": r["caption"],
            "original_filename": r["original_filename"], "archive_path": path, "format": r["format"],
            "mime_type": r["mime_type"], "file_size": r["file_size"], "sha256": r["sha256"],
            "resolution": r["resolution"], "duration_seconds": r["duration"],
            "taken_at": _iso(r["taken_at"]), "uploaded_at": _iso(r["created_at"]),
            "favorite": bool(r["is_favorite"]), "archived_at": _iso(r["archived_at"]),
            "tag_ids": list(r["tag_ids"] or []),
        }
        if options.exif:
            asset["exif"] = {
                "taken_at_original": _iso(r["taken_at_original"]), "camera_make": r["camera_make"],
                "camera_model": r["camera_model"], "orientation": r["orientation"],
            }
        if options.location:
            asset["location"] = {
                "latitude": _number(r["place_latitude"] if r["place_latitude"] is not None else r["exif_latitude"]),
                "longitude": _number(r["place_longitude"] if r["place_longitude"] is not None else r["exif_longitude"]),
                "altitude_m": _number(r["altitude_m"]),
                "place": r["place_display_name"], "locality": r["locality"], "region": r["region"],
                "country_code": r["country_code"], "country_name": r["country_name"],
                "holiday": r["holiday_name"], "sunrise_at": _iso(r["sunrise_at"]), "sunset_at": _iso(r["sunset_at"]),
                "weather_temp_c": _number(r["weather_temp_c"]), "weather_condition": r["weather_condition"],
            }
        if options.ocr:
            asset["ocr"] = {"text": r["extracted_text"], "language": r["detected_language"]}
        assets.append(asset)
        entries.append({"asset_id": r["id"], "storage_path": r["storage_path"], "archive_path": path,
                        "created_at": r["created_at"]})

    memberships = [{
        "album_id": r["album_id"], "asset_id": r["asset_id"], "position": r["position"],
        "added_at": _iso(r["added_at"]),
    } for r in execute_safe(conn, """
        SELECT aa.album_id, aa.asset_id, aa.position, aa.added_at
        FROM album_assets aa
        JOIN assets m ON m.id = aa.asset_id AND m.deleted_at IS NULL
        WHERE aa.owner_id = :user_id
        ORDER BY aa.album_id, aa.position NULLS LAST, aa.asset_id
    """, params).mappings().all()]

    # F04: los comentarios de los recuerdos exportados, de quien sea que los
    # escribiera; antes del primer despliegue del export, así que sigue en v1.
    comments = [{
        "id": c["id"], "asset_id": c["asset_id"],
        "author": {"username": c["username"], "full_name": c["full_name"]},
        "body": c["body"], "created_at": _iso(c["created_at"]), "updated_at": _iso(c["updated_at"]),
    } for c in export_comments(conn, user_id)]

    manifest = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "generated_at": generated_at.isoformat(),
        "account": {"username": account["username"], "full_name": account["full_name"]},
        "options": {"exif": options.exif, "location": options.location, "ocr": options.ocr},
        "counts": {"albums": len(albums), "smart_albums": len(smart_albums), "assets": len(assets),
                   "memberships": len(memberships), "tags": len(tags), "comments": len(comments)},
        "albums": albums, "smart_albums": smart_albums, "tags": tags, "assets": assets,
        "memberships": memberships, "comments": comments,
    }
    return manifest, entries


class _Sink:
    """Destino no buscable para `zipfile`: acumula lo escrito hasta que el
    generador lo entrega. Sin `tell()`/`seek()`, `zipfile` escribe cada
    entrada con descriptor de datos y nunca vuelve atrás."""

    def __init__(self):
        self._parts: list[bytes] = []

    def write(self, data) -> int:
        self._parts.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = b"".join(self._parts)
        self._parts.clear()
        return data


def _zip_time(value) -> tuple:
    if not isinstance(value, datetime) or value.year < 1980:
        return _ZIP_EPOCH
    return value.timetuple()[:6]


def stream_export(entries, manifest: dict, backend):
    """Generador de bytes del ZIP. Cerrarlo a mitad (el cliente se desconecta)
    sale del `materialize()` en curso, así que nada queda retenido."""
    sink = _Sink()
    stamp = _zip_time(datetime.fromisoformat(manifest["generated_at"])) if manifest.get("generated_at") else _ZIP_EPOCH
    with zipfile.ZipFile(sink, "w", allowZip64=True) as archive:
        def text_entry(name: str, text: str):
            info = zipfile.ZipInfo(ROOT + name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text.encode("utf-8"))

        text_entry("README.txt", README)
        text_entry("metadata/albumfp.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        yield sink.drain()

        missing = []
        for entry in entries:
            try:
                with backend.materialize(entry["storage_path"]) as real_path:
                    # Los originales ya vienen comprimidos (JPEG, HEIC, MP4...):
                    # se guardan tal cual y no se gasta CPU en desinflarlos.
                    info = zipfile.ZipInfo(entry["archive_path"], _zip_time(entry["created_at"]))
                    info.compress_type = zipfile.ZIP_STORED
                    with real_path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                        while chunk := source.read(CHUNK_BYTES):
                            target.write(chunk)
                            yield sink.drain()
            except ObjectNotFound:
                missing.append(entry["asset_id"])
                continue
            yield sink.drain()

        text_entry("metadata/export-report.json", json.dumps({"missing_asset_ids": missing}, indent=2))
    yield sink.drain()
