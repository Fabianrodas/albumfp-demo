"""Pertenencia asset ↔ album (L10A): el unico sitio que sabe como se relaciona
un asset con sus albumes.

Un asset es del dueño (`assets.user_id`) y puede estar en cero, uno o varios
albumes suyos (`album_assets`). Todo el SQL de aqui usa el alias `m` para
`assets`, igual que el resto del backend.

Autorizacion sobre un asset:
- el dueño lo tiene todo mientras el asset este en su alcance activo, incluido
  un asset suelto (sin album, p. ej. tras borrar su unico album);
- cualquier otra cuenta solo por los albumes ACTIVOS que lo contienen y que le
  dan acceso: leer si alguno la deja leer, una capacidad si alguno se la
  concede. Un album que no contiene el asset nunca aporta nada.
"""
from ..domain.rules import capabilities_for, permission_allows
from ..security.permissions import get_album_access
from ..utils.sql_security import execute_safe

# Vista personal del dueño: el asset cuenta si esta suelto o si al menos uno de
# sus albumes esta activo. Conserva el filtro `albums.active` de antes de L10A.
ACTIVE_SCOPE_SQL = (
    "(NOT EXISTS (SELECT 1 FROM album_assets sc0 WHERE sc0.asset_id = m.id)"
    " OR EXISTS (SELECT 1 FROM album_assets sc1 JOIN albums sa1 ON sa1.id = sc1.album_id"
    " WHERE sc1.asset_id = m.id AND sa1.active = TRUE))"
)

_ROLE_RANK = {"read": 1, "write": 2, "owner": 3}


def in_album_sql(param: str = "album_id") -> str:
    """El asset `m` es miembro del album `:param`."""
    return f"EXISTS (SELECT 1 FROM album_assets inm WHERE inm.asset_id = m.id AND inm.album_id = :{param})"


def in_active_album_sql(extra: str = "") -> str:
    """El asset `m` esta en algun album activo que cumpla `extra` (alias `ia`)."""
    return (
        "EXISTS (SELECT 1 FROM album_assets iaa JOIN albums ia ON ia.id = iaa.album_id"
        f" WHERE iaa.asset_id = m.id AND ia.active = TRUE {extra})"
    )


def context_album_join(*, extra: str = "", prefer_param: str | None = None) -> str:
    """`LEFT JOIN LATERAL` que expone `ctx.album_id` y `ctx.album_titulo`.

    Es el album activo con el que se presenta un asset en un listado: el de su
    pertenencia mas antigua (para lo migrado, el album donde se subio), salvo
    que `prefer_param` nombre un album que tambien lo contiene. Un asset suelto
    da NULL en las dos columnas.
    """
    prefer = f"(ca.id = :{prefer_param}) DESC, " if prefer_param else ""
    return f"""
        LEFT JOIN LATERAL (
            SELECT ca.id AS album_id, ca.titulo AS album_titulo
            FROM album_assets caa JOIN albums ca ON ca.id = caa.album_id
            WHERE caa.asset_id = m.id AND ca.active = TRUE {extra}
            ORDER BY {prefer}caa.added_at, ca.id
            LIMIT 1
        ) ctx ON TRUE
    """


def add_membership(conn, *, album_id: int, asset_id: int, owner_id: int) -> bool:
    """Mete un asset en un album; False si ya estaba. El esquema rechaza un
    album de otro dueño. Solo relacional: ningun objeto se copia ni se mueve."""
    return execute_safe(
        conn,
        """
        INSERT INTO album_assets (album_id, asset_id, owner_id)
        VALUES (:album_id, :asset_id, :owner_id)
        ON CONFLICT (album_id, asset_id) DO NOTHING
        RETURNING asset_id
        """,
        {"album_id": album_id, "asset_id": asset_id, "owner_id": owner_id},
    ).first() is not None


def remove_membership(conn, *, album_id: int, asset_id: int) -> bool:
    """Saca un asset de un album; False si no estaba. Nunca toca el asset ni
    sus objetos, y si era la portada, fk_album_cover_member la vacia."""
    return execute_safe(
        conn,
        "DELETE FROM album_assets WHERE album_id = :album_id AND asset_id = :asset_id RETURNING asset_id",
        {"album_id": album_id, "asset_id": asset_id},
    ).first() is not None


