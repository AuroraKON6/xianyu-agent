from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from xianyu_agent.commercial_trial import (
    CommercialTrialStore,
    STAGE_CODES,
    default_controls,
    reconcile_trial,
    validate_controls,
)
from xianyu_agent import server
from xianyu_agent.order_runtime import OrderRuntimeStore
from xianyu_agent.resale import ResaleManager
from xianyu_agent.work_orders import WorkOrderManager


START = 1_800_000_000.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _trial(**overrides):
    controls = default_controls()
    controls.update(
        {
            "max_candidates": 2,
            "max_published": 1,
            "target_candidates": 1,
            "target_inquiries": 2,
            "target_paid_orders": 1,
        }
    )
    trial = {
        "trial_id": "ct_test",
        "cookie_id": "seller_a",
        "category": "商用厨具",
        "lifecycle": "active",
        "controls": controls,
        "candidate_ids": ["rc_1"],
        "decision_history": [],
        "created_at": START,
        "started_at": _iso(START),
    }
    trial.update(overrides)
    return trial


def _candidate(**overrides):
    candidate = {
        "candidate_id": "rc_1",
        "category": "商用厨具",
        "supplier_price": 500,
        "supplier_shipping": 20,
    }
    candidate.update(overrides)
    return candidate


def _ready_account():
    return {
        "cookie_id": "seller_a",
        "configured": True,
        "enabled": True,
        "prompt_ready": True,
        "sync_current": True,
        "runtime_connected": True,
        "runtime_fresh": True,
        "runtime_unblocked": True,
    }


def _work_order(*, cookie_id="seller_a", candidate_id="rc_1", created_at=START + 10, status="approved"):
    return {
        "work_order_id": "wo_1",
        "kind": "resale_listing",
        "created_at": created_at,
        "context": {"cookie_id": cookie_id, "candidate_id": candidate_id},
        "approvals": [
            {
                "approval_id": "approval_1",
                "action": "publish_listing",
                "status": status,
            }
        ],
    }


def _job(*, external_id="item_1", created_at=START + 20, ok=True):
    return {
        "job_id": "job_1",
        "approval_id": "approval_1",
        "action": "publish_listing",
        "status": "completed",
        "created_at": created_at,
        "finished_at": created_at + 1,
        "result": {
            "ok": ok,
            "publish_result": {"ok": ok, "external_id": external_id},
        },
    }


def _purchase_work_order(*, status="approved"):
    return {
        "work_order_id": "wo_purchase_1",
        "kind": "resale_purchase",
        "created_at": START + 44,
        "approvals": [{
            "approval_id": "approval_purchase_1",
            "action": "prepare_purchase_draft",
            "status": status,
        }],
    }


def _intake(
    intake_id,
    *,
    cookie_id="seller_a",
    item_id="item_1",
    created_at=START + 30,
    status="created",
    approval_id="",
):
    return {
        "intake_id": intake_id,
        "cookie_id": cookie_id,
        "item_id": item_id,
        "approval_id": approval_id or f"approval_{intake_id}",
        "work_order_id": f"wo_{intake_id}",
        "status": status,
        "created_at": created_at,
        "buyer_id": "must-not-leak",
        "incoming_message_summary": "must-not-leak",
    }


def _reply_job(intake_id, *, created_at=START + 35, status="completed", ok=True):
    return {
        "job_id": f"job_reply_{intake_id}",
        "approval_id": f"approval_{intake_id}",
        "action": "send_message",
        "status": status,
        "created_at": created_at,
        "finished_at": created_at + 1,
        "result": {"ok": ok, "buyer_id": "must-not-leak"},
    }


def _runtime_order(*, order_id="xy_order_1", cookie_id="seller_a", item_id="item_1", created_at=START + 40, status="delivered"):
    return {
        "order_id": order_id,
        "cookie_id": cookie_id,
        "item_id": item_id,
        "buyer_id": "must-not-leak",
        "amount": 620,
        "status": status,
        "created_at": created_at,
        "status_history": [{"new_status": "shipped", "created_at": created_at + 1}],
    }


