"""Comentarios de asset (F04): contrato de texto y lecturas.

Un comentario es texto plano atado a la identidad durable del asset. Quién
puede leerlo o escribirlo NO se decide aquí: lo decide el llamador con la
autorización central (`require_asset_permission`) o, para un enlace público,
con el helper del token. Este módulo solo valida y lee/escribe filas, con la
conexión de quien llama.

Sin actividad, avisos, menciones, respuestas ni «me gusta» (fuera de v1).
"""
import unicodedata

from .utils.sql_security import execute_safe

MAX_COMMENT_LENGTH = 2000
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 50
_ALLOWED_CONTROLS = {"\n", "\t"}


def clean_comment_body(value) -> str:
    """El texto que se guarda, o `ValueError` con el motivo.

    Recorta los extremos, unifica los saltos de línea y conserva los de en
    medio. No «sanea» nada a base de reemplazos: el texto se guarda tal cual y
    Angular lo pinta escapado. Rechaza lo que no es texto seguro (controles,
    sustitutos sueltos) en vez de modificarlo en silencio.
    """
    if not isinstance(value, str):
        raise ValueError("El comentario debe ser texto.")
    body = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        raise ValueError("El comentario no puede estar vacío.")
    if len(body) > MAX_COMMENT_LENGTH:
        raise ValueError(f"El comentario no puede superar {MAX_COMMENT_LENGTH} caracteres.")
    try:
        body.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("El comentario contiene caracteres no válidos.") from None
    if any(unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROLS for ch in body):
        raise ValueError("El comentario contiene caracteres de control.")
    return body


# Autor: solo lo que ya es público de una cuenta. Nunca la ruta del avatar:
# la foto se pide por su endpoint público con el id.
_COMMENT_COLUMNS = """
    c.id, c.asset_id, c.user_id, c.body, c.created_at, c.updated_at,
    u.username AS author_username,
    COALESCE(NULLIF(u.full_name, ''), u.username) AS author_full_name,
    (u.avatar_path IS NOT NULL) AS author_has_avatar
"""


def shape_comment(row, viewer_id: int | None = None) -> dict:
    comment = {
        "id": row["id"], "asset_id": row["asset_id"], "body": row["body"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "edited": row["updated_at"] is not None,
        "author": {"id": row["user_id"], "username": row["author_username"],
                   "full_name": row["author_full_name"], "has_avatar": bool(row["author_has_avatar"])},
    }
    if viewer_id is not None:
        comment["is_own"] = row["user_id"] == viewer_id
    return comment


def list_comments(conn, asset_id: int, *, per_page: int, offset: int) -> tuple[int, list]:
    """(total, filas de la página). Dos consultas acotadas, con el autor en la
    misma: nada de una consulta por comentario."""
    total = execute_safe(conn, "SELECT COUNT(*) FROM asset_comments WHERE asset_id = :asset_id",
                         {"asset_id": asset_id}).scalar()
    rows = execute_safe(conn, f"""
        SELECT {_COMMENT_COLUMNS}
        FROM asset_comments c
        JOIN users u ON u.id = c.user_id
        WHERE c.asset_id = :asset_id
        ORDER BY c.created_at, c.id
        LIMIT :limit OFFSET :offset
    """, {"asset_id": asset_id, "limit": per_page, "offset": offset}).mappings().all()
    return int(total), rows


def read_comment(conn, comment_id: int):
    return execute_safe(conn, f"""
        SELECT {_COMMENT_COLUMNS}
        FROM asset_comments c JOIN users u ON u.id = c.user_id
        WHERE c.id = :comment_id
    """, {"comment_id": comment_id}).mappings().first()


def insert_comment(conn, asset_id: int, user_id: int, body: str) -> int:
    return execute_safe(conn, """
        INSERT INTO asset_comments (asset_id, user_id, body) VALUES (:asset_id, :user_id, :body)
        RETURNING id
    """, {"asset_id": asset_id, "user_id": user_id, "body": body}).scalar()


def export_comments(conn, owner_id: int) -> list[dict]:
    """Para la exportación portable (L13): los comentarios de los assets que
    se exportan (del dueño, fuera de la papelera), de quien sea que los
    escribiera, con una identidad de autor mínima."""
    rows = execute_safe(conn, """
        SELECT c.id, c.asset_id, c.body, c.created_at, c.updated_at,
               u.username, COALESCE(NULLIF(u.full_name, ''), u.username) AS full_name
        FROM asset_comments c
        JOIN assets m ON m.id = c.asset_id AND m.user_id = :owner_id AND m.deleted_at IS NULL
        JOIN users u ON u.id = c.user_id
        ORDER BY c.asset_id, c.created_at, c.id
    """, {"owner_id": owner_id}).mappings().all()
    return [dict(r) for r in rows]