def asset_albums(conn, asset_id: int) -> list[dict]:
    """Los albumes activos que contienen el asset, en orden de pertenencia.
    Solo se le enseñan a su dueño: pueden ser albumes privados."""
    return [dict(f) for f in execute_safe(
        conn,
        """
        SELECT a.id, a.titulo
        FROM album_assets aa JOIN albums a ON a.id = aa.album_id
        WHERE aa.asset_id = :asset_id AND a.active = TRUE
        ORDER BY aa.added_at, a.id
        """,
        {"asset_id": asset_id},
    ).mappings().all()]


def _active_memberships(conn, asset_id: int) -> tuple[list[int], bool]:
    """(albumes activos que lo contienen, en orden de pertenencia; tiene alguna pertenencia)."""
    filas = execute_safe(
        conn,
        """
        SELECT aa.album_id, a.active
        FROM album_assets aa JOIN albums a ON a.id = aa.album_id
        WHERE aa.asset_id = :asset_id
        ORDER BY aa.added_at, aa.album_id
        """,
        {"asset_id": asset_id},
    ).mappings().all()
    return [f["album_id"] for f in filas if f["active"]], bool(filas)


def asset_accesses(conn, asset, user_id: int) -> list[dict]:
    """Cada acceso real de `user_id` al asset, en orden de pertenencia."""
    activos, alguna = _active_memberships(conn, asset["id"])
    if asset["user_id"] == user_id:
        if alguna and not activos:
            return []  # solo en albumes inactivos: igual que antes de L10A
        album = get_album_access(conn, activos[0], user_id)["album"] if activos else None
        return [{
            "role": "owner",
            "capabilities": capabilities_for("owner", None),
            "album": album,
            "album_id": activos[0] if activos else None,
            "owner_id": asset["user_id"],
        }]
    accesos = []
    for album_id in activos:
        acceso = get_album_access(conn, album_id, user_id)
        if acceso:
            accesos.append({**acceso, "album_id": album_id, "owner_id": asset["user_id"]})
    return accesos


def find_asset(conn, asset_id: int, *, trashed: bool | None = False):
    """El asset activo (`trashed=False`), en papelera (`True`) o cualquiera (`None`)."""
    estado = {False: "AND deleted_at IS NULL", True: "AND deleted_at IS NOT NULL", None: ""}[trashed]
    return execute_safe(
        conn,
        f"SELECT id, user_id, deleted_at FROM assets WHERE id = :asset_id {estado}",
        {"asset_id": asset_id},
    ).mappings().first()


def require_asset_permission(conn, asset_id: int, user_id: int, permission: str, *, trashed: bool | None = False):
    """`(asset, acceso)`; `(asset, None)` si no alcanza; `(None, None)` si no existe.

    El acceso devuelto es el de mayor rol, con la union de las capacidades de
    todos los albumes que le dan acceso.
    """
    asset = find_asset(conn, asset_id, trashed=trashed)
    if not asset:
        return None, None
    accesos = asset_accesses(conn, asset, user_id)
    if not accesos:
        return asset, None
    mejor = max(accesos, key=lambda a: _ROLE_RANK[a["role"]])
    union = set().union(*(a["capabilities"] for a in accesos))
    if not permission_allows(mejor["role"], permission):
        return asset, None
    return asset, {**mejor, "capabilities": union}


def require_asset_in_album(conn, asset_id: int, album_id: int, user_id: int):
    """`(asset, acceso)` para ver el asset desde un album concreto
    (`/albumes/:albumId/media/:mediaId`). El acceso es el de ESE album, no la
    union: la vista en contexto enseña lo que ese album permite.

    `(asset, None)` si el usuario no puede leer el album; `(None, None)` si el
    asset no existe o no es miembro. El acceso se comprueba antes que la
    pertenencia, para que quien no ve el album no pueda sondear que contiene.
    """
    asset = find_asset(conn, asset_id)
    if not asset:
        return None, None
    acceso = get_album_access(conn, album_id, user_id)
    if not acceso:
        return asset, None
    miembro = execute_safe(
        conn,
        f"SELECT 1 FROM assets m WHERE m.id = :asset_id AND {in_album_sql('album_id')}",
        {"asset_id": asset_id, "album_id": album_id},
    ).first()
    if not miembro:
        return None, None
    return asset, {**acceso, "album_id": album_id, "owner_id": asset["user_id"]}


def require_asset_capability(conn, asset_id: int, user_id: int, capability: str):
    """`(asset, acceso)` con el primer album que concede `capability` sobre un
    asset activo; `(asset, None)` si ninguno; `(None, None)` si no existe."""
    asset = find_asset(conn, asset_id)
    if not asset:
        return None, None
    for acceso in asset_accesses(conn, asset, user_id):
        if capability in acceso["capabilities"]:
            return asset, acceso
    return asset, None