def _resale_order(*, resale_order_id="ro_1", xianyu_order_id="xy_order_1", candidate_id="rc_1", created_at=None, status="completed", actual_profit=80):
    return {
        "resale_order_id": resale_order_id,
        "xianyu_order_id": xianyu_order_id,
        "candidate_id": candidate_id,
        "commercial_trial_id": "ct_test",
        "cookie_id": "seller_a",
        "item_id": "item_1",
        "purchase_task_id": "pt_1",
        "purchase_draft_id": "pd_1",
        "fulfillment_lock_owner": "commercial_trial:ct_test",
        "supplier_order_id": "supplier_order_1",
        "supplier_tracking_number": "supplier_tracking_1",
        "xianyu_tracking_number": "xianyu_tracking_1",
        "sale_amount": 620,
        "supplier_cost": 500,
        "shipping_cost": 20,
        "platform_fee": round(620 - 500 - 20 - actual_profit, 2),
        "actual_profit": actual_profit,
        "status": status,
        "created_at": created_at or _iso(START + 50),
        "completed_at": _iso(START + 55) if status == "completed" else None,
        "financial_evidence_note": "已按闲鱼订单实收和供应商账单逐项核对",
        "financial_evidence_ref": "order-ledger/xy_order_1",
        "financial_reconciled_at": _iso(START + 55),
    }


def _handoff():
    return {
        "order_id": "xy_order_1",
        "item_id": "item_1",
        "candidate_id": "rc_1",
        "plan_id": "rp_1",
        "purchase_draft_id": "pd_1",
        "work_order_id": "wo_purchase_1",
        "approval_id": "approval_purchase_1",
        "lock_owner": "commercial_trial:ct_test",
        "quantity": 1,
        "total_cost": 520,
        "created_at": START + 45,
    }


def _reconcile(**overrides):
    payload = {
        "trial": _trial(fulfillment_handoffs=[_handoff()]),
        "account": _ready_account(),
        "candidates": [_candidate()],
        "work_orders": [_work_order(), _purchase_work_order()],
        "jobs": [_job(), _reply_job("intake_1"), _reply_job("intake_2", created_at=START + 36)],
        "intake_records": [_intake("intake_1"), _intake("intake_2", created_at=START + 31)],
        "runtime_orders": [_runtime_order()],
        "resale_orders": [_resale_order()],
        "preflight_results": {"rc_1": {"ready": True}},
        "checked_at": _iso(START + 60),
    }
    payload.update(overrides)
    return reconcile_trial(**payload)


def _stage(result, code):
    return next(stage for stage in result["stages"] if stage["code"] == code)


def test_control_validation_is_bounded_and_cross_field_consistent():
    controls, errors = validate_controls(
        {"max_candidates": 2, "max_published": 3, "cash_budget": 100, "max_realized_loss": 200}
    )
    assert controls["max_candidates"] == 2
    assert "max_published cannot exceed max_candidates" in errors
    assert "max_realized_loss cannot exceed cash_budget" in errors
    _, errors = validate_controls({"monitor_days": 91, "max_after_sales_rate": 0})
    assert "monitor_days must be between 1 and 90" in errors
    assert "max_after_sales_rate must be a finite number in (0, 1]" in errors


def test_store_is_atomic_persistent_and_one_active_trial_per_account():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "commercial_trials.json"
        store = CommercialTrialStore(path)
        created = store.create_trial(cookie_id="seller_a", category="商用厨具", candidate_ids=["rc_1"])
        assert path.exists()
        assert not path.with_suffix(".json.tmp").exists()
        try:
            store.create_trial(cookie_id="seller_a", category="商用厨具")
        except ValueError as exc:
            assert "active or paused" in str(exc)
        else:
            raise AssertionError("second active trial should be rejected")

        paused = store.decide(created["trial_id"], "pause", "operator pause", review_eligible=False)
        assert paused["lifecycle"] == "paused"
        assert paused["decision_history"][-1]["review_eligible"] is False
        resumed = store.decide(created["trial_id"], "continue", review_eligible=False)
        assert resumed["lifecycle"] == "active"

        reloaded = CommercialTrialStore(path)
        assert reloaded.get_trial(created["trial_id"])["cookie_id"] == "seller_a"
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert "buyer_id" not in json.dumps(persisted, ensure_ascii=False)


