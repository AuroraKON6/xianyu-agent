"""Business contracts for the commerce domain (WP01).

These contracts are the stable, serialized shapes for the new commerce
entities: OfferSnapshot, ProductMatch, Opportunity, AgentJob,
DecisionRecord, OperatingMandate, AuthorizationRecord, BudgetReservation,
ProcurementOrder and LedgerEntry.

Contract rules (master plan section 8 / WP01):
- Money is Decimal + currency, serialized as a decimal string. A float
  never participates in money serialization.
- Money has an explicit tri-state: ``known``, ``unknown`` or
  ``not_applicable``. Unknown money never silently becomes 0 in any
  calculation; helpers propagate unknown instead of guessing.
- Times are timezone-aware UTC datetimes, serialized as ISO-8601 with an
  explicit offset. The business day boundary is Asia/Shanghai (UTC+8,
  no DST in China).
- Every record carries scope, account binding (supplier-side research
  records may be account-independent but must say so), schema_version,
  revision, source_ref and trace_id.
- Records are validated on construction; ``to_dict``/``from_dict`` must
  round-trip losslessly.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

CONTRACTS_SCHEMA_VERSION = 1

SCOPE_DOMESTIC_XIANYU = "domestic_xianyu"

PRICE_STATE_KNOWN = "known"
PRICE_STATE_UNKNOWN = "unknown"
PRICE_STATE_NOT_APPLICABLE = "not_applicable"

_SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:16]}"


def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{field_name} must be timezone-aware (UTC)")
    return value.astimezone(timezone.utc)


def business_day(value: datetime) -> str:
    """Return the Asia/Shanghai business day (YYYY-MM-DD) of a UTC instant."""
    return require_utc(value, "value").astimezone(_SHANGHAI_TZ).strftime("%Y-%m-%d")


class ContractError(ValueError):
    """Base error for contract construction and validation problems."""


class CurrencyMismatch(ContractError):
    pass


@dataclass(frozen=True)
class Money:
    """Tri-state amount: known / unknown / not_applicable.

    ``amount`` is None unless state is ``known``. Unknown money means the
    fact was not observable; not_applicable means the cost concept does
    not apply to this record. Neither is ever treated as zero.
    """

    state: str = PRICE_STATE_UNKNOWN
    amount: Optional[Decimal] = None
    currency: str = "CNY"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in (PRICE_STATE_KNOWN, PRICE_STATE_UNKNOWN, PRICE_STATE_NOT_APPLICABLE):
            raise ContractError(f"invalid money state: {self.state!r}")
        if self.state == PRICE_STATE_KNOWN:
            if not isinstance(self.amount, Decimal):
                raise ContractError("known money requires a Decimal amount")
            if not self.amount.is_finite():
                raise ContractError("money amount must be finite")
        elif self.amount is not None:
            raise ContractError("non-known money must not carry an amount")
        if self.currency and len(self.currency) != 3:
            raise ContractError("currency must be an ISO-4217-like 3-letter code")

    @staticmethod
    def known(amount: Any, currency: str = "CNY") -> "Money":
        if isinstance(amount, float):
            raise ContractError("float money is not accepted; pass str or Decimal")
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        return Money(state=PRICE_STATE_KNOWN, amount=amount, currency=currency)

    @staticmethod
    def unknown(reason: str = "") -> "Money":
        return Money(state=PRICE_STATE_UNKNOWN, reason=reason)

    @staticmethod
    def not_applicable() -> "Money":
        return Money(state=PRICE_STATE_NOT_APPLICABLE)

    def add(self, other: "Money") -> "Money":
        """Sum two money values, propagating unknown over applicable rules.

        unknown dominates (one unknown input -> unknown result);
        not_applicable only survives when every input is not_applicable.
        A known + not_applicable is a contract misuse and raises.
        """
        if not isinstance(other, Money):
            raise ContractError("can only add Money to Money")
        if PRICE_STATE_UNKNOWN in (self.state, other.state):
            reasons = [r for r in (self.reason, other.reason) if r]
            return Money.unknown("; ".join(reasons))
        if self.state == PRICE_STATE_NOT_APPLICABLE and other.state == PRICE_STATE_NOT_APPLICABLE:
            return Money.not_applicable()
        if PRICE_STATE_NOT_APPLICABLE in (self.state, other.state):
            raise ContractError("cannot add not_applicable money to known money")
        if self.currency != other.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")
        return Money.known(self.amount + other.amount, self.currency)  # type: ignore[union-attr]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "amount": format(self.amount, "f") if self.amount is not None else None,
            "currency": self.currency if self.state == PRICE_STATE_KNOWN else None,
            "reason": self.reason or None,
        }

    @staticmethod
    def from_dict(value: Mapping[str, Any]) -> "Money":
        state = value.get("state") or PRICE_STATE_UNKNOWN
        raw_amount = value.get("amount")
        amount = Decimal(str(raw_amount)) if raw_amount is not None else None
        return Money(
            state=state,
            amount=amount,
            currency=str(value.get("currency") or "CNY"),
            reason=str(value.get("reason") or ""),
        )


def money_from_mapping(value: Optional[Mapping[str, Any]], *, field_name: str) -> Money:
    if value is None:
        return Money.unknown(f"{field_name} not provided")
    if isinstance(value, Money):
        return value
    return Money.from_dict(value)


# ---------------------------------------------------------------------------
# Shared record envelope helpers
# ---------------------------------------------------------------------------


def record_envelope(
    *,
    record_type: str,
    record_id: str,
    scope: str,
    account_id: str,
    source_ref: str,
    trace_id: str,
    created_at: datetime,
    revision: int,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "record_type": record_type,
        "record_id": record_id,
        "scope": scope,
        "account_id": account_id,
        "source_ref": source_ref,
        "trace_id": trace_id,
        "schema_version": CONTRACTS_SCHEMA_VERSION,
        "revision": revision,
        "created_at": require_utc(created_at, "created_at").isoformat(),
    }
    if extra:
        base.update(extra)
    return base


def envelope_id(record: Mapping[str, Any]) -> str:
    """Stable natural identity used for uniqueness checks (plan section 11.1)."""
    return str(record.get("record_id") or "")


# ---------------------------------------------------------------------------
# OfferSnapshot
# ---------------------------------------------------------------------------

PRICE_TYPE_LIST = "list_price_display"
PRICE_TYPE_SKU = "sku_price"
PRICE_TYPE_COUPON_CONDITIONAL = "coupon_conditional"
PRICE_TYPE_TIERED = "tiered"
PRICE_TYPE_SUPPLIER_CONFIRMED = "supplier_confirmed"
PRICE_TYPE_OWN_TRANSACTION = "own_transaction"

STOCK_STATE_IN_STOCK = "in_stock"
STOCK_STATE_LOW = "low"
STOCK_STATE_OUT = "out_of_stock"
STOCK_STATE_UNKNOWN = "unknown"

OBSERVABLE_FIELDS = (
    "provider_id",
    "source_item_id",
    "source_sku_id",
    "observed_at",
)


def build_offer_snapshot(
    *,
    provider_id: str,
    source_item_id: str,
    source_sku_id: str = "",
    source_url: str = "",
    observed_at: datetime,
    fetched_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    unit: str = "",
    quantity: Optional[int] = None,
    condition: str = "",
    attributes: Optional[Mapping[str, str]] = None,
    price_type: str = PRICE_TYPE_SKU,
    list_price: Optional[Mapping[str, Any]] = None,
    eligible_price: Optional[Mapping[str, Any]] = None,
    eligibility_conditions: Optional[List[str]] = None,
    shipping_quote: Optional[Mapping[str, Any]] = None,
    destination_region: str = "",
    stock_state: str = STOCK_STATE_UNKNOWN,
    stock_quantity: Optional[int] = None,
    moq: Optional[int] = None,
    tier_prices: Optional[List[Mapping[str, Any]]] = None,
    tax_mode: str = "unknown",
    seller_ref: str = "",
    evidence_ref: str = "",
    payload_hash: str,
    scope: str = SCOPE_DOMESTIC_XIANYU,
    account_id: str = "",
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build one immutable offer snapshot record.

    Natural key: provider + source item + source sku + observed_at +
    payload_hash (master plan 11.1). Supplier-side research offers may use
    an empty ``account_id`` but then must carry ``source_ref`` describing
    the import batch or observation that produced them.
    """
    for name, value in (("provider_id", provider_id), ("source_item_id", source_item_id), ("payload_hash", payload_hash)):
        if not str(value).strip():
            raise ContractError(f"offer snapshot requires non-empty {name}")
    if not provider_id.strip() or not source_item_id.strip():
        raise ContractError("offer snapshot requires provider and item identity")
    observed_at = require_utc(observed_at, "observed_at")
    now = created_at or utc_now()
    if fetched_at is not None:
        fetched_at = require_utc(fetched_at, "fetched_at")
    if expires_at is not None:
        expires_at = require_utc(expires_at, "expires_at")
    if not trace_id:
        trace_id = new_trace_id()
    if not record_id:
        record_id = new_id("offer")

    eligible = money_from_mapping(eligible_price, field_name="eligible_price")
    listed = money_from_mapping(list_price, field_name="list_price")
    shipping = money_from_mapping(shipping_quote, field_name="shipping_quote")

    return record_envelope(
        record_type="offer_snapshot",
        record_id=record_id,
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id,
        created_at=now,
        revision=1,
        extra={
            "provider_id": provider_id,
            "source_item_id": source_item_id,
            "source_sku_id": source_sku_id,
            "source_url": source_url,
            "observed_at": observed_at.isoformat(),
            "fetched_at": fetched_at.isoformat() if fetched_at else None,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "unit": unit,
            "quantity": quantity,
            "condition": condition,
            "attributes": dict(attributes or {}),
            "price_type": price_type,
            "list_price": listed.to_dict(),
            "eligible_price": eligible.to_dict(),
            "eligibility_conditions": list(eligibility_conditions or []),
            "shipping_quote": shipping.to_dict(),
            "destination_region": destination_region,
            "stock_state": stock_state,
            "stock_quantity": stock_quantity,
            "moq": moq,
            "tier_prices": [dict(t) for t in (tier_prices or [])],
            "tax_mode": tax_mode,
            "seller_ref": seller_ref,
            "evidence_ref": evidence_ref,
            "payload_hash": payload_hash,
            "business_day": business_day(observed_at),
        },
    )


