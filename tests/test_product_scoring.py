"""Tests for the ProductScorer strategy scoring model.

Verifies scoring dimensions, recommendation thresholds, and integration
with ResaleManager candidate creation.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xianyu_agent.resale import ProductScorer, ResaleManager, SCORING_DIMENSIONS


class ProductScorerTests(unittest.TestCase):
    def setUp(self):
        self.scorer = ProductScorer()

    def _make_candidate(self, **overrides):
        base = {
            "title": "测试产品",
            "category": "",
            "target_sale_price": 500,
            "supplier_price": 200,
            "supplier_shipping": 20,
            "supplier_verified": True,
            "economics": {"gross_margin": 0.4},
            "risk_level": "low",
            "risk_flags": [],
        }
        base.update(overrides)
        return base

    def test_all_dimensions_present_in_output(self):
        result = self.scorer.score(self._make_candidate())
        for dim in SCORING_DIMENSIONS:
            self.assertIn(dim, result["scores"], f"Missing dimension: {dim}")

    def test_scores_in_valid_range(self):
        result = self.scorer.score(self._make_candidate())
        for dim, score in result["scores"].items():
            self.assertGreaterEqual(score, 1, f"{dim} score {score} < 1")
            self.assertLessEqual(score, 5, f"{dim} score {score} > 5")

    def test_total_score_is_sum_of_dimensions(self):
        result = self.scorer.score(self._make_candidate())
        expected_total = sum(result["scores"].values())
        self.assertEqual(result["total_score"], expected_total)

    def test_high_ticket_product_scores_high(self):
        candidate = self._make_candidate(target_sale_price=1500)
        result = self.scorer.score(candidate)
        self.assertEqual(result["scores"]["high_ticket"], 5)

    def test_low_ticket_product_scores_low(self):
        candidate = self._make_candidate(target_sale_price=15)
        result = self.scorer.score(candidate)
        self.assertEqual(result["scores"]["high_ticket"], 1)

    def test_cold_category_commercial_kitchen(self):
        candidate = self._make_candidate(category="商用厨具", title="不锈钢操作台")
        result = self.scorer.score(candidate)
        self.assertEqual(result["scores"]["cold_category"], 5)

    def test_crowded_electronics_scores_low(self):
        candidate = self._make_candidate(category="手机", title="二手iPhone")
        result = self.scorer.score(candidate)
        self.assertEqual(result["scores"]["cold_category"], 1)

    def test_batch_order_product(self):
        candidate = self._make_candidate(title="养殖防护围栏")
        result = self.scorer.score(candidate)
        self.assertEqual(result["scores"]["batch_order"], 5)

    def test_blocked_compliance_risk(self):
        candidate = self._make_candidate(risk_level="blocked")
        result = self.scorer.score(candidate)
        self.assertEqual(result["scores"]["compliance_risk"], 1)

    def test_low_compliance_risk(self):
        candidate = self._make_candidate(risk_level="low", risk_flags=[])
        result = self.scorer.score(candidate)
        self.assertEqual(result["scores"]["compliance_risk"], 5)

    def test_recommendation_test(self):
        # All 5s = 35, should be "test"
        candidate = self._make_candidate(
            category="商用厨具",
            title="不锈钢养殖围栏设备",
            target_sale_price=2000,
            supplier_verified=True,
            economics={"gross_margin": 0.6},
            risk_level="low",
            risk_flags=[],
        )
        result = self.scorer.score(candidate)
        self.assertEqual(result["recommendation"], "test")

    def test_recommendation_reject(self):
        # Very low scores
        candidate = self._make_candidate(
            category="手机壳",
            title="手机壳",
            target_sale_price=5,
            supplier_verified=False,
            economics={"gross_margin": 0.05},
            risk_level="blocked",
            risk_flags=["brand_infringement"],
        )
        result = self.scorer.score(candidate)
        self.assertIn(result["recommendation"], ("reject", "weak"))

    def test_scored_at_timestamp(self):
        result = self.scorer.score(self._make_candidate())
        self.assertIn("scored_at", result)
        self.assertRegex(result["scored_at"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class ResaleManagerScoringTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = ResaleManager(Path(self.tmpdir))

    def test_candidate_created_with_scoring(self):
        candidate = self.manager.create_candidate({
            "title": "商用不锈钢操作台",
            "category": "商用厨具",
            "supplier_price": 500,
            "supplier_shipping": 30,
            "target_sale_price": 1200,
            "source_platform": "pdd",
        })
        self.assertIn("scoring", candidate)
        self.assertIn("scores", candidate["scoring"])
        self.assertIn("total_score", candidate["scoring"])
        self.assertIn("recommendation", candidate["scoring"])

    def test_score_candidate_method(self):
        candidate = self.manager.create_candidate({
            "title": "测试产品",
            "supplier_price": 100,
            "supplier_shipping": 10,
            "target_sale_price": 300,
        })
        cid = candidate["candidate_id"]
        scoring = self.manager.score_candidate(cid)
        self.assertIsNotNone(scoring)
        self.assertIn("scores", scoring)
        self.assertEqual(scoring["total_score"], sum(scoring["scores"].values()))

    def test_list_by_recommendation(self):
        self.manager.create_candidate({
            "title": "商用不锈钢操作台",
            "category": "商用厨具",
            "supplier_price": 500,
            "supplier_shipping": 30,
            "target_sale_price": 2000,
        })
        self.manager.create_candidate({
            "title": "手机壳",
            "category": "手机配件",
            "supplier_price": 2,
            "supplier_shipping": 1,
            "target_sale_price": 5,
        })
        test_list = self.manager.list_by_recommendation("test")
        # At least the kitchen equipment should be "test"
        self.assertTrue(len(test_list) >= 1)

    def test_dashboard_scoring_summary(self):
        self.manager.create_candidate({
            "title": "商用不锈钢操作台",
            "category": "商用厨具",
            "supplier_price": 500,
            "supplier_shipping": 30,
            "target_sale_price": 1200,
        })
        dashboard = self.manager.get_dashboard_summary()
        self.assertIn("scoring_summary", dashboard)
        self.assertIn("scored_count", dashboard["scoring_summary"])
        self.assertEqual(dashboard["scoring_summary"]["scored_count"], 1)


if __name__ == "__main__":
    unittest.main()
