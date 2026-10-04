"""Explicit maintenance commands for a local AlbumFP Demo installation.

Run commands from the backend directory with the Demo environment loaded.
Commands that touch PostgreSQL use the guarded Demo database connection;
media maintenance uses the configured local filesystem root.
"""
import inspect
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

from .db.db import db_conn
from .media.checksum_jobs import backfill_media_checksums, verify_media_integrity
from .notifications import purge_expired as purge_expired_activity_rows, retention_days
from .security.rate_limit import purge_expired_counters
from .security.sessions import hash_token
from .security.share_unlock import purge_expired_unlocks
from .storage.backends import get_storage_backend
from .storage.compensation import mark_committed, record_pending
from .storage.quarantine import quarantine_root
from .utils.env import int_env
from .utils.sql_security import execute_safe

def cleanup_quarantine() -> int:
    umbral_segundos = int_env("MEDIA_QUARANTINE_MAX_AGE_HOURS", 24) * 3600
    corte = time.time() - umbral_segundos
    borrados = 0
    for item in quarantine_root().iterdir():
        if item.is_file() and item.stat().st_mtime < corte:
            item.unlink(missing_ok=True)
            borrados += 1
    print(f"{borrados} archivo(s) huerfano(s) de cuarentena eliminados.")
    return 0


def _referenced_storage_paths(conn) -> set[str]:
    referenciados: set[str] = set()
    for fila in execute_safe(conn, "SELECT storage_path FROM assets", {}).mappings().all():
        referenciados.add(fila["storage_path"])
    for fila in execute_safe(
        conn, "SELECT preview_storage_path FROM media_metadata WHERE preview_storage_path IS NOT NULL", {}
    ).mappings().all():
        referenciados.add(fila["preview_storage_path"])
    for fila in execute_safe(
        conn, "SELECT avatar_path FROM users WHERE avatar_path IS NOT NULL", {}
    ).mappings().all():
        referenciados.add(fila["avatar_path"])
    return referenciados


def _protected_storage_paths(conn) -> set[str]:
    """Union de las tres referencias de la base con las keys mencionadas en
    cualquier registro de compensacion todavia sin resolver (pending o
    rolled_back).

    El margen de edad ya protege una subida legitima que esta en curso ahora
    mismo, pero un registro de compensacion es la señal explicita de que una
    key ya se escribio en storage y todavia no se sabe si su fila de base
    comiteo o si su delete se reconcilio (spec seccion 12, paso 4: "consulta
    de nuevo referencias DB y registros/leases activos"). Un registro
    illegible hace fallar esta funcion entera -- mismo contrato fail-closed
    que `compensation.pending_operations()`, nunca se ignora en silencio.
    """
    from .storage.compensation import pending_operations

    protegidos = _referenced_storage_paths(conn)
    for registro in pending_operations():
        protegidos.update(registro["keys"])
    return protegidos


