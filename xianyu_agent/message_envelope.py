from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Mapping, Optional


def normalize_message_envelope(
    *,
    source: str,
    raw_payload: Mapping[str, Any],
    cookie_id: str = "",
    defaults: Optional[Mapping[str, Any]] = None,
    received_at: float | None = None,
) -> Dict[str, Any]:
    """Normalize inbound message payloads from WS, browser, CLI, or manual tools."""
    raw = dict(raw_payload or {})
    default_values = dict(defaults or {})
    clean_source = str(source or raw.get("source") or "manual").strip() or "manual"
    clean_cookie_id = _first_text(raw, "cookie_id", "cookieId", default=default_values.get("cookie_id") or cookie_id)
    source_message_id = _first_text(
        raw,
        "message_id",
        "messageId",
        "msg_id",
        "msgId",
        "mid",
        "id",
        default=default_values.get("message_id"),
    )
    buyer_id = _first_text(
        raw,
        "buyer_id",
        "buyerId",
        "senderUserId",
        "senderId",
        "send_user_id",
        "userId",
        default=default_values.get("buyer_id"),
    )
    buyer_name = _first_text(
        raw,
        "buyer_name",
        "buyerName",
        "senderNick",
        "senderUserName",
        "send_user_name",
        "nick",
        default=default_values.get("buyer_name"),
    )
    chat_id = _first_text(
        raw,
        "chat_id",
        "chatId",
        "session_id",
        "sessionId",
        "cid",
        default=default_values.get("chat_id"),
    )
    item_id = _first_text(
        raw,
        "item_id",
        "itemId",
        "product_id",
        "productId",
        "listing_id",
        "listingId",
        default=default_values.get("item_id"),
    )
    message_text = _first_text(
        raw,
        "message",
        "message_text",
        "text",
        "content",
        "reminderContent",
        "last_message",
        "summary",
        default=default_values.get("message"),
    )
    occurred_at = _timestamp_from_raw(
        _first_text(raw, "created_at", "createTime", "msg_time", "msgTime", "time", "ts")
    )
    if not occurred_at:
        occurred_at = time.time()
    received = time.time() if received_at is None else float(received_at)
    direction = "outbound" if bool(raw.get("isSelf") or raw.get("is_self")) else "inbound"
    dedupe_key = _dedupe_key(
        source=clean_source,
        cookie_id=clean_cookie_id,
        source_message_id=source_message_id,
        buyer_id=buyer_id,
        chat_id=chat_id,
        item_id=item_id,
        message_text=message_text,
    )
    envelope_id = "env_" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:16]
    return {
        "envelope_id": envelope_id,
        "source": clean_source,
        "source_message_id": source_message_id,
        "cookie_id": clean_cookie_id,
        "buyer_id": buyer_id,
        "buyer_name": buyer_name,
        "chat_id": chat_id,
        "item_id": item_id,
        "message_text": message_text,
        "message_type": _first_text(raw, "msg_type", "msgType", "type", default="text") or "text",
        "direction": direction,
        "occurred_at": occurred_at,
        "received_at": received,
        "ack_status": "pending" if direction == "inbound" else "not_required",
        "dedupe_key": dedupe_key,
    }


def to_inbound_request_payload(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert a normalized envelope into the existing inbound message API payload."""
    public_envelope = public_message_envelope(envelope)
    return {
        "source": str(envelope.get("source") or "manual"),
        "message_id": str(envelope.get("source_message_id") or ""),
        "buyer_id": str(envelope.get("buyer_id") or ""),
        "buyer_name": str(envelope.get("buyer_name") or ""),
        "message": str(envelope.get("message_text") or ""),
        "chat_id": str(envelope.get("chat_id") or ""),
        "item_id": str(envelope.get("item_id") or ""),
        "product_id": str(envelope.get("item_id") or ""),
        "cookie_id": str(envelope.get("cookie_id") or "default"),
        "conversation": [
            {
                "role": "buyer",
                "content": str(envelope.get("message_text") or ""),
                "time": str(envelope.get("occurred_at") or ""),
            }
        ],
        "context": {
            "message_envelope": public_envelope,
            "message_dedupe_key": str(envelope.get("dedupe_key") or ""),
            "source_message_id": str(envelope.get("source_message_id") or ""),
        },
    }


def public_message_envelope(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the operator-safe envelope without raw platform payloads."""
    return {
        "envelope_id": str(envelope.get("envelope_id") or ""),
        "source": str(envelope.get("source") or ""),
        "source_message_id": str(envelope.get("source_message_id") or ""),
        "cookie_id": str(envelope.get("cookie_id") or ""),
        "buyer_id": str(envelope.get("buyer_id") or ""),
        "buyer_name": str(envelope.get("buyer_name") or ""),
        "chat_id": str(envelope.get("chat_id") or ""),
        "item_id": str(envelope.get("item_id") or ""),
        "message_text": str(envelope.get("message_text") or ""),
        "message_type": str(envelope.get("message_type") or "text"),
        "direction": str(envelope.get("direction") or "inbound"),
        "occurred_at": float(envelope.get("occurred_at") or 0),
        "received_at": float(envelope.get("received_at") or 0),
        "ack_status": str(envelope.get("ack_status") or "pending"),
        "dedupe_key": str(envelope.get("dedupe_key") or ""),
    }


def with_ack_status(envelope: Mapping[str, Any], ack_status: str) -> Dict[str, Any]:
    public = public_message_envelope(envelope)
    public["ack_status"] = str(ack_status or public.get("ack_status") or "pending")
    return public


def _first_text(raw: Mapping[str, Any], *keys: str, default: Any = "") -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    if default is not None and str(default).strip():
        return str(default).strip()
    return ""


def _timestamp_from_raw(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed > 10_000_000_000:
        parsed = parsed / 1000.0
    return parsed


def _dedupe_key(
    *,
    source: str,
    cookie_id: str,
    source_message_id: str,
    buyer_id: str,
    chat_id: str,
    item_id: str,
    message_text: str,
) -> str:
    if source_message_id:
        return f"message_id:{source}:{cookie_id}:{source_message_id}"
    raw = json.dumps(
        {
            "source": source,
            "cookie_id": cookie_id,
            "buyer_id": buyer_id,
            "chat_id": chat_id,
            "item_id": item_id,
            "message_text": message_text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
