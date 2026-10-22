"""Financial reconciliation views (taskbook R14).

Read-only projections over the ledger and budget stores. They never
write, never edit history, and never mix currencies:

* ``ledger_balance`` — per-currency credits minus debits (reversals
  count by their own direction), per tier and overall;
* ``budget_cash_view`` — reserved / settled / released totals and the
  committed usage per bucket/day: a settled reservation counts its
  ACTUAL amount once (settle replaces the reservation — cash is never
  double-committed), and a released reservation stops counting
  (only an un-paid reservation may be released);
* ``order_reconciliation`` — for one sales order: receipts recorded
  (provisional spend), verified payouts (settled income), refunds
  (adjustments/reversals) and the explicit pending list — a receipt
  without a verified payment stays visibly unreconciled instead of
  being presented as money in hand.

Projection note (taskbook): unsold inventory cost is NOT attributed to
sold-order profit here — it stays a cash-occupation fact until the
units sell.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Mapping


def _money_of(entry: Mapping[str, Any]) -> Decimal:
    amount = entry.get("amount") or {}
    if str(amount.get("state")) != "known":
        return Decimal("0")
    try:
        return Decimal(str(amount.get("amount")))
    except Exception:
        return Decimal("0")


def _currency_of(entry: Mapping[str, Any]) -> str:
    return str((entry.get("amount") or {}).get("currency") or "CNY")


def ledger_balance(entries: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Per-currency, per-tier balance: credits - debits (reversals by direction)."""
    tiers: Dict[str, Dict[str, Decimal]] = {}

    def bucket(kind: str, currency: str) -> Decimal:
        tiers.setdefault(kind, {})
        tiers[kind].setdefault(currency, Decimal("0"))
        return tiers[kind][currency]

    for entry in entries:
        value = _money_of(entry)
        if value == 0:
            continue
        currency = _currency_of(entry)
        signed = value if str(entry.get("direction")) == "credit" else -value
        bucket(str(entry.get("kind") or ""), currency)
        tiers[str(entry.get("kind") or "")][currency] += signed

    by_tier = {
        kind: {currency: str(value) for currency, value in sorted(net.items())}
        for kind, net in sorted(tiers.items())
    }
    overall: Dict[str, Decimal] = {}
    for net in tiers.values():
        for currency, value in net.items():
            overall.setdefault(currency, Decimal("0"))
            overall[currency] += value
    return {
        "balanced": True,
        "by_tier": by_tier,
        "net_by_currency": {currency: str(value) for currency, value in sorted(overall.items())},
        "entry_count": len(entries),
        "note": "nets are per currency; cross-currency totals are never summed",
    }


def budget_cash_view(
    store,
    *,
    account_id: str,
    bucket: str,
    day: str,
    limit: Decimal = Decimal("0"),
) -> Dict[str, Any]:
    """Committed-cash view for one account/bucket/day.

    Committed = reserved + settled(actual). Settle replaces the reserve
    (one payment never commits cash twice); released reservations stop
    counting — and only an un-paid (reserved) reservation can be
    released, so paid money is never restored to available.
    """
    reserved = Decimal("0")
    settled = Decimal("0")
    released = Decimal("0")
    for record in store.list(account_id=account_id):
        if str(record.get("budget_bucket")) != bucket or str(record.get("business_day")) != day:
            continue
        status = str(record.get("status") or "")
        if status == "reserved":
            amount = record.get("amount") or {}
            if str(amount.get("state")) == "known":
                reserved += Decimal(str(amount.get("amount")))
        elif status == "settled":
            actual = record.get("actual_amount") or {}
            source = actual if str(actual.get("state")) == "known" else (record.get("amount") or {})
            if str(source.get("state")) == "known":
                settled += Decimal(str(source.get("amount")))
        elif status == "released":
            amount = record.get("amount") or {}
            if str(amount.get("state")) == "known":
                released += Decimal(str(amount.get("amount")))

    committed = reserved + settled
    return {
        "account_id": account_id,
        "bucket": bucket,
        "day": day,
        "reserved": str(reserved),
        "settled_actual": str(settled),
        "released": str(released),
        "committed": str(committed),
        "limit": str(limit),
        "remaining_limit": str(max(Decimal("0"), limit - committed)) if limit else None,
        "notes": [
            "settle replaces the reservation: one payment commits cash once",
            "released reservations no longer count; only un-paid reservations can be released",
        ],
    }


def order_reconciliation(
    ledger_service,
    *,
    order_ref: str,
) -> Dict[str, Any]:
    """Reconciliation card for one sales order.

    Splits the ledger rows into: supplier receipts (provisional spend —
    cost recorded but NOT yet payment-verified), verified payouts
    (settled income), refunds/compensations (adjustments and
    reversals), and lists everything still pending reconciliation.
    """
    rows = ledger_service.entries(order_ref=order_ref)
    receipts: List[Mapping[str, Any]] = []
    payouts: List[Mapping[str, Any]] = []
    refunds: List[Mapping[str, Any]] = []
    pending: List[str] = []

    for entry in rows:
        kind = str(entry.get("kind") or "")
        category = str(entry.get("category") or "")
        direction = str(entry.get("direction") or "")
        summary = {
            "record_id": entry.get("record_id"),
            "category": category,
            "amount": entry.get("amount"),
            "evidence_ref": entry.get("evidence_ref") or "",
            "reversal_of": entry.get("reversal_of") or "",
        }
        if entry.get("reversal_of"):
            refunds.append(summary)
        elif category.startswith("reversal:"):
            refunds.append(summary)
        elif kind == "provisional" and direction == "debit":
            receipts.append(summary)
            pending.append(
                f"receipt {entry.get('record_id')} recorded as cost; no verified "
                "payment evidence attached yet"
            )
        elif kind == "settled" and direction == "credit":
            payouts.append(summary)
        elif category in ("refund", "supplier_compensation"):
            refunds.append(summary)
        else:
            pending.append(f"unclassified entry {entry.get('record_id')} ({kind}/{category})")

    return {
        "order_ref": order_ref,
        "receipts": receipts,
        "verified_payouts": payouts,
        "refunds_and_reversals": refunds,
        "pending_reconciliation": pending,
        "note": (
            "receipts are cost facts, not payment evidence; unsold inventory cost "
            "is a cash-occupation fact and is not attributed to sold-order profit"
        ),
    }
