"""Operating mandates (WP10): bounded operator-issued authorization.

An OperatingMandate is the operator's machine-readable grant: which
account, which SKUs/categories, which action types, price bounds,
per-transaction and daily money limits, validity window and revocation.

Rules enforced here (WP10 backlog):
- A mandate is issued once; expanding its limits requires issuing a NEW
  mandate (record expansion is refused, plan 11.1 "额度扩大必须新版
  本"). Shrinking likewise -- edits are not a thing.
- Revocation is append-only metadata on the stored record (revoked_at /
  revoked_by / revoke_reason); the policy engine reads it live, so
  already-queued-but-unexecuted actions fail their re-check. Requests
  already sent to the platform cannot be recalled by us -- that stays
  with the execution verification path.
- ``list_active`` only returns mandates inside their time window that
  are not revoked.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from .commerce.contracts import (
    PRICE_STATE_KNOWN,
    Money,
    build_operating_mandate,
    business_day,
    money_from_mapping,
    utc_now,
)
from .commerce.repositories import LockedJsonList


class MandateError(ValueError):
    pass


class OperatingMandateStore:
    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "operating_mandates.json")

    def issue(
        self,
        *,
        operator: str,
        account_id: str,
        action_types: List[str],
        sku_scope: Optional[List[str]] = None,
        category_scope: Optional[List[str]] = None,
        price_floor: Optional[Mapping[str, Any]] = None,
        price_ceiling: Optional[Mapping[str, Any]] = None,
        per_transaction_limit: Optional[Mapping[str, Any]] = None,
        daily_limit: Optional[Mapping[str, Any]] = None,
        max_actions: Optional[int] = None,
        valid_until: datetime,
        valid_from: Optional[datetime] = None,
        suppliers: Optional[List[str]] = None,
        max_jobs: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not operator.strip():
            raise MandateError("issuing a mandate requires the operator's name")
        record = build_operating_mandate(
            operator=operator,
            account_id=account_id,
            action_types=action_types,
            sku_scope=sku_scope,
            category_scope=category_scope,
            price_floor=price_floor,
            price_ceiling=price_ceiling,
            per_transaction_limit=per_transaction_limit,
            daily_limit=daily_limit,
            max_actions=max_actions,
            valid_from=valid_from,
            valid_until=valid_until,
            suppliers=suppliers,
            max_jobs=max_jobs,
        )
        return self._store.append(record)

    def revoke(self, mandate_id: str, *, operator: str, reason: str) -> Dict[str, Any]:
        if not reason.strip():
            raise MandateError("revocation requires a reason")

        def mutate(mandate: Dict[str, Any]) -> None:
            if mandate.get("revoked_at"):
                raise MandateError("mandate already revoked")
            mandate["revoked_at"] = utc_now().isoformat()
            mandate["revoked_by"] = operator
            mandate["revoke_reason"] = reason

        try:
            return self._store.update(mandate_id, mutate)
        except KeyError as exc:
            raise MandateError(f"mandate not found: {mandate_id}") from exc

    def get(self, mandate_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda m: str(m.get("record_id")) == mandate_id)
        return found[0] if found else None

    def is_active(self, mandate: Mapping[str, Any], *, now: Optional[datetime] = None) -> bool:
        moment = now or utc_now()
        if mandate.get("revoked_at"):
            return False
        try:
            until = datetime.fromisoformat(str(mandate.get("valid_until")))
            if until.tzinfo is None:
                until = until.replace(tzinfo=moment.tzinfo)
            if moment > until:
                return False
            valid_from = mandate.get("valid_from")
            if valid_from:
                start = datetime.fromisoformat(str(valid_from))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=moment.tzinfo)
                if moment < start:
                    return False
        except ValueError:
            return False
        return True

    def list_active(self, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        return [m for m in self._store.all() if self.is_active(m, now=now)]

    def list(self) -> List[Dict[str, Any]]:
        return self._store.all()


# ---------------------------------------------------------------------------
# Policy engine: deterministic allow/deny from live mandate + budget state
# ---------------------------------------------------------------------------

ACTION_TYPES_REQUIRING_AMOUNT = {"procurement_order", "refund_commit", "price_adjust"}


class ActionRequest:
    """One action the system (or an agent) proposes to perform."""

    def __init__(
        self,
        *,
        action_type: str,
        account_id: str,
        sku: str = "",
        category: str = "",
        amount: Optional[Mapping[str, Any]] = None,
        listing_price: Optional[Mapping[str, Any]] = None,
        quantity: Optional[int] = None,
        references: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not action_type.strip() or not account_id.strip():
            raise MandateError("action request requires action_type and account_id")
        self.action_type = action_type.strip()
        self.account_id = account_id.strip()
        self.sku = sku.strip()
        self.category = category.strip()
        self.amount = money_from_mapping(amount, field_name="amount") if amount is not None else None
        self.listing_price = money_from_mapping(listing_price, field_name="listing_price") if listing_price is not None else None
        self.quantity = quantity
        self.references = dict(references or {})


class PolicyDecision(dict):
    """Deterministic policy outcome; shadow callers record, never execute."""


def evaluate_action(
    request: ActionRequest,
    *,
    active_mandates: List[Mapping[str, Any]],
    budget_usage_today: Optional[Mapping[str, float]] = None,
    mandate_action_counts: Optional[Mapping[str, int]] = None,
) -> PolicyDecision:
    """Evaluate one action against active mandates deterministically.

    ``budget_usage_today`` maps mandate_id -> already committed total
    (settled + reserved) for the current business day.
    ``mandate_action_counts`` maps mandate_id -> actions authorized
    today (for ``max_actions``). Neither input is fetched here so the
    function stays pure and testable.
    """
    usage = dict(budget_usage_today or {})
    counts = dict(mandate_action_counts or {})
    reasons: List[str] = []
    if request.amount is not None and request.amount.state == PRICE_STATE_KNOWN and request.amount.amount < 0:
        return PolicyDecision(
            allowed=False,
            authorization_type="mandate",
            reasons=["negative amounts are never authorizable"],
            matched_mandate=None,
            checks=[{"check": "non_negative_amount", "state": "blocked"}],
        )

    for mandate in active_mandates:
        checks = _check_mandate(request, mandate, usage, counts)
        blocked = [c for c in checks if c["state"] == "blocked"]
        if not blocked:
            return PolicyDecision(
                allowed=True,
                authorization_type="mandate",
                reasons=[c["note"] for c in checks],
                matched_mandate={
                    "mandate_id": mandate.get("record_id"),
                    "revision": mandate.get("revision"),
                    "valid_until": mandate.get("valid_until"),
                },
                checks=checks,
            )
        reasons.extend(f"[{mandate.get('record_id')}] " + c["note"] for c in blocked)

    return PolicyDecision(
        allowed=False,
        authorization_type="mandate",
        reasons=reasons or ["no active mandate matches this action"],
        matched_mandate=None,
        checks=[{"check": "no_matching_mandate", "state": "blocked"}],
    )


def _check_mandate(
    request: ActionRequest,
    mandate: Mapping[str, Any],
    usage: Mapping[str, float],
    counts: Mapping[str, int],
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []

    def add(name: str, passed: bool, note: str) -> None:
        checks.append({"check": name, "state": "pass" if passed else "blocked", "note": note})

    add("account", str(mandate.get("account_id")) == request.account_id, f"account {mandate.get('account_id')} vs {request.account_id}")
    add("action_type", request.action_type in (mandate.get("action_types") or []), f"action {request.action_type} not in mandate actions")

    sku_scope = mandate.get("sku_scope") or []
    if sku_scope:
        add("sku_scope", request.sku in sku_scope, f"sku {request.sku or '(none)'} outside scope")
    category_scope = mandate.get("category_scope") or []
    if category_scope:
        add("category_scope", request.category in category_scope, f"category {request.category or '(none)'} outside scope")

    if request.action_type in ACTION_TYPES_REQUIRING_AMOUNT or request.amount is not None:
        per_txn = money_from_mapping(mandate.get("per_transaction_limit"), field_name="per_transaction_limit")
        if request.amount is None or request.amount.state != PRICE_STATE_KNOWN:
            add("amount_known", False, "action needs a known amount; unknown money is not authorizable")
        elif per_txn.state != PRICE_STATE_KNOWN:
            add("per_transaction_limit", False, "mandate has no known per-transaction limit; refusing money action")
        else:
            add("per_transaction_limit", request.amount.amount <= per_txn.amount, f"{request.amount.amount} > per-txn {per_txn.amount}")

        daily = money_from_mapping(mandate.get("daily_limit"), field_name="daily_limit")
        if daily.state == PRICE_STATE_KNOWN and request.amount is not None and request.amount.state == PRICE_STATE_KNOWN:
            mandate_id = str(mandate.get("record_id"))
            used = usage.get(mandate_id, 0.0)
            projected = used + float(request.amount.amount)
            add("daily_limit", projected <= float(daily.amount), f"projected {projected} > daily {daily.amount}")

    if request.listing_price is not None and request.listing_price.state == PRICE_STATE_KNOWN:
        floor = money_from_mapping(mandate.get("price_floor"), field_name="price_floor")
        ceiling = money_from_mapping(mandate.get("price_ceiling"), field_name="price_ceiling")
        price = request.listing_price.amount
        if floor.state == PRICE_STATE_KNOWN and price < floor.amount:
            add("price_floor", False, f"listing price {price} below mandate floor {floor.amount}")
        if ceiling.state == PRICE_STATE_KNOWN and price > ceiling.amount:
            add("price_ceiling", False, f"listing price {price} above mandate ceiling {ceiling.amount}")

    max_actions = mandate.get("max_actions")
    if max_actions is not None:
        mandate_id = str(mandate.get("record_id"))
        used = counts.get(mandate_id, 0)
        add("max_actions", used < int(max_actions), f"mandate action count {used} >= max {max_actions}")

    return checks


def today_key(*, now: Optional[datetime] = None) -> str:
    """Business-day key for budget/limit accounting (Asia/Shanghai)."""
    return business_day(now or utc_now())
