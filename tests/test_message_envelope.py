from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


class MessageEnvelopeNormalizationTests(unittest.TestCase):
    def test_normalize_xianyu_ws_payload_extracts_standard_fields(self):
        from xianyu_agent.message_envelope import normalize_message_envelope

        envelope = normalize_message_envelope(
            source="xianyu_ws",
            raw_payload={
                "message_id": "mid_ws_1",
                "senderUserId": "buyer_1",
                "senderNick": "Ada",
                "reminderContent": "Still available?",
                "sessionId": "chat_1",
                "itemId": "item_1",
                "createTime": 1710000000000,
            },
            cookie_id="seller_a",
        )

        self.assertEqual(envelope["source"], "xianyu_ws")
        self.assertEqual(envelope["source_message_id"], "mid_ws_1")
        self.assertEqual(envelope["cookie_id"], "seller_a")
        self.assertEqual(envelope["buyer_id"], "buyer_1")
        self.assertEqual(envelope["buyer_name"], "Ada")
        self.assertEqual(envelope["chat_id"], "chat_1")
        self.assertEqual(envelope["item_id"], "item_1")
        self.assertEqual(envelope["message_text"], "Still available?")
        self.assertEqual(envelope["direction"], "inbound")
        self.assertEqual(envelope["ack_status"], "pending")
        self.assertIn("message_id:xianyu_ws:seller_a:mid_ws_1", envelope["dedupe_key"])

    def test_normalize_browser_message_uses_aliases_and_context_defaults(self):
        from xianyu_agent.message_envelope import normalize_message_envelope

        envelope = normalize_message_envelope(
            source="browser",
            raw_payload={
                "messageId": "browser_msg_1",
                "senderId": "buyer_2",
                "senderNick": "Ben",
                "content": "Can you ship today?",
                "createTime": "1710000001000",
                "isSelf": False,
            },
            cookie_id="seller_b",
            defaults={"chat_id": "session_2", "item_id": "item_2"},
        )

        self.assertEqual(envelope["source"], "browser")
        self.assertEqual(envelope["source_message_id"], "browser_msg_1")
        self.assertEqual(envelope["buyer_id"], "buyer_2")
        self.assertEqual(envelope["chat_id"], "session_2")
        self.assertEqual(envelope["item_id"], "item_2")
        self.assertEqual(envelope["message_text"], "Can you ship today?")
        self.assertEqual(envelope["direction"], "inbound")

    def test_to_inbound_request_payload_maps_envelope_without_raw_payload(self):
        from xianyu_agent.message_envelope import normalize_message_envelope, to_inbound_request_payload

        envelope = normalize_message_envelope(
            source="manual",
            raw_payload={
                "message_id": "manual_1",
                "buyer_id": "buyer_3",
                "buyer_name": "Cy",
                "message": "Specs?",
                "chat_id": "chat_3",
                "item_id": "item_3",
            },
            cookie_id="seller_c",
        )

        payload = to_inbound_request_payload(envelope)

        self.assertEqual(payload["source"], "manual")
        self.assertEqual(payload["message_id"], "manual_1")
        self.assertEqual(payload["buyer_id"], "buyer_3")
        self.assertEqual(payload["message"], "Specs?")
        self.assertEqual(payload["chat_id"], "chat_3")
        self.assertEqual(payload["item_id"], "item_3")
        self.assertEqual(payload["cookie_id"], "seller_c")
        self.assertIn("message_envelope", payload["context"])
        self.assertNotIn("raw_payload", payload["context"]["message_envelope"])


class FakeWorkflow:
    def __init__(self, reply="Envelope draft reply."):
        self.reply = reply
        self.calls = []

    def generate_auto_reply(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


class MessageEnvelopeRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_workflow = server.workflow
        self.original_ledger = server.message_intake_ledger
        self.tmp = TemporaryDirectory()
        tmp = Path(self.tmp.name)
        server.work_order_manager = WorkOrderManager(tmp / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(tmp / "message_intake.json")
        server.workflow = FakeWorkflow()

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.workflow = self.original_workflow
        self.server.message_intake_ledger = self.original_ledger
        self.tmp.cleanup()

    def test_message_envelope_route_is_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/message-intake/envelope", paths)

    def test_message_envelope_route_creates_intake_and_persists_ack_status(self):
        client = TestClient(self.server.app)

        response = client.post(
            "/message-intake/envelope",
            json={
                "source": "browser",
                "cookie_id": "seller_route",
                "defaults": {"chat_id": "chat_route", "item_id": "item_route"},
                "raw_payload": {
                    "messageId": "msg_route_1",
                    "senderId": "buyer_route",
                    "senderNick": "Rui",
                    "content": "Can I pick it up?",
                    "createTime": "1710000002000",
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["type"], "customer_message_intake")
        self.assertEqual(data["message_envelope"]["source"], "browser")
        self.assertEqual(data["message_envelope"]["ack_status"], "recorded")

        work_order = data["work_order"]
        self.assertEqual(work_order["context"]["message_envelope"]["dedupe_key"], data["message_envelope"]["dedupe_key"])
        self.assertEqual(work_order["approvals"][0]["payload"]["source"], "browser")

        intake_record = data["intake_record"]
        self.assertEqual(intake_record["source_message_id"], "msg_route_1")
        self.assertEqual(intake_record["ack_status"], "recorded")
        self.assertEqual(intake_record["envelope_id"], data["message_envelope"]["envelope_id"])

    def test_duplicate_envelope_does_not_create_second_work_order(self):
        client = TestClient(self.server.app)
        payload = {
            "source": "xianyu_ws",
            "cookie_id": "seller_dup",
            "raw_payload": {
                "message_id": "msg_dup_1",
                "buyer_id": "buyer_dup",
                "message": "Same message",
                "chat_id": "chat_dup",
                "item_id": "item_dup",
            },
        }

        first = client.post("/message-intake/envelope", json=payload)
        second = client.post("/message-intake/envelope", json=payload)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["type"], "customer_message_intake")
        self.assertEqual(second.json()["type"], "customer_message_intake_duplicate")
        self.assertFalse(second.json()["approval_required"])
        self.assertEqual(len(self.server.work_order_manager.list_work_orders(limit=20)), 1)
        self.assertEqual(len(self.server.workflow.calls), 1)
        self.assertEqual(second.json()["message_envelope"]["ack_status"], "duplicate")


class XianyuWebSocketEnvelopeTests(unittest.TestCase):
    def test_process_chat_message_callback_includes_message_envelope(self):
        from xianyu_agent.xianyu_ws import XianyuWebSocket

        events = []

        class FakeAutoReply:
            def generate_reply(self, **kwargs):
                return "Draft only."

        client = XianyuWebSocket("", cookie_id="seller_ws", auto_reply_engine=FakeAutoReply())
        client.on_message(lambda event: events.append(event))

        asyncio.run(client._process_chat_message("chat_ws", "buyer_ws", "Ada", "Hello", "item_ws", ws=None))

        self.assertEqual(len(events), 1)
        envelope = events[0]["message_envelope"]
        self.assertEqual(envelope["source"], "xianyu_ws")
        self.assertEqual(envelope["cookie_id"], "seller_ws")
        self.assertEqual(envelope["buyer_id"], "buyer_ws")
        self.assertEqual(envelope["chat_id"], "chat_ws")
        self.assertEqual(envelope["item_id"], "item_ws")
        self.assertEqual(envelope["message_text"], "Hello")
        self.assertEqual(envelope["ack_status"], "pending")


if __name__ == "__main__":
    unittest.main()
