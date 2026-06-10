import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


class FakeWorkflow:
    def __init__(self, reply="Draft reply from hardening tests.", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def generate_auto_reply(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


class MessageIntakeHardeningRouteTests(unittest.TestCase):
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

    def test_message_intake_observability_routes_are_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/message-intake/recent", paths)
        self.assertIn("/message-intake/metrics", paths)

    def test_duplicate_inbound_event_does_not_create_second_work_order(self):
        client = TestClient(self.server.app)
        payload = {
            "source": "feishu_cli",
            "message_id": "msg_dedupe_1",
            "buyer_id": "buyer_dedupe",
            "message": "Do you still have it?",
            "chat_id": "chat_dedupe",
            "item_id": "item_dedupe",
            "cookie_id": "seller_a",
        }

        first = client.post("/workflows/messages/inbound", json=payload)
        second = client.post("/workflows/messages/inbound", json=payload)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        first_data = first.json()
        second_data = second.json()
        self.assertEqual(first_data["type"], "customer_message_intake")
        self.assertTrue(first_data["approval_required"])
        self.assertEqual(second_data["type"], "customer_message_intake_duplicate")
        self.assertTrue(second_data["duplicate"])
        self.assertFalse(second_data["approval_required"])
        self.assertEqual(second_data["existing_work_order_id"], first_data["work_order"]["work_order_id"])
        self.assertEqual(len(self.server.workflow.calls), 1)
        self.assertEqual(len(self.server.work_order_manager.list_work_orders(limit=20)), 1)

        metrics = client.get("/message-intake/metrics").json()
        self.assertEqual(metrics["created_intakes"], 1)
        self.assertEqual(metrics["duplicate_intakes"], 1)
        self.assertEqual(metrics["pending_approvals"], 1)

    def test_manual_reply_and_generation_failure_are_visible_in_metrics(self):
        client = TestClient(self.server.app)
        self.server.workflow = FakeWorkflow(reply=None)

        manual = client.post(
            "/workflows/messages/inbound",
            json={
                "message_id": "msg_manual",
                "buyer_id": "buyer_manual",
                "message": "thanks",
                "chat_id": "chat_manual",
            },
        )

        self.server.workflow = FakeWorkflow(error=RuntimeError("model unavailable"))
        failed = client.post(
            "/workflows/messages/inbound",
            json={
                "message_id": "msg_failure",
                "buyer_id": "buyer_failure",
                "message": "hello",
                "chat_id": "chat_failure",
            },
        )

        self.assertEqual(manual.status_code, 200, manual.text)
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertTrue(manual.json()["needs_manual_reply"])
        self.assertFalse(manual.json()["approval_required"])
        self.assertTrue(failed.json()["needs_manual_reply"])

        metrics = client.get("/message-intake/metrics").json()
        self.assertEqual(metrics["manual_reply_fallbacks"], 2)
        self.assertEqual(metrics["generation_failures"], 1)
        self.assertEqual(metrics["pending_approvals"], 0)

        recent = client.get("/message-intake/recent?limit=5").json()
        statuses = {record["status"] for record in recent}
        self.assertIn("manual_reply", statuses)
        self.assertIn("generation_failed", statuses)


class MessageIntakeLedgerTests(unittest.TestCase):
    def test_ledger_persists_recent_records_and_metrics(self):
        from xianyu_agent.message_intake import MessageIntakeLedger

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "message_intake.json"
            ledger = MessageIntakeLedger(path)
            ledger.record_created(
                event_key="event_1",
                source="xianyu_im",
                buyer_id="buyer_1",
                chat_id="chat_1",
                item_id="item_1",
                incoming_message_summary="hello",
                reply_summary="hi",
                risk_flags=["vx"],
                work_order_id="wo_1",
                approval_id="approval_1",
                status="created",
            )
            ledger.record_duplicate(event_key="event_1", source="xianyu_im", duplicate_of="wo_1")

            restored = MessageIntakeLedger(path)
            recent = restored.list_recent(limit=10)
            metrics = restored.metrics()

            self.assertEqual(len(recent), 2)
            self.assertEqual(metrics["created_intakes"], 1)
            self.assertEqual(metrics["duplicate_intakes"], 1)
            self.assertEqual(metrics["risk_flagged_intakes"], 1)
            self.assertEqual(restored.find_created("event_1")["work_order_id"], "wo_1")


class MessageIntakeConsoleAndDocsTests(unittest.TestCase):
    def test_console_has_message_intake_operator_view(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="message-intake-metrics"', console_html)
        self.assertIn('id="message-intake-feed"', console_html)
        self.assertIn('id="refresh-message-intake"', console_html)
        self.assertIn("/message-intake/metrics", app_js)
        self.assertIn("/message-intake/recent", app_js)
        self.assertIn("renderMessageIntakeFeed", app_js)

    def test_feishu_cli_docs_show_structured_buyer_message_example(self):
        docs = Path("docs/openclaw_feishu_workflow.md").read_text(encoding="utf-8")

        self.assertIn('"xianyu_event": "buyer_message"', docs)
        self.assertIn('"message_id"', docs)
        self.assertIn("/message-intake/metrics", docs)
        self.assertIn("/message-intake/recent", docs)


if __name__ == "__main__":
    unittest.main()
