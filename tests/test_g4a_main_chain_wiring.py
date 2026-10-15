"""G4A main-chain wiring tests: procurement / inventory / ledger over the
real server routes with shared service instances (A90, A92, A96, A99-H).

Service-level behaviors (reservation recovery, reversal caps, cross-account
idempotency scope, failure windows) are covered by the existing
test_procurement_ledger / test_inventory_and_listing / test_recovery_drills
suites; this file proves the composition: one shared instance set, the
HTTP surface, and the golden order flow a human operator drives.
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from quote_fixtures import declared_quote_csv


class ProcurementLedgerHttpTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server as server_module

        self.client = TestClient(server_module.app)
        self.server = server_module

    def _import_supplier(self, sku, price="160"):
        csv_body = (
            "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
            f"{sku},不锈钢工作台,{price},件,1,50,包邮,120x60x85cm,众商,全新\n"
        ).encode("utf-8")
        payload = declared_quote_csv(csv_body).decode("utf-8")
        preview = self.client.post(
            "/commerce/supplier-imports/preview", json={"kind": "pasted_text", "content": payload}
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        confirm = self.client.post(
            f"/commerce/supplier-imports/{preview.json()['preview_id']}/confirm",
            json={
                "content_sha256": preview.json()["content_sha256"],
                "revision": preview.json()["revision"],
                "supplier": "杭州五金供应商",
                "operator": "test",
            },
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        return confirm.json()["snapshots"][0]

    def test_a90_shared_composition_and_golden_operator_flow(self):
        # one shared instance set backs every route (no per-request stores)
        self.assertIs(self.server.procurement_service._inventory, self.server.inventory_service)

        snapshot = self._import_supplier("SKU-G4A")
        register = self.client.post(
            "/commerce/inventory/register",
            json={"account_id": "acct-g4a", "sku": "SKU-G4A", "snapshot_id": snapshot["record_id"]},
        )
        self.assertEqual(register.status_code, 200, register.text)
        self.assertGreater(register.json()["sellable_base"], 0)

        allocation = self.client.get(
            "/commerce/inventory", params={"account_id": "acct-g4a", "sku": "SKU-G4A"}
        )
        self.assertEqual(allocation.status_code, 200, allocation.text)

        # one live procurement per sales order (business idempotency, A92)
        create = self.client.post(
            "/commerce/procurement/from-sales-order",
            json={"account_id": "acct-g4a", "sku": "SKU-G4A", "quantity": 2,
                  "sales_order_ref": "xy-order-9001", "opportunity_id": ""},
        )
        self.assertEqual(create.status_code, 200, create.text)
        order_id = create.json()["record_id"]
        self.assertEqual(create.json()["status"], "awaiting_approval")

        duplicate = self.client.post(
            "/commerce/procurement/from-sales-order",
            json={"account_id": "acct-g4a", "sku": "SKU-G4A", "quantity": 2,
                  "sales_order_ref": "xy-order-9001"},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

        # human ordering + receipt registration (A96): facts recorded, nothing auto-submitted
        approve = self.client.post(f"/commerce/procurement/{order_id}/approve", json={"operator": "操作者甲"})
        self.assertEqual(approve.status_code, 200, approve.text)
        supplier_order = self.client.post(
            f"/commerce/procurement/{order_id}/supplier-order",
            json={"supplier_order_id": "SO-2026-0001", "operator": "操作者甲"},
        )
        self.assertEqual(supplier_order.status_code, 200, supplier_order.text)
        receipt = self.client.post(
            f"/commerce/procurement/{order_id}/receipt",
            json={"receipt_ref": "fixture:receipt-photo", "actual_unit_cost": "161",
                  "actual_freight": "20", "operator": "操作者甲"},
        )
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertEqual(receipt.json()["status"], "received")

        # ledger projection on the same order reference (A99-H)
        spend = self.server.ledger_service.record_spend(
            category="procurement",
            amount={"state": "known", "amount": "342", "currency": "CNY"},
            order_ref="xy-order-9001",
            evidence_ref="platform:fixture:payment-voucher",
            payment_evidence_ref="platform:fixture:payment-voucher",
        )
        self.assertEqual(spend["kind"], "settled")
        entries = self.client.get("/commerce/ledger/entries", params={"order_ref": "xy-order-9001"})
        self.assertEqual(entries.status_code, 200, entries.text)
        # 2 entries: the receipt's auto-provisional (Codex E9) + this explicit settled spend
        self.assertEqual(len(entries.json()["entries"]), 2)

        listed = self.client.get("/commerce/procurement", params={"status": "received"})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertTrue(any(o["record_id"] == order_id for o in listed.json()["orders"]))

        # one order detail projects procurement + ledger side by side (G4A-10)
        detail = self.client.get(f"/commerce/orders/xy-order-9001/detail")
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()
        self.assertTrue(any(o["record_id"] == order_id for o in body["procurements"]))
        self.assertEqual(len(body["ledger_entries"]), 2)  # receipt provisional + explicit settled
        self.assertEqual(body["ledger_entries"][0]["order_ref"], "xy-order-9001")

    def test_a92_cross_account_same_ref_no_collision(self):
        snapshot = self._import_supplier("SKU-G4A2")
        for account in ("acct-a", "acct-b"):
            register = self.client.post(
                "/commerce/inventory/register",
                json={"account_id": account, "sku": "SKU-G4A2", "snapshot_id": snapshot["record_id"]},
            )
            self.assertEqual(register.status_code, 200, register.text)
        first = self.client.post(
            "/commerce/procurement/from-sales-order",
            json={"account_id": "acct-a", "sku": "SKU-G4A2", "quantity": 1, "sales_order_ref": "same-ref-01"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        # Codex E5: the business key is (account, sales ref); different
        # accounts independently procure the same platform order ref
        second = self.client.post(
            "/commerce/procurement/from-sales-order",
            json={"account_id": "acct-b", "sku": "SKU-G4A2", "quantity": 1, "sales_order_ref": "same-ref-01"},
        )
        self.assertEqual(second.status_code, 200, second.text)

    def test_a99_reversal_keeps_original_entry(self):
        entry = self.server.ledger_service.record_income(
            amount={"state": "known", "amount": "300", "currency": "CNY"},
            order_ref="xy-order-9900",
            payout_evidence_ref="platform:fixture:settlement",
        )
        reversed_entry = self.server.ledger_service.reverse(
            entry["record_id"],
            reason="fixture: partial refund",
            evidence_ref="fixture:refund-voucher",
            amount={"state": "known", "amount": "50", "currency": "CNY"},
        )
        self.assertEqual(reversed_entry["reversal_of"], entry["record_id"])
        self.assertTrue(str(reversed_entry["category"]).startswith("reversal:"))
        self.assertEqual(reversed_entry["direction"], "debit")
        original = [e for e in self.server.ledger_service.entries(order_ref="xy-order-9900")
                    if e["record_id"] == entry["record_id"]]
        self.assertEqual(len(original), 1)
        self.assertEqual(original[0]["amount"]["amount"], "300")  # original untouched


if __name__ == "__main__":
    unittest.main()