def test_end_to_end_chain_uses_targets_iso_profit_time_and_procurement_cash():
    trial = _trial(
        fulfillment_handoffs=[_handoff()],
        decision_history=[
            {
                "decision": "complete",
                "note": "first order reviewed",
                "review_eligible": True,
                "decided_at": START + 55,
            }
        ]
    )
    result = _reconcile(trial=trial)

    assert tuple(stage["code"] for stage in result["stages"]) == STAGE_CODES
    assert all(stage["status"] == "pass" for stage in result["stages"])
    assert result["completed_prefix_count"] == len(STAGE_CODES)
    assert result["inquiry_count"] == 2
    assert result["inquiry_response_count"] == 2
    assert result["paid_order_count"] == 1
    assert result["exposure"] == 520
    assert result["committed_cash_requirement"] == 520
    assert result["realized_profit"] == 80
    assert result["review_eligible"] is True
    rendered = json.dumps(result, ensure_ascii=False)
    assert "must-not-leak" not in rendered
    assert "buyer_id" not in rendered


def test_wrong_account_duplicate_pre_publish_and_stub_evidence_do_not_advance():
    result = _reconcile(
        jobs=[_job(external_id="STUB_demo")],
        intake_records=[
            _intake("wrong-account", cookie_id="seller_b"),
            _intake("duplicate", status="duplicate"),
            _intake("historical", created_at=START + 1),
        ],
        runtime_orders=[
            _runtime_order(cookie_id="seller_b"),
            _runtime_order(created_at=START + 1),
        ],
        resale_orders=[_resale_order()],
    )
    assert _stage(result, "approval_created")["status"] == "pass"
    assert _stage(result, "publish_confirmed")["status"] == "pending"
    assert _stage(result, "inquiry_observed")["status"] == "pending"
    assert _stage(result, "paid_order")["status"] == "pending"
    assert _stage(result, "profit_reconciled")["status"] == "pending"

    synthetic = _runtime_order(status="paid")
    synthetic["status_history"] = []
    synthetic["source"] = "fake_sync_sync"
    synthetic_result = _reconcile(runtime_orders=[synthetic], resale_orders=[])
    assert _stage(synthetic_result, "paid_order")["status"] == "pending"
    assert synthetic_result["paid_order_count"] == 0


def test_targets_prevent_one_inquiry_from_claiming_success():
    result = _reconcile(intake_records=[_intake("only-one")])
    assert _stage(result, "inquiry_observed")["status"] == "pending"
    assert _stage(result, "inquiry_observed")["counts"] == {"inquiries": 1, "target": 2}
    assert result["completed_prefix_count"] == 5


def test_inquiry_requires_a_successful_send_job_linked_to_the_exact_intake_approval():
    intake = _intake("intake_1")
    wrong_approval = _reply_job("other")
    failed = _reply_job("intake_1", ok=False)
    result = _reconcile(
        jobs=[_job(), wrong_approval, failed],
        intake_records=[intake, _intake("intake_2", created_at=START + 31)],
        runtime_orders=[],
        resale_orders=[],
    )

    assert _stage(result, "inquiry_observed")["status"] == "pass"
    assert _stage(result, "inquiry_responded")["status"] == "pending"
    assert result["inquiry_response_count"] == 0
    assert result["next_action"]["action"] == "approve_and_send_inquiry_reply"


def test_profit_requires_strong_order_linkage_and_financial_evidence():
    unlinked = _resale_order()
    unlinked["commercial_trial_id"] = "ct_other"
    unlinked_result = _reconcile(resale_orders=[unlinked])
    assert _stage(unlinked_result, "profit_reconciled")["status"] == "pending"
    assert "linked_resale_order_missing" in unlinked_result["profit_reconciliation_gaps"][0]["reasons"]

    missing_finance = _resale_order()
    missing_finance["financial_evidence_note"] = ""
    missing_finance["financial_evidence_ref"] = ""
    missing_finance["financial_reconciled_at"] = ""
    missing_result = _reconcile(resale_orders=[missing_finance])
    assert _stage(missing_result, "fulfillment_observed")["status"] == "pass"
    assert _stage(missing_result, "profit_reconciled")["status"] == "pending"
    reasons = missing_result["profit_reconciliation_gaps"][0]["reasons"]
    assert "financial_evidence_note_missing" in reasons
    assert "financial_evidence_ref_missing" in reasons


