from flask import request
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from .storage.contracts import InvalidStorageKey, ObjectNotFound, StorageError
from .utils.responses import fail


def register_error_handlers(app):
    @app.errorhandler(RequestEntityTooLarge)
    def handle_upload_too_large(e):
        # `or 0` y no `.get(clave, 0)`: Flask define MAX_CONTENT_LENGTH con
        # valor None, asi que la clave EXISTE y el default del `get` nunca se
        # usa. Sin esto, dividir None reventaba el propio manejador y devolvia
        # un 500 en vez del 413 limpio.
        #
        # Desde S03 este mismo error dispara por DOS motivos distintos: un
        # archivo que supera MAX_IMAGE_MB/MAX_VIDEO_MB, o un cuerpo JSON/
        # formulario que supera MAX_JSON_BODY_KB (`request.max_content_length`,
        # fijado por peticion en `app.py`, no en el `MAX_CONTENT_LENGTH`
        # global que sigue sin fijar). El mensaje no puede decir siempre
        # "archivo" porque la segunda causa nunca es un archivo.
        es_multipart = (request.content_type or "").startswith("multipart/form-data")
        limit_bytes = (app.config.get("MAX_CONTENT_LENGTH") or request.max_content_length or 0)
        sujeto = "El archivo" if es_multipart else "La solicitud"
        # KB para el tope de JSON (cientos de KB) y MB para el de archivo
        # (decenas/cientos de MB): mostrar el JSON en MB redondearia a "0 MB".
        if limit_bytes >= 1024 * 1024:
            medida = f"{int(limit_bytes / (1024 * 1024))} MB"
        elif limit_bytes:
            medida = f"{int(limit_bytes / 1024)} KB"
        else:
            medida = None
        message = f"{sujeto} supera el límite permitido de {medida}" if medida else f"{sujeto} supera el límite permitido"
        return fail(message, status=413, code=413)

    # 1) Errores HTTP estándar (abort(404), etc.)
    @app.errorhandler(HTTPException)
    def handle_http_exception(e: HTTPException):
        return fail(e.description, status=e.code, code=e.code)

    # 2) Conflicto de integridad (UNIQUE, FK, etc.)
    @app.errorhandler(IntegrityError)
    def handle_integrity_error(e):
        return fail("Conflicto: registro duplicado o referencia inválida", status=409, code=409)

    @app.errorhandler(OperationalError)
    def handle_database_unavailable(e):
        app.logger.error("Base de datos no disponible", exc_info=True)
        return fail("Servicio de base de datos no disponible", status=503, code=503)

    # Un fallo del almacenamiento es un servicio temporalmente no disponible:
    # devuelve 503 para que el cliente pueda reintentar. El mensaje es fijo
    # para que los detalles internos no viajen al navegador.
    @app.errorhandler(ObjectNotFound)
    def handle_object_missing(e):
        return fail("Archivo no encontrado", status=404, code=404)

    @app.errorhandler(InvalidStorageKey)
    def handle_invalid_storage_key(e):
        return fail("Ruta de archivo inválida", status=400, code=400)

    @app.errorhandler(StorageError)
    def handle_storage_unavailable(e):
        app.logger.error("Almacenamiento no disponible", exc_info=True)
        return fail(
            "El almacenamiento de fotos no está disponible ahora mismo",
            status=503,
            code="media_storage_unavailable",
        )

    # 3) Fallback general (cualquier excepción no controlada)
    @app.errorhandler(Exception)
    def handle_exception(e):
        app.logger.error(str(e), exc_info=True)
        return fail("Internal server error", status=500, code=500)
