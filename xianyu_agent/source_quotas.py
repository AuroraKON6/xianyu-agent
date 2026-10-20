"""Persistent per-source request quota ledger (G2A/A46, taskbook R08).

Rules:
- Quotas count REAL request ATTEMPTS, retries included: one 429-then-retry
  page consumes two units, never one.
- The ledger is persistent across process restarts (per-source, per-day
  window) and every read-modify-write runs under an OS-level file lock,
  so two processes racing for the last admission cannot over-admit.
- The effective contract limit is persisted with every mutation: a
  tightened (or revoked) limit survives restarts and never resurrects a
  superseded value.
- Caller-side admissions are single-use precharge tickets:
  ``check_and_consume`` issues one; a fetch session's FIRST request may
  proceed only by atomically claiming that ticket (``claim_precharge``).
  A fresh session without a verified ticket meters itself — a spent
  precharge can never be reused by a later session.
- An UNKNOWN limit (no contract) forbids unattended fetching: sessions
  must refuse before any transport attempt; an explicit manual operator
  action may still proceed and is recorded as such.
- ``revoke`` persists limit 0: the source is fully closed afterwards.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


def _day(now: datetime) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


class QuotaExhausted(RuntimeError):
    pass


class UnattendedQuotaUnknown(RuntimeError):
    pass


def _os_lock(handle) -> None:
    handle.seek(0)
    try:
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    except ImportError:  # POSIX
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _os_unlock(handle) -> None:
    handle.seek(0)
    try:
        import msvcrt

        handle.flush()
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except ImportError:  # POSIX
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SourceQuotaLedger:
    """Day-windowed attempt counters with contract limits.

    Storage is a plain JSON list at ``<data_dir>/commerce/source_quotas.json``
    (same format the previous in-process store used, so existing data
    reads back unchanged). Every transaction re-reads the file under an
    OS-level lock on a sidecar ``.lock`` file and writes back atomically
    (temp file + os.replace), which is what makes the ledger safe across
    processes, not just threads.
    """

    def __init__(self, data_dir: Path, *, limits: Optional[Mapping[str, int]] = None) -> None:
        self._path = Path(data_dir) / "commerce" / "source_quotas.json"
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._limits = dict(limits or {})
        self._thread_lock = threading.RLock()

    # ---- storage -----------------------------------------------------------

    def _read_rows(self) -> List[Dict[str, Any]]:
        try:
            text = self._path.read_text(encoding="utf-8")
            rows = json.loads(text) if text.strip() else []
            return rows if isinstance(rows, list) else []
        except OSError:
            return []

    def _write_rows(self, rows: List[Dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(tmp_path), str(self._path))

    @contextmanager
    def _transaction(self):
        """Thread + OS locked read-modify-write over the persisted rows."""
        with self._thread_lock:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(self._lock_path), "a+b") as handle:
                _os_lock(handle)
                try:
                    rows = self._read_rows()
                    yield rows
                    self._write_rows(rows)
                finally:
                    _os_unlock(handle)

    def _read(self) -> List[Dict[str, Any]]:
        with self._thread_lock:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(self._lock_path), "a+b") as handle:
                _os_lock(handle)
                try:
                    return self._read_rows()
                finally:
                    _os_unlock(handle)

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _find(rows: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
        for row in rows:
            if str(row.get("window_key")) == key:
                return row
        return None

    def _effective_limit(self, source: str, rows: List[Dict[str, Any]]) -> Optional[int]:
        """In-memory contract wins; otherwise the limit persisted with the
        counters keeps enforcement (and tightening/revocation) alive across
        restarts (R08/Q4)."""
        if source in self._limits:
            return int(self._limits[source])
        for row in rows:
            if str(row.get("source")) == source and row.get("limit") is not None:
                return int(row.get("limit"))
        return None

    def is_managed(self, source: str) -> bool:
        """True when a real contract limit exists for this source."""
        return self._effective_limit(source, self._read()) is not None

    # ---- queries ---------------------------------------------------------------

    def status(self, source: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        day = _day(now or datetime.now(timezone.utc))
        key = f"{source}:{day}"
        rows = self._read()
        row = self._find(rows, key)
        used = int(row.get("attempts") or 0) if row else 0
        limit = self._effective_limit(source, rows)
        return {
            "source": source, "window": day, "attempts": used,
            "limit": limit, "limit_known": limit is not None,
            "remaining": (limit - used) if limit is not None else None,
            "exhausted": limit is not None and used >= limit,
            "note": "limit from contract" if limit is not None
            else "no contract limit recorded; unattended scanning forbidden",
        }

    # ---- mutations ---------------------------------------------------------------

    def _gate_and_consume(
        self,
        source: str,
        *,
        manual: bool,
        now: Optional[datetime],
        issue_ticket: bool,
    ) -> Dict[str, Any]:
        moment = now or datetime.now(timezone.utc)
        day = _day(moment)
        key = f"{source}:{day}"

        def mutate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            limit = self._effective_limit(source, rows)
            row = self._find(rows, key)
            used = int(row.get("attempts") or 0) if row else 0
            if limit is None and not manual:
                raise UnattendedQuotaUnknown(
                    f"source {source}: no contract limit recorded; unattended scanning forbidden"
                )
            if limit is not None and used >= limit:
                raise QuotaExhausted(f"source {source}: {used}/{limit} attempts used in {day}")
            if row is None:
                row = {
                    "record_type": "source_quota_window", "window_key": key,
                    "source": source, "window": day, "attempts": 0,
                }
                rows.append(row)
            row["attempts"] = used + 1
            # R08/Q4: persist the effective limit with every mutation so a
            # tightened or revoked contract survives restarts
            if limit is not None:
                row["limit"] = limit
            elif "limit" in row:
                del row["limit"]
            row["last_mode"] = "manual" if manual else "unattended"
            row["last_at"] = moment.isoformat()
            if issue_ticket:
                row["precharge"] = {
                    "id": uuid.uuid4().hex,
                    "issued_at_attempts": row["attempts"],
                    "issued_at": moment.isoformat(),
                }
            else:
                row.pop("precharge", None)
            return dict(row)

        with self._transaction() as rows:
            result = mutate(rows)
        limit = result.get("limit")
        return {
            "source": source, "window": day,
            "attempts": result["attempts"], "limit": limit,
            "limit_known": limit is not None,
            "remaining": (limit - result["attempts"]) if limit is not None else None,
            "exhausted": limit is not None and result["attempts"] >= limit,
        }

    def check_and_consume(
        self, source: str, *, manual: bool = False, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Gate + count atomically; issues a single-use precharge ticket.

        The ticket authorizes exactly one later first-request (a fetch
        session claims it via ``claim_precharge``); retries and further
        pages always meter themselves.
        """
        return self._gate_and_consume(source, manual=manual, now=now, issue_ticket=True)

    def consume_attempt(
        self, source: str, *, manual: bool = False, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Meter one attempt WITHOUT issuing a new precharge ticket.

        Used by fetch sessions for retries and further pages, so a session
        can never re-authorize itself.
        """
        return self._gate_and_consume(source, manual=manual, now=now, issue_ticket=False)

    def claim_precharge(
        self, source: str, *, manual: bool = False, now: Optional[datetime] = None
    ) -> bool:
        """Atomically claim the outstanding precharge ticket, if any.

        Returns True when an unspent, still-current ticket existed (the
        attempt is already counted — do not meter again). A stale ticket
        (any consumption happened after it was issued) is void.
        """
        moment = now or datetime.now(timezone.utc)
        day = _day(moment)
        key = f"{source}:{day}"

        def mutate(rows: List[Dict[str, Any]]) -> bool:
            row = self._find(rows, key)
            ticket = (row or {}).get("precharge") or {}
            if not ticket:
                return False
            if int(ticket.get("issued_at_attempts") or -1) != int((row or {}).get("attempts") or -1):
                return False  # stale: something consumed after the ticket
            del row["precharge"]
            row["last_mode"] = "manual" if manual else "unattended"
            row["last_at"] = moment.isoformat()
            return True

        with self._transaction() as rows:
            return mutate(rows)

    def record_attempt(
        self, source: str, *, manual: bool = False, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Count ONE attempt unconditionally (no gate, no ticket)."""
        moment = now or datetime.now(timezone.utc)
        day = _day(moment)
        key = f"{source}:{day}"

        def mutate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            limit = self._effective_limit(source, rows)
            row = self._find(rows, key)
            if row is None:
                row = {
                    "record_type": "source_quota_window", "window_key": key,
                    "source": source, "window": day, "attempts": 0,
                }
                rows.append(row)
            row["attempts"] = int(row.get("attempts") or 0) + 1
            if limit is not None:
                row["limit"] = limit
            row["last_mode"] = "manual" if manual else "unattended"
            row["last_at"] = moment.isoformat()
            return dict(row)

        with self._transaction() as rows:
            return mutate(rows)

    def revoke(self, source: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Close the source entirely: persist limit 0 (wins over restart)."""
        moment = now or datetime.now(timezone.utc)
        day = _day(moment)
        key = f"{source}:{day}"
        self._limits[source] = 0

        def mutate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            row = self._find(rows, key)
            if row is None:
                row = {
                    "record_type": "source_quota_window", "window_key": key,
                    "source": source, "window": day, "attempts": 0,
                }
                rows.append(row)
            row["limit"] = 0
            row["revoked_at"] = moment.isoformat()
            row.pop("precharge", None)
            return dict(row)

        with self._transaction() as rows:
            mutate(rows)
        return self.status(source, now=moment)
