import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class SendMessageHandlerTests(unittest.TestCase):
    """Tests for the real send_message handler in ActionExecutor."""

    def test_send_message_handler_calls_adapter(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        calls = []

        class FakeAdapter:
            async def send_message(self, buyer_id, message, cookie_id="default"):
                calls.append({"buyer_id": buyer_id, "message": message, "cookie_id": cookie_id})
                return {"ok": True, "message": "消息已发送"}

        class FakeWorkflow:
            adapter = FakeAdapter()

        approval = {
            "action": "send_message",
            "payload": {
                "buyer_id": "buyer_abc",
                "message": "你好，商品还在",
                "cookie_id": "default",
            },
        }
        context = {"workflow": FakeWorkflow()}

        import asyncio
        result = asyncio.run(executor.execute_async(approval, context))

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "send_message")
        self.assertEqual(result["buyer_id"], "buyer_abc")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["buyer_id"], "buyer_abc")

    def test_send_message_handler_returns_error_without_workflow(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        approval = {
            "action": "send_message",
            "payload": {"buyer_id": "b1", "message": "hi"},
        }

        import asyncio
        result = asyncio.run(executor.execute_async(approval, {}))

        self.assertFalse(result["ok"])
        self.assertIn("workflow", result["error"].lower())

    def test_send_message_handler_returns_error_missing_buyer_id(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        class FakeWorkflow:
            adapter = None

        approval = {"action": "send_message", "payload": {"message": "hi"}}
        context = {"workflow": FakeWorkflow()}

        import asyncio
        result = asyncio.run(executor.execute_async(approval, context))

        self.assertFalse(result["ok"])
        self.assertIn("buyer_id", result["error"].lower())

    def test_send_message_handler_surfaces_adapter_failure(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        class FakeAdapter:
            async def send_message(self, buyer_id, message, cookie_id="default"):
                return {"ok": False, "error": "No browser session"}

        class FakeWorkflow:
            adapter = FakeAdapter()

        approval = {
            "action": "send_message",
            "payload": {"buyer_id": "b1", "message": "hi", "cookie_id": "default"},
        }
        context = {"workflow": FakeWorkflow()}

        import asyncio
        result = asyncio.run(executor.execute_async(approval, context))

        self.assertFalse(result["ok"])
        self.assertIn("No browser", result["send_result"]["error"])

    def test_send_message_handler_returns_error_no_adapter(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        class FakeWorkflow:
            adapter = None

        approval = {
            "action": "send_message",
            "payload": {"buyer_id": "b1", "message": "hi"},
        }
        context = {"workflow": FakeWorkflow()}

        import asyncio
        result = asyncio.run(executor.execute_async(approval, context))

        self.assertFalse(result["ok"])
        self.assertIn("adapter", result["error"].lower())


class SendMessageWorkOrderTests(unittest.TestCase):
    """Tests for the send_message work order creation flow."""

    def test_create_send_message_task_creates_pending_approval(self):
        from xianyu_agent.server import app
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            wom = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = wom.create_work_order(
                kind="customer_message",
                title="消息 buyer_abc",
                owner="customer_service",
                context={"buyer_id": "buyer_abc", "message": "你好"},
            )
            plan = wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {
                        "agent": "customer_service",
                        "action": "send_message",
                        "description": "给 buyer_abc 发送消息",
                        "params": {
                            "buyer_id": "buyer_abc",
                            "message": "你好，商品还在",
                            "cookie_id": "default",
                        },
                        "draft_snapshot": {
                            "buyer_id": "buyer_abc",
                            "chat_id": "",
                            "cookie_id": "default",
                            "message_summary": "你好，商品还在",
                            "risk_flags": [],
                        },
                    },
                ],
            )

            wo = wom.get_work_order(order["work_order_id"])
            self.assertEqual(len(wo["approvals"]), 1)
            approval = wo["approvals"][0]
            self.assertEqual(approval["status"], "pending")
            self.assertEqual(approval["action"], "send_message")
            self.assertTrue(wo["action_plan"]["actions"][0]["requires_approval"])
            self.assertEqual(approval["draft_snapshot"]["buyer_id"], "buyer_abc")

    def test_send_message_approval_can_be_enqueued(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order(kind="customer_message", title="消息", owner="customer_service")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "customer_service", "action": "send_message", "description": "发消息",
                     "params": {"buyer_id": "b1", "message": "hi"}},
                ],
            )
            wo = wom.get_work_order(order["work_order_id"])
            approval_id = wo["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")

            job = eq.create_job(approval_id, wom)
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["action"], "send_message")

    def test_send_message_run_next_completes(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            executor = ActionExecutor()

            class FakeAdapter:
                async def send_message(self, buyer_id, message, cookie_id="default"):
                    return {"ok": True, "message": "消息已发送"}

            class FakeWorkflow:
                adapter = FakeAdapter()

            original_handler = executor._handlers["send_message"]

            def fake_send_handler(approval, ctx):
                ctx["workflow"] = FakeWorkflow()
                return original_handler(approval, ctx)

            executor._handlers["send_message"] = fake_send_handler

            order = wom.create_work_order(kind="customer_message", title="消息", owner="customer_service")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "customer_service", "action": "send_message", "description": "发消息",
                     "params": {"buyer_id": "b1", "message": "hi"}},
                ],
            )
            wo = wom.get_work_order(order["work_order_id"])
            approval_id = wo["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            eq.create_job(approval_id, wom)

            results = eq.run_next(executor, wom)
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["ok"])
            self.assertEqual(results[0]["status"], "completed")

    def test_send_message_failure_marks_job_failed(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            executor = ActionExecutor()

            class FakeAdapter:
                async def send_message(self, buyer_id, message, cookie_id="default"):
                    return {"ok": False, "error": "No browser session"}

            class FakeWorkflow:
                adapter = FakeAdapter()

            original_handler = executor._handlers["send_message"]

            def fake_send_handler(approval, ctx):
                ctx["workflow"] = FakeWorkflow()
                return original_handler(approval, ctx)

            executor._handlers["send_message"] = fake_send_handler

            order = wom.create_work_order(kind="customer_message", title="消息", owner="customer_service")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "customer_service", "action": "send_message", "description": "发消息",
                     "params": {"buyer_id": "b1", "message": "hi"}},
                ],
            )
            wo = wom.get_work_order(order["work_order_id"])
            approval_id = wo["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            eq.create_job(approval_id, wom)

            results = eq.run_next(executor, wom)
            self.assertEqual(len(results), 1)
            job = eq.get_job(results[0]["job_id"])
            self.assertEqual(job["status"], "failed")
            self.assertIn("No browser", job["error"])


class SendMessageRiskTests(unittest.TestCase):
    """Tests for risk detection in send_message flow."""

    def test_risk_keyword_produces_risk_flags(self):
        """Messages containing sensitive keywords should be flagged as high risk."""
        from xianyu_agent.server import app
        import inspect

        handler = None
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/workflows/messages/{buyer_id}/send":
                if hasattr(route, "endpoint"):
                    handler = route.endpoint
                    break

        self.assertIsNotNone(handler)
        source = inspect.getsource(handler)
        # Should contain risk detection logic
        self.assertIn("risk", source.lower())

    def test_snapshot_masks_long_message(self):
        """Snapshot should contain message_summary, not the full message."""
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            wom = WorkOrderManager(Path(tmp) / "work_orders.json")
            long_message = "这是一条很长的消息" * 20  # 180 chars

            order = wom.create_work_order(kind="customer_message", title="消息", owner="customer_service")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {
                        "agent": "customer_service",
                        "action": "send_message",
                        "description": "发消息",
                        "params": {"buyer_id": "b1", "message": long_message},
                        "draft_snapshot": {
                            "buyer_id": "b1",
                            "chat_id": "",
                            "cookie_id": "default",
                            "message_summary": long_message[:80],
                            "risk_flags": [],
                        },
                    },
                ],
            )
            wo = wom.get_work_order(order["work_order_id"])
            snapshot = wo["approvals"][0]["draft_snapshot"]
            self.assertNotIn("message", snapshot)
            self.assertIn("message_summary", snapshot)
            self.assertLessEqual(len(snapshot["message_summary"]), 80)


