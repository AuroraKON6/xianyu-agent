import inspect
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


def record_created(
    ledger,
    index,
    *,
    status="created",
    created_at=100.0,
    buyer_id=None,
    chat_id=None,
    event_key=None,
    approval_id=None,
):
    return ledger.record_created(
        event_key=event_key or f"message_id:feishu_cli:seller_secret:raw_msg_{index}",
        source="feishu_cli",
        buyer_id=buyer_id or f"buyer_secret_{index}",
        chat_id=chat_id or f"chat_secret_{index}",
        item_id=f"item_{index}",
        incoming_message_summary=f"buyer asked about availability {index}",
        reply_summary=f"draft reply summary {index}",
        risk_flags=["payment_off_platform"] if status == "generation_failed" else [],
        work_order_id=f"wo_secret_{index}",
        approval_id=approval_id if approval_id is not None else f"approval_{index}",
        status=status,
        generation_error="deepseek outage" if status == "generation_failed" else "",
        created_at=created_at,
    )


class MessageIntakeRecoveryLedgerTests(unittest.TestCase):
    def test_runbooks_cover_message_intake_alert_codes(self):
        from xianyu_agent.message_intake import get_message_intake_runbooks

        runbooks = get_message_intake_runbooks(
            ["bridge_stale", "duplicate_rate", "generation_failures", "model_recovery"]
        )

        codes = {runbook["code"] for runbook in runbooks}
        self.assertEqual(
            codes,
            {"bridge_stale", "duplicate_rate", "generation_failures", "model_recovery"},
        )
        for runbook in runbooks:
            self.assertGreaterEqual(len(runbook["checks"]), 2)
            self.assertGreaterEqual(len(runbook["recovery_steps"]), 2)
            self.assertIn("approval", " ".join(runbook["safety_boundary"]).lower())
            self.assertIn("/message-intake/health", runbook["related_endpoints"])

    def test_incident_bundle_redacts_identifiers_and_keeps_summaries(self):
        from xianyu_agent.message_intake import MessageIntakeLedger

        with TemporaryDirectory() as tmp:
            ledger = MessageIntakeLedger(Path(tmp) / "message_intake.json")
            record_created(
                ledger,
                1,
                status="generation_failed",
                created_at=100.0,
                buyer_id="buyer_secret_123",
                chat_id="chat_secret_123",
                event_key="message_id:feishu_cli:seller_cookie_secret:raw_msg_secret",
                approval_id="approval_secret_123",
            )
            ledger.record_duplicate(
                event_key="message_id:feishu_cli:seller_cookie_secret:raw_msg_secret",
                source="feishu_cli",
                duplicate_of="wo_secret_1",
                created_at=110.0,
            )

            bundle = ledger.incident_bundle(
                limit=5,
                now=500.0,
                health_options={
                    "window_seconds": 600,
                    "failure_threshold": 1,
                    "duplicate_min_events": 1,
                    "stale_after_seconds": 60,
                },
            )

            self.assertEqual(bundle["type"], "message_intake_incident_bundle")
            self.assertTrue(bundle["dry_run"])
            self.assertEqual(bundle["metrics"]["total_records"], 2)
            self.assertEqual(bundle["recent_intakes"][0]["buyer_ref"][:5], "hash:")
            self.assertIn("buyer asked about availability", bundle["recent_intakes"][0]["incoming_summary"])
            self.assertTrue(bundle["runbooks"])

            dumped = json.dumps(bundle, ensure_ascii=False)
            self.assertNotIn("buyer_secret_123", dumped)
            self.assertNotIn("chat_secret_123", dumped)
            self.assertNotIn("seller_cookie_secret", dumped)
            self.assertNotIn("raw_msg_secret", dumped)
            self.assertNotIn("approval_secret_123", dumped)
            self.assertNotIn("event_key", dumped)


class MessageIntakeRecoveryRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_ledger = server.message_intake_ledger
        self.tmp = TemporaryDirectory()
        tmp = Path(self.tmp.name)
        server.work_order_manager = WorkOrderManager(tmp / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(tmp / "message_intake.json")

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.message_intake_ledger = self.original_ledger
        self.tmp.cleanup()

    def test_recovery_routes_are_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/message-intake/runbooks", paths)
        self.assertIn("/message-intake/incident-bundle", paths)
        self.assertIn("/message-intake/health-check/dry-run", paths)

    def test_dry_run_health_check_previews_notifications_without_mutating_work_orders(self):
        client = TestClient(self.server.app)
        record_created(
            self.server.message_intake_ledger,
            1,
            status="generation_failed",
            created_at=100.0,
            approval_id="",
        )
        self.server.work_order_manager.create_work_order(
            kind="customer_message",
            title="Existing work order",
            owner="customer_service",
            context={"source": "test"},
        )
        before_work_orders = self.server.work_order_manager.list_work_orders(limit=20)
        before_metrics = self.server.message_intake_ledger.metrics()

        response = client.post(
            "/message-intake/health-check/dry-run",
            json={
                "window_minutes": 60,
                "failure_threshold": 1,
                "stale_after_minutes": 1,
                "notification_hooks": ["console", "local_file"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["type"], "message_intake_health_check")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["health"]["ok"])
        self.assertGreaterEqual(len(data["notification_previews"]), 2)
        self.assertEqual(
            [order["work_order_id"] for order in self.server.work_order_manager.list_work_orders(limit=20)],
            [order["work_order_id"] for order in before_work_orders],
        )
        self.assertEqual(self.server.message_intake_ledger.metrics(), before_metrics)

    def test_incident_bundle_endpoint_returns_sanitized_export(self):
        client = TestClient(self.server.app)
        record_created(
            self.server.message_intake_ledger,
            7,
            status="generation_failed",
            created_at=100.0,
            buyer_id="buyer_secret_export",
            chat_id="chat_secret_export",
            event_key="message_id:feishu_cli:cookie_secret_export:raw_msg_export",
            approval_id="approval_secret_export",
        )

        response = client.get("/message-intake/incident-bundle?limit=3&failure_threshold=1&stale_after_minutes=1")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["type"], "message_intake_incident_bundle")
        self.assertTrue(data["recent_intakes"])
        dumped = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("buyer_secret_export", dumped)
        self.assertNotIn("chat_secret_export", dumped)
        self.assertNotIn("cookie_secret_export", dumped)
        self.assertNotIn("raw_msg_export", dumped)
        self.assertNotIn("approval_secret_export", dumped)

    def test_dry_run_endpoint_does_not_reference_execution_paths(self):
        route = next(
            (
                route
                for route in self.server.app.routes
                if getattr(route, "path", "") == "/message-intake/health-check/dry-run"
            ),
            None,
        )
        self.assertIsNotNone(route)

        source = inspect.getsource(route.endpoint)
        self.assertNotIn("execution_queue", source)
        self.assertNotIn("action_executor", source)
        self.assertNotIn("create_work_order", source)
        self.assertNotIn("decide_approval", source)


class MessageIntakeRecoveryConsoleAndDocsTests(unittest.TestCase):
    def test_console_exposes_recovery_controls(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('id="export-message-intake-bundle"', console_html)
        self.assertIn('id="run-message-intake-dry-run"', console_html)
        self.assertIn('id="message-intake-runbooks"', console_html)
        self.assertIn("/message-intake/runbooks", app_js)
        self.assertIn("/message-intake/incident-bundle", app_js)
        self.assertIn("/message-intake/health-check/dry-run", app_js)
        self.assertIn("exportMessageIntakeBundle", app_js)
        self.assertIn("runMessageIntakeDryRun", app_js)

    def test_docs_describe_phase15_recovery_endpoints(self):
        workflow_docs = Path("docs/openclaw_feishu_workflow.md").read_text(encoding="utf-8")
        project_plan = Path("docs/PROJECT_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("GET /message-intake/runbooks", workflow_docs)
        self.assertIn("GET /message-intake/incident-bundle", workflow_docs)
        self.assertIn("POST /message-intake/health-check/dry-run", workflow_docs)
        self.assertIn("Phase 15: Bridge Runbooks and Recovery Automation", project_plan)
        self.assertIn("Status: implemented", project_plan)


if __name__ == "__main__":
    unittest.main()
