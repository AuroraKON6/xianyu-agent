"""G4B golden-sample finance chain over real HTTP routes (A100-A102, S/H).

Matrix 15.1 numbers are explicit fixture inputs, never production
defaults. A103/A104 (real first order) stay WAITING_INPUT / B-level.
"""
from __future__ import annotations

import threading
import unittest
from decimal import Decimal

from fastapi.testclient import TestClient

from quote_fixtures import declared_quote_csv


class _GoldenChain:
    def __init__(self, client, ledger):
        self.client = client
        self.ledger = ledger

    def make_opportunity(self, sku):
        csv_body = (
            "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
            f"{sku},不锈钢工作台,160,件,1,50,20,120x60x85cm,众商,全新\n"
        ).encode("utf-8")
        payload = declared_quote_csv(csv_body).decode("utf-8")
        preview = self.client.post(
            "/commerce/supplier-imports/preview", json={"kind": "pasted_text", "content": payload}
        )
        assert preview.status_code == 200, preview.text
        confirm = self.client.post(
            f"/commerce/supplier-imports/{preview.json()['preview_id']}/confirm",
            json={
                "content_sha256": preview.json()["content_sha256"],
                "revision": preview.json()["revision"],
                "supplier": "杭州五金供应商",
                "operator": "test",
            },
        )
        assert confirm.status_code == 200, confirm.text
        supplier_snapshot = confirm.json()["snapshots"][0]
        market = self.client.post(
            "/commerce/market-observations",
            json={"sku": sku, "title": f"工作台 {sku}", "spec": "120x60x85cm",
                  "brand": "众商", "price": "300", "condition": "全新", "unit": "件"},
        )
        assert market.status_code == 200, market.text
        match = self.client.post(
            "/commerce/product-matches",
            json={"left_snapshot_id": market.json()["snapshot"]["record_id"],
                  "right_snapshot_id": supplier_snapshot["record_id"]},
        )
        assert match.status_code == 200, match.text
        opportunity = self.client.post(
            "/commerce/opportunities",
            json={"market_snapshot_id": market.json()["snapshot"]["record_id"],
                  "supplier_snapshot_id": supplier_snapshot["record_id"],
                  "match_id": match.json()["match_id"]},
        )
        assert opportunity.status_code == 200, opportunity.text
        opportunity_id = opportunity.json()["record_id"]
        evaluate = self.client.post(
            f"/commerce/opportunities/{opportunity_id}/evaluate",
            json={
                "platform_fee_rate": "0.03",
                "packaging": {"state": "known", "amount": "5", "currency": "CNY"},
                "expected_return_loss": {"state": "known", "amount": "12", "currency": "CNY"},
                "attributable_costs": {"state": "known", "amount": "4", "currency": "CNY"},
            },
        )
        assert evaluate.status_code == 200, evaluate.text
        return opportunity_id, evaluate.json()


class GoldenSampleFinanceTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server as server_module

        self.client = TestClient(server_module.app)
        self.server = server_module
        self.chain = _GoldenChain(self.client, self.server.ledger_service)

    # ------------------------------------------------------------------- A100

    def test_a100_golden_sample_projected_then_settled(self):
        opportunity_id, evaluation = self.chain.make_opportunity("SKU-G4B")
        # expected contribution profit = 300 - 160 - 20 - 5 - 9 - 4 - 12 = 90
        conservative = evaluation["economics"]["scenarios"]["conservative"]
        self.assertEqual(Decimal(conservative["contribution_profit"]["amount"]), Decimal("90"))

        # settled reality: income 300 with payout evidence; spend 198 with
        # payment evidence (procurement 160+20 supplier + 5 packaging + 9 fee
        # + 4 attributable); matrix 15.1 says settled difference 102
        income = self.server.ledger_service.record_income(
            amount={"state": "known", "amount": "300", "currency": "CNY"},
            order_ref="xy-golden-1", opportunity_id=opportunity_id,
            payout_evidence_ref="platform:fixture:xianyu-payout",
        )
        self.assertEqual(income["kind"], "settled")
        spend = self.server.ledger_service.record_spend(
            category="procurement",
            amount={"state": "known", "amount": "198", "currency": "CNY"},
            order_ref="xy-golden-1", opportunity_id=opportunity_id,
            payment_evidence_ref="platform:fixture:supplier-payment",
        )
        self.assertEqual(spend["kind"], "settled")

        summary = self.server.ledger_service.profit_summary(opportunity_id=opportunity_id)
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("102"))
        self.assertEqual(Decimal(summary["provisional"]["net"]), Decimal("0"))

        # partial refund 50 (no fee return, no new spend): 102 - 50 = 52
        refund = self.server.ledger_service.record_spend(
            category="after_sales_refund",
            amount={"state": "known", "amount": "50", "currency": "CNY"},
            order_ref="xy-golden-1", opportunity_id=opportunity_id,
            payment_evidence_ref="platform:fixture:refund-transfer",
        )
        self.assertEqual(refund["kind"], "settled")
        summary = self.server.ledger_service.profit_summary(opportunity_id=opportunity_id)
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("52"))
        # the 12 reserve must NOT be deducted again: no reserve entry exists
        reserve_entries = [e for e in self.server.ledger_service.entries(opportunity_id=opportunity_id)
                           if "reserve" in str(e.get("category") or "")]
        self.assertEqual(reserve_entries, [])

        # HTTP summary route agrees with the service view
        response = self.client.get("/commerce/ledger/summary", params={"opportunity_id": opportunity_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Decimal(response.json()["settled"]["net"]), Decimal("52"))

        # order detail projection shows every line traceable
        detail = self.client.get("/commerce/orders/xy-golden-1/detail")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(len(detail.json()["ledger_entries"]), 3)  # income + spend + refund

    def test_a102_provisional_never_counts_as_cash(self):
        opportunity_id, _ = self.chain.make_opportunity("SKU-G4B2")
        self.server.ledger_service.record_income(
            amount={"state": "known", "amount": "300", "currency": "CNY"},
            order_ref="xy-golden-2", opportunity_id=opportunity_id,
            # no payout evidence yet -> provisional
        )
        self.server.ledger_service.record_spend(
            category="procurement",
            amount={"state": "known", "amount": "180", "currency": "CNY"},
            order_ref="xy-golden-2", opportunity_id=opportunity_id,
            # no payment evidence -> provisional
        )
        summary = self.server.ledger_service.profit_summary(opportunity_id=opportunity_id)
        self.assertEqual(Decimal(summary["provisional"]["net"]), Decimal("120"))
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("0"))
        self.assertTrue(any("not money in hand" in note for note in summary["notes"]))
        # HTTP view keeps the tiers separated the same way
        response = self.client.get("/commerce/ledger/summary", params={"opportunity_id": opportunity_id})
        body = response.json()
        self.assertEqual(Decimal(body["settled"]["net"]), Decimal("0"))
        self.assertEqual(Decimal(body["provisional"]["net"]), Decimal("120"))

    # ------------------------------------------------------------------- A101

    def test_a101_reversal_caps_conflicts_and_concurrency(self):
        opportunity_id, _ = self.chain.make_opportunity("SKU-G4B3")
        entry = self.server.ledger_service.record_income(
            amount={"state": "known", "amount": "300", "currency": "CNY"},
            order_ref="xy-golden-3", opportunity_id=opportunity_id,
            payout_evidence_ref="platform:fixture:payout",
        )
        from xianyu_agent.ledger_service import LedgerError

        # a legal partial reversal lands exactly
        partial = self.server.ledger_service.reverse(
            entry["record_id"], reason="fixture: 部分退款 50",
            amount={"state": "known", "amount": "50", "currency": "CNY"},
            evidence_ref="fixture:refund-1",
        )
        self.assertEqual(Decimal(partial["amount"]["amount"]), Decimal("50"))

        # double full reversal refused: 50 already reversed, 300 > cap
        with self.assertRaises(LedgerError) as ctx:
            self.server.ledger_service.reverse(
                entry["record_id"], reason="fixture: 重复全额冲正被拒",
                evidence_ref="fixture:refund-2",
            )
        self.assertIn("exceeds original", str(ctx.exception))
        # oversized partial refused
        with self.assertRaises(LedgerError):
            self.server.ledger_service.reverse(
                entry["record_id"], reason="fixture: 超额部分冲正被拒",
                amount={"state": "known", "amount": "251", "currency": "CNY"},
                evidence_ref="fixture:refund-3",
            )
        # wrong currency refused
        with self.assertRaises(LedgerError) as ctx:
            self.server.ledger_service.reverse(
                entry["record_id"], reason="fixture: 错币种冲正被拒",
                amount={"state": "known", "amount": "10", "currency": "USD"},
                evidence_ref="fixture:refund-4",
            )
        self.assertIn("currency", str(ctx.exception))
        # unknown original refused, original entries never touched
        with self.assertRaises(LedgerError):
            self.server.ledger_service.reverse(
                "entry_does_not_exist", reason="fixture: 未知原分录",
                evidence_ref="fixture:refund-5",
            )
        original = [e for e in self.server.ledger_service.entries(order_ref="xy-golden-3")
                    if e["record_id"] == entry["record_id"]]
        self.assertEqual(len(original), 1)
        self.assertEqual(Decimal(original[0]["amount"]["amount"]), Decimal("300"))
        # exactly-once-to-zero: reverse the remaining 250, then any more fails
        self.server.ledger_service.reverse(
            entry["record_id"], reason="fixture: 冲正剩余 250",
            amount={"state": "known", "amount": "250", "currency": "CNY"},
            evidence_ref="fixture:refund-6",
        )
        summary = self.server.ledger_service.profit_summary(opportunity_id=opportunity_id)
        self.assertEqual(Decimal(summary["settled"]["net"]), Decimal("0"))
        with self.assertRaises(LedgerError):
            self.server.ledger_service.reverse(
                entry["record_id"], reason="fixture: 归零后再冲正被拒",
                amount={"state": "known", "amount": "1", "currency": "CNY"},
                evidence_ref="fixture:refund-7",
            )

        # concurrent partial reversals must not break the cumulative cap
        entry2 = self.server.ledger_service.record_income(
            amount={"state": "known", "amount": "100", "currency": "CNY"},
            order_ref="xy-golden-4", opportunity_id=opportunity_id,
            payout_evidence_ref="platform:fixture:payout-2",
        )
        errors = []

        def worker():
            try:
                self.server.ledger_service.reverse(
                    entry2["record_id"], reason="fixture: 并发冲正竞争",
                    amount={"state": "known", "amount": "70", "currency": "CNY"},
                    evidence_ref="fixture:concurrent-refund",
                )
            except LedgerError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(errors), 1)  # exactly one 70 wins, cap rejects the other
        net = self.server.ledger_service.profit_summary(opportunity_id=opportunity_id)
        settled_rows = [e for e in self.server.ledger_service.entries(order_ref="xy-golden-4")]
        signed = sum(
            (Decimal(e["amount"]["amount"]) if e["direction"] == "credit" else -Decimal(e["amount"]["amount"]))
            for e in settled_rows
        )
        self.assertEqual(signed, Decimal("30"))  # 100 - 70, never 100 - 140
        self.assertEqual(Decimal(net["settled"]["net"]), Decimal("30"))


if __name__ == "__main__":
    unittest.main()
