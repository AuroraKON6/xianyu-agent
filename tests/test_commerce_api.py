"""WP09 commerce pipeline API integration tests."""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from xianyu_agent import server


QUOTE_TEXT = (
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
    "GZ-1200,不锈钢工作台,150,件,2,10,包邮,120x60x85cm,众商,全新\n"
)


class CommercePipelineApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_full_thin_loop_over_http(self):
        # 1. supplier import preview -> confirm
        preview = self.client.post(
            "/commerce/supplier-imports/preview",
            json={"kind": "pasted_text", "content": QUOTE_TEXT},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_body = preview.json()
        self.assertEqual(preview_body["summary"]["ok"], 1)

        confirm = self.client.post(
            f"/commerce/supplier-imports/{preview_body['preview_id']}/confirm",
            json={
                "content_sha256": preview_body["content_sha256"],
                "revision": preview_body["revision"],
                "supplier": "杭州五金供应商",
                "operator": "test",
            },
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        supplier_snapshot = confirm.json()["snapshots"][0]
        self.assertEqual(supplier_snapshot["eligible_price"]["amount"], "150")

        # 2. market observation
        market = self.client.post(
            "/commerce/market-observations",
            json={
                "sku": "GZ-1200",
                "title": "不锈钢工作台 GZ-1200 全新",
                "spec": "120x60x85cm",
                "brand": "众商",
                "price": "299",
                "condition": "全新",
                "unit": "件",
            },
        )
        self.assertEqual(market.status_code, 200, market.text)
        market_snapshot = market.json()["snapshot"]

        # 3. match
        match = self.client.post(
            "/commerce/product-matches",
            json={
                "left_snapshot_id": market_snapshot["record_id"],
                "right_snapshot_id": supplier_snapshot["record_id"],
            },
        )
        self.assertEqual(match.status_code, 200, match.text)
        match_body = match.json()
        self.assertEqual(match_body["grade"], "EXACT")

        # 4. opportunity + economics
        opportunity = self.client.post(
            "/commerce/opportunities",
            json={
                "market_snapshot_id": market_snapshot["record_id"],
                "supplier_snapshot_id": supplier_snapshot["record_id"],
                "match_id": match_body["match_id"],
            },
        )
        self.assertEqual(opportunity.status_code, 200, opportunity.text)
        opportunity_id = opportunity.json()["record_id"]

        evaluated = self.client.post(
            f"/commerce/opportunities/{opportunity_id}/evaluate",
            json={
                "platform_fee_rate": "0.03",
                "packaging": {"state": "known", "amount": "5", "currency": "CNY"},
                "expected_return_loss": {"state": "known", "amount": "12", "currency": "CNY"},
                "attributable_costs": {"state": "known", "amount": "4", "currency": "CNY"},
            },
        )
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        evaluated_body = evaluated.json()
        self.assertTrue(evaluated_body["qualification"]["qualified"])
        self.assertEqual(evaluated_body["opportunity"]["status"], "ready_for_test")

        # 5. decision card + funnel
        card = self.client.get(f"/commerce/opportunities/{opportunity_id}/decision-card")
        self.assertEqual(card.status_code, 200, card.text)
        card_body = card.json()
        self.assertEqual(len(card_body["decisions"]), 1)
        self.assertEqual(card_body["decisions"][0]["actor_role"], "pricing_agent")
        self.assertEqual(card_body["funnel"]["discovered_total"], 1)

        funnel = self.client.get("/commerce/opportunities/funnel")
        self.assertEqual(funnel.status_code, 200)
        self.assertEqual(funnel.json()["cost_complete"], 1)

    def test_confirm_with_wrong_hash_is_409(self):
        preview = self.client.post(
            "/commerce/supplier-imports/preview",
            json={"kind": "pasted_text", "content": QUOTE_TEXT},
        ).json()
        response = self.client.post(
            f"/commerce/supplier-imports/{preview['preview_id']}/confirm",
            json={
                "content_sha256": "tampered",
                "revision": preview["revision"],
                "supplier": "s",
                "operator": "test",
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_invalid_preview_kind_is_400(self):
        response = self.client.post(
            "/commerce/supplier-imports/preview",
            json={"kind": "pdf", "content": "binary-ish"},
        )
        self.assertEqual(response.status_code, 400)

    def test_market_observation_requires_sku_and_price(self):
        response = self.client.post(
            "/commerce/market-observations",
            json={"sku": "", "price": ""},
        )
        self.assertEqual(response.status_code, 400)

    def test_offers_listing(self):
        self.client.post(
            "/commerce/market-observations",
            json={"sku": "GZ-1201", "title": "t", "spec": "1x1x1cm", "price": "10", "condition": "全新"},
        )
        response = self.client.get("/commerce/offers", params={"provider_id": "market:xianyu_observation"})
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["total"], 1)

    def test_data_providers_registry_projection(self):
        response = self.client.get("/commerce/data-providers")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        by_id = {p["provider_id"]: p for p in body["providers"]}
        self.assertIn("supplier_file", by_id)
        self.assertIn("pinduoduo_duoduojinbao", by_id)
        self.assertEqual(by_id["pinduoduo_duoduojinbao"]["status"], "UNVERIFIED")
        # nothing is VERIFIED in this baseline
        self.assertEqual(body["verified_count"], 0)


if __name__ == "__main__":
    unittest.main()
