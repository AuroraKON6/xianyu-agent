"""Snapshot freshness classification (WP04).

Freshness answers "may this snapshot still drive decisions?" per source
type, using the same thresholds the candidate-activation gates use:
supplier quotes 24h, market baselines 72h. Expired data is still
readable history; it must never silently drive an automatic commitment
(the opportunity qualification checks the same fact independently).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from ..candidate_activation import MARKET_MAX_AGE_SECONDS, QUOTE_MAX_AGE_SECONDS

FRESH_CURRENT = "current"
FRESH_EXPIRED = "expired"
FRESH_UNKNOWN = "unknown"


def provider_ttl_seconds(provider_id: str) -> Optional[int]:
    text = str(provider_id or "")
    if text.startswith("supplier_file"):
        return QUOTE_MAX_AGE_SECONDS
    if text.startswith("market:"):
        return MARKET_MAX_AGE_SECONDS
    return None


def classify_snapshot(snapshot: Mapping[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Classify one OfferSnapshot: current / expired / unknown + age."""
    moment = now or datetime.now(timezone.utc)
    provider_id = str(snapshot.get("provider_id") or "")
    ttl = provider_ttl_seconds(provider_id)
    observed_raw = str(snapshot.get("observed_at") or "")
    if ttl is None or not observed_raw:
        return {"freshness": FRESH_UNKNOWN, "age_seconds": None, "ttl_seconds": ttl}
    try:
        observed = datetime.fromisoformat(observed_raw)
    except ValueError:
        return {"freshness": FRESH_UNKNOWN, "age_seconds": None, "ttl_seconds": ttl}
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = (moment - observed).total_seconds()
    return {
        "freshness": FRESH_CURRENT if age <= ttl else FRESH_EXPIRED,
        "age_seconds": round(age, 1),
        "ttl_seconds": ttl,
    }
