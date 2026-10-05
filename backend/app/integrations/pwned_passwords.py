"""External breach lookup is omitted; local password rules remain active."""


def is_password_pwned(password: str) -> bool | None:
    """Return ``None`` because the Demo does not query external services."""
    return None
