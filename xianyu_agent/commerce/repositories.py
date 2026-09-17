"""Repository layer for the WP01 commerce contracts.

Protocols first: business services depend on the abstract repositories,
never on the storage mechanics. The alpha implementation is JSON-backed
(one append-only file per aggregate under ``data_dir/commerce/``) which
matches the existing store conventions; the production target is a
transactional database (see docs/adr/0001). New modules must not scatter
raw reads/writes of the legacy JSON/SQLite files; they go through these
repositories or the existing stores.

Semantics:
- Offer snapshots are immutable and idempotent by natural key
  (provider + item + sku + observed_at + payload_hash). Re-adding the
  same snapshot returns the stored record; history is never overwritten.
- Product matches append; the latest record per (left, right,
  rule_version) is the current judgment, older records stay for audit.
- Opportunities are uniquely keyed by (canonical_sku, source) and every
  status change must go through ``transition`` with a reason and an
  expected revision; concurrent writers get ``RevisionConflict``.
- Decision records are append-only; they are never edited after being
  written.
"""
from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contracts import (
    apply_opportunity_transition,
    match_pair_key,
    offer_natural_key,
    opportunity_natural_key,
)


class RevisionConflict(RuntimeError):
    """Raised when a writer's expected revision no longer matches."""


class DuplicateRecord(RuntimeError):
    """Raised when a uniqueness constraint is violated."""


