from __future__ import annotations

import os
import re
from datetime import datetime

REGISTRATION_MODES = ("open", "invite_only", "closed")


def registration_mode() -> str:
    """Una sola fuente de verdad para `REGISTRATION_MODE`: la lee tanto
    `auth.py` (para exigirla de verdad) como `health.py` (para que el
    formulario sepa que pintar antes de tener sesión). Un valor desconocido
    en el .env cae a "closed" para que un error de configuración nunca abra
    los registros accidentalmente."""
    modo = (os.getenv("REGISTRATION_MODE") or "open").strip().lower()
    return modo if modo in REGISTRATION_MODES else "closed"


def validate_registration_fields(username: str, full_name: str | None = None) -> dict:
    username = str(username or "").strip()
    full_name = str(full_name or "").strip()
    if not username:
        raise ValueError("username es obligatorio")
    if len(username) > 50:
        raise ValueError("username no puede superar 50 caracteres")
    if len(full_name) > 120:
        raise ValueError("El nombre completo no puede superar 120 caracteres")
    return {"username": username, "full_name": full_name or None}


PERMISSION_LEVELS = {"read": 0, "write": 1, "owner": 2}
SHARE_PERMISSIONS = ("read", "write")

# Capacidades de ESCRITURA que el dueno concede una por una a un colaborador.
# Leer nunca esta aqui: cualquiera con acceso al album puede ver, y eso no se
# puede quitar (por eso el modal las muestra fijas). El orden es el que se
# pinta en el frontend, de menos a mas alcance.
ALBUM_CAPABILITIES = ("upload", "edit_media", "delete_media", "organize", "edit_album")


def permission_allows(role: str, required: str) -> bool:
    role_level = PERMISSION_LEVELS.get((role or "").lower())
    required_level = PERMISSION_LEVELS.get((required or "").lower())
    if role_level is None or required_level is None:
        return False
    return role_level >= required_level


def normalize_capabilities(value) -> list[str]:
    """Lista de capacidades tal como la manda el frontend -> lista limpia, sin
    duplicados, en el orden canonico de ALBUM_CAPABILITIES. Un valor
    desconocido es un error, no algo que se ignora en silencio: preferimos
    fallar a conceder o negar algo que el dueno no eligio."""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("capabilities debe ser una lista")
    pedidas = set()
    for item in value:
        nombre = str(item or "").strip().lower()
        if nombre not in ALBUM_CAPABILITIES:
            raise ValueError(f"capacidad desconocida: {item}")
        pedidas.add(nombre)
    return [nombre for nombre in ALBUM_CAPABILITIES if nombre in pedidas]


def capabilities_for(role: str, capabilities) -> set[str]:
    """El dueno siempre las tiene todas (nunca dependen de una fila de share);
    un colaborador tiene exactamente las que le concedieron; 'read' ninguna,
    aunque su fila trajera algo — asi un dato corrupto no concede escritura."""
    role = (role or "").lower()
    if role == "owner":
        return set(ALBUM_CAPABILITIES)
    if role != "write":
        return set()
    return {c for c in (capabilities or []) if c in ALBUM_CAPABILITIES}


def _validate_iso_datetime(value: str | None, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} debe ser una fecha ISO-8601")
    normalized = value.strip()
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} debe ser una fecha ISO-8601 válida") from exc
    return normalized


# Dia, mes y año son obligatorios; la hora es opcional. Sin esto, Python 3.11+
# acepta "2026" y "2026-08" en `fromisoformat` y los rellena con enero y el
# dia 1: una fecha a medias se guardaria como si fuera exacta.
_FECHA_COMPLETA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")


