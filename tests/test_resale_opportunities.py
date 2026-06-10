"""Tests for resale opportunity Milestone 1."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestResaleCandidateCreation(unittest.TestCase):
    """Test creating resale candidates with different configurations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmpdir)
        from xianyu_agent.resale import ResaleManager
        self.manager = ResaleManager(self.data_dir)

    def test_create_domestic_xianyu_candidate(self):
        result = self.manager.create_candidate({
            "title": "蓝牙耳机",
            "source_platform": "pdd",
            "source_url": "https://pdd.example.com/item/123",
            "supplier_price": 25.0,
            "supplier_shipping": 3.0,
            "market_scope": "domestic",
            "sales_platform": "xianyu",
            "target_sale_price": 59.0,
            "category": "数码配件",
        })
        self.assertEqual(result["status"], "evaluating")
        self.assertEqual(result["source_platform"], "pdd")
        self.assertEqual(result["market_scope"], "domestic")
        self.assertEqual(result["sales_platform"], "xianyu")
        self.assertIn("economics", result)
        self.assertIn("risk_level", result)

    def test_create_cross_border_shopee_candidate(self):
        result = self.manager.create_candidate({
            "title": "Phone case silicone",
            "source_platform": "1688",
            "source_url": "https://1688.example.com/offer/456",
            "supplier_price": 5.0,
            "supplier_shipping": 2.0,
            "market_scope": "cross_border",
            "sales_platform": "shopee",
            "target_sale_price": 30.0,
            "category": "phone accessories",
        })
        self.assertEqual(result["status"], "evaluating")
        self.assertEqual(result["market_scope"], "cross_border")
        self.assertEqual(result["sales_platform"], "shopee")

    def test_create_lazada_candidate(self):
        result = self.manager.create_candidate({
            "title": "USB cable",
            "source_platform": "1688",
            "supplier_price": 3.0,
            "supplier_shipping": 1.0,
            "market_scope": "cross_border",
            "sales_platform": "lazada",
            "target_sale_price": 15.0,
            "category": "electronics",
        })
        self.assertEqual(result["sales_platform"], "lazada")


class TestResaleEconomics(unittest.TestCase):
    """Test profit and margin calculations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from xianyu_agent.resale import ResaleManager
        self.manager = ResaleManager(Path(self.tmpdir))

    def test_gross_profit_calculation(self):
        econ = self.manager.compute_economics(
            supplier_price=25.0,
            supplier_shipping=3.0,
            target_sale_price=59.0,
            platform_fee_rate=0.08,
            expected_refund_rate=0.02,
        )
        # platform_fee = 59 * 0.08 = 4.72
        # refund_loss = 59 * 0.02 = 1.18
        # gross_profit = 59 - 25 - 3 - 4.72 - 1.18 = 25.1
        self.assertAlmostEqual(econ["platform_fee"], 4.72, places=2)
        self.assertAlmostEqual(econ["refund_loss"], 1.18, places=2)
        self.assertAlmostEqual(econ["gross_profit"], 25.10, places=2)
        self.assertGreater(econ["gross_profit"], 0)

    def test_gross_margin_calculation(self):
        econ = self.manager.compute_economics(
            supplier_price=25.0,
            supplier_shipping=3.0,
            target_sale_price=59.0,
        )
        expected_margin = econ["gross_profit"] / 59.0
        self.assertAlmostEqual(econ["gross_margin"], expected_margin, places=4)

    def test_zero_target_price_no_division_error(self):
        econ = self.manager.compute_economics(
            supplier_price=10.0,
            supplier_shipping=2.0,
            target_sale_price=0.0,
        )
        self.assertEqual(econ["gross_margin"], 0.0)

    def test_negative_profit_detected(self):
        econ = self.manager.compute_economics(
            supplier_price=50.0,
            supplier_shipping=10.0,
            target_sale_price=30.0,
        )
        self.assertLess(econ["gross_profit"], 0)

    def test_economics_appear_in_candidate(self):
        result = self.manager.create_candidate({
            "title": "Test item",
            "source_platform": "pdd",
            "supplier_price": 20.0,
            "supplier_shipping": 5.0,
            "target_sale_price": 50.0,
        })
        econ = result["economics"]
        self.assertEqual(econ["supplier_price"], 20.0)
        self.assertEqual(econ["supplier_shipping"], 5.0)
        self.assertEqual(econ["target_sale_price"], 50.0)
        self.assertIn("gross_profit", econ)
        self.assertIn("gross_margin", econ)


class TestResaleBlockedCategories(unittest.TestCase):
    """Test blocked category detection."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from xianyu_agent.resale import ResaleManager
        self.manager = ResaleManager(Path(self.tmpdir))

    def test_blocked_category_food(self):
        result = self.manager.create_candidate({
            "title": "零食大礼包",
            "source_platform": "pdd",
            "supplier_price": 10.0,
            "supplier_shipping": 5.0,
            "target_sale_price": 30.0,
            "category": "食品",
        })
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["risk_level"], "blocked")
        self.assertIsNotNone(result["blocked_reason"])

    def test_blocked_category_cosmetics(self):
        result = self.manager.create_candidate({
            "title": "面膜",
            "source_platform": "1688",
            "supplier_price": 5.0,
            "supplier_shipping": 2.0,
            "target_sale_price": 25.0,
            "category": "化妆品",
        })
        self.assertEqual(result["status"], "blocked")

    def test_blocked_category_english(self):
        result = self.manager.create_candidate({
            "title": "Baby stroller",
            "source_platform": "1688",
            "supplier_price": 100.0,
            "supplier_shipping": 30.0,
            "target_sale_price": 200.0,
            "category": "baby_safety",
        })
        self.assertEqual(result["status"], "blocked")

    def test_blocked_category_counterfeit(self):
        result = self.manager.create_candidate({
            "title": "仿品包包",
            "source_platform": "pdd",
            "supplier_price": 50.0,
            "supplier_shipping": 10.0,
            "target_sale_price": 200.0,
            "category": "仿品",
        })
        self.assertEqual(result["status"], "blocked")

    def test_safe_category_not_blocked(self):
        result = self.manager.create_candidate({
            "title": "手机壳",
            "source_platform": "pdd",
            "supplier_price": 3.0,
            "supplier_shipping": 1.0,
            "target_sale_price": 15.0,
            "category": "数码配件",
        })
        self.assertEqual(result["status"], "evaluating")
        self.assertNotEqual(result["risk_level"], "blocked")


