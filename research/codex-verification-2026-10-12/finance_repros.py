"""Independent finance review evidence, not production acceptance.

Run: python -B finance_repros.py --case all
Cases assert EXPECTED invariants; current defects intentionally fail unittest.
The real server is imported with temporary data and a stub adapter. Network
and external processes are denied; only Windows event-loop socketpair setup
is allowed. Ledger cases seed service facts explicitly, not via HTTP writes.
Only the sibling finance-repros.log and temporary fixture data are written.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
LOG = Path(__file__).with_name("finance-repros.log")
CASES = {
    "http_paid_body_not_authority": ("A91", "xianyu_agent/server.py:7309"),
    "http_missing_order_payment_gate": ("A91", "xianyu_agent/server.py:7313"),
    "http_order_account_owner": ("A91/A92", "xianyu_agent/server.py:7310"),
    "http_order_sku_owner": ("A91/A98", "xianyu_agent/server.py:7311"),
    "http_cross_account_order_identity": ("A92", "xianyu_agent/procurement.py:96"),
    "http_legacy_purchase_authority": ("A90/A92", "xianyu_agent/server.py:5942"),
    "http_procurement_budget_reservation": ("A92/A94", "xianyu_agent/procurement.py:149"),
    "http_unknown_freight_gate": ("A93", "xianyu_agent/procurement.py:121"),
    "http_moq_gate": ("A93", "xianyu_agent/procurement.py:130"),
    "http_supplier_stock_sku_binding": ("A93", "xianyu_agent/server.py:7384"),
    "http_recovery_orphan_lock": ("A90/A94", "xianyu_agent/server.py:318"),
    "http_receipt_accounting_projection": ("A90/A96/A100", "xianyu_agent/server.py:7348"),
    "http_receipt_replay": ("A96/A99", "xianyu_agent/procurement.py:187"),
    "http_missing_procurement_404": ("A90", "xianyu_agent/server.py:7299"),
    "ledger_payment_evidence_authority": ("A102/A103", "xianyu_agent/ledger_service.py:76"),
    "ledger_payment_fact_replay": ("A99/A100", "xianyu_agent/ledger_service.py:84"),
    "ledger_negative_reversal": ("A101", "xianyu_agent/ledger_service.py:145"),
}


def money(value):
    return {"state": "known", "amount": str(value), "currency": "CNY"}


class FinanceRepros(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.tmp = tempfile.TemporaryDirectory(prefix="codex-finance-repros-")
        cls.base = Path(cls.tmp.name)
        cls.original_cwd = Path.cwd()
        cls.network_attempts = []
        cls.process_attempts = []
        os.chdir(cls.base)
        cls.stack.enter_context(patch.dict(os.environ, {
            "DATA_DIR": str(cls.base / "bootstrap"),
            "DB_PATH": str(cls.base / "bootstrap" / "fixture.db"),
            "XIANYU_ADAPTER": "stub", "XIANYU_COOKIES": "",
            "SHADOW_OBSERVER_ENABLED": "0", "RADAR_DISPATCHER_ENABLED": "0",
            "DISCOVERY_ENABLED": "0", "GITHUB_DISCOVERY_ENABLED": "0",
            "AUTO_REPLY_ENABLED": "0", "MARKET_CHANNEL_PROBE_AUTHORIZED": "0",
            "DEEPSEEK_API_KEY": "", "MIMO_API_KEY": "", "MIMO_HERMES_KEY": "",
            "MIMO_LISTING_KEY": "", "MIMO_DISCOVERY_KEY": "",
        }))

        def guarded_connect(original):
            def guard(sock, address):
                internal = any(
                    frame.name in ("socketpair", "_fallback_socketpair")
                    and Path(frame.filename).name == "socket.py"
                    for frame in traceback.extract_stack()
                )
                if internal:
                    return original(sock, address)
                cls.network_attempts.append(repr(address))
                raise AssertionError("Network disabled by review harness")
            return guard

        def deny_process(*args, **kwargs):
            cls.process_attempts.append("blocked")
            raise AssertionError("External processes disabled by review harness")

        for method in ("connect", "connect_ex"):
            original = getattr(socket.socket, method)
            cls.stack.enter_context(patch.object(socket.socket, method, guarded_connect(original)))
        cls.stack.enter_context(patch.object(subprocess, "Popen", deny_process))
        cls.stack.enter_context(patch.object(socket, "create_connection", side_effect=AssertionError("Network disabled")))
        sys.path[:0] = [str(REPO), str(REPO / "tests")]
        try:
            from xianyu_agent import server
            cls.server = server
            if server.workflow.adapter.name != "stub":
                raise AssertionError("Review requires a stub adapter")
            if server.settings.data_dir != cls.base / "bootstrap":
                raise AssertionError("Server escaped temporary data boundary")
        except BaseException:
            cls._close()
            raise

    @classmethod
    def _close(cls):
        server = getattr(cls, "server", None)
        if server is not None:
            try:
                server.pi_task_runner.stop()
                server.pi_runtime.stop()
            finally:
                server.db_manager.close()
        os.chdir(cls.original_cwd)
        cls.stack.close()
        cls.tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.network_attempts or cls.process_attempts:
                raise AssertionError("Forbidden I/O was attempted: %r / %r" % (
                    cls.network_attempts, cls.process_attempts))
        finally:
            cls._close()

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
        try:
            self.client.close()
        finally:
            self.s.db_manager.close()

    def seed_quote(self, sku="SKU-X", moq=1, shipping=None):
        from quote_fixtures import with_declared_quote_provenance
        from xianyu_agent.commerce.contracts import build_offer_snapshot, utc_now
        quote = with_declared_quote_provenance(build_offer_snapshot(
            provider_id="supplier_file:isolated-fixture", source_item_id=sku,
            source_sku_id=sku, observed_at=utc_now(), eligible_price=money(160),
            shipping_quote=money(20) if shipping is None else shipping,
            stock_state="in_stock", stock_quantity=50, moq=moq,
            payload_hash="fixture-" + sku + str(moq) + str(shipping),
            source_ref="fixture:review-only",
        ))
        self.s.commerce_repositories.offers.add(quote)
        for account in ("acct-a", "acct-b"):
            response = self.client.post("/commerce/inventory/register", json={
                "account_id": account, "sku": sku, "snapshot_id": quote["record_id"],
            })
            self.assertEqual(response.status_code, 200, response.text)
        return quote

    def runtime_order(self, ref="fixture-sale", status="paid"):
        # Synthetic runtime facts establish identity, not authenticated payment.
        return self.s.order_runtime_store.upsert_order(
            order_id=ref, cookie_id="acct-a", item_id="SKU-X", status=status,
            amount=300, source="fixture:runtime", metadata={"fixture_only": True},
        )

    def create(self, ref="fixture-sale", account="acct-a", sku="SKU-X", **extra):
        return self.client.post("/commerce/procurement/from-sales-order", json={
            "account_id": account, "sku": sku, "quantity": 1,
            "sales_order_ref": ref, **extra,
        })

    def assert_blocked(self, response, invariant):
        self.assertIn(response.status_code, (400, 403, 404, 409, 422),
                      "%s; actual HTTP %s: %s" % (invariant, response.status_code, response.text))
        self.assertEqual(self.s.procurement_service.list(), [], invariant)
        self.assertEqual(self.s.inventory_service.list_locks(), [], invariant)

    def ordered_fixture(self):
        from xianyu_agent.commerce.contracts import build_procurement_order
        # Seed domain state directly so receipt tests do not depend on the paid gate.
        order = build_procurement_order(
            supplier_ref="fixture:supplier", canonical_sku="SKU-X", quantity=1,
            unit_cost=money(160), shipping_cost=money(20), sales_order_ref="fixture-sale",
            account_id="acct-a", status="approved",
        )
        key = "proc:" + order["record_id"]
        order["reservation_key"] = key
        self.s.inventory_service.lock_for_order(
            account_id="acct-a", sku="SKU-X", order_id=key, quantity=1,
        )
        self.s.procurement_service._store.append(order)
        response = self.client.post(
            "/commerce/procurement/%s/supplier-order" % order["record_id"],
            json={"supplier_order_id": "fixture:supplier-1", "operator": "fixture"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return order

    def receipt(self, order):
        return self.client.post("/commerce/procurement/%s/receipt" % order["record_id"], json={
            "receipt_ref": "fixture:receipt", "actual_unit_cost": "160",
            "actual_freight": "20", "operator": "fixture",
        })

    def test_http_paid_body_not_authority(self):
        self.runtime_order(status="pending_payment")
        self.assert_blocked(self.create(paid=True, status="paid"),
                            "Body paid=true must not override authoritative pending_payment")

    def test_http_missing_order_payment_gate(self):
        self.assert_blocked(self.create("nonexistent-order", paid=False),
                            "An absent platform order cannot reserve procurement stock")

    def test_http_order_account_owner(self):
        self.runtime_order()
        self.assert_blocked(self.create(account="acct-b"),
                            "Procurement account must match the runtime order account")

    def test_http_order_sku_owner(self):
        self.runtime_order()
        self.seed_quote("SKU-Y")
        self.assert_blocked(self.create(sku="SKU-Y"),
                            "Procurement SKU must match the runtime order item")

    def test_http_cross_account_order_identity(self):
        first = self.create("same-ref", account="acct-a")
        self.assertEqual(first.status_code, 200, first.text)
        second = self.create("same-ref", account="acct-b")
        self.assertEqual(second.status_code, 200,
                         "A92 requires distinct account/order keys; actual: " + second.text)

    def test_http_legacy_purchase_authority(self):
        self.ordered_fixture()
        task = self.s.resale_manager.create_purchase_task({
            "draft_id": "fixture:draft", "xianyu_order_id": "fixture-sale",
            "cookie_id": "acct-a", "quantity": 1, "total_cost": 180,
        })
        response = self.client.post(
            "/resale/purchase-tasks/%s/mark-ordered" % task["purchase_task_id"],
            json={"supplier_order_id": "fixture:supplier-2"},
        )
        self.assertIn(response.status_code, (403, 409),
                      "Legacy writes must not order the same sale independently; actual: " + response.text)

    def test_http_procurement_budget_reservation(self):
        response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        reserved = self.s.budget_reservation_store.list(account_id="acct-a", status="reserved")
        self.assertTrue(reserved, "Accepted procurement must own a persisted cash reservation")

    def test_http_unknown_freight_gate(self):
        self.seed_quote(shipping={"state": "unknown"})
        self.assert_blocked(self.create(), "Unknown destination freight must block procurement")

    def test_http_moq_gate(self):
        self.seed_quote(moq=5)
        self.assert_blocked(self.create(), "Quantity 1 cannot use a quote with MOQ 5")

    def test_http_supplier_stock_sku_binding(self):
        quote = self.s.commerce_repositories.offers.list(source_item_id="SKU-X")[0]
        response = self.client.post("/commerce/inventory/register", json={
            "account_id": "acct-a", "sku": "SKU-OTHER", "snapshot_id": quote["record_id"],
        })
        self.assertIn(response.status_code, (400, 409, 422),
                      "Stock snapshot SKU must match registered SKU; actual: " + response.text)

    def test_http_recovery_orphan_lock(self):
        from xianyu_agent.inventory import InventoryAllocationService
        from xianyu_agent.procurement import ProcurementService
        self.s.inventory_service.lock_for_order(
            account_id="acct-a", sku="SKU-X", order_id="proc:missing", quantity=2,
        )
        self.s.inventory_service = InventoryAllocationService(self.root, safety_margin=0)
        self.s.procurement_service = ProcurementService(
            self.root, self.s.commerce_repositories, self.s.inventory_service,
            self.s.supplier_import_service,
        )

        def suppress_background(coroutine, *args, **kwargs):
            coroutine.close()
            return None

        with patch.object(asyncio, "create_task", suppress_background):
            asyncio.run(self.s.on_startup())
        response = self.client.get("/commerce/inventory", params={"account_id": "acct-a", "sku": "SKU-X"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["locked"], 0,
                         "Startup recovery must reconcile a missing-procurement reservation")

    def test_http_receipt_accounting_projection(self):
        order = self.ordered_fixture()
        response = self.receipt(order)
        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get("/commerce/orders/fixture-sale/detail")
        self.assertEqual(detail.status_code, 200, detail.text)
        entries = detail.json()["ledger_entries"]
        self.assertTrue(entries, "Recorded actual costs must reach the authoritative provisional ledger")
        self.assertEqual(entries[0]["kind"], "provisional", "Receipt is not payment evidence")

    def test_http_receipt_replay(self):
        order = self.ordered_fixture()
        first = self.receipt(order)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.receipt(order)
        self.assertEqual(second.status_code, 200, "Identical receipt retry must be idempotent: " + second.text)
        self.assertEqual(first.json(), second.json(), "A replay must not mutate the original fact")

    def test_http_missing_procurement_404(self):
        response = self.client.get("/commerce/procurement/missing-id")
        self.assertEqual(response.status_code, 404, "Missing procurement must not be a server error")

    def test_ledger_payment_evidence_authority(self):
        self.s.ledger_service.record_income(
            amount=money(100), opportunity_id="fixture-opp", order_ref="fixture-sale",
            payout_evidence_ref="does-not-exist",
        )
        response = self.client.get("/commerce/ledger/summary", params={"opportunity_id": "fixture-opp"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["settled"]["net"], "0",
                         "A nonempty string is not authenticated payout evidence")

    def test_ledger_payment_fact_replay(self):
        for _ in range(2):
            self.s.ledger_service.record_income(
                amount=money(100), opportunity_id="fixture-opp", order_ref="fixture-sale",
                payout_evidence_ref="fixture:same-payment-fact",
            )
        response = self.client.get("/commerce/ledger/entries", params={"order_ref": "fixture-sale"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["entries"]), 1,
                         "One payment fact cannot become two income entries")

    def test_ledger_negative_reversal(self):
        from xianyu_agent.ledger_service import LedgerError
        original = self.s.ledger_service.record_income(
            amount=money(100), payout_evidence_ref="fixture:payout",
        )
        with self.assertRaises((LedgerError, ValueError), msg="Negative reversal must be rejected"):
            self.s.ledger_service.reverse(original["record_id"], reason="fixture", amount=money(-50))


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    names = list(CASES) if args.case == "all" else [args.case]
    with LOG.open("w", encoding="utf-8", newline="\n") as log:
        output = Tee(sys.stdout, log)
        output.write("EXPECTED-INVARIANT REVIEW REPROS; fixture evidence only; failures expose defects\n")
        output.write("BOUNDARY: temporary data/SQLite; stub adapter; no service network/model/platform/MCP\n")
        for name in names:
            ids, ref = CASES[name]
            output.write("CASE %s | %s | %s\n" % (name, ids, REPO / ref))
        suite = unittest.TestSuite(FinanceRepros("test_" + name) for name in names)
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        output.write("BOUNDARY: network attempts=%d; external process attempts=%d; explicit DB close in teardown\n" % (
            len(FinanceRepros.network_attempts), len(FinanceRepros.process_attempts)))
        output.flush()
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