def validate_capture_date(value: str | None, field_name: str = "taken_at") -> str | None:
    """Fecha de captura: vacio significa "sin fecha propia" (quien llame
    decide si eso es borrarla o volver a la del EXIF), y cualquier otra cosa
    tiene que traer al menos el dia completo."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _FECHA_COMPLETA_RE.match(value.strip()):
        raise ValueError(f"{field_name} necesita al menos día, mes y año")
    return _validate_iso_datetime(value, field_name)


def validate_coordinate(value, minimum: float, maximum: float, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} debe ser un número") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{field_name} debe estar entre {minimum} y {maximum}")
    return number


SHARE_PASSWORD_MIN_LENGTH = 4
SHARE_PASSWORD_MAX_LENGTH = 128


def validate_share_password(value) -> str | None:
    """Un enlace público opcionalmente pide una contraseña antes de mostrar
    nada -- no es una credencial de cuenta (NIST 800-63B es para eso, ver
    `password_policy.py`), es un PIN sobre un link que ya de por sí es difícil
    de adivinar (256 bits de token). El mínimo es bajo a propósito: evita un
    valor vacío o de un solo carácter por error, no impone teatro de
    complejidad sobre un gesto pensado para compartirse de palabra."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("La contraseña del enlace debe ser texto")
    if not (SHARE_PASSWORD_MIN_LENGTH <= len(value) <= SHARE_PASSWORD_MAX_LENGTH):
        raise ValueError(
            f"La contraseña del enlace debe tener entre {SHARE_PASSWORD_MIN_LENGTH} y {SHARE_PASSWORD_MAX_LENGTH} caracteres"
        )
    return value


def normalize_share_request(payload: dict) -> dict:
    permission = str(payload.get("permission") or "read").strip().lower()
    if permission not in SHARE_PERMISSIONS:
        raise ValueError(f"permission debe ser una de: {', '.join(SHARE_PERMISSIONS)}")

    capabilities = normalize_capabilities(payload.get("capabilities"))
    if permission == "read" and capabilities:
        raise ValueError("Un acceso de solo lectura no puede llevar capacidades de escritura")
    if permission == "write" and not capabilities:
        raise ValueError("Un colaborador necesita al menos una capacidad")

    raw_user_id = payload.get("shared_with_user_id")
    if payload.get("shared_with_email") not in (None, ""):
        raise ValueError("La compartición por correo ya no está disponible; usa una invitación por enlace")

    create_link = payload.get("create_link") is True
    has_user = raw_user_id is not None
    if sum((has_user, create_link)) != 1:
        raise ValueError("Debes elegir exactamente un tipo de compartición")

    user_id = None
    target_type = "link"
    share_type = "account"

    if has_user:
        if isinstance(raw_user_id, bool):
            raise ValueError("shared_with_user_id debe ser un entero positivo")
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("shared_with_user_id debe ser un entero positivo") from exc
        if user_id <= 0:
            raise ValueError("shared_with_user_id debe ser un entero positivo")
        target_type = "user"
    else:
        share_type = str(payload.get("share_type") or "public_link").strip().lower()
        if share_type not in {"account", "public_link"}:
            raise ValueError("share_type debe ser account o public_link")
        if share_type == "public_link" and permission != "read":
            raise ValueError("Un enlace público solo puede ser de lectura")

    # password/allow_original_download/show_metadata son exclusivos de un
    # enlace público (chk_public_link_only_extras los impone también en la
    # base) -- se rechazan explícitamente en vez de ignorarse en silencio si
    # llegan para una invitación de cuenta, mismo criterio que "capacidades
    # vacías se rechazan" más arriba.
    if "allow_original_download" in payload and not isinstance(payload["allow_original_download"], bool):
        raise ValueError("allow_original_download debe ser booleano")
    if "show_metadata" in payload and not isinstance(payload["show_metadata"], bool):
        raise ValueError("show_metadata debe ser booleano")
    allow_original_download = payload.get("allow_original_download", False)
    show_metadata = payload.get("show_metadata", True)

    if share_type == "public_link":
        password = validate_share_password(payload.get("password"))
    else:
        if payload.get("password") not in (None, ""):
            raise ValueError("Solo un enlace público puede llevar contraseña")
        if allow_original_download:
            raise ValueError("Permitir descargar el original solo aplica a un enlace público")
        if not show_metadata:
            raise ValueError("Mostrar metadatos solo aplica a un enlace público")
        password = None

    return {
        "permission": permission,
        "capabilities": capabilities,
        "target_type": target_type,
        "share_type": share_type,
        "shared_with_user_id": user_id,
        "create_link": create_link,
        "expires_at": _validate_iso_datetime(payload.get("expires_at"), "expires_at"),
        "password": password,
        "allow_original_download": allow_original_download,
        "show_metadata": show_metadata,
    }


