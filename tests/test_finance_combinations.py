"""R11 resident acceptance: procurement/budget/inventory atomic commands.

Ports the 2026-10-20 review's twelve finance-combination probes (11 were
red) as permanent tests: shared legacy/new ownership, payment-fact gate,
atomic create+reserve with rollback, cancel healing, immutable receipt
costs, ledger-failure compensation, receipt-not-payment, cross-account
runtime identity, startup reconciliation and create/recovery
serialization.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def money(value):
    return {"state": "known", "amount": str(value), "currency": "CNY"}


class FinanceCombinations(unittest.TestCase):
    """Isolated harness: temp root per test, fresh services, stub adapter."""

    @classmethod
    def setUpClass(cls):
        import os
        import sys

        cls.tmp = tempfile.TemporaryDirectory(prefix="r11-finance-")
        cls.base = Path(cls.tmp.name)
        cls.env = {
            "DATA_DIR": str(cls.base / "bootstrap"),
            "DB_PATH": str(cls.base / "bootstrap" / "fixture.db"),
            "XIANYU_ADAPTER": "stub",
            "XIANYU_COOKIES": "",
            "SHADOW_OBSERVER_ENABLED": "0",
            "RADAR_DISPATCHER_ENABLED": "0",
            "DISCOVERY_ENABLED": "0",
            "GITHUB_DISCOVERY_ENABLED": "0",
            "AUTO_REPLY_ENABLED": "0",
            "MARKET_CHANNEL_PROBE_AUTHORIZED": "0",
            "DEEPSEEK_API_KEY": "",
            "MIMO_API_KEY": "",
        }
        os.environ.update(cls.env)
        cls._saved_env = {key: os.environ.get(key) for key in cls.env}
        sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "tests")]
        from xianyu_agent import server

        cls.server = server
        # snapshot module-level composition so THIS class's per-test
        # replacements never leak into other test files in the same
        # process (restored in tearDownClass before the temp root dies)
        cls._saved = {
            name: getattr(server, name)
            for name in (
                "settings", "commerce_repositories", "supplier_import_service",
                "inventory_service", "procurement_service", "ledger_service",
                "budget_reservation_store", "order_runtime_store", "resale_manager",
            )
        }

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.pi_task_runner.stop()
            cls.server.pi_runtime.stop()
        finally:
            for name, value in cls._saved.items():
                setattr(cls.server, name, value)
            for key, value in cls._saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            cls.tmp.cleanup()

    def setUp(self):
        from fastapi.testclient import TestClient

        from xianyu_agent.budget_reservations import BudgetReservationStore
        from xianyu_agent.commerce import JsonCommerceRepositories
        from xianyu_agent.inventory import InventoryAllocationService
        from xianyu_agent.ledger_service import LedgerService
        from xianyu_agent.order_runtime import OrderRuntimeStore
        from xianyu_agent.procurement import ProcurementService
        from xianyu_agent.resale import ResaleManager
        from xianyu_agent.supplier_import import SupplierImportService

        self.s = self.server
        self.root = self.base / self._testMethodName
        self.root.mkdir()
        self.s.settings = replace(self.s.settings, data_dir=self.root)
        self.s.commerce_repositories = JsonCommerceRepositories(self.root)
        self.s.supplier_import_service = SupplierImportService(self.s.commerce_repositories, self.root)
        self.s.inventory_service = InventoryAllocationService(self.root, safety_margin=0)
        self.s.procurement_service = ProcurementService(
            self.root, self.s.commerce_repositories, self.s.inventory_service,
            self.s.supplier_import_service,
        )
        self.s.ledger_service = LedgerService(self.root)
        self.s.budget_reservation_store = BudgetReservationStore(self.root)
        self.s.order_runtime_store = OrderRuntimeStore(self.root / "order_runtime_state.json")
        self.s.resale_manager = ResaleManager(self.root)
        self.client = TestClient(self.s.app, raise_server_exceptions=False)
        self.seed_quote()

    def tearDown(self):
        self.client.close()
        self.s.db_manager.close()

    # ---- fixtures -----------------------------------------------------

    def seed_quote(self, sku="SKU-X", moq=1):
        from quote_fixtures import with_declared_quote_provenance
        from xianyu_agent.commerce.contracts import build_offer_snapshot, utc_now

        quote = with_declared_quote_provenance(build_offer_snapshot(
            provider_id="supplier_file:isolated-fixture", source_item_id=sku,
            source_sku_id=sku, observed_at=utc_now(), eligible_price=money(160),
            shipping_quote=money(20), stock_state="in_stock", stock_quantity=50,
            moq=moq, payload_hash="r11-" + sku + str(moq), source_ref="fixture:r11",
        ))
        self.s.commerce_repositories.offers.add(quote)
        for account in ("acct-a", "acct-b"):
            response = self.client.post("/commerce/inventory/register", json={
                "account_id": account, "sku": sku, "snapshot_id": quote["record_id"],
            })
            self.assertEqual(response.status_code, 200, response.text)

    def runtime_order(self, ref="fixture-sale", status="paid", account="acct-a"):
        return self.s.order_runtime_store.upsert_order(
            order_id=ref, cookie_id=account, item_id="SKU-X", status=status,
            amount=300, source="fixture:runtime", metadata={"fixture_only": True},
        )

    def create(self, ref="fixture-sale", account="acct-a", sku="SKU-X", **extra):
        return self.client.post("/commerce/procurement/from-sales-order", json={
            "account_id": account, "sku": sku, "quantity": 1,
            "sales_order_ref": ref, **extra,
        })

    def legacy(self, account="acct-a", ref="fixture-sale"):
        return self.s.resale_manager.create_purchase_task({
            "draft_id": "fixture:draft", "xianyu_order_id": ref,
            "cookie_id": account, "item_id": "SKU-X", "quantity": 1,
            "total_cost": 180,
        })

    def mark_legacy(self, task):
        return self.client.post(
            "/resale/purchase-tasks/%s/mark-ordered" % task["purchase_task_id"],
            json={"supplier_order_id": "fixture:legacy-supplier"},
        )

    def accept_new(self, **extra):
        response = self.create(**extra)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def order_new(self, order):
        for suffix, body in (("approve", {"operator": "fixture"}),
                             ("supplier-order", {"operator": "fixture",
                                                 "supplier_order_id": "fixture:new-supplier"})):
            response = self.client.post(
                "/commerce/procurement/%s/%s" % (order["record_id"], suffix), json=body)
            self.assertEqual(response.status_code, 200, response.text)

    def actual_receipt(self, order, **extra):
        return self.client.post("/commerce/procurement/%s/receipt" % order["record_id"], json={
            "receipt_ref": "fixture:receipt", "actual_unit_cost": "175",
            "actual_freight": "25", **extra,
        })

    def reload_finance(self):
        from xianyu_agent.budget_reservations import BudgetReservationStore
        from xianyu_agent.inventory import InventoryAllocationService
        from xianyu_agent.ledger_service import LedgerService
        from xianyu_agent.procurement import ProcurementService

        self.s.inventory_service = InventoryAllocationService(self.root, safety_margin=0)
        self.s.procurement_service = ProcurementService(
            self.root, self.s.commerce_repositories, self.s.inventory_service,
            self.s.supplier_import_service)
        self.s.budget_reservation_store = BudgetReservationStore(self.root)
        self.s.ledger_service = LedgerService(self.root)

    def startup(self):
        """Simulate a process restart: boot + reconcile WITHOUT spawning
        the background loops (they are sleep-first in production anyway)."""
        async def run():
            await self.s.runtime.start_async()

        saved_loops = self.s.runtime._loops
        self.s.runtime._loops = []
        try:
            asyncio.run(run())
        finally:
            self.s.runtime._loops = saved_loops
        return []

    def budgets(self):
        return self.s.budget_reservation_store.usage(account_id="acct-a", bucket="procurement")

    def costs(self):
        return [row["amount"]["amount"] for row in self.s.ledger_service.entries(order_ref="fixture-sale")]

    # ---- cases ---------------------------------------------------------

    def test_legacy_first(self):
        self.runtime_order()
        task = self.legacy()
        legacy_response = self.mark_legacy(task)
        self.assertEqual(legacy_response.status_code, 200, legacy_response.text)
        new_response = self.create()
        self.assertIn(new_response.status_code, (403, 409),
                      "Legacy-owned sale must reject new-chain procurement")

    def test_legacy_cross_account(self):
        self.runtime_order()
        order = self.accept_new()
        task = self.legacy(account="acct-b")
        response = self.mark_legacy(task)
        self.assertEqual(response.status_code, 200,
                         "A different account/order key must not collide with new authority")

    def test_legacy_service_interleave(self):
        task = self.legacy()
        checked = threading.Event()
        proceed = threading.Event()
        original_list = self.s.procurement_service.list

        def paused_list(*args, **kwargs):
            rows = original_list(*args, **kwargs)
            checked.set()
            if not proceed.wait(5):
                raise AssertionError("Timed out controlling the legacy check/write interval")
            return rows

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(self.s.procurement_service, "list", paused_list):
                future = pool.submit(self.mark_legacy, task)
                try:
                    self.assertTrue(checked.wait(5), "Legacy handler did not reach duplicate check")
                    order = self.s.procurement_service.create_from_sales_order(
                        account_id="acct-a", sku="SKU-X", quantity=1,
                        sales_order_ref="fixture-sale")
                finally:
                    proceed.set()
                response = future.result(timeout=5)
        self.assertEqual(response.status_code, 409,
                         "Legacy check and commit must share procurement ownership synchronization")

    def test_runtime_account_collision(self):
        self.runtime_order(ref="shared-ref")
        self.s.order_runtime_store.upsert_order(
            order_id="shared-ref", cookie_id="acct-b", item_id="SKU-X",
            status="paid", amount=300, source="fixture:runtime")
        first = self.create(ref="shared-ref", account="acct-a", paid=True)
        second = self.create(ref="shared-ref", account="acct-b", paid=True)
        self.assertEqual(first.status_code, 200,
                         "A second account's runtime upsert must not overwrite the first account/order")

    def test_cancel_crash_restart(self):
        order = self.accept_new()
        path = "/commerce/procurement/%s/cancel" % order["record_id"]
        with patch.object(self.s.inventory_service, "release_order",
                          side_effect=OSError("fixture: crash after cancelled persisted")):
            first = self.client.post(path, json={"reason": "fixture", "operator": "fixture"})
        self.assertEqual(first.status_code, 500, "Fault injection must hit the post-cancel lock release")
        self.reload_finance()
        self.startup()
        retry = self.client.post(path, json={"reason": "retry", "operator": "fixture"})
        allocation = self.s.inventory_service.allocation(account_id="acct-a", sku="SKU-X")
        self.assertEqual(allocation["locked"], 0,
                         "Cancelled procurement lock must be healed at startup or cancellation retry")

    def test_cancel_budget_recreate(self):
        old = self.accept_new()
        cancelled = self.client.post(
            "/commerce/procurement/%s/cancel" % old["record_id"], json={"reason": "fixture"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        new = self.accept_new()
        stale = self.client.post(
            "/commerce/procurement/%s/cancel" % old["record_id"], json={"reason": "stale retry"})
        self.assertEqual(stale.status_code, 200, stale.text)
        self.reload_finance()
        self.assertEqual(Decimal(self.budgets()["reserved"]), Decimal("180"),
                         "Only the new live procurement should reserve cash")

    def test_budget_rejection(self):
        from xianyu_agent.budget_reservations import BudgetError

        with patch.object(self.s.budget_reservation_store, "reserve",
                          side_effect=BudgetError("fixture: cash reservation refused")):
            response = self.create()
        self.reload_finance()
        self.assertTrue(
            response.status_code in (400, 403, 409, 422, 503)
            and not self.s.procurement_service.list()
            and not self.s.inventory_service.list_locks(),
            "Budget refusal must not yield successful unfunded procurement and locked stock")

    def test_receipt_replay_costs(self):
        order = self.accept_new()
        self.order_new(order)
        first = self.actual_receipt(order)
        second = self.actual_receipt(order)
        changed = self.actual_receipt(order, actual_unit_cost="900", actual_freight="99")
        omitted = self.client.post("/commerce/procurement/%s/receipt" % order["record_id"],
                                   json={"receipt_ref": "fixture:receipt"})
        self.reload_finance()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.costs(), ["200"],
                         "Receipt retry must not duplicate costs or use retry-body/quote costs")

    def test_receipt_ledger_failure_restart(self):
        order = self.accept_new()
        self.order_new(order)
        with patch.object(self.s.ledger_service._store, "_persist",
                          side_effect=OSError("fixture: ledger unavailable")):
            response = self.actual_receipt(order)
        self.reload_finance()
        self.startup()
        self.assertFalse(response.status_code == 200 and not self.costs(),
                         "Receipt ledger failure must not be silently finalized")

    def test_receipt_prefix_not_payment(self):
        order = self.accept_new()
        self.order_new(order)
        response = self.actual_receipt(order, receipt_ref="platform:fixture-receipt-not-payment")
        self.assertEqual(response.status_code, 200, response.text)
        entries = self.s.ledger_service.entries(order_ref="fixture-sale")
        self.assertEqual([e["kind"] for e in entries], ["provisional"],
                         "Receipt prefix is not verified payment evidence")

    def test_startup_mixed_owners(self):
        order = self.accept_new()
        for key in ("proc:missing-fixture", "foreign:fixture"):
            self.s.inventory_service.lock_for_order(account_id="acct-a", sku="SKU-X",
                                                    order_id=key, quantity=1)
        self.reload_finance()
        self.startup()
        keys = sorted(lock["order_id"] for lock in self.s.inventory_service.list_locks())
        self.assertEqual(keys, sorted([order["reservation_key"], "foreign:fixture"]))

    def test_recovery_create_serialization(self):
        proc = self.s.procurement_service
        locked = threading.Event()
        append_allowed = threading.Event()
        recovery_entered = threading.Event()
        inventory_scanned = threading.Event()
        original_append = proc._store.append
        original_guard = proc.mutation_guard
        original_scan = self.s.inventory_service.list_locks

        def pause_append(row):
            locked.set()
            if not append_allowed.wait(5):
                raise AssertionError("Timed out before procurement append")
            return original_append(row)

        @contextmanager
        def observe_guard():
            recovery_entered.set()
            with original_guard():
                yield

        def observe_scan(*args, **kwargs):
            inventory_scanned.set()
            return original_scan(*args, **kwargs)

        with patch.object(proc._store, "append", pause_append), \
                patch.object(proc, "mutation_guard", observe_guard), \
                patch.object(self.s.inventory_service, "list_locks", observe_scan):
            with ThreadPoolExecutor(max_workers=2) as pool:
                create = pool.submit(proc.create_from_sales_order, account_id="acct-a",
                                     sku="SKU-X", quantity=1, sales_order_ref="fixture-sale")
                try:
                    self.assertTrue(locked.wait(5))
                    recovery = pool.submit(proc.reconcile_orphan_locks)
                    self.assertTrue(recovery_entered.wait(5))
                    scanned_before_append = inventory_scanned.wait(0.15)
                finally:
                    append_allowed.set()
                order = create.result(timeout=5)
                result = recovery.result(timeout=5)
        self.assertFalse(scanned_before_append,
                         "Recovery must wait on the same mutation lock as create")
        self.assertEqual(result["released_locks"], [])
        self.assertEqual(self.s.inventory_service.list_locks()[0]["order_id"], order["reservation_key"])


if __name__ == "__main__":
    unittest.main()