def offer_natural_key(record: Mapping[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(record.get("provider_id") or ""),
        str(record.get("source_item_id") or ""),
        str(record.get("source_sku_id") or ""),
        str(record.get("observed_at") or ""),
        str(record.get("payload_hash") or ""),
    )


# ---------------------------------------------------------------------------
# ProductMatch
# ---------------------------------------------------------------------------

MATCH_EXACT = "EXACT"
MATCH_COMPARABLE = "COMPARABLE"
MATCH_NOT_MATCH = "NOT_MATCH"
MATCH_UNKNOWN = "UNKNOWN"
MATCH_GRADES = (MATCH_EXACT, MATCH_COMPARABLE, MATCH_NOT_MATCH, MATCH_UNKNOWN)


def build_product_match(
    *,
    left_snapshot_id: str,
    right_snapshot_id: str,
    grade: str,
    rule_version: str,
    hard_constraints: Optional[List[Mapping[str, Any]]] = None,
    conflicts: Optional[List[Mapping[str, Any]]] = None,
    recall_score: Optional[float] = None,
    explanation: str = "",
    human_correction: Optional[Mapping[str, Any]] = None,
    left_snapshot_revision: Optional[int] = None,
    right_snapshot_revision: Optional[int] = None,
    scope: str = SCOPE_DOMESTIC_XIANYU,
    account_id: str = "",
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if grade not in MATCH_GRADES:
        raise ContractError(f"invalid match grade {grade!r}; expected one of {MATCH_GRADES}")
    if not left_snapshot_id or not right_snapshot_id:
        raise ContractError("product match requires both snapshot ids")
    if left_snapshot_id == right_snapshot_id:
        raise ContractError("cannot match a snapshot to itself")
    return record_envelope(
        record_type="product_match",
        record_id=record_id or new_id("match"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=1,
        extra={
            "left_snapshot_id": left_snapshot_id,
            "right_snapshot_id": right_snapshot_id,
            "left_snapshot_revision": left_snapshot_revision,
            "right_snapshot_revision": right_snapshot_revision,
            "grade": grade,
            "rule_version": rule_version,
            "hard_constraints": [dict(c) for c in (hard_constraints or [])],
            "conflicts": [dict(c) for c in (conflicts or [])],
            "recall_score": recall_score,
            "explanation": explanation,
            "human_correction": dict(human_correction) if human_correction else None,
        },
    )


def match_pair_key(record: Mapping[str, Any]) -> Tuple[str, str, str]:
    left = str(record.get("left_snapshot_id") or "")
    right = str(record.get("right_snapshot_id") or "")
    return (left, right, str(record.get("rule_version") or ""))


# ---------------------------------------------------------------------------
# Opportunity
# ---------------------------------------------------------------------------

OPPORTUNITY_DISCOVERED = "discovered"
OPPORTUNITY_RESEARCHING = "researching"
OPPORTUNITY_READY_FOR_TEST = "ready_for_test"
OPPORTUNITY_TESTING = "testing"
OPPORTUNITY_VALIDATED = "validated"
OPPORTUNITY_PAUSED = "paused"
OPPORTUNITY_REJECTED = "rejected"

OPPORTUNITY_TRANSITIONS: Dict[str, set] = {
    OPPORTUNITY_DISCOVERED: {OPPORTUNITY_RESEARCHING, OPPORTUNITY_REJECTED},
    OPPORTUNITY_RESEARCHING: {OPPORTUNITY_READY_FOR_TEST, OPPORTUNITY_PAUSED, OPPORTUNITY_REJECTED},
    OPPORTUNITY_READY_FOR_TEST: {OPPORTUNITY_TESTING, OPPORTUNITY_PAUSED, OPPORTUNITY_REJECTED},
    OPPORTUNITY_TESTING: {OPPORTUNITY_VALIDATED, OPPORTUNITY_PAUSED, OPPORTUNITY_REJECTED},
    OPPORTUNITY_PAUSED: {OPPORTUNITY_RESEARCHING, OPPORTUNITY_REJECTED},
    OPPORTUNITY_VALIDATED: set(),
    OPPORTUNITY_REJECTED: set(),
}


def build_opportunity(
    *,
    canonical_sku: str,
    source: str,
    title: str = "",
    match_id: str = "",
    offer_snapshot_ids: Optional[Sequence[str]] = None,
    market_snapshot_id: str = "",
    status: str = OPPORTUNITY_DISCOVERED,
    scope: str = SCOPE_DOMESTIC_XIANYU,
    account_id: str = "",
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not canonical_sku.strip() or not source.strip():
        raise ContractError("opportunity requires canonical_sku and source")
    if status not in OPPORTUNITY_TRANSITIONS:
        raise ContractError(f"invalid opportunity status {status!r}")
    now = created_at or utc_now()
    return record_envelope(
        record_type="opportunity",
        record_id=record_id or new_id("opp"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=now,
        revision=1,
        extra={
            "canonical_sku": canonical_sku,
            "source": source,
            "title": title,
            "match_id": match_id,
            "offer_snapshot_ids": list(offer_snapshot_ids or []),
            "market_snapshot_id": market_snapshot_id,
            "status": status,
            "status_reason": "",
            "economics_ref": None,
            "decision_ids": [],
            "history": [
                {
                    "at": now.isoformat(),
                    "from": None,
                    "to": status,
                    "reason": "created",
                    "evidence_refs": [],
                    "actor": "system",
                }
            ],
        },
    )


def opportunity_natural_key(record: Mapping[str, Any]) -> Tuple[str, str]:
    return (str(record.get("canonical_sku") or ""), str(record.get("source") or ""))


def apply_opportunity_transition(
    record: Dict[str, Any],
    *,
    to_status: str,
    reason: str,
    evidence_refs: Optional[Sequence[str]] = None,
    actor: str = "system",
    at: Optional[datetime] = None,
) -> Dict[str, Any]:
    current = str(record.get("status"))
    if to_status not in OPPORTUNITY_TRANSITIONS:
        raise ContractError(f"invalid opportunity status {to_status!r}")
    allowed = OPPORTUNITY_TRANSITIONS.get(current, set())
    if to_status not in allowed:
        raise ContractError(f"cannot transition opportunity from {current} to {to_status}")
    if not reason.strip():
        raise ContractError("opportunity transition requires a reason")
    moment = (at or utc_now()).isoformat()
    record["status"] = to_status
    record["status_reason"] = reason
    record["revision"] = int(record.get("revision") or 1) + 1
    record.setdefault("history", []).append(
        {
            "at": moment,
            "from": current,
            "to": to_status,
            "reason": reason,
            "evidence_refs": list(evidence_refs or []),
            "actor": actor,
        }
    )
    return record


# ---------------------------------------------------------------------------
# AgentJob (contract shape only; execution engine is WP07)
# ---------------------------------------------------------------------------

AGENT_JOB_STATUSES = (
    "queued",
    "leased",
    "running",
    "awaiting_input",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "unknown",
)


def build_agent_job(
    *,
    role: str,
    goal_id: str = "",
    project_id: str = "",
    account_id: str = "",
    input_refs: Optional[Sequence[str]] = None,
    input_versions: Optional[Mapping[str, int]] = None,
    priority: int = 5,
    deadline: Optional[datetime] = None,
    status: str = "queued",
    attempt: int = 0,
    lease_owner: str = "",
    lease_until: Optional[datetime] = None,
    fencing_token: int = 0,
    model_budget: Optional[Mapping[str, Any]] = None,
    parent_job_id: str = "",
    idempotency_key: str = "",
    scope: str = SCOPE_DOMESTIC_XIANYU,
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not role.strip():
        raise ContractError("agent job requires a role")
    if status not in AGENT_JOB_STATUSES:
        raise ContractError(f"invalid agent job status {status!r}")
    return record_envelope(
        record_type="agent_job",
        record_id=record_id or new_id("job"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=1,
        extra={
            "role": role,
            "goal_id": goal_id,
            "project_id": project_id,
            "input_refs": list(input_refs or []),
            "input_versions": dict(input_versions or {}),
            "priority": priority,
            "deadline": deadline.isoformat() if deadline else None,
            "status": status,
            "attempt": attempt,
            "lease_owner": lease_owner,
            "lease_until": lease_until.isoformat() if lease_until else None,
            "fencing_token": fencing_token,
            "model_budget": dict(model_budget or {}),
            "parent_job_id": parent_job_id,
            "idempotency_key": idempotency_key,
        },
    )


# ---------------------------------------------------------------------------
# DecisionRecord (immutable decision card)
# ---------------------------------------------------------------------------


def build_decision_record(
    *,
    summary: str,
    actor_role: str,
    decision_type: str,
    target_ref: str = "",
    opportunity_id: str = "",
    options: Optional[List[Mapping[str, Any]]] = None,
    recommendation: str = "",
    evidence_refs: Optional[List[Mapping[str, Any]]] = None,
    input_versions: Optional[Mapping[str, Any]] = None,
    constraint_checks: Optional[List[Mapping[str, Any]]] = None,
    unknowns: Optional[List[str]] = None,
    model_version: str = "",
    cost: Optional[Mapping[str, Any]] = None,
    authorization_type: str = "human",
    mandate_ref: str = "",
    result: Optional[Mapping[str, Any]] = None,
    scope: str = SCOPE_DOMESTIC_XIANYU,
    account_id: str = "",
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not summary.strip() or not actor_role.strip() or not decision_type.strip():
        raise ContractError("decision record requires summary, actor_role and decision_type")
    if authorization_type not in ("human", "mandate"):
        raise ContractError("authorization_type must be human or mandate")
    if authorization_type == "mandate" and not mandate_ref:
        raise ContractError("mandate authorization requires mandate_ref")
    return record_envelope(
        record_type="decision_record",
        record_id=record_id or new_id("dec"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=1,
        extra={
            "summary": summary,
            "actor_role": actor_role,
            "decision_type": decision_type,
            "target_ref": target_ref,
            "opportunity_id": opportunity_id,
            "options": [dict(o) for o in (options or [])],
            "recommendation": recommendation,
            "evidence_refs": [dict(e) for e in (evidence_refs or [])],
            "input_versions": dict(input_versions or {}),
            "constraint_checks": [dict(c) for c in (constraint_checks or [])],
            "unknowns": list(unknowns or []),
            "model_version": model_version,
            "cost": dict(cost or {}),
            "authorization_type": authorization_type,
            "mandate_ref": mandate_ref,
            "result": dict(result) if result else None,
        },
    )


# ---------------------------------------------------------------------------
# OperatingMandate / AuthorizationRecord / BudgetReservation (WP10 shapes)
# ---------------------------------------------------------------------------


def build_operating_mandate(
    *,
    operator: str,
    account_id: str,
    action_types: Sequence[str],
    sku_scope: Optional[Sequence[str]] = None,
    category_scope: Optional[Sequence[str]] = None,
    price_floor: Optional[Mapping[str, Any]] = None,
    price_ceiling: Optional[Mapping[str, Any]] = None,
    per_transaction_limit: Optional[Mapping[str, Any]] = None,
    daily_limit: Optional[Mapping[str, Any]] = None,
    max_actions: Optional[int] = None,
    valid_from: Optional[datetime] = None,
    valid_until: datetime,
    suppliers: Optional[Sequence[str]] = None,
    max_jobs: Optional[int] = None,
    revision: int = 1,
    mandate_id: str = "",
    scope: str = SCOPE_DOMESTIC_XIANYU,
    source_ref: str = "",
    trace_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not operator.strip() or not account_id:
        raise ContractError("mandate requires operator and account binding")
    if not action_types:
        raise ContractError("mandate must enumerate allowed action types")
    valid_until = require_utc(valid_until, "valid_until")
    if valid_from is not None:
        valid_from = require_utc(valid_from, "valid_from")
        if valid_from >= valid_until:
            raise ContractError("mandate valid_from must precede valid_until")
    return record_envelope(
        record_type="operating_mandate",
        record_id=mandate_id or new_id("mandate"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=revision,
        extra={
            "operator": operator,
            "action_types": list(action_types),
            "sku_scope": list(sku_scope or []),
            "category_scope": list(category_scope or []),
            "price_floor": money_from_mapping(price_floor, field_name="price_floor").to_dict(),
            "price_ceiling": money_from_mapping(price_ceiling, field_name="price_ceiling").to_dict(),
            "per_transaction_limit": money_from_mapping(per_transaction_limit, field_name="per_transaction_limit").to_dict(),
            "daily_limit": money_from_mapping(daily_limit, field_name="daily_limit").to_dict(),
            "max_actions": max_actions,
            "valid_from": valid_from.isoformat() if valid_from else None,
            "valid_until": valid_until.isoformat(),
            "suppliers": list(suppliers or []),
            "max_jobs": max_jobs,
            "revoked_at": None,
            "revoked_by": None,
        },
    )


def build_authorization_record(
    *,
    decision_id: str,
    action_digest: str,
    authorization_type: str = "human",
    mandate_id: str = "",
    mandate_revision: Optional[int] = None,
    account_id: str = "",
    scope: str = SCOPE_DOMESTIC_XIANYU,
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not decision_id.strip() or not action_digest.strip():
        raise ContractError("authorization record requires decision and action digest")
    if authorization_type not in ("human", "mandate"):
        raise ContractError("authorization_type must be human or mandate")
    if authorization_type == "mandate" and (not mandate_id or mandate_revision is None):
        raise ContractError("mandate authorization requires mandate id and revision")
    return record_envelope(
        record_type="authorization_record",
        record_id=record_id or new_id("auth"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=1,
        extra={
            "decision_id": decision_id,
            "action_digest": action_digest,
            "authorization_type": authorization_type,
            "mandate_id": mandate_id,
            "mandate_revision": mandate_revision,
        },
    )


def build_budget_reservation(
    *,
    account_id: str,
    job_id: str,
    budget_bucket: str,
    amount: Optional[Mapping[str, Any]] = None,
    status: str = "reserved",
    scope: str = SCOPE_DOMESTIC_XIANYU,
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if status not in ("reserved", "settled", "released"):
        raise ContractError(f"invalid budget reservation status {status!r}")
    money = money_from_mapping(amount, field_name="amount")
    if status != "reserved" and money.state != PRICE_STATE_KNOWN:
        raise ContractError("settle/release requires a known amount")
    return record_envelope(
        record_type="budget_reservation",
        record_id=record_id or new_id("bres"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=1,
        extra={
            "job_id": job_id,
            "budget_bucket": budget_bucket,
            "amount": money.to_dict(),
            "status": status,
            "released_reason": None,
        },
    )


# ---------------------------------------------------------------------------
# ProcurementOrder / LedgerEntry (WP13 shapes)
# ---------------------------------------------------------------------------

PROCUREMENT_STATUSES = (
    "draft",
    "awaiting_approval",
    "approved",
    "ordered",
    "supplier_confirmed",
    "shipped",
    "received",
    "cancelled",
    "failed",
)


def build_procurement_order(
    *,
    supplier_ref: str,
    canonical_sku: str,
    quantity: int,
    unit_cost: Optional[Mapping[str, Any]] = None,
    shipping_cost: Optional[Mapping[str, Any]] = None,
    sales_order_ref: str = "",
    opportunity_id: str = "",
    status: str = "draft",
    supplier_order_id: str = "",
    scope: str = SCOPE_DOMESTIC_XIANYU,
    account_id: str = "",
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not supplier_ref.strip() or not canonical_sku.strip():
        raise ContractError("procurement order requires supplier and sku")
    if quantity <= 0:
        raise ContractError("procurement quantity must be positive")
    if status not in PROCUREMENT_STATUSES:
        raise ContractError(f"invalid procurement status {status!r}")
    return record_envelope(
        record_type="procurement_order",
        record_id=record_id or new_id("proc"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=1,
        extra={
            "supplier_ref": supplier_ref,
            "canonical_sku": canonical_sku,
            "quantity": quantity,
            "unit_cost": money_from_mapping(unit_cost, field_name="unit_cost").to_dict(),
            "shipping_cost": money_from_mapping(shipping_cost, field_name="shipping_cost").to_dict(),
            "sales_order_ref": sales_order_ref,
            "opportunity_id": opportunity_id,
            "status": status,
            "supplier_order_id": supplier_order_id,
            "receipt_ref": None,
        },
    )


LEDGER_KINDS = ("expected", "provisional", "settled")
LEDGER_KIND_EXPECTED = "expected"
LEDGER_KIND_PROVISIONAL = "provisional"
LEDGER_KIND_SETTLED = "settled"
LEDGER_DIRECTIONS = ("debit", "credit")


def build_ledger_entry(
    *,
    kind: str,
    direction: str,
    category: str,
    amount: Optional[Mapping[str, Any]] = None,
    opportunity_id: str = "",
    order_ref: str = "",
    evidence_ref: str = "",
    reversal_of: str = "",
    scope: str = SCOPE_DOMESTIC_XIANYU,
    account_id: str = "",
    source_ref: str = "",
    trace_id: str = "",
    record_id: str = "",
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if kind not in LEDGER_KINDS:
        raise ContractError(f"invalid ledger kind {kind!r}")
    if direction not in LEDGER_DIRECTIONS:
        raise ContractError(f"invalid ledger direction {direction!r}")
    money = money_from_mapping(amount, field_name="amount")
    if money.state != PRICE_STATE_KNOWN:
        raise ContractError("ledger entries require known amounts; unknown facts stay out of the ledger")
    if not category.strip():
        raise ContractError("ledger entry requires a category")
    return record_envelope(
        record_type="ledger_entry",
        record_id=record_id or new_id("led"),
        scope=scope,
        account_id=account_id,
        source_ref=source_ref,
        trace_id=trace_id or new_trace_id(),
        created_at=created_at or utc_now(),
        revision=1,
        extra={
            "kind": kind,
            "direction": direction,
            "category": category,
            "amount": money.to_dict(),
            "opportunity_id": opportunity_id,
            "order_ref": order_ref,
            "evidence_ref": evidence_ref,
            "reversal_of": reversal_of,
        },
    )
