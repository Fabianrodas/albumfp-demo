from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from ..media.image_validation import ImageValidationError, validate_image
from .backends import get_storage_backend, storage_backend_mode
from .contracts import InvalidStorageKey, ObjectConflict, StorageConfigurationError
from .object_keys import validate_storage_key

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
# Tests may temporarily override this with a Path. In normal execution it stays
# as None so the current environment is resolved at call time.
_STORAGE_ROOT: Path | None = None


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser numérico") from exc


def _configured_storage_root() -> Path:
    if _STORAGE_ROOT is not None:
        return Path(_STORAGE_ROOT).expanduser().resolve()

    configured = (os.getenv("MEDIA_STORAGE_ROOT") or "../../albumfp-demo-runtime/media").strip()
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = _BACKEND_ROOT / candidate
    return candidate.resolve()


def storage_root() -> Path:
    """Raiz local del arbol final. Solo existe con el backend local.

    El guard va ANTES de resolver nada: en remote los bytes viven en el local workstation,
    asi que ni se calcula una raiz ni se crea el directorio vacio que la
    delataria en el VPS (spec 6 y 12).
    """
    if _STORAGE_ROOT is None and storage_backend_mode() != "local":
        raise StorageConfigurationError(
            "storage_root() solo existe con MEDIA_STORAGE_BACKEND=local"
        )
    root = _configured_storage_root()

    expected_mount = (os.getenv("MEDIA_EXPECTED_MOUNTPOINT") or "").strip()
    if expected_mount:
        mount = Path(expected_mount).expanduser().resolve()
        if not os.path.ismount(mount):
            raise RuntimeError(f"El almacenamiento esperado no está montado en {mount}")
        if root != mount and mount not in root.parents:
            raise RuntimeError("MEDIA_STORAGE_ROOT debe estar dentro de MEDIA_EXPECTED_MOUNTPOINT")

    root.mkdir(parents=True, exist_ok=True)
    return root


def storage_capacity_status(required_bytes: int = 0) -> dict:
    """Return current storage capacity policy without changing app behavior by default.

    Development remains unrestricted unless MEDIA_MAX_USAGE_PERCENT and/or
    MEDIA_MIN_FREE_GB are configured. Production examples enable both.
    """
    capacidad = get_storage_backend().capacity()
    required = max(0, int(required_bytes or 0))

    # 100 and 0 intentionally preserve the historic development behavior.
    max_usage_percent = _env_float("MEDIA_MAX_USAGE_PERCENT", 100.0)
    min_free_gb = _env_float("MEDIA_MIN_FREE_GB", 0.0)
    if not 0 < max_usage_percent <= 100:
        raise RuntimeError("MEDIA_MAX_USAGE_PERCENT debe estar entre 0 y 100")
    if min_free_gb < 0:
        raise RuntimeError("MEDIA_MIN_FREE_GB no puede ser negativo")

    min_free_bytes = int(min_free_gb * 1024**3)
    total, usado, libre = capacidad.total_bytes, capacidad.used_bytes, capacidad.free_bytes
    projected_used = min(total, usado + required)
    projected_free = max(0, libre - required)
    projected_percent = (projected_used / total * 100) if total else 100.0

    allowed = (
        capacidad.mount_ok
        and capacidad.writable
        and required <= libre
        and projected_percent <= max_usage_percent
        and projected_free >= min_free_bytes
    )
    return {
        "allowed": allowed,
        "total": total,
        "used": usado,
        "free": libre,
        "required_bytes": required,
        "projected_used_percent": round(projected_percent, 2),
        "max_usage_percent": max_usage_percent,
        "min_free_bytes": min_free_bytes,
    }


def ensure_storage_capacity(required_bytes: int = 0) -> dict:
    status = storage_capacity_status(required_bytes)
    if not status["allowed"]:
        raise ValueError("No hay espacio suficiente en el almacenamiento para completar la subida")
    return status


def safe_filename(filename: str) -> str:
    base = Path(filename or "").name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")


def _iso_bmff_brands(header: bytes) -> set[bytes]:
    """Devuelve major/compatible brands de una caja ``ftyp`` inicial.

    HEIF y AVIF comparten el contenedor ISO-BMFF. Mirar una subcadena suelta
    permitiria confundir datos en otra caja con una firma; aqui se exige la
    posicion, el tamano declarado y brands completos de cuatro bytes.
    """
    if len(header) < 16 or header[4:8] != b"ftyp":
        return set()
    box_size = int.from_bytes(header[:4], "big")
    if box_size < 16:
        return set()
    available = min(box_size, len(header))
    brands = {header[8:12]}
    brands.update(header[offset:offset + 4] for offset in range(16, available - 3, 4))
    return brands


