from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


def _ready_manager():
    from xianyu_agent.resale import ResaleManager

    data_dir = Path(tempfile.mkdtemp())
    manager = ResaleManager(data_dir)
    candidate = manager.create_candidate(
        {
            "title": "商用不锈钢操作台 带制冷 餐饮厨房设备",
            "description": "适合餐饮后厨、奶茶店、食堂备餐区使用。",
            "source_platform": "pdd",
            "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=ops-table",
            "supplier_name": "夹具供应商A",
            "supplier_sku": "OPS-1200-304",
            "supplier_price": 680.0,
            "supplier_shipping": 80.0,
            "target_sale_price": 1280.0,
            "category": "商用厨具",
            "specifications": {
                "size": "120x60x80cm",
                "material": "304不锈钢",
                "feature": "带制冷",
            },
        }
    )
    manager.verify_candidate(
        candidate["candidate_id"],
        {
            "supplier_verified": True,
            "supplier_verification_note": "供应商确认可发货，图片需人工确认授权。",
        },
    )
    manager.set_decision(candidate["candidate_id"], "approve")
    return manager, data_dir, manager.get_candidate(candidate["candidate_id"])


class ListingStructuredDraftTests(unittest.TestCase):
    def test_listing_draft_contains_all_playbook_fields(self):
        manager, _, candidate = _ready_manager()

        draft = manager.create_listing_draft(candidate["candidate_id"])

        self.assertEqual(draft["title"], "商用不锈钢操作台 带制冷 餐饮厨房设备")
        self.assertIn("search_keywords", draft)
        self.assertIn("lead_image_requirement", draft)
        self.assertIn("specification_table", draft)
        self.assertIn("price_ladder", draft)
        self.assertIn("trust_paragraph", draft)
        self.assertIn("delivery_after_sales_notes", draft)
        self.assertIn("operator_verification_questions", draft)
        self.assertTrue(draft["structured_fields_complete"])

    def test_structured_fields_are_actionable_for_operator_review(self):
        manager, _, candidate = _ready_manager()

        draft = manager.create_listing_draft(candidate["candidate_id"])

        self.assertGreaterEqual(len(draft["search_keywords"]), 5)
        self.assertLessEqual(len(draft["search_keywords"]), 10)
        self.assertTrue(any("操作台" in item for item in draft["search_keywords"]))
        self.assertIn("授权", json.dumps(draft["lead_image_requirement"], ensure_ascii=False))
        self.assertEqual(draft["specification_table"][0]["material"], "304不锈钢")
        self.assertEqual(draft["specification_table"][0]["price"], 1280.0)
        self.assertTrue(any(item["role"] == "visible_entry" for item in draft["price_ladder"]))
        self.assertIn("质量", draft["trust_paragraph"])
        self.assertIn("shipping_method", draft["delivery_after_sales_notes"])
        self.assertTrue(any("图片" in item for item in draft["operator_verification_questions"]))

    def test_executable_draft_file_preserves_structured_fields(self):
        manager, data_dir, candidate = _ready_manager()

        draft = manager.create_listing_draft(candidate["candidate_id"])
        draft_file = data_dir / "drafts" / f"{draft['draft_id']}.json"
        payload = json.loads(draft_file.read_text(encoding="utf-8"))

        self.assertEqual(payload["id"], draft["draft_id"])
        self.assertEqual(payload["search_keywords"], draft["search_keywords"])
        self.assertEqual(payload["specification_table"], draft["specification_table"])
        self.assertEqual(payload["price_ladder"], draft["price_ladder"])
        self.assertIn("operator_verification_questions", payload)

    def test_listing_draft_work_order_snapshot_includes_structured_fields(self):
        from fastapi.testclient import TestClient
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.resale import ResaleManager
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.account_commercial_terms import AccountCommercialTermsStore
        from preflight_helpers import (
            add_preflight_account,
            archive_candidate_evidence_fixtures,
            build_runtime_store,
            evidence_verify_payload,
            record_ready_commercial_terms,
        )
        from unittest.mock import patch

        data_dir = Path(tempfile.mkdtemp())
        db = DBManager(str(data_dir / "accounts.db"))
        add_preflight_account(db, "acct_1")
        runtime = build_runtime_store(data_dir, "acct_1")
        resale = ResaleManager(data_dir / "resale")
        work_orders = WorkOrderManager(data_dir / "work_orders.json")
        terms = AccountCommercialTermsStore(data_dir / "commercial_terms.json")
        with patch.object(server, "resale_manager", resale), patch.object(
            server, "db_manager", db
        ), patch.object(server, "account_runtime_store", runtime), patch.object(
            server, "account_commercial_terms_store", terms
        ), patch.object(
            server, "work_order_manager", work_orders
        ):
            client = TestClient(server.app)
            create_resp = client.post(
                "/resale/candidates",
                json={
                    "title": "商用不锈钢操作台 带制冷",
                    "source_platform": "pdd",
                    "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=ops-table",
                    "supplier_name": "夹具供应商A",
                    "supplier_sku": "OPS-1200-304",
                    "supplier_price": 680,
                    "supplier_shipping": 80,
                    "target_sale_price": 1280,
                    "category": "商用厨具",
                    "specifications": {"material": "304不锈钢", "size": "120x60x80cm"},
                    "images": ["test-fixture/owned-ops-table.jpg"],
                },
            )
            candidate_id = create_resp.json()["candidate"]["candidate_id"]
            record_ready_commercial_terms(
                terms, resale, resale.get_candidate(candidate_id), "acct_1"
            )
            client.post(f"/resale/candidates/{candidate_id}/verify", json=evidence_verify_payload())
            archive_candidate_evidence_fixtures(resale, candidate_id)
            client.post(f"/resale/candidates/{candidate_id}/decision", json={"decision": "approve"})

            response = client.post(f"/resale/candidates/{candidate_id}/listing-draft?cookie_id=acct_1")

            self.assertEqual(response.status_code, 200, response.text)
            snapshot = response.json()["work_order"]["approvals"][0]["draft_snapshot"]
            self.assertIn("search_keywords", snapshot)
            self.assertIn("lead_image_requirement", snapshot)
            self.assertIn("specification_table", snapshot)
            self.assertIn("price_ladder", snapshot)
            self.assertIn("operator_verification_questions", snapshot)
            self.assertIn("七日无理由", snapshot["description"])
            self.assertEqual(
                snapshot["delivery_after_sales_notes"]["return_policy"],
                "seven_day",
            )


if __name__ == "__main__":
    unittest.main()
