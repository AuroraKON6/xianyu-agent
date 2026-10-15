"""WP13 procurement and ledger scenario tests.

Each scenario walks real entity transitions and asserts hand-verifiable
numbers: normal order, stockout cancellation, freight damage,
full return, partial refund and payout-timeout.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from quote_fixtures import declared_quote_csv, with_declared_quote_provenance

from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import build_offer_snapshot
from xianyu_agent.inventory import InventoryAllocationService, InventoryError
from xianyu_agent.ledger_service import LedgerError, LedgerService
from xianyu_agent.procurement import ProcurementError, ProcurementService
from xianyu_agent.supplier_import import SupplierImportService

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
        snapshot = with_declared_quote_provenance(build_offer_snapshot(
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
        ))
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


class ProcurementIdempotencyTests(ScenarioBase):
    """P1 regression: duplicate sales orders and missing stock reservation."""

    def test_duplicate_sales_order_rejected_and_stock_reserved(self):
        first = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=8, sales_order_ref="sale-dup"
        )
        self.assertEqual(first["status"], "awaiting_approval")
        # the reservation is visible immediately
        allocation = self.inventory.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["available"], 2)  # 10 registered, 8 reserved
        # duplicate submission is refused
        with self.assertRaises(ProcurementError) as ctx:
            self.procurement.create_from_sales_order(
                account_id="acc-1", sku="GZ-1200", quantity=8, sales_order_ref="sale-dup"
            )
        self.assertIn("already exists", str(ctx.exception))
        # cancelling releases the reservation
        self.procurement.cancel(first["record_id"], reason="buyer cancelled", operator="操作者")
        released = self.inventory.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(released["available"], 10)
        # after cancellation a new procurement for the same order is allowed
        second = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=8, sales_order_ref="sale-dup"
        )
        self.assertEqual(second["status"], "awaiting_approval")


class RevokedBatchIsolationTests(unittest.TestCase):
    """P1 regression: revoked import batches must not drive procurement."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repos = JsonCommerceRepositories(root)
        self.importer = SupplierImportService(self.repos, root)
        self.inventory = InventoryAllocationService(root, safety_margin=0)
        self.procurement = ProcurementService(root, self.repos, self.inventory, self.importer)
        quote = (
            "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
            "GZ-1200,商用不锈钢工作台,150,件,2,10,15,120x60x85cm,众商,全新\n"
        ).encode("utf-8")
        preview = self.importer.preview_bytes(declared_quote_csv(quote), kind="csv")
        self.confirm = self.importer.confirm(
            preview["preview_id"],
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="撤销测试供应商",
            operator="test",
        )
        snapshot = self.confirm["snapshots"][0]
        self.inventory.register_supplier_snapshot(account_id="acc-1", sku="GZ-1200", snapshot=snapshot)

    def tearDown(self):
        self.tmp.cleanup()

    def test_revoked_batch_quote_cannot_procure(self):
        self.importer.revoke_batch(self.confirm["batch"]["record_id"], operator="t", reason="错误版本")
        with self.assertRaises(ProcurementError) as ctx:
            self.procurement.create_from_sales_order(
                account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-r1"
            )
        self.assertIn("no usable supplier offer", str(ctx.exception))

    def test_committed_batch_quote_still_procures(self):
        order = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=2, sales_order_ref="sale-r2"
        )  # quantity >= MOQ(2) per the Codex E4 MOQ gate
        self.assertEqual(order["status"], "awaiting_approval")