def cleanup_orphaned_media() -> int:
    """Borra objetos de almacenamiento sin referencia y mas viejos que
    `ORPHANED_MEDIA_MAX_AGE_HOURS` (spec seccion 12).

    SEGURIDAD CRITICA, fail-closed en cada fase: si las referencias de la
    base no se pueden leer, si el backend de storage no se puede obtener, si
    el inventario llega incompleto (pagina truncada, snapshot que cambia a
    mitad del recorrido) o si el origin se cae a mitad del recorrido, el
    contrato es CERO deletes -- nunca "borra lo que se alcanzo a ver". Un
    inventario a medias es indistinguible de una biblioteca vacia, y eso no
    puede terminar en un DELETE masivo.
    """
    from .storage.contracts import ObjectConflict, StorageError

    umbral_horas = max(24, int_env("ORPHANED_MEDIA_MAX_AGE_HOURS", 24))

    try:
        with db_conn() as conn:
            protegidos = _protected_storage_paths(conn)
    except Exception as exc:
        print(f"No se pudo leer las referencias de la base: {exc}")
        return 1

    try:
        backend = get_storage_backend()
    except StorageError as exc:
        print(f"No se pudo obtener el backend de almacenamiento ({exc}): no se borra nada.")
        return 1

    inventario: list = []
    cursor = None
    snapshot = None
    snapshot_created_at = None
    try:
        while True:
            pagina = backend.list_objects(cursor=cursor, limit=500)
            if snapshot is None:
                snapshot = pagina.snapshot_id
                snapshot_created_at = pagina.created_at
            elif pagina.snapshot_id != snapshot:
                print("El inventario cambio de snapshot a mitad del recorrido: no se borra nada.")
                return 1
            inventario.extend(pagina.objects)
            if pagina.complete:
                break
            if not pagina.next_cursor:
                print("El inventario termino sin marcarse completo: no se borra nada.")
                return 1
            cursor = pagina.next_cursor
    except StorageError as exc:
        print(f"No se pudo obtener el inventario ({exc}): no se borra nada.")
        return 1

    # La edad se mide contra el reloj del snapshot, no `time.time()` de este
    # proceso: RackNerd y el local workstation son hosts distintos, y mezclar relojes
    # podria hacer que un objeto recien subido parezca "viejo" por un simple
    # adelanto de reloj local (spec seccion 12, paso 3).
    corte_ns = int((snapshot_created_at - umbral_horas * 3600) * 1e9)
    candidatos = [o for o in inventario
                  if o.key not in protegidos and o.modified_at_ns < corte_ns]

    borrados = pendientes = 0
    for lote in (candidatos[i:i + 100] for i in range(0, len(candidatos), 100)):
        try:
            with db_conn() as conn:
                vigentes = _protected_storage_paths(conn)
        except Exception as exc:
            print(f"La base dejo de responder ({exc}): se detiene el borrado.")
            break
        for objeto in lote:
            if objeto.key in vigentes:
                continue
            try:
                if backend.delete(objeto.key, expected_version=objeto.version):
                    borrados += 1
            except ObjectConflict:
                pendientes += 1  # cambio bajo nuestros pies: se conserva
            except StorageError as exc:
                print(f"El almacenamiento dejo de responder ({exc}): se detiene el borrado.")
                pendientes += 1
                break

    print(f"{borrados} archivo(s) huerfano(s) de almacenamiento final eliminados.")
    return 1 if pendientes else 0


def cleanup_workspace() -> int:
    """Borra residuos de workspaces locales abandonados por un proceso
    muerto a la fuerza (spec seccion 11). Puramente local: nunca llama al
    backend de almacenamiento configurado, corre igual en local y remote."""
    from .storage.workspace import sweep_workspaces

    borrados = sweep_workspaces(int_env("MEDIA_WORK_MAX_AGE_HOURS", 24))
    print(f"{borrados} workspace(s) abandonado(s) eliminados.")
    return 0


def reconcile_storage_operations() -> int:
    """Cierra las operaciones de storage que quedaron a medias, comprobando
    SIEMPRE las referencias reales de la base antes de borrar nada (spec
    seccion 7)."""
    from .storage.compensation import discard_operation, pending_operations
    from .storage.contracts import StorageError

    umbral = max(24, int_env("ORPHANED_MEDIA_MAX_AGE_HOURS", 24)) * 3600
    try:
        with db_conn() as conn:
            referenciados = _referenced_storage_paths(conn)
    except Exception as exc:
        print(f"No se pudo leer las referencias de la base: {exc}")
        return 1

    try:
        backend = get_storage_backend()
    except StorageError as exc:
        print(f"No se pudo obtener el backend de almacenamiento ({exc}).")
        return 1

    resueltas = fallidas = 0
    ahora = datetime.now(timezone.utc)
    for registro in pending_operations():
        creado = datetime.fromisoformat(registro["created_at"])
        vencida = (ahora - creado).total_seconds() >= umbral
        if registro["state"] == "pending" and not vencida:
            continue  # todavia puede estar en curso
        huerfanas = [k for k in registro["keys"] if k not in referenciados]
        try:
            for clave in huerfanas:
                backend.delete(clave)
        except StorageError as exc:
            print(f"  ! {registro['operation_id']}: {exc}")
            fallidas += 1
            continue
        discard_operation(registro["operation_id"])
        resueltas += 1

    print(f"{resueltas} operacion(es) reconciliada(s), {fallidas} pendiente(s).")
    return 1 if fallidas else 0


