"""G2C deterministic-card completeness tests: the decision card must answer
material expiry and per-use permission questions with freshly recomputed
facts (A60 engineering side; the real card itself stays WAITING_INPUT).
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient


class DecisionCardMaterialStatusTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server as server_module

        self.client = TestClient(server_module.app)

    def _make_opportunity(self, sku):
        from quote_fixtures import declared_quote_csv

        csv_body = (
            "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
            f"{sku},不锈钢工作台,160,件,1,50,包邮,120x60x85cm,众商,全新\n"
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
        supplier_snapshot = confirm.json()["snapshots"][0]
        market = self.client.post(
            "/commerce/market-observations",
            json={"sku": sku, "title": f"工作台 {sku}", "spec": "120x60x85cm",
                  "brand": "众商", "price": "300", "condition": "全新", "unit": "件"},
        )
        self.assertEqual(market.status_code, 200, market.text)
        match = self.client.post(
            "/commerce/product-matches",
            json={"left_snapshot_id": market.json()["snapshot"]["record_id"],
                  "right_snapshot_id": supplier_snapshot["record_id"]},
        )
        self.assertEqual(match.status_code, 200, match.text)
        opportunity = self.client.post(
            "/commerce/opportunities",
            json={"market_snapshot_id": market.json()["snapshot"]["record_id"],
                  "supplier_snapshot_id": supplier_snapshot["record_id"],
                  "match_id": match.json()["match_id"]},
        )
        self.assertEqual(opportunity.status_code, 200, opportunity.text)
        return opportunity.json()

    def test_card_answers_material_expiry_and_use_permissions(self):
        opportunity = self._make_opportunity("SKU-G2C")
        card = self.client.get(f"/commerce/opportunities/{opportunity['record_id']}/decision-card")
        self.assertEqual(card.status_code, 200, card.text)
        body = card.json()
        material = body.get("material_status")
        self.assertIsInstance(material, list) and self.assertEqual(len(material), 1)
        entry = material[0]
        self.assertEqual(entry["sku"], "SKU-G2C")
        self.assertTrue(entry["expires_at"])  # 资料何时到期
        self.assertEqual(entry["source_kind"], "supplier_original")
        for use in ("local_analysis", "external_model", "business_use"):
            self.assertIn(use, entry["use_permissions"])
            # no confirmation exists yet -> honestly ineligible with reasons
            self.assertFalse(entry["use_permissions"][use]["eligible"])
            self.assertTrue(entry["use_permissions"][use]["reasons"])
        # the card also carries the deterministic economics view
        self.assertIn("economics", body)
        self.assertIn("decisions", body)


if __name__ == "__main__":
    unittest.main()
