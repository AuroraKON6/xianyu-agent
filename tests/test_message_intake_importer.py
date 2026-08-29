from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi.testclient import TestClient


class FakeWorkflow:
    def __init__(self, adapter, reply: str = "Draft reply from importer."):
        self.adapter = adapter
        self.reply = reply
        self.calls = []

    def generate_auto_reply(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


class BrowserMessageImportTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.browser_adapter import BrowserAdapter
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

        settings = SimpleNamespace(data_dir=tmp, browser_cdp_url="", xianyu_cookies="")
        self.adapter = BrowserAdapter(settings, storage=None, browser_pool=None)
        server.workflow = FakeWorkflow(self.adapter)

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.workflow = self.original_workflow
        self.server.message_intake_ledger = self.original_ledger
        self.tmp.cleanup()

    def test_browser_message_import_route_is_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/message-intake/import-browser", paths)

    def test_browser_message_import_batches_inbound_messages_and_skips_noise(self):
        async def fake_get_messages(buyer_id: str = "", cookie_id: str = "default"):
            self.assertEqual(buyer_id, "buyer_import")
            self.assertEqual(cookie_id, "seller_import")
            return [
                {
                    "message_id": "browser_msg_1",
                    "sender": "Ada",
                    "content": "Still available?",
                    "time": "1710000000000",
                    "is_self": False,
                },
                {
                    "message_id": "browser_msg_self",
                    "sender": "me",
                    "content": "Yes, it is.",
                    "time": "1710000001000",
                    "is_self": True,
                },
                {
                    "message_id": "browser_msg_1",
                    "sender": "Ada",
                    "content": "Still available?",
                    "time": "1710000000000",
                    "is_self": False,
                },
                {
                    "message_id": "browser_msg_empty",
                    "sender": "Ada",
                    "content": "",
                    "time": "1710000002000",
                    "is_self": False,
                },
            ]

        self.adapter.get_messages = fake_get_messages
        client = TestClient(self.server.app)

        response = client.post(
            "/message-intake/import-browser",
            json={
                "cookie_id": "seller_import",
                "buyer_id": "buyer_import",
                "buyer_name": "Ada",
                "chat_id": "chat_import",
                "item_id": "item_import",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["type"], "browser_message_import")
        self.assertEqual(data["total"], 4)
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["duplicates"], 1)
        self.assertEqual(data["skipped_outbound"], 1)
        self.assertEqual(data["skipped_invalid"], 1)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(len(data["results"]), 4)
        self.assertEqual(data["results"][0]["status"], "created")
        self.assertEqual(data["results"][1]["status"], "skipped_outbound")
        self.assertEqual(data["results"][2]["status"], "duplicate")
        self.assertEqual(data["results"][3]["status"], "skipped_invalid")

        work_orders = self.server.work_order_manager.list_work_orders(limit=20)
        self.assertEqual(len(work_orders), 1)
        self.assertEqual(work_orders[0]["context"]["buyer_id"], "buyer_import")
        self.assertEqual(work_orders[0]["context"]["message_envelope"]["source"], "browser")
        self.assertEqual(work_orders[0]["context"]["message_envelope"]["ack_status"], "pending")
        self.assertEqual(self.server.workflow.calls[0]["message"], "Still available?")

    def test_browser_conversation_import_route_is_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/message-intake/import-browser-conversations", paths)

    def test_browser_conversation_import_reads_unread_conversations_and_imports_messages(self):
        calls = []

        async def fake_get_conversations(cookie_id: str = "default"):
            self.assertEqual(cookie_id, "seller_queue")
            return [
                {
                    "session_id": "chat_unread",
                    "buyer_id": "buyer_unread",
                    "buyer_name": "Una",
                    "last_message": "Need details",
                    "unread": 2,
                },
                {
                    "session_id": "chat_read",
                    "buyer_id": "buyer_read",
                    "buyer_name": "Ray",
                    "last_message": "Thanks",
                    "unread": 0,
                },
                {
                    "session_id": "chat_missing",
                    "buyer_id": "",
                    "buyer_name": "Missing",
                    "last_message": "No buyer id",
                    "unread": 1,
                },
            ]

        async def fake_get_messages(buyer_id: str = "", cookie_id: str = "default"):
            calls.append({"buyer_id": buyer_id, "cookie_id": cookie_id})
            return [
                {
                    "message_id": f"msg_{buyer_id}_1",
                    "sender": "Una",
                    "content": "Need details",
                    "time": "1710000003000",
                    "is_self": False,
                },
                {
                    "message_id": f"msg_{buyer_id}_self",
                    "sender": "me",
                    "content": "One sec.",
                    "time": "1710000004000",
                    "is_self": True,
                },
            ]

        self.adapter.get_conversations = fake_get_conversations
        self.adapter.get_messages = fake_get_messages
        client = TestClient(self.server.app)

        response = client.post(
            "/message-intake/import-browser-conversations",
            json={
                "cookie_id": "seller_queue",
                "messages_per_conversation": 10,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["type"], "browser_conversation_import")
        self.assertEqual(data["conversations_total"], 3)
        self.assertEqual(data["conversations_selected"], 1)
        self.assertEqual(data["conversations_skipped_read"], 1)
        self.assertEqual(data["conversations_skipped_invalid"], 1)
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["skipped_outbound"], 1)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(calls, [{"buyer_id": "buyer_unread", "cookie_id": "seller_queue"}])

        imports = data["conversation_results"]
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0]["buyer_id"], "buyer_unread")
        self.assertEqual(imports[0]["chat_id"], "chat_unread")
        self.assertEqual(imports[0]["import_result"]["created"], 1)

        work_orders = self.server.work_order_manager.list_work_orders(limit=20)
        self.assertEqual(len(work_orders), 1)
        self.assertEqual(work_orders[0]["context"]["buyer_id"], "buyer_unread")
        self.assertEqual(work_orders[0]["context"]["chat_id"], "chat_unread")


if __name__ == "__main__":
    unittest.main()
