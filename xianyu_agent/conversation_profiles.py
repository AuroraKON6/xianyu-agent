from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List


_LONG_NUMBER_RE = re.compile(r"\b\d{5,}\b")
_CONTACT_WORD_RE = re.compile(r"\b(QQ|wechat|weixin|wx|phone|tel|alipay)\b", re.IGNORECASE)
_CONTACT_COMBINED_RE = re.compile(
    r"(\b\d{5,}\b|\bQQ\b|\bwechat\b|\bweixin\b|\bwx\b|\bphone\b|\btel\b|\balipay\b)",
    re.IGNORECASE,
)


def summarize_conversation(
    history: List[Dict[str, Any]] | None,
    recent_limit: int = 5,
    content_limit: int = 160,
) -> Dict[str, Any]:
    """Build a compact, redacted conversation summary for operators and agents."""
    rows = list(history or [])
    intents = [str(row.get("intent") or "") for row in rows if row.get("intent")]
    bargain_counts = [_safe_int(row.get("bargain_count")) for row in rows]
    risk_flags = sorted(_collect_risk_flags(rows))

    recent_messages = [
        _safe_message(row, content_limit=content_limit)
        for row in rows[-max(0, recent_limit):]
    ]
    max_bargain_count = max(bargain_counts) if bargain_counts else 0

    return {
        "turns": len(rows),
        "last_intent": intents[-1] if intents else "",
        "intent_counts": dict(Counter(intents)),
        "role_counts": dict(Counter(str(row.get("role") or "unknown") for row in rows)),
        "max_bargain_count": max_bargain_count,
        "risk_flags": risk_flags,
        "recent_messages": recent_messages,
        "next_operator_action": _next_operator_action(rows, intents, max_bargain_count, risk_flags),
    }


def _safe_message(row: Dict[str, Any], content_limit: int) -> Dict[str, Any]:
    content = _redact_text(str(row.get("content") or ""))
    if len(content) > content_limit:
        content = content[: max(0, content_limit - 3)].rstrip() + "..."
    return {
        "role": str(row.get("role") or "unknown"),
        "content": content,
        "intent": str(row.get("intent") or ""),
        "bargain_count": _safe_int(row.get("bargain_count")),
        "created_at": str(row.get("created_at") or ""),
    }


def _collect_risk_flags(rows: List[Dict[str, Any]]) -> set[str]:
    flags: set[str] = set()
    for row in rows:
        content = str(row.get("content") or "")
        if _LONG_NUMBER_RE.search(content) or _CONTACT_WORD_RE.search(content):
            flags.add("off_platform_contact")
    return flags


def _redact_text(value: str) -> str:
    return _CONTACT_COMBINED_RE.sub("[redacted_contact]", value)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _next_operator_action(
    rows: List[Dict[str, Any]],
    intents: List[str],
    max_bargain_count: int,
    risk_flags: List[str],
) -> str:
    if not rows:
        return "wait_for_buyer_message"
    if "off_platform_contact" in risk_flags:
        return "review_risk_before_reply"
    if max_bargain_count >= 2:
        return "review_discount_boundary"
    if intents and intents[-1] in {"complaint", "after_sales", "quality"}:
        return "manual_after_sales_review"
    return "ready_for_reply_draft"
