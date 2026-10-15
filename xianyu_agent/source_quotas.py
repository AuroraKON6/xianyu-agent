"""Persistent per-source request quota ledger (G2A/A46).

Rules (plan G2A-8 / matrix A46):
- Quotas count REAL request ATTEMPTS, retries included: one 429-then-retry
  page consumes two units, never one.
- The ledger is persistent across process restarts (per-source, per-day
  window) -- restarting the app never resets the counter.
- An UNKNOWN limit (no contract) forbids unattended online scanning: the
  background dispatcher must refuse to start fetch loops against such a
  source; an explicit manual operator action may still proceed and is
  recorded as such.
- Quota configuration comes from real contracts; nothing here invents a
  default limit.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .commerce.repositories import LockedJsonList


def _day(now: datetime) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


class QuotaExhausted(RuntimeError):
    pass


class UnattendedQuotaUnknown(RuntimeError):
    pass


class SourceQuotaLedger:
    """Day-windowed attempt counters with optional contract limits."""

    def __init__(self, data_dir: Path, *, limits: Optional[Mapping[str, int]] = None) -> None:
        self._store = LockedJsonList(Path(data_dir) / "commerce" / "source_quotas.json")
        self._limits = dict(limits or {})
        self._admission_lock = threading.RLock()  # Codex D2: check+consume atomic

    def _contract_limit(self, source: str, items: Optional[Any] = None) -> Optional[int]:
        """Effective contract limit: in-memory contracts win; otherwise the
        limit persisted alongside the counters keeps enforcement alive
        across process restarts (Codex D7)."""
        limit = self._limits.get(source)
        if limit is not None:
            return int(limit)
        rows = items._items if items is not None else self._store.find(lambda _r: True)
        for row in rows:
            if str(row.get("source")) == source and row.get("limit") is not None:
                return int(row.get("limit"))
        return None

    def is_managed(self, source: str) -> bool:
        """True when a real contract limit exists for this source.

        Unmanaged sources (no contract anywhere) are not metered here; the
        background dispatcher refuses unattended loops against them
        separately (UnattendedQuotaUnknown at loop start).
        """
        return self._contract_limit(source) is not None

    def status(self, source: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        day = _day(now or datetime.now(timezone.utc))
        key = f"{source}:{day}"
        found = self._store.find(lambda r: str(r.get("window_key")) == key)
        used = int(found[0].get("attempts")) if found else 0
        limit = self._contract_limit(source, found[0] if found else None)
        manual_only = [
            r for r in self._store.find(lambda r: str(r.get("window_key")) == key)
            if r.get("last_mode") == "manual"
        ]
        return {
            "source": source, "window": day, "attempts": used,
            "limit": limit, "limit_known": limit is not None,
            "remaining": (limit - used) if limit is not None else None,
            "exhausted": limit is not None and used >= limit,
            "note": "limit from contract" if limit is not None
            else "no contract limit recorded; unattended scanning forbidden",
        }

    def record_attempt(
        self, source: str, *, manual: bool = False, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Count ONE request attempt (caller calls per try, retries included)."""
        day = _day(now or datetime.now(timezone.utc))
        key = f"{source}:{day}"

        def mutate(items: LockedJsonList) -> Dict[str, Any]:
            limit = self._contract_limit(source, items)
            for row in items._items:
                if str(row.get("window_key")) == key:
                    row["attempts"] = int(row.get("attempts") or 0) + 1
                    row["last_mode"] = "manual" if manual else "unattended"
                    row["last_at"] = (now or datetime.now(timezone.utc)).isoformat()
                    return dict(row)
            row = {
                "record_type": "source_quota_window", "window_key": key,
                "source": source, "window": day, "attempts": 1,
                "limit": limit, "last_mode": "manual" if manual else "unattended",
                "last_at": (now or datetime.now(timezone.utc)).isoformat(),
            }
            items._items.append(row)
            return dict(row)

        with self._store as items:
            return mutate(items)

    def check_and_consume(
        self, source: str, *, manual: bool = False, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Gate + count atomically inside one store transaction.

        Reads and writes happen under the store's own lock so concurrent
        callers cannot interleave between the quota check and the increment.
        Raises UnattendedQuotaUnknown / QuotaExhausted per the contract.
        """
        day = _day(now or datetime.now(timezone.utc))
        key = f"{source}:{day}"

        def mutate(items: LockedJsonList) -> Dict[str, Any]:
            limit = self._contract_limit(source, items)
            row = None
            for candidate in items._items:
                if str(candidate.get("window_key")) == key:
                    row = candidate
                    break
            used = int(row.get("attempts") or 0) if row else 0
            if limit is None and not manual:
                raise UnattendedQuotaUnknown(
                    f"source {source}: no contract limit recorded; unattended scanning forbidden"
                )
            if limit is not None and used >= limit:
                raise QuotaExhausted(
                    f"source {source}: {used}/{limit} attempts used in {day}"
                )
            if row is None:
                row = {
                    "record_type": "source_quota_window", "window_key": key,
                    "source": source, "window": day, "attempts": 0,
                    "limit": limit, "last_mode": "manual" if manual else "unattended",
                    "last_at": (now or datetime.now(timezone.utc)).isoformat(),
                }
                items._items.append(row)
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["last_mode"] = "manual" if manual else "unattended"
            row["last_at"] = (now or datetime.now(timezone.utc)).isoformat()
            return dict(row)

        with self._store as items:
            result = mutate(items)
        limit = result.get("limit")
        return {
            "source": source, "window": day,
            "attempts": result["attempts"], "limit": limit,
            "limit_known": limit is not None,
            "remaining": (limit - result["attempts"]) if limit is not None else None,
            "exhausted": limit is not None and result["attempts"] >= limit,
        }
