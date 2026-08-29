from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class FirstRunReadinessTests(unittest.TestCase):
    def test_first_run_readiness_blocks_without_accounts(self):
        from xianyu_agent.first_run_readiness import build_first_run_readiness

        result = build_first_run_readiness(
            account_profiles=[],
            account_queues={"accounts": [], "unassigned": {"queues": {}}},
            adapter_health={"adapter": "browser", "ready": True, "sync_capabilities": {"orders": True, "listings": True}},
        )

        self.assertFalse(result["ready_for_first_run"])
        self.assertEqual(result["next_step"]["gate_id"], "account_configured")
        gates = {gate["id"]: gate for gate in result["gates"]}
        self.assertFalse(gates["account_configured"]["passed"])
        self.assertTrue(gates["adapter_ready"]["passed"])

    def test_first_run_readiness_passes_when_account_prompt_sync_and_adapter_are_ready(self):
        from xianyu_agent.first_run_readiness import build_first_run_readiness

        result = build_first_run_readiness(
            account_profiles=[
                {
                    "cookie_id": "seller_ready",
                    "enabled": True,
                    "counts": {"items": 2},
                    "capabilities": {"seller_auto_reply": True},
                }
            ],
            account_queues={
                "accounts": [
                    {
                        "cookie_id": "seller_ready",
                        "enabled": True,
                        "queues": {"items": 2, "pending_approvals": 0},
                        "prompt_profile": {"ready": True, "missing_required_keys": []},
                        "account_runtime": {
                            "connection_status": "connected",
                            "last_heartbeat_at": 100.0,
                            "token_fresh": True,
                            "cookie_fresh": True,
                            "pending_verification_reason": "",
                        },
                        "sync_freshness": {
                            "listings": {"fresh": True, "last_count": 2},
                            "orders": {"fresh": True, "last_count": 0},
                        },
                    }
                ],
                "unassigned": {"queues": {"pending_approvals": 0, "queued_execution": 0, "pending_reply_drafts": 0}},
            },
            adapter_health={"adapter": "browser", "ready": True, "sync_capabilities": {"orders": True, "listings": True}},
            now=110.0,
        )

        self.assertTrue(result["ready_for_first_run"])
        self.assertEqual(result["next_step"]["gate_id"], "ready")
        self.assertTrue(all(gate["passed"] for gate in result["gates"]))
        self.assertEqual(result["selected_account"]["cookie_id"], "seller_ready")
        self.assertTrue(result["selected_account"]["ready"])

    def test_first_run_readiness_blocks_when_runtime_state_is_missing_or_unfresh(self):
        from xianyu_agent.first_run_readiness import build_first_run_readiness

        result = build_first_run_readiness(
            account_profiles=[
                {
                    "cookie_id": "seller_ready",
                    "enabled": True,
                    "counts": {"items": 2},
                    "capabilities": {"seller_auto_reply": True},
                }
            ],
            account_queues={
                "accounts": [
                    {
                        "cookie_id": "seller_ready",
                        "enabled": True,
                        "queues": {"items": 2},
                        "prompt_profile": {"ready": True, "missing_required_keys": []},
                        "account_runtime": {
                            "connection_status": "error",
                            "last_heartbeat_at": 0,
                            "token_fresh": False,
                            "cookie_fresh": True,
                            "pending_verification_reason": "manual login required",
                        },
                        "sync_freshness": {
                            "listings": {"fresh": True},
                            "orders": {"fresh": True},
                        },
                    }
                ],
                "unassigned": {"queues": {}},
            },
            adapter_health={"adapter": "browser", "ready": True, "sync_capabilities": {"orders": True, "listings": True}},
            now=110.0,
        )

        gates = {gate["id"]: gate for gate in result["gates"]}
        self.assertFalse(result["ready_for_first_run"])
        self.assertFalse(gates["runtime_connected"]["passed"])
        self.assertFalse(gates["runtime_fresh"]["passed"])
        self.assertFalse(gates["runtime_unblocked"]["passed"])
        self.assertEqual(result["next_step"]["gate_id"], "runtime_connected")

    def test_readiness_does_not_combine_partial_state_from_different_accounts(self):
        from xianyu_agent.first_run_readiness import build_first_run_readiness

        result = build_first_run_readiness(
            account_profiles=[
                {"cookie_id": "seller_prompt", "enabled": True, "counts": {"items": 2}},
                {"cookie_id": "seller_runtime", "enabled": True, "counts": {"items": 0}},
            ],
            account_queues={
                "accounts": [
                    {
                        "cookie_id": "seller_prompt",
                        "enabled": True,
                        "queues": {"items": 2},
                        "prompt_profile": {"ready": True},
                        "sync_freshness": {
                            "orders": {"fresh": True},
                            "listings": {"fresh": True},
                        },
                        "account_runtime": {
                            "connection_status": "disconnected",
                            "last_heartbeat_at": 0,
                            "token_fresh": False,
                            "cookie_fresh": False,
                        },
                    },
                    {
                        "cookie_id": "seller_runtime",
                        "enabled": True,
                        "queues": {"items": 0},
                        "prompt_profile": {"ready": False},
                        "sync_freshness": {},
                        "account_runtime": {
                            "connection_status": "connected",
                            "last_heartbeat_at": 105.0,
                            "token_fresh": True,
                            "cookie_fresh": True,
                            "pending_verification_reason": "",
                        },
                    },
                ],
                "unassigned": {"queues": {}},
            },
            adapter_health={"adapter": "browser", "ready": True, "sync_capabilities": {"orders": True, "listings": True}},
            now=110.0,
        )

        self.assertFalse(result["ready_for_first_run"])
        self.assertEqual(result["selected_account"]["cookie_id"], "seller_prompt")
        self.assertFalse(result["selected_account"]["ready"])
        self.assertEqual(len(result["account_readiness"]), 2)

    def test_readiness_requires_both_order_and_listing_sync_for_same_account(self):
        from xianyu_agent.first_run_readiness import build_first_run_readiness

        result = build_first_run_readiness(
            account_profiles=[{"cookie_id": "seller_a", "enabled": True, "counts": {"items": 1}}],
            account_queues={
                "accounts": [
                    {
                        "cookie_id": "seller_a",
                        "enabled": True,
                        "queues": {"items": 1},
                        "prompt_profile": {"ready": True},
                        "sync_freshness": {"listings": {"fresh": True}},
                        "account_runtime": {
                            "connection_status": "connected",
                            "last_heartbeat_at": 105.0,
                            "token_fresh": True,
                            "cookie_fresh": True,
                            "pending_verification_reason": "",
                        },
                    }
                ],
                "unassigned": {"queues": {}},
            },
            adapter_health={"adapter": "browser", "ready": True, "sync_capabilities": {"orders": True, "listings": True}},
            now=110.0,
        )

        gates = {gate["id"]: gate for gate in result["gates"]}
        self.assertFalse(result["ready_for_first_run"])
        self.assertFalse(gates["sync_current"]["passed"])

    def test_stub_adapter_is_not_ready_for_a_real_account_run(self):
        from xianyu_agent.first_run_readiness import build_first_run_readiness

        result = build_first_run_readiness(
            account_profiles=[],
            account_queues={"accounts": [], "unassigned": {"queues": {}}},
            adapter_health={"adapter": "stub", "ready": True, "sync_capabilities": {"orders": True, "listings": True}},
        )

        gates = {gate["id"]: gate for gate in result["gates"]}
        self.assertFalse(gates["adapter_ready"]["passed"])

    def test_first_run_readiness_route_returns_sanitized_gates(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager

        db = DBManager(str(Path(tempfile.mkdtemp()) / "first_run.db"))
        db.save_cookie("seller_route", "raw-cookie-secret", "route account")
        db.save_ai_reply_settings("seller_route", {"ai_enabled": True, "custom_prompts": {"price": "secret prompt"}})

        with patch.object(server, "db_manager", db):
            response = TestClient(server.app).get("/dashboard/first-run-readiness")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("ready_for_first_run", data)
        self.assertIn("gates", data)
        self.assertIn("next_step", data)
        self.assertNotIn("raw-cookie-secret", response.text)
        self.assertNotIn("secret prompt", response.text)
        db.close()


if __name__ == "__main__":
    unittest.main()