def test_publish_approval_reserves_capacity_before_execution():
    result = _reconcile(jobs=[], intake_records=[], runtime_orders=[], resale_orders=[])
    assert result["publish_commitment_count"] == 1
    assert result["published_count"] == 0
    assert result["batch_draft_blocked"] is True
    assert result["batch_draft_blocked_reason"] == "max_publish_commitments_reached"


def test_after_sales_and_over_budget_commitment_require_stop():
    expensive = _candidate(supplier_price=1_100, supplier_shipping=20)
    result = _reconcile(
        candidates=[expensive],
        runtime_orders=[_runtime_order(status="refund_requested")],
        resale_orders=[_resale_order(actual_profit=-100)],
    )
    assert result["stop_required"] is True
    assert "committed_cash_exceeds_budget" in result["stop_reasons"]
    assert "after_sales_rate_exceeds_threshold" in result["stop_reasons"]
    assert "realized_loss_exceeds_max" in result["stop_reasons"]


def test_operational_pause_does_not_complete_review_stage_later():
    trial = _trial(
        fulfillment_handoffs=[_handoff()],
        decision_history=[
            {
                "decision": "pause",
                "note": "waiting for supplier",
                "review_eligible": False,
                "decided_at": START + 15,
            }
        ]
    )
    result = _reconcile(trial=trial)
    assert result["review_eligible"] is True  # Profit is reconciled.
    assert _stage(result, "review_decision")["status"] == "pending"


def test_fulfillment_and_profit_must_reach_paid_order_target():
    controls = _trial()["controls"]
    controls["target_paid_orders"] = 2
    trial = _trial(controls=controls)
    second_paid = _runtime_order(order_id="xy_order_2", created_at=START + 41, status="paid")
    second_paid["status_history"] = []
    result = _reconcile(
        trial=trial,
        runtime_orders=[_runtime_order(), second_paid],
        resale_orders=[_resale_order()],
    )
    assert _stage(result, "paid_order")["status"] == "pass"
    assert _stage(result, "fulfillment_observed")["status"] == "pending"
    assert _stage(result, "profit_reconciled")["status"] == "pending"


def test_rejected_approval_does_not_reserve_capacity_or_trigger_account_stop():
    not_ready = _ready_account()
    not_ready["runtime_fresh"] = False
    result = _reconcile(
        account=not_ready,
        work_orders=[_work_order(status="rejected")],
        jobs=[],
        intake_records=[],
        runtime_orders=[],
        resale_orders=[],
    )
    assert result["approval_count"] == 1
    assert result["publish_commitment_count"] == 0
    assert "account_lost_readiness_after_commitment" not in result["stop_reasons"]


def test_paid_order_requires_handoff_and_handoff_commitment_is_audited():
    paid = _runtime_order(status="paid")
    paid["status_history"] = []
    without_handoff = _reconcile(
        trial=_trial(fulfillment_handoffs=[]),
        runtime_orders=[paid],
        resale_orders=[],
    )
    assert without_handoff["next_action"]["action"] == "prepare_paid_order_fulfillment"
    assert without_handoff["order_links"][0]["handoff"] is None
    assert "buyer_id" not in json.dumps(without_handoff, ensure_ascii=False)

    handoff = _handoff()
    with_handoff = _reconcile(
        trial=_trial(fulfillment_handoffs=[handoff]),
        runtime_orders=[paid],
        resale_orders=[],
    )
    assert with_handoff["procurement_commitment"] == 520
    assert with_handoff["order_links"][0]["handoff"]["approval_id"] == "approval_purchase_1"
    assert with_handoff["next_action"]["action"] == "complete_fulfillment_evidence"

    rejected_purchase = _purchase_work_order(status="rejected")
    rejected = _reconcile(
        trial=_trial(fulfillment_handoffs=[handoff]),
        work_orders=[_work_order(), rejected_purchase],
        runtime_orders=[paid],
        resale_orders=[],
    )
    assert rejected["procurement_commitment"] == 0
    assert rejected["order_links"][0]["handoff"]["approval_status"] == "rejected"
    assert rejected["next_action"]["action"] == "resolve_rejected_fulfillment_handoff"


