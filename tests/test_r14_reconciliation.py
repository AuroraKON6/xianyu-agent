"""R14 acceptance tests: ledger, payouts and reversals.

Taskbook matrix:
* duplicate receipt idempotent; different-amount conflict REJECTED;
* refunds never double-count (evidence idempotent; reversal caps);
* currencies never mixed (per-currency nets in summary and balance);
* ledger balance nets credits/debits/reversals per currency;
* budget cash: settle replaces the reserve (no double commit); a
  settled (paid) reservation can never be released back to available.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


def money(value, currency="CNY"):
    return {"state": "known", "amount": str(value), "currency": currency}


class LedgerFactIdempotencyTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent.ledger_service import LedgerService

        self.tmp = Path(tempfile.mkdtemp(prefix="r14-ledger-"))
        self.ledger = LedgerService(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_duplicate_receipt_is_idempotent(self):
        first = self.ledger.record_spend(
            category="procurement", amount=money(200), order_ref="O-1",
            evidence_ref="receipt://r14/1", verified=False,
        )
        second = self.ledger.record_spend(
            category="procurement", amount=money(200), order_ref="O-1",
            evidence_ref="receipt://r14/1", verified=False,
        )
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(self.ledger.entries(order_ref="O-1")), 1)

    def test_same_evidence_different_amount_is_conflict(self):
        from xianyu_agent.ledger_service import LedgerError

        self.ledger.record_spend(
            category="procurement", amount=money(200), order_ref="O-1",
            evidence_ref="receipt://r14/2", verified=False,
        )
        with self.assertRaises(LedgerError) as ctx:
            self.ledger.record_spend(
                category="procurement", amount=money(999), order_ref="O-1",
                evidence_ref="receipt://r14/2", verified=False,
            )
        self.assertIn("cannot have two amounts", str(ctx.exception))
        self.assertEqual(len(self.ledger.entries(order_ref="O-1")), 1)

    def test_income_replay_conflict_rejected_not_silent(self):
        from xianyu_agent.ledger_service import LedgerError

        self.ledger.record_income(
            amount=money(300), order_ref="O-2",
            payout_evidence_ref="payout://r14/1",
        )
        with self.assertRaises(LedgerError):
            self.ledger.record_income(
                amount=money(301), order_ref="O-2",
                payout_evidence_ref="payout://r14/1",
            )
        # exact replay stays idempotent
        same = self.ledger.record_income(
            amount=money(300), order_ref="O-2",
            payout_evidence_ref="payout://r14/1",
        )
        self.assertEqual(len(self.ledger.entries(order_ref="O-2")), 1)
        self.assertTrue(same)

    def test_refund_adjustment_evidence_idempotent(self):
        first = self.ledger.record_adjustment(
            category="refund", direction="credit", amount=money(50),
            order_ref="O-3", evidence_ref="refund://r14/1",
        )
        second = self.ledger.record_adjustment(
            category="refund", direction="credit", amount=money(50),
            order_ref="O-3", evidence_ref="refund://r14/1",
        )
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(self.ledger.entries(order_ref="O-3")), 1)

    def test_reversal_chain_capped_no_double_count(self):
        from xianyu_agent.ledger_service import LedgerError

        spend = self.ledger.record_spend(
            category="procurement", amount=money(200), order_ref="O-4",
            evidence_ref="receipt://r14/4", verified=False,
        )
        self.ledger.reverse(spend["record_id"], reason="供应商全额退款",
                             evidence_ref="refund://r14/4a")
        with self.assertRaises(LedgerError):
            self.ledger.reverse(spend["record_id"], reason="重复冲正",
                                 evidence_ref="refund://r14/4b")
        summary = self.ledger.profit_summary(opportunity_id="")
        rows = self.ledger.entries(order_ref="O-4")
        debits = sum(1 for e in rows if e.get("direction") == "debit")
        credits = sum(1 for e in rows if e.get("direction") == "credit")
        self.assertEqual((debits, credits), (1, 1), "full reversal counted once")


class CurrencySeparationTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent.ledger_service import LedgerService

        self.tmp = Path(tempfile.mkdtemp(prefix="r14-ccy-"))
        self.ledger = LedgerService(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_profit_summary_groups_by_currency(self):
        self.ledger.record_spend(category="procurement", amount=money(160),
                                  opportunity_id="OPP-1",
                                  evidence_ref="r14-cny-spend", verified=False)
        self.ledger.record_income(amount=money(25, "USD"), opportunity_id="OPP-1",
                                   payout_evidence_ref="payout://r14/usd")
        summary = self.ledger.profit_summary(opportunity_id="OPP-1")
        settled = summary["settled"]["net_by_currency"]
        provisional = summary["provisional"]["net_by_currency"]
        # USD income landed settled; CNY spend stayed provisional — each
        # currency appears in exactly its own tier, never summed together
        self.assertEqual(settled["USD"], "25")
        self.assertEqual(provisional["CNY"], "-160")
        self.assertNotIn("CNY", settled)
        self.assertTrue(any("never summed" in note for note in summary["notes"]))

    def test_ledger_balance_per_currency(self):
        from xianyu_agent.application.reconciliation import ledger_balance

        self.ledger.record_spend(category="procurement", amount=money(100),
                                  order_ref="O-5", evidence_ref="r14-b-1", verified=False)
        self.ledger.record_income(amount=money(10, "USD"), order_ref="O-5",
                                   payout_evidence_ref="payout://r14/b")
        balance = ledger_balance(self.ledger.entries())
        self.assertEqual(balance["net_by_currency"]["CNY"], "-100")
        self.assertEqual(balance["net_by_currency"]["USD"], "10")


class BudgetCashViewTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent.budget_reservations import BudgetReservationStore

        self.tmp = Path(tempfile.mkdtemp(prefix="r14-budget-"))
        self.store = BudgetReservationStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_settle_replaces_reservation_no_double_commit(self):
        from xianyu_agent.application.reconciliation import budget_cash_view

        record = self.store.reserve(
            account_id="acct-a", budget_bucket="procurement",
            job_id="proc-1", amount=money(100),
        )
        view_before = budget_cash_view(self.store, account_id="acct-a",
                                       bucket="procurement", day=record["business_day"])
        self.assertEqual(Decimal(view_before["committed"]), Decimal("100"))

        self.store.settle(record["record_id"], actual_amount=money(140))
        view_after = budget_cash_view(self.store, account_id="acct-a",
                                      bucket="procurement", day=record["business_day"])
        # settle replaces: reserved 0 + settled 140 = 140 (NOT 100+140)
        self.assertEqual(Decimal(view_after["reserved"]), Decimal("0"))
        self.assertEqual(Decimal(view_after["settled_actual"]), Decimal("140"))
        self.assertEqual(Decimal(view_after["committed"]), Decimal("140"))

    def test_settled_reservation_cannot_be_released(self):
        from xianyu_agent.budget_reservations import BudgetError

        record = self.store.reserve(
            account_id="acct-a", budget_bucket="procurement",
            job_id="proc-2", amount=money(100),
        )
        self.store.settle(record["record_id"], actual_amount=money(100))
        with self.assertRaises(BudgetError):
            self.store.release(record["record_id"], reason="paid money is not available again")

    def test_released_reservation_stops_counting(self):
        from xianyu_agent.application.reconciliation import budget_cash_view

        record = self.store.reserve(
            account_id="acct-a", budget_bucket="procurement",
            job_id="proc-3", amount=money(80),
        )
        self.store.release(record["record_id"], reason="cancelled")
        view = budget_cash_view(self.store, account_id="acct-a",
                                bucket="procurement", day=record["business_day"])
        self.assertEqual(Decimal(view["committed"]), Decimal("0"))
        self.assertEqual(Decimal(view["released"]), Decimal("80"))


class OrderReconciliationTests(unittest.TestCase):
    def test_receipt_without_payment_stays_visibly_pending(self):
        from xianyu_agent.application.reconciliation import order_reconciliation
        from xianyu_agent.ledger_service import LedgerService

        tmp = Path(tempfile.mkdtemp(prefix="r14-orderrec-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        ledger = LedgerService(tmp)
        ledger.record_spend(category="procurement", amount=money(200),
                            order_ref="O-9", evidence_ref="receipt://r14/9",
                            verified=False)
        card = order_reconciliation(ledger, order_ref="O-9")
        self.assertEqual(len(card["receipts"]), 1)
        self.assertEqual(card["verified_payouts"], [])
        self.assertTrue(any("no verified payment" in p for p in card["pending_reconciliation"]))


if __name__ == "__main__":
    unittest.main()