def detect_media_signature(header: bytes, filename: str, mimetype: str | None) -> tuple[str, str]:
    name = safe_filename(filename).lower()
    suffix = Path(name).suffix.lower().lstrip(".")
    mime = (mimetype or "").lower()

    if header.startswith(b"\xff\xd8\xff") and suffix in {"jpg", "jpeg"} and mime in {"image/jpeg", "image/jpg", ""}:
        return "image", "jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n") and suffix == "png" and mime in {"image/png", ""}:
        return "image", "png"
    if header.startswith((b"GIF87a", b"GIF89a")) and suffix == "gif" and mime in {"image/gif", ""}:
        return "image", "gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP" and suffix == "webp" and mime in {"image/webp", ""}:
        return "image", "webp"
    if b"ftypavif" in header[:32] and suffix == "avif" and mime in {"image/avif", ""}:
        return "image", "avif"
    # ``mif1``/``msf1`` identifican el contenedor generico y tambien pueden
    # aparecer en AVIF. Exigimos ademas un brand HEVC de imagen estatica para
    # no admitir un AVIF renombrado ni una secuencia HEVC (`hevc`/`hevx`) que
    # Pillow reduciria silenciosamente a su frame primario. La decodificacion
    # profunda decide despues si los bytes completos son una imagen valida.
    heif_brands = _iso_bmff_brands(header)
    heif_mimes = {
        "heic": {"image/heic", ""},
        "heif": {"image/heif", ""},
    }
    if (
        heif_brands.intersection({b"heic", b"heix"})
        and not heif_brands.intersection({b"hevc", b"hevx"})
        and suffix in heif_mimes
        and mime in heif_mimes[suffix]
    ):
        return "image", suffix

    if b"ftyp" in header[:16] and suffix in {"mp4", "mov", "m4v"} and (mime.startswith("video/") or not mime):
        return "video", "mov" if suffix == "mov" else "mp4"
    if header.startswith(b"\x1aE\xdf\xa3") and suffix in {"webm", "mkv"} and (mime.startswith("video/") or not mime):
        return "video", "webm"
    if header.startswith(b"OggS") and suffix in {"ogv", "ogg"} and (mime.startswith("video/") or not mime):
        return "video", "ogg"

    raise ValueError("Archivo no compatible: selecciona una foto o video admitido")


# El MIME que declara el cliente (`file.mimetype`, la cabecera Content-Type de
# esa parte del multipart) es dato NO CONFIABLE -- viaja en el propio request
# y no pasa por ninguna verificacion. Antes de esta fase se guardaba tal cual
# y se servia de vuelta como Content-Type real; el MIME canonico, derivado
# del formato que `detect_media_signature` ya verifico por firma de bytes, es
# el unico que se persiste y se sirve.
_CANONICAL_MIME_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "heic": "image/heic",
    "heif": "image/heif",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "ogg": "video/ogg",
}


def canonical_mime_type(file_format: str) -> str:
    return _CANONICAL_MIME_TYPES.get(file_format, "application/octet-stream")


def _validate_quarantined_file(path: Path, file_type: str):
    """Corre DESPUES de cuarentena y ANTES de promover: decodificacion
    profunda con Pillow para imagenes -- no basta con que la cabecera del
    archivo parezca valida. Nunca deja pasar un archivo corrupto o un
    decompression bomb -- lanza ValueError, que los dos llamadores
    (create_media, upload_my_avatar) ya saben traducir a un 400.

    Devuelve un `ValidatedImage` (con las dimensiones REALES, nunca las que
    haya declarado el cliente) para fotos, o `None` para cualquier otro tipo.

    Deliberadamente NO hay aqui ningun escaner de malware ni validacion de
    video: ambos se probaron (ClamAV en S06, ffprobe en S07) y se retiraron
    el mismo dia porque los dos exigen un binario/daemon instalado a nivel
    de sistema operativo, algo que no viaja con `pip install` y que podria
    no estar disponible -- o ser dificil de instalar -- en un host cloud
    gestionado. Ver la nota de portabilidad en CLAUDE.md antes de reintroducir
    algo asi.
    """
    if file_type != "image":
        return None
    try:
        return validate_image(path)
    except ImageValidationError as exc:
        raise ValueError(str(exc)) from exc


