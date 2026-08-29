from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class ExecutionEvidenceTests(unittest.TestCase):
    def _make_job(self, tmp: Path):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        wom = WorkOrderManager(tmp / "work_orders.json")
        eq = ExecutionJobQueue(tmp / "execution_jobs.json")
        order = wom.create_work_order("listing", "Publish with evidence", "listing")
        wom.create_action_plan(
            order["work_order_id"],
            proposed_actions=[
                {
                    "agent": "listing",
                    "action": "publish_listing",
                    "description": "publish listing",
                    "params": {"draft_id": "draft_1"},
                },
            ],
        )
        approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
        wom.decide_approval(approval_id, decision="approved", note="ok")
        job = eq.create_job(approval_id, wom)
        return wom, eq, job

    def test_execution_job_persists_handler_evidence_on_failure(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.execution_queue import ExecutionJobQueue

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wom, eq, job = self._make_job(tmp)

            executor = ActionExecutor()
            executor.register_handler(
                "publish_listing",
                lambda approval, ctx: {
                    "ok": False,
                    "error": "page blocked",
                    "evidence": {
                        "before_screenshot": "evidence/before.png",
                        "after_screenshot": "evidence/after.png",
                        "page_url": "https://example.test/publish",
                        "detected_blocker": "login_required",
                        "cooldown_seconds": 45,
                        "operator_next_action": "restore_login_then_retry",
                    },
                },
            )

            eq.run_next(executor, wom)
            stored = eq.get_job(job["job_id"])

            self.assertEqual(stored["status"], "failed")
            self.assertEqual(stored["evidence"]["detected_blocker"], "login_required")
            self.assertEqual(stored["evidence"]["page_url"], "https://example.test/publish")
            self.assertEqual(stored["evidence"]["cooldown_seconds"], 45)
            self.assertEqual(stored["operator_next_action"], "restore_login_then_retry")

            reloaded = ExecutionJobQueue(tmp / "execution_jobs.json")
            restored = reloaded.get_job(job["job_id"])
            self.assertEqual(restored["evidence"]["after_screenshot"], "evidence/after.png")

    def test_execution_queue_can_record_manual_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _wom, eq, job = self._make_job(tmp)

            updated = eq.record_evidence(
                job["job_id"],
                {
                    "page_url": "https://example.test/check",
                    "detected_blocker": "captcha",
                    "operator_next_action": "manual_verify_browser",
                },
            )

            self.assertEqual(updated["evidence"]["detected_blocker"], "captcha")
            self.assertEqual(updated["operator_next_action"], "manual_verify_browser")


class DashboardRuntimeEvidenceTests(unittest.TestCase):
    def test_dashboard_summarizes_order_runtime_and_execution_blockers(self):
        from xianyu_agent.opc_dashboard import OPCDashboard

        class FakeOrderRuntime:
            def summary(self):
                return {
                    "total_orders": 2,
                    "needs_fulfillment": 1,
                    "after_sales": 1,
                    "blocked": 1,
                    "locked": 1,
                    "by_status": {"paid": 1, "refund_requested": 1},
                    "next_actions": [
                        {
                            "order_id": "order_1",
                            "status": "paid",
                            "operator_next_action": "verify_order_and_prepare_fulfillment",
                            "pending_blocker": "",
                        }
                    ],
                }

        class FakeExecutionQueue:
            def list_jobs(self, limit=50):
                return [
                    {
                        "job_id": "job_1",
                        "status": "failed",
                        "action": "publish_listing",
                        "evidence": {"detected_blocker": "login_required"},
                        "operator_next_action": "restore_login_then_retry",
                    }
                ]

        dashboard = OPCDashboard(
            execution_queue=FakeExecutionQueue(),
            order_runtime_store=FakeOrderRuntime(),
            adapter_health_provider=lambda: {"adapter": "stub", "ready": True, "configured": True},
        )

        result = dashboard.get_opc_daily()

        self.assertEqual(result["order_runtime"]["needs_fulfillment"], 1)
        self.assertEqual(result["execution_evidence"]["failed_with_blocker"], 1)
        kinds = [action["kind"] for action in result["next_actions"]]
        self.assertIn("order_fulfillment", kinds)
        self.assertIn("execution_blocker", kinds)


if __name__ == "__main__":
    unittest.main()
