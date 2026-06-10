import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class FulfillOrderHandlerTests(unittest.TestCase):
    """Tests for the real fulfill_order handler in ActionExecutor."""

    def test_fulfill_order_handler_calls_workflow(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        calls = []

        class FakeWorkflow:
            def fulfill_order(self, payload):
                calls.append(payload)
                return {"ok": True, "message": "发货成功", "order_id": payload.get("order_id")}

        approval = {
            "action": "fulfill_order",
            "payload": {
                "order_id": "order_123",
                "delivery_type": "digital",
                "delivery_payload": "https://download.link/abc",
                "cookie_id": "default",
            },
        }
        context = {"workflow": FakeWorkflow()}

        import asyncio
        result = asyncio.run(executor.execute_async(approval, context))

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "fulfill_order")
        self.assertEqual(result["order_id"], "order_123")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["order_id"], "order_123")

    def test_fulfill_order_handler_returns_error_without_workflow(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        approval = {
            "action": "fulfill_order",
            "payload": {"order_id": "order_123", "delivery_type": "digital", "delivery_payload": "abc"},
        }

        import asyncio
        result = asyncio.run(executor.execute_async(approval, {}))

        self.assertFalse(result["ok"])
        self.assertIn("workflow", result["error"].lower())

    def test_fulfill_order_handler_returns_error_missing_order_id(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        class FakeWorkflow:
            def fulfill_order(self, payload):
                return {"ok": True}

        approval = {"action": "fulfill_order", "payload": {"delivery_type": "digital"}}
        context = {"workflow": FakeWorkflow()}

        import asyncio
        result = asyncio.run(executor.execute_async(approval, context))

        self.assertFalse(result["ok"])
        self.assertIn("order_id", result["error"].lower())

    def test_fulfill_order_handler_surfaces_workflow_failure(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()

        class FakeWorkflow:
            def fulfill_order(self, payload):
                return {"ok": False, "message": "Platform guard blocked"}

        approval = {
            "action": "fulfill_order",
            "payload": {"order_id": "order_456", "delivery_type": "digital", "delivery_payload": "abc"},
        }
        context = {"workflow": FakeWorkflow()}

        import asyncio
        result = asyncio.run(executor.execute_async(approval, context))

        self.assertFalse(result["ok"])
        self.assertIn("Platform guard", result["fulfillment_result"]["message"])


class FulfillOrderWorkOrderTests(unittest.TestCase):
    """Tests for the fulfill order work order creation flow."""

    def test_create_fulfill_task_creates_pending_approval(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            wom = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = wom.create_work_order(
                kind="order_fulfillment",
                title="发货 order_123",
                owner="order",
                context={"order_id": "order_123", "delivery_type": "digital"},
            )
            plan = wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {
                        "agent": "order",
                        "action": "fulfill_order",
                        "description": "为 order_123 发货",
                        "params": {
                            "order_id": "order_123",
                            "delivery_type": "digital",
                            "delivery_payload": "https://download.link/abc",
                            "cookie_id": "default",
                        },
                        "draft_snapshot": {
                            "order_id": "order_123",
                            "delivery_type": "digital",
                            "delivery_payload_summary": "https://do***abc",
                            "cookie_id": "default",
                        },
                    },
                ],
            )

            wo = wom.get_work_order(order["work_order_id"])
            self.assertEqual(len(wo["approvals"]), 1)
            approval = wo["approvals"][0]
            self.assertEqual(approval["status"], "pending")
            self.assertEqual(approval["action"], "fulfill_order")
            # requires_approval is on the plan action, not the approval
            self.assertTrue(wo["action_plan"]["actions"][0]["requires_approval"])
            self.assertEqual(approval["draft_snapshot"]["order_id"], "order_123")

    def test_fulfill_approval_can_be_enqueued(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order(kind="order_fulfillment", title="发货", owner="order")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "order", "action": "fulfill_order", "description": "发货",
                     "params": {"order_id": "o1", "delivery_type": "digital", "delivery_payload": "abc"}},
                ],
            )
            wo = wom.get_work_order(order["work_order_id"])
            approval_id = wo["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认发货")

            job = eq.create_job(approval_id, wom)
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["action"], "fulfill_order")

    def test_fulfill_run_next_completes(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            executor = ActionExecutor()

            class FakeWorkflow:
                def fulfill_order(self, payload):
                    return {"ok": True, "message": "发货成功", "order_id": payload.get("order_id")}

            # Patch the handler's workflow reference via context
            # We need to register a custom handler that uses our fake workflow
            import asyncio
            original_handler = executor._handlers["fulfill_order"]

            def fake_fulfill_handler(approval, ctx):
                ctx["workflow"] = FakeWorkflow()
                return original_handler(approval, ctx)

            executor._handlers["fulfill_order"] = fake_fulfill_handler

            order = wom.create_work_order(kind="order_fulfillment", title="发货", owner="order")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "order", "action": "fulfill_order", "description": "发货",
                     "params": {"order_id": "o1", "delivery_type": "digital", "delivery_payload": "abc"}},
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

            job = eq.get_job(results[0]["job_id"])
            self.assertEqual(job["status"], "completed")

    def test_fulfill_failure_marks_job_failed(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            executor = ActionExecutor()

            class FakeWorkflow:
                def fulfill_order(self, payload):
                    return {"ok": False, "message": "Platform guard blocked"}

            original_handler = executor._handlers["fulfill_order"]

            def fake_fulfill_handler(approval, ctx):
                ctx["workflow"] = FakeWorkflow()
                return original_handler(approval, ctx)

            executor._handlers["fulfill_order"] = fake_fulfill_handler

            order = wom.create_work_order(kind="order_fulfillment", title="发货", owner="order")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "order", "action": "fulfill_order", "description": "发货",
                     "params": {"order_id": "o2", "delivery_type": "digital", "delivery_payload": "abc"}},
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
            self.assertIn("Platform guard", job["error"])

    def test_fulfill_snapshot_masks_payload(self):
        """Snapshot should store delivery_payload_summary, not full delivery_payload."""
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            wom = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = wom.create_work_order(kind="order_fulfillment", title="发货", owner="order")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {
                        "agent": "order",
                        "action": "fulfill_order",
                        "description": "发货",
                        "params": {"order_id": "o1", "delivery_type": "digital", "delivery_payload": "https://secret.download.link/file.zip"},
                        "draft_snapshot": {
                            "order_id": "o1",
                            "delivery_type": "digital",
                            "delivery_payload_summary": "https://se***.zip",
                            "cookie_id": "default",
                        },
                    },
                ],
            )
            wo = wom.get_work_order(order["work_order_id"])
            snapshot = wo["approvals"][0]["draft_snapshot"]
            self.assertNotIn("delivery_payload", snapshot)
            self.assertIn("delivery_payload_summary", snapshot)
            self.assertEqual(snapshot["delivery_payload_summary"], "https://se***.zip")


class FulfillOrderRouteTests(unittest.TestCase):
    def test_fulfill_route_registered(self):
        from xianyu_agent.server import app

        route_paths = [route.path for route in app.routes if hasattr(route, "path")]
        self.assertIn("/workflows/orders/{order_id}/fulfill", route_paths)

    def test_fulfill_route_creates_work_order(self):
        from xianyu_agent.server import app
        import inspect

        handler = None
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/workflows/orders/{order_id}/fulfill":
                if hasattr(route, "endpoint"):
                    handler = route.endpoint
                    break

        self.assertIsNotNone(handler)
        source = inspect.getsource(handler)
        self.assertIn("order_fulfillment", source)
        self.assertIn("approval_required", source)


class FulfillOrderJSTests(unittest.TestCase):
    def test_js_has_fulfill_order_display(self):
        js_path = Path(__file__).resolve().parent.parent / "web" / "app.js"
        source = js_path.read_text(encoding="utf-8")
        self.assertIn("fulfill_order", source)
        self.assertIn("delivery_type", source)
        self.assertIn("order_id", source)


if __name__ == "__main__":
    unittest.main()
