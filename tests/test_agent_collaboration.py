import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xianyu_agent.agent_registry import AgentRegistry
from xianyu_agent.agents.base import BaseAgent
from xianyu_agent.agents.listing import ListingAgent
from xianyu_agent.work_orders import WorkOrderManager


class EchoAgent(BaseAgent):
    name = "echo"


class ScriptedDiscussionAgent:
    def __init__(self, name, line):
        self.name = name
        self.line = line

    def contribute_to_discussion(self, topic, transcript, context=None):
        return f"{self.name}: {self.line}"


class FakeWorkflow:
    def __init__(self):
        self.created = []

    def create_listing(self, raw_text, source="agent", product_images=None, publish=True):
        self.created.append(
            {
                "raw_text": raw_text,
                "source": source,
                "product_images": product_images or [],
                "publish": publish,
            }
        )
        return {
            "draft": {"title": "协作商品", "raw_text": raw_text},
            "publish_result": None,
        }


class AgentCollaborationTests(unittest.TestCase):
    def tearDown(self):
        BaseAgent.set_registry(None)
        BaseAgent.set_work_order_manager(None)

    def test_registry_send_message_dispatches_task_and_records_thread(self):
        registry = AgentRegistry()
        registry.register_handler(
            "analytics",
            "calculate_profit",
            lambda params: {"ok": True, "profit": 12.5, "params": params},
        )

        task = registry.send_message(
            from_agent="listing",
            to_agent="analytics",
            action="calculate_profit",
            params={"cost": 10, "price": 25},
            reason="price check",
        )

        self.assertEqual(task.status, "completed")
        self.assertEqual(task.result["profit"], 12.5)
        messages = registry.get_message_history()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["from_agent"], "listing")
        self.assertEqual(messages[0]["to_agent"], "analytics")
        self.assertEqual(messages[0]["action"], "calculate_profit")
        self.assertEqual(messages[0]["task_id"], task.task_id)

    def test_registry_persists_peer_messages_between_instances(self):
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "agent_collaboration.jsonl"
            registry = AgentRegistry(history_path=history_path)
            registry.register_handler(
                "analytics",
                "calculate_profit",
                lambda params: {"ok": True, "profit": 12.5, "params": params},
            )

            task = registry.send_message(
                from_agent="listing",
                to_agent="analytics",
                action="calculate_profit",
                params={"cost": 10, "price": 25},
                reason="price check",
            )

            reloaded = AgentRegistry(history_path=history_path)
            messages = reloaded.get_message_history()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["task_id"], task.task_id)
            self.assertEqual(messages[0]["from_agent"], "listing")
            self.assertEqual(messages[0]["to_agent"], "analytics")
            self.assertEqual(messages[0]["result"]["profit"], 12.5)

    def test_base_agent_can_ask_peer_agent_through_registry(self):
        registry = AgentRegistry()
        registry.register_handler("github", "find_opportunities", lambda params: {"ok": True, "results": {"count": 2}})
        BaseAgent.set_registry(registry)

        agent = EchoAgent()
        result = agent.ask_agent("github", "find_opportunities", {"queries": ["ai agent"]}, reason="research")

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["results"]["count"], 2)
        self.assertEqual(registry.get_message_history()[0]["from_agent"], "echo")

    def test_listing_agent_can_build_collaborative_listing_context(self):
        registry = AgentRegistry()
        registry.register_agent("listing", ScriptedDiscussionAgent("listing", "标题要突出成色和低延迟"))
        registry.register_agent("discovery", ScriptedDiscussionAgent("discovery", "市场看重价格和配件完整度"))
        registry.register_agent("analytics", ScriptedDiscussionAgent("analytics", "利润率接近合理区间"))
        registry.register_handler(
            "discovery",
            "analyze_market",
            lambda params: {"ok": True, "analysis": {"avg_price": 88, "recommendations": ["强调成色"]}},
        )
        registry.register_handler(
            "analytics",
            "calculate_profit",
            lambda params: {"ok": True, "profit": 18.2, "profit_rate": 0.26, "advice": "售价合理"},
        )
        BaseAgent.set_registry(registry)
        BaseAgent.set_workflow(FakeWorkflow())

        listing = ListingAgent()
        result = listing.create_collaborative_listing(
            {
                "text": "上架：蓝牙耳机，成本70，售价98",
                "category": "数码配件",
                "cost": 70,
                "price": 98,
                "publish": False,
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["collaboration"]["discussion"]["participants"], ["listing", "discovery", "analytics"])
        self.assertIn("市场看重价格", result["collaboration"]["discussion"]["summary"])
        self.assertEqual(result["collaboration"]["market"]["analysis"]["avg_price"], 88)
        self.assertEqual(result["collaboration"]["pricing"]["profit"], 18.2)
        created_text = listing.workflow.created[0]["raw_text"]
        self.assertIn("协作上下文", created_text)
        self.assertIn("讨论摘要", created_text)
        self.assertIn("售价合理", created_text)
        self.assertEqual(
            [(m["from_agent"], m["to_agent"], m["action"]) for m in registry.get_message_history()],
            [
                ("listing", "discovery", "analyze_market"),
                ("listing", "analytics", "calculate_profit"),
            ],
        )

    def test_collaborative_listing_creates_work_order_plan_and_holds_publish_for_approval(self):
        with TemporaryDirectory() as tmp:
            registry = AgentRegistry()
            registry.register_agent("listing", ScriptedDiscussionAgent("listing", "标题突出成色和低延迟"))
            registry.register_agent("discovery", ScriptedDiscussionAgent("discovery", "市场看重价格和配件"))
            registry.register_agent("analytics", ScriptedDiscussionAgent("analytics", "利润率可以接受"))
            registry.register_handler(
                "discovery",
                "analyze_market",
                lambda params: {"ok": True, "analysis": {"avg_price": 88}},
            )
            registry.register_handler(
                "analytics",
                "calculate_profit",
                lambda params: {"ok": True, "profit": 18.2, "profit_rate": 0.26},
            )
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            BaseAgent.set_registry(registry)
            BaseAgent.set_workflow(FakeWorkflow())
            BaseAgent.set_work_order_manager(manager)

            listing = ListingAgent()
            result = listing.create_collaborative_listing(
                {
                    "text": "协作上架：蓝牙耳机，成本70，售价98",
                    "category": "数码配件",
                    "cost": 70,
                    "price": 98,
                    "publish": True,
                }
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["approval_required"])
            self.assertEqual(listing.workflow.created[0]["publish"], False)
            self.assertEqual(result["action_plan"]["status"], "needs_approval")
            self.assertEqual(result["action_plan"]["actions"][-1]["action"], "publish_listing")
            work_order = manager.get_work_order(result["work_order"]["work_order_id"])
            self.assertEqual(work_order["status"], "needs_approval")
            self.assertEqual(work_order["approvals"][0]["action"], "publish_listing")

    def test_collaboration_routes_are_registered(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}

        self.assertIn(("/agents/messages", ("GET",)), routes)
        self.assertIn(("/agents/collaboration", ("GET",)), routes)


if __name__ == "__main__":
    unittest.main()
