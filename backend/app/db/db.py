"""Guarded SQLAlchemy connection used by the local Demo application."""

import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

from .safety import database_url_from_environment


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPOSITORY_ROOT / ".env", override=False)


def _database_purpose() -> str:
    configured = (os.getenv("ALBUMFP_DEMO_TEST_MODE") or "").strip().lower()
    app_environment = (os.getenv("APP_ENV") or "").strip().lower()
    enabled_values = {"1", "true", "yes", "on"}
    disabled_values = {"", "0", "false", "no", "off"}
    if configured not in enabled_values | disabled_values:
        raise RuntimeError("ALBUMFP_DEMO_TEST_MODE must be a boolean value")
    if configured in enabled_values or app_environment in {"test", "testing"}:
        return "test"
    return "development"


connection_url = database_url_from_environment(purpose=_database_purpose())
engine = create_engine(connection_url, pool_pre_ping=True)


@contextmanager
def db_conn():
    """Yield a transaction on the validated Demo database."""
    with engine.begin() as connection:
        yield connection
