from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class FakeWorkOrderManager:
    def __init__(self, orders):
        self._orders = orders

    def list_work_orders(self, limit=500):
        return self._orders


class FakeExecutionQueue:
    def __init__(self, jobs):
        self._jobs = jobs

    def list_jobs(self, limit=500):
        return self._jobs


class FakeMessageIntake:
    def __init__(self, records):
        self._records = records

    def list_recent(self, limit=100):
        return self._records[:limit]


class AccountOpsQueueTests(unittest.TestCase):
    def test_build_account_ops_queues_groups_daily_work_by_cookie_id_without_raw_buyer_ids(self):
        from xianyu_agent.account_ops_queues import build_account_ops_queues

        account_profiles = [
            {
                "cookie_id": "seller_a",
                "enabled": True,
                "remark": "main",
                "capabilities": {"seller_auto_reply": True},
                "counts": {"items": 2, "orders_total": 1},
                "recommended_next_actions": ["Account profile is ready for monitored seller-mode operation."],
            },
            {
                "cookie_id": "seller_b",
                "enabled": False,
                "remark": "backup",
                "capabilities": {"seller_auto_reply": False},
                "counts": {"items": 0, "orders_total": 0},
                "recommended_next_actions": ["Enable the account before running live monitoring."],
            },
        ]
        work_orders = FakeWorkOrderManager(
            [
                {
                    "work_order_id": "wo_a",
                    "kind": "customer_message",
                    "title": "reply",
                    "context": {"cookie_id": "seller_a", "buyer_id": "buyer-secret"},
                    "approvals": [
                        {
                            "approval_id": "ap_pending",
                            "action": "send_message",
                            "status": "pending",
                            "payload": {"cookie_id": "seller_a", "buyer_id": "buyer-secret"},
                            "draft_snapshot": {"message_summary": "still available"},
                        },
                        {
                            "approval_id": "ap_approved",
                            "action": "publish_listing",
                            "status": "approved",
                            "payload": {"cookie_id": "seller_a"},
                        },
                    ],
                },
                {
                    "work_order_id": "wo_unknown",
                    "kind": "listing",
                    "title": "unassigned",
                    "context": {},
                    "approvals": [{"approval_id": "ap_unknown", "action": "publish_listing", "status": "pending"}],
                },
            ]
        )
        execution = FakeExecutionQueue(
            [
                {"job_id": "job_q", "approval_id": "ap_approved", "work_order_id": "wo_a", "status": "queued"},
                {"job_id": "job_f", "approval_id": "missing", "work_order_id": "wo_a", "status": "failed"},
            ]
        )
        intake = FakeMessageIntake(
            [
                {
                    "intake_id": "intake_a",
                    "work_order_id": "wo_a",
                    "approval_id": "ap_pending",
                    "buyer_id": "buyer-secret",
                    "chat_id": "chat-secret",
                    "incoming_message_summary": "还在吗",
                    "status": "created",
                }
            ]
        )

        result = build_account_ops_queues(
            account_profiles=account_profiles,
            prompt_profile_provider=lambda cookie_id: {
                "ready": cookie_id == "seller_a",
                "missing_required_keys": [] if cookie_id == "seller_a" else ["price"],
            },
            work_order_manager=work_orders,
            execution_queue=execution,
            message_intake_ledger=intake,
            adapter_health={"sync_capabilities": {"orders": True, "listings": True}},
            account_runtime_states={
                "seller_a": {
                    "cookie_id": "seller_a",
                    "connection_status": "connected",
                    "last_heartbeat_at": 100.0,
                    "token_fresh": True,
                    "cookie_fresh": True,
                    "manual_takeover_count": 0,
                    "pending_verification_reason": "",
                },
                "seller_b": {
                    "cookie_id": "seller_b",
                    "connection_status": "error",
                    "last_heartbeat_at": 0,
                    "token_fresh": False,
                    "cookie_fresh": False,
                    "manual_takeover_count": 1,
                    "pending_verification_reason": "manual login required",
                },
            },
        )

        accounts = {item["cookie_id"]: item for item in result["accounts"]}
        seller_a = accounts["seller_a"]
        self.assertEqual(seller_a["queues"]["pending_approvals"], 1)
        self.assertEqual(seller_a["queues"]["queued_execution"], 1)
        self.assertEqual(seller_a["queues"]["failed_jobs"], 1)
        self.assertEqual(seller_a["queues"]["pending_reply_drafts"], 1)
        self.assertTrue(seller_a["prompt_profile"]["ready"])
        self.assertEqual(seller_a["account_runtime"]["connection_status"], "connected")
        self.assertTrue(seller_a["sync_capabilities"]["orders"])
        self.assertTrue(any(action["kind"] == "pending_approvals" for action in seller_a["next_actions"]))
        self.assertEqual(result["unassigned"]["queues"]["pending_approvals"], 1)

        seller_b = accounts["seller_b"]
        self.assertFalse(seller_b["prompt_profile"]["ready"])
        self.assertIn("price", seller_b["prompt_profile"]["missing_required_keys"])
        self.assertTrue(any(action["kind"] == "account_disabled" for action in seller_b["next_actions"]))
        self.assertTrue(any(action["kind"] == "runtime_blocked" for action in seller_b["next_actions"]))

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("buyer-secret", serialized)
        self.assertNotIn("chat-secret", serialized)

    def test_dashboard_account_queues_route_returns_per_account_queue(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager

        db = DBManager(str(Path(tempfile.mkdtemp()) / "account_queues.db"))
        db.save_cookie("seller_route", "raw-cookie-secret", "route account")
        db.save_keywords("seller_route", [("在吗", "在的")])
        db.save_item("item_route", "seller_route", title="操作台")

        original_db = server.db_manager
        original_work_orders = server.work_order_manager
        original_execution = server.execution_queue
        original_intake = server.message_intake_ledger
        server.db_manager = db
        server.work_order_manager = FakeWorkOrderManager([])
        server.execution_queue = FakeExecutionQueue([])
        server.message_intake_ledger = FakeMessageIntake([])
        try:
            client = TestClient(server.app)
            response = client.get("/dashboard/account-queues")
        finally:
            server.db_manager = original_db
            server.work_order_manager = original_work_orders
            server.execution_queue = original_execution
            server.message_intake_ledger = original_intake

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["accounts"][0]["cookie_id"], "seller_route")
        self.assertEqual(data["accounts"][0]["queues"]["items"], 1)
        self.assertIn("account_runtime", data["accounts"][0])
        self.assertNotIn("raw-cookie-secret", str(data))


if __name__ == "__main__":
    unittest.main()