def storage_key_lookup(argv) -> int:
    """Diagnostico explicito del operador: de key a hash, o de hash a key.
    La salida va a esta terminal, nunca a un log."""
    from .storage.object_keys import key_hash

    if not argv:
        print("Uso: python -m app.cli storage-key-lookup <key-o-key_hash>")
        return 2
    entrada = argv[0].strip()
    if "/" in entrada:
        print(f"key      = {entrada}")
        print(f"key_hash = {key_hash(entrada)}")
        return 0
    with db_conn() as conn:
        for clave in sorted(_referenced_storage_paths(conn)):
            if key_hash(clave) == entrada:
                print(f"key_hash = {entrada}")
                print(f"key      = {clave}")
                return 0
    print(f"Ninguna key referenciada tiene el hash {entrada}.")
    return 1


def check_storage() -> int:
    """Estado del almacenamiento de objetos y del workspace local, por
    separado (spec seccion 20). Nunca imprime credenciales. Fail-closed: si
    no se puede medir con confianza, el codigo de salida es 4."""
    import shutil

    from .storage.backends import storage_backend_mode
    from .storage.contracts import StorageError
    from .storage.workspace import workspace_root

    max_percent = float((os.getenv("MEDIA_MAX_USAGE_PERCENT") or "90").strip())
    min_free = float((os.getenv("MEDIA_MIN_FREE_GB") or "5").strip()) * 1024**3

    try:
        backend = get_storage_backend()
        if hasattr(backend, "ready"):
            backend.ready()
        cap = backend.capacity()
    except StorageError as exc:
        print(f"Almacenamiento de objetos: NO DISPONIBLE ({type(exc).__name__})")
        return 4
    if not cap.mount_ok or not cap.writable:
        print("Almacenamiento de objetos: montaje ausente o no escribible")
        return 4

    porcentaje = (cap.used_bytes / cap.total_bytes * 100) if cap.total_bytes else 100.0
    print(f"Almacenamiento de objetos ({storage_backend_mode()}): "
          f"{porcentaje:.1f}% usado, {cap.free_bytes / 1024**3:.1f} GiB libres")

    uso_local = shutil.disk_usage(workspace_root())
    print(f"Workspace local: {uso_local.free / 1024**3:.1f} GiB libres en {workspace_root()}")

    if porcentaje >= max_percent:
        print(f"AVISO: se alcanzo el umbral de uso ({max_percent}%).")
        return 2
    if cap.free_bytes < min_free:
        print(f"AVISO: se alcanzo la reserva minima ({min_free / 1024**3:.0f} GiB).")
        return 3
    return 0


def purge_expired_trash() -> int:
    """Papelera global: borra definitivamente la media con mas de 30 dias.

    Era `POST /api/internal/jobs/trash/purge-expired`. El orden importa y es el
    mismo de siempre: la intencion durable se registra ANTES del commit, para
    que un proceso muerto entre el commit y el borrado fisico deje un rastro
    que el reconciliador pueda retomar. Nunca se reinsertan filas.
    """
    # Import diferido: `app.api.media` arrastra Flask y el resto de blueprints,
    # y ningun otro comando de esta CLI los necesita.
    from .api.media import _purge_media_rows, remove_media_files

    with db_conn() as conn:
        deleted = _purge_media_rows(
            conn,
            "m.deleted_at IS NOT NULL AND m.deleted_at < NOW() - INTERVAL '30 days'",
            {},
        )
        claves = [c for i in deleted for c in (i.get("storage_path"), i.get("preview_storage_path")) if c]
        operacion = record_pending("delete", claves) if claves else None

    # Fuera de la transaccion: si la base falla, nunca se pierde el archivo
    # antes de confirmar que su fila desaparecio.
    borrados, pendientes = remove_media_files(deleted)
    if operacion and not pendientes:
        mark_committed(operacion)
    if pendientes:
        # Sale 0 a proposito aunque queden pendientes: no es un fallo, es una
        # finalizacion diferida que `reconcile-storage-operations` drena sola.
        # Devolver 1 pintaria de rojo el timer por una condicion que se cura
        # sola, y el ruido acabaria haciendo que nadie mire los rojos de verdad.
        print(f"purge-expired-trash: {pendientes} objeto(s) pendiente(s); "
              f"los retoma reconcile-storage-operations")
    print(f"{len(deleted)} elemento(s) purgado(s), {borrados} archivo(s) borrado(s).")
    return 0


