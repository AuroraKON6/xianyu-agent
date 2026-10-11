"""Time/provenance gates for supplier quotes, not supplier authentication."""
from datetime import datetime, timezone
import re
from typing import Mapping

from .candidate_activation import QUOTE_MAX_AGE_SECONDS


def source_time(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    try:
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def source_kind(snapshot):
    attributes = snapshot.get("attributes") or {}
    if not isinstance(attributes, Mapping):
        return "unknown"
    meta = attributes.get("quote_provenance") or {}
    if not isinstance(meta, Mapping):
        meta = {}
    declared = str(meta.get("source_kind") or "unknown").strip().lower()
    markers = (declared, str(attributes.get("source") or "").strip().lower())
    if any(value in ("demo", "fixture", "synthetic") for value in markers) or re.search(
            r"\bsource\s*=\s*(demo|fixture|synthetic)\b", str(attributes.get("notes") or ""), re.IGNORECASE):
        return "demo"
    return "supplier_original" if declared in ("real", "supplier_original") else "unknown"


def assess_quote_provenance(snapshot, *, now=None):
    moment = source_time(now or datetime.now(timezone.utc))
    if moment is None:
        raise ValueError("quote assessment time must be timezone-aware")
    attributes = snapshot.get("attributes") or {}
    meta = attributes.get("quote_provenance") if isinstance(attributes, Mapping) else None
    meta = meta if isinstance(meta, Mapping) else {}
    observed = source_time(meta.get("source_observed_at"))
    imported = source_time(snapshot.get("fetched_at"))
    expiry = source_time(meta.get("valid_until"))
    kind = source_kind(snapshot)
    reasons = []
    if type(meta.get("schema_version")) is not int or meta.get("schema_version") != 1:
        reasons.append("quote_provenance_missing_or_unsupported")
    if kind != "supplier_original":
        reasons.append("source_" + kind)
    reference = str(meta.get("source_reference") or "").strip()
    if reference.lower() in ("", "unknown", "unverified", "pending", "tbd", "none", "null", "-", "?"):
        reasons.append("source_reference_missing")
    if observed is None:
        reasons.append("source_observed_at_missing_or_invalid")
    if imported is None or imported > moment:
        reasons.append("import_time_missing_or_future")
    if observed is not None and (observed > moment or (imported is not None and observed > imported)):
        reasons.append("source_observed_at_future_or_after_import")
    if observed is not None and source_time(snapshot.get("observed_at")) != observed:
        reasons.append("source_observed_at_binding_mismatch")
    if expiry is None:
        reasons.append("valid_until_missing_or_invalid")
    elif source_time(snapshot.get("expires_at")) != expiry:
        reasons.append("valid_until_binding_mismatch")
    if observed is not None and expiry is not None and expiry <= observed:
        reasons.append("valid_until_not_after_observation")
    age = (moment - observed).total_seconds() if observed is not None else None
    expired = (age is not None and age > QUOTE_MAX_AGE_SECONDS) or (expiry is not None and expiry <= moment)
    if expired:
        reasons.append("quote_expired")
    return {
        "policy_version": "quote-provenance-v1", "eligible": not reasons, "source_kind": kind,
        "source_reference": reference, "source_observed_at": meta.get("source_observed_at"),
        "valid_until": meta.get("valid_until"), "imported_at": snapshot.get("fetched_at"),
        "age_seconds": round(age, 1) if age is not None else None, "expired": expired,
        "reasons": reasons, "assessed_at": moment.isoformat(), "provider_verified": False,
    }
