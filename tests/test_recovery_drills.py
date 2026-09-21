"""Drill round: cross-file failures, restart recovery, full-flow combination.

All drills run on temporary data dirs with real service instances;
"restart" means constructing fresh service objects over the same files,
which is exactly what a process restart sees.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from xianyu_agent.agent_jobs import (
    JOB_QUEUED,
    JOB_UNKNOWN,
    AgentJobStore,
)
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import build_offer_snapshot, build_product_match
from xianyu_agent.inventory import InventoryAllocationService
from xianyu_agent.ledger_service import LedgerService
from xianyu_agent.opportunity_service import OpportunityService
from xianyu_agent.procurement import ProcurementError, ProcurementService
from xianyu_agent.product_matching import ProductMatchingService
from xianyu_agent.recovery import reconcile_orphan_reservations
from xianyu_agent.supplier_import import SupplierImportService

UTC = timezone.utc


def money(amount):
    return {"state": "known", "amount": str(amount), "currency": "CNY"}


QUOTE_CSV = (
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
    "GZ-1200,商用不锈钢工作台,150,件,2,10,15,120x60x85cm,众商,全新\n"
).encode("utf-8")


class DrillBase(unittest.TestCase):
    """One live pipeline wired over a temp dir; rebuildable to simulate restart."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.build_services()

    def tearDown(self):
        self.tmp.cleanup()

    def build_services(self):
        self.repos = JsonCommerceRepositories(self.root)
        self.importer = SupplierImportService(self.repos, self.root)
        self.inventory = InventoryAllocationService(self.root, safety_margin=0)
        self.procurement = ProcurementService(self.root, self.repos, self.inventory, self.importer)
        self.ledger = LedgerService(self.root)
        self.matcher = ProductMatchingService(self.repos)
        self.opportunities = OpportunityService(self.repos)
        self.jobs = AgentJobStore(self.root)

    def restart(self):
        """Simulate a process restart: fresh instances over the same files."""
        self.build_services()

    def seed_live_pipeline(self):
        """Import a committed quote, register stock, create a market side,
        match EXACT and build a qualified opportunity."""
        preview = self.importer.preview_bytes(QUOTE_CSV, kind="csv")
        confirm = self.importer.confirm(
            preview["preview_id"],
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="演练供应商",
            operator="drill",
        )
        snapshot = confirm["snapshots"][0]
        self.batch_id = confirm["batch"]["record_id"]
        self.supplier_snapshot = snapshot
        self.inventory.register_supplier_snapshot(account_id="acc-1", sku="GZ-1200", snapshot=snapshot)

        market = build_offer_snapshot(
            provider_id="market:xianyu_observation",
            source_item_id="GZ-1200",
            source_sku_id="GZ-1200",
            observed_at=datetime.now(UTC),
            attributes={"name": "不锈钢工作台 GZ-1200 全新", "spec": "120x60x85cm", "brand": "众商"},
            condition="全新",
            unit="件",
            price_type="list_price_display",
            eligible_price=money("299"),
            payload_hash="drill-market-1",
            account_id="",
            source_ref="market:manual_observation",
        )
        self.market_snapshot, _ = self.repos.offers.add(market)

        match_record = build_product_match(
            left_snapshot_id=self.market_snapshot["record_id"],
            right_snapshot_id=snapshot["record_id"],
            grade="EXACT",
            rule_version="hard-constraints-v1",
        )
        self.match = self.repos.matches.add(match_record)
        self.opportunity = self.opportunities.create_from_match(
            market_snapshot_id=self.market_snapshot["record_id"],
            supplier_snapshot_id=snapshot["record_id"],
            match_id=self.match["record_id"],
        )


class CrossFileFailureDrills(DrillBase):
    """A failure in one file must not leave the other file inconsistent."""

    def test_procurement_store_failure_releases_the_reservation(self):
        self.seed_live_pipeline()
        original_persist = self.procurement._store._persist
        self.procurement._store._persist = lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
        with self.assertRaises(RuntimeError):
            self.procurement.create_from_sales_order(
                account_id="acc-1", sku="GZ-1200", quantity=4, sales_order_ref="sale-x1"
            )
        self.procurement._store._persist = original_persist

        # inventory file keeps full availability; no procurement was stored
        allocation = self.inventory.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["available"], 10)
        self.assertEqual(self.procurement.list(), [])
        # and the reservation is genuinely gone from the lock table
        self.assertEqual(self.inventory.list_locks(), [])

    def test_cancel_with_release_failure_is_healed_by_recovery(self):
        self.seed_live_pipeline()
        order = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=4, sales_order_ref="sale-x2"
        )
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 6)

        # crash between the cancel transition and the lock release:
        # simulate by failing the inventory persist during cancel
        original_persist = self.inventory._store._persist
        self.inventory._store._persist = lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
        with self.assertRaises(RuntimeError):
            self.procurement.cancel(order["record_id"], reason="buyer cancel", operator="o")
        self.inventory._store._persist = original_persist

        # order is cancelled on disk, but the lock file still holds 4
        stored_order = self.procurement.get(order["record_id"])
        self.assertEqual(stored_order["status"], "cancelled")
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 6)

        # restart + reconcile releases the orphaned reservation
        self.restart()
        report = reconcile_orphan_reservations(self.procurement, self.inventory)
        self.assertEqual(len(report["released"]), 1)
        self.assertIn("cancelled", report["released"][0]["cause"])
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 10)
        # idempotent second pass does nothing
        again = reconcile_orphan_reservations(self.procurement, self.inventory)
        self.assertEqual(again["released"], [])


