from __future__ import annotations

import json
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4


EVENT_STATUSES = {"unread", "read", "promoted", "rejected"}
SENSITIVE_KEYS = {
    "address",
    "api_key",
    "authorization",
    "buyer_id",
    "chat_id",
    "cookie",
    "cookies",
    "cookie_value",
    "password",
    "phone",
    "raw_cookie",
    "secret",
    "token",
}


class DiscoveryInboxStore:
    """JSON-backed inbox for product leads from scans, tools, and manual input."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = RLock()
        self._events: Dict[str, Dict[str, Any]] = {}
        self._load()

    def add_event(
        self,
        *,
        source: str,
        raw_payload: Optional[Mapping[str, Any]] = None,
        title: str = "",
        summary: str = "",
        category_hint: str = "",
        supplier_hint: str = "",
        price_hint: float | int | str = 0,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        event_id = f"di_{uuid4().hex[:12]}"
        now = self._timestamp()
        event = {
            "event_id": event_id,
            "source": str(source or "manual").strip() or "manual",
            "raw_payload": _sanitize_value(dict(raw_payload or {})),
            "title": str(title or "").strip(),
            "summary": str(summary or "").strip(),
            "category_hint": str(category_hint or "").strip(),
            "supplier_hint": str(supplier_hint or "").strip(),
            "price_hint": _to_float(price_hint),
            "metadata": _sanitize_value(dict(metadata or {})),
            "status": "unread",
            "rejection_reason": "",
            "promoted_candidate_id": "",
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._events[event_id] = event
            self._persist()
            return _copy(event)

    def list_events(self, *, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events.values())
            if status:
                events = [event for event in events if event.get("status") == status]
            events.sort(key=lambda event: float(event.get("created_at") or 0), reverse=True)
            return _copy(events[: max(0, int(limit or 100))])

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            event = self._events.get(str(event_id or ""))
            return _copy(event) if event else None

    def mark_read(self, event_id: str) -> Dict[str, Any]:
        return self._update(event_id, {"status": "read"})

    def reject_event(self, event_id: str, *, reason: str = "") -> Dict[str, Any]:
        return self._update(event_id, {"status": "rejected", "rejection_reason": str(reason or "")[:300]})

    def promote_to_candidate(
        self,
        event_id: str,
        *,
        resale_manager: Any,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            event = self._require_event(event_id)
            override_values = dict(overrides or {})
            raw_payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), Mapping) else {}
            supplier_price = _first_number(
                override_values.get("supplier_price"),
                event.get("price_hint"),
                raw_payload.get("price") if isinstance(raw_payload, Mapping) else None,
            )
            supplier_shipping = _first_number(override_values.get("supplier_shipping"), 0)
            target_sale_price = _first_number(
                override_values.get("target_sale_price"),
                supplier_price * 1.8 if supplier_price else 0,
            )
            candidate_payload = {
                "title": override_values.get("title") or event.get("title") or "Discovery lead",
                "description": override_values.get("description") or event.get("summary") or "",
                "source_platform": (
                    override_values.get("source_platform")
                    or event.get("supplier_hint")
                    or event.get("source")
                    or "pdd"
                ),
                "source_url": override_values.get("source_url") or raw_payload.get("url", ""),
                "supplier_price": supplier_price,
                "supplier_shipping": supplier_shipping,
                "target_sale_price": target_sale_price,
                "category": override_values.get("category") or event.get("category_hint") or "",
                "market_scope": override_values.get("market_scope") or "domestic",
                "sales_platform": override_values.get("sales_platform") or "xianyu",
                "platform_fee_rate": override_values.get("platform_fee_rate", 0.08),
                "expected_refund_rate": override_values.get("expected_refund_rate", 0.02),
                "specifications": override_values.get("specifications") or {},
                "notes": _join_notes(
                    override_values.get("notes"),
                    f"Promoted from discovery inbox {event['event_id']}.",
                    event.get("summary"),
                ),
            }
            candidate = resale_manager.create_candidate(candidate_payload)
            event["status"] = "promoted"
            event["promoted_candidate_id"] = candidate.get("candidate_id") or candidate.get("id") or ""
            event["updated_at"] = self._timestamp()
            self._persist()
            return {"ok": True, "event": _copy(event), "candidate": candidate}

    def _update(self, event_id: str, values: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            event = self._require_event(event_id)
            status = values.get("status")
            if status and status not in EVENT_STATUSES:
                raise ValueError(f"unsupported discovery inbox status: {status}")
            event.update(dict(values))
            event["updated_at"] = self._timestamp()
            self._persist()
            return _copy(event)

    def _require_event(self, event_id: str) -> Dict[str, Any]:
        event = self._events.get(str(event_id or ""))
        if not event:
            raise KeyError("discovery inbox event not found")
        return event

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_events = data.get("events") if isinstance(data, Mapping) else None
        if not isinstance(raw_events, list):
            return
        for item in raw_events:
            if not isinstance(item, Mapping):
                continue
            event_id = str(item.get("event_id") or "").strip()
            if not event_id:
                continue
            event = _default_event(event_id)
            event.update(dict(item))
            event["event_id"] = event_id
            event["raw_payload"] = _sanitize_value(event.get("raw_payload") or {})
            event["metadata"] = _sanitize_value(event.get("metadata") or {})
            if event.get("status") not in EVENT_STATUSES:
                event["status"] = "unread"
            self._events[event_id] = event

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        events = sorted(self._events.values(), key=lambda event: float(event.get("created_at") or 0))
        tmp_path.write_text(
            json.dumps({"events": events}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    @staticmethod
    def _timestamp() -> float:
        return time.time()


def _default_event(event_id: str) -> Dict[str, Any]:
    now = time.time()
    return {
        "event_id": event_id,
        "source": "manual",
        "raw_payload": {},
        "title": "",
        "summary": "",
        "category_hint": "",
        "supplier_hint": "",
        "price_hint": 0.0,
        "metadata": {},
        "status": "unread",
        "rejection_reason": "",
        "promoted_candidate_id": "",
        "created_at": now,
        "updated_at": now,
    }


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SENSITIVE_KEYS:
                continue
            clean[key_text] = _sanitize_value(item)
        return clean
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _first_number(*values: Any) -> float:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return number
    return 0.0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _join_notes(*parts: Any) -> str:
    return "\n".join(str(part).strip() for part in parts if str(part or "").strip())


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
