"""Opportunity adoption bridge (G3A): map a validated opportunity onto an
EXISTING resale candidate -- an explicit operator decision, never a model
auto-approval, never a bypass of the old gates.

The adoption record is deliberately small: it stores WHO adopted WHAT
INPUT VERSION of an opportunity onto which candidate and account scope.
It does not create evidence, does not flip candidate gates, does not
start a trial and does not publish anything.

Guarantees (A70-A74):
- Idempotent by operator idempotency key; the same key with a different
  payload is a conflict. Re-adopting the same (opportunity, candidate,
  account) returns the existing record instead of a second candidate
  mapping.
- Account isolation: an adoption record is scoped to one account id;
  another account adopting the same opportunity gets its own record and
  never inherits this one.
- Adoption captures the CURRENT input fingerprint (match, supplier and
  market snapshots, terms version). ``recheck`` compares that fingerprint
  against the live opportunity: a newer supplier snapshot, a match
  correction, expiry or a terms revision marks the adoption ``stale``
  with the precise reason; the old record and any order history stay
  untouched -- a new evaluation requires a NEW adoption, never a silent
  overwrite.
- Material confirmation does not impersonate product approval: adoption
  only requires that the material may be used for local analysis right
  now; candidate approval, trial and publishing remain the old chain's
  separate human facts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .commerce.repositories import LockedJsonList
from .opportunity_service import OpportunityService

ADOPTION_POLICY_VERSION = "opportunity-adoption-v1"
ADOPTED = "adopted"
STALE = "stale"
_IDEMPOTENCY_PATTERN = __import__("re").compile(r"^[A-Za-z0-9_.:-]{8,128}$")


class AdoptionError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _input_fingerprint(opportunity: Mapping[str, Any]) -> Dict[str, Any]:
    economics = opportunity.get("economics") or {}
    inputs = economics.get("inputs") or {}
    terms = inputs.get("commercial_terms") or {}
    return {
        "match_id": inputs.get("match_id"),
        "match_grade": inputs.get("match_grade"),
        "supplier_snapshot_id": inputs.get("supplier_snapshot_id"),
        "market_snapshot_id": inputs.get("market_snapshot_id"),
        "commercial_terms_version": terms.get("snapshot_version"),
        "economics_computed": opportunity.get("economics_ref"),
    }


class OpportunityAdoptionService:
    def __init__(self, repos, resale_manager, *, opportunities: OpportunityService,
                 eligibility_lookup=None, data_dir: Path) -> None:
        self._repos = repos
        self._resale = resale_manager
        self._opportunities = opportunities
        # eligibility_lookup(snapshot_id, use) -> {"eligible": bool, "reasons": [...]}
        self._eligibility = eligibility_lookup
        self._records = LockedJsonList(Path(data_dir) / "commerce" / "opportunity_adoptions.json")

    # ---------------------------------------------------------------- adopt

    def adopt(
        self,
        *,
        opportunity_id: str,
        candidate_id: str,
        account_id: str,
        operator: str,
        reason: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if not _IDEMPOTENCY_PATTERN.fullmatch(str(idempotency_key or "")):
            raise AdoptionError("invalid idempotency key", status_code=400)
        if len(str(reason or "").strip()) < 10:
            raise AdoptionError("adoption requires a substantive reason", status_code=400)
        if not str(account_id or "").strip():
            raise AdoptionError("account_id is required (adoption is account-scoped)", status_code=400)
        candidate = self._resale.get_candidate(candidate_id)
        if candidate is None:
            raise AdoptionError(f"candidate not found: {candidate_id}", status_code=404)
        opportunity = self._repos.opportunities.get(opportunity_id)
        if opportunity is None:
            raise AdoptionError(f"opportunity not found: {opportunity_id}", status_code=404)

        payload_fingerprint = _digest({
            "opportunity_id": opportunity_id, "candidate_id": candidate_id,
            "account_id": account_id, "reason": str(reason).strip(),
        })
        for existing in self._records.find(lambda r: str(r.get("idempotency_key")) == idempotency_key):
            if str(existing.get("payload_fingerprint")) == payload_fingerprint:
                return {"adoption": existing, "idempotent": True}
            raise AdoptionError("idempotency key already used with a different payload", status_code=409)

        # duplicate adoption guard: same scope maps once (A71)
        for existing in self._records.find(
            lambda r: str(r.get("opportunity_id")) == opportunity_id
            and str(r.get("candidate_id")) == candidate_id
            and str(r.get("account_id")) == account_id
            and str(r.get("status")) == ADOPTED
        ):
            return {"adoption": existing, "idempotent": True, "already_adopted": True}

        # the opportunity must currently be valid (fresh inputs, latest match EXACT);
        # a paused/invalid opportunity cannot be adopted -- fix the cause first
        validation = self._opportunities.validate_current(opportunity_id)
        if not validation.get("valid"):
            raise AdoptionError(
                "opportunity failed current validation: " + "; ".join(validation.get("reasons") or []),
                status_code=409,
            )

        # material must at least be usable for local analysis right now;
        # this is NOT product approval and grants no trial/publish rights (A73)
        supplier_snapshot_id = str((opportunity.get("offer_snapshot_ids") or [""])[-1])
        material_note = "no eligibility channel configured; recorded as unverified"
        if self._eligibility is not None:
            outcome = self._eligibility(supplier_snapshot_id, "local_analysis")
            if not outcome.get("eligible"):
                raise AdoptionError(
                    "material not confirmed for local analysis: "
                    + "; ".join(outcome.get("reasons") or ["unconfirmed"]),
                    status_code=409,
                )
            material_note = "material confirmed for local_analysis at adoption time"

        fingerprint = _input_fingerprint(opportunity)
        record = {
            "record_id": "adp_" + hashlib.sha256(
                f"{opportunity_id}:{candidate_id}:{account_id}".encode("utf-8")
            ).hexdigest()[:16],
            "record_type": "opportunity_adoption",
            "policy_version": ADOPTION_POLICY_VERSION,
            "opportunity_id": opportunity_id,
            "opportunity_revision": int(opportunity.get("revision") or 1),
            "candidate_id": candidate_id,
            "account_id": str(account_id).strip(),
            "operator": str(operator or "").strip(),
            "reason": str(reason).strip(),
            "input_fingerprint": fingerprint,
            "input_digest": _digest(fingerprint),
            "material_note": material_note,
            "status": ADOPTED,
            "stale_reason": "",
            "rechecked_at": None,
            "adopted_at": datetime.utcnow().isoformat() + "+00:00",
            "superseded_by": None,
            "idempotency_key": idempotency_key,
            "payload_fingerprint": payload_fingerprint,
        }
        stored = self._records.append(record)
        return {"adoption": stored, "idempotent": False}

    # --------------------------------------------------------------- recheck

    def recheck(self, adoption_id: str) -> Dict[str, Any]:
        found = self._records.find(lambda r: str(r.get("record_id")) == adoption_id)
        if not found:
            raise AdoptionError(f"adoption not found: {adoption_id}", status_code=404)
        record = found[0]
        if str(record.get("status")) != ADOPTED:
            return {"adoption": record, "valid": False, "reasons": [f"adoption status {record.get('status')}"]}

        reasons: List[str] = []
        opportunity = self._repos.opportunities.get(str(record.get("opportunity_id")))
        if opportunity is None:
            reasons.append("opportunity record missing")
        else:
            validation = self._opportunities.validate_current(str(record.get("opportunity_id")))
            if not validation.get("valid"):
                reasons.extend(validation.get("reasons") or [])
            current = _input_fingerprint(opportunity)
            if current != dict(record.get("input_fingerprint") or {}):
                reasons.append(
                    "opportunity inputs changed since adoption "
                    f"(stored digest {record.get('input_digest')}, current {_digest(current)}); "
                    "re-adopt against the new evaluation, the old basis is kept for audit"
                )
        supplier_snapshot_id = str((record.get("input_fingerprint") or {}).get("supplier_snapshot_id") or "")
        if self._eligibility is not None and supplier_snapshot_id:
            outcome = self._eligibility(supplier_snapshot_id, "local_analysis")
            if not outcome.get("eligible"):
                reasons.append("material confirmation lapsed: " + "; ".join(outcome.get("reasons") or []))

        valid = not reasons

        def mutate(stored: Dict[str, Any]) -> None:
            stored["rechecked_at"] = datetime.utcnow().isoformat() + "+00:00"
            if not valid:
                stored["status"] = STALE
                stored["stale_reason"] = "; ".join(reasons)

        updated = self._records.update(adoption_id, mutate)
        return {"adoption": updated, "valid": valid, "reasons": reasons}

    # ----------------------------------------------------------------- reads

    def list_adoptions(self, *, opportunity_id: str = "", candidate_id: str = "", account_id: str = "") -> List[Dict[str, Any]]:
        def predicate(record: Mapping[str, Any]) -> bool:
            if opportunity_id and str(record.get("opportunity_id")) != opportunity_id:
                return False
            if candidate_id and str(record.get("candidate_id")) != candidate_id:
                return False
            if account_id and str(record.get("account_id")) != account_id:
                return False
            return True

        return self._records.find(predicate)

    def get(self, adoption_id: str) -> Dict[str, Any]:
        found = self._records.find(lambda r: str(r.get("record_id")) == adoption_id)
        if not found:
            raise AdoptionError(f"adoption not found: {adoption_id}", status_code=404)
        return found[0]
