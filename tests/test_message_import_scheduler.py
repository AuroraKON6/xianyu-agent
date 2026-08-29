from __future__ import annotations

from datetime import datetime
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


class MessageImportSchedulerStoreTests(unittest.TestCase):
    def test_scheduler_health_actions_cover_account_level_operating_states(self):
        from xianyu_agent.message_import_scheduler import build_scheduler_health_actions

        last_run_timestamp = 1_700_000_000
        health = build_scheduler_health_actions(
            [
                {"cookie_id": "seller_missing", "enabled": True},
                {"cookie_id": "seller_due", "enabled": True},
                {"cookie_id": "seller_failed", "enabled": True},
                {"cookie_id": "seller_stale", "enabled": True},
                {"cookie_id": "seller_disabled", "enabled": False},
            ],
            {
                "profiles": {
                    "seller_due": {"enabled": True, "due": True, "interval_seconds": 60},
                    "seller_failed": {
                        "enabled": True,
                        "due": False,
                        "consecutive_failures": 2,
                        "backoff_seconds": 120,
                        "last_error": "adapter unavailable",
                    },
                    "seller_stale": {
                        "enabled": True,
                        "due": False,
                        "interval_seconds": 60,
                        "last_run_at": datetime.fromtimestamp(last_run_timestamp).isoformat(timespec="seconds"),
                    },
                    "seller_disabled": {"enabled": True, "due": False},
                }
            },
            now=last_run_timestamp + 9000,
        )

        self.assertEqual(health["status"], "critical")
        self.assertEqual(health["missing_profiles"], ["seller_missing"])
        self.assertEqual(health["due_profiles"], ["seller_due"])
        self.assertEqual(health["failed_profiles"][0]["account"], "seller_failed")
        self.assertEqual(health["backing_off_profiles"][0]["backoff_seconds"], 120)
        self.assertEqual(health["orphaned_profiles"], ["seller_disabled"])
        self.assertTrue(any(item["account"] == "seller_stale" for item in health["stale_profiles"]))
        kinds = {item["kind"] for item in health["actions"]}
        self.assertIn("scheduler_profile_missing", kinds)
        self.assertIn("scheduler_due", kinds)
        self.assertIn("scheduler_error", kinds)
        self.assertIn("scheduler_stale", kinds)
        self.assertIn("scheduler_profile_orphaned", kinds)
        self.assertNotIn("cookie_id", str(health))

    def test_scheduler_store_persists_config_and_due_state(self):
        from xianyu_agent.message_import_scheduler import MessageImportSchedulerStore

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scheduler.json"
            store = MessageImportSchedulerStore(path)

            initial = store.snapshot(now=1000)
            self.assertFalse(initial["enabled"])
            self.assertEqual(initial["interval_seconds"], 300)
            self.assertFalse(initial["due"])

            configured = store.configure(
                {
                    "enabled": True,
                    "cookie_id": "seller_sched",
                    "interval_seconds": 120,
                    "max_conversations": 5,
                    "messages_per_conversation": 7,
                },
                now=1000,
            )
            self.assertTrue(configured["enabled"])
            self.assertEqual(configured["cookie_id"], "seller_sched")
            self.assertEqual(configured["interval_seconds"], 120)
            self.assertTrue(configured["due"])

            reloaded = MessageImportSchedulerStore(path)
            self.assertTrue(reloaded.snapshot(now=1000)["enabled"])
            self.assertTrue(reloaded.snapshot(now=1121)["due"])

    def test_scheduler_error_uses_backoff_and_success_resets_failures(self):
        from xianyu_agent.message_import_scheduler import MessageImportSchedulerStore

        with TemporaryDirectory() as tmp:
            store = MessageImportSchedulerStore(Path(tmp) / "scheduler.json")
            store.configure({"enabled": True, "interval_seconds": 60}, now=1000)

            first = store.record_error("adapter unavailable", now=1000)
            self.assertEqual(first["consecutive_failures"], 1)
            self.assertEqual(first["backoff_seconds"], 60)
            self.assertFalse(store.snapshot(now=1059)["due"])
            self.assertTrue(store.snapshot(now=1060)["due"])

            second = store.record_error("adapter unavailable", now=1060)
            self.assertEqual(second["consecutive_failures"], 2)
            self.assertEqual(second["backoff_seconds"], 120)
            self.assertFalse(store.snapshot(now=1179)["due"])
            self.assertTrue(store.snapshot(now=1180)["due"])

            recovered = store.record_result({"type": "browser_conversation_import", "created": 0}, now=1180)
            self.assertEqual(recovered["consecutive_failures"], 0)
            self.assertEqual(recovered["backoff_seconds"], 0)
            self.assertFalse(store.snapshot(now=1239)["due"])
            self.assertTrue(store.snapshot(now=1240)["due"])

    def test_scheduler_profiles_keep_account_state_independent(self):
        from xianyu_agent.message_import_scheduler import MessageImportSchedulerStore

        with TemporaryDirectory() as tmp:
            store = MessageImportSchedulerStore(Path(tmp) / "scheduler.json")
            store.configure_profile(
                "seller_a",
                {"enabled": True, "interval_seconds": 60, "max_conversations": 2},
                now=1000,
            )
            store.configure_profile(
                "seller_b",
                {"enabled": True, "interval_seconds": 300, "max_conversations": 8},
                now=1000,
            )

            store.record_error("adapter unavailable", cookie_id="seller_a", now=1000)
            store.record_result(
                {"type": "browser_conversation_import", "created": 2},
                cookie_id="seller_b",
                now=1000,
            )
            profiles = store.list_profiles(now=1060)

            self.assertEqual(set(profiles["profiles"].keys()), {"seller_a", "seller_b"})
            self.assertEqual(profiles["active_cookie_id"], "seller_b")
            self.assertTrue(profiles["profiles"]["seller_a"]["due"])
            self.assertFalse(profiles["profiles"]["seller_b"]["due"])
            self.assertEqual(profiles["profiles"]["seller_a"]["consecutive_failures"], 1)
            self.assertEqual(profiles["profiles"]["seller_a"]["backoff_seconds"], 60)
            self.assertEqual(profiles["profiles"]["seller_b"]["last_result"]["created"], 2)
            self.assertEqual(
                store.request_payload(cookie_id="seller_b")["max_conversations"],
                8,
            )

    def test_bootstrap_from_account_profiles_creates_enabled_profiles_without_overwriting(self):
        from xianyu_agent.message_import_scheduler import MessageImportSchedulerStore

        with TemporaryDirectory() as tmp:
            store = MessageImportSchedulerStore(Path(tmp) / "scheduler.json")
            store.configure_profile(
                "seller_existing",
                {"enabled": True, "interval_seconds": 60, "max_conversations": 2},
                now=900,
            )
            profiles = [
                {"cookie_id": "seller_existing", "enabled": True},
                {"cookie_id": "seller_new", "enabled": True},
                {"cookie_id": "seller_disabled", "enabled": False},
            ]

            preview = store.bootstrap_from_account_profiles(profiles, preset="observe", dry_run=True, now=1000)
            self.assertEqual(preview["type"], "message_import_scheduler_bootstrap")
            self.assertTrue(preview["dry_run"])
            self.assertEqual(preview["created"], 1)
            self.assertEqual(preview["updated"], 0)
            self.assertEqual(preview["skipped"], 2)
            self.assertNotIn("seller_new", store.list_profiles(now=1000)["profiles"])

            result = store.bootstrap_from_account_profiles(profiles, preset="observe", now=1000)
            stored_profiles = result["scheduler"]["profiles"]

            self.assertEqual(result["created"], 1)
            self.assertEqual(result["updated"], 0)
            self.assertEqual(result["skipped"], 2)
            self.assertEqual(stored_profiles["seller_new"]["interval_seconds"], 600)
            self.assertEqual(stored_profiles["seller_new"]["max_conversations"], 10)
            self.assertEqual(stored_profiles["seller_existing"]["interval_seconds"], 60)
            self.assertNotIn("seller_disabled", stored_profiles)

            overwritten = store.bootstrap_from_account_profiles(profiles, preset="high_touch", overwrite=True, now=1100)
            overwritten_profiles = overwritten["scheduler"]["profiles"]
            self.assertEqual(overwritten["updated"], 2)
            self.assertEqual(overwritten_profiles["seller_existing"]["interval_seconds"], 60)
            self.assertEqual(overwritten_profiles["seller_existing"]["max_conversations"], 30)