class CommercialTrialTests(unittest.TestCase):
    def test_control_validation(self):
        test_control_validation_is_bounded_and_cross_field_consistent()

    def test_store(self):
        test_store_is_atomic_persistent_and_one_active_trial_per_account()

    def test_end_to_end_chain(self):
        test_end_to_end_chain_uses_targets_iso_profit_time_and_procurement_cash()

    def test_unrelated_evidence(self):
        test_wrong_account_duplicate_pre_publish_and_stub_evidence_do_not_advance()

    def test_targets(self):
        test_targets_prevent_one_inquiry_from_claiming_success()

    def test_inquiry_reply_evidence(self):
        test_inquiry_requires_a_successful_send_job_linked_to_the_exact_intake_approval()

    def test_profit_evidence(self):
        test_profit_requires_strong_order_linkage_and_financial_evidence()

    def test_publish_commitment(self):
        test_publish_approval_reserves_capacity_before_execution()

    def test_stop_conditions(self):
        test_after_sales_and_over_budget_commitment_require_stop()

    def test_operational_pause(self):
        test_operational_pause_does_not_complete_review_stage_later()

    def test_target_fulfillment_and_profit(self):
        test_fulfillment_and_profit_must_reach_paid_order_target()

    def test_rejected_approval(self):
        test_rejected_approval_does_not_reserve_capacity_or_trigger_account_stop()

    def test_paid_order_handoff(self):
        test_paid_order_requires_handoff_and_handoff_commitment_is_audited()