class _LockedJsonList:
    """Thread-safe, process-local append-mostly JSON list store.

    Writes are atomic (temp file + os.replace). This is the same
    durability level as the existing JSON stores: it survives graceful
    process restarts and crash-safe file replacement; it is not a
    multi-process transactional database.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._items = []
            return
        text = self.path.read_text(encoding="utf-8")
        self._items = json.loads(text) if text.strip() else []

    def _persist(self) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(self.path))

    # context manager for read-modify-write sections
    def __enter__(self) -> "_LockedJsonList":
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._persist()
        self._lock.release()

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def find(self, predicate) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items if predicate(item)]


class OfferSnapshotRepository(ABC):
    @abstractmethod
    def add(self, snapshot: Mapping[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Store one immutable snapshot; returns (record, created)."""

    @abstractmethod
    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def list(
        self,
        *,
        provider_id: str = "",
        source_item_id: str = "",
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def latest(self, *, provider_id: str, source_item_id: str, source_sku_id: str = "") -> Optional[Dict[str, Any]]:
        ...


class ProductMatchRepository(ABC):
    @abstractmethod
    def add(self, match: Mapping[str, Any]) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def latest_for_pair(self, left_snapshot_id: str, right_snapshot_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def list(self, *, grade: str = "") -> List[Dict[str, Any]]:
        ...


class OpportunityRepository(ABC):
    @abstractmethod
    def create(self, opportunity: Mapping[str, Any]) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def find_by_natural_key(self, canonical_sku: str, source: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def transition(
        self,
        record_id: str,
        *,
        to_status: str,
        reason: str,
        evidence_refs: Optional[Sequence[str]] = None,
        actor: str = "system",
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        ...

    @abstractmethod
    def attach(
        self,
        record_id: str,
        *,
        field: str,
        value: Any,
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        ...

    @abstractmethod
    def list(self, *, status: str = "") -> List[Dict[str, Any]]:
        ...


class DecisionRecordRepository(ABC):
    @abstractmethod
    def append(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def list(self, *, opportunity_id: str = "", actor_role: str = "") -> List[Dict[str, Any]]:
        ...


class JsonOfferSnapshotRepository(OfferSnapshotRepository):
    def __init__(self, store: _LockedJsonList) -> None:
        self._store = store

    def add(self, snapshot: Mapping[str, Any]) -> Tuple[Dict[str, Any], bool]:
        record = dict(snapshot)
        key = offer_natural_key(record)
        with self._store as items:
            for existing in items._items:
                if offer_natural_key(existing) == key:
                    return dict(existing), False
            items._items.append(record)
        return record, True

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda r: str(r.get("record_id")) == record_id)
        return found[0] if found else None

    def list(
        self,
        *,
        provider_id: str = "",
        source_item_id: str = "",
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        def predicate(record: Mapping[str, Any]) -> bool:
            if provider_id and str(record.get("provider_id")) != provider_id:
                return False
            if source_item_id and str(record.get("source_item_id")) != source_item_id:
                return False
            if account_id is not None and str(record.get("account_id") or "") != account_id:
                return False
            return True

        return self._store.find(predicate)

    def latest(self, *, provider_id: str, source_item_id: str, source_sku_id: str = "") -> Optional[Dict[str, Any]]:
        candidates = self.list(provider_id=provider_id, source_item_id=source_item_id)
        if source_sku_id:
            candidates = [c for c in candidates if str(c.get("source_sku_id") or "") == source_sku_id]
        if not candidates:
            return None
        return max(candidates, key=lambda r: str(r.get("observed_at") or ""))


class JsonProductMatchRepository(ProductMatchRepository):
    def __init__(self, store: _LockedJsonList) -> None:
        self._store = store

    def add(self, match: Mapping[str, Any]) -> Dict[str, Any]:
        record = dict(match)
        pair = match_pair_key(record)
        with self._store as items:
            # exact duplicate of the current judgment is idempotent
            for existing in items._items:
                if match_pair_key(existing) == pair and existing.get("grade") == record.get("grade"):
                    return dict(existing)
            items._items.append(record)
        return record

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda r: str(r.get("record_id")) == record_id)
        return found[0] if found else None

    def latest_for_pair(self, left_snapshot_id: str, right_snapshot_id: str) -> Optional[Dict[str, Any]]:
        candidates = self._store.find(
            lambda r: (str(r.get("left_snapshot_id")), str(r.get("right_snapshot_id"))) == (left_snapshot_id, right_snapshot_id)
        )
        if not candidates:
            return None
        return max(candidates, key=lambda r: str(r.get("created_at") or ""))

    def list(self, *, grade: str = "") -> List[Dict[str, Any]]:
        def predicate(record: Mapping[str, Any]) -> bool:
            return (not grade) or str(record.get("grade")) == grade

        return self._store.find(predicate)


class JsonOpportunityRepository(OpportunityRepository):
    def __init__(self, store: _LockedJsonList) -> None:
        self._store = store

    def _get_locked(self, items: _LockedJsonList, record_id: str) -> Dict[str, Any]:
        for existing in items._items:
            if str(existing.get("record_id")) == record_id:
                return existing
        raise KeyError(f"opportunity not found: {record_id}")

    def create(self, opportunity: Mapping[str, Any]) -> Dict[str, Any]:
        record = dict(opportunity)
        key = opportunity_natural_key(record)
        with self._store as items:
            for existing in items._items:
                if opportunity_natural_key(existing) == key:
                    raise DuplicateRecord(f"opportunity already exists for {key}")
            items._items.append(record)
        return record

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda r: str(r.get("record_id")) == record_id)
        return found[0] if found else None

    def find_by_natural_key(self, canonical_sku: str, source: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda r: opportunity_natural_key(r) == (canonical_sku, source))
        return found[0] if found else None

    def transition(
        self,
        record_id: str,
        *,
        to_status: str,
        reason: str,
        evidence_refs: Optional[Sequence[str]] = None,
        actor: str = "system",
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._store as items:
            record = self._get_locked(items, record_id)
            if expected_revision is not None and int(record.get("revision") or 1) != expected_revision:
                raise RevisionConflict(
                    f"opportunity {record_id} at revision {record.get('revision')}, expected {expected_revision}"
                )
            apply_opportunity_transition(
                record,
                to_status=to_status,
                reason=reason,
                evidence_refs=evidence_refs,
                actor=actor,
            )
            return dict(record)

    def attach(
        self,
        record_id: str,
        *,
        field: str,
        value: Any,
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        if field in ("status", "revision", "record_id", "history"):
            raise ValueError(f"field {field!r} is managed by transitions, not attach")
        with self._store as items:
            record = self._get_locked(items, record_id)
            if expected_revision is not None and int(record.get("revision") or 1) != expected_revision:
                raise RevisionConflict(
                    f"opportunity {record_id} at revision {record.get('revision')}, expected {expected_revision}"
                )
            if field == "decision_ids":
                decision_ids = list(record.get("decision_ids") or [])
                if value not in decision_ids:
                    decision_ids.append(value)
                record["decision_ids"] = decision_ids
            else:
                record[field] = value
            record["revision"] = int(record.get("revision") or 1) + 1
            return dict(record)

    def list(self, *, status: str = "") -> List[Dict[str, Any]]:
        def predicate(record: Mapping[str, Any]) -> bool:
            return (not status) or str(record.get("status")) == status

        return self._store.find(predicate)


class JsonDecisionRecordRepository(DecisionRecordRepository):
    def __init__(self, store: _LockedJsonList) -> None:
        self._store = store

    def append(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        stored = dict(record)
        with self._store as items:
            for existing in items._items:
                if str(existing.get("record_id")) == str(stored.get("record_id")):
                    raise DuplicateRecord(f"decision record already exists: {stored.get('record_id')}")
            items._items.append(stored)
        return stored

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda r: str(r.get("record_id")) == record_id)
        return found[0] if found else None

    def list(self, *, opportunity_id: str = "", actor_role: str = "") -> List[Dict[str, Any]]:
        def predicate(record: Mapping[str, Any]) -> bool:
            if opportunity_id and str(record.get("opportunity_id")) != opportunity_id:
                return False
            if actor_role and str(record.get("actor_role")) != actor_role:
                return False
            return True

        return self._store.find(predicate)


class JsonCommerceRepositories:
    """Composition root for the JSON-backed alpha repositories.

    Files live under ``<data_dir>/commerce/``: offers.json, matches.json,
    opportunities.json, decisions.json.
    """

    def __init__(self, data_dir: Path) -> None:
        base = Path(data_dir) / "commerce"
        self.offers: OfferSnapshotRepository = JsonOfferSnapshotRepository(_LockedJsonList(base / "offers.json"))
        self.matches: ProductMatchRepository = JsonProductMatchRepository(_LockedJsonList(base / "matches.json"))
        self.opportunities: OpportunityRepository = JsonOpportunityRepository(_LockedJsonList(base / "opportunities.json"))
        self.decisions: DecisionRecordRepository = JsonDecisionRecordRepository(_LockedJsonList(base / "decisions.json"))
