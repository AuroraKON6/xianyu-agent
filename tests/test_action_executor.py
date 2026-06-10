import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class ActionExecutorTests(unittest.TestCase):
    def test_register_and_execute_handler(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        executor.register_handler("echo", lambda approval, ctx: {"ok": True, "echo": approval.get("action")})

        approval = {"action": "echo", "payload": {"x": 1}}
        result = executor.execute(approval, context={})

        self.assertTrue(result["ok"])
        self.assertEqual(result["echo"], "echo")

    def test_unknown_action_returns_failed(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        approval = {"action": "nonexistent_action", "payload": {}}
        result = executor.execute(approval, context={})

        self.assertFalse(result["ok"])
        self.assertIn("not registered", result["error"])
        self.assertEqual(result["action"], "nonexistent_action")

    def test_publish_listing_handler_reads_draft_and_calls_adapter(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        # Fake context with draft data and a fake adapter
        draft_data = {
            "title": "蓝牙耳机",
            "price": 98.0,
            "stock": 3,
            "description": "成色很新",
            "delivery_type": "physical",
            "category": "数码配件",
            "tags": [],
            "selected_images": [],
            "condition": "new",
            "location": "",
            "delivery_payload": "",
            "source": "test",
            "raw_text": "蓝牙耳机 资料",
        }

        class FakeAdapter:
            async def publish_listing(self, draft, cookie_id="default"):
                class R:
                    ok = True
                    external_id = "item_fake_123"
                    adapter = "fake"
                    message = "发布成功"
                return R()

        class FakeStorage:
            def save_publish_result(self, result, draft):
                pass

        class FakeWorkflow:
            adapter = FakeAdapter()
            storage = FakeStorage()

        with TemporaryDirectory() as tmp:
            drafts_dir = Path(tmp) / "drafts"
            drafts_dir.mkdir()
            (drafts_dir / "draft_abc.json").write_text(json.dumps(draft_data, ensure_ascii=False), encoding="utf-8")

            context = {
                "workflow": FakeWorkflow(),
                "data_dir": Path(tmp),
                "loop": None,  # Will use asyncio.run
            }

            approval = {
                "action": "publish_listing",
                "payload": {"draft_id": "draft_abc"},
                "draft_snapshot": {},
            }

            # Since the handler is async, we need to run it
            import asyncio
            result = asyncio.run(executor.execute_async(approval, context))

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "publish_listing")
            self.assertEqual(result["draft_id"], "draft_abc")
            self.assertEqual(result["publish_result"]["external_id"], "item_fake_123")

    def test_phase12_actions_validate_required_ids(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        expectations = {
            "delete_item": "item_id",
            "refund_order": "order_id",
        }
        for action, missing_field in expectations.items():
            approval = {"action": action, "payload": {}}
            result = executor.execute(approval, context={})
            self.assertFalse(result["ok"], f"{action} should return ok=False")
            self.assertIn(missing_field, result["error"], f"{action} error should mention missing field")
            self.assertEqual(result["action"], action)

    def test_execute_approval_through_executor(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        executor.register_handler("publish_listing", lambda a, ctx: {"ok": True, "external_id": "ext_exec"})

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = manager.create_work_order("listing", "协作上架", "listing")
            manager.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            manager.decide_approval(approval_id, decision="approved", note="确认发布")

            result = manager.execute_approval(approval_id, action_executor=executor, executor_context={})

            self.assertTrue(result["ok"])
            self.assertEqual(result["execution_status"], "completed")
            self.assertEqual(result["execution_result"]["external_id"], "ext_exec")

    def test_execute_approval_with_executor_unknown_action(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()  # No custom handlers; mystery_action is not registered

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = manager.create_work_order("listing", "协作上架", "listing", risk_level="high")
            manager.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "mystery_action", "description": "未知动作"},
                ],
            )
            approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            manager.decide_approval(approval_id, decision="approved", note="试试看")

            result = manager.execute_approval(approval_id, action_executor=executor, executor_context={})

            # The executor ran successfully (lifecycle ok), but the handler returned ok=False
            self.assertTrue(result["ok"])  # execution lifecycle succeeded
            self.assertEqual(result["execution_status"], "completed")
            self.assertFalse(result["execution_result"]["ok"])  # handler reported failure
            self.assertIn("not registered", result["execution_result"]["error"])


class ActionExecutorRouteTests(unittest.TestCase):
    def test_server_uses_action_executor_not_inline_logic(self):
        """Verify the execute route no longer contains inline publish_listing logic."""
        from xianyu_agent.server import app
        import inspect

        # Find the execute route handler
        execute_handler = None
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/approvals/{approval_id}/execute":
                if hasattr(route, "endpoint"):
                    execute_handler = route.endpoint
                    break

        self.assertIsNotNone(execute_handler)
        source = inspect.getsource(execute_handler)
        # Should NOT contain inline browser adapter logic
        self.assertNotIn("BrowserAdapter", source)
        self.assertNotIn("adapter.publish_listing", source)
        # Should reference execution_queue (enqueue pattern, not direct execution)
        self.assertIn("execution_queue", source)


if __name__ == "__main__":
    unittest.main()
