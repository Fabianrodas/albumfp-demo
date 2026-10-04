import os


def truthy(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser entero") from exc