class CommercialOrderTransitionApiTests(unittest.TestCase):
    def test_commercial_order_requires_shipment_then_delivery_and_financial_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            resale = ResaleManager(root / "resale")
            trial_store = CommercialTrialStore(root / "trials.json")
            runtime = OrderRuntimeStore(root / "runtime.json")
            trial = trial_store.create_trial(
                cookie_id="seller_a",
                category="商用厨具",
                candidate_ids=["rc_1"],
            )
            trial_id = trial["trial_id"]
            trial_store.record_fulfillment_handoff(
                trial_id,
                {
                    "order_id": "xy_order_1",
                    "item_id": "item_1",
                    "candidate_id": "rc_1",
                    "plan_id": "rp_1",
                    "purchase_draft_id": "pd_1",
                    "work_order_id": "wo_purchase_1",
                    "approval_id": "approval_purchase_1",
                    "lock_owner": f"commercial_trial:{trial_id}",
                    "quantity": 1,
                    "total_cost": 520,
                    "created_at": time.time(),
                },
            )
            task = resale.create_purchase_task({
                "draft_id": "pd_1",
                "plan_id": "rp_1",
                "candidate_id": "rc_1",
                "product_specs": {"size": "1200x600"},
                "quantity": 1,
                "buyer_address": "上海浦东新区***",
                "commercial_trial_id": trial_id,
                "xianyu_order_id": "xy_order_1",
                "cookie_id": "seller_a",
                "item_id": "item_1",
                "sale_amount": 620,
                "fulfillment_lock_owner": f"commercial_trial:{trial_id}",
                "total_cost": 520,
                "profit_review": {"expected_profit": 80},
            })
            resale.update_purchase_task(task["purchase_task_id"], {
                "status": "ordered",
                "supplier_order_id": "supplier_order_1",
                "tracking_number": "supplier_tracking_1",
                "ordered_at": _iso(START + 45),
            })
            order = resale.create_order_from_task(
                task["purchase_task_id"],
                overrides={"sale_amount": 620, "shipping_cost": 20, "platform_fee": 20},
            )
            runtime.upsert_order(
                order_id="xy_order_1",
                cookie_id="seller_a",
                item_id="item_1",
                status="shipped",
                amount=620,
                source="browser",
            )

            with patch.object(server, "resale_manager", resale), patch.object(
                server, "commercial_trial_store", trial_store
            ), patch.object(server, "order_runtime_store", runtime):
                client = TestClient(server.app)
                complete_payload = {
                    "status": "completed",
                    "supplier_order_id": "supplier_order_1",
                    "supplier_tracking_number": "supplier_tracking_1",
                    "xianyu_tracking_number": "xianyu_tracking_1",
                    "sale_amount": 620,
                    "supplier_cost": 500,
                    "shipping_cost": 20,
                    "platform_fee": 20,
                    "financial_evidence_note": "已按平台实收和供应商账单逐项核对",
                    "financial_evidence_ref": "screenshots/xy_order_1-ledger.png",
                }
                blocked = client.post(
                    f"/resale/orders/{order['resale_order_id']}/status",
                    json=complete_payload,
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertIn("order_must_be_shipped_before_completed", blocked.json()["detail"]["reasons"])

                shipped = client.post(
                    f"/resale/orders/{order['resale_order_id']}/status",
                    json={
                        "status": "shipped",
                        "supplier_order_id": "supplier_order_1",
                        "supplier_tracking_number": "supplier_tracking_1",
                        "xianyu_tracking_number": "xianyu_tracking_1",
                    },
                )
                self.assertEqual(shipped.status_code, 200)

                still_blocked = client.post(
                    f"/resale/orders/{order['resale_order_id']}/status",
                    json=complete_payload,
                )
                self.assertEqual(still_blocked.status_code, 409)
                self.assertIn("platform_delivery_not_confirmed", still_blocked.json()["detail"]["reasons"])

                runtime.update_status("xy_order_1", "delivered", context="platform sync")
                completed = client.post(
                    f"/resale/orders/{order['resale_order_id']}/status",
                    json=complete_payload,
                )
                self.assertEqual(completed.status_code, 200)
                completed_order = completed.json()["resale_order"]
                self.assertEqual(completed_order["actual_profit"], 80)
                self.assertTrue(completed_order["financial_reconciled_at"])
                self.assertTrue(completed_order["completed_at"])


class _FakeDB:
    def get_all_cookies(self):
        return {"seller_a": "secret-cookie-value"}

    def get_all_cookie_status(self):
        return {"seller_a": True}


class _FakeResaleManager:
    def __init__(self):
        self.candidates = {"rc_1": _candidate()}

    def get_candidate(self, candidate_id):
        return self.candidates.get(candidate_id)


class CommercialTrialApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = CommercialTrialStore(Path(self.temp.name) / "trials.json")
        self.client = TestClient(server.app)
        self.reconcile = lambda trial: {
            **trial,
            "stages": [],
            "controls": trial["controls"],
            "stop_required": False,
            "stop_reasons": [],
            "review_eligible": False,
            "monitor_elapsed": False,
            "publish_commitment_count": 0,
            "committed_candidate_ids": [],
            "published_count": 0,
            "remaining_cash_budget": 1000,
        }
        self.patches = [
            patch.object(server, "commercial_trial_store", self.store),
            patch.object(server, "db_manager", _FakeDB()),
            patch.object(server, "resale_manager", _FakeResaleManager()),
            patch.object(server, "_reconcile_commercial_trial", side_effect=self._reconcile_dispatch),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def _reconcile_dispatch(self, trial):
        return self.reconcile(trial)

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def _create(self):
        trial = self.store.create_trial(
            cookie_id="seller_a",
            category="商用厨具",
            candidate_ids=["rc_1"],
            controls={"target_candidates": 1},
        )
        return self._reconcile_dispatch(trial)

    def test_create_list_active_and_get_routes(self):
        created = self._create()
        trial_id = created["trial_id"]
        active = self.client.get("/commercial-trials/active", params={"cookie_id": "seller_a"})
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.json()["trial"]["trial_id"], trial_id)
        self.assertEqual(active.json()["count"], 1)
        self.assertEqual(self.client.get(f"/commercial-trials/{trial_id}").status_code, 200)
        self.assertEqual(self.client.get("/commercial-trials").json()["count"], 1)

    def test_second_active_batch_for_same_account_is_rejected(self):
        self._create()
        response = self.client.post(
            "/commercial-trials",
            json={"cookie_id": "seller_a", "category": "商用厨具"},
        )
        self.assertEqual(response.status_code, 409)

    def test_publish_commitment_blocks_before_listing_side_effect(self):
        created = self._create()
        strict_side_effect = Mock(return_value={"ok": True})
        self.reconcile = lambda trial: {
            **trial,
            "controls": trial["controls"],
            "stop_required": False,
            "publish_commitment_count": 1,
            "committed_candidate_ids": [],
            "remaining_cash_budget": 1000,
        }
        with patch.object(server, "_strict_listing_draft_side_effects", strict_side_effect):
            response = self.client.post(
                f"/commercial-trials/{created['trial_id']}/candidates/rc_1/listing-draft"
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "max_publish_commitments_reached")
        strict_side_effect.assert_not_called()

    def test_insufficient_procurement_budget_blocks_before_side_effect(self):
        created = self._create()
        strict_side_effect = Mock(return_value={"ok": True})
        self.reconcile = lambda trial: {
            **trial,
            "controls": trial["controls"],
            "stop_required": False,
            "publish_commitment_count": 0,
            "committed_candidate_ids": [],
            "remaining_cash_budget": 100,
        }
        with patch.object(server, "_strict_listing_draft_side_effects", strict_side_effect):
            response = self.client.post(
                f"/commercial-trials/{created['trial_id']}/candidates/rc_1/listing-draft"
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "insufficient_cash_budget")
        strict_side_effect.assert_not_called()

    def test_complete_is_rejected_before_monitor_or_profit(self):
        created = self._create()
        response = self.client.post(
            f"/commercial-trials/{created['trial_id']}/decision",
            json={"decision": "complete", "note": "too early"},
        )
        self.assertEqual(response.status_code, 409)


class CommercialTrialFulfillmentHandoffApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.trial_store = CommercialTrialStore(root / "trials.json")
        self.resale = ResaleManager(root / "resale")
        self.work_orders = WorkOrderManager(root / "work_orders.json")
        self.runtime_orders = OrderRuntimeStore(root / "runtime_orders.json")
        candidate = self.resale.create_candidate({
            "title": "商用不锈钢操作台",
            "source_platform": "pdd",
            "source_url": "https://supplier.example/item/1",
            "supplier_price": 500,
            "supplier_shipping": 20,
            "target_sale_price": 899,
            "category": "商用厨具",
            "specifications": {"size": "120x60x80cm"},
        })
        self.candidate_id = candidate["candidate_id"]
        self.resale.verify_candidate(self.candidate_id, {"supplier_verified": True})
        self.resale.set_decision(self.candidate_id, "approve")
        self.trial = self.trial_store.create_trial(
            cookie_id="seller_a",
            category="商用厨具",
            candidate_ids=[self.candidate_id],
            controls={"target_candidates": 1},
        )
        self.runtime_orders.upsert_order(
            order_id="xy_paid_1",
            cookie_id="seller_a",
            item_id="item_live_1",
            buyer_id="must-not-leak",
            amount=899,
            status="paid",
            source="browser_sync",
        )
        self.client = TestClient(server.app)
        self.patches = [
            patch.object(server, "commercial_trial_store", self.trial_store),
            patch.object(server, "resale_manager", self.resale),
            patch.object(server, "work_order_manager", self.work_orders),
            patch.object(server, "order_runtime_store", self.runtime_orders),
            patch.object(server, "_reconcile_commercial_trial", side_effect=self._reconcile),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def _reconcile(self, trial):
        handoffs = list(trial.get("fulfillment_handoffs") or [])
        runtime_order = self.runtime_orders.get_order("xy_paid_1")
        lock = runtime_order.get("fulfillment_lock") or {}
        handoff = next((item for item in handoffs if item.get("order_id") == "xy_paid_1"), None)
        return {
            **trial,
            "controls": trial["controls"],
            "stop_required": False,
            "stop_reasons": [],
            "exposure": 520,
            "order_links": [{
                "order_id": "xy_paid_1",
                "item_id": "item_live_1",
                "candidate_id": self.candidate_id,
                "status": runtime_order["status"],
                "amount": 899,
                "locked": bool(lock.get("locked")),
                "lock_owner": str(lock.get("owner") or ""),
                "handoff": handoff,
            }],
            "fulfillment_handoffs": handoffs,
        }

    @staticmethod
    def _payload(**overrides):
        payload = {
            "quantity": 1,
            "buyer_address_summary": "上海浦东新区***",
            "specs_confirmed": True,
            "address_confirmed": True,
            "supplier_stock_reconfirmed": True,
            "supplier_quote_reconfirmed": True,
            "operator_note": "paid order verified against platform",
        }
        payload.update(overrides)
        return payload

    def _handoff(self, **overrides):
        return self.client.post(
            f"/commercial-trials/{self.trial['trial_id']}/orders/xy_paid_1/fulfillment-handoff",
            json=self._payload(**overrides),
        )

    def test_handoff_is_locked_approval_gated_idempotent_and_propagates_order_linkage(self):
        first = self._handoff()
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertFalse(body["idempotent"])
        self.assertTrue(body["runtime_order"]["locked"])
        self.assertNotIn("buyer_id", json.dumps(body, ensure_ascii=False))
        self.assertEqual(len(self.work_orders.list_work_orders()), 1)
        approval = body["work_order"]["approvals"][0]
        self.assertEqual(approval["action"], "prepare_purchase_draft")
        self.assertEqual(approval["status"], "pending")

        second = self._handoff()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["idempotent"])
        self.assertEqual(len(self.work_orders.list_work_orders()), 1)
        self.assertEqual(len(self.resale._plans), 1)

        draft = body["purchase_draft"]
        self.assertEqual(draft["commercial_trial_id"], self.trial["trial_id"])
        self.assertEqual(draft["xianyu_order_id"], "xy_paid_1")
        self.assertEqual(draft["sale_amount"], 899)
        task = self.resale.create_purchase_task(draft)
        order = self.resale.create_order_from_task(task["purchase_task_id"])
        self.assertEqual(order["commercial_trial_id"], self.trial["trial_id"])
        self.assertEqual(order["xianyu_order_id"], "xy_paid_1")
        self.assertEqual(order["sale_amount"], 899)

        persisted_trial = (Path(self.temp.name) / "trials.json").read_text(encoding="utf-8")
        self.assertNotIn("must-not-leak", persisted_trial)
        self.assertNotIn("上海浦东", persisted_trial)

    def test_missing_confirmation_blocks_before_lock_or_purchase_side_effects(self):
        response = self._handoff(specs_confirmed=False)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "required_confirmations_missing")
        self.assertFalse(self.runtime_orders.get_order("xy_paid_1")["fulfillment_lock"]["locked"])
        self.assertEqual(self.work_orders.list_work_orders(), [])
        self.assertEqual(len(self.resale._plans), 0)

    def test_projected_budget_blocks_before_lock_or_purchase_side_effects(self):
        self.trial["controls"]["cash_budget"] = 500
        self.trial_store._trials[self.trial["trial_id"]]["controls"]["cash_budget"] = 500
        response = self._handoff()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "procurement_budget_exceeded")
        self.assertFalse(self.runtime_orders.get_order("xy_paid_1")["fulfillment_lock"]["locked"])
        self.assertEqual(self.work_orders.list_work_orders(), [])

    def test_release_requires_shipment_evidence_and_marks_handoff_released(self):
        created = self._handoff()
        self.assertEqual(created.status_code, 200, created.text)
        path = f"/commercial-trials/{self.trial['trial_id']}/orders/xy_paid_1/fulfillment-release"
        early = self.client.post(path)
        self.assertEqual(early.status_code, 409)
        self.runtime_orders.update_status("xy_paid_1", "shipped", operator_action="platform_shipment_confirmed")
        released = self.client.post(path)
        self.assertEqual(released.status_code, 200, released.text)
        self.assertFalse(released.json()["runtime_order"]["locked"])
        self.assertGreater(released.json()["handoff"]["released_at"], 0)

    def test_rejected_purchase_approval_can_release_paid_order_lock(self):
        created = self._handoff()
        self.assertEqual(created.status_code, 200, created.text)
        approval_id = created.json()["handoff"]["approval_id"]
        self.work_orders.decide_approval(approval_id, "rejected", "supplier terms changed")
        path = f"/commercial-trials/{self.trial['trial_id']}/orders/xy_paid_1/fulfillment-release"
        released = self.client.post(path)
        self.assertEqual(released.status_code, 200, released.text)
        self.assertFalse(released.json()["runtime_order"]["locked"])


if __name__ == "__main__":
    unittest.main()
