from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class AccountRuntimeTests(unittest.TestCase):
    def test_runtime_store_updates_persists_and_sanitizes_account_state(self):
        from xianyu_agent.account_runtime import AccountRuntimeStore

        runtime_path = Path(tempfile.mkdtemp()) / "runtime.json"
        store = AccountRuntimeStore(runtime_path)

        store.record_connection("seller_a", "connected", timestamp=100.0)
        store.record_heartbeat("seller_a", timestamp=110.0)
        store.record_message("seller_a", timestamp=120.0)
        store.record_token_refresh("seller_a", fresh=True, timestamp=130.0)
        store.record_cookie_refresh("seller_a", fresh=False, timestamp=140.0)
        store.set_manual_takeover(
            "seller_a",
            chat_id="chat-secret",
            reason="operator replied manually",
            until=200.0,
            timestamp=150.0,
        )

        reloaded = AccountRuntimeStore(runtime_path)
        public_state = reloaded.get_public_state("seller_a")

        self.assertEqual(public_state["cookie_id"], "seller_a")
        self.assertEqual(public_state["connection_status"], "connected")
        self.assertEqual(public_state["last_heartbeat_at"], 110.0)
        self.assertEqual(public_state["last_message_at"], 120.0)
        self.assertEqual(public_state["last_token_refresh_at"], 130.0)
        self.assertEqual(public_state["last_cookie_refresh_at"], 140.0)
        self.assertTrue(public_state["token_fresh"])
        self.assertFalse(public_state["cookie_fresh"])
        self.assertEqual(public_state["manual_takeover_count"], 1)
        self.assertEqual(public_state["manual_takeover_chats"][0]["reason"], "operator replied manually")
        self.assertNotIn("chat-secret", json.dumps(public_state, ensure_ascii=False))

    def test_authenticated_activity_marks_credentials_and_connection_current(self):
        from xianyu_agent.account_runtime import AccountRuntimeStore

        store = AccountRuntimeStore(Path(tempfile.mkdtemp()) / "runtime.json")
        state = store.record_authenticated_activity("seller_live", timestamp=120.0)

        self.assertEqual(state["connection_status"], "connected")
        self.assertEqual(state["last_heartbeat_at"], 120.0)
        self.assertEqual(state["last_auth_verified_at"], 120.0)
        self.assertTrue(state["token_fresh"])
        self.assertTrue(state["cookie_fresh"])
        self.assertEqual(state["last_error"], "")
        self.assertEqual(state["pending_verification_reason"], "")

    def test_runtime_store_records_errors_and_reconnects(self):
        from xianyu_agent.account_runtime import AccountRuntimeStore

        runtime_path = Path(tempfile.mkdtemp()) / "runtime.json"
        store = AccountRuntimeStore(runtime_path)

        store.record_connection("seller_b", "connecting", timestamp=100.0)
        store.record_reconnect("seller_b", error="websocket closed", timestamp=105.0)
        store.record_error("seller_b", "token expired", pending_verification_reason="manual login required", timestamp=110.0)

        state = store.get_public_state("seller_b")

        self.assertEqual(state["connection_status"], "error")
        self.assertEqual(state["reconnect_count"], 1)
        self.assertEqual(state["last_error"], "token expired")
        self.assertEqual(state["pending_verification_reason"], "manual login required")

    def test_accounts_runtime_route_returns_sanitized_states_for_configured_accounts(self):
        from xianyu_agent import server
        from xianyu_agent.account_runtime import AccountRuntimeStore
        from xianyu_agent.db_manager import DBManager

        runtime_path = Path(tempfile.mkdtemp()) / "runtime.json"
        runtime_store = AccountRuntimeStore(runtime_path)
        runtime_store.record_connection("seller_route", "connected", timestamp=100.0)
        runtime_store.record_heartbeat("seller_route", timestamp=110.0)

        db = DBManager(str(Path(tempfile.mkdtemp()) / "runtime_route.db"))
        db.save_cookie("seller_route", "raw-cookie-secret", "route account")

        original_db = server.db_manager
        original_runtime = server.account_runtime_store
        server.db_manager = db
        server.account_runtime_store = runtime_store
        try:
            response = TestClient(server.app).get("/accounts/runtime")
        finally:
            server.db_manager = original_db
            server.account_runtime_store = original_runtime
            db.close()

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["accounts"][0]["cookie_id"], "seller_route")
        self.assertEqual(data["accounts"][0]["connection_status"], "connected")
        self.assertNotIn("raw-cookie-secret", response.text)


if __name__ == "__main__":
    unittest.main()