class TestResalePlanAndDraft(unittest.TestCase):
    """Test plan and purchase draft creation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from xianyu_agent.resale import ResaleManager
        self.manager = ResaleManager(Path(self.tmpdir))
        self.candidate = self.manager.create_candidate({
            "title": "蓝牙耳机",
            "source_platform": "pdd",
            "source_url": "https://pdd.example.com/item/123",
            "supplier_price": 25.0,
            "supplier_shipping": 3.0,
            "market_scope": "domestic",
            "sales_platform": "xianyu",
            "target_sale_price": 59.0,
            "category": "数码配件",
            "specifications": {"color": "black", "type": "TWS"},
        })

    def test_create_plan_from_candidate(self):
        plan = self.manager.create_plan(self.candidate["candidate_id"])
        self.assertIsNotNone(plan)
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(plan["candidate_id"], self.candidate["candidate_id"])
        self.assertIn("pricing_strategy", plan)
        self.assertIn("risk_assessment", plan)

    def test_create_plan_blocked_candidate_fails(self):
        blocked = self.manager.create_candidate({
            "title": "药品",
            "source_platform": "pdd",
            "supplier_price": 10.0,
            "supplier_shipping": 5.0,
            "target_sale_price": 30.0,
            "category": "药品",
        })
        plan = self.manager.create_plan(blocked["candidate_id"])
        self.assertIsNone(plan)

    def test_create_purchase_draft(self):
        plan = self.manager.create_plan(self.candidate["candidate_id"])
        draft = self.manager.create_purchase_draft(
            plan_id=plan["plan_id"],
            quantity=2,
            buyer_address_summary="北京市朝阳区***1234",
        )
        self.assertIsNotNone(draft)
        self.assertEqual(draft["quantity"], 2)
        self.assertEqual(draft["status"], "pending_approval")
        self.assertIn("total_cost", draft)
        self.assertIn("profit_review", draft)
        self.assertIn("risk_warnings", draft)
        self.assertEqual(draft["buyer_address"], "北京市朝阳区***1234")

    def test_purchase_draft_total_cost(self):
        plan = self.manager.create_plan(self.candidate["candidate_id"])
        draft = self.manager.create_purchase_draft(plan_id=plan["plan_id"], quantity=3)
        # total = 25 * 3 + 3 = 78
        self.assertAlmostEqual(draft["total_cost"], 78.0, places=2)

    def test_purchase_draft_masks_address(self):
        plan = self.manager.create_plan(self.candidate["candidate_id"])
        draft = self.manager.create_purchase_draft(
            plan_id=plan["plan_id"],
            buyer_address_full="北京市朝阳区某某街道123号",
        )
        # Address should be masked
        self.assertNotEqual(draft["buyer_address"], "北京市朝阳区某某街道123号")

    def test_purchase_draft_includes_mvp_warning(self):
        plan = self.manager.create_plan(self.candidate["candidate_id"])
        draft = self.manager.create_purchase_draft(plan_id=plan["plan_id"])
        warnings = draft["risk_warnings"]
        self.assertTrue(any("MVP" in w for w in warnings))


class TestResaleWorkOrderIntegration(unittest.TestCase):
    """Test that purchase drafts go through WorkOrder approval flow."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from xianyu_agent.resale import ResaleManager
        self.manager = ResaleManager(Path(self.tmpdir))
        self.candidate = self.manager.create_candidate({
            "title": "测试商品",
            "source_platform": "pdd",
            "source_url": "https://pdd.example.com/item/999",
            "supplier_price": 10.0,
            "supplier_shipping": 2.0,
            "target_sale_price": 30.0,
            "category": "数码配件",
        })

    def test_prepare_purchase_draft_requires_approval(self):
        """prepare_purchase_draft should be in RISKY_ACTIONS."""
        from xianyu_agent.work_orders import RISKY_ACTIONS
        self.assertIn("prepare_purchase_draft", RISKY_ACTIONS)

    def test_place_supplier_order_in_risky_actions(self):
        from xianyu_agent.work_orders import RISKY_ACTIONS
        self.assertIn("place_supplier_order", RISKY_ACTIONS)

    def test_approve_resale_plan_in_risky_actions(self):
        from xianyu_agent.work_orders import RISKY_ACTIONS
        self.assertIn("approve_resale_plan", RISKY_ACTIONS)


