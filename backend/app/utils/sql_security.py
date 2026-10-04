import re
from typing import Any
from sqlalchemy import text

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_identifier(identifier: str, allowed: set[str] | None = None) -> str:
    if not identifier or not _IDENTIFIER_RE.match(identifier):
        raise ValueError("Identificador SQL inválido")
    if allowed is not None and identifier not in allowed:
        raise ValueError("Identificador SQL no permitido")
    return identifier


def sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}

    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if not _PARAM_RE.match(key):
            raise ValueError(f"Nombre de parámetro inválido: {key}")
        cleaned[key] = value
    return cleaned


def safe_text(query: str):
    normalized = (query or "").strip()
    if not normalized:
        raise ValueError("Query vacío")
    if "--" in normalized or "/*" in normalized or "*/" in normalized:
        raise ValueError("Query inválido")
    if ";" in normalized.rstrip(";"):
        raise ValueError("Solo se permite una sentencia SQL")
    return text(normalized)


def execute_safe(conn, query: str, params: dict[str, Any] | None = None):
    stmt = safe_text(query)
    return conn.execute(stmt, sanitize_params(params))
