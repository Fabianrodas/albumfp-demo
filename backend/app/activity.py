"""Actividad de un álbum normal (L12): quién hizo qué, y cuándo.

Cada evento lo escribe el servidor DENTRO de la transacción de la operación
que lo causa, con la conexión que esa operación ya tiene abierta: si la
operación se deshace, el evento también. El navegador nunca elige el tipo ni
los metadatos, y los álbumes inteligentes no tienen actividad.

Los metadatos se normalizan por evento y solo guardan ids y enums: nada de
títulos, descripciones, rutas, tokens ni contraseñas.
"""
import json

from .domain.rules import ALBUM_CAPABILITIES, SHARE_PERMISSIONS
from .utils.sql_security import execute_safe

_ACCESS_KEYS = frozenset({"target_user_id", "permission", "capabilities"})

# evento -> claves de metadatos que admite (y exige, si las hay).
ACTIVITY_EVENTS = {
    "album_invite_created": _ACCESS_KEYS,
    "share_claimed": frozenset(),
    "asset_uploaded": frozenset(),
    "asset_added": frozenset(),
    "asset_removed": frozenset(),
    "cover_changed": frozenset({"cleared"}),
    "share_permission_changed": _ACCESS_KEYS,
}


def _user_id_or_none(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("target_user_id debe ser un id de usuario o null")
    return value


def normalize_activity_metadata(event_type: str, metadata) -> dict:
    """Los metadatos exactos de un evento, o ValueError. Es un error de
    programación, no de usuario: ningún dato de la petición llega aquí tal cual."""
    if event_type not in ACTIVITY_EVENTS:
        raise ValueError(f"Evento de actividad desconocido: {event_type}")
    metadata = metadata or {}
    if not isinstance(metadata, dict):
        raise ValueError("Los metadatos de actividad deben ser un objeto")
    permitidas = ACTIVITY_EVENTS[event_type]
    sobrantes = set(metadata) - permitidas
    if sobrantes:
        raise ValueError(f"Metadatos no permitidos para {event_type}: {', '.join(sorted(sobrantes))}")

    if permitidas is _ACCESS_KEYS:
        if set(metadata) != _ACCESS_KEYS:
            raise ValueError(f"{event_type} exige target_user_id, permission y capabilities")
        permission = metadata["permission"]
        capabilities = metadata["capabilities"]
        if permission not in SHARE_PERMISSIONS:
            raise ValueError("permission desconocido")
        if not isinstance(capabilities, (list, tuple)) or not set(capabilities) <= set(ALBUM_CAPABILITIES):
            raise ValueError("capabilities desconocidas")
        return {
            "target_user_id": _user_id_or_none(metadata["target_user_id"]),
            "permission": permission,
            # Orden canónico: dos listas con las mismas capacidades se guardan igual.
            "capabilities": [c for c in ALBUM_CAPABILITIES if c in set(capabilities)],
        }
    if event_type == "cover_changed" and metadata:
        if metadata.get("cleared") is not True:
            raise ValueError("cover_changed solo admite cleared: true")
        return {"cleared": True}
    return {}


def record_activity(conn, album_id: int, actor_user_id: int | None, event_type: str, *,
                    subject_asset_id: int | None = None, metadata: dict | None = None) -> None:
    """Escribe un evento con la conexión de la operación que lo causa."""
    execute_safe(
        conn,
        """
        INSERT INTO album_activity (album_id, actor_user_id, event_type, subject_asset_id, metadata_json)
        VALUES (:album_id, :actor_user_id, :event_type, :subject_asset_id, CAST(:metadata AS jsonb))
        """,
        {
            "album_id": album_id,
            "actor_user_id": actor_user_id,
            "event_type": event_type,
            "subject_asset_id": subject_asset_id,
            "metadata": json.dumps(normalize_activity_metadata(event_type, metadata)),
        },
    )


def _user(row, prefix: str):
    if row[f"{prefix}_id"] is None:
        return None
    return {"id": row[f"{prefix}_id"], "username": row[f"{prefix}_username"], "full_name": row[f"{prefix}_full_name"]}


def list_album_activity(conn, album_id: int, *, limit: int, offset: int) -> tuple[int, list[dict]]:
    """Una página de la actividad, más nueva primero. Actor, destinatario y
    recuerdo se resuelven con JOINs en la misma consulta: sin N+1. Del recuerdo
    solo sale su tipo y si sigue disponible en ESTE álbum -- nunca su título ni
    su leyenda, que podría vivir ya solo en álbumes privados del dueño."""
    total = execute_safe(
        conn, "SELECT COUNT(*) AS total FROM album_activity WHERE album_id = :album_id", {"album_id": album_id},
    ).mappings().first()["total"]
    rows = execute_safe(
        conn,
        """
        SELECT e.id, e.event_type, e.metadata_json, e.created_at,
               actor.id AS actor_id, actor.username AS actor_username, actor.full_name AS actor_full_name,
               target.id AS target_id, target.username AS target_username, target.full_name AS target_full_name,
               s.id AS subject_id, s.file_type AS subject_file_type,
               (s.deleted_at IS NULL AND EXISTS (
                   SELECT 1 FROM album_assets aa WHERE aa.album_id = e.album_id AND aa.asset_id = s.id
               )) AS subject_available
        FROM album_activity e
        LEFT JOIN users actor ON actor.id = e.actor_user_id
        LEFT JOIN users target ON target.id = (e.metadata_json ->> 'target_user_id')::int
        LEFT JOIN assets s ON s.id = e.subject_asset_id
        WHERE e.album_id = :album_id
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT :limit OFFSET :offset
        """,
        {"album_id": album_id, "limit": limit, "offset": offset},
    ).mappings().all()
    entries = []
    for row in rows:
        metadata = dict(row["metadata_json"] or {})
        metadata.pop("target_user_id", None)
        entries.append({
            "id": row["id"],
            "event_type": row["event_type"],
            "actor": _user(row, "actor"),
            "target": _user(row, "target"),
            "subject": None if row["subject_id"] is None else {
                "id": row["subject_id"], "file_type": row["subject_file_type"],
                "available": bool(row["subject_available"]),
            },
            "details": metadata,
            "created_at": row["created_at"],
        })
    return total, entries