class RestartRecoveryDrills(DrillBase):
    """A restart must see the same facts and reclaim abandoned state."""

    def test_full_state_survives_restart(self):
        self.seed_live_pipeline()
        result = self.opportunities.evaluate(
            self.opportunity["record_id"],
            platform_fee_rate="0.03",
            packaging=money("5"),
            expected_return_loss=money("12"),
            attributable_costs=money("4"),
        )
        self.assertEqual(result["opportunity"]["status"], "ready_for_test")
        order = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=2, sales_order_ref="sale-restart"
        )
        job = self.jobs.submit(role="pricing", idempotency_key="drill-job-1")
        claimed = self.jobs.claim_next(worker_id="w1")
        self.assertEqual(claimed["record_id"], job["record_id"])

        self.restart()

        # every fact survives
        stored_opp = self.repos.opportunities.get(self.opportunity["record_id"])
        self.assertEqual(stored_opp["status"], "ready_for_test")
        self.assertEqual(self.procurement.get(order["record_id"])["status"], "awaiting_approval")
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 8)
        self.assertEqual(self.repos.offers.list()[0]["record_id"], self.supplier_snapshot["record_id"])
        self.assertEqual(self.jobs.get(job["record_id"])["status"], "leased")

    def test_orphan_lock_from_crash_between_lock_and_append(self):
        self.seed_live_pipeline()
        # simulate the crash window: lock exists, procurement was never stored
        self.inventory.lock_for_order(
            account_id="acc-1", sku="GZ-1200", order_id="proc:proc_ghost", quantity=3
        )
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 7)

        self.restart()
        report = reconcile_orphan_reservations(self.procurement, self.inventory)
        self.assertEqual(report["released"][0]["order_id"], "proc:proc_ghost")
        self.assertIn("missing", report["released"][0]["cause"])
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 10)

        # dry_run reports without touching
        self.inventory.lock_for_order(
            account_id="acc-1", sku="GZ-1200", order_id="proc:proc_ghost2", quantity=2
        )
        dry = reconcile_orphan_reservations(self.procurement, self.inventory, dry_run=True)
        self.assertEqual(dry["released"][0]["action"], "would_release")
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 8)

    def test_expired_agent_job_leases_recovered_after_restart(self):
        analysis = self.jobs.submit(role="analysis", max_attempts=2)
        write = self.jobs.submit(role="publish", account_id="acc-1", write_required=True)
        claimed_analysis = self.jobs.claim_next(worker_id="w1")
        claimed_write = self.jobs.claim_next(worker_id="w2")
        self.assertEqual(claimed_analysis["record_id"], analysis["record_id"])
        self.assertEqual(claimed_write["record_id"], write["record_id"])

        self.restart()
        recovered = self.jobs.recover_expired_leases(now=datetime.now(UTC) + timedelta(hours=2))
        by_id = {j["record_id"]: j for j in recovered}
        # analysis requeues for another attempt
        self.assertEqual(by_id[analysis["record_id"]]["status"], JOB_QUEUED)
        # a write job's platform outcome is unknown -> needs verification
        self.assertEqual(by_id[write["record_id"]]["status"], JOB_UNKNOWN)
        # the recovered analysis job is claimable again
        reclaimed = self.jobs.claim_next(worker_id="w3")
        self.assertEqual(reclaimed["record_id"], analysis["record_id"])
        self.assertEqual(reclaimed["attempt"], 2)


