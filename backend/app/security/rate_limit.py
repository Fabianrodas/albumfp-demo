"""Limitador de intentos por ventana fija, y la unica fuente de la IP del
cliente (fase S03 del roadmap de seguridad).

`try_consume` generaliza el mismo patron atomico que ya usa
`app/integrations/usage_budget.py::try_reserve` para el presupuesto diario de
las integraciones externas (INSERT...ON CONFLICT DO NOTHING seguido de
UPDATE...WHERE count < limite RETURNING, para que dos peticiones a la vez
nunca puedan colarse las dos por debajo del mismo limite). No se reutiliza esa
misma tabla (`integration_usage`) a proposito: ahi el identificador es un
nombre de proveedor estable y de bajo volumen; aqui es un username o una IP,
con volumen potencialmente alto si alguien abusa del endpoint publico de
login/registro, y con una politica de retencion distinta (hay que poder
purgar las ventanas ya vencidas, cosa que `integration_usage` nunca necesito).

`client_ip()` usa la dirección del socket local. Los encabezados reenviados no
se aceptan en esta edición, que no se ejecuta detrás de un proxy.

**ponytail: ventana fija, no deslizante.** Un atacante que sincronice sus
intentos justo antes y despues del corte de ventana puede colar hasta 2x el
limite en una franja corta. Aceptable para el volumen de esta app (personal,
reservada); si algun dia hace falta cerrar ese margen, la vía es una ventana
deslizante (Redis con ZSET, o un segundo contador desplazado medio periodo).
"""
from datetime import datetime, timedelta

from flask import request

from ..utils.sql_security import execute_safe


def client_ip() -> str:
    """La única fuente de la dirección del cliente: el socket directo."""
    return request.remote_addr or "desconocida"


def _window(ahora: datetime, window_seconds: int) -> tuple[str, datetime, int]:
    """(clave de la ventana ACTUAL, cuándo termina, segundos que faltan)."""
    epoch = int(ahora.timestamp())
    inicio_epoch = epoch - (epoch % window_seconds)
    window_ends_at = datetime.fromtimestamp(inicio_epoch + window_seconds)
    return str(inicio_epoch), window_ends_at, max(1, int((window_ends_at - ahora).total_seconds()))


def peek(conn, scope: str, identifier: str, limit: int, window_seconds: int,
         *, now: datetime | None = None) -> tuple[int, int]:
    """Cuántos intentos quedan en la ventana actual, SIN gastar ninguno.
    Devuelve `(restantes, retry_after_segundos)`. Solo para enseñarlo en la
    UI: la autoridad sigue siendo `try_consume`."""
    window_key, _ends, retry_after = _window(now or datetime.now(), window_seconds)
    usados = execute_safe(
        conn,
        """
        SELECT request_count FROM rate_limit_counters
        WHERE scope = :scope AND identifier = :identifier AND window_key = :window_key
        """,
        {"scope": scope, "identifier": identifier, "window_key": window_key},
    ).scalar() or 0
    return max(0, limit - usados), retry_after


def try_consume(conn, scope: str, identifier: str, limit: int, window_seconds: int,
                *, now: datetime | None = None) -> tuple[bool, int]:
    """Reserva un intento dentro de la ventana actual.

    Devuelve `(permitido, retry_after_segundos)`. `retry_after_segundos` es
    cuanto falta para que la ventana ACTUAL termine -- vale tanto si se
    permitio (informativo) como si no (para la cabecera `Retry-After`).

    `limit <= 0` siempre deniega: es la forma de desactivar por completo un
    scope sin tocar el codigo que lo llama, mismo criterio que
    `try_reserve` con `daily_budget <= 0`.
    """
    window_key, window_ends_at, retry_after = _window(now or datetime.now(), window_seconds)

    if limit <= 0:
        return False, retry_after

    execute_safe(
        conn,
        """
        INSERT INTO rate_limit_counters (scope, identifier, window_key, request_count, window_ends_at)
        VALUES (:scope, :identifier, :window_key, 0, :window_ends_at)
        ON CONFLICT (scope, identifier, window_key) DO NOTHING
        """,
        {"scope": scope, "identifier": identifier, "window_key": window_key, "window_ends_at": window_ends_at},
    )
    reservado = execute_safe(
        conn,
        """
        UPDATE rate_limit_counters
        SET request_count = request_count + 1
        WHERE scope = :scope AND identifier = :identifier AND window_key = :window_key
          AND request_count < :limit
        RETURNING request_count
        """,
        {"scope": scope, "identifier": identifier, "window_key": window_key, "limit": limit},
    ).first()
    return reservado is not None, retry_after


def purge_expired_counters(conn, *, older_than: timedelta = timedelta(hours=1)) -> int:
    """Borra ventanas que ya vencieron hace rato. El margen (1h por defecto)
    no es por correccion -- una fila vencida ya no la usa `try_consume`, que
    siempre calcula la ventana ACTUAL -- es solo para no purgar algo que un
    operador pudiera querer inspeccionar en los minutos posteriores."""
    resultado = execute_safe(
        conn,
        "DELETE FROM rate_limit_counters WHERE window_ends_at < :corte",
        {"corte": datetime.now() - older_than},
    )
    return resultado.rowcount or 0
