"""Tests for discovery strategy modes.

Verifies all 5 selection methods from the product selection playbook
are supported: raw_material, high_ticket, batch_demand, regional,
same_product_multi_link.
"""
from __future__ import annotations

import unittest

from xianyu_agent.agents.discovery import DiscoveryAgent
from xianyu_agent.agents.base import BaseAgent


class FakeWorkflow:
    def run_discovery(self, keyword, max_pages, min_price, max_price):
        return []


class DiscoveryStrategyModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BaseAgent.set_workflow(FakeWorkflow())

    def test_all_five_modes_in_strategy_modes(self):
        modes = list(DiscoveryAgent.STRATEGY_MODES.keys())
        self.assertIn("raw_material", modes)
        self.assertIn("high_ticket", modes)
        self.assertIn("batch_demand", modes)
        self.assertIn("regional", modes)
        self.assertIn("same_product_multi_link", modes)
        self.assertEqual(len(modes), 5)

    def test_raw_material_keywords(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("raw_material", {})
        self.assertTrue(len(keywords) > 0)
        self.assertIn("不锈钢", keywords)

    def test_raw_material_with_specific_material(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("raw_material", {"material": "玻璃"})
        self.assertEqual(keywords, ["玻璃"])

    def test_high_ticket_keywords(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("high_ticket", {})
        self.assertTrue(len(keywords) > 0)
        self.assertTrue(any("厨具" in kw or "机械" in kw for kw in keywords))

    def test_high_ticket_with_category(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("high_ticket", {"category": "液压设备"})
        self.assertEqual(keywords, ["液压设备"])

    def test_batch_demand_keywords(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("batch_demand", {})
        self.assertTrue(len(keywords) > 0)
        self.assertTrue(any("围栏" in kw or "地垫" in kw for kw in keywords))

    def test_regional_with_city_and_keyword(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("regional", {"city": "成都", "keyword": "操作台"})
        self.assertEqual(keywords, ["成都 操作台"])

    def test_regional_without_city(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("regional", {})
        self.assertEqual(keywords, ["操作台"])

    def test_same_product_multi_link_with_keyword(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("same_product_multi_link", {"keyword": "商用冷柜"})
        self.assertEqual(keywords, ["商用冷柜"])

    def test_same_product_multi_link_without_keyword(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("same_product_multi_link", {})
        self.assertTrue(len(keywords) > 0)

    def test_unknown_mode_returns_empty(self):
        agent = DiscoveryAgent()
        keywords = agent._get_strategy_keywords("nonexistent_mode", {})
        self.assertEqual(keywords, [])


if __name__ == "__main__":
    unittest.main()
