from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from preflight_helpers import (
    add_preflight_account,
    build_commercial_terms_store,
    build_runtime_store,
    make_preflight_ready_candidate,
)


def _ready_account():
    return {
        "cookie_id": "seller_live",
        "explicit": True,
        "configured": True,
        "enabled": True,
        "account_enabled": True,
        "prompt_ready": True,
        "sync_current": True,
        "runtime_connected": True,
        "runtime_fresh": True,
        "runtime_unblocked": True,
    }


def _candidate(**overrides):
    candidate = {
        "candidate_id": "rc_live",
        "title": "Commercial kitchen table",
        "category": "commercial_kitchen",
        "status": "approved",
        "manual_decision": "approve",
        "supplier_price": 680,
        "supplier_shipping": 80,
        "updated_at": "2026-08-13T09:00:00",
    }
    candidate.update(overrides)
    return candidate


def _ready_terms():
    return {
        "ready": True,
        "failed_gate_codes": [],
        "gates": [{"code": "fee_evidence_ready", "status": "pass", "evidence": "rate=0.08"}],
        "snapshot": {
            "snapshot_version": "terms-v1",
            "observed_at": "2026-08-13T09:00:00Z",
            "platform_fee_rate": 0.08,
            "platform_fixed_fee": 0,
        },
    }


def _preview(**overrides):
    from xianyu_agent.commercial_launch import build_commercial_launch_preview

    payload = {
        "cookie_id": "seller_live",
        "candidate": _candidate(),
        "account": _ready_account(),
        "account_version": {"runtime_heartbeat": 100, "orders_sync": 90, "listings_sync": 90},
        "adapter_health": {
            "adapter": "browser",
            "ready": True,
            "sync_capabilities": {"orders": True, "listings": True},
        },
        "unassigned_review": {"count": 0, "preview_token": "clean"},
        "unassigned_queue_counts": {},
        "active_trials": [],
        "candidate_preflight": {"ready": True, "failed_gate_codes": [], "gates": []},
        "commercial_terms": _ready_terms(),
        "controls": {"cash_budget": 1000, "max_realized_loss": 100},
    }
    payload.update(overrides)
    return build_commercial_launch_preview(**payload)