class FullFlowCombinationDrill(DrillBase):
    """The whole corrected pipeline in one sequence with hand-checked totals."""

    def test_full_flow_with_failure_branches(self):
        # 1. import committed batch + stock + qualified opportunity
        self.seed_live_pipeline()
        evaluated = self.opportunities.evaluate(
            self.opportunity["record_id"],
            platform_fee_rate="0.03",
            packaging=money("5"),
            expected_return_loss=money("12"),
            attributable_costs=money("4"),
        )
        self.assertTrue(evaluated["qualification"]["qualified"])
        self.assertEqual(evaluated["opportunity"]["status"], "ready_for_test")

        # 2. procurement: verify -> reserve -> human approval -> supplier order -> receipt
        order = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-full-1",
            opportunity_id=self.opportunity["record_id"],
        )
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 9)
        self.procurement.approve(order["record_id"], operator="操作者")
        self.procurement.record_supplier_order(order["record_id"], supplier_order_id="SO-DRILL-1", operator="操作者")
        received = self.procurement.record_receipt(order["record_id"], receipt_ref="receipt://drill/1", operator="操作者")
        self.assertEqual(received["status"], "received")

        # 3. money: pay with evidence, income with payout evidence
        self.ledger.record_spend(
            category="procurement", amount=money("150"), order_ref=order["record_id"],
            opportunity_id=self.opportunity["record_id"], payment_evidence_ref="pay://drill/1",
        )
        self.ledger.record_spend(
            category="supplier_freight", amount=money("15"), order_ref=order["record_id"],
            opportunity_id=self.opportunity["record_id"], payment_evidence_ref="pay://drill/1",
        )
        income = self.ledger.record_income(
            amount=money("299"), order_ref="sale-full-1",
            opportunity_id=self.opportunity["record_id"], payout_evidence_ref="payout://drill/1",
        )
        summary = self.ledger.profit_summary(opportunity_id=self.opportunity["record_id"])
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("134"))  # 299-150-15

        # 4. branch: partial refund keeps the ledger sane
        self.ledger.reverse(income["record_id"], reason="部分退款100", amount=money("100"), evidence_ref="refund://drill/1")
        summary = self.ledger.profit_summary(opportunity_id=self.opportunity["record_id"])
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("34"))

        # 5. branch: revoking the quote batch blocks any NEW procurement
        self.importer.revoke_batch(self.batch_id, operator="操作者", reason="停售")
        with self.assertRaises(ProcurementError):
            self.procurement.create_from_sales_order(
                account_id="acc-1", sku="GZ-1200", quantity=1, sales_order_ref="sale-full-2"
            )

        # 6. branch: human match correction invalidates the qualification
        self.matcher.correct_grade(
            self.match["record_id"], corrected_grade="NOT_MATCH", operator="操作者", reason="复核发现规格不符"
        )
        rechecked = self.opportunities.evaluate(
            self.opportunity["record_id"],
            platform_fee_rate="0.03",
            packaging=money("5"),
            expected_return_loss=money("12"),
            attributable_costs=money("4"),
        )
        self.assertFalse(rechecked["qualification"]["qualified"])
        self.assertEqual(rechecked["opportunity"]["status"], "paused")
        self.assertIn("invalidated", rechecked["opportunity"]["status_reason"])

        # 7. branch: cancelling a live procurement returns its stock
        order_b = None  # batch revoked -> cannot create another; use cancel of the received flow's sibling instead
        # (create a fresh committed batch to enable one more procurement)
        preview = self.importer.preview_bytes(QUOTE_CSV.replace(b"150", b"165"), kind="csv")
        confirm2 = self.importer.confirm(
            preview["preview_id"],
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="演练供应商",
            operator="drill",
        )
        self.inventory.register_supplier_snapshot(
            account_id="acc-1", sku="GZ-1200", snapshot=confirm2["snapshots"][0]
        )
        order_b = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=2, sales_order_ref="sale-full-3"
        )
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 7)
        self.procurement.cancel(order_b["record_id"], reason="测试取消", operator="操作者")
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 9)
        # stale double-cancel of order_b cannot release anything else
        self.procurement.cancel(order_b["record_id"], reason="重放取消", operator="操作者")
        self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 9)

        # 8. final restart: every fact still consistent
        self.restart()
        final = self.ledger.profit_summary(opportunity_id=self.opportunity["record_id"])
        self.assertEqual(Decimal(final["settled"]["net"]), Decimal("34"))
        self.assertEqual(self.repos.opportunities.get(self.opportunity["record_id"])["status"], "paused")
        self.assertEqual(self.procurement.get(order["record_id"])["status"], "received")
        locks = {lock["order_id"] for lock in self.inventory.list_locks()}
        self.assertIn(f"proc:{order['record_id']}", locks)  # the received order keeps its sold allocation
        self.assertNotIn(f"proc:{order_b['record_id']}", locks)  # cancelled order released


