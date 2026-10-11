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
from ..quote_provenance import assess_quote_provenance, source_kind, source_time

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
    moment = source_time(now or datetime.now(timezone.utc))
    if moment is None:
        raise ValueError("freshness assessment time must be timezone-aware")
    provider_id = str(snapshot.get("provider_id") or "")
    ttl = provider_ttl_seconds(provider_id)
    if provider_id.startswith("supplier_file"):
        provenance = assess_quote_provenance(snapshot, now=moment)
        return dict(provenance, ttl_seconds=ttl, business_eligible=provenance["eligible"],
                    freshness=FRESH_CURRENT if provenance["eligible"] else FRESH_EXPIRED if provenance["expired"] else FRESH_UNKNOWN)
    observed_raw = str(snapshot.get("observed_at") or "")
    if ttl is None or not observed_raw:
        return {"freshness": FRESH_UNKNOWN, "age_seconds": None, "ttl_seconds": ttl}
    observed = source_time(observed_raw)
    if observed is None:
        return {"freshness": FRESH_UNKNOWN, "age_seconds": None, "ttl_seconds": ttl}
    age = (moment - observed).total_seconds()
    expiry = source_time(snapshot.get("expires_at"))
    valid = 0 <= age <= ttl and (not snapshot.get("expires_at") or (expiry is not None and moment < expiry))
    demo = source_kind(snapshot) == "demo"
    return {
        "freshness": FRESH_UNKNOWN if age < 0 or demo else FRESH_CURRENT if valid else FRESH_EXPIRED,
        "age_seconds": round(age, 1),
        "ttl_seconds": ttl,
        "source_kind": "demo" if demo else "market_observation",
        "business_eligible": valid and not demo,
    }
