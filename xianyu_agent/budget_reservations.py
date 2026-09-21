"""Budget reservations (WP10): atomic reserve / settle / release.

Money that an authorized action intends to spend is reserved before
the action and either settled (actual spent, may differ from the
reservation) or released (action did not happen). Unknown outcomes
keep the reservation until verified -- a reservation is never silently
dropped, and daily usage counts reserved + settled totals so
concurrent actions cannot jointly exceed a mandate's daily limit.

Concurrency: reserve/settle/release are atomic within the process
lock; two reserves that would jointly exceed the limit -> the second
is refused with a budget error, not queued.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from .commerce.contracts import (
    PRICE_STATE_KNOWN,
    Money,
    build_budget_reservation,
    business_day,
    money_from_mapping,
    new_id,
    utc_now,
)
from .commerce.repositories import LockedJsonList

RESERVED = "reserved"
SETTLED = "settled"
RELEASED = "released"


class BudgetError(RuntimeError):
    pass


class BudgetReservationStore:
    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "budget_reservations.json")

    def _usage_locked(self, items: LockedJsonList, *, account_id: str, bucket: str, day: str) -> Decimal:
        """Committed usage for the day: reserved + settled.

        Settled reservations count their ACTUAL settled amount when it
        differs from the reservation (settle-at-140 after reserving 100
        must consume 140 of the limit, not 100).
        """
        total = Decimal("0")
        for record in items._items:
            if (
                str(record.get("account_id")) == account_id
                and str(record.get("budget_bucket")) == bucket
                and str(record.get("business_day")) == day
                and str(record.get("status")) in (RESERVED, SETTLED)
            ):
                status = str(record.get("status"))
                source = record.get("amount") or {}
                if status == SETTLED:
                    actual = record.get("actual_amount") or {}
                    if actual.get("state") == PRICE_STATE_KNOWN:
                        source = actual
                if source.get("state") == PRICE_STATE_KNOWN:
                    total += Decimal(str(source.get("amount")))
        return total

    def reserve(
        self,
        *,
        account_id: str,
        budget_bucket: str,
        job_id: str,
        amount: Optional[Mapping[str, Any]] = None,
        limit: Optional[Mapping[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        money = money_from_mapping(amount, field_name="amount")
        if money.state != PRICE_STATE_KNOWN:
            raise BudgetError("reservations require a known amount; unknown money is not reservable")
        if money.amount < 0:
            raise BudgetError("negative reservations are not allowed")
        moment = now or utc_now()
        day = business_day(moment)
        with self._store as items:
            # idempotent per (job, bucket): one live reservation each
            for record in items._items:
                if (
                    str(record.get("job_id")) == job_id
                    and str(record.get("budget_bucket")) == budget_bucket
                    and str(record.get("status")) == RESERVED
                ):
                    return dict(record)
            if limit is not None:
                limit_money = money_from_mapping(limit, field_name="limit")
                if limit_money.state == PRICE_STATE_KNOWN:
                    projected = self._usage_locked(items, account_id=account_id, bucket=budget_bucket, day=day) + money.amount
                    if projected > limit_money.amount:
                        raise BudgetError(
                            f"daily budget exceeded for {account_id}/{budget_bucket} on {day}: "
                            f"projected {projected} > limit {limit_money.amount}"
                        )
            record = build_budget_reservation(
                account_id=account_id,
                job_id=job_id,
                budget_bucket=budget_bucket,
                amount=money.to_dict(),
                status=RESERVED,
            )
            record["business_day"] = day
            record["record_id"] = new_id("bres")
            items._items.append(record)
            return dict(record)

    def settle(self, reservation_id: str, *, actual_amount: Mapping[str, Any]) -> Dict[str, Any]:
        actual = money_from_mapping(actual_amount, field_name="actual_amount")
        if actual.state != PRICE_STATE_KNOWN:
            raise BudgetError("settling requires the actual known amount; unknown outcome keeps the reservation")

        def mutate(record: Dict[str, Any]) -> None:
            if str(record.get("status")) != RESERVED:
                raise BudgetError(f"cannot settle reservation in status {record.get('status')}")
            record["status"] = SETTLED
            record["actual_amount"] = actual.to_dict()

        return self._update(reservation_id, mutate)

    def release(self, reservation_id: str, *, reason: str) -> Dict[str, Any]:
        if not reason.strip():
            raise BudgetError("release requires a reason")

        def mutate(record: Dict[str, Any]) -> None:
            if str(record.get("status")) != RESERVED:
                raise BudgetError(f"cannot release reservation in status {record.get('status')}")
            record["status"] = RELEASED
            record["released_reason"] = reason
            record["released_at"] = utc_now().isoformat()

        return self._update(reservation_id, mutate)

    def _update(self, reservation_id: str, mutate) -> Dict[str, Any]:
        try:
            return self._store.update(reservation_id, mutate)
        except KeyError as exc:
            raise BudgetError(f"reservation not found: {reservation_id}") from exc

    def usage(self, *, account_id: str, bucket: str, day: Optional[str] = None) -> Dict[str, Any]:
        target_day = day or business_day(utc_now())
        records = self._store.find(
            lambda r: str(r.get("account_id")) == account_id
            and str(r.get("budget_bucket")) == bucket
            and str(r.get("business_day")) == target_day
        )
        reserved = settled = released = Decimal("0")
        for record in records:
            status = str(record.get("status"))
            source = record.get("amount") or {}
            if status == SETTLED:
                actual = record.get("actual_amount") or {}
                if actual.get("state") == PRICE_STATE_KNOWN:
                    source = actual
            if source.get("state") != PRICE_STATE_KNOWN:
                continue
            value = Decimal(str(source.get("amount")))
            if status == RESERVED:
                reserved += value
            elif status == SETTLED:
                settled += value
            elif status == RELEASED:
                released += value
        return {
            "account_id": account_id,
            "bucket": bucket,
            "business_day": target_day,
            "reserved": str(reserved),
            "settled": str(settled),
            "released": str(released),
            "committed_total": str(reserved + settled),
        }

    def list(self, *, account_id: str = "", status: str = "") -> List[Dict[str, Any]]:
        def predicate(record: Mapping[str, Any]) -> bool:
            if account_id and str(record.get("account_id")) != account_id:
                return False
            if status and str(record.get("status")) != status:
                return False
            return True

        return self._store.find(predicate)


# ---------------------------------------------------------------------------
# Shadow authorization log (WP10): record what WOULD happen, execute nothing
# ---------------------------------------------------------------------------


class ShadowAuthorizationLog:
    """Shadow-mode records: the policy verdict without any execution.

    The operator still performs every real action; these records exist
    to accumulate the evidence required before any autonomy is enabled
    (>= 7 days / >= 100 evaluable samples per the plan). Counts are
    facts about records, not readiness claims.
    """

    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "shadow_authorizations.json")

    def record(
        self,
        *,
        decision: Mapping[str, Any],
        action_summary: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        entry = {
            "record_type": "shadow_authorization",
            "record_id": new_id("shadow"),
            "mode": "shadow",
            "business_day": business_day(now or utc_now()),
            "recorded_at": (now or utc_now()).isoformat(),
            "action_summary": action_summary,
            "would_allow": bool(decision.get("allowed")),
            "reasons": list(decision.get("reasons") or []),
            "matched_mandate": decision.get("matched_mandate"),
            "authorization_type": decision.get("authorization_type"),
            "note": "shadow record only; no action was executed by this system",
        }
        return self._store.append(entry)

    def metrics(self) -> Dict[str, Any]:
        records = self._store.all()
        total = len(records)
        allowed = sum(1 for r in records if r.get("would_allow"))
        days = sorted({str(r.get("business_day")) for r in records})
        by_action: Dict[str, Dict[str, int]] = {}
        for record in records:
            summary = str(record.get("action_summary") or "unknown")[:60]
            bucket = by_action.setdefault(summary, {"total": 0, "allowed": 0})
            bucket["total"] += 1
            if record.get("would_allow"):
                bucket["allowed"] += 1
        return {
            "total": total,
            "allowed": allowed,
            "allow_rate": round(allowed / total, 4) if total else None,
            "distinct_days": len(days),
            "days": days,
            "readiness_note": (
                f"n={total} over {len(days)} day(s); the >=7 days / >=100 samples gate "
                "is NOT met unless both numbers say so"
            ),
            "by_action_summary": by_action,
        }
