import asyncio
import inspect
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


class FakeWorkflow:
    def __init__(self, reply="The item is available. I can ship tonight."):
        self.reply = reply
        self.calls = []

    def generate_auto_reply(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply

    def handle_feishu_text(self, text):
        return {"type": "legacy_text", "text": text}


class AutomaticMessageIntakeRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_workflow = server.workflow
        self.original_ledger = server.message_intake_ledger
        self.tmp = TemporaryDirectory()
        server.work_order_manager = WorkOrderManager(Path(self.tmp.name) / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(Path(self.tmp.name) / "message_intake.json")
        server.workflow = FakeWorkflow()

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.workflow = self.original_workflow
        self.server.message_intake_ledger = self.original_ledger
        self.tmp.cleanup()

    def test_inbound_message_route_is_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/workflows/messages/inbound", paths)

    def test_inbound_message_creates_pending_reply_approval(self):
        client = TestClient(self.server.app)

        response = client.post(
            "/workflows/messages/inbound",
            json={
                "source": "xianyu_im",
                "buyer_id": "buyer_123",
                "buyer_name": "Ada",
                "message": "Is this still available?",
                "chat_id": "chat_123",
                "item_id": "item_123",
                "product_title": "Keyboard",
                "cookie_id": "seller_a",
                "conversation": [{"role": "buyer", "content": "Is this still available?"}],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["approval_required"])
        self.assertEqual(data["type"], "customer_message_intake")
        self.assertEqual(data["reply_candidate"], "The item is available. I can ship tonight.")

        work_order = data["work_order"]
        self.assertEqual(work_order["kind"], "customer_message")
        self.assertEqual(work_order["owner"], "customer_service")
        self.assertEqual(work_order["context"]["incoming_message"], "Is this still available?")
        self.assertEqual(work_order["context"]["reply_candidate"], "The item is available. I can ship tonight.")
        self.assertEqual(work_order["context"]["conversation"][0]["content"], "Is this still available?")

        approval = work_order["approvals"][0]
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["action"], "send_message")
        self.assertEqual(approval["payload"]["buyer_id"], "buyer_123")
        self.assertEqual(approval["payload"]["message"], "The item is available. I can ship tonight.")
        self.assertIn("conversation_context", approval["payload"])

        snapshot = approval["draft_snapshot"]
        self.assertEqual(snapshot["source"], "xianyu_im")
        self.assertEqual(snapshot["incoming_message_summary"], "Is this still available?")
        self.assertEqual(snapshot["reply_summary"], "The item is available. I can ship tonight.")
        self.assertEqual(snapshot["conversation_turns"], 1)
        self.assertNotIn("message", snapshot)

        self.assertEqual(len(self.server.workflow.calls), 1)
        self.assertEqual(self.server.workflow.calls[0]["buyer_id"], "buyer_123")
        self.assertEqual(self.server.workflow.calls[0]["message"], "Is this still available?")

    def test_inbound_risk_flags_cover_incoming_and_candidate_reply(self):
        self.server.workflow = FakeWorkflow(reply="Please add vx 123456 to talk.")
        client = TestClient(self.server.app)

        response = client.post(
            "/workflows/messages/inbound",
            json={
                "buyer_id": "buyer_123",
                "message": "Can I add vx?",
                "chat_id": "chat_123",
                "cookie_id": "seller_a",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["work_order"]["risk_level"], "high")
        self.assertGreaterEqual(len(data["risk_flags"]), 1)
        self.assertEqual(data["work_order"]["approvals"][0]["draft_snapshot"]["risk_flags"], data["risk_flags"])

    def test_inbound_route_only_creates_approvals(self):
        route = next(
            (route for route in self.server.app.routes if getattr(route, "path", "") == "/workflows/messages/inbound"),
            None,
        )
        self.assertIsNotNone(route)

        source = inspect.getsource(route.endpoint)
        self.assertIn("approval_required", source)
        self.assertNotIn("execution_queue", source)
        self.assertNotIn("action_executor", source)
        self.assertNotIn("run_next", source)
        self.assertNotIn("adapter.", source)


class FeishuInboundMessageTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_workflow = server.workflow
        self.original_ledger = server.message_intake_ledger
        self.tmp = TemporaryDirectory()
        server.work_order_manager = WorkOrderManager(Path(self.tmp.name) / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(Path(self.tmp.name) / "message_intake.json")
        server.workflow = FakeWorkflow(reply="Yes, it is available.")

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.workflow = self.original_workflow
        self.server.message_intake_ledger = self.original_ledger
        self.tmp.cleanup()

    def test_feishu_event_with_xianyu_buyer_message_uses_inbound_flow(self):
        class FakeRequest:
            async def json(self_inner):
                return {
                    "event": {
                        "message": {
                            "content": json.dumps(
                                {
                                    "xianyu_event": "buyer_message",
                                    "source": "feishu_cli",
                                    "buyer_id": "buyer_feishu",
                                    "message": "Still in stock?",
                                    "chat_id": "chat_feishu",
                                    "item_id": "item_feishu",
                                }
                            )
                        }
                    }
                }

        result = asyncio.run(self.server.feishu_events(FakeRequest()))

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["type"], "customer_message_intake")
        self.assertTrue(result["result"]["approval_required"])
        work_order = result["result"]["work_order"]
        self.assertEqual(work_order["kind"], "customer_message")
        self.assertEqual(work_order["approvals"][0]["action"], "send_message")
        self.assertEqual(work_order["approvals"][0]["payload"]["buyer_id"], "buyer_feishu")


class XianyuWebSocketSafetyTests(unittest.TestCase):
    def test_process_chat_message_does_not_send_candidate_reply_directly(self):
        from xianyu_agent.xianyu_ws import XianyuWebSocket

        sent = []
        events = []

        class FakeAutoReply:
            def generate_reply(self, **kwargs):
                return "Draft reply only."

        async def fake_send_message(ws, chat_id, buyer_id, text):
            sent.append({"chat_id": chat_id, "buyer_id": buyer_id, "text": text})

        client = XianyuWebSocket("", cookie_id="seller_a", auto_reply_engine=FakeAutoReply())
        client.send_message = fake_send_message
        client.on_message(lambda event: events.append(event))

        asyncio.run(client._process_chat_message("chat_1", "buyer_1", "Ada", "Hello", "item_1", ws=None))

        self.assertEqual(sent, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reply"], "Draft reply only.")


class AutomaticMessageIntakeConsoleContractTests(unittest.TestCase):
    def test_console_can_render_inbound_message_snapshots(self):
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn("incoming_message_summary", app_js)
        self.assertIn("reply_summary", app_js)
        self.assertIn('data-action="inbound_message"', app_js)


if __name__ == "__main__":
    unittest.main()