def upload_size(file) -> int:
    """Measure an upload without consuming the stream."""
    stream = getattr(file, "stream", None)
    if stream is None:
        return int(getattr(file, "content_length", 0) or 0)
    try:
        current = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(current)
        return int(size)
    except (AttributeError, OSError):
        return int(getattr(file, "content_length", 0) or 0)


def save_upload(
    file,
    owner_id: int,
    album_id: int | None = None,
    signature: tuple[str, str] | None = None,
    *,
    folder: str | None = None,
    max_bytes: int = 0,
    prepare_image_metadata: bool = False,
    before_store: Callable[[str], None] | None = None,
) -> dict:
    """Guarda una subida bajo `user_<id>/<folder o album_<id>>/`.

    `folder` permite reutilizar esta función para archivos que no pertenecen a
    ningún álbum, como la foto de perfil, sin duplicar la escritura atómica.

    Los bytes pasan primero por cuarentena (`app/storage/quarantine.py`):
    se copian por bloques -- nunca se cargan enteros en memoria -- y solo se
    persisten al backend seleccionado después. `max_bytes` es un tope
    adicional verificado DURANTE la copia (0 = sin tope, el criterio de
    siempre); el llamador ya suele hacer su propia comprobación con
    `upload_size()` antes de llegar aquí, así que esto es una segunda barrera
    por si esa medición no reflejara los bytes reales.

    Entre cuarentena y persistencia corre `_validate_quarantined_file()`:
    decodificación profunda con Pillow para imágenes. Puede lanzar
    `ValueError` (archivo corrupto o que no supera la validación, 400 para
    quien llama) -- y eso pasa ANTES de registrar ninguna operación de
    compensación y ANTES de cualquier PUT.

    Con `prepare_image_metadata=True` (fotos de álbum, no avatar) el EXIF y
    la vista previa se calculan MIENTRAS el original sigue en cuarentena, en
    vez de subir primero y volver a materializar el original para generarlos
    después (spec §7: "mientras existe una copia local validada, procesa
    ahí"). Así el original recién subido nunca se descarga de vuelta del
    backend. El dict de retorno gana entonces `exif: dict` y
    `preview: dict | None`; sin ese flag, ninguna de las dos claves aparece.

    `storage_operation_id` identifica el registro de compensación (spec §7)
    que lista las keys que este PUT se propone escribir -- original y, si
    aplica, vista previa -- ANTES del primer PUT. Un fallo entre registrar y
    terminar de escribir marca la operación `rolled_back` y no deja ningún
    objeto a medio escribir en el backend; quien llama (Task 19) decide
    cuándo marcarla `committed`, una vez el COMMIT de la fila en Postgres ya
    sucedió.
    """
    if not file or not file.filename:
        raise ValueError("Debes seleccionar una foto o video")

    if signature is None:
        file.stream.seek(0)
        header = file.stream.read(64)
        file.stream.seek(0)
        signature = detect_media_signature(header, file.filename, file.mimetype)
    file_type, file_format = signature

    from ..media.exif import extract_image_exif
    from ..media.previews import render_image_preview
    from .compensation import mark_rolled_back, record_pending
    from .object_keys import build_object_key
    from .quarantine import quarantined_upload
    from .workspace import local_workspace

    subdir = folder or f"album_{album_id}"
    es_foto = file_type == "image"
    quiere_derivados = prepare_image_metadata and es_foto and folder != "avatar"

    with quarantined_upload(file, max_bytes=max_bytes) as quarantined:
        # Decodificación profunda ANTES de comprometerse a nada: un archivo
        # corrupto nunca llega a abrir un workspace, registrar una operación
        # de compensación ni acercarse a un PUT.
        validated_image = _validate_quarantined_file(quarantined.path, file_type)

        # El hash ya se calculó por streaming al entrar en cuarentena. Este
        # hook permite consultar duplicados sin acoplar storage a Postgres y,
        # sobre todo, antes de gastar preview, operación durable o PUT.
        if before_store is not None:
            before_store(quarantined.sha256)

        exif = extract_image_exif(quarantined.path) if (prepare_image_metadata and es_foto) else {}

        # ponytail: esta ranura de `MEDIA_WORK_MAX_CONCURRENT` se suma a la
        # que `request_streams.py` ya reserva para todo el cuerpo del
        # multipart cuando la subida pesa más de 512 KiB (el caso normal de
        # una foto real) -- una subida con vista previa ocupa entonces DOS
        # ranuras durante toda la petición, así que el techo de 4 concurrentes
        # rinde como 2 subidas grandes a la vez, no 4. Reutilizar el workspace
        # de "ingress" de la petición exigiría que este módulo -- deliberada-
        # mente ajeno a Flask y probado sin ningún request context -- conociera
        # el `BudgetedRequest` en curso; para una biblioteca personal con un
        # puñado de subidas concurrentes como mucho, ese acoplamiento no vale
        # lo que ahorra. Si esto llega a doler de verdad, la salida es subir
        # `MEDIA_WORK_MAX_CONCURRENT`, no enredar los dos módulos.
        with local_workspace("upload-preview") as espacio:
            renderizada = render_image_preview(quarantined.path, espacio.directory) \
                if quiere_derivados else None

            clave_original = build_object_key(owner_id, subdir, file_format)
            clave_preview = build_object_key(owner_id, subdir, "webp") if renderizada else None
            claves = [clave_original] + ([clave_preview] if clave_preview else [])
            operacion = record_pending("upload", claves)

            backend = get_storage_backend()
            try:
                # La capacidad se comprueba DENTRO del mismo `try` que el PUT:
                # cualquier fallo desde aquí en adelante ya tiene una operación
                # pendiente escrita, así que cualquier fallo desde aquí en
                # adelante debe dejarla `rolled_back`, no colgada en `pending`.
                ensure_storage_capacity(quarantined.size_bytes +
                                        (renderizada.file_size if renderizada else 0))
                recibo = backend.put_from_path(clave_original, quarantined.path)
                preview_dict = None
                if renderizada and clave_preview:
                    backend.put_from_path(clave_preview, renderizada.path)
                    preview_dict = {
                        "storage_path": clave_preview,
                        "mime_type": renderizada.mime_type,
                        "width": renderizada.width,
                        "height": renderizada.height,
                        "file_size": renderizada.file_size,
                    }
            except Exception:
                mark_rolled_back(operacion)
                raise

    resultado = {
        "storage_path": recibo.key,
        "file_type": file_type,
        "file_size": recibo.size_bytes,
        "format": file_format,
        "mime_type": canonical_mime_type(file_format),
        "original_filename": quarantined.original_filename or f"archivo.{file_format}",
        "sha256": recibo.sha256,
        "storage_operation_id": operacion,
    }
    if validated_image is not None:
        # Las dimensiones REALES (decodificadas), nunca las que haya mandado
        # el cliente en el formulario -- mismo criterio que canonical_mime_type.
        resultado["resolution"] = f"{validated_image.width}x{validated_image.height}"
    if prepare_image_metadata:
        resultado["exif"] = exif
        resultado["preview"] = preview_dict if quiere_derivados else None
    return resultado


