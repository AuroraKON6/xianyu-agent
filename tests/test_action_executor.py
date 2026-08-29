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
            called_cookie_id = ""

            async def publish_listing(self, draft, cookie_id="default"):
                self.called_cookie_id = cookie_id
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
                "payload": {"draft_id": "draft_abc", "cookie_id": "seller_b"},
                "draft_snapshot": {},
            }

            # Since the handler is async, we need to run it
            import asyncio
            result = asyncio.run(executor.execute_async(approval, context))

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "publish_listing")
            self.assertEqual(result["draft_id"], "draft_abc")
            self.assertEqual(result["cookie_id"], "seller_b")
            self.assertEqual(result["publish_result"]["external_id"], "item_fake_123")
            self.assertEqual(FakeWorkflow.adapter.called_cookie_id, "seller_b")

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

    def test_edit_listing_submits_one_approved_change_without_claiming_confirmation(self):
        from xianyu_agent.action_executor import ActionExecutor

        class FakeAdapter:
            def __init__(self):
                self.calls = []

            async def polish_listing(self, item_id, record, cookie_id="default"):
                self.calls.append((item_id, record, cookie_id))
                return {
                    "ok": True,
                    "submitted": True,
                    "confirmed": False,
                    "page_url": "https://www.goofish.com/item?id=item_1",
                }

        class FakeWorkflow:
            adapter = FakeAdapter()

        executor = ActionExecutor()
        approval = {
            "action": "edit_listing",
            "payload": {
                "item_id": "item_1",
                "cookie_id": "seller_a",
                "variable": "title",
                "after_value": "不锈钢商用工作台 1200x600",
            },
        }
        result = executor.execute(approval, {"workflow": FakeWorkflow()})

        self.assertTrue(result["ok"])
        self.assertTrue(result["submitted"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["operator_next_action"], "sync_listing_and_activate_experiment")
        self.assertEqual(
            FakeWorkflow.adapter.calls,
            [("item_1", {"title": "不锈钢商用工作台 1200x600"}, "seller_a")],
        )

    def test_edit_listing_manual_variable_returns_operator_handoff(self):
        from xianyu_agent.action_executor import ActionExecutor

        result = ActionExecutor().execute(
            {
                "action": "edit_listing",
                "payload": {
                    "item_id": "item_1",
                    "cookie_id": "seller_a",
                    "variable": "cover",
                    "after_value": "实拍尺寸图",
                },
            },
            context={},
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["manual_required"])
        self.assertEqual(result["operator_next_action"], "complete_manual_listing_edit")

    def test_edit_listing_refuses_a_cancelled_commercial_experiment(self):
        from xianyu_agent.action_executor import ActionExecutor

        class FakeStore:
            def get_trial(self, trial_id):
                return {
                    "trial_id": trial_id,
                    "lifecycle": "active",
                    "experiments": [{"experiment_id": "te_1", "status": "cancelled"}],
                }

        result = ActionExecutor().execute(
            {
                "action": "edit_listing",
                "payload": {
                    "commercial_trial_id": "ct_1",
                    "experiment_id": "te_1",
                    "item_id": "item_1",
                    "cookie_id": "seller_a",
                    "variable": "title",
                    "after_value": "新标题",
                },
            },
            context={"commercial_trial_store": FakeStore()},
        )

        self.assertFalse(result["ok"])
        self.assertIn("no longer executable", result["error"])
        self.assertEqual(result["operator_next_action"], "review_cancelled_or_inactive_experiment")

    def test_manual_completion_replaces_a_failed_handler_result_with_audited_success(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = manager.create_work_order("listing_experiment", "首图实验", "listing", risk_level="high")
            manager.create_action_plan(
                order["work_order_id"],
                proposed_actions=[{
                    "agent": "listing",
                    "action": "edit_listing",
                    "description": "人工修改首图",
                    "params": {"item_id": "item_1", "cookie_id": "seller_a", "variable": "cover"},
                }],
            )
            approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            manager.decide_approval(approval_id, "approved", "同意首图实验")
            approval = manager.get_work_order(order["work_order_id"])["approvals"][0]
            approval["execution_status"] = "completed"
            approval["execution_result"] = {"ok": False, "manual_required": True}
            manager._work_orders[order["work_order_id"]]["approvals"][0] = approval

            completed = manager.complete_approval_manually(
                approval_id,
                {"ok": True, "evidence_ref": "screenshots/item_1-cover.png"},
            )

            self.assertTrue(completed["execution_result"]["ok"])
            self.assertTrue(completed["execution_result"]["manual_confirmation"])
            saved = manager.get_work_order(order["work_order_id"])
            self.assertEqual(saved["events"][-1]["type"], "execution_manually_confirmed")

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
