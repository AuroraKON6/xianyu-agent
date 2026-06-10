from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

import requests

from .config import Settings


def verify_feishu_token(payload: Dict[str, Any], settings: Settings) -> bool:
    return True


def extract_feishu_text(payload: Dict[str, Any]) -> str:
    event = payload.get("event", {})
    message = event.get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        try:
            content_json = json.loads(content)
            text = content_json.get("text") or content_json.get("title") or ""
            if text:
                return str(text).strip()
        except json.JSONDecodeError:
            return content.strip()
    if isinstance(content, dict):
        return str(content.get("text") or content.get("title") or "").strip()
    if payload.get("text"):
        return str(payload["text"]).strip()
    return ""


def extract_xianyu_inbound_message(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract a structured buyer-message event bridged by Feishu CLI.

    Feishu CLI can forward local Xianyu IM events either as a direct JSON payload
    or inside message.content. Plain Feishu text messages are intentionally ignored
    here so command/listing flows keep their existing behavior.
    """
    for candidate in _structured_event_candidates(payload):
        normalized = _normalize_xianyu_message(candidate)
        if normalized:
            return normalized
    return None


def _structured_event_candidates(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content")

    raw_candidates = [
        payload,
        event,
        payload.get("xianyu_message"),
        event.get("xianyu_message"),
        payload.get("data"),
        event.get("data"),
        _json_value(content),
    ]

    parsed_content = _json_value(content)
    if isinstance(parsed_content, dict):
        text_payload = _json_value(parsed_content.get("text"))
        raw_candidates.extend(
            [
                parsed_content.get("xianyu_message"),
                parsed_content.get("data"),
                text_payload,
            ]
        )

    for item in raw_candidates:
        if isinstance(item, dict):
            yield item


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _normalize_xianyu_message(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    marker = str(
        candidate.get("xianyu_event")
        or candidate.get("event_type")
        or candidate.get("type")
        or ""
    ).strip().lower()
    allowed_markers = {
        "buyer_message",
        "xianyu_buyer_message",
        "incoming_message",
        "xianyu_inbound_message",
    }

    buyer_id = str(
        candidate.get("buyer_id")
        or candidate.get("buyerId")
        or candidate.get("sender_user_id")
        or candidate.get("senderUserId")
        or candidate.get("user_id")
        or ""
    ).strip()
    message = str(
        candidate.get("message")
        or candidate.get("text")
        or candidate.get("content_text")
        or ""
    ).strip()

    if marker not in allowed_markers and not (
        buyer_id and message and str(candidate.get("source") or "").startswith(("xianyu", "feishu_cli"))
    ):
        return None
    if not buyer_id or not message:
        return None

    return {
        "source": str(candidate.get("source") or "feishu_cli").strip() or "feishu_cli",
        "buyer_id": buyer_id,
        "buyer_name": str(candidate.get("buyer_name") or candidate.get("buyerName") or "").strip(),
        "message": message,
        "message_id": str(candidate.get("message_id") or candidate.get("messageId") or candidate.get("id") or "").strip(),
        "chat_id": str(candidate.get("chat_id") or candidate.get("chatId") or "").strip(),
        "item_id": str(candidate.get("item_id") or candidate.get("itemId") or "").strip(),
        "product_id": str(candidate.get("product_id") or candidate.get("productId") or "").strip(),
        "product_title": str(candidate.get("product_title") or candidate.get("productTitle") or "").strip(),
        "product_description": str(
            candidate.get("product_description") or candidate.get("productDescription") or ""
        ).strip(),
        "product_price": str(candidate.get("product_price") or candidate.get("productPrice") or "").strip(),
        "cookie_id": str(candidate.get("cookie_id") or candidate.get("cookieId") or "default").strip() or "default",
        "conversation": candidate.get("conversation") if isinstance(candidate.get("conversation"), list) else [],
    }


class FeishuNotifier:
    """Send text messages to Feishu via webhook bot URL.

    Deprecated: prefer Feishu CLI long connection for bidirectional messaging.
    This class is only used when FEISHU_WEBHOOK_URL is explicitly configured.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_text(self, text: str) -> None:
        if not self.settings.feishu_webhook_url:
            return
        requests.post(
            self.settings.feishu_webhook_url,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=20,
        ).raise_for_status()
