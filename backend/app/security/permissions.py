from ..domain.rules import capabilities_for, permission_allows
from ..utils.sql_security import execute_safe
from .sessions import hash_token


def get_album_access(conn, album_id: int, user_id: int):
    album = execute_safe(
        conn,
        """
        SELECT a.id, a.user_id, a.titulo, a.descripcion, a.active, a.is_private,
               a.cover_media_id, a.created_at, a.updated_at,
               cm.file_type AS cover_file_type
        FROM albums a
        LEFT JOIN assets cm
          ON cm.id = a.cover_media_id
         AND cm.deleted_at IS NULL
        WHERE a.id = :album_id
        """,
        {"album_id": album_id},
    ).mappings().first()

    if not album or not album["active"]:
        return None
    if album["user_id"] == user_id:
        return {"role": "owner", "capabilities": capabilities_for("owner", None), "album": album}

    share = execute_safe(
        conn,
        """
        SELECT permission, capabilities
        FROM album_shares
        WHERE album_id = :album_id
          AND share_type = 'account'
          AND shared_with_user_id = :user_id
          AND active = TRUE
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"album_id": album_id, "user_id": user_id},
    ).mappings().first()
    if share:
        return {
            "role": share["permission"],
            "capabilities": capabilities_for(share["permission"], share["capabilities"]),
            "album": album,
            # Distingue a un colaborador de solo lectura de cualquiera que lee
            # un álbum público: los dos tienen rol "read" (ver abajo).
            "via_share": True,
        }

    # Un álbum marcado como no privado es legible por cualquier cuenta: es lo
    # que sostiene el feed del inicio y los perfiles públicos. Solo concede
    # "read", así que subir, borrar, compartir y ver la papelera siguen
    # exigiendo ser dueño o colaborador. La media en papelera tampoco se
    # expone: sus lecturas piden "owner" explícitamente.
    if not album["is_private"]:
        return {"role": "read", "capabilities": set(), "album": album}

    return None


def is_album_collaborator(access) -> bool:
    """Dueño o acceso de cuenta activo. Leer un álbum público no cuenta."""
    return bool(access) and (access["role"] == "owner" or bool(access.get("via_share")))


def require_album_collaborator(conn, album_id: int, user_id: int):
    """L12: el historial de colaboración de un álbum es del dueño y de sus
    colaboradores con acceso de cuenta activo. Un álbum no privado lo puede
    LEER cualquier cuenta, pero su actividad (quién invitó a quién) no."""
    access = get_album_access(conn, album_id, user_id)
    return access if is_album_collaborator(access) else None


def require_album_permission(conn, album_id: int, user_id: int, permission: str):
    access = get_album_access(conn, album_id, user_id)
    if not access or not permission_allows(access["role"], permission):
        return None
    return access


def require_album_capability(conn, album_id: int, user_id: int, capability: str):
    """Puerta de las acciones de escritura. A diferencia de
    `require_album_permission`, no hay jerarquia: el dueno del album las tiene
    todas y un colaborador tiene exactamente las que le concedieron, ni una
    mas."""
    access = get_album_access(conn, album_id, user_id)
    if not access or capability not in access["capabilities"]:
        return None
    return access


def get_token_share(conn, token: str, required_permission: str = "read"):
    """No comprueba contraseña: eso es un paso aparte porque no todos los
    llamadores necesitan bloquear en el mismo punto (crear el desbloqueo
    necesita la fila del share ANTES de saber si la contraseña es correcta).
    `password_hash`/`allow_original_download`/`show_metadata` viajan en el
    resultado para que quien llama decida qué hacer con cada uno."""
    share = execute_safe(
        conn,
        """
        SELECT s.id, s.album_id, s.shared_by, s.permission, s.share_type,
               s.expires_at, s.password_hash, s.allow_original_download, s.show_metadata,
               a.user_id AS album_owner_id, a.titulo, a.descripcion,
               a.is_private, a.cover_media_id
        FROM album_shares s
        JOIN albums a ON a.id = s.album_id
        WHERE s.token_hash = :token_hash
          AND s.share_type = 'public_link'
          AND s.permission = 'read'
          AND s.active = TRUE
          AND a.active = TRUE
          AND (s.expires_at IS NULL OR s.expires_at > NOW())
        LIMIT 1
        """,
        {"token_hash": hash_token(token)},
    ).mappings().first()

    if not share or not permission_allows(share["permission"], required_permission):
        return None
    return share
