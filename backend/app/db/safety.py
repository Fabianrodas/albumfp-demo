"""Fail-closed parsing for the Demo's local PostgreSQL URLs."""

import os
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit


_DATABASES = {
    "development": "albumfp_demo",
    "test": "albumfp_demo_test",
}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_POSTGRES_SCHEMES = {"postgresql", "postgresql+psycopg2"}


def validate_loopback_host(host: str) -> str:
    """Return a normalized loopback bind host, rejecting network interfaces."""
    if not isinstance(host, str):
        raise ValueError("services must bind to a loopback host")
    normalized = host.strip().lower()
    if normalized not in _LOOPBACK_HOSTS:
        raise ValueError("services must bind to a loopback host")
    return normalized


def database_url_from_environment(
    environ: Mapping[str, str] | None = None, *, purpose: str = "development"
) -> str:
    """Read only the URL dedicated to the requested Demo environment."""
    variable = {"development": "DATABASE_URL", "test": "TEST_DATABASE_URL"}.get(purpose)
    if variable is None:
        raise ValueError("database purpose must be development or test")

    values = os.environ if environ is None else environ
    url = values.get(variable)
    if not url:
        raise RuntimeError(f"{variable} must be set for the {purpose} database")
    try:
        return parse_demo_database_url(url, purpose=purpose)
    except ValueError as exc:
        raise RuntimeError(f"{variable} is not a safe local Demo database URL") from exc


def parse_demo_database_url(url: str, *, purpose: str = "development") -> str:
    """Validate and return a URL for the requested local Demo database."""
    if purpose not in _DATABASES:
        raise ValueError("database purpose must be development or test")
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError("a PostgreSQL URL is required")

    try:
        parts = urlsplit(url)
        host = parts.hostname
        database = unquote(parts.path[1:]) if parts.path.startswith("/") else ""
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid PostgreSQL URL") from exc

    if parts.scheme not in _POSTGRES_SCHEMES:
        raise ValueError("the Demo requires PostgreSQL")
    if parts.username != "albumfp_demo" or not parts.password:
        raise ValueError("the Demo database must use the non-admin albumfp_demo role")
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("the Demo database must use a loopback host")
    if port != 55432:
        raise ValueError("the Demo database must use its dedicated PostgreSQL port 55432")
    if parts.query or parts.fragment:
        raise ValueError("query parameters and fragments are not allowed")
    if parts.path.count("/") != 1 or database != _DATABASES[purpose]:
        raise ValueError(f"the {purpose} database name is not allowlisted")

    return url


def assert_demo_database_url(url: str, *, purpose: str = "development") -> None:
    """Raise before a connection when a URL is not an allowlisted Demo URL."""
    parse_demo_database_url(url, purpose=purpose)