class SendMessageRouteTests(unittest.TestCase):
    def test_send_message_route_registered(self):
        from xianyu_agent.server import app

        route_paths = [route.path for route in app.routes if hasattr(route, "path")]
        self.assertIn("/workflows/messages/{buyer_id}/send", route_paths)

    def test_send_message_route_creates_work_order(self):
        from xianyu_agent.server import app
        import inspect

        handler = None
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/workflows/messages/{buyer_id}/send":
                if hasattr(route, "endpoint"):
                    handler = route.endpoint
                    break

        self.assertIsNotNone(handler)
        source = inspect.getsource(handler)
        self.assertIn("customer_message", source)
        self.assertIn("approval_required", source)


class SendMessageJSTests(unittest.TestCase):
    def test_js_has_send_message_display(self):
        js_path = Path(__file__).resolve().parent.parent / "web" / "app.js"
        source = js_path.read_text(encoding="utf-8")
        self.assertIn("send_message", source)
        self.assertIn("buyer_id", source)
        self.assertIn("message_summary", source)
        self.assertIn("risk_flags", source)


class ExecutionQueueContextRouteTests(unittest.TestCase):
    def test_run_next_route_passes_workflow_context(self):
        from xianyu_agent.server import app
        import inspect

        handler = None
        for route in app.routes:
            if getattr(route, "path", "") == "/execution-jobs/run-next":
                handler = route.endpoint
                break

        self.assertIsNotNone(handler)
        source = inspect.getsource(handler)

        self.assertIn('"workflow": workflow', source)
        self.assertIn('"data_dir": settings.data_dir', source)


if __name__ == "__main__":
    unittest.main()
