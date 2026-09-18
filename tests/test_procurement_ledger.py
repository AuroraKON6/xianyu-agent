"""WP13 procurement and ledger scenario tests.

Each scenario walks real entity transitions and asserts hand-verifiable
numbers: normal order, stockout cancellation, freight damage,
full return, partial refund and payout-timeout.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import build_offer_snapshot
from xianyu_agent.inventory import InventoryAllocationService
from xianyu_agent.ledger_service import LedgerError, LedgerService
from xianyu_agent.procurement import ProcurementError, ProcurementService

UTC = timezone.utc


def money(amount):
    return {"state": "known", "amount": str(amount), "currency": "CNY"}


class ScenarioBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repos = JsonCommerceRepositories(root)
        self.inventory = InventoryAllocationService(root, safety_margin=0)
        self.procurement = ProcurementService(root, self.repos, self.inventory)
        self.ledger = LedgerService(root)
        # import a fresh supplier quote with stock 10
        snapshot = build_offer_snapshot(
            provider_id="supplier_file:S供应商",
            source_item_id="GZ-1200",
            source_sku_id="GZ-1200",
            observed_at=datetime.now(UTC),
            attributes={"name": "不锈钢工作台"},
            condition="全新",
            unit="件",
            price_type="supplier_confirmed",
            eligible_price=money("150"),
            shipping_quote=money("15"),
            stock_state="in_stock",
            stock_quantity=10,
            payload_hash="wp13-base",
            account_id="",
            source_ref="test",
        )
        self.repos.offers.add(snapshot)
        self.inventory.register_supplier_snapshot(account_id="acc-1", sku="GZ-1200", snapshot=snapshot)

    def tearDown(self):
        self.tmp.cleanup()


class NormalOrderScenario(ScenarioBase):
    def test_full_reconciled_order(self):
        draft = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-1001", opportunity_id="opp-1"
        )
        self.assertEqual(draft["status"], "awaiting_approval")
        self.assertEqual(draft["supplier_price_at_creation"], "150")

        self.procurement.approve(draft["record_id"], operator="操作者")
        ordered = self.procurement.record_supplier_order(
            draft["record_id"], supplier_order_id="SO-20260918-001", operator="操作者"
        )
        self.assertEqual(ordered["status"], "ordered")
        received = self.procurement.record_receipt(
            draft["record_id"], receipt_ref="receipt://wp13/normal", operator="操作者"
        )
        self.assertEqual(received["status"], "received")

        # pay with evidence -> settled spend; income with payout evidence -> settled
        self.ledger.record_spend(
            category="procurement", amount=money("150"), order_ref=draft["record_id"],
            opportunity_id="opp-1", payment_evidence_ref="pay://wx/001",
        )
        self.ledger.record_spend(
            category="supplier_freight", amount=money("15"), order_ref=draft["record_id"],
            opportunity_id="opp-1", payment_evidence_ref="pay://wx/001",
        )
        self.ledger.record_income(
            amount=money("299"), order_ref="sale-1001", opportunity_id="opp-1",
            payout_evidence_ref="payout://xianyu/1001",
        )
        summary = self.ledger.profit_summary(
            opportunity_id="opp-1", expected_profit={"state": "known", "amount": "104.03", "currency": "CNY"}
        )
        # hand check: 299 - 150 - 15 = 134
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("134"))
        self.assertEqual(summary["expected"]["amount"], "104.03")
        self.assertEqual(Decimal(summary["provisional"]["net"]), Decimal("0"))


class StockoutCancelScenario(ScenarioBase):
    def test_cannot_create_when_allocation_insufficient(self):
        # sellable base is 10; lock 10 first
        self.inventory.lock_for_order(account_id="acc-1", sku="GZ-1200", order_id="other-order", quantity=10)
        with self.assertRaises(ProcurementError) as ctx:
            self.procurement.create_from_sales_order(
                account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-1002"
            )
        self.assertIn("allocation", str(ctx.exception))

    def test_cancel_before_supplier_order_writes_no_money(self):
        draft = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-1003"
        )
        cancelled = self.procurement.cancel(draft["record_id"], reason="buyer cancelled", operator="操作者")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.ledger.entries(opportunity_id=draft["record_id"]), [])

    def test_cancel_after_supplier_order_refused(self):
        draft = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-1004"
        )
        self.procurement.approve(draft["record_id"], operator="操作者")
        self.procurement.record_supplier_order(draft["record_id"], supplier_order_id="SO-9", operator="操作者")
        with self.assertRaises(ProcurementError):
            self.procurement.cancel(draft["record_id"], reason="late cancel", operator="操作者")


class FreightDamageScenario(ScenarioBase):
    def test_supplier_compensation_adjusts_profit_by_reversal(self):
        spend = self.ledger.record_spend(
            category="procurement", amount=money("165"), order_ref="po-1",
            opportunity_id="opp-2", payment_evidence_ref="pay://1",
        )
        income = self.ledger.record_income(
            amount=money("299"), order_ref="sale-1", opportunity_id="opp-2",
            payout_evidence_ref="payout://1",
        )
        # goods damaged in transit; supplier refunds 30 of the spend
        self.ledger.reverse(spend["record_id"], reason="运损赔付", amount=money("30"), evidence_ref="aftersale://1")
        summary = self.ledger.profit_summary(opportunity_id="opp-2")
        # hand check: 299 - 165 + 30 = 164
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("164"))
        self.assertEqual(summary["settled"]["reversal_count"], 1)
        # original entry never modified
        stored = [e for e in self.ledger.entries(opportunity_id="opp-2") if e["record_id"] == spend["record_id"]]
        self.assertEqual(stored[0]["amount"]["amount"], "165")


class ReturnScenario(ScenarioBase):
    def test_full_return_reverses_income_not_spend(self):
        self.ledger.record_spend(
            category="procurement", amount=money("165"), order_ref="po-2",
            opportunity_id="opp-3", payment_evidence_ref="pay://2",
        )
        income = self.ledger.record_income(
            amount=money("299"), order_ref="sale-2", opportunity_id="opp-3",
            payout_evidence_ref="payout://2",
        )
        self.ledger.reverse(income["record_id"], reason="全额退货退款", evidence_ref="refund://2")
        summary = self.ledger.profit_summary(opportunity_id="opp-3")
        # hand check: income reversed, spend stands: -165
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("-165"))


class PartialRefundScenario(ScenarioBase):
    def test_partial_refund_partial_reversal(self):
        self.ledger.record_spend(
            category="procurement", amount=money("165"), order_ref="po-3",
            opportunity_id="opp-4", payment_evidence_ref="pay://3",
        )
        income = self.ledger.record_income(
            amount=money("299"), order_ref="sale-3", opportunity_id="opp-4",
            payout_evidence_ref="payout://3",
        )
        self.ledger.reverse(income["record_id"], reason="部分退款100", amount=money("100"), evidence_ref="refund://3")
        summary = self.ledger.profit_summary(opportunity_id="opp-4")
        # hand check: 299 - 100 - 165 = 34
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("34"))


class PayoutTimeoutScenario(ScenarioBase):
    def test_income_without_payout_evidence_stays_provisional(self):
        self.ledger.record_spend(
            category="procurement", amount=money("165"), order_ref="po-4",
            opportunity_id="opp-5", payment_evidence_ref="pay://4",
        )
        # signed receipt but payout not settled on the platform yet
        self.ledger.record_income(amount=money("299"), order_ref="sale-4", opportunity_id="opp-5")
        summary = self.ledger.profit_summary(opportunity_id="opp-5")
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("-165"))
        self.assertEqual(Decimal(summary["provisional"]["net"]), Decimal("299"))
        self.assertTrue(any("not money in hand" in note for note in summary["notes"]))


class LedgerIntegrityTests(ScenarioBase):
    def test_unknown_amount_entries_rejected(self):
        with self.assertRaises(Exception):
            self.ledger.record_spend(category="x", amount={"state": "unknown"}, opportunity_id="o")

    def test_reversal_requires_reason_and_existing_entry(self):
        with self.assertRaises(LedgerError):
            self.ledger.reverse("missing", reason="r")
        entry = self.ledger.record_spend(category="x", amount=money("1"), opportunity_id="o")
        with self.assertRaises(LedgerError):
            self.ledger.reverse(entry["record_id"], reason="  ")


if __name__ == "__main__":
    unittest.main()
