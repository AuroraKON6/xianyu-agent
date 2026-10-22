"""Role-aware PostgreSQL connections (taskbook R10).

Two roles, two jobs:

* ``admin`` (XIANYU_PG_ADMIN_DSN) — owns the schema; the ONLY role that
  may run migrations / DDL. Never handed to request handlers.
* ``app`` (XIANYU_PG_APP_DSN) — runtime DML role. In test contexts the
  DSN must additionally pass the offline guard's fail-closed allowlist
  (``XIANYU_PG_TEST_ALLOWLIST``, exact matches only — a database merely
  named like a test database is never sufficient).
"""
from __future__ import annotations

import os
from typing import Optional


class PersistenceError(RuntimeError):
    pass


def _dsn_from_env(name: str, *, required: bool = False) -> Optional[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        if required:
            raise PersistenceError(f"{name} is not configured")
        return None
    return value


def admin_dsn() -> Optional[str]:
    return _dsn_from_env("XIANYU_PG_ADMIN_DSN")


def app_dsn() -> Optional[str]:
    return _dsn_from_env("XIANYU_PG_APP_DSN")


def connect(dsn: str):
    """Open a psycopg2 connection with sane transaction defaults."""
    import psycopg2

    connection = psycopg2.connect(dsn)
    connection.autocommit = False
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, current_database()")
        user, database = cursor.fetchone()
    connection.rollback()
    return connection


def ensure_test_dsn_allowed(dsn: str, allowlist_env: str = "XIANYU_PG_TEST_ALLOWLIST") -> None:
    """Fail-closed test guard (R01 rule: no name-based trust)."""
    if os.environ.get("XIANYU_TEST_ISOLATION", "") == "1":
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
        try:
            from isolation_guard import assert_safe_postgres_dsn  # type: ignore
        except ImportError:
            raise PersistenceError("test isolation active but isolation_guard unavailable")
        allowed = [
            entry.strip()
            for entry in os.environ.get(allowlist_env, "").split(",")
            if entry.strip()
        ]
        assert_safe_postgres_dsn(dsn, allowed)
