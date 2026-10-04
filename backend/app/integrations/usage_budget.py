"""Presupuesto diario compartido por cualquier proveedor externo con cuota.

Una sola tabla generica (`integration_usage`, clave (provider, period_key)) en
vez de una por proveedor: LocationIQ la usa hoy, Visual Crossing/OCR.Space/
Imagga la reutilizaran igual mas adelante, sin migracion nueva.

`try_reserve` hace la comprobacion y el incremento en UNA sola sentencia SQL
(UPDATE ... WHERE request_count < :budget), para que dos peticiones a la vez
nunca puedan colarse las dos por debajo del mismo limite: solo una gana la
fila, la otra no encuentra RETURNING y sabe que debe abortar antes de llamar
al proveedor. El "reservar antes de llamar" es deliberado: cuenta el intento,
no solo el exito, para nunca arriesgarse a pasarse de la cuota real del
proveedor por una carrera entre dos peticiones simultaneas.
"""
from datetime import date

from ..utils.sql_security import execute_safe


def try_reserve(conn, provider: str, daily_budget: int, *, today: date | None = None, period: str = "day") -> bool:
    """True si queda cupo y se reservo una llamada; False si ya se agoto.

    `period="month"` cuenta por mes en vez de por dia: el cupo gratuito de
    Imagga es mensual (100/mes), no diario como el de los demas. Es solo otra
    forma de `period_key` en la misma tabla, sin columna ni tabla nueva.
    """
    if daily_budget <= 0:
        return False

    hoy = today or date.today()
    period_key = hoy.strftime("%Y-%m") if period == "month" else hoy.isoformat()
    execute_safe(
        conn,
        """
        INSERT INTO integration_usage (provider, period_key, request_count)
        VALUES (:provider, :period_key, 0)
        ON CONFLICT (provider, period_key) DO NOTHING
        """,
        {"provider": provider, "period_key": period_key},
    )
    reserved = execute_safe(
        conn,
        """
        UPDATE integration_usage
        SET request_count = request_count + 1, updated_at = NOW()
        WHERE provider = :provider AND period_key = :period_key AND request_count < :budget
        RETURNING request_count
        """,
        {"provider": provider, "period_key": period_key, "budget": daily_budget},
    ).first()
    return reserved is not None
