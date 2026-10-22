"""Unit of Work: one transaction boundary for one business command
(taskbook R10).

Rules encoded here:

* every business mutation runs inside exactly one transaction; leaving
  the context commits, an exception rolls back — a half-applied command
  never becomes visible;
* lock ordering is FIXED and documented: accounts → research entities
  (offers → matches → opportunities) → jobs/work orders → fulfilment
  (sales orders → procurement → budget → inventory) → ledger → outbox.
  Writers take PostgreSQL transaction-scoped advisory locks in this
  order so two concurrent commands cannot deadlock;
* the transactional outbox row is written in the SAME transaction as
  the business intent (an external effect without a committed intent is
  impossible, and a committed intent is never lost).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Dict, Optional

# fixed global lock order (taskbook R10: canonical lock ordering)
LOCK_ORDER = (
    "accounts",
    "offer_snapshots",
    "product_matches",
    "opportunities",
    "agent_jobs",
    "work_orders",
    "sales_orders",
    "procurement_orders",
    "budget_reservations",
    "inventory_locks",
    "ledger_entries",
    "outbox",
)


class UnitOfWork:
    """One connection, one transaction, ordered advisory locks."""

    def __init__(self, connection) -> None:
        self._conn = connection

    def begin(self) -> "UnitOfWork":
        self._conn.rollback()  # drop any implicit snapshot, start clean
        return self

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    # ---- lock discipline ---------------------------------------------------

    def lock(self, *keys: str) -> None:
        """Take transaction-scoped advisory locks in CANONICAL order.

        ``lock('sales_orders:acc-1:CH:oid9', 'ledger_entries:acc-1')``
        sorts the domains first (LOCK_ORDER), then locks each key once —
        callers may pass keys in any order without deadlock risk against
        other UnitOfWork users.
        """
        rank = {domain: index for index, domain in enumerate(LOCK_ORDER)}
        ordered = sorted(keys, key=lambda key: (rank.get(key.split(":", 1)[0], 99), key))
        with self._conn.cursor() as cursor:
            for key in ordered:
                domain = key.split(":", 1)[0]
                if domain not in rank:
                    raise ValueError(f"unknown lock domain: {domain!r} (key {key!r})")
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))

    # ---- outbox -------------------------------------------------------------

    def write_outbox(
        self,
        *,
        intent_key: str,
        account_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append a transactional-outbox row inside THIS transaction."""
        record_id = "obx_" + uuid.uuid4().hex[:20]
        import json

        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO outbox (record_id, intent_key, account_id, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (intent_key) DO NOTHING
                RETURNING record_id
                """,
                (record_id, intent_key, account_id, json.dumps(payload or {}, ensure_ascii=False)),
            )
            row = cursor.fetchone()
        return row[0] if row else ""  # empty => duplicate intent, caller checks

    # ---- context manager ------------------------------------------------------

    def __enter__(self) -> "UnitOfWork":
        return self.begin()

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


@contextmanager
def transaction(connection):
    """Functional form: ``with transaction(conn) as uow: ...``"""
    uow = UnitOfWork(connection)
    uow.begin()
    try:
        yield uow
    except Exception:
        uow.rollback()
        raise
    else:
        uow.commit()
