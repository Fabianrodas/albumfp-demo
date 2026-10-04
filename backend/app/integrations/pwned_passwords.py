"""Remote breach lookup is omitted; local password rules remain active."""


def is_password_pwned(password: str) -> bool | None:
    """Return ``None`` to signal that no remote lookup runs in the Demo."""
    return None
