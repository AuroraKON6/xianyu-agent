import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


class FakeWorkflow:
    def __init__(self, reply="Recovered draft reply.", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def generate_auto_reply(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


def record_created(ledger, index, *, status="created", created_at=100.0, approval_id="approval"):
    return ledger.record_created(
        event_key=f"event_{index}",
        source="feishu_cli",
        buyer_id=f"buyer_{index}",
        chat_id=f"chat_{index}",
        item_id=f"item_{index}",
        incoming_message_summary=f"inbound {index}",
        reply_summary=f"reply {index}",
        risk_flags=[],
        work_order_id=f"wo_{index}",
        approval_id=approval_id,
        status=status,
        generation_error="model unavailable" if status == "generation_failed" else "",
        created_at=created_at,
    )


class MessageIntakeLedgerRetentionAndAlertTests(unittest.TestCase):
    def test_retention_prunes_by_age_then_max_records(self):
        from xianyu_agent.message_intake import MessageIntakeLedger

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "message_intake.json"
            ledger = MessageIntakeLedger(path)
            record_created(ledger, 1, created_at=10.0)
            record_created(ledger, 2, created_at=20.0)
            record_created(ledger, 3, created_at=30.0)
            record_created(ledger, 4, created_at=40.0)

            result = ledger.prune(max_age_seconds=35, max_records=2, now=50.0)

            self.assertEqual(result["removed"], 2)
            self.assertEqual(result["kept"], 2)
            self.assertEqual([record["buyer_id"] for record in ledger.list_recent(limit=10)], ["buyer_4", "buyer_3"])

            restored = MessageIntakeLedger(path)
            self.assertEqual([record["buyer_id"] for record in restored.list_recent(limit=10)], ["buyer_4", "buyer_3"])

    def test_health_reports_generation_failure_duplicate_rate_and_staleness_alerts(self):
        from xianyu_agent.message_intake import MessageIntakeLedger

        with TemporaryDirectory() as tmp:
            ledger = MessageIntakeLedger(Path(tmp) / "message_intake.json")
            record_created(ledger, 1, status="generation_failed", created_at=800.0, approval_id="")
            record_created(ledger, 2, status="generation_failed", created_at=810.0, approval_id="")
            record_created(ledger, 3, status="generation_failed", created_at=820.0, approval_id="")
            record_created(ledger, 4, status="created", created_at=830.0)
            for index in range(5, 11):
                ledger.record_duplicate(
                    event_key="event_4",
                    source="feishu_cli",
                    duplicate_of="wo_4",
                    created_at=840.0 + index,
                )

            health = ledger.health(
                window_seconds=300,
                failure_threshold=3,
                duplicate_rate_threshold=0.5,
                duplicate_min_events=5,
                stale_after_seconds=120,
                now=1000.0,
            )

            self.assertFalse(health["ok"])
            self.assertEqual(health["bridge_status"], "stale")
            self.assertEqual(health["last_event_age_seconds"], 150)
            alert_codes = {alert["code"] for alert in health["alerts"]}
            self.assertIn("generation_failures", alert_codes)
            self.assertIn("duplicate_rate", alert_codes)
            self.assertIn("bridge_stale", alert_codes)


class MessageIntakeRetryRouteTests(unittest.TestCase):
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
        server.workflow = FakeWorkflow(reply=None)

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.workflow = self.original_workflow
        self.server.message_intake_ledger = self.original_ledger
        self.tmp.cleanup()

    def test_alerting_retention_and_retry_routes_are_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/message-intake/health", paths)
        self.assertIn("/message-intake/retention/prune", paths)
        self.assertIn("/message-intake/{intake_id}/retry", paths)

    def test_retry_manual_fallback_creates_draft_reply_approval_without_execution(self):
        client = TestClient(self.server.app)
        inbound = client.post(
            "/workflows/messages/inbound",
            json={
                "source": "feishu_cli",
                "message_id": "msg_retry_manual",
                "buyer_id": "buyer_retry",
                "buyer_name": "Ada",
                "message": "Still available?",
                "chat_id": "chat_retry",
                "item_id": "item_retry",
                "product_title": "Keyboard",
                "cookie_id": "seller_a",
            },
        )
        self.assertEqual(inbound.status_code, 200, inbound.text)
        inbound_data = inbound.json()
        self.assertTrue(inbound_data["needs_manual_reply"])
        self.assertFalse(inbound_data["approval_required"])

        self.server.workflow = FakeWorkflow(reply="Yes, it is still available.")
        intake_id = inbound_data["intake_record"]["intake_id"]
        retry = client.post(f"/message-intake/{intake_id}/retry")

        self.assertEqual(retry.status_code, 200, retry.text)
        data = retry.json()
        self.assertEqual(data["type"], "customer_message_intake_retry")
        self.assertTrue(data["approval_required"])
        self.assertEqual(data["work_order"]["work_order_id"], inbound_data["work_order"]["work_order_id"])
        self.assertEqual(data["reply_candidate"], "Yes, it is still available.")

        approval = data["work_order"]["approvals"][0]
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["action"], "send_message")
        self.assertEqual(approval["payload"]["buyer_id"], "buyer_retry")
        self.assertEqual(approval["payload"]["message"], "Yes, it is still available.")

        updated_record = data["intake_record"]
        self.assertEqual(updated_record["status"], "retry_created")
        self.assertEqual(updated_record["approval_id"], approval["approval_id"])

    def test_retry_endpoint_only_creates_approval_not_execution_jobs(self):
        route = next(
            (route for route in self.server.app.routes if getattr(route, "path", "") == "/message-intake/{intake_id}/retry"),
            None,
        )
        self.assertIsNotNone(route)

        source = inspect.getsource(route.endpoint)
        self.assertIn("approval_required", source)
        self.assertNotIn("execution_queue", source)
        self.assertNotIn("action_executor", source)
        self.assertNotIn("run_next", source)


class MessageIntakeAlertingConsoleAndDocsTests(unittest.TestCase):
    def test_console_exposes_health_alert_retention_and_retry_controls(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="message-intake-health"', console_html)
        self.assertIn('id="message-intake-alerts"', console_html)
        self.assertIn('id="prune-message-intake"', console_html)
        self.assertIn("/message-intake/health", app_js)
        self.assertIn("/message-intake/retention/prune", app_js)
        self.assertIn("retryMessageIntake", app_js)

    def test_docs_describe_alerting_retention_health_and_retry_endpoints(self):
        workflow_docs = Path("docs/openclaw_feishu_workflow.md").read_text(encoding="utf-8")
        project_plan = Path("docs/PROJECT_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("GET /message-intake/health", workflow_docs)
        self.assertIn("POST /message-intake/retention/prune", workflow_docs)
        self.assertIn("POST /message-intake/{intake_id}/retry", workflow_docs)
        self.assertIn("Phase 14: Message Intake Alerting and Retention", project_plan)
        self.assertIn("Status: implemented", project_plan)


if __name__ == "__main__":
    unittest.main()
