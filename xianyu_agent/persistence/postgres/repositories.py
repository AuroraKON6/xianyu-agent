"""PostgreSQL implementations of the commerce repository contract
(taskbook R10).

Same behavioral contract as the JSON repositories (same ABCs), backed
by the 0001 schema: composite unique keys, optimistic revisions,
NUMERIC money, JSONB payload round-trip. These implementations prove
the contract is storage-independent; production switchover happens in
R15 with the full-domain migration — nothing here is wired into the
running server yet (no dual writes).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ...commerce import contracts as c



def _loads(payload):
    """psycopg2 may return JSONB as dict (json) or str; handle both."""
    if isinstance(payload, (dict, list)):
        return payload
    return json.loads(payload)

class PostgresOfferSnapshotRepository:
    """Contract-mirror of OfferSnapshotRepository over PG."""

    def __init__(self, uow) -> None:
        self._uow = uow

    def _row_to_record(self, row) -> Dict[str, Any]:
        payload = row["payload"]
        return dict(_loads(payload))

    def add(self, snapshot: Mapping[str, Any]) -> Tuple[Dict[str, Any], bool]:
        record = dict(snapshot)
        record.setdefault("record_id", "off_" + uuid.uuid4().hex[:20])
        price = dict(record.get("eligible_price") or {})
        state = str(price.get("state") or "unknown")
        amount = price.get("amount") if state == "known" else None
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO offer_snapshots
                    (record_id, account_id, channel, provider_id, source_item_id,
                     source_sku_id, observed_at, price_state, price_amount,
                     price_currency, price_reason, payload, payload_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING record_id
                """,
                (
                    record["record_id"],
                    str(record.get("account_id") or ""),
                    str(record.get("channel") or ""),
                    str(record.get("provider_id") or ""),
                    str(record.get("source_item_id") or ""),
                    str(record.get("source_sku_id") or ""),
                    record.get("observed_at"),
                    state,
                    amount,
                    str(price.get("currency") or "CNY"),
                    str(price.get("reason") or ""),
                    json.dumps(record, ensure_ascii=False),
                    str(record.get("payload_hash") or uuid.uuid4().hex),
                ),
            )
            inserted = cursor.fetchone()
        if inserted is None:
            # natural-key dedupe: return the existing record
            existing = self._find_existing(record)
            if existing is not None:
                return existing, False
            raise RuntimeError("insert conflicted but existing row not found")
        return record, True

    def _find_existing(self, record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload FROM offer_snapshots
                WHERE account_id = %s AND channel = %s AND provider_id = %s
                  AND source_item_id = %s AND source_sku_id = %s AND payload_hash = %s
                """,
                (
                    str(record.get("account_id") or ""),
                    str(record.get("channel") or ""),
                    str(record.get("provider_id") or ""),
                    str(record.get("source_item_id") or ""),
                    str(record.get("source_sku_id") or ""),
                    str(record.get("payload_hash") or ""),
                ),
            )
            row = cursor.fetchone()
        return self._row_to_record({"payload": row[0]}) if row else None

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM offer_snapshots WHERE record_id = %s", (record_id,)
            )
            row = cursor.fetchone()
        return self._row_to_record({"payload": row[0]}) if row else None

    def list(self, *, provider_id: str = "", source_item_id: str = "", account_id=None):
        query = "SELECT payload FROM offer_snapshots WHERE 1=1"
        params: List[Any] = []
        if provider_id:
            query += " AND provider_id = %s"
            params.append(provider_id)
        if source_item_id:
            query += " AND source_item_id = %s"
            params.append(source_item_id)
        if account_id is not None:
            query += " AND account_id = %s"
            params.append(account_id)
        query += " ORDER BY observed_at DESC"
        with self._uow._conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
        return [dict(_loads(row[0])) for row in rows]

    def latest(self, *, provider_id: str, source_item_id: str, source_sku_id: str = ""):
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload FROM offer_snapshots
                WHERE provider_id = %s AND source_item_id = %s
                ORDER BY observed_at DESC LIMIT 1
                """,
                (provider_id, source_item_id),
            )
            row = cursor.fetchone()
        return self._row_to_record({"payload": row[0]}) if row else None


class PostgresOpportunityRepository:
    """Contract-mirror of OpportunityRepository over PG."""

    def __init__(self, uow) -> None:
        self._uow = uow

    def create(self, opportunity: Mapping[str, Any]) -> Dict[str, Any]:
        record = dict(opportunity)
        record.setdefault("record_id", "opp_" + uuid.uuid4().hex[:20])
        record.setdefault("revision", 1)
        natural = c.opportunity_natural_key(record)
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO opportunities (record_id, canonical_sku, source, account_id,
                                           status, revision, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record["record_id"], natural[0], natural[1],
                    str(record.get("account_id") or ""),
                    str(record.get("status") or "discovered"),
                    int(record.get("revision") or 1),
                    json.dumps(record, ensure_ascii=False),
                ),
            )
        return record

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM opportunities WHERE record_id = %s", (record_id,)
            )
            row = cursor.fetchone()
        return dict(_loads(row[0])) if row else None

    def find_by_natural_key(self, canonical_sku: str, source: str):
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM opportunities WHERE canonical_sku = %s AND source = %s",
                (canonical_sku, source),
            )
            row = cursor.fetchone()
        return dict(_loads(row[0])) if row else None

    def transition(
        self,
        record_id: str,
        *,
        to_status: str,
        reason: str,
        evidence_refs=None,
        actor: str = "system",
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE opportunities
                SET status = %s, revision = revision + 1,
                    updated_at = now(),
                    payload = jsonb_set(
                        jsonb_set(payload, '{status}', to_jsonb(%s::text)),
                        '{revision}', to_jsonb((revision + 1)::int)
                    )
                WHERE record_id = %s AND (%s::int IS NULL OR revision = %s::int)
                RETURNING payload
                """,
                (to_status, to_status, record_id, expected_revision, expected_revision),
            )
            row = cursor.fetchone()
        if row is None:
            if self.get(record_id) is None:
                raise KeyError(f"opportunity not found: {record_id}")
            raise c.ContractError(
                f"revision conflict for {record_id}: expected {expected_revision}"
            )
        return dict(_loads(row[0]))

    def attach(self, record_id: str, *, field: str, value: Any,
               expected_revision: Optional[int] = None) -> Dict[str, Any]:
        with self._uow._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE opportunities
                SET revision = revision + 1, updated_at = now(),
                    payload = payload || jsonb_build_object(%s, to_jsonb(%s::jsonb))
                WHERE record_id = %s
                RETURNING payload
                """,
                (field, json.dumps(value, ensure_ascii=False), record_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"opportunity not found: {record_id}")
        return dict(_loads(row[0]))

    def list(self, *, status: str = "") -> List[Dict[str, Any]]:
        query = "SELECT payload FROM opportunities"
        params: Tuple = ()
        if status:
            query += " WHERE status = %s"
            params = (status,)
        query += " ORDER BY created_at"
        with self._uow._conn.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return [dict(_loads(row[0])) for row in rows]
