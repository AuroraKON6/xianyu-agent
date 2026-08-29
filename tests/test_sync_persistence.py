from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class FakeSyncAdapter:
    name = "fake_sync"

    def sync_orders(self, cookie_id="default", status=""):
        return {
            "ok": True,
            "adapter": self.name,
            "cookie_id": cookie_id,
            "status": status,
            "orders": [
                {
                    "order_id": "order_1",
                    "item_id": "item_1",
                    "buyer_id": "buyer-secret",
                    "status": "paid",
                    "amount": 128.5,
                    "delivery_payload": "delivery-secret",
                }
            ],
        }

    def sync_my_listings(self, cookie_id="default"):
        return {
            "ok": True,
            "adapter": self.name,
            "cookie_id": cookie_id,
            "items": [
                {
                    "item_id": "item_1",
                    "title": "Stainless work table",
                    "price": 799,
                    "description": "Commercial kitchen table",
                    "status": "active",
                }
            ],
        }


class FakePartialFailureAdapter:
    name = "fake_partial_failure"

    def sync_orders(self, cookie_id="default", status=""):
        return {"ok": False, "adapter": self.name, "error": "login expired"}

    def sync_my_listings(self, cookie_id="default"):
        return {"ok": True, "adapter": self.name, "items": []}


class SyncPersistenceTests(unittest.TestCase):
    def test_sync_account_snapshot_persists_orders_listings_and_freshness_without_raw_payloads(self):
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.sync_persistence import sync_account_snapshot

        db = DBManager(str(Path(tempfile.mkdtemp()) / "sync.db"))
        db.save_cookie("seller_a", "raw-cookie-secret", "main account")

        result = sync_account_snapshot(
            db=db,
            adapter=FakeSyncAdapter(),
            cookie_id="seller_a",
            status="paid",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["orders"]["persisted"], 1)
        self.assertEqual(result["listings"]["persisted"], 1)
        self.assertEqual(db.get_order("order_1")["status"], "paid")
        self.assertEqual(db.get_items_by_cookie("seller_a")[0]["title"], "Stainless work table")

        sync_state = db.get_account_sync_state("seller_a")
        self.assertEqual(sync_state["orders"]["last_count"], 1)
        self.assertEqual(sync_state["listings"]["last_count"], 1)
        self.assertEqual(sync_state["orders"]["adapter"], "fake_sync")
        self.assertIn("last_synced_at", sync_state["orders"])

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("raw-cookie-secret", serialized)
        self.assertNotIn("buyer-secret", serialized)
        self.assertNotIn("delivery-secret", serialized)

    def test_failed_resource_sync_does_not_create_false_freshness(self):
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.sync_persistence import sync_account_snapshot

        db = DBManager(str(Path(tempfile.mkdtemp()) / "sync_failure.db"))
        db.save_cookie("seller_failure", "raw-cookie-secret", "failure account")

        result = sync_account_snapshot(
            db=db,
            adapter=FakePartialFailureAdapter(),
            cookie_id="seller_failure",
        )

        self.assertFalse(result["ok"])
        sync_state = db.get_account_sync_state("seller_failure")
        self.assertNotIn("orders", sync_state)
        self.assertTrue(sync_state["listings"]["fresh"])

    def test_sync_persist_route_uses_active_adapter_and_updates_account_queue_freshness(self):
        from xianyu_agent import server
        from xianyu_agent.account_runtime import AccountRuntimeStore
        from xianyu_agent.db_manager import DBManager

        temp_dir = Path(tempfile.mkdtemp())
        db = DBManager(str(temp_dir / "sync_route.db"))
        runtime_store = AccountRuntimeStore(temp_dir / "account_runtime.json")
        db.save_cookie("seller_route", "raw-cookie-secret", "route account")

        original_db = server.db_manager
        original_adapter = server.workflow.adapter
        original_runtime = server.account_runtime_store
        server.db_manager = db
        server.workflow.adapter = FakeSyncAdapter()
        server.account_runtime_store = runtime_store
        try:
            client = TestClient(server.app)
            response = client.post("/adapter/sync-persist?cookie_id=seller_route&status=paid")
            queue_response = client.get("/dashboard/account-queues")
        finally:
            server.db_manager = original_db
            server.workflow.adapter = original_adapter
            server.account_runtime_store = original_runtime

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["orders"]["persisted"], 1)
        self.assertEqual(data["listings"]["persisted"], 1)
        self.assertNotIn("raw-cookie-secret", response.text)
        self.assertNotIn("buyer-secret", response.text)

        self.assertEqual(queue_response.status_code, 200, queue_response.text)
        account = queue_response.json()["accounts"][0]
        self.assertEqual(account["cookie_id"], "seller_route")
        self.assertEqual(account["sync_freshness"]["orders"]["last_count"], 1)
        self.assertEqual(account["sync_freshness"]["listings"]["last_count"], 1)
        self.assertTrue(account["sync_freshness"]["orders"]["fresh"])
        self.assertEqual(account["account_runtime"]["connection_status"], "connected")
        self.assertTrue(account["account_runtime"]["token_fresh"])
        self.assertTrue(account["account_runtime"]["cookie_fresh"])

    def test_partial_sync_failure_marks_runtime_blocked_for_operator_action(self):
        from xianyu_agent import server
        from xianyu_agent.account_runtime import AccountRuntimeStore
        from xianyu_agent.db_manager import DBManager

        temp_dir = Path(tempfile.mkdtemp())
        db = DBManager(str(temp_dir / "sync_route_failure.db"))
        runtime_store = AccountRuntimeStore(temp_dir / "account_runtime_failure.json")
        db.save_cookie("seller_failure", "raw-cookie-secret", "failure account")

        with patch.object(server, "db_manager", db), patch.object(
            server.workflow, "adapter", FakePartialFailureAdapter()
        ), patch.object(server, "account_runtime_store", runtime_store):
            response = TestClient(server.app).post("/adapter/sync-persist?cookie_id=seller_failure")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["ok"])
        runtime = runtime_store.get_public_state("seller_failure")
        self.assertEqual(runtime["connection_status"], "error")
        self.assertEqual(runtime["pending_verification_reason"], "account authentication requires operator attention")


if __name__ == "__main__":
    unittest.main()
