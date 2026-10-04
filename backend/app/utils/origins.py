"""Normalización única de los orígenes que el navegador puede usar."""

import os
from ipaddress import ip_address
from urllib.parse import urlsplit


def _environment() -> str:
    return (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()


def _parse_origins(value: str | None, variable: str) -> set[str]:
    origins = {item.strip().rstrip("/") for item in (value or "").split(",") if item.strip()}
    if "*" in origins:
        raise ValueError(f"{variable} no puede contener '*' ; use un origen HTTPS exacto")
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            raise ValueError(f"{variable} debe contener solo orígenes http(s) exactos")
        if parsed.scheme != "http" or parsed.username or parsed.password:
            raise ValueError(f"{variable} solo puede usar HTTP local en el Demo")
        try:
            local_frontend = parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port == 4200
        except ValueError:
            local_frontend = False
        if not local_frontend:
            raise ValueError(f"{variable} debe apuntar al frontend local en loopback")
    return origins


def cors_origins() -> list[str]:
    """CORS solo existe cuando hay un origen explícito.

    La edición Demo acepta solo el frontend local en `localhost:4200`.
    """
    origins = _parse_origins(os.getenv("CORS_ORIGINS"), "CORS_ORIGINS")
    if origins:
        return sorted(origins)
    return ["http://localhost:4200"]


def trusted_origins() -> set[str]:
    """Orígenes permitidos para la defensa CSRF además de SameSite.

    `PUBLIC_ORIGIN` may select the same local frontend origin used by Angular.
    """
    origins = _parse_origins(os.getenv("CORS_ORIGINS"), "CORS_ORIGINS")
    origins |= _parse_origins(os.getenv("PUBLIC_ORIGIN"), "PUBLIC_ORIGIN")
    if origins:
        return origins
    return {"http://localhost:4200"}


def webauthn_relying_party() -> tuple[str, list[str]] | None:
    """RP ID y orígenes esperados de las passkeys (L15), derivados de la misma
    autoridad que CSRF: nunca de lo que diga el navegador.

    El RP ID es el host de `PUBLIC_ORIGIN` o, si no hay, el único host de los
    orígenes de confianza. Todo origen tiene que ser ese host o un subdominio,
    HTTPS salvo `localhost` fuera de producción, y nunca una IP. Cualquier cosa
    ambigua devuelve `None` y las passkeys quedan desactivadas (503): mejor
    sin passkeys que con un RP ID adivinado."""
    try:
        origins = trusted_origins()
        public = _parse_origins(os.getenv("PUBLIC_ORIGIN"), "PUBLIC_ORIGIN")
    except ValueError:
        return None
    hosts = {urlsplit(origin).hostname or "" for origin in origins}
    if len(public) == 1:
        rp_id = urlsplit(next(iter(public))).hostname or ""
    elif len(hosts) == 1:
        rp_id = next(iter(hosts))
    else:
        return None
    try:
        ip_address(rp_id)
        return None
    except ValueError:
        pass
    production = _environment() == "production"
    for origin in origins:
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        if host != rp_id and not host.endswith("." + rp_id):
            return None
        if parsed.scheme != "https" and (production or host != "localhost"):
            return None
    return rp_id, sorted(origins)
