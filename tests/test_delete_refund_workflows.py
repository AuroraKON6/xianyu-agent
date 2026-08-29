import asyncio
import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class DestructiveFinancialActionHandlerTests(unittest.TestCase):
    def test_delete_item_handler_calls_adapter(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        calls = []

        class FakeAdapter:
            async def delete_item(self, item_id, cookie_id="default"):
                calls.append({"item_id": item_id, "cookie_id": cookie_id})
                return {"ok": True, "item_id": item_id, "message": "deleted"}

        class FakeWorkflow:
            adapter = FakeAdapter()

        approval = {
            "action": "delete_item",
            "payload": {"item_id": "item_123", "cookie_id": "seller_a", "reason": "duplicate listing"},
        }

        result = asyncio.run(executor.execute_async(approval, {"workflow": FakeWorkflow()}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "delete_item")
        self.assertEqual(result["item_id"], "item_123")
        self.assertEqual(calls, [{"item_id": "item_123", "cookie_id": "seller_a"}])

    def test_delete_item_handler_requires_item_id(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        approval = {"action": "delete_item", "payload": {"cookie_id": "default"}}

        result = asyncio.run(executor.execute_async(approval, {"workflow": object()}))

        self.assertFalse(result["ok"])
        self.assertIn("item_id", result["error"])

    def test_refund_order_handler_calls_workflow_refund_order(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        calls = []

        class FakeWorkflow:
            async def refund_order(self, payload):
                calls.append(dict(payload))
                return {"ok": True, "refund_id": "refund_1", "message": "refund submitted"}

        approval = {
            "action": "refund_order",
            "payload": {
                "order_id": "order_123",
                "amount_cny": 19.9,
                "reason": "buyer returned item",
                "cookie_id": "seller_a",
            },
        }

        result = asyncio.run(executor.execute_async(approval, {"workflow": FakeWorkflow()}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "refund_order")
        self.assertEqual(result["order_id"], "order_123")
        self.assertEqual(calls[0]["order_id"], "order_123")

    def test_sensitive_reply_handler_uses_send_message_adapter(self):
        from xianyu_agent.action_executor import ActionExecutor

        executor = ActionExecutor()
        calls = []

        class FakeAdapter:
            async def send_message(self, buyer_id, message, cookie_id="default"):
                calls.append({"buyer_id": buyer_id, "message": message, "cookie_id": cookie_id})
                return {"ok": True, "message": "sent"}

        class FakeWorkflow:
            adapter = FakeAdapter()

        approval = {
            "action": "sensitive_reply",
            "payload": {
                "dispute_id": "disp_123",
                "buyer_id": "buyer_123",
                "message": "We can refund after return inspection.",
                "cookie_id": "seller_a",
            },
        }

        result = asyncio.run(executor.execute_async(approval, {"workflow": FakeWorkflow()}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "sensitive_reply")
        self.assertEqual(result["dispute_id"], "disp_123")
        self.assertEqual(calls[0]["buyer_id"], "buyer_123")


class DeleteRefundWorkflowRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_db = server.db_manager
        self.tmp = TemporaryDirectory()
        server.work_order_manager = WorkOrderManager(Path(self.tmp.name) / "work_orders.json")
        server.db_manager = DBManager(str(Path(self.tmp.name) / "accounts.db"))
        server.db_manager.save_cookie("seller_a", "test-cookie", "test account")

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.db_manager.close()
        self.server.db_manager = self.original_db
        self.tmp.cleanup()

    def test_phase12_routes_are_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/workflows/items/{item_id}/delete", paths)
        self.assertIn("/workflows/refunds/{order_id}/refund", paths)
        self.assertIn("/workflows/disputes/{dispute_id}/reply", paths)

    def test_delete_item_endpoint_creates_pending_approval_without_execution(self):
        req = self.server.DeleteItemRequest(
            reason="duplicate listing",
            cookie_id="seller_a",
            context={"operator": "ops"},
        )

        result = asyncio.run(self.server.delete_item_endpoint("item_123", req))

        self.assertTrue(result["ok"])
        self.assertTrue(result["approval_required"])
        work_order = result["work_order"]
        self.assertEqual(work_order["kind"], "delete_item")
        self.assertEqual(work_order["risk_level"], "high")
        approval = work_order["approvals"][0]
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["action"], "delete_item")
        self.assertEqual(approval["payload"]["item_id"], "item_123")
        self.assertEqual(approval["draft_snapshot"]["item_id"], "item_123")
        self.assertNotIn("api_key", str(approval["draft_snapshot"]).lower())

    def test_refund_endpoint_creates_critical_pending_approval(self):
        req = self.server.RefundOrderRequest(
            amount_cny=19.9,
            reason="buyer returned item",
            buyer_id="buyer_123",
            cookie_id="seller_a",
            context={"support_ticket": "T-1"},
        )

        result = asyncio.run(self.server.refund_order_endpoint("order_123", req))

        self.assertTrue(result["ok"])
        self.assertTrue(result["approval_required"])
        work_order = result["work_order"]
        self.assertEqual(work_order["kind"], "refund_order")
        self.assertEqual(work_order["risk_level"], "critical")
        approval = work_order["approvals"][0]
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["action"], "refund_order")
        self.assertEqual(approval["payload"]["order_id"], "order_123")
        self.assertEqual(approval["draft_snapshot"]["amount_cny"], 19.9)
        self.assertIn("approval_note", approval["draft_snapshot"])

    def test_dispute_reply_endpoint_stores_summary_not_full_message_in_snapshot(self):
        full_message = "Please return the item first. " * 8
        req = self.server.DisputeReplyRequest(
            order_id="order_123",
            buyer_id="buyer_123",
            message=full_message,
            cookie_id="seller_a",
            context={"support_ticket": "T-2"},
        )

        result = asyncio.run(self.server.dispute_reply_endpoint("disp_123", req))

        self.assertTrue(result["ok"])
        self.assertTrue(result["approval_required"])
        work_order = result["work_order"]
        self.assertEqual(work_order["kind"], "dispute_reply")
        approval = work_order["approvals"][0]
        self.assertEqual(approval["action"], "sensitive_reply")
        self.assertEqual(approval["payload"]["message"], full_message)
        snapshot = approval["draft_snapshot"]
        self.assertEqual(snapshot["dispute_id"], "disp_123")
        self.assertIn("message_summary", snapshot)
        self.assertNotEqual(snapshot["message_summary"], full_message)
        self.assertNotIn("message", snapshot)

    def test_workflow_routes_only_create_approvals(self):
        route_paths = {
            "/workflows/items/{item_id}/delete",
            "/workflows/refunds/{order_id}/refund",
            "/workflows/disputes/{dispute_id}/reply",
        }
        for route in self.server.app.routes:
            if getattr(route, "path", "") not in route_paths:
                continue
            source = inspect.getsource(route.endpoint)
            self.assertIn("approval_required", source)
            self.assertNotIn("execution_queue", source)
            self.assertNotIn("action_executor", source)
            self.assertNotIn("run_next", source)
            self.assertNotIn("adapter.", source)


class DeleteRefundQueueIntegrationTests(unittest.TestCase):
    def test_delete_refund_and_sensitive_reply_approvals_can_be_queued(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            actions = [
                ("delete_item", {"item_id": "item_1"}),
                ("refund_order", {"order_id": "order_1", "amount_cny": 12.3}),
                ("sensitive_reply", {"dispute_id": "disp_1", "buyer_id": "buyer_1", "message": "reply"}),
            ]
            for action, params in actions:
                order = wom.create_work_order(action, action, "operations", risk_level="critical")
                wom.create_action_plan(
                    order["work_order_id"],
                    proposed_actions=[{"agent": "operations", "action": action, "description": action, "params": params}],
                )
                approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
                wom.decide_approval(approval_id, decision="approved", note="approved")
                job = eq.create_job(approval_id, wom)
                self.assertEqual(job["status"], "queued")
                self.assertEqual(job["action"], action)


class DeleteRefundConsoleContractTests(unittest.TestCase):
    def test_console_renders_phase12_approval_snapshots(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn("delete_item", app_js)
        self.assertIn("refund_order", app_js)
        self.assertIn("sensitive_reply", app_js)
        self.assertIn("dispute_id", app_js)
        self.assertIn("amount_cny", app_js)


if __name__ == "__main__":
    unittest.main()