def resolve_storage_path(relative_path: str) -> Path:
    """Ruta absoluta de una key canonica. Solo local, igual que storage_root().

    La gramatica la decide `validate_storage_key()`, no una comprobacion
    propia: es la misma que aplica el origin. El guard de contencion se
    conserva de todas formas -- una raiz con enlaces simbolicos podria sacar
    de ella una key por lo demas valida. `InvalidStorageKey` hereda de
    `ValueError` y conserva el mensaje historico, asi que los llamadores que
    todavia capturan `ValueError` siguen funcionando igual.
    """
    try:
        validate_storage_key(relative_path)
    except InvalidStorageKey as exc:
        raise InvalidStorageKey("Ruta de archivo inválida") from exc
    root = storage_root().resolve()
    candidate = (root / relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise InvalidStorageKey("Ruta de archivo inválida")
    return candidate


def remove_stored_file(relative_path: str | None) -> bool:
    """True si borro algo; False si no habia nada que borrar.

    Un fallo de infraestructura se propaga: antes, `except (OSError,
    ValueError)` convertia un origin caido o un permiso denegado en un
    silencioso "no habia nada", y quien llamaba se quedaba creyendo que los
    bytes ya no existian. Solo la ausencia (False del backend), una key que
    no cumple la gramatica y un conflicto de version -- ninguno borra nada --
    siguen siendo False.
    """
    if not relative_path:
        return False
    try:
        return get_storage_backend().delete(relative_path)
    except (InvalidStorageKey, ObjectConflict):
        return False
