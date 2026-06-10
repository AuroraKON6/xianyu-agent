"""Tests for OPC Dashboard aggregation module.

Verifies read-only aggregation, missing data handling, bottleneck analysis,
and weekly review Markdown generation.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xianyu_agent.opc_dashboard import OPCDashboard


class FakeResaleManager:
    """Minimal fake for testing dashboard aggregation."""

    def __init__(self, candidates=None, orders=None):
        self._candidates = candidates or []
        self._orders = orders or []

    def list_candidates(self, **kwargs):
        return self._candidates

    def list_resale_orders(self, **kwargs):
        return self._orders

    def get_dashboard_summary(self):
        return {"total_candidates": len(self._candidates)}


class FakeWorkOrderManager:
    def __init__(self, orders=None):
        self._orders = orders or []

    def list_work_orders(self, limit=50):
        return self._orders


class FakeExecutionQueue:
    def __init__(self, jobs=None):
        self._jobs = jobs or []

    def list_jobs(self, limit=50):
        return self._jobs


class FakeMessageIntake:
    def __init__(self, metrics=None):
        self._metrics = metrics or {}

    def metrics(self):
        return self._metrics


class OPCDashboardBasicTests(unittest.TestCase):
    def test_opc_daily_returns_all_sections(self):
        dashboard = OPCDashboard()
        result = dashboard.get_opc_daily()

        self.assertIn("today_actions", result)
        self.assertIn("funnel", result)
        self.assertIn("category_performance", result)
        self.assertIn("bottleneck", result)
        self.assertIn("asset_updates", result)
        self.assertIn("source_notes", result)
        self.assertIn("generated_at", result)

    def test_missing_all_sources_returns_zeros(self):
        dashboard = OPCDashboard()
        result = dashboard.get_opc_daily()

        actions = result["today_actions"]
        self.assertEqual(actions["pending_approvals"], 0)
        self.assertEqual(actions["pending_execution"], 0)
        self.assertEqual(actions["pending_reply_drafts"], 0)
        self.assertEqual(actions["failed_jobs"], 0)
        self.assertEqual(actions["new_discoveries"], 0)

    def test_missing_sources_record_notes(self):
        dashboard = OPCDashboard()
        result = dashboard.get_opc_daily()

        notes = result["source_notes"]
        self.assertTrue(len(notes) > 0)
        self.assertTrue(any("未配置" in n for n in notes))

    def test_no_secrets_in_output(self):
        dashboard = OPCDashboard()
        result = dashboard.get_opc_daily()
        result_str = json.dumps(result, ensure_ascii=False)

        # Should not contain common secret patterns
        self.assertNotIn("cookie", result_str.lower())
        self.assertNotIn("token", result_str.lower())
        self.assertNotIn("password", result_str.lower())
        self.assertNotIn("buyer_id", result_str.lower())
        self.assertNotIn("chat_id", result_str.lower())


class OPCDashboardWithDataTests(unittest.TestCase):
    def setUp(self):
        self.resale = FakeResaleManager(
            candidates=[
                {
                    "candidate_id": "rc_001",
                    "category": "商用厨具",
                    "status": "approved",
                    "manual_decision": "approve",
                    "created_at": "2026-06-05T10:00:00",
                    "scoring": {"recommendation": "test", "total_score": 30},
                },
                {
                    "candidate_id": "rc_002",
                    "category": "商用厨具",
                    "status": "evaluating",
                    "manual_decision": None,
                    "created_at": "2026-06-04T10:00:00",
                    "scoring": {"recommendation": "watch", "total_score": 22},
                },
                {
                    "candidate_id": "rc_003",
                    "category": "不锈钢制品",
                    "status": "blocked",
                    "manual_decision": None,
                    "created_at": "2026-06-03T10:00:00",
                    "scoring": {"recommendation": "reject", "total_score": 10},
                },
            ],
            orders=[
                {
                    "order_id": "ro_001",
                    "candidate_id": "rc_001",
                    "status": "completed",
                    "actual_profit": 200.0,
                },
            ],
        )
        self.work_orders = FakeWorkOrderManager(
            orders=[
                {
                    "work_order_id": "wo_001",
                    "approvals": [
                        {"approval_id": "ap_001", "status": "pending"},
                    ],
                },
            ]
        )
        self.exec_queue = FakeExecutionQueue(
            jobs=[
                {"job_id": "ej_001", "status": "queued"},
                {"job_id": "ej_002", "status": "failed"},
                {"job_id": "ej_003", "status": "completed"},
            ]
        )
        self.intake = FakeMessageIntake(
            metrics={"manual_reply_fallbacks": 3}
        )

    def test_today_actions_counts(self):
        dashboard = OPCDashboard(
            resale_manager=self.resale,
            work_order_manager=self.work_orders,
            execution_queue=self.exec_queue,
            message_intake_ledger=self.intake,
        )
        result = dashboard.get_opc_daily()
        actions = result["today_actions"]

        self.assertEqual(actions["pending_approvals"], 1)
        self.assertEqual(actions["pending_execution"], 1)
        self.assertEqual(actions["failed_jobs"], 1)
        self.assertEqual(actions["pending_reply_drafts"], 3)

    def test_funnel_metrics(self):
        dashboard = OPCDashboard(resale_manager=self.resale)
        result = dashboard.get_opc_daily()
        funnel = result["funnel"]

        self.assertEqual(funnel["new_candidates_this_week"], 3)
        self.assertEqual(funnel["testing"], 1)  # approved
        self.assertEqual(funnel["converted"], 1)  # completed order

    def test_category_performance(self):
        dashboard = OPCDashboard(resale_manager=self.resale)
        result = dashboard.get_opc_daily()
        cats = result["category_performance"]

        self.assertTrue(len(cats) > 0)
        kitchen = next((c for c in cats if c["category"] == "商用厨具"), None)
        self.assertIsNotNone(kitchen)
        self.assertEqual(kitchen["candidates"], 2)
        self.assertEqual(kitchen["orders"], 1)
        self.assertEqual(kitchen["revenue"], 200.0)

    def test_bottleneck_analysis(self):
        dashboard = OPCDashboard(resale_manager=self.resale)
        result = dashboard.get_opc_daily()
        bottleneck = result["bottleneck"]

        self.assertIn("stage", bottleneck)
        self.assertIn("description", bottleneck)
        self.assertIn("drop_off", bottleneck)

    def test_source_notes_populated_for_missing_sources(self):
        dashboard = OPCDashboard(resale_manager=self.resale)
        result = dashboard.get_opc_daily()
        notes = result["source_notes"]

        # work_orders, execution_queue, message_intake not configured
        self.assertTrue(any("work_orders" in n for n in notes))
        self.assertTrue(any("execution_queue" in n for n in notes))


class OPCDashboardWeeklyReviewTests(unittest.TestCase):
    def test_weekly_review_creates_markdown_file(self):
        dashboard = OPCDashboard()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = dashboard.generate_weekly_review(Path(tmpdir))

            self.assertTrue(result["ok"])
            self.assertIn("file", result)

            output_path = Path(result["file"])
            self.assertTrue(output_path.exists())
            content = output_path.read_text(encoding="utf-8")
            self.assertIn("OPC 周复盘", content)
            self.assertIn("今日待办", content)
            self.assertIn("漏斗指标", content)
            self.assertIn("品类表现", content)
            self.assertIn("瓶颈分析", content)
            self.assertIn("下一步优先级", content)

    def test_weekly_review_with_data(self):
        resale = FakeResaleManager(
            candidates=[
                {"candidate_id": "rc_001", "category": "商用厨具", "status": "approved",
                 "manual_decision": "approve", "created_at": "2026-06-05T10:00:00"},
            ],
            orders=[],
        )
        dashboard = OPCDashboard(resale_manager=resale)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = dashboard.generate_weekly_review(Path(tmpdir))
            content = Path(result["file"]).read_text(encoding="utf-8")
            self.assertIn("商用厨具", content)


class OPCDashboardBottleneckTests(unittest.TestCase):
    def test_no_candidates_returns_discovery_bottleneck(self):
        dashboard = OPCDashboard()
        result = dashboard.get_opc_daily()
        bottleneck = result["bottleneck"]

        self.assertEqual(bottleneck["stage"], "discovery")
        self.assertIn("暂无", bottleneck["description"])

    def test_candidates_no_orders_returns_listing_bottleneck(self):
        resale = FakeResaleManager(
            candidates=[
                {"candidate_id": "rc_001", "category": "厨具", "status": "approved",
                 "manual_decision": "approve", "created_at": "2026-06-05T10:00:00"},
                {"candidate_id": "rc_002", "category": "厨具", "status": "approved",
                 "manual_decision": "approve", "created_at": "2026-06-05T11:00:00"},
            ],
            orders=[],
        )
        dashboard = OPCDashboard(resale_manager=resale)
        result = dashboard.get_opc_daily()
        bottleneck = result["bottleneck"]

        self.assertIn(bottleneck["stage"], ("listing", "inquiry"))


if __name__ == "__main__":
    unittest.main()
