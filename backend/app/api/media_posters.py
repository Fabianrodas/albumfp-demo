"""Portada (poster) de un video, v1.1.

El servidor no puede sacar fotogramas de un video: FFmpeg/libav romperían la
regla de portabilidad (nada instalado a nivel de sistema). El fotograma lo
captura el NAVEGADOR (seek + canvas) y lo sube aquí como imagen; el servidor
lo trata como cualquier foto no confiable: cuarentena, firma de bytes,
decodificación real con Pillow y recodificación a WebP (sin EXIF).

La portada se guarda como la VISTA PREVIA del video (`media_metadata.
preview_*`), así que no hay migración: la cuadrícula, las portadas de álbum,
los enlaces públicos, la cuota y todos los borrados (papelera, purga, cuenta)
ya saben manejar ese derivado.

Orden de escritura, el mismo criterio que la subida: primero el objeto nuevo,
después la fila, y solo tras el COMMIT se borra el objeto viejo. Si la fila
falla de forma conocida se borra el objeto nuevo; un fallo ambiguo (crash a
mitad del COMMIT) deja como mucho un objeto huérfano, que es justo lo que
barre `cleanup-orphaned-media`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import request

from ..db.db import db_conn
from ..media.assets import require_asset_capability, require_asset_permission
from ..media.image_validation import ImageValidationError, validate_image
from ..media.previews import render_image_preview
from ..security.sessions import current_user_id, session_required
from ..storage.backends import get_storage_backend
from ..storage.media_storage import detect_media_signature, remove_stored_file
from ..storage.object_keys import build_object_key
from ..storage.quarantine import quarantined_upload
from ..storage.workspace import local_workspace
from ..utils.responses import fail, ok
from ..utils.sql_security import execute_safe
from .media import media_bp, reserve_storage_quota

# Un fotograma JPEG de 1280px pesa unos cientos de KB; 8 MB deja margen para
# un 4K sin dejar que esta ruta sirva para subir cualquier cosa.
MAX_POSTER_BYTES = 8 * 1024 * 1024
POSTER_FORMATS = {"jpg", "png", "webp"}


@media_bp.put("/media/<int:media_id>/poster")
@session_required
def set_video_poster(media_id: int):
    user_id = current_user_id()

    def _poster_target(conn):
        """(fila, error). Puede poner la portada quien puede editar el recuerdo
        (`edit_media`, el dueño las tiene todas) o quien lo subió mientras
        conserva `upload`: así un colaborador que solo sube también deja su video
        con portada, pero no puede cambiar la de videos ajenos."""
        asset, readable = require_asset_permission(conn, media_id, user_id, "read")
        if not asset or not readable:
            return None, fail("Media no encontrada", status=404)
        row = execute_safe(
            conn,
            "SELECT id, user_id, file_type, storage_path, created_by FROM assets WHERE id = :media_id",
            {"media_id": media_id},
        ).mappings().first()
        _, can_edit = require_asset_capability(conn, media_id, user_id, "edit_media")
        if not can_edit:
            _, can_upload = require_asset_capability(conn, media_id, user_id, "upload")
            if not (can_upload and row["created_by"] == user_id):
                return None, fail("No autorizado para cambiar la portada", status=403)
        if row["file_type"] != "video":
            return None, fail("Solo los videos tienen portada", status=400)
        return row, None

    file = request.files.get("poster")
    if not file or not file.filename:
        return fail("Falta la imagen de la portada", status=400)

    with db_conn() as conn:
        target, error = _poster_target(conn)
    if error:
        return error
    owner_id = target["user_id"]
    scope = Path(target["storage_path"]).parent.name

    # Sin transacción abierta mientras se valida y se escribe el objeto.
    try:
        with quarantined_upload(file, max_bytes=MAX_POSTER_BYTES) as quarantined:
            with open(quarantined.path, "rb") as handle:
                header = handle.read(64)
            kind, fmt = detect_media_signature(header, quarantined.original_filename, file.mimetype)
            if kind != "image" or fmt not in POSTER_FORMATS:
                raise ValueError("La portada debe ser una imagen JPEG, PNG o WebP")
            validate_image(quarantined.path)
            with local_workspace("upload-preview") as workspace:
                rendered = render_image_preview(quarantined.path, workspace.directory, keep_larger=True)
                if rendered is None:
                    raise ValueError("No pudimos procesar la imagen de la portada")
                key = build_object_key(owner_id, scope, "webp")
                get_storage_backend().put_from_path(key, rendered.path)
    except (ValueError, ImageValidationError) as exc:
        return fail(str(exc), status=400)

    try:
        with db_conn() as conn:
            current, error = _poster_target(conn)
            if error:
                raise PermissionError("permiso revocado durante la subida")
            old = execute_safe(
                conn,
                "SELECT preview_storage_path, preview_file_size FROM media_metadata WHERE media_id = :media_id FOR UPDATE",
                {"media_id": media_id},
            ).mappings().first()
            if old is None:
                raise ValueError("Este video no tiene metadatos; vuelve a subirlo")
            extra = max(0, rendered.file_size - (old["preview_file_size"] or 0))
            if not reserve_storage_quota(conn, owner_id, extra):
                raise ValueError("Se alcanzó el límite de almacenamiento de esta cuenta")
            execute_safe(
                conn,
                """
                UPDATE media_metadata
                SET preview_storage_path = :path, preview_mime_type = :mime,
                    preview_width = :width, preview_height = :height, preview_file_size = :size
                WHERE media_id = :media_id
                """,
                {"path": key, "mime": rendered.mime_type, "width": rendered.width,
                 "height": rendered.height, "size": rendered.file_size, "media_id": media_id},
            )
    except (ValueError, PermissionError) as exc:
        remove_stored_file(key)
        if isinstance(exc, PermissionError):
            return fail("No autorizado para cambiar la portada", status=403)
        return fail(str(exc), status=400)

    if old["preview_storage_path"]:
        try:
            remove_stored_file(old["preview_storage_path"])
        except Exception:
            # Ya no lo referencia ninguna fila: lo recoge cleanup-orphaned-media.
            logging.warning("No se pudo borrar la portada anterior del video %s", media_id)

    return ok(
        data={"media_id": media_id, "preview_width": rendered.width, "preview_height": rendered.height},
        message="Portada actualizada",
    )
