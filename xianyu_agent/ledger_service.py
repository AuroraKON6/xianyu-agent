"""Append-only ledger and profit reconciliation (WP13 minimal).

Money facts are ledger entries; corrections are REVERSALS, never edits
of the original spend. Profit is reported in three tiers per the plan:

- ``expected``: the pre-sale economics estimate (passed in by
  reference; the ledger never invents it),
- ``provisional``: recorded entries without payout evidence,
- ``settled``: income entries that carry real payout evidence refs.

Signed receipt is NOT payout: income without payout evidence stays
provisional, and provisional profit is never reported as money in
hand.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from .commerce.contracts import (
    LEDGER_KIND_SETTLED,
    build_ledger_entry,
    utc_now,
)
from .commerce.repositories import LockedJsonList


class LedgerError(RuntimeError):
    pass


class LedgerService:
    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "ledger.json")

    # ---- entries ----

    def record_spend(
        self,
        *,
        category: str,
        amount: Mapping[str, Any],
        order_ref: str = "",
        opportunity_id: str = "",
        evidence_ref: str = "",
        payment_evidence_ref: str = "",
    ) -> Dict[str, Any]:
        """Record spend; settled only with real payment evidence."""
        entry = build_ledger_entry(
            kind=LEDGER_KIND_SETTLED if payment_evidence_ref.strip() else "provisional",
            direction="debit",
            category=category,
            amount=amount,
            opportunity_id=opportunity_id,
            order_ref=order_ref,
            evidence_ref=payment_evidence_ref or evidence_ref,
        )
        return self._store.append(entry)

    def record_income(
        self,
        *,
        amount: Mapping[str, Any],
        order_ref: str = "",
        opportunity_id: str = "",
        payout_evidence_ref: str = "",
    ) -> Dict[str, Any]:
        """Record sale income; settled only with real payout evidence."""
        entry = build_ledger_entry(
            kind=LEDGER_KIND_SETTLED if payout_evidence_ref.strip() else "provisional",
            direction="credit",
            category="sale_income",
            amount=amount,
            opportunity_id=opportunity_id,
            order_ref=order_ref,
            evidence_ref=payout_evidence_ref,
        )
        return self._store.append(entry)

    def record_adjustment(
        self,
        *,
        category: str,
        direction: str,
        amount: Mapping[str, Any],
        order_ref: str = "",
        opportunity_id: str = "",
        evidence_ref: str = "",
    ) -> Dict[str, Any]:
        """Record a real adjustment fact (e.g. supplier compensation)."""
        entry = build_ledger_entry(
            kind=LEDGER_KIND_SETTLED,
            direction=direction,
            category=category,
            amount=amount,
            opportunity_id=opportunity_id,
            order_ref=order_ref,
            evidence_ref=evidence_ref,
        )
        return self._store.append(entry)

    def reverse(
        self,
        entry_id: str,
        *,
        reason: str,
        amount: Optional[Mapping[str, Any]] = None,
        evidence_ref: str = "",
    ) -> Dict[str, Any]:
        """Append a reversal (full by default, partial via ``amount``).

        The original entry is never modified; the reversal carries the
        opposite direction so tier nets include the correction.
        """
        if not reason.strip():
            raise LedgerError("reversal requires a reason")
        found = self._store.find(lambda e: str(e.get("record_id")) == entry_id)
        if not found:
            raise LedgerError(f"entry not found: {entry_id}")
        original = found[0]
        opposite = "debit" if str(original.get("direction")) == "credit" else "credit"
        reversal_amount = amount if amount is not None else original.get("amount")
        reversal = build_ledger_entry(
            kind=str(original.get("kind")),
            direction=opposite,
            category=f"reversal:{original.get('category')}",
            amount=reversal_amount,
            opportunity_id=str(original.get("opportunity_id") or ""),
            order_ref=str(original.get("order_ref") or ""),
            evidence_ref=evidence_ref,
            reversal_of=entry_id,
        )
        reversal["reversal_reason"] = reason
        return self._store.append(reversal)

    def entries(self, *, opportunity_id: str = "", order_ref: str = "") -> List[Dict[str, Any]]:
        def predicate(entry: Mapping[str, Any]) -> bool:
            if opportunity_id and str(entry.get("opportunity_id")) != opportunity_id:
                return False
            if order_ref and str(entry.get("order_ref")) != order_ref:
                return False
            return True

        return self._store.find(predicate)

    # ---- profit ----

    def profit_summary(
        self,
        *,
        opportunity_id: str,
        expected_profit: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        rows = self.entries(opportunity_id=opportunity_id)

        def signed(entry: Mapping[str, Any]) -> Decimal:
            value = Decimal(str((entry.get("amount") or {}).get("amount") or "0"))
            return value if str(entry.get("direction")) == "credit" else -value

        def tier(kind: str) -> Dict[str, Any]:
            # reversals carry the opposite direction, so they must be
            # INCLUDED in the net; originals are never rewritten
            selected = [e for e in rows if str(e.get("kind")) == kind]
            originals = [e for e in selected if not e.get("reversal_of")]
            reversals = [e for e in selected if e.get("reversal_of")]
            net = sum((signed(e) for e in selected), Decimal("0"))
            return {
                "net": str(net),
                "original_count": len(originals),
                "reversal_count": len(reversals),
            }

        provisional = tier("provisional")
        settled = tier(LEDGER_KIND_SETTLED)
        result: Dict[str, Any] = {
            "opportunity_id": opportunity_id,
            "expected": dict(expected_profit) if expected_profit else None,
            "provisional": provisional,
            "settled": settled,
            "notes": [],
        }
        if Decimal(provisional["net"]) != 0:
            result["notes"].append("provisional tier has unreconciled entries; not money in hand")
        if expected_profit is None:
            result["notes"].append("expected tier not provided (no economics reference attached)")
        return result