def purge_expired_rate_limits() -> int:
    """Ventanas de login/registro ya vencidas (S03). Esta tabla SI necesita
    purga periodica: un endpoint publico puede recibir mucho mas trafico de
    abuso que el resto de la app."""
    with db_conn() as conn:
        borradas = purge_expired_counters(conn)
    print(f"{borradas} contador(es) de limite vencido(s) borrado(s).")
    return 0


def purge_expired_share_unlocks() -> int:
    """Cookies de desbloqueo de enlaces con contrasena ya vencidas (S08), por
    el mismo motivo de volumen que los contadores de limite."""
    with db_conn() as conn:
        borradas = purge_expired_unlocks(conn)
    print(f"{borradas} desbloqueo(s) de enlace vencido(s) borrado(s).")
    return 0


def purge_expired_activity_notifications() -> int:
    """Retención de L12: avisos in-app de mas de `NOTIFICATION_RETENTION_DAYS`
    (90) y actividad de album de mas de `ALBUM_ACTIVITY_RETENTION_DAYS` (365),
    leidos o no. Solo filas de PostgreSQL: ni HTTP, ni token, ni un archivo."""
    avisos_dias, actividad_dias = retention_days()
    with db_conn() as conn:
        avisos, eventos = purge_expired_activity_rows(conn, avisos_dias, actividad_dias)
    print(f"{avisos} notificación(es) de más de {avisos_dias} días borrada(s); "
          f"{eventos} evento(s) de actividad de más de {actividad_dias} días borrado(s).")
    return 0


def purge_expired_webauthn_challenges() -> int:
    """Challenges de passkey vencidos (L15). Cada challenge nuevo ya se lleva
    los vencidos; esto cubre una instalación sin tráfico de passkeys."""
    from .api.passkeys import purge_expired_challenges

    with db_conn() as conn:
        borrados = purge_expired_challenges(conn)
    print(f"{borrados} challenge(s) de passkey vencido(s) borrado(s).")
    return 0


def create_registration_invite() -> int:
    """Crea una invitacion de registro de un solo uso.

    Exclusivo del operador del servidor: no existe ningun camino para que un
    usuario invite a otro, seria una dinamica social que este producto no
    tiene. El token en claro se imprime UNA vez, aqui, en esta terminal y
    nunca en un log; la base solo guarda su SHA-256, igual que las sesiones.
    Que esto sea un comando local y no una ruta HTTP ademas evita que el token
    recien creado viaje por ningun sitio.
    """
    raw_token = secrets.token_urlsafe(32)
    dias = int_env("REGISTRATION_INVITE_EXPIRES_DAYS", 7)
    expires_at = datetime.now() + timedelta(days=dias)

    with db_conn() as conn:
        execute_safe(
            conn,
            "INSERT INTO registration_invites (token_hash, expires_at) VALUES (:token_hash, :expires_at)",
            {"token_hash": hash_token(raw_token), "expires_at": expires_at},
        )

    print(f"Invitacion creada. Caduca el {expires_at.isoformat()}.")
    print(f"Token (se muestra una sola vez): {raw_token}")
    return 0


COMMANDS = {
    "cleanup-quarantine": cleanup_quarantine,
    "cleanup-orphaned-media": cleanup_orphaned_media,
    "cleanup-workspace": cleanup_workspace,
    "reconcile-storage-operations": reconcile_storage_operations,
    "storage-key-lookup": storage_key_lookup,
    "check-storage": check_storage,
    "backfill-media-checksums": backfill_media_checksums,
    "verify-media-integrity": verify_media_integrity,
    "purge-expired-trash": purge_expired_trash,
    "purge-expired-rate-limits": purge_expired_rate_limits,
    "purge-expired-share-unlocks": purge_expired_share_unlocks,
    "purge-expired-activity-notifications": purge_expired_activity_notifications,
    "purge-expired-webauthn-challenges": purge_expired_webauthn_challenges,
    "create-registration-invite": create_registration_invite,
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(f"Uso: python -m app.cli <comando> [argumentos]\nComandos: {', '.join(sorted(COMMANDS))}")
        return 2
    comando = COMMANDS[argv[0]]
    # Los comandos que aceptan argumentos declaran un parametro (por ahora,
    # solo storage-key-lookup); el resto se llama sin argumentos.
    if inspect.signature(comando).parameters:
        return comando(argv[1:])
    return comando()


if __name__ == "__main__":
    sys.exit(main())
