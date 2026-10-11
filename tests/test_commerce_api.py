"""WP09 commerce pipeline API integration tests."""
from __future__ import annotations

import unittest
from quote_fixtures import declared_quote_csv

from fastapi.testclient import TestClient

from xianyu_agent import server


QUOTE_TEXT = declared_quote_csv((
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
    "GZ-1200,不锈钢工作台,150,件,2,10,包邮,120x60x85cm,众商,全新\n"
).encode("utf-8")).decode("utf-8")


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

    def test_supplier_file_probe_runs_local_fetch(self):
        preview = self.client.post(
            "/commerce/supplier-imports/preview",
            json={"kind": "pasted_text", "content": QUOTE_TEXT},
        ).json()
        self.client.post(
            f"/commerce/supplier-imports/{preview['preview_id']}/confirm",
            json={
                "content_sha256": preview["content_sha256"],
                "revision": preview["revision"],
                "supplier": "探测供应商",
                "operator": "test",
            },
        )
        response = self.client.post("/commerce/data-providers/supplier_file/probe")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["fetch"]["status"], "ok")
        self.assertGreaterEqual(body["sample_record_count"], 1)

    def test_unverified_provider_probe_refused(self):
        response = self.client.post("/commerce/data-providers/pinduoduo_duoduojinbao/probe")
        self.assertEqual(response.status_code, 409)
        self.assertIn("UNVERIFIED", response.json()["detail"])

    def test_offers_carry_freshness(self):
        self.client.post(
            "/commerce/market-observations",
            json={"sku": "GZ-1299", "title": "t", "spec": "1x1x1cm", "price": "10", "condition": "全新", "unit": "件"},
        )
        response = self.client.get("/commerce/offers", params={"provider_id": "market:xianyu_observation"})
        offers = response.json()["offers"]
        self.assertGreaterEqual(len(offers), 1)
        self.assertIn(offers[0]["freshness_meta"]["freshness"], ("current", "expired", "unknown"))

    def test_agent_jobs_submit_list_cancel(self):
        submitted = self.client.post(
            "/commerce/agent-jobs",
            json={"role": "matching", "goal_id": "opp-1", "idempotency_key": "ij-1"},
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        job = submitted.json()
        # idempotent resubmit
        again = self.client.post(
            "/commerce/agent-jobs",
            json={"role": "matching", "goal_id": "opp-1", "idempotency_key": "ij-1"},
        )
        self.assertEqual(again.json()["record_id"], job["record_id"])

        listed = self.client.get("/commerce/agent-jobs", params={"role": "matching"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["jobs"]), 1)

        cancelled = self.client.post(
            f"/commerce/agent-jobs/{job['record_id']}/cancel",
            json={"actor": "operator", "reason": "duplicate"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

        stats = self.client.get("/commerce/agent-jobs/stats")
        self.assertEqual(stats.json()["by_status"].get("cancelled"), 1)

    def test_agent_job_write_requires_account(self):
        response = self.client.post(
            "/commerce/agent-jobs",
            json={"role": "publish", "write_required": True},
        )
        self.assertEqual(response.status_code, 400)

    def test_mandate_lifecycle_and_shadow_evaluation(self):
        from datetime import datetime, timedelta, timezone

        valid_until = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        issued = self.client.post(
            "/commerce/operating-mandates",
            json={
                "operator": "操作者",
                "account_id": "acc-shadow",
                "action_types": ["faq_reply"],
                "per_transaction_limit": {"state": "known", "amount": "100", "currency": "CNY"},
                "daily_limit": {"state": "known", "amount": "500", "currency": "CNY"},
                "valid_until": valid_until,
            },
        )
        self.assertEqual(issued.status_code, 200, issued.text)
        mandate_id = issued.json()["record_id"]

        shadow = self.client.post(
            "/commerce/authorization/shadow-evaluate",
            json={"action_type": "faq_reply", "account_id": "acc-shadow"},
        )
        self.assertEqual(shadow.status_code, 200, shadow.text)
        body = shadow.json()
        self.assertTrue(body["decision"]["allowed"])
        self.assertEqual(body["mode"], "shadow")

        denied = self.client.post(
            "/commerce/authorization/shadow-evaluate",
            json={"action_type": "listing_publish", "account_id": "acc-shadow"},
        )
        self.assertFalse(denied.json()["decision"]["allowed"])

        # revoke -> the same action is refused by the live re-check
        revoked = self.client.post(
            f"/commerce/operating-mandates/{mandate_id}/revoke",
            json={"operator": "操作者", "reason": "测试撤销"},
        )
        self.assertEqual(revoked.status_code, 200)
        after = self.client.post(
            "/commerce/authorization/shadow-evaluate",
            json={"action_type": "faq_reply", "account_id": "acc-shadow"},
        )
        self.assertFalse(after.json()["decision"]["allowed"])

        metrics = self.client.get("/commerce/authorization/shadow-metrics")
        self.assertEqual(metrics.json()["total"], 3)

    def test_mandate_requires_valid_until(self):
        response = self.client.post(
            "/commerce/operating-mandates",
            json={"operator": "o", "account_id": "a", "action_types": ["faq_reply"]},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
