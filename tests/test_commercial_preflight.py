"""Phase 18: evidence-based commercial publish preflight tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent import server
from xianyu_agent.commercial_preflight import run_commercial_preflight
from xianyu_agent.db_manager import DBManager
from xianyu_agent.resale import ResaleManager
from xianyu_agent.work_orders import WorkOrderManager

from preflight_helpers import (
    add_preflight_account,
    build_commercial_terms_store,
    build_runtime_store,
    iso_hours_ago,
    iso_seconds_ago,
    make_preflight_ready_candidate,
)


# Fixed "now" shared by evidence generation and preflight evaluation so pure
# tests are deterministic on any calendar date/timezone.
CHECKED_AT = "2026-08-02T11:00:00"


class FakeSettings:
    min_profit_amount = 20.0
    min_profit_rate = 0.30


def ready_account(cookie_id: str = "acct_1"):
    return {
        "cookie_id": cookie_id,
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


def failing_account(cookie_id: str = "acct_1", **overrides):
    account = ready_account(cookie_id)
    account.update(overrides)
    return account


def _make_manager():
    return ResaleManager(Path(tempfile.mkdtemp()))


class CommercialPreflightPureTests(unittest.TestCase):
    def test_passing_preflight_is_ready_with_all_gates_pass(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["cookie_id"], "acct_1")
        self.assertFalse(result["next_step"])
        self.assertTrue(all(g["status"] == "pass" for g in result["gates"]))
        self.assertEqual([g["code"] for g in result["gates"]][:3],
                         ["account_configured", "account_enabled", "prompt_ready"])

    def test_old_record_fails_until_reverified(self):
        manager = _make_manager()
        candidate = manager.create_candidate({
            "title": "旧记录",
            "source_platform": "pdd",
            "supplier_price": 10.0,
            "target_sale_price": 30.0,
            "specifications": {},
        })
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertFalse(result["ready"])
        failed = {g["code"] for g in result["gates"] if g["status"] == "fail"}
        self.assertIn("supplier_verified", failed)
        self.assertIn("supplier_quote_fresh", failed)
        self.assertIn("stock_checked_fresh", failed)
        self.assertIn("fulfillment_verified", failed)
        self.assertIn("candidate_images", failed)
        self.assertIn("image_rights_evidence", failed)
        self.assertIn("structured_fields", failed)

    def test_missing_account_fails_account_and_readiness_gates(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        result = run_commercial_preflight(
            candidate=candidate, account=None, settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertFalse(result["ready"])
        by_code = {g["code"]: g for g in result["gates"]}
        self.assertEqual(by_code["account_configured"]["status"], "fail")
        self.assertEqual(by_code["account_enabled"]["status"], "fail")
        self.assertEqual(by_code["prompt_ready"]["status"], "fail")

    def test_ambiguous_account_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        account = {"cookie_id": "", "explicit": False, "configured": False,
                   "enabled": False, "account_enabled": False,
                   "prompt_ready": False, "sync_current": False,
                   "runtime_connected": False, "runtime_fresh": False,
                   "runtime_unblocked": False}
        result = run_commercial_preflight(
            candidate=candidate, account=account, settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertFalse(result["ready"])
        gate = {g["code"]: g for g in result["gates"]}["account_configured"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("缺少", gate["message"])

    def test_unknown_account_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        account = failing_account("nope", explicit=True, configured=False, enabled=False)
        result = run_commercial_preflight(
            candidate=candidate, account=account, settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["account_configured"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("不存在", gate["message"])

    def test_disabled_account_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        account = failing_account(enabled=False, account_enabled=False)
        result = run_commercial_preflight(
            candidate=candidate, account=account, settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["account_configured"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("未启用", gate["message"])

    def test_cross_border_candidate_fails_before_publish(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        candidate.update({"sales_platform": "shopee", "market_scope": "cross_border"})

        result = run_commercial_preflight(
            candidate=candidate,
            account=ready_account(),
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )

        self.assertFalse(result["ready"])
        gate = {item["code"]: item for item in result["gates"]}["candidate_channel_valid"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("sales_platform=shopee", gate["evidence"])
        self.assertIn("market_scope=cross_border", gate["evidence"])

    def test_stale_supplier_quote_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT, supplier_quote_age_hours=25,
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertFalse(result["ready"])
        gate = {g["code"]: g for g in result["gates"]}["supplier_quote_fresh"]
        self.assertEqual(gate["status"], "fail")

    def test_stale_stock_timestamp_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT, stock_age_hours=30,
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertFalse(result["ready"])
        gate = {g["code"]: g for g in result["gates"]}["stock_checked_fresh"]
        self.assertEqual(gate["status"], "fail")

    def test_future_supplier_quote_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT,
            supplier_quote_age_hours=-1,
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertFalse(result["ready"])
        gate = {g["code"]: g for g in result["gates"]}["supplier_quote_fresh"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("future", gate["evidence"])
        self.assertIn("未来", gate["message"])

    def test_far_future_stock_timestamp_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT, stock_age_hours=-120,
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["stock_checked_fresh"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("future", gate["evidence"])

    def test_exact_24h_passes(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT, supplier_quote_age_hours=24,
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["supplier_quote_fresh"]
        self.assertEqual(gate["status"], "pass")

    def test_24h_plus_one_second_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT,
            supplier_quote_age_hours=24 + 1 / 3600,
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["supplier_quote_fresh"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("> 24h", gate["evidence"])

    def test_non_finite_gross_profit_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        for bad_value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad_value):
                candidate["economics"] = {**candidate["economics"], "gross_profit": bad_value}
                result = run_commercial_preflight(
                    candidate=candidate, account=ready_account(), settings=FakeSettings(),
                    checked_at=CHECKED_AT,
                )
                gate = {g["code"]: g for g in result["gates"]}["min_profit_amount"]
                self.assertEqual(gate["status"], "fail")
                self.assertNotIn("nan", gate["evidence"])
                self.assertNotIn("inf", gate["evidence"])

    def test_non_finite_gross_margin_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        candidate["economics"] = {**candidate["economics"], "gross_margin": float("inf")}
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["min_profit_rate"]
        self.assertEqual(gate["status"], "fail")
        self.assertNotIn("inf", gate["evidence"])

    def test_low_gross_profit_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)

        class HighAmount(FakeSettings):
            min_profit_amount = 1000.0

        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=HighAmount(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["min_profit_amount"]
        self.assertEqual(gate["status"], "fail")

    def test_low_gross_margin_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)

        class HighRate(FakeSettings):
            min_profit_rate = 0.90

        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=HighRate(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["min_profit_rate"]
        self.assertEqual(gate["status"], "fail")

    def test_missing_fulfillment_evidence_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        candidate["fulfillment_verified"] = False
        candidate["fulfillment_verification_note"] = ""
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["fulfillment_verified"]
        self.assertEqual(gate["status"], "fail")

    def test_missing_images_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT, images=[])
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["candidate_images"]
        self.assertEqual(gate["status"], "fail")

    def test_blank_images_fail(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT, images=["", "   ", None],
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["candidate_images"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("images=0", gate["evidence"])

    def test_invalid_image_rights_type_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        candidate["image_rights_type"] = "downloaded"
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["image_rights_type"]
        self.assertEqual(gate["status"], "fail")

    def test_missing_image_rights_evidence_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        candidate["image_rights_evidence"] = ""
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["image_rights_evidence"]
        self.assertEqual(gate["status"], "fail")

    def test_incomplete_structured_fields_fails(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT, title="", specifications={},
        )

    def test_empty_specification_values_fail(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT,
            specifications={"material": "", "size": "   "},
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["structured_fields"]
        self.assertEqual(gate["status"], "fail")
        self.assertIn("specs=0", gate["evidence"])

    def test_numeric_and_boolean_spec_values_are_meaningful(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(
            manager, checked_at=CHECKED_AT,
            specifications={"width_cm": 0, "in_stock": False},
        )
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        gate = {g["code"]: g for g in result["gates"]}["structured_fields"]
        self.assertEqual(gate["status"], "pass")
        self.assertIn("specs=2", gate["evidence"])

    def test_cross_account_readiness_isolation(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        result_b = run_commercial_preflight(
            candidate=candidate,
            account=failing_account("acct_b", sync_current=False, runtime_connected=False,
                                    runtime_fresh=False, runtime_unblocked=False),
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        self.assertFalse(result_b["ready"])
        by_code = {g["code"]: g for g in result_b["gates"]}
        self.assertEqual(by_code["runtime_connected"]["status"], "fail")
        self.assertEqual(by_code["sync_current"]["status"], "fail")

    def test_next_step_is_ordered_by_business_gates(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        candidate["fulfillment_verified"] = False
        candidate["fulfillment_verification_note"] = ""
        candidate["images"] = []
        result = run_commercial_preflight(
            candidate=candidate, account=failing_account(runtime_fresh=False),
            settings=FakeSettings(), checked_at=CHECKED_AT,
        )
        self.assertFalse(result["ready"])
        codes = [step["code"] for step in result["next_step"]]
        # Business order: runtime_fresh before fulfillment_verified before candidate_images.
        self.assertEqual(
            codes,
            ["runtime_fresh", "candidate_activation_ready", "fulfillment_verified", "candidate_images"],
        )

    def test_snapshot_and_evidence_are_sanitized(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, checked_at=CHECKED_AT)
        result = run_commercial_preflight(
            candidate=candidate, account=ready_account(), settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("cookie-value", blob)
        self.assertNotIn("password", blob)
        self.assertNotIn("authorization", blob)
        self.assertEqual(
            sorted(result["snapshot"].keys()),
            ["candidate_activation", "checked_at", "gates"],
        )


class CommercialPreflightHTTPTests(unittest.TestCase):
    def _client_with(self, tmp, manager, *, cookie_ids=("acct_1",)):
        """Return (stack, client, wom) with db/runtime/resale/workorder patched."""
        from contextlib import ExitStack

        db = DBManager(str(tmp / "accounts.db"))
        for cookie_id in cookie_ids:
            add_preflight_account(db, cookie_id=cookie_id)
        wom = WorkOrderManager(tmp / "work_orders.json")
        candidates = manager.list_candidates()
        terms = build_commercial_terms_store(tmp, manager, candidates[0], cookie_ids[0])
        stack = ExitStack()
        stack.enter_context(patch.object(server, "db_manager", db))
        stack.enter_context(patch.object(server, "account_runtime_store", build_runtime_store(tmp, cookie_ids[0])))
        stack.enter_context(patch.object(server, "account_commercial_terms_store", terms))
        stack.enter_context(patch.object(server, "resale_manager", manager))
        stack.enter_context(patch.object(server, "work_order_manager", wom))
        return stack, TestClient(server.app), wom

    def test_get_preflight_ready_for_good_candidate(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        stack, client, _ = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            resp = client.get(
                f"/resale/candidates/{candidate['candidate_id']}/publish-preflight?cookie_id=acct_1"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ready"])

    def test_get_preflight_missing_account_returns_blocked_result(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        stack, client, _ = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            resp = client.get(f"/resale/candidates/{candidate['candidate_id']}/publish-preflight")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["status"], "blocked")
        gate = {g["code"]: g for g in body["gates"]}["account_configured"]
        self.assertEqual(gate["status"], "fail")

    def test_post_failed_preflight_has_zero_side_effects(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, images=[])
        stack, client, wom = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            before_drafts = len(manager._listing_drafts)
            before_wo = len(list(wom.list_work_orders(limit=999)))
            resp = client.post(f"/resale/candidates/{candidate['candidate_id']}/listing-draft?cookie_id=acct_1")
            body = resp.json()["detail"]
            after_drafts = len(manager._listing_drafts)
            after_wo = len(list(wom.list_work_orders(limit=999)))
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(body["ready"])
        self.assertEqual(after_drafts - before_drafts, 0)
        self.assertEqual(after_wo - before_wo, 0)
        self.assertIsNone(manager.get_candidate(candidate["candidate_id"]).get("listing_draft_id"))

    def test_missing_commercial_terms_blocks_before_listing_side_effects(self):
        from xianyu_agent.account_commercial_terms import AccountCommercialTermsStore

        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        tmp = Path(tempfile.mkdtemp())
        stack, client, wom = self._client_with(tmp, manager)
        empty_terms = AccountCommercialTermsStore(tmp / "empty_terms.json")
        with stack, patch.object(server, "account_commercial_terms_store", empty_terms):
            resp = client.post(
                f"/resale/candidates/{candidate['candidate_id']}/listing-draft?cookie_id=acct_1"
            )

        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(
            {gate["code"]: gate for gate in detail["gates"]}["commercial_terms_ready"]["status"],
            "fail",
        )
        self.assertEqual(len(manager._listing_drafts), 0)
        self.assertEqual(len(wom.list_work_orders(limit=999)), 0)

    def test_post_failed_preflight_returns_sanitized_detail(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager, images=[])
        stack, client, _ = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            resp = client.post(f"/resale/candidates/{candidate['candidate_id']}/listing-draft?cookie_id=acct_1")
        self.assertEqual(resp.status_code, 409)
        detail = resp.json()["detail"]
        self.assertIn("gates", detail)
        self.assertIn("next_step", detail)
        self.assertIn("checked_at", detail)
        blob = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("cookie-value", blob)

    def test_post_unknown_account_returns_409(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        stack, client, _ = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            resp = client.post(f"/resale/candidates/{candidate['candidate_id']}/listing-draft?cookie_id=ghost")
        self.assertEqual(resp.status_code, 409)
        gate = {g["code"]: g for g in resp.json()["detail"]["gates"]}["account_configured"]
        self.assertEqual(gate["status"], "fail")

    def test_successful_post_snapshot_is_sanitized(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        stack, client, _ = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            resp = client.post(f"/resale/candidates/{candidate['candidate_id']}/listing-draft?cookie_id=acct_1")
        self.assertEqual(resp.status_code, 200)
        approval = resp.json()["work_order"]["approvals"][0]
        self.assertIn("preflight", approval["payload"])
        snapshot = approval["payload"]["preflight"]
        self.assertEqual(
            sorted(snapshot.keys()),
            ["candidate_activation", "checked_at", "commercial_terms", "gates"],
        )
        blob = json.dumps(resp.json(), ensure_ascii=False)
        self.assertNotIn("cookie-value", blob)

    def test_cross_account_http_isolation(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        tmp = Path(tempfile.mkdtemp())
        db = DBManager(str(tmp / "accounts.db"))
        add_preflight_account(db, "acct_1")
        # acct_2 is configured + enabled but has no prompt/sync/runtime evidence.
        db.save_cookie("acct_2", "cookie-value-acct_2", "acct2")
        db.save_cookie_status("acct_2", True)
        runtime = build_runtime_store(tmp, "acct_1")
        terms = build_commercial_terms_store(tmp, manager, candidate, "acct_1")
        with patch.object(server, "db_manager", db), patch.object(
            server, "account_runtime_store", runtime
        ), patch.object(server, "account_commercial_terms_store", terms), patch.object(
            server, "resale_manager", manager
        ):
            client = TestClient(server.app)
            good = client.get(f"/resale/candidates/{candidate['candidate_id']}/publish-preflight?cookie_id=acct_1")
            other = client.get(f"/resale/candidates/{candidate['candidate_id']}/publish-preflight?cookie_id=acct_2")
        self.assertTrue(good.json()["ready"])
        self.assertFalse(other.json()["ready"])
        other_gates = {g["code"]: g for g in other.json()["gates"]}
        self.assertEqual(other_gates["account_configured"]["status"], "pass")
        self.assertEqual(other_gates["prompt_ready"]["status"], "fail")

    def test_verify_endpoint_updates_images(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        stack, client, _ = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            resp = client.post(
                f"/resale/candidates/{candidate['candidate_id']}/verify",
                json={
                    "supplier_verified": True,
                    "supplier_verification_note": "note",
                    "images": ["https://example.com/new/1.jpg", "https://example.com/new/2.jpg"],
                },
            )
            updated = resp.json()["candidate"]
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(updated["images"], ["https://example.com/new/1.jpg", "https://example.com/new/2.jpg"])

    def test_verify_endpoint_omission_preserves_existing_images(self):
        manager = _make_manager()
        candidate = make_preflight_ready_candidate(manager)
        original_images = list(candidate["images"])
        stack, client, _ = self._client_with(Path(tempfile.mkdtemp()), manager)
        with stack:
            resp = client.post(
                f"/resale/candidates/{candidate['candidate_id']}/verify",
                json={"supplier_verification_note": "只改备注，不带图片"},
            )
            updated = resp.json()["candidate"]
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(updated["images"], original_images)
        self.assertEqual(updated["supplier_verification_note"], "只改备注，不带图片")


if __name__ == "__main__":
    unittest.main()
