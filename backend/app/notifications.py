"""Avisos in-app (L12): la campana de cada cuenta, y sus preferencias.

Solo in-app. No hay push del navegador ni proveedor externo: la fila en
`notifications` ES el aviso. Se escribe DENTRO de la transacción de la
operación que lo causa, con su misma conexión, así que un aviso nunca
describe algo que no llegó a confirmarse.

Tres categorías, cada una con su preferencia. Sin fila de preferencias, las
tres están encendidas; apagar una solo evita avisos FUTUROS de esa categoría:
no borra los viejos ni toca la actividad del álbum.
"""
import os

from .domain.rules import NOTIFICATION_PREFERENCE_FIELDS
from .utils.sql_security import execute_safe

# categoría -> columna de preferencia que la gobierna.
NOTIFICATION_EVENTS = {
    "album_invite": "notify_album_invites",
    "share_claimed": "notify_share_claimed",
    "shared_album_upload": "notify_shared_album_uploads",
}

DEFAULTS = {field: True for field in NOTIFICATION_PREFERENCE_FIELDS}

RETENTION_LIMITS = (1, 3650)


def get_preferences(conn, user_id: int) -> dict:
    row = execute_safe(
        conn,
        """
        SELECT notify_album_invites, notify_share_claimed, notify_shared_album_uploads
        FROM user_notification_preferences WHERE user_id = :user_id
        """,
        {"user_id": user_id},
    ).mappings().first()
    return dict(DEFAULTS) if not row else {field: row[field] for field in NOTIFICATION_PREFERENCE_FIELDS}


def upsert_preferences(conn, user_id: int, updates: dict) -> dict:
    """`updates` ya viene validado (solo claves conocidas, valores booleanos).
    Une lo nuevo con lo que ya hubiera -- o los defaults si es la primera vez
    -- para que un PATCH parcial no resetee el resto de columnas."""
    merged = get_preferences(conn, user_id)
    merged.update(updates)
    execute_safe(
        conn,
        """
        INSERT INTO user_notification_preferences (
            user_id, notify_album_invites, notify_share_claimed, notify_shared_album_uploads
        )
        VALUES (:user_id, :notify_album_invites, :notify_share_claimed, :notify_shared_album_uploads)
        ON CONFLICT (user_id) DO UPDATE SET
            notify_album_invites = EXCLUDED.notify_album_invites,
            notify_share_claimed = EXCLUDED.notify_share_claimed,
            notify_shared_album_uploads = EXCLUDED.notify_shared_album_uploads,
            updated_at = NOW()
        """,
        {"user_id": user_id, **merged},
    )
    return merged


def _preference_column(event_type: str) -> str:
    if event_type not in NOTIFICATION_EVENTS:
        raise ValueError(f"Categoría de aviso desconocida: {event_type}")
    return NOTIFICATION_EVENTS[event_type]


def notify(conn, user_id: int, event_type: str, *, album_id: int | None, actor_user_id: int | None) -> bool:
    """Un aviso para una cuenta, si su preferencia lo permite. Una sola
    sentencia: sin fila de preferencias, `COALESCE` la da por encendida."""
    column = _preference_column(event_type)
    inserted = execute_safe(
        conn,
        f"""
        INSERT INTO notifications (user_id, event_type, album_id, actor_user_id)
        SELECT :user_id, :event_type, :album_id, :actor_user_id
        WHERE COALESCE(
            (SELECT p.{column} FROM user_notification_preferences p WHERE p.user_id = :user_id), TRUE
        )
        RETURNING id
        """,
        {"user_id": user_id, "event_type": event_type, "album_id": album_id, "actor_user_id": actor_user_id},
    ).first()
    return inserted is not None


def notify_album_upload(conn, album_id: int, uploader_id: int) -> int:
    """Foto nueva en un álbum compartido: un aviso para cada acceso de CUENTA
    activo y vigente del álbum, salvo quien subió. Un enlace público no es un
    destinatario, y el dueño no es un acceso (no se avisa a sí mismo de lo que
    sube, ni recibe avisos de sus colaboradores en esta categoría). `LEFT JOIN`
    + `COALESCE`: una cuenta sin fila de preferencias recibe el aviso."""
    return execute_safe(
        conn,
        """
        INSERT INTO notifications (user_id, event_type, album_id, actor_user_id)
        SELECT DISTINCT s.shared_with_user_id, 'shared_album_upload', :album_id, :uploader_id
        FROM album_shares s
        JOIN users u ON u.id = s.shared_with_user_id AND u.active = TRUE
        LEFT JOIN user_notification_preferences p ON p.user_id = s.shared_with_user_id
        WHERE s.album_id = :album_id
          AND s.share_type = 'account'
          AND s.shared_with_user_id IS NOT NULL
          AND s.shared_with_user_id != :uploader_id
          AND s.active = TRUE
          AND (s.expires_at IS NULL OR s.expires_at > NOW())
          AND COALESCE(p.notify_shared_album_uploads, TRUE)
        """,
        {"album_id": album_id, "uploader_id": uploader_id},
    ).rowcount or 0


