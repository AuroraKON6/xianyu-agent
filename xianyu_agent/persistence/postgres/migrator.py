"""Versioned SQL migration runner (taskbook R10).

Migrations live in ``migrations/`` as ``NNNN_name.up.sql`` /
``NNNN_name.down.sql`` pairs. The runner:

* owns a ``schema_migrations`` bookkeeping table;
* applies each pending ``up`` script inside ONE transaction, recording
  the version only when the script committed;
* supports ``downgrade`` via the matching ``down`` script (rollback
  compatibility: the application version that shipped a migration must
  be stopped first — JSON stores stay the recovery source);
* is idempotent (``upgrade`` twice applies nothing the second time).

Only the admin role may run this module.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .connection import PersistenceError

MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum   TEXT NOT NULL
)
"""

_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.up\.sql$")


def discover(migrations_dir: Path) -> List[Dict[str, str]]:
    """List available migrations sorted by version."""
    found = []
    for path in sorted(Path(migrations_dir).glob("*.up.sql")):
        match = _MIGRATION_RE.match(path.name)
        if match is None:
            raise PersistenceError(f"unrecognized migration file: {path.name}")
        down = path.with_name(path.name.replace(".up.sql", ".down.sql"))
        if not down.is_file():
            raise PersistenceError(f"migration {path.name} has no .down.sql (rollback required)")
        found.append(
            {
                "version": match.group(1),
                "name": match.group(2),
                "up_path": str(path),
                "down_path": str(down),
                "checksum": _checksum(path),
            }
        )
    return found


def _checksum(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


class MigrationRunner:
    def __init__(self, connection, migrations_dir: Path) -> None:
        self._conn = connection
        self._dir = Path(migrations_dir)

    def _ensure_bookkeeping(self) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(MIGRATIONS_TABLE)
        self._conn.commit()

    def applied(self) -> Dict[str, str]:
        self._ensure_bookkeeping()
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")
            return {version: checksum for version, checksum in cursor.fetchall()}

    def status(self) -> Dict[str, object]:
        available = discover(self._dir)
        applied = self.applied()
        for entry in available:
            recorded = applied.get(entry["version"])
            if recorded is not None and recorded != entry["checksum"]:
                raise PersistenceError(
                    f"migration {entry['version']} changed on disk after being applied "
                    f"(applied checksum {recorded}, disk {entry['checksum']}); refusing"
                )
        return {
            "applied": sorted(applied),
            "pending": [e["version"] for e in available if e["version"] not in applied],
            "available": [e["version"] for e in available],
        }

    def upgrade(self) -> List[str]:
        """Apply all pending migrations; returns the versions applied."""
        applied_now: List[str] = []
        state = self.status()
        for entry in discover(self._dir):
            if entry["version"] in state["applied"]:
                continue
            sql = Path(entry["up_path"]).read_text(encoding="utf-8")
            # one migration = one transaction (psycopg2 already opened one)
            with self._conn.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (entry["version"], entry["name"], entry["checksum"]),
                )
            self._conn.commit()
            applied_now.append(entry["version"])
        return applied_now

    def downgrade(self, *, to_version: str = "") -> List[str]:
        """Roll back migrations newest-first down to (excluding) to_version.

        Rollback-compatibility rule: stop the application version that
        shipped these migrations first; JSON stores remain the recovery
        source of truth.
        """
        target = str(to_version)
        applied = self.applied()
        removed: List[str] = []
        for entry in reversed(discover(self._dir)):
            version = entry["version"]
            if version not in applied:
                continue
            if version == target:
                break
            sql = Path(entry["down_path"]).read_text(encoding="utf-8")
            with self._conn.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "DELETE FROM schema_migrations WHERE version = %s", (version,)
                )
            self._conn.commit()
            applied.pop(version, None)
            removed.append(version)
            if target and version < target:
                break
        return removed

    def grant_runtime_role(self, role: str) -> None:
        """DML-only grants for the app role; ledger stays append-only.

        The runtime role gets SELECT/INSERT/UPDATE on business tables —
        no DDL, no DELETE, and ledger_entries is INSERT+SELECT only so
        history cannot be edited or dropped by the application.
        """
        from .connection import PersistenceError as _PE

        role = str(role).strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
            raise _PE(f"invalid role name: {role!r}")
        with self._conn.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname = %s", (role,)
            )
            if not cursor.fetchone()[0]:
                raise _PE(f"role {role!r} does not exist; create it first")
            cursor.execute(
                """
                GRANT USAGE ON SCHEMA public TO {role};
                GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role};
                REVOKE UPDATE, DELETE, TRUNCATE ON TABLE ledger_entries FROM {role};
                REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM {role};
                GRANT INSERT, SELECT ON TABLE ledger_entries TO {role};
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
                """.format(role=role)
            )
        self._conn.commit()