class StockExpiryRevalidationTests(ScenarioBase):
    """P1 regression: expired stock must fail new locks at read/lock time."""

    def test_expired_stock_blocks_new_locks_and_allocation_reads_zero(self):
        later = datetime.now(UTC) + timedelta(hours=30)
        # allocation re-validates freshness at read time
        allocation = self.inventory.allocation(account_id="acc-1", sku="GZ-1200", now=later)
        self.assertEqual(allocation["available"], 0)
        self.assertFalse(allocation["stock_current_at_read"])
        self.assertIn("expired", allocation["sellable_reason"])
        # locking is refused with the expiry reason
        with self.assertRaises(InventoryError) as ctx:
            self.inventory.lock_for_order(
                account_id="acc-1", sku="GZ-1200", order_id="late-order", quantity=1, now=later
            )
        self.assertIn("expired", str(ctx.exception))
        # procurement is refused too (allocation check sees 0 at that time)
        with self.assertRaises(ProcurementError):
            self.procurement.create_from_sales_order(
                account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-late", now=later
            )
        # a fresh snapshot unblocks selling again
        fresh = with_declared_quote_provenance(build_offer_snapshot(
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
            payload_hash="fresh-again",
            account_id="",
            source_ref="test",
        ))
        self.repos.offers.add(fresh)
        self.inventory.register_supplier_snapshot(account_id="acc-1", sku="GZ-1200", snapshot=fresh)
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 10)


class ReversalAbuseTests(ScenarioBase):
    """P1 regression: duplicate/oversized reversals must not fake profit."""

    def test_duplicate_full_reversal_rejected(self):
        spend = self.ledger.record_spend(
            category="procurement", amount=money("100"), order_ref="po-x",
            opportunity_id="opp-x", payment_evidence_ref="pay://x",
        )
        first = self.ledger.reverse(spend["record_id"], reason="first reversal")
        self.assertIsNotNone(first)
        with self.assertRaises(LedgerError) as ctx:
            self.ledger.reverse(spend["record_id"], reason="duplicate reversal")
        self.assertIn("exceeds original", str(ctx.exception))
        summary = self.ledger.profit_summary(opportunity_id="opp-x")
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("0"))

    def test_partial_reversals_capped_at_original(self):
        spend = self.ledger.record_spend(
            category="procurement", amount=money("100"), order_ref="po-y",
            opportunity_id="opp-y", payment_evidence_ref="pay://y",
        )
        self.ledger.reverse(spend["record_id"], reason="partial 60", amount=money("60"))
        with self.assertRaises(LedgerError):
            self.ledger.reverse(spend["record_id"], reason="over cap", amount=money("60"))
        # completing to exactly the original is allowed
        self.ledger.reverse(spend["record_id"], reason="remaining 40", amount=money("40"))
        summary = self.ledger.profit_summary(opportunity_id="opp-y")
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("0"))

    def test_reversal_currency_mismatch_rejected(self):
        spend = self.ledger.record_spend(
            category="procurement", amount=money("100"), opportunity_id="opp-z", payment_evidence_ref="p"
        )
        with self.assertRaises(LedgerError) as ctx:
            self.ledger.reverse(
                spend["record_id"], reason="wrong currency", amount={"state": "known", "amount": "100", "currency": "USD"}
            )
        self.assertIn("currency", str(ctx.exception))


class ConcurrentProcurementTests(ScenarioBase):
    """P1 re-verify regression: duplicate-check + reserve + store must be
    one atomic sequence. Two threads submitting the same sales order
    must end with exactly ONE procurement and ONE reservation."""

    def setUp(self):
        super().setUp()
        import sys

        self._old_switch = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # force frequent GIL switches so the race window is hit

    def tearDown(self):
        import sys

        sys.setswitchinterval(self._old_switch)
        super().tearDown()

    def test_concurrent_same_sales_order_creates_single_procurement(self):
        import threading

        for round_no in range(20):
            ref = f"sale-race-{round_no}"
            barrier = threading.Barrier(2)
            outcomes = []
            lock = threading.Lock()

            def worker():
                barrier.wait()
                try:
                    order = self.procurement.create_from_sales_order(
                        account_id="acc-1", sku="GZ-1200", quantity=4, sales_order_ref=ref
                    )
                    with lock:
                        outcomes.append(("ok", order["record_id"]))
                except ProcurementError as exc:
                    with lock:
                        outcomes.append(("rejected", str(exc)))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            created = [o for o in outcomes if o[0] == "ok"]
            rejected = [o for o in outcomes if o[0] == "rejected"]
            self.assertEqual(len(created), 1, f"round {round_no}: outcomes={outcomes}")
            # the loser must lose on the duplicate check, not on stock
            self.assertIn("already exists", rejected[0][1], f"round {round_no}: {outcomes}")
            for_this_ref = [o for o in self.procurement.list() if o["sales_order_ref"] == ref]
            self.assertEqual(len(for_this_ref), 1, f"round {round_no}: duplicate orders on disk")
            allocation = self.inventory.allocation(account_id="acc-1", sku="GZ-1200")
            self.assertEqual(allocation["locked"], 4, f"round {round_no}: locked={allocation['locked']}")
            self.assertEqual(allocation["available"], 6, f"round {round_no}: available={allocation['available']}")
            # release this round's reservation so the next round starts full
            self.procurement.cancel(created[0][1], reason="round teardown", operator="test")
            restored = self.inventory.allocation(account_id="acc-1", sku="GZ-1200")
            self.assertEqual(restored["available"], 10, f"round {round_no}: teardown left {restored['available']}")