def validate_share_permission_update(payload: dict) -> tuple[str, list[str], dict]:
    """Cambiar lo que puede hacer un colaborador ya activo, sin revocar y
    re-invitar. Permission/capabilities siguen siendo obligatorios (para un
    enlace público valen siempre 'read'/[], así que mandarlos no es una
    carga extra). El tercer valor es un PATCH PARCIAL de los controles
    exclusivos de un enlace público: solo trae las claves que el payload
    incluyó de verdad -- quien llama decide si el share es de tipo
    public_link antes de aplicarlas, esta función no toca la base."""
    permission = str(payload.get("permission") or "").strip().lower()
    if permission not in SHARE_PERMISSIONS:
        raise ValueError(f"permission debe ser una de: {', '.join(SHARE_PERMISSIONS)}")
    capabilities = normalize_capabilities(payload.get("capabilities"))
    if permission == "read" and capabilities:
        raise ValueError("Un acceso de solo lectura no puede llevar capacidades de escritura")
    if permission == "write" and not capabilities:
        raise ValueError("Un colaborador necesita al menos una capacidad")

    public_link_updates: dict = {}
    if "password" in payload:
        public_link_updates["password"] = validate_share_password(payload.get("password"))
    if "allow_original_download" in payload:
        if not isinstance(payload["allow_original_download"], bool):
            raise ValueError("allow_original_download debe ser booleano")
        public_link_updates["allow_original_download"] = payload["allow_original_download"]
    if "show_metadata" in payload:
        if not isinstance(payload["show_metadata"], bool):
            raise ValueError("show_metadata debe ser booleano")
        public_link_updates["show_metadata"] = payload["show_metadata"]

    return permission, capabilities, public_link_updates


# Las tres categorías de avisos IN-APP (L12). No hay interruptor de push: el
# proveedor externo se retiró, y su vieja bandera ahora es un campo desconocido.
NOTIFICATION_PREFERENCE_FIELDS = (
    "notify_album_invites",
    "notify_share_claimed",
    "notify_shared_album_uploads",
)


def validate_notification_preferences_update(payload: dict) -> dict:
    """PATCH parcial: solo se validan y devuelven las claves presentes, las
    demas se quedan con el valor que ya tuvieran (o el default). Rechaza
    cualquier clave fuera de las 3 conocidas y cualquier valor que no sea
    booleano de verdad -- `1`/`"true"` no cuentan, para no adivinar la
    intencion de un valor a medias."""
    if not isinstance(payload, dict):
        raise ValueError("El cuerpo debe ser un objeto JSON")
    updates: dict[str, bool] = {}
    for key, value in payload.items():
        if key not in NOTIFICATION_PREFERENCE_FIELDS:
            raise ValueError(f"Campo desconocido: {key}")
        if not isinstance(value, bool):
            raise ValueError(f"{key} debe ser booleano")
        updates[key] = value
    return updates


