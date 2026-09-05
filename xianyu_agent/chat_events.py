"""Append-only chat event fact stream.

Every customer-facing message fact -- an inbound buyer message or an
outbound sent reply -- is recorded once as an immutable, hash-chained
ChatEvent next to the intake ledger. The stream is the replayable history
for chat read models; it never replaces the envelope dedup contract and it
never mutates recorded entries.

Contract:
- Records are strictly sequenced and hash-chained (``previous_event_hash``).
- Inbound events are idempotent by envelope id: re-appending the same
  envelope returns the stored event; the same id with different content is
  a hard error.
- Outbound events are idempotent by execution job id: one send attempt maps
  to at most one event, and a repeated append of the same attempt is a
  no-op.
- Stored entries are never edited or deleted; corrections require a new
  event.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

EVENT_SCHEMA_VERSION = 1
OUTBOUND_SEND_ACTIONS = {"send_message", "sensitive_reply"}

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def canonical_event_sha256(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ChatEventStore:
    """Append-only, hash-chained chat event store backed by JSON."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = Path(storage_path or Path("data") / "chat_events.json")
        self._thread_lock = _path_lock(self.storage_path)
        self._events: List[Dict[str, Any]] = []
        self._load()

    def configure(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
        self._thread_lock = _path_lock(self.storage_path)
        self._events = []
        self._load()

    # ---- append ----

    def append_inbound(
        self,
        envelope: Mapping[str, Any],
        *,
        intake_id: str = "",
        occurred_at: Optional[float] = None,
        recorded_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record one inbound message fact; idempotent by envelope id."""
        envelope_id = str(envelope.get("envelope_id") or "").strip()
        if not envelope_id:
            raise ValueError("chat event requires a non-empty envelope_id")
        with self._locked_state():
            existing = self._find_by_envelope(envelope_id)
            if existing is not None:
                conflicting = (
                    str(existing.get("dedupe_key") or "")
                    != str(envelope.get("dedupe_key") or existing.get("dedupe_key") or "")
                    or str(existing.get("message_text") or "")
                    != str(envelope.get("message_text") or "")
                )
                if conflicting:
                    raise ValueError(
                        f"chat event {envelope_id} already recorded with different content"
                    )
                return {"status": "already_recorded", "event": _copy(existing)}
            record = self._base_record(
                direction="inbound",
                envelope=envelope,
                occurred_at=occurred_at,
                recorded_at=recorded_at,
            )
            record["intake_id"] = str(intake_id or "")
            return self._append_unlocked(record)

    def append_outbound(
        self,
        *,
        execution_job_id: str,
        cookie_id: str = "",
        chat_id: str = "",
        buyer_id: str = "",
        item_id: str = "",
        message_text: str = "",
        reply_proposal_id: str = "",
        source_event_key: str = "",
        source_message_id: str = "",
        send_outcome: str = "sent",
        occurred_at: Optional[float] = None,
        recorded_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record one outbound send fact; idempotent by execution job id."""
        clean_job_id = str(execution_job_id or "").strip()
        if not clean_job_id:
            raise ValueError("outbound chat event requires execution_job_id")
        outcome = str(send_outcome or "sent").strip() or "sent"
        if outcome not in {"sent", "verification_required"}:
            raise ValueError("send_outcome must be sent or verification_required")
        envelope_id = "env-out-" + hashlib.sha256(
            f"outbound:{clean_job_id}".encode("utf-8")
        ).hexdigest()[:16]
        dedupe_key = f"outbound_execution_job:{clean_job_id}"
        with self._locked_state():
            existing = self._find_by_envelope(envelope_id)
            if existing is not None:
                if str(existing.get("send_outcome") or "") != outcome:
                    raise ValueError(
                        f"outbound chat event for job {clean_job_id} already recorded "
                        "with a different outcome"
                    )
                return {"status": "already_recorded", "event": _copy(existing)}
            envelope = {
                "envelope_id": envelope_id,
                "dedupe_key": dedupe_key,
                "source": "reply_outbox",
                "source_message_id": str(source_message_id or ""),
                "cookie_id": str(cookie_id or ""),
                "buyer_id": str(buyer_id or ""),
                "buyer_name": "",
                "chat_id": str(chat_id or ""),
                "item_id": str(item_id or ""),
                "message_text": str(message_text or ""),
                "message_type": "text",
            }
            record = self._base_record(
                direction="outbound",
                envelope=envelope,
                occurred_at=occurred_at,
                recorded_at=recorded_at,
            )
            record["reply_proposal_id"] = str(reply_proposal_id or "")
            record["execution_job_id"] = clean_job_id
            record["send_outcome"] = outcome
            return self._append_unlocked(record)

    # ---- reads ----

    def list_events(
        self,
        *,
        cookie_id: str = "",
        chat_id: str = "",
        item_id: str = "",
        direction: str = "",
        limit: int = 50,
        before_sequence: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return events in sequence order (pagination walks backwards)."""
        with self._thread_lock:
            selected = []
            for event in self._events:
                if cookie_id and str(event.get("cookie_id") or "") != str(cookie_id):
                    continue
                if chat_id and str(event.get("chat_id") or "") != str(chat_id):
                    continue
                if item_id and str(event.get("item_id") or "") != str(item_id):
                    continue
                if direction and str(event.get("direction") or "") != str(direction):
                    continue
                if before_sequence is not None and int(event.get("sequence") or 0) >= int(
                    before_sequence
                ):
                    continue
                selected.append(event)
            selected = selected[-max(1, int(limit)) :]
            return [_copy(event) for event in selected]

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._thread_lock:
            for event in self._events:
                if str(event.get("event_id") or "") == str(event_id):
                    return _copy(event)
        return None

    def replay(self, from_sequence: int = 1) -> List[Dict[str, Any]]:
        """Return the ordered, verified suffix of the stream for read-model rebuilds."""
        events = self.list_events(limit=len(self._events) or 1)
        verification = self.verify_chain()
        if not verification.get("ok"):
            raise ValueError(
                "chat event stream failed integrity verification: "
                + "; ".join(str(error) for error in verification.get("errors", []))
            )
        return [event for event in events if int(event.get("sequence") or 0) >= int(from_sequence)]

    def verify_chain(self) -> Dict[str, Any]:
        errors: List[str] = []
        previous_hash = ""
        with self._thread_lock:
            for index, event in enumerate(self._events, start=1):
                if int(event.get("sequence") or 0) != index:
                    errors.append(f"sequence break at position {index}")
                if str(event.get("previous_event_hash") or "") != previous_hash:
                    errors.append(f"chain break at position {index}")
                recomputed = _event_hash(event)
                if str(event.get("event_hash") or "") != recomputed:
                    errors.append(f"hash mismatch at position {index}")
                    break
                previous_hash = str(event.get("event_hash") or "")
        return {
            "ok": not errors,
            "errors": errors[:20],
            "event_count": len(self._events),
            "last_event_hash": previous_hash,
        }

    def metrics(self) -> Dict[str, Any]:
        with self._thread_lock:
            by_direction: Dict[str, int] = {}
            by_account: Dict[str, int] = {}
            for event in self._events:
                direction = str(event.get("direction") or "unknown")
                by_direction[direction] = by_direction.get(direction, 0) + 1
                account = str(event.get("cookie_id") or "unknown")
                by_account[account] = by_account.get(account, 0) + 1
            return {
                "schema_version": EVENT_SCHEMA_VERSION,
                "event_count": len(self._events),
                "last_sequence": int(self._events[-1].get("sequence") or 0) if self._events else 0,
                "by_direction": by_direction,
                "by_account": by_account,
                "chain": self.verify_chain(),
            }

    # ---- internals ----

    def _base_record(
        self,
        *,
        direction: str,
        envelope: Mapping[str, Any],
        occurred_at: Optional[float],
        recorded_at: Optional[float],
    ) -> Dict[str, Any]:
        now = time.time() if recorded_at is None else float(recorded_at)
        happened = float(occurred_at) if occurred_at is not None else _as_float(
            envelope.get("occurred_at")
        )
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": "ce_" + uuid.uuid4().hex[:12],
            "sequence": 0,
            "direction": str(direction),
            "envelope_id": str(envelope.get("envelope_id") or ""),
            "dedupe_key": str(envelope.get("dedupe_key") or ""),
            "source": str(envelope.get("source") or ""),
            "source_message_id": str(envelope.get("source_message_id") or ""),
            "cookie_id": str(envelope.get("cookie_id") or ""),
            "buyer_id": str(envelope.get("buyer_id") or ""),
            "buyer_name": str(envelope.get("buyer_name") or ""),
            "chat_id": str(envelope.get("chat_id") or ""),
            "item_id": str(envelope.get("item_id") or ""),
            "message_type": str(envelope.get("message_type") or "text"),
            "message_text": str(envelope.get("message_text") or ""),
            "occurred_at": happened,
            "recorded_at": now,
            "previous_event_hash": "",
            "event_hash": "",
        }

    def _append_unlocked(self, record: Dict[str, Any]) -> Dict[str, Any]:
        previous_hash = str(self._events[-1].get("event_hash") or "") if self._events else ""
        record["sequence"] = len(self._events) + 1
        record["previous_event_hash"] = previous_hash
        record["event_hash"] = _event_hash(record)
        self._events.append(record)
        self._save_unlocked()
        return {"status": "appended", "event": _copy(record)}

    def _find_by_envelope(self, envelope_id: str) -> Optional[Dict[str, Any]]:
        for event in self._events:
            if str(event.get("envelope_id") or "") == str(envelope_id):
                return event
        return None

    @contextmanager
    def _locked_state(self):
        with self._thread_lock:
            with self._exclusive_file_lock():
                self._reload_unlocked()
                try:
                    yield
                finally:
                    pass

    @contextmanager
    def _exclusive_file_lock(self):
        lock_path = self.storage_path.with_name(f"{self.storage_path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _load(self) -> None:
        self._reload_unlocked()

    def _reload_unlocked(self) -> None:
        self._events = []
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("event_id"):
                self._events.append(item)

    def _save_unlocked(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.storage_path.with_name(
            f".{self.storage_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(self._events, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp_path.replace(self.storage_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def _event_hash(record: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "event_hash"}
    return canonical_event_sha256(payload)


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


chat_event_store = ChatEventStore()