def list_notifications(conn, user_id: int, *, limit: int, offset: int) -> tuple[int, list[dict]]:
    """La campana, más nueva primero. El álbum solo se nombra si sigue activo:
    uno borrado ya no tiene id al que navegar (su FK lo dejó en NULL)."""
    total = execute_safe(
        conn, "SELECT COUNT(*) AS total FROM notifications WHERE user_id = :user_id", {"user_id": user_id},
    ).mappings().first()["total"]
    rows = execute_safe(
        conn,
        """
        SELECT n.id, n.event_type, n.read_at, n.created_at,
               a.id AS album_id, a.titulo AS album_title,
               u.id AS actor_id, u.username AS actor_username, u.full_name AS actor_full_name
        FROM notifications n
        LEFT JOIN albums a ON a.id = n.album_id AND a.active = TRUE
        LEFT JOIN users u ON u.id = n.actor_user_id
        WHERE n.user_id = :user_id
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT :limit OFFSET :offset
        """,
        {"user_id": user_id, "limit": limit, "offset": offset},
    ).mappings().all()
    return total, [{
        "id": r["id"],
        "event_type": r["event_type"],
        "album_id": r["album_id"],
        "album_title": r["album_title"],
        "actor": None if r["actor_id"] is None else {
            "id": r["actor_id"], "username": r["actor_username"], "full_name": r["actor_full_name"],
        },
        "read_at": r["read_at"],
        "created_at": r["created_at"],
    } for r in rows]


def unread_count(conn, user_id: int) -> int:
    return execute_safe(
        conn, "SELECT COUNT(*) AS unread FROM notifications WHERE user_id = :user_id AND read_at IS NULL",
        {"user_id": user_id},
    ).mappings().first()["unread"]


def mark_read(conn, user_id: int, notification_id: int):
    """Idempotente: la primera vez fija `read_at`, las siguientes lo conservan.
    Un aviso ajeno no existe para esta cuenta (None -> 404)."""
    return execute_safe(
        conn,
        """
        UPDATE notifications SET read_at = COALESCE(read_at, NOW())
        WHERE id = :id AND user_id = :user_id
        RETURNING id, read_at
        """,
        {"id": notification_id, "user_id": user_id},
    ).mappings().first()


def mark_all_read(conn, user_id: int) -> int:
    return execute_safe(
        conn, "UPDATE notifications SET read_at = NOW() WHERE user_id = :user_id AND read_at IS NULL",
        {"user_id": user_id},
    ).rowcount or 0


def _retention(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        days = int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser un número entero de días") from exc
    low, high = RETENTION_LIMITS
    # Fuera de rango se niega en vez de ajustarse: 0 borraría todo el historial
    # en la siguiente pasada del timer.
    if not low <= days <= high:
        raise RuntimeError(f"{name} debe estar entre {low} y {high} días")
    return days


def retention_days() -> tuple[int, int]:
    """(avisos, actividad) en días."""
    return _retention("NOTIFICATION_RETENTION_DAYS", 90), _retention("ALBUM_ACTIVITY_RETENTION_DAYS", 365)


def purge_expired(conn, notification_days: int, activity_days: int) -> tuple[int, int]:
    """Borra lo que ya pasó su ventana, leído o no. Solo filas de PostgreSQL.
    ponytail: sin índice por `created_at`; un barrido diario sobre tablas que
    la propia retención mantiene pequeñas. Añadirlo si el barrido se nota."""
    avisos = execute_safe(
        conn, "DELETE FROM notifications WHERE created_at < NOW() - make_interval(days => :days)",
        {"days": notification_days},
    ).rowcount or 0
    eventos = execute_safe(
        conn, "DELETE FROM album_activity WHERE created_at < NOW() - make_interval(days => :days)",
        {"days": activity_days},
    ).rowcount or 0
    return avisos, eventos
