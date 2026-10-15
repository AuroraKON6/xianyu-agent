"""Bounded offline combination recheck, not commercial acceptance.

Run from the repository: python -B research/codex-recheck-2026-10-13/finance-combinations.py --case all
Only this script is added. No log, business data, source, test, or document
is written. Reuses Oct12's temporary-data/socket/process isolation without
running any of its original cases. HTTP uses in-process TestClient without
lifespan; explicit startup closes scheduled coroutines before they run.
The legacy concurrency probe is a handler/service-worker interleaving, NOT
evidence of two requests interleaving on one production ASGI event loop.
Failures assert the intended invariant and print the actual persisted facts.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "research" / "codex-verification-2026-10-12")]
from finance_repros import FinanceRepros as IsolatedFinance  # noqa: E402

CASES = (
    "legacy_first",
    "legacy_cross_account",
    "legacy_service_interleave",
    "runtime_account_collision",
    "cancel_crash_restart",
    "cancel_budget_recreate",
    "budget_rejection",
    "receipt_replay_costs",
    "receipt_ledger_failure_restart",
    "receipt_prefix_not_payment",
    "startup_mixed_owners",
    "recovery_create_serialization",
)
HASH_PATHS = (
    "xianyu_agent/server.py", "xianyu_agent/procurement.py",
    "xianyu_agent/ledger_service.py", "xianyu_agent/inventory.py",
    "xianyu_agent/budget_reservations.py", "xianyu_agent/order_runtime.py",
    "xianyu_agent/resale.py", "xianyu_agent/commerce/repositories.py",
    "research/codex-verification-2026-10-12/finance_repros.py",
)


def hashes():
    return {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in HASH_PATHS}


class FinanceCombinations(IsolatedFinance):
    @classmethod
    def setUpClass(cls):
        # The imported harness sets temp cwd/env before importing the server.
        # Deny actual background thread starts during that import as well.
        with patch.object(threading.Thread, "start", side_effect=AssertionError("Background autostart disabled")):
            super().setUpClass()
        if cls.server.shadow_observer_thread is not None:
            raise AssertionError("Background observer escaped isolation")

    def observed(self, **facts):
        print("OBSERVED " + self._testMethodName[5:] + " " + json.dumps(facts, sort_keys=True, default=str))

    def legacy(self, account="acct-a", ref="fixture-sale"):
        return self.s.resale_manager.create_purchase_task({
            "draft_id": "fixture:draft", "xianyu_order_id": ref,
            "cookie_id": account, "item_id": "SKU-X", "quantity": 1,
            "total_cost": 180,
        })

    def mark_legacy(self, task):
        return self.client.post("/resale/purchase-tasks/%s/mark-ordered" % task["purchase_task_id"],
                                json={"supplier_order_id": "fixture:legacy-supplier"})

    def accept_new(self, **extra):
        response = self.create(**extra)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def order_new(self, order):
        for suffix, body in (("approve", {"operator": "fixture"}),
                             ("supplier-order", {"operator": "fixture", "supplier_order_id": "fixture:new-supplier"})):
            response = self.client.post("/commerce/procurement/%s/%s" % (order["record_id"], suffix), json=body)
            self.assertEqual(response.status_code, 200, response.text)

    def actual_receipt(self, order, **extra):
        return self.client.post("/commerce/procurement/%s/receipt" % order["record_id"], json={
            "receipt_ref": "fixture:receipt", "actual_unit_cost": "175", "actual_freight": "25", **extra,
        })

    def reload_finance(self):
        from xianyu_agent.budget_reservations import BudgetReservationStore
        from xianyu_agent.inventory import InventoryAllocationService
        from xianyu_agent.ledger_service import LedgerService
        from xianyu_agent.procurement import ProcurementService
        self.s.inventory_service = InventoryAllocationService(self.root, safety_margin=0)
        self.s.procurement_service = ProcurementService(
            self.root, self.s.commerce_repositories, self.s.inventory_service, self.s.supplier_import_service)
        self.s.budget_reservation_store = BudgetReservationStore(self.root)
        self.s.ledger_service = LedgerService(self.root)

    def startup(self):
        suppressed = []

        def close_task(coroutine, *args, **kwargs):
            suppressed.append(coroutine.cr_code.co_name)
            coroutine.close()

        with patch.object(asyncio, "create_task", close_task):
            asyncio.run(self.s.on_startup())
        return suppressed

    def budgets(self):
        return self.s.budget_reservation_store.usage(account_id="acct-a", bucket="procurement")

    def costs(self):
        return [row["amount"]["amount"] for row in self.s.ledger_service.entries(order_ref="fixture-sale")]

    def test_legacy_first(self):
        self.runtime_order()
        task = self.legacy()
        legacy_response = self.mark_legacy(task)
        self.assertEqual(legacy_response.status_code, 200, legacy_response.text)
        new_response = self.create()
        if new_response.status_code == 200:
            self.order_new(new_response.json())
        self.observed(legacy_http=legacy_response.status_code, new_http=new_response.status_code,
                      legacy_status=self.s.resale_manager.get_purchase_task(task["purchase_task_id"])["status"],
                      new_statuses=[o["status"] for o in self.s.procurement_service.list()])
        self.assertIn(new_response.status_code, (403, 409), "Legacy-owned sale must reject new-chain procurement")

    def test_legacy_cross_account(self):
        self.runtime_order()
        order = self.accept_new()
        task = self.legacy(account="acct-b")
        response = self.mark_legacy(task)
        self.observed(new_account=order["account_id"], legacy_account=task["cookie_id"],
                      same_ref=task["xianyu_order_id"], legacy_http=response.status_code, detail=response.json())
        self.assertEqual(response.status_code, 200, "A different account/order key must not collide with new authority")

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
                        account_id="acct-a", sku="SKU-X", quantity=1, sales_order_ref="fixture-sale")
                finally:
                    proceed.set()
                response = future.result(timeout=5)
        self.observed(boundary="legacy handler plus direct service writer, not single-loop HTTP concurrency",
                      legacy_http=response.status_code, new_status=order["status"],
                      legacy_status=self.s.resale_manager.get_purchase_task(task["purchase_task_id"])["status"])
        self.assertEqual(response.status_code, 409, "Legacy check and commit must share procurement ownership synchronization")

    def test_runtime_account_collision(self):
        self.runtime_order(ref="shared-ref")
        self.s.order_runtime_store.upsert_order(order_id="shared-ref", cookie_id="acct-b", item_id="SKU-X",
                                               status="paid", amount=300, source="fixture:runtime")
        first = self.create(ref="shared-ref", account="acct-a", paid=True)
        second = self.create(ref="shared-ref", account="acct-b", paid=True)
        from xianyu_agent.order_runtime import OrderRuntimeStore
        reloaded = OrderRuntimeStore(self.root / "order_runtime_state.json")
        self.observed(account_a_orders=reloaded.list_orders(cookie_id="acct-a"),
                      account_b_count=len(reloaded.list_orders(cookie_id="acct-b")),
                      account_a_http=first.status_code, account_b_http=second.status_code)
        self.assertEqual(first.status_code, 200, "A second account's runtime upsert must not overwrite the first account/order")

    def test_cancel_crash_restart(self):
        order = self.accept_new()
        path = "/commerce/procurement/%s/cancel" % order["record_id"]
        with patch.object(self.s.inventory_service, "release_order", side_effect=OSError("fixture: crash after cancelled persisted")):
            first = self.client.post(path, json={"reason": "fixture", "operator": "fixture"})
        self.assertEqual(first.status_code, 500, "Fault injection must hit the post-cancel lock release")
        self.reload_finance()
        suppressed = self.startup()
        retry = self.client.post(path, json={"reason": "retry", "operator": "fixture"})
        allocation = self.s.inventory_service.allocation(account_id="acct-a", sku="SKU-X")
        self.observed(first_http=first.status_code, retry_http=retry.status_code,
                      persisted_status=self.s.procurement_service.get(order["record_id"])["status"],
                      locked_after_restart_and_retry=allocation["locked"], budget=self.budgets(),
                      suppressed_background=suppressed)
        self.assertEqual(allocation["locked"], 0, "Cancelled procurement lock must be healed at startup or cancellation retry")

    def test_cancel_budget_recreate(self):
        old = self.accept_new()
        cancelled = self.client.post("/commerce/procurement/%s/cancel" % old["record_id"], json={"reason": "fixture"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        new = self.accept_new()
        stale = self.client.post("/commerce/procurement/%s/cancel" % old["record_id"], json={"reason": "stale retry"})
        self.assertEqual(stale.status_code, 200, stale.text)
        self.reload_finance()
        reservations = self.s.budget_reservation_store.list(account_id="acct-a")
        self.observed(old_status=self.s.procurement_service.get(old["record_id"])["status"],
                      new_status=self.s.procurement_service.get(new["record_id"])["status"],
                      locks=self.s.inventory_service.list_locks(), budget=self.budgets(),
                      reservations=[{"job_id": r["job_id"], "status": r["status"]} for r in reservations])
        self.assertEqual(Decimal(self.budgets()["reserved"]), Decimal("180"), "Only the new live procurement should reserve cash")

    def test_budget_rejection(self):
        from xianyu_agent.budget_reservations import BudgetError
        with patch.object(self.s.budget_reservation_store, "reserve", side_effect=BudgetError("fixture: cash reservation refused")):
            response = self.create()
        self.reload_finance()
        self.observed(http=response.status_code, procurements=len(self.s.procurement_service.list()),
                      locks=self.s.inventory_service.list_locks(), budget_rows=self.s.budget_reservation_store.list())
        self.assertTrue(response.status_code in (400, 403, 409, 422, 503)
                        and not self.s.procurement_service.list() and not self.s.inventory_service.list_locks(),
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
        stored = self.s.procurement_service.get(order["record_id"])
        self.observed(http=[r.status_code for r in (first, second, changed, omitted)],
                      stored_actual_unit=stored["actual_unit_cost"], stored_actual_freight=stored["actual_freight"],
                      ledger_costs=self.costs(), budget=self.budgets())
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.costs(), ["200"], "Receipt retry must not duplicate costs or use retry-body/quote costs instead of the immutable actuals")

    def test_receipt_ledger_failure_restart(self):
        order = self.accept_new()
        self.order_new(order)
        with patch.object(self.s.ledger_service._store, "_persist", side_effect=OSError("fixture: ledger unavailable")):
            response = self.actual_receipt(order)
        self.reload_finance()
        suppressed = self.startup()
        self.observed(http=response.status_code, persisted_status=self.s.procurement_service.get(order["record_id"])["status"],
                      ledger_costs=self.costs(), suppressed_background=suppressed)
        self.assertFalse(response.status_code == 200 and not self.costs(),
                         "Receipt ledger failure must not be silently finalized with no durable pending/recovery work")

    def test_receipt_prefix_not_payment(self):
        order = self.accept_new()
        self.order_new(order)
        response = self.actual_receipt(order, receipt_ref="platform:fixture-receipt-not-payment")
        self.assertEqual(response.status_code, 200, response.text)
        entries = self.s.ledger_service.entries(order_ref="fixture-sale")
        self.observed(receipt_ref=response.json()["receipt_ref"], ledger_kinds=[e["kind"] for e in entries], budget=self.budgets())
        self.assertEqual([e["kind"] for e in entries], ["provisional"], "Receipt prefix is not verified payment evidence")

    def test_startup_mixed_owners(self):
        order = self.accept_new()
        for key in ("proc:missing-fixture", "foreign:fixture"):
            self.s.inventory_service.lock_for_order(account_id="acct-a", sku="SKU-X", order_id=key, quantity=1)
        self.reload_finance()
        suppressed = self.startup()
        keys = sorted(lock["order_id"] for lock in self.s.inventory_service.list_locks())
        self.observed(remaining_lock_keys=keys, live_key=order["reservation_key"], suppressed_background=suppressed)
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

        with patch.object(proc._store, "append", pause_append), patch.object(proc, "mutation_guard", observe_guard), \
                patch.object(self.s.inventory_service, "list_locks", observe_scan):
            with ThreadPoolExecutor(max_workers=2) as pool:
                create = pool.submit(proc.create_from_sales_order, account_id="acct-a", sku="SKU-X",
                                     quantity=1, sales_order_ref="fixture-sale")
                try:
                    self.assertTrue(locked.wait(5))
                    recovery = pool.submit(proc.reconcile_orphan_locks)
                    self.assertTrue(recovery_entered.wait(5))
                    scanned_before_append = inventory_scanned.wait(0.15)
                finally:
                    append_allowed.set()
                order = create.result(timeout=5)
                result = recovery.result(timeout=5)
        self.observed(recovery_scanned_before_append=scanned_before_append, recovery=result,
                      remaining_locks=self.s.inventory_service.list_locks())
        self.assertFalse(scanned_before_append, "Recovery must wait on the same mutation lock as create")
        self.assertEqual(result["released_locks"], [])
        self.assertEqual(self.s.inventory_service.list_locks()[0]["order_id"], order["reservation_key"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    before = hashes()
    print("SOURCE_SHA256 " + json.dumps(before, sort_keys=True), flush=True)
    names = CASES if args.case == "all" else (args.case,)
    suite = unittest.TestSuite(FinanceCombinations("test_" + name) for name in names)
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    changed = [name for name, digest in hashes().items() if digest != before[name]]
    print("SUMMARY " + json.dumps({
        "run": result.testsRun, "invariant_failures": len(result.failures), "errors": len(result.errors),
        "network_attempts": len(FinanceCombinations.network_attempts),
        "process_attempts": len(FinanceCombinations.process_attempts),
        "source_hashes_changed_during_run": changed,
        "evidence_scope": "temporary fixtures only; no model/browser/platform/background loops",
    }, sort_keys=True))
    return 0 if result.wasSuccessful() and not changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
