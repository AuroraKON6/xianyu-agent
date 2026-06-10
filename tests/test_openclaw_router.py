import unittest

from xianyu_agent.agents import create_all_agents
from xianyu_agent.agent_registry import AgentRegistry
from xianyu_agent.openclaw_router import OpenClawRouter


class DiscussionAgent:
    def __init__(self, name):
        self.name = name

    def contribute_to_discussion(self, topic, transcript, context=None):
        return f"{self.name}:{topic}:{len(transcript)}"


class FakeWorkflow:
    def __init__(self):
        self.calls = []

    def handle_agent_message(self, text, publish=True):
        self.calls.append((text, publish))
        return {"type": "fallback", "result": {"text": text, "publish": publish}}


class OpenClawRouterTests(unittest.TestCase):
    def test_default_agents_exclude_hermes_orchestrator(self):
        agents = create_all_agents()

        self.assertNotIn("hermes", agents)
        self.assertIn("listing", agents)
        self.assertIn("customer_service", agents)

    def test_routes_listing_command_directly_through_registry(self):
        registry = AgentRegistry()

        def create_listing(params):
            return {
                "ok": True,
                "draft": {"title": params["text"], "price": 9.9},
                "publish_result": None,
            }

        registry.register_handler("listing", "create_listing", create_listing)
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message("上架：测试商品，库存1，售价9.9", publish=False)

        self.assertEqual(result["type"], "listing")
        self.assertTrue(result["ok"])
        self.assertEqual(result["entrypoint"], "openclaw")
        self.assertEqual(result["result"]["draft"]["title"], "测试商品，库存1，售价9.9")
        self.assertEqual(result["task"]["agent"], "listing")
        self.assertEqual(result["task"]["action"], "create_listing")

    def test_routes_collaborative_listing_to_collaboration_handler(self):
        registry = AgentRegistry()
        registry.register_handler(
            "listing",
            "create_collaborative_listing",
            lambda params: {"ok": True, "draft": {"title": params["text"]}, "collaboration": {}},
        )
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message("协作上架：蓝牙耳机，成本70，售价98", publish=False)

        self.assertEqual(result["type"], "listing")
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["action"], "create_collaborative_listing")

    def test_routes_real_chinese_collaborative_listing_command(self):
        registry = AgentRegistry()
        registry.register_handler(
            "listing",
            "create_collaborative_listing",
            lambda params: {"ok": True, "draft": {"title": params["text"]}, "collaboration": {}},
        )
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message("协作上架：蓝牙耳机，成本70，售价98", publish=False)

        self.assertEqual(result["type"], "listing")
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["action"], "create_collaborative_listing")
        self.assertIn("蓝牙耳机", result["result"]["draft"]["title"])

    def test_status_reports_openclaw_entrypoint_and_registered_agents(self):
        registry = AgentRegistry()
        registry.register_agent("listing", object())
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message("状态")

        self.assertEqual(result["type"], "openclaw_status")
        self.assertEqual(result["entrypoint"], "openclaw")
        self.assertEqual(result["result"]["registered_agents"], ["listing"])

    def test_routes_discussion_command_to_agent_roundtable(self):
        registry = AgentRegistry()
        registry.register_agent("listing", DiscussionAgent("listing"))
        registry.register_agent("analytics", DiscussionAgent("analytics"))
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message(
            "讨论：如何上架蓝牙耳机",
            context={"participants": ["listing", "analytics"], "rounds": 1},
        )

        self.assertEqual(result["type"], "agent_discussion")
        self.assertEqual(result["entrypoint"], "openclaw")
        self.assertEqual([turn["agent"] for turn in result["result"]["transcript"]], ["listing", "analytics"])

    def test_routes_real_chinese_discussion_command_to_agent_roundtable(self):
        registry = AgentRegistry()
        registry.register_agent("listing", DiscussionAgent("listing"))
        registry.register_agent("analytics", DiscussionAgent("analytics"))
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message(
            "讨论：如何上架蓝牙耳机",
            context={"participants": ["listing", "analytics"], "rounds": 1},
        )

        self.assertEqual(result["type"], "agent_discussion")
        self.assertEqual([turn["agent"] for turn in result["result"]["transcript"]], ["listing", "analytics"])

    def test_legacy_workflow_commands_fall_back_without_hermes(self):
        workflow = FakeWorkflow()
        router = OpenClawRouter(registry=AgentRegistry(), workflow=workflow)

        result = router.process_message("比价：AI资料包，源售价99，计划卖39", publish=False)

        self.assertEqual(result["type"], "fallback")
        self.assertEqual(workflow.calls, [("比价：AI资料包，源售价99，计划卖39", False)])

    def test_unknown_chat_returns_help_without_creating_listing(self):
        workflow = FakeWorkflow()
        router = OpenClawRouter(registry=AgentRegistry(), workflow=workflow)

        result = router.process_message("你能做什么", publish=True)

        self.assertEqual(result["type"], "openclaw_help")
        self.assertEqual(result["entrypoint"], "openclaw")
        self.assertEqual(workflow.calls, [])

    def test_routes_same_product_multi_link_command(self):
        registry = AgentRegistry()
        registry.register_handler(
            "discovery",
            "strategy_discovery",
            lambda params: {"ok": True, "mode": params.get("mode"), "keyword": params.get("keyword", "")},
        )
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message("多链接测试：不锈钢操作台")

        self.assertEqual(result["type"], "strategy_discovery")
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["mode"], "same_product_multi_link")
        self.assertEqual(result["result"]["keyword"], "不锈钢操作台")

    def test_routes_multi_link_without_keyword(self):
        registry = AgentRegistry()
        registry.register_handler(
            "discovery",
            "strategy_discovery",
            lambda params: {"ok": True, "mode": params.get("mode")},
        )
        router = OpenClawRouter(registry=registry, workflow=FakeWorkflow())

        result = router.process_message("竞品分析")

        self.assertEqual(result["type"], "strategy_discovery")
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["mode"], "same_product_multi_link")


if __name__ == "__main__":
    unittest.main()