class FakeWorkflow:
    def __init__(self, adapter, reply: str = "Scheduled draft reply."):
        self.adapter = adapter
        self.reply = reply
        self.calls = []

    def generate_auto_reply(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


class MessageImportSchedulerRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.browser_adapter import BrowserAdapter
        from xianyu_agent.message_import_scheduler import MessageImportSchedulerStore
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_workflow = server.workflow
        self.original_ledger = server.message_intake_ledger
        self.original_scheduler = server.message_import_scheduler_store
        self.tmp = TemporaryDirectory()
        tmp = Path(self.tmp.name)

        server.work_order_manager = WorkOrderManager(tmp / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(tmp / "message_intake.json")
        server.message_import_scheduler_store = MessageImportSchedulerStore(tmp / "message_import_scheduler.json")

        settings = SimpleNamespace(data_dir=tmp, browser_cdp_url="", xianyu_cookies="")
        self.adapter = BrowserAdapter(settings, storage=None, browser_pool=None)
        server.workflow = FakeWorkflow(self.adapter)

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.workflow = self.original_workflow
        self.server.message_intake_ledger = self.original_ledger
        self.server.message_import_scheduler_store = self.original_scheduler
        self.tmp.cleanup()

    def test_scheduler_routes_are_registered(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]

        self.assertIn("/message-intake/import-scheduler", paths)
        self.assertIn("/message-intake/import-scheduler/bootstrap-accounts", paths)
        self.assertIn("/message-intake/import-scheduler/run-now", paths)

    def test_bootstrap_route_creates_profiles_from_account_profiles(self):
        from xianyu_agent.db_manager import DBManager

        db = DBManager(str(Path(self.tmp.name) / "accounts.db"))
        db.save_cookie("seller_a", "secret-cookie-a", "main")
        db.save_cookie_status("seller_a", True)
        db.save_cookie("seller_b", "secret-cookie-b", "backup")
        db.save_cookie_status("seller_b", False)

        with patch.object(self.server, "db_manager", db):
            client = TestClient(self.server.app)
            response = client.post(
                "/message-intake/import-scheduler/bootstrap-accounts",
                json={"preset": "observe"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["type"], "message_import_scheduler_bootstrap")
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["skipped"], 1)
        self.assertIn("seller_a", data["scheduler"]["profiles"])
        self.assertNotIn("seller_b", data["scheduler"]["profiles"])
        self.assertNotIn("secret-cookie", response.text)

    def test_run_now_imports_unread_conversations_and_records_scheduler_result(self):
        async def fake_get_conversations(cookie_id: str = "default"):
            self.assertEqual(cookie_id, "seller_sched")
            return [
                {
                    "session_id": "chat_sched",
                    "buyer_id": "buyer_sched",
                    "buyer_name": "Scheduler Buyer",
                    "last_message": "Is it ready?",
                    "unread": 1,
                }
            ]

        async def fake_get_messages(buyer_id: str = "", cookie_id: str = "default"):
            return [
                {
                    "message_id": "msg_sched_1",
                    "sender": "Scheduler Buyer",
                    "content": "Is it ready?",
                    "time": "1710000005000",
                    "is_self": False,
                }
            ]

        self.adapter.get_conversations = fake_get_conversations
        self.adapter.get_messages = fake_get_messages
        client = TestClient(self.server.app)

        configure = client.post(
            "/message-intake/import-scheduler",
            json={
                "enabled": True,
                "cookie_id": "seller_sched",
                "interval_seconds": 120,
                "max_conversations": 3,
                "messages_per_conversation": 5,
            },
        )
        self.assertEqual(configure.status_code, 200, configure.text)
        self.assertTrue(configure.json()["enabled"])

        response = client.post("/message-intake/import-scheduler/run-now")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["type"], "message_import_scheduler_run")
        self.assertEqual(data["import_result"]["type"], "browser_conversation_import")
        self.assertEqual(data["import_result"]["created"], 1)
        self.assertEqual(data["scheduler"]["last_result"]["created"], 1)
        self.assertEqual(data["scheduler"]["run_count"], 1)

        status = client.get("/message-intake/import-scheduler")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["last_result"]["created"], 1)
        self.assertEqual(len(self.server.work_order_manager.list_work_orders(limit=20)), 1)

    def test_run_now_can_target_a_specific_scheduler_profile(self):
        called_cookie_ids = []

        async def fake_get_conversations(cookie_id: str = "default"):
            called_cookie_ids.append(cookie_id)
            return [
                {
                    "session_id": f"chat_{cookie_id}",
                    "buyer_id": f"buyer_{cookie_id}",
                    "buyer_name": "Profile Buyer",
                    "last_message": "Any stock?",
                    "unread": 1,
                }
            ]

        async def fake_get_messages(buyer_id: str = "", cookie_id: str = "default"):
            return [
                {
                    "message_id": f"msg_{cookie_id}",
                    "sender": "Profile Buyer",
                    "content": "Any stock?",
                    "time": "1710000006000",
                    "is_self": False,
                }
            ]

        self.adapter.get_conversations = fake_get_conversations
        self.adapter.get_messages = fake_get_messages
        client = TestClient(self.server.app)

        for cookie_id in ("seller_a", "seller_b"):
            response = client.post(
                "/message-intake/import-scheduler",
                json={"enabled": True, "cookie_id": cookie_id, "interval_seconds": 60},
            )
            self.assertEqual(response.status_code, 200, response.text)

        response = client.post("/message-intake/import-scheduler/run-now?cookie_id=seller_a")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(called_cookie_ids, ["seller_a"])
        self.assertEqual(data["scheduler"]["cookie_id"], "seller_a")
        profiles = client.get("/message-intake/import-scheduler").json()["profiles"]
        self.assertEqual(profiles["seller_a"]["run_count"], 1)
        self.assertEqual(profiles["seller_b"]["run_count"], 0)


if __name__ == "__main__":
    unittest.main()