def validate_tag_ids(tag_ids) -> list[int]:
    if tag_ids is None:
        return []
    if not isinstance(tag_ids, list):
        raise ValueError("tag_ids debe ser una lista de enteros positivos")

    normalized: list[int] = []
    seen: set[int] = set()
    for value in tag_ids:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("tag_ids debe contener enteros positivos")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def validate_media_upload_fields(payload: dict, file_type: str) -> dict:
    file_type = str(file_type or "").strip().lower()
    if file_type not in {"image", "video"}:
        raise ValueError("file_type debe ser image o video")

    title = str(payload.get("title") or "").strip() or None
    if title and len(title) > 120:
        raise ValueError("title no puede superar 120 caracteres")

    caption = payload.get("caption")
    if caption is not None:
        caption = str(caption).strip() or None
    if caption and len(caption) > 2000:
        raise ValueError("caption no puede superar 2000 caracteres")

    favorite_raw = payload.get("is_favorite", False)
    if isinstance(favorite_raw, bool):
        is_favorite = favorite_raw
    else:
        normalized = str(favorite_raw).strip().lower()
        if normalized not in {"", "true", "false", "1", "0"}:
            raise ValueError("is_favorite debe ser boolean")
        is_favorite = normalized in {"true", "1"}

    force_raw = payload.get("force_duplicate", False)
    if isinstance(force_raw, bool):
        force_duplicate = force_raw
    else:
        normalized = str(force_raw).strip().lower()
        if normalized not in {"", "true", "false", "1", "0"}:
            raise ValueError("force_duplicate debe ser boolean")
        force_duplicate = normalized in {"true", "1"}

    taken_at = validate_capture_date(payload.get("taken_at"))

    # Coordenadas de un pin puesto a mano en el mapa: igual que la fecha
    # manual, ganan sobre el GPS del EXIF. O van las dos o no va ninguna,
    # mismo criterio que el EXIF con latitud/longitud.
    latitude = validate_coordinate(payload.get("latitude"), -90, 90, "latitude")
    longitude = validate_coordinate(payload.get("longitude"), -180, 180, "longitude")
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude y longitude deben ir juntas")

    resolution = str(payload.get("resolution") or "").strip() or None
    duration_raw = payload.get("duration")
    duration = None
    if duration_raw not in (None, ""):
        try:
            duration = max(0, int(float(duration_raw)))
        except (TypeError, ValueError) as exc:
            raise ValueError("duration debe ser un número no negativo") from exc
        if file_type != "video":
            raise ValueError("duration solo aplica a videos")

    tag_raw = payload.get("tag_ids")
    tag_ids = []
    if tag_raw not in (None, ""):
        if isinstance(tag_raw, str):
            try:
                tag_ids = [int(value) for value in tag_raw.split(",") if value.strip()]
            except ValueError as exc:
                raise ValueError("tag_ids debe contener enteros positivos") from exc
        else:
            tag_ids = tag_raw

    return {
        "file_type": file_type,
        "title": title,
        "caption": caption,
        "is_favorite": is_favorite,
        "force_duplicate": force_duplicate,
        "taken_at": taken_at,
        "resolution": resolution,
        "duration": duration,
        "tag_ids": validate_tag_ids(tag_ids),
        "latitude": latitude,
        "longitude": longitude,
    }


def validate_media_update_fields(payload: dict) -> dict:
    """Edicion parcial post-subida: a diferencia de validate_media_upload_fields,
    solo valida y devuelve las claves presentes en payload (reutilizando los
    mismos limites y los mismos helpers privados), para que el endpoint arme
    un UPDATE con exactamente esos campos y ninguno mas. Favoritos y tags no
    entran aqui: ya tienen su propio endpoint."""
    updates: dict = {}

    if "title" in payload:
        title = str(payload.get("title") or "").strip() or None
        if title and len(title) > 120:
            raise ValueError("title no puede superar 120 caracteres")
        updates["title"] = title

    if "caption" in payload:
        caption = payload.get("caption")
        if caption is not None:
            caption = str(caption).strip() or None
        if caption and len(caption) > 2000:
            raise ValueError("caption no puede superar 2000 caracteres")
        updates["caption"] = caption

    # Vaciar la fecha NO la borra: la devuelve a la del EXIF, igual que al
    # subir sin escribir fecha. El endpoint lo resuelve, aqui solo se marca.
    if "taken_at" in payload:
        fecha = validate_capture_date(payload.get("taken_at"))
        updates["taken_at"] = fecha
        if fecha is None:
            updates["reset_taken_at"] = True

    # Mismo criterio para el lugar: `clear_location` lo devuelve al del EXIF.
    if payload.get("clear_location") is True:
        updates["reset_location"] = True
    elif "latitude" in payload or "longitude" in payload:
        latitude = validate_coordinate(payload.get("latitude"), -90, 90, "latitude")
        longitude = validate_coordinate(payload.get("longitude"), -180, 180, "longitude")
        if (latitude is None) != (longitude is None):
            raise ValueError("latitude y longitude deben ir juntas")
        if latitude is None:
            updates["reset_location"] = True
        else:
            updates["latitude"] = latitude
            updates["longitude"] = longitude

    return updates
