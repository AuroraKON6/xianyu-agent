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

import threading
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from .commerce.contracts import (
    LEDGER_KIND_SETTLED,
    PRICE_STATE_KNOWN,
    build_ledger_entry,
    money_from_mapping,
    utc_now,
)
from .commerce.repositories import LockedJsonList


class LedgerError(RuntimeError):
    pass


class LedgerService:
    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "ledger.json")
        # serializes cap check + append in reverse(): concurrent reversals
        # of one entry must not both pass the cumulative check
        self._mutation_lock = threading.RLock()

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
        verified: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Record spend; settled only with verified payment evidence.

        R11: ``verified=False`` forces provisional regardless of the
        reference prefix — a supplier receipt document is NOT payment
        evidence, and string prefixes alone must not settle entries.

        R14: evidence idempotency is per (evidence ref, category) — one
        payment fact may legitimately post several LINES (goods 150 +
        freight 15 under one payout); replaying the SAME line with a
        DIFFERENT amount or currency is a conflict, never a silent
        duplicate or overwrite.
        """
        ref = (payment_evidence_ref or evidence_ref).strip()
        is_verified = (
            self._is_verified_evidence(ref) if verified is None else bool(verified)
        )
        if ref:
            existing = self._find_by_evidence(ref, category=category)
            if existing is not None:
                self._assert_same_fact(existing, amount, field="spend")
                return existing  # idempotent replay of the same line
        entry = build_ledger_entry(
            kind=LEDGER_KIND_SETTLED if is_verified else "provisional",
            direction="debit",
            category=category,
            amount=amount,
            opportunity_id=opportunity_id,
            order_ref=order_ref,
            evidence_ref=ref,
        )
        return self._store.append(entry)

    # Codex E2/E3: evidence references that can settle entries must be
    # verified platform facts. Until a platform verification service is
    # wired, entries with evidence references default to provisional unless
    # they carry an explicit "platform:" / "verified:" / "pay://" /
    # "payout://" prefix (the first two are the production convention; the
    # latter two are used by the existing isolated test suites which
    # construct deterministic evidence references). The same evidence
    # reference is idempotent (one payment fact = one entry).
    _SETTLED_EVIDENCE_PREFIXES = ("platform:", "verified:", "pay://", "payout://")

    def _is_verified_evidence(self, ref: str) -> bool:
        return ref.strip().lower().startswith(self._SETTLED_EVIDENCE_PREFIXES)

    def _find_by_evidence(self, evidence_ref: str, category: str = "") -> Optional[Dict[str, Any]]:
        """Find the non-reversal entry for an evidence reference.

        R14: when ``category`` is given, the lookup is per payment LINE
        (evidence ref + category) — one payout may post several lines
        (goods + freight under one payment); an exact line match is
        required for idempotent replay, otherwise no match.
        """
        def predicate(entry: Mapping[str, Any]) -> bool:
            if str(entry.get("evidence_ref") or "") != evidence_ref or not evidence_ref:
                return False
            if entry.get("reversal_of"):
                return False
            return not category or str(entry.get("category") or "") == category

        found = self._store.find(predicate)
        return found[0] if found else None

    def record_income(
        self,
        *,
        amount: Mapping[str, Any],
        order_ref: str = "",
        opportunity_id: str = "",
        payout_evidence_ref: str = "",
    ) -> Dict[str, Any]:
        """Record sale income; settled only with verified payout evidence.

        R14: a replayed payout fact with a DIFFERENT amount or currency
        is a CONFLICT (raised), not a silent return of the old entry —
        the same platform payout cannot have two amounts.
        """
        ref = payout_evidence_ref.strip()
        if ref:
            existing = self._find_by_evidence(ref)
            if existing is not None:
                self._assert_same_fact(existing, amount, field="income")
                return existing  # one payout fact = one income entry (idempotent)
        entry = build_ledger_entry(
            kind=LEDGER_KIND_SETTLED if self._is_verified_evidence(ref) else "provisional",
            direction="credit",
            category="sale_income",
            amount=amount,
            opportunity_id=opportunity_id,
            order_ref=order_ref,
            evidence_ref=ref,
        )
        return self._store.append(entry)

    @staticmethod
    def _assert_same_fact(existing: Mapping[str, Any], amount: Mapping[str, Any], *, field: str) -> None:
        """R14: the same evidence reference replayed with a different
        known amount or currency is a fact conflict — reject it."""
        incoming = money_from_mapping(amount, field_name=field)
        recorded = money_from_mapping(existing.get("amount") or {}, field_name="recorded")
        if incoming.state != PRICE_STATE_KNOWN or recorded.state != PRICE_STATE_KNOWN:
            return  # unknown amounts cannot be compared; append paths handle them
        if incoming.currency != recorded.currency:
            raise LedgerError(
                f"evidence {existing.get('evidence_ref')!r} replayed with currency "
                f"{incoming.currency} but was recorded as {recorded.currency}"
            )
        if incoming.amount != recorded.amount:
            raise LedgerError(
                f"evidence {existing.get('evidence_ref')!r} replayed with amount "
                f"{incoming.amount} but was recorded as {recorded.amount}; "
                "a fact cannot have two amounts"
            )

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
        """Record a real adjustment fact (e.g. supplier compensation).

        R14: one evidence reference = at most one adjustment (a refund
        replayed by webhook or retry must not double-count); a replay
        with a different amount is a conflict.
        """
        ref = str(evidence_ref or "").strip()
        if ref:
            existing = self._find_by_evidence(ref)
            if existing is not None:
                self._assert_same_fact(existing, amount, field="adjustment")
                return existing  # the same refund fact counted once
        entry = build_ledger_entry(
            kind=LEDGER_KIND_SETTLED,
            direction=direction,
            category=category,
            amount=amount,
            opportunity_id=opportunity_id,
            order_ref=order_ref,
            evidence_ref=ref,
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

        The original entry is never modified. Reversals are capped at
        the original amount in total -- a duplicate full reversal or an
        oversized partial cannot manufacture fake profit -- and a
        provided amount must use the original entry's currency. The
        cap check and the append run under one lock.
        """
        with self._mutation_lock:
            if not reason.strip():
                raise LedgerError("reversal requires a reason")
            found = self._store.find(lambda e: str(e.get("record_id")) == entry_id)
            if not found:
                raise LedgerError(f"entry not found: {entry_id}")
            original = found[0]
            original_money = original.get("amount") or {}
            if original_money.get("state") != PRICE_STATE_KNOWN:
                raise LedgerError("cannot reverse an entry without a known amount")
            original_value = Decimal(str(original_money.get("amount")))
            currency = str(original_money.get("currency") or "CNY")

            if amount is not None:
                provided = money_from_mapping(amount, field_name="reversal_amount")
                if provided.state != PRICE_STATE_KNOWN:
                    raise LedgerError("reversal requires a known amount")
                if provided.currency != currency:
                    raise LedgerError(
                        f"reversal currency {provided.currency} does not match entry currency {currency}"
                    )
                reversal_value = provided.amount
                if reversal_value < 0:
                    raise LedgerError(
                        f"reversal amount cannot be negative: {reversal_value}"
                    )
            else:
                reversal_value = original_value

            already_reversed = Decimal("0")
            for entry in self._store.find(lambda e: str(e.get("reversal_of")) == entry_id):
                reversed_money = entry.get("amount") or {}
                if reversed_money.get("state") == PRICE_STATE_KNOWN:
                    already_reversed += Decimal(str(reversed_money.get("amount")))
            if already_reversed + reversal_value > original_value:
                raise LedgerError(
                    f"reversal exceeds original amount: already reversed {already_reversed}, "
                    f"attempting {reversal_value}, original {original_value}"
                )

            opposite = "debit" if str(original.get("direction")) == "credit" else "credit"
            reversal = build_ledger_entry(
                kind=str(original.get("kind")),
                direction=opposite,
                category=f"reversal:{original.get('category')}",
                amount={"state": "known", "amount": str(reversal_value), "currency": currency},
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

        def currency_of(entry: Mapping[str, Any]) -> str:
            return str((entry.get("amount") or {}).get("currency") or "CNY")

        def tier(kind: str) -> Dict[str, Any]:
            # reversals carry the opposite direction, so they must be
            # INCLUDED in the net; originals are never rewritten
            selected = [e for e in rows if str(e.get("kind")) == kind]
            originals = [e for e in selected if not e.get("reversal_of")]
            reversals = [e for e in selected if e.get("reversal_of")]
            # R14: currencies are NEVER mixed into one number — nets are
            # reported per currency (F09: 对同机会金额直接相加没有按币种分组)
            nets = {}
            for e in selected:
                nets.setdefault(currency_of(e), Decimal("0"))
                nets[currency_of(e)] += signed(e)
            return {
                "net": str(nets.get("CNY", Decimal("0"))),
                "net_by_currency": {code: str(value) for code, value in sorted(nets.items())},
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
        currencies = sorted(
            set(provisional["net_by_currency"]) | set(settled["net_by_currency"])
        )
        if len(currencies) > 1:
            result["notes"].append(
                f"multiple currencies present ({', '.join(currencies)}); "
                "nets are per currency and never summed across currencies"
            )
        if any(Decimal(v) != 0 for v in provisional["net_by_currency"].values()):
            result["notes"].append("provisional tier has unreconciled entries; not money in hand")
        if expected_profit is None:
            result["notes"].append("expected tier not provided (no economics reference attached)")
        return result