class SyncGapDrills(DrillBase):
    """P1 re-verify round 3: synchronization gaps between recovery and
    creation, and between cancel and supplier-order registration."""

    def _create_and_approve(self, ref):
        order = self.procurement.create_from_sales_order(
            account_id="acc-1", sku="GZ-1200", quantity=4, sales_order_ref=ref
        )
        self.procurement.approve(order["record_id"], operator="o")
        return order

    def test_reconcile_cannot_release_a_reservation_being_created(self):
        """Reconcile must not run between the inventory lock and the
        procurement append of a concurrent creation."""
        import threading

        self.seed_live_pipeline()
        append_entered = threading.Event()
        allow_append = threading.Event()
        original_append = self.procurement._store.append

        def paused_append(item):
            append_entered.set()  # creation holds the mutation lock, lock+checks done
            allow_append.wait(timeout=10)
            return original_append(item)

        self.procurement._store.append = paused_append

        create_result = {}

        def creator():
            try:
                order = self.procurement.create_from_sales_order(
                    account_id="acc-1", sku="GZ-1200", quantity=4, sales_order_ref="sale-sync-1"
                )
                create_result["order"] = order
            except Exception as exc:  # noqa: BLE001
                create_result["error"] = str(exc)

        thread = threading.Thread(target=creator)
        thread.start()
        self.assertTrue(append_entered.wait(timeout=5))

        # run reconcile while the creation is parked inside its append;
        # the main thread releases the park explicitly afterwards
        report = reconcile_orphan_reservations(self.procurement, self.inventory)

        allow_append.set()
        thread.join(timeout=10)

        # the creation must have succeeded
        self.assertNotIn("error", create_result)
        order = create_result["order"]
        self.assertEqual(order["status"], "awaiting_approval")

        # the reservation of the LIVE order must survive: reconcile either
        # waited for the creation (correct) or released it (bug)
        locks = {lock["order_id"] for lock in self.inventory.list_locks()}
        self.assertIn(
            f"proc:{order['record_id']}", locks,
            "reconcile released the reservation of an in-flight creation",
        )
        allocation = self.inventory.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["available"], 6)
        self.assertEqual(allocation["locked"], 4)
        # and the reconcile report must have seen the live order
        self.assertEqual(report["released"], [])
        self.assertGreaterEqual(report["kept_live"], 1)

    def test_cancel_cannot_overwrite_a_registered_supplier_order(self):
        """Cancel must not overwrite an order that concurrently moved to
        `ordered`; state changes need one synchronization domain and a
        re-check under the lock."""
        import threading

        self.seed_live_pipeline()
        order = self._create_and_approve("sale-sync-2")

        # park ONLY the cancel transition; other transitions pass through
        transition_entered = threading.Event()
        allow_transition = threading.Event()
        original_transition = self.procurement._transition

        def paused_transition(procurement_id, to_status, **kwargs):
            if to_status == "cancelled":
                transition_entered.set()
                allow_transition.wait(timeout=10)
            return original_transition(procurement_id, to_status, **kwargs)

        self.procurement._transition = paused_transition

        cancel_result = {}

        def canceller():
            try:
                self.procurement.cancel(order["record_id"], reason="交错取消", operator="o")
                cancel_result["ok"] = True
            except ProcurementError as exc:
                cancel_result["blocked"] = str(exc)

        thread = threading.Thread(target=canceller)
        thread.start()
        self.assertTrue(transition_entered.wait(timeout=5))

        # while cancel is parked, the supplier order registers
        registration_error = None
        try:
            self.procurement.record_supplier_order(
                order["record_id"], supplier_order_id="SO-RACE-1", operator="o"
            )
        except ProcurementError as exc:
            registration_error = str(exc)

        allow_transition.set()
        thread.join(timeout=10)

        stored = self.procurement.get(order["record_id"])
        supplier_id = str(stored.get("supplier_order_id") or "")

        if stored["status"] == "cancelled":
            # cancel won: it must have cancelled a still-approved order,
            # and the late supplier registration must have been refused
            self.assertEqual(supplier_id, "", "cancelled order must not carry a supplier order id")
            self.assertIsNotNone(registration_error, "registration on a cancelled order must fail")
            self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 10)
        else:
            # registration won: the order is ordered with its id and cancel
            # must have been refused (ordered orders are not cancellable)
            self.assertEqual(stored["status"], "ordered")
            self.assertEqual(supplier_id, "SO-RACE-1")
            self.assertIn("blocked", cancel_result)
            self.assertEqual(self.inventory.allocation(account_id="acc-1", sku="GZ-1200")["available"], 6)
        # either way, the impossible combination must not exist
        self.assertFalse(
            stored["status"] == "cancelled" and supplier_id,
            "cancel overwrote a registered supplier order",
        )


if __name__ == "__main__":
    unittest.main()