class TestPlaceSupplierOrderBlocked(unittest.TestCase):
    """Test that place_supplier_order cannot execute in MVP."""

    def test_handler_returns_blocked(self):
        from xianyu_agent.action_executor import action_executor
        result = action_executor.execute(
            {"action": "place_supplier_order", "payload": {}},
            {},
        )
        self.assertFalse(result["ok"])
        self.assertIn("MVP", result["error"])


class TestResaleRoutesExist(unittest.TestCase):
    """Test that all 5 resale API routes exist in the FastAPI app."""

    def test_routes_exist(self):
        from xianyu_agent.server import app
        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        self.assertIn("/resale/candidates", route_paths)
        self.assertIn("/resale/candidates/{candidate_id}", route_paths)
        self.assertIn("/resale/candidates/{candidate_id}/plan", route_paths)
        self.assertIn("/resale/plans/{plan_id}/purchase-draft", route_paths)

    def test_candidate_post_route(self):
        from xianyu_agent.server import app
        methods = set()
        for r in app.routes:
            if hasattr(r, "path") and r.path == "/resale/candidates":
                if hasattr(r, "methods"):
                    methods.update(r.methods)
        self.assertIn("POST", methods)
        self.assertIn("GET", methods)


class TestResaleListFilters(unittest.TestCase):
    """Test listing candidates with filters."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from xianyu_agent.resale import ResaleManager
        self.manager = ResaleManager(Path(self.tmpdir))
        self.manager.create_candidate({
            "title": "国内商品A", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
            "market_scope": "domestic", "sales_platform": "xianyu",
        })
        self.manager.create_candidate({
            "title": "跨境商品B", "source_platform": "1688",
            "supplier_price": 5.0, "target_sale_price": 20.0,
            "market_scope": "cross_border", "sales_platform": "shopee",
        })

    def test_list_all(self):
        results = self.manager.list_candidates()
        self.assertEqual(len(results), 2)

    def test_filter_by_market_scope(self):
        results = self.manager.list_candidates(market_scope="domestic")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "国内商品A")

    def test_filter_by_source_platform(self):
        results = self.manager.list_candidates(source_platform="1688")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "跨境商品B")

    def test_filter_by_status(self):
        results = self.manager.list_candidates(status="evaluating")
        self.assertEqual(len(results), 2)


class TestResalePersistence(unittest.TestCase):
    """Test JSON persistence across manager instances."""

    def test_candidates_persist(self):
        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        from xianyu_agent.resale import ResaleManager
        manager1 = ResaleManager(data_dir)
        created = manager1.create_candidate({
            "title": "持久化测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        # New instance should load from disk
        manager2 = ResaleManager(data_dir)
        loaded = manager2.get_candidate(created["candidate_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["title"], "持久化测试")


if __name__ == "__main__":
    unittest.main()
