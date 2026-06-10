"""Bridge events — idempotent event log for Xianyu ↔ Ecommerce sync.

Safety boundaries:
- Read/write only local JSON file. No network calls.
- Idempotency key prevents duplicate events.
- No secrets, buyer IDs, chat IDs, cookies, or tokens in event records.
- Bridge intake creates WorkOrder records only; no direct execution.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class BridgeEventLedger:
    """Persistent, idempotent event log for bridge sync."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self._path = storage_path
        self._events: Dict[str, Dict[str, Any]] = {}  # idempotency_key -> event
        if self._path and self._path.exists():
            self._load()

    def configure(self, storage_path: Path) -> None:
        self._path = storage_path
        if self._path.exists():
            self._load()

    def record(
        self,
        *,
        direction: str,
        event_type: str,
        candidate_id: str,
        idempotency_key: str,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "sent",
    ) -> Dict[str, Any]:
        """Record a bridge event. If idempotency_key exists, return existing record."""
        if idempotency_key in self._events:
            existing = self._events[idempotency_key]
            existing["duplicate_count"] = existing.get("duplicate_count", 0) + 1
            existing["last_duplicate_at"] = _now_iso()
            self._save()
            return _copy(existing)

        event = {
            "event_id": _new_id("be"),
            "direction": direction,
            "type": event_type,
            "candidate_id": candidate_id,
            "idempotency_key": idempotency_key,
            "payload": payload or {},
            "status": status,
            "duplicate_count": 0,
            "created_at": _now_iso(),
            "last_duplicate_at": None,
        }
        self._events[idempotency_key] = event
        self._save()
        return _copy(event)

    def list_events(
        self,
        direction: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List events, optionally filtered."""
        results = list(self._events.values())
        if direction:
            results = [e for e in results if e.get("direction") == direction]
        if event_type:
            results = [e for e in results if e.get("type") == event_type]
        results.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return [_copy(e) for e in results[:limit]]

    def get_event(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        e = self._events.get(idempotency_key)
        return _copy(e) if e else None

    def metrics(self) -> Dict[str, Any]:
        events = list(self._events.values())
        return {
            "total_events": len(events),
            "xianyu_to_ecommerce": sum(1 for e in events if e.get("direction") == "xianyu_to_ecommerce"),
            "ecommerce_to_xianyu": sum(1 for e in events if e.get("direction") == "ecommerce_to_xianyu"),
            "duplicates_suppressed": sum(e.get("duplicate_count", 0) for e in events),
        }

    def _load(self) -> None:
        if not self._path:
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._events = {e["idempotency_key"]: e for e in data if "idempotency_key" in e}
        except (json.JSONDecodeError, KeyError):
            self._events = {}

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = list(self._events.values())
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


# Singleton
bridge_events = BridgeEventLedger()