class CommercialLaunchPureTests(unittest.TestCase):
    def test_ready_preview_enforces_one_account_one_candidate_one_listing(self):
        result = _preview()

        self.assertTrue(result["ready"])
        self.assertEqual(result["controls"]["max_candidates"], 1)
        self.assertEqual(result["controls"]["target_candidates"], 1)
        self.assertEqual(result["controls"]["max_published"], 1)
        self.assertEqual(result["candidate_id"], "rc_live")
        self.assertEqual(result["unit_procurement_cost"], 760)
        self.assertEqual(result["confirmation"], "START 1")
        self.assertEqual(len(result["preview_token"]), 64)
        self.assertTrue(all(gate["status"] == "pass" for gate in result["gates"]))

    def test_gate_order_matches_day_zero_business_sequence(self):
        result = _preview(
            account={},
            adapter_health={"adapter": "stub", "ready": True, "sync_capabilities": {}},
            candidate=None,
            candidate_preflight={},
            unassigned_review={"count": 2, "preview_token": "dirty"},
            unassigned_queue_counts={"pending_approvals": 2},
        )

        codes = [gate["code"] for gate in result["gates"]]
        self.assertEqual(
            codes,
            [
                "historical_work_resolved",
                "account_configured",
                "account_enabled",
                "live_adapter",
                "sync_capabilities",
                "prompt_ready",
                "sync_current",
                "runtime_connected",
                "runtime_fresh",
                "runtime_unblocked",
                "candidate_selected",
                "candidate_manual_approved",
                "candidate_category_valid",
                "account_commercial_terms_ready",
                "candidate_preflight_ready",
                "launch_controls_valid",
                "procurement_cost_known",
                "cash_budget_covers_unit",
                "launch_scope_one_item",
                "no_active_trial",
            ],
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["next_step"]["gate_code"], "historical_work_resolved")

    def test_budget_and_manual_decision_are_real_launch_gates(self):
        result = _preview(
            candidate=_candidate(status="evaluating", manual_decision=None),
            controls={"cash_budget": 700, "max_realized_loss": 100},
        )

        failures = set(result["failed_gate_codes"])
        self.assertIn("candidate_manual_approved", failures)
        self.assertIn("cash_budget_covers_unit", failures)

    def test_cross_border_candidate_cannot_enter_xianyu_alpha(self):
        result = _preview(candidate=_candidate(
            sales_platform="shopee",
            market_scope="cross_border",
        ))

        self.assertFalse(result["ready"])
        gate = {item["code"]: item for item in result["gates"]}["candidate_category_valid"]
        self.assertEqual(gate["status"], "blocked")
        self.assertIn("sales_platform=shopee", gate["evidence"])

    def test_preview_token_changes_when_operating_evidence_changes(self):
        before = _preview()
        after_candidate = _preview(candidate=_candidate(updated_at="2026-08-13T09:01:00"))
        after_runtime = _preview(account_version={"runtime_heartbeat": 101, "orders_sync": 90, "listings_sync": 90})
        after_unassigned = _preview(unassigned_review={"count": 1, "preview_token": "changed"})
        after_preflight_evidence = _preview(candidate_preflight={
            "ready": True,
            "failed_gate_codes": [],
            "gates": [{"code": "supplier_quote_fresh", "status": "pass", "evidence": "age=1h"}],
        })
        changed_terms = _ready_terms()
        changed_terms["snapshot"] = {**changed_terms["snapshot"], "snapshot_version": "terms-v2"}
        after_terms = _preview(commercial_terms=changed_terms)

        self.assertNotEqual(before["preview_token"], after_candidate["preview_token"])
        self.assertNotEqual(before["preview_token"], after_runtime["preview_token"])
        self.assertNotEqual(before["preview_token"], after_unassigned["preview_token"])
        self.assertNotEqual(before["preview_token"], after_preflight_evidence["preview_token"])
        self.assertNotEqual(before["preview_token"], after_terms["preview_token"])

    def test_commercial_terms_gate_and_activation_work_order_are_actionable(self):
        blocked = _preview(commercial_terms={
            "ready": False,
            "failed_gate_codes": ["guarantee_ready", "candidate_fee_matches"],
            "gates": [
                {"code": "guarantee_ready", "status": "fail", "evidence": "required=yes/ready=no"},
                {"code": "candidate_fee_matches", "status": "fail", "evidence": "version=mismatch"},
            ],
        })

        self.assertFalse(blocked["ready"])
        gate = {item["code"]: item for item in blocked["gates"]}["account_commercial_terms_ready"]
        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["action"], "complete_commercial_terms")
        step = next(
            item for item in blocked["activation_plan"]["steps"]
            if item["code"] == "account_commercial_terms_ready"
        )
        self.assertEqual(step["stage"], "06 核验账号商业条款")
        self.assertIn("费率", step["evidence_required"])
        self.assertEqual(blocked["activation_plan"]["total_steps"], 20)


class CommercialLaunchRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.commercial_trial import CommercialTrialStore
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.resale import ResaleManager
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.tmp = Path(tempfile.mkdtemp())
        self.db = DBManager(str(self.tmp / "accounts.db"))
        add_preflight_account(self.db, "seller_live")
        self.runtime = build_runtime_store(self.tmp, "seller_live")
        self.resale = ResaleManager(self.tmp / "resale")
        self.candidate = make_preflight_ready_candidate(
            self.resale,
            category="commercial_kitchen",
            supplier_price=680.0,
            supplier_shipping=80.0,
            target_sale_price=1280.0,
        )
        self.commercial_terms = build_commercial_terms_store(
            self.tmp,
            self.resale,
            self.candidate,
            "seller_live",
        )
        self.candidate = self.resale.get_candidate(self.candidate["candidate_id"])
        self.trials = CommercialTrialStore(self.tmp / "commercial_trials.json")
        self.work_orders = WorkOrderManager(self.tmp / "work_orders.json")
        self.execution = ExecutionJobQueue(self.tmp / "execution_jobs.json")
        self.intake = MessageIntakeLedger(self.tmp / "message_intake.json")
        self.health = {
            "adapter": "browser",
            "configured": True,
            "ready": True,
            "missing": [],
            "sync_capabilities": {"orders": True, "listings": True},
        }
        self.patches = [
            patch.object(server, "db_manager", self.db),
            patch.object(server, "account_runtime_store", self.runtime),
            patch.object(server, "account_commercial_terms_store", self.commercial_terms),
            patch.object(server, "resale_manager", self.resale),
            patch.object(server, "commercial_trial_store", self.trials),
            patch.object(server, "work_order_manager", self.work_orders),
            patch.object(server, "execution_queue", self.execution),
            patch.object(server, "message_intake_ledger", self.intake),
            patch.object(server, "_current_adapter_health", return_value=self.health),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.db.close()

    def _request(self):
        return {
            "cookie_id": "seller_live",
            "candidate_id": self.candidate["candidate_id"],
            "controls": {"cash_budget": 1000, "max_realized_loss": 100},
        }

    def test_preview_is_read_only_and_start_is_atomic_and_idempotent(self):
        preview_response = self.client.post("/commercial-launch/preview", json=self._request())
        self.assertEqual(preview_response.status_code, 200, preview_response.text)
        preview = preview_response.json()
        self.assertTrue(preview["ready"], preview)
        self.assertEqual(self.trials.list_trials(), [])

        start_payload = {
            **self._request(),
            "preview_token": preview["preview_token"],
            "confirmation": preview["confirmation"],
        }
        started = self.client.post("/commercial-launch/start", json=start_payload)
        self.assertEqual(started.status_code, 200, started.text)
        data = started.json()
        self.assertFalse(data["idempotent"])
        self.assertEqual(data["trial"]["candidate_ids"], [self.candidate["candidate_id"]])
        self.assertEqual(data["trial"]["controls"]["max_candidates"], 1)
        self.assertEqual(len(self.trials.list_trials()), 1)

        from xianyu_agent.commercial_trial import CommercialTrialStore

        reloaded_store = CommercialTrialStore(self.tmp / "commercial_trials.json")
        with patch.object(self.server, "commercial_trial_store", reloaded_store):
            repeated = self.client.post("/commercial-launch/start", json=start_payload)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["idempotent"])
        self.assertEqual(repeated.json()["trial"]["trial_id"], data["trial"]["trial_id"])
        persisted = reloaded_store.trial_for_launch_key(preview["preview_token"])
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["launch_mode"], "day0")
        self.assertEqual(len(self.trials.list_trials()), 1)

        changed_controls = self.client.post(
            "/commercial-launch/start",
            json={
                **start_payload,
                "controls": {"cash_budget": 1200, "max_realized_loss": 100},
            },
        )
        self.assertEqual(changed_controls.status_code, 409, changed_controls.text)
        self.assertEqual(changed_controls.json()["detail"]["reason"], "launch_token_conflict")
        self.assertEqual(len(self.trials.list_trials()), 1)

    def test_legacy_direct_create_is_disabled(self):
        response = self.client.post(
            "/commercial-trials",
            json={
                "cookie_id": "seller_live",
                "category": self.candidate["category"],
                "candidate_ids": [self.candidate["candidate_id"]],
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["reason"], "legacy_direct_creation_disabled")
        self.assertEqual(self.trials.list_trials(), [])

    def test_start_rejects_stale_preview_without_side_effects(self):
        preview = self.client.post("/commercial-launch/preview", json=self._request()).json()
        self.resale.set_decision(self.candidate["candidate_id"], "watch", "recheck supplier evidence")

        response = self.client.post(
            "/commercial-launch/start",
            json={
                **self._request(),
                "preview_token": preview["preview_token"],
                "confirmation": preview["confirmation"],
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["reason"], "stale_preview_token")
        self.assertEqual(self.trials.list_trials(), [])

    def test_start_rejects_unresolved_historical_work(self):
        self.work_orders.create_work_order(
            kind="listing",
            title="legacy test listing",
            owner="listing",
            context={"cookie_id": "old_test_account"},
        )

        preview = self.client.post("/commercial-launch/preview", json=self._request()).json()
        self.assertFalse(preview["ready"])
        self.assertIn("historical_work_resolved", preview["failed_gate_codes"])
        response = self.client.post(
            "/commercial-launch/start",
            json={
                **self._request(),
                "preview_token": preview["preview_token"],
                "confirmation": preview["confirmation"],
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["reason"], "launch_gates_blocked")
        self.assertEqual(self.trials.list_trials(), [])

    def test_missing_commercial_terms_blocks_preview_and_creates_no_trial(self):
        from xianyu_agent.account_commercial_terms import AccountCommercialTermsStore

        empty_terms = AccountCommercialTermsStore(self.tmp / "empty_terms.json")
        with patch.object(self.server, "account_commercial_terms_store", empty_terms):
            preview = self.client.post("/commercial-launch/preview", json=self._request()).json()
            response = self.client.post(
                "/commercial-launch/start",
                json={
                    **self._request(),
                    "preview_token": preview["preview_token"],
                    "confirmation": preview["confirmation"],
                },
            )

        self.assertFalse(preview["ready"])
        self.assertIn("account_commercial_terms_ready", preview["failed_gate_codes"])
        self.assertIn("terms_snapshot_present", preview["commercial_terms"]["failed_gate_codes"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.trials.list_trials(), [])


if __name__ == "__main__":
    unittest.main()
