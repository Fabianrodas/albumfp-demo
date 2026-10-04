"""Lookup local de reputacion de IP -- nunca una llamada a AbuseIPDB.

`security_ip_reputation` la llena SOLO el job programado
(`python -m app.cli refresh-ip-reputation`). Consultarla en login/registro no
agrega ninguna llamada de red ni latencia de terceros: es un SELECT por clave
primaria contra una tabla local.

Una IP sin fila vigente (nunca reportada, o cache vencido porque el job no
corrio) no es sospechosa: es "sin señal", tratada igual que confianza 0. La
reputacion NUNCA es la unica autoridad de bloqueo -- solo achica el limite de
intentos que `app/security/rate_limit.py` ya aplicaba.

**Bug real encontrado en QA de S05, no en S04**: `client_ip()` puede devolver
`"desconocida"` (su propio valor de respaldo cuando `request.remote_addr` es
`None`, algo que en efecto pasa dentro de `Flask.test_client()` en ciertas
condiciones). Esa cadena no es una IP valida, y Postgres rechaza compararla
contra la columna `INET` con un error de sintaxis -- sin la validacion de
abajo, eso convertia una IP simplemente desconocida en un 500 en pleno login,
en vez de tratarla como "sin señal" igual que cualquier otra IP sin fila.
"""
import ipaddress

from ..utils.sql_security import execute_safe

# Umbral recomendado por la propia documentacion de AbuseIPDB para denegacion
# de servicio (75-100). El free tier de blacklist ya solo trae confianza 100,
# asi que en la practica esto es "esta en la lista o no"; el umbral se deja
# explicito por si el futuro trae una fuente con mas granularidad.
HIGH_RISK_THRESHOLD = 90


def reputation_score(conn, ip: str) -> int:
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        # No es una IP valida ("desconocida", vacio, etc.): no hay nada que
        # buscar, y desde luego no es motivo para tratarla como riesgosa.
        return 0
    fila = execute_safe(
        conn,
        "SELECT abuse_confidence FROM security_ip_reputation WHERE network_or_ip = :ip AND expires_at > NOW()",
        {"ip": ip},
    ).mappings().first()
    return fila["abuse_confidence"] if fila else 0


def is_high_risk(conn, ip: str) -> bool:
    return reputation_score(conn, ip) >= HIGH_RISK_THRESHOLD