class ConcurrentReversalTests(ScenarioBase):
    """P1 re-verify regression: cap check and append must be atomic."""

    def setUp(self):
        super().setUp()
        import sys

        self._old_switch = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # force frequent GIL switches so the race window is hit

    def tearDown(self):
        import sys

        sys.setswitchinterval(self._old_switch)
        super().tearDown()

    def _entry(self, tag):
        return self.ledger.record_spend(
            category="procurement", amount=money("100"), order_ref=f"po-{tag}",
            opportunity_id=f"opp-{tag}", payment_evidence_ref=f"pay://{tag}",
        )

    def test_concurrent_full_reversals_yield_single_reversal(self):
        import threading

        for round_no in range(20):
            tag = f"race-full-{round_no}"
            entry = self._entry(tag)
            barrier = threading.Barrier(2)
            results = []
            lock = threading.Lock()

            def worker():
                barrier.wait()
                try:
                    self.ledger.reverse(entry["record_id"], reason="race")
                    with lock:
                        results.append("ok")
                except LedgerError:
                    with lock:
                        results.append("rejected")

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(
                sorted(results), ["ok", "rejected"], f"round {round_no}: {results}"
            )
            summary = self.ledger.profit_summary(opportunity_id=f"opp-{tag}")
            self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("0"), f"round {round_no}")

    def test_concurrent_partial_reversals_respect_cap(self):
        import threading

        for round_no in range(20):
            tag = f"race-part-{round_no}"
            entry = self._entry(tag)
            barrier = threading.Barrier(2)
            results = []
            lock = threading.Lock()

            def worker():
                barrier.wait()
                try:
                    self.ledger.reverse(entry["record_id"], reason="race", amount=money("60"))
                    with lock:
                        results.append("ok")
                except LedgerError:
                    with lock:
                        results.append("rejected")

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            # 60+60 > 100: at most ONE partial may succeed
            self.assertLessEqual(results.count("ok"), 1, f"round {round_no}: {results}")
            summary = self.ledger.profit_summary(opportunity_id=f"opp-{tag}")
            # net is -100 (no reversal) or -40 (one 60 reversal); never positive
            self.assertLessEqual(Decimal(summary["settled"]["net"]), Decimal("-40"), f"round {round_no}")


class CancelIdempotencyTests(ScenarioBase):
    """P1 re-verify regression: re-cancelling an OLD order must not
    release the NEW order's reservation for the same sales ref."""

    def test_repeat_cancel_of_old_order_keeps_new_reservation(self):
        old = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=8, sales_order_ref="sale-cycle"
        )
        self.procurement.cancel(old["record_id"], reason="first cancel", operator="o")
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 10)

        new = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=8, sales_order_ref="sale-cycle"
        )
        self.assertEqual(new["status"], "awaiting_approval")
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 2)

        # re-cancelling the OLD order must be a no-op for inventory
        repeated = self.procurement.cancel(old["record_id"], reason="stale retry", operator="o")
        self.assertEqual(repeated["status"], "cancelled")
        self.assertEqual(new["status"], "awaiting_approval")
        allocation = self.inventory.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["available"], 2, "stale cancel released the new order's stock")
        self.assertEqual(allocation["locked"], 8)


if __name__ == "__main__":
    unittest.main()
