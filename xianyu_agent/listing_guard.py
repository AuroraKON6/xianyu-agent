"""Listing content guard (WP12).

Scans listing drafts for claims the system cannot back with verified
facts and blocks approval until they are removed or evidenced:

- Fabricated supply relationships ("我们在其他平台也有店铺"), cashback
  solicitation ("好评返现") -- platform violations and unverified
  assertions that templates must never generate again.
- Absolute promises the seller cannot guarantee (arrival guarantees,
  "绝对正品").
- Structural completeness: title/price/spec evidence and image
  references must exist; a draft without images is not publishable.
- Price staleness: when the draft cites a supplier offer snapshot, the
  draft price must match the latest snapshot for that SKU; a changed
  supplier price makes the draft stale and it must be rebuilt (the old
  approval never survives a price revision).
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence

SEVERITY_BLOCK = "block"
SEVERITY_WARN = "warn"

# Unverified-claim patterns. These assertions must come from verified
# operating facts, never from templates.
_UNVERIFIED_CLAIM_PATTERNS: Sequence[Mapping[str, Any]] = (
    {"pattern": re.compile(r"(其他|别的|多个?)平台.*(店铺|有店)|我们.*都有店|拼多多店铺也是我们的"), "code": "fabricated_multi_platform_store", "reason": "asserts store ownership on other platforms without verified facts"},
    {"pattern": re.compile(r"返现|好评返|现金返"), "code": "cashback_solicitation", "reason": "cashback for reviews violates platform rules"},
    {"pattern": re.compile(r"(一定|保证).{0,6}(到|送达|到货)"), "code": "arrival_guarantee", "reason": "arrival guarantees cannot be promised"},
    {"pattern": re.compile(r"绝对正品|100%正品"), "code": "absolute_authenticity", "reason": "absolute authenticity claims are unverifiable"},
)

_REQUIRED_FIELDS = ("title", "price", "spec")


def scan_listing_text(text: str) -> List[Dict[str, Any]]:
    """Detect unverified-claim patterns in listing text."""
    clean = str(text or "")
    issues: List[Dict[str, Any]] = []
    for spec in _UNVERIFIED_CLAIM_PATTERNS:
        match = spec["pattern"].search(clean)
        if match:
            issues.append(
                {
                    "code": spec["code"],
                    "severity": SEVERITY_BLOCK,
                    "excerpt": clean[max(0, match.start() - 8) : match.end() + 8],
                    "reason": spec["reason"],
                }
            )
    return issues


def check_listing_draft(
    draft: Mapping[str, Any],
    *,
    latest_offer: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate one listing draft for publishability.

    ``draft``: {title, price, spec, description, images: [refs],
    offer_snapshot_id, offer_price}. ``latest_offer`` is the current
    latest supplier OfferSnapshot for the same SKU; a price mismatch
    marks the draft stale.
    """
    issues: List[Dict[str, Any]] = []
    issues.extend(scan_listing_text(str(draft.get("title") or "")))
    issues.extend(scan_listing_text(str(draft.get("description") or "")))

    for field_name in _REQUIRED_FIELDS:
        value = draft.get(field_name)
        if value in (None, "", []):
            issues.append({"code": f"missing_{field_name}", "severity": SEVERITY_BLOCK, "reason": f"{field_name} is required"})
    price = draft.get("price")
    if price is not None and Decimal(str(price)) <= 0:
        issues.append({"code": "invalid_price", "severity": SEVERITY_BLOCK, "reason": "price must be positive"})

    if not (draft.get("images") or []):
        issues.append({"code": "missing_images", "severity": SEVERITY_BLOCK, "reason": "at least one authorized image reference is required"})

    stale = False
    if latest_offer is not None and draft.get("offer_snapshot_id"):
        draft_offer_price = draft.get("offer_price")
        latest_price = (latest_offer.get("eligible_price") or {}).get("amount")
        if draft_offer_price is not None and latest_price is not None:
            if Decimal(str(draft_offer_price)) != Decimal(str(latest_price)):
                stale = True
                issues.append(
                    {
                        "code": "stale_offer_price",
                        "severity": SEVERITY_BLOCK,
                        "reason": f"draft built on offer price {draft_offer_price} but the latest snapshot price is {latest_price}; rebuild the draft",
                    }
                )

    blocking = [i for i in issues if i["severity"] == SEVERITY_BLOCK]
    return {
        "approved": not blocking and not stale,
        "stale": stale,
        "issues": issues,
        "note": "guard result only; actual publishing still requires the operator approval chain",
    }
