from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent import server
from xianyu_agent.browser_adapter import BrowserAdapter
from xianyu_agent.commercial_trial import (
    CommercialTrialStore,
    _build_item_performance,
    _traffic_next_action,
    default_controls,
)
from xianyu_agent.db_manager import DBManager
from xianyu_agent.execution_queue import ExecutionJobQueue
from xianyu_agent.sync_persistence import sync_account_snapshot
from xianyu_agent.work_orders import WorkOrderManager


def test_browser_listing_metrics_distinguish_missing_fields_from_real_zero():
    adapter = BrowserAdapter.__new__(BrowserAdapter)
    available = adapter._parse_my_item({
        "itemId": "item_1",
        "title": "工作台",
        "data": {"browseCount": "12", "wantCount": 3},
    })
    assert available["metrics_available"] is True
    assert available["browse_count"] == 12
    assert available["want_count"] == 3

    missing = adapter._parse_my_item({"itemId": "item_2", "title": "水槽"})
    assert missing["metrics_available"] is False
    assert missing["browse_count"] == 0
    assert missing["want_count"] == 0


class _MetricsAdapter:
    name = "browser"

    def sync_orders(self, cookie_id="default", status=""):
        return {"ok": True, "adapter": self.name, "orders": []}

    def sync_my_listings(self, cookie_id="default"):
        return {
            "ok": True,
            "adapter": self.name,
            "items": [
                {
                    "product_id": "item_metric",
                    "title": "商用工作台",
                    "price": "899",
                    "status": "active",
                    "metrics_available": True,
                    "browse_count": 21,
                    "want_count": 2,
                    "raw": {"must_not_persist": "secret"},
                },
                {
                    "product_id": "item_without_metric",
                    "title": "商用水槽",
                    "price": "599",
                    "status": "active",
                    "metrics_available": False,
                },
            ],
        }


def test_sync_persists_metric_availability_counts_and_observation_time_only():
    with TemporaryDirectory() as tmp:
        db = DBManager(str(Path(tmp) / "metrics.db"))
        try:
            db.save_cookie("seller_a", "secret-cookie")
            result = sync_account_snapshot(
                db=db,
                adapter=_MetricsAdapter(),
                cookie_id="seller_a",
            )
            assert result["ok"] is True
            rows = {item["item_id"]: item for item in db.get_items_by_cookie("seller_a")}
            metric = rows["item_metric"]
            assert metric["metrics_available"] == 1
            assert metric["browse_count"] == 21
            assert metric["want_count"] == 2
            assert metric["metrics_observed_at"] > 0
            missing = rows["item_without_metric"]
            assert missing["metrics_available"] == 0
            assert missing["metrics_observed_at"] == 0
            db_text = Path(tmp, "metrics.db").read_bytes()
            assert b"must_not_persist" not in db_text
        finally:
            db.close()


def test_observation_store_is_monotonic_daily_and_experiment_keeps_baseline():
    with TemporaryDirectory() as tmp:
        store = CommercialTrialStore(Path(tmp) / "trials.json")
        trial = store.create_trial(
            cookie_id="seller_a",
            category="商用厨具",
            candidate_ids=["rc_1"],
        )
        trial_id = trial["trial_id"]
        baseline = store.record_traffic_observation(
            trial_id,
            item_id="item_1",
            candidate_id="rc_1",
            views=10,
            wants=1,
            source="operator",
            evidence_note="商品页首次截图",
        )
        updated = store.record_traffic_observation(
            trial_id,
            item_id="item_1",
            candidate_id="rc_1",
            views=12,
            wants=2,
            source="operator",
            evidence_note="商品页午间截图",
        )
        assert updated["observation_id"] == baseline["observation_id"]

        try:
            store.record_traffic_observation(
                trial_id,
                item_id="item_1",
                candidate_id="rc_1",
                views=11,
                wants=2,
                source="operator",
                evidence_note="错误的倒退数据",
            )
        except ValueError as exc:
            assert "cannot decrease" in str(exc)
        else:
            raise AssertionError("cumulative traffic counts must not decrease")

        experiment = store.start_experiment(
            trial_id,
            item_id="item_1",
            candidate_id="rc_1",
            variable="title",
            hypothesis="增加规格词可以提升有效浏览",
            before_value="商用工作台",
            after_value="不锈钢商用工作台 1200x600",
            planned_days=3,
            baseline_inquiries=0,
            baseline_paid_orders=0,
        )
        result_snapshot = store.record_traffic_observation(
            trial_id,
            item_id="item_1",
            candidate_id="rc_1",
            views=30,
            wants=4,
            source="operator",
            evidence_note="改标题后的商品页截图",
            observed_at=time.time() + 1,
        )
        assert result_snapshot["observation_id"] != experiment["baseline_observation_id"]

        decided = store.decide_experiment(
            trial_id,
            experiment["experiment_id"],
            decision="keep_change",
            note="提前结束，浏览和想要均明显增加",
            current_inquiries=1,
            current_paid_orders=0,
            allow_early=True,
        )
        assert decided["status"] == "decided"
        assert decided["result"] == {
            "views_delta": 18,
            "wants_delta": 2,
            "inquiries_delta": 1,
            "paid_orders_delta": 0,
        }


def test_item_performance_uses_observation_window_and_one_prioritized_action():
    controls = default_controls()
    published_at = 1_800_000_000.0
    observations = [{
        "observation_id": "to_1",
        "item_id": "item_1",
        "candidate_id": "rc_1",
        "source": "operator",
        "local_date": "2027-01-15",
        "views": 30,
        "wants": 2,
        "evidence_note": "商品页截图",
        "evidence_ref": "shot.png",
        "observed_at": published_at + 60,
        "first_observed_at": published_at + 60,
        "recorded_at": published_at + 60,
        "updated_at": published_at + 60,
    }]
    performance = _build_item_performance(
        publishes=[{"external_id": "item_1", "candidate_id": "rc_1", "finished_at": published_at}],
        observations=observations,
        experiments=[],
        inquiries=[],
        paid_orders=[],
        controls=controls,
        now=published_at + 25 * 3600,
    )
    assert performance[0]["recommendation"]["code"] == "views_no_inquiry"
    assert performance[0]["want_rate"] == round(2 / 30, 4)
    action = _traffic_next_action(performance)
    assert action["action"] == "start_conversion_experiment"
    assert action["detail"] == "item_id=item_1"


def test_commercial_observation_and_experiment_api_preserve_real_item_linkage():
    with TemporaryDirectory() as tmp:
        store = CommercialTrialStore(Path(tmp) / "trials.json")
        manager = WorkOrderManager(Path(tmp) / "work_orders.json")
        trial = store.create_trial(
            cookie_id="seller_a",
            category="商用厨具",
            candidate_ids=["rc_1"],
        )
        trial_id = trial["trial_id"]

        def fake_reconcile(raw_trial):
            current = store.get_trial(trial_id) or dict(raw_trial)
            approvals = {
                approval["approval_id"]: approval
                for work_order in manager.list_work_orders(limit=100)
                for approval in work_order.get("approvals") or []
            }
            experiments = []
            for raw_experiment in current.get("experiments") or []:
                experiment = dict(raw_experiment)
                approval = approvals.get(experiment.get("approval_id")) or {}
                execution_result = approval.get("execution_result") or {}
                experiment.update({
                    "approval_status": approval.get("status") or "missing",
                    "approval_execution_status": approval.get("execution_status") or "",
                    "approval_execution_ok": bool(execution_result.get("ok")),
                    "job_status": "",
                    "job_result_ok": False,
                    "manual_required": experiment.get("variable") not in {"title", "price"},
                })
                experiments.append(experiment)
            return {
                **current,
                "published_items": [{
                    "item_id": "item_1",
                    "candidate_id": "rc_1",
                    "published_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60)),
                }],
                "experiments": experiments,
                "item_performance": [{
                    "item_id": "item_1",
                    "candidate_id": "rc_1",
                    "inquiries": 1,
                    "paid_orders": 0,
                }],
            }

        class FakeSyncDB:
            def get_account_sync_state(self, cookie_id):
                return {
                    "listings": {
                        "fresh": True,
                        "last_synced_epoch": time.time() + 1,
                    }
                }

            def get_items_by_cookie(self, cookie_id):
                return [{"item_id": "item_1", "title": "不锈钢商用工作台 1200x600", "price": 899.0}]

        with patch.object(server, "commercial_trial_store", store), patch.object(
            server, "_reconcile_commercial_trial", side_effect=fake_reconcile
        ), patch.object(server, "work_order_manager", manager), patch.object(
            server, "db_manager", FakeSyncDB()
        ):
            client = TestClient(server.app)
            observed = client.post(
                f"/commercial-trials/{trial_id}/items/item_1/traffic-observations",
                json={"views": 10, "wants": 1, "evidence_note": "商品页真实截图"},
            )
            assert observed.status_code == 200
            assert observed.json()["observation"]["source"] == "operator"

            started = client.post(
                f"/commercial-trials/{trial_id}/items/item_1/experiments",
                json={
                    "variable": "title",
                    "hypothesis": "增加规格词可以提升有效浏览",
                    "before_value": "商用工作台",
                    "after_value": "不锈钢商用工作台 1200x600",
                    "planned_days": 3,
                },
            )
            assert started.status_code == 200
            started_payload = started.json()
            experiment_id = started_payload["experiment"]["experiment_id"]
            assert started_payload["experiment"]["status"] == "proposed"
            assert started_payload["experiment"]["started_at"] == 0
            approval_id = started_payload["experiment"]["approval_id"]
            manager.decide_approval(approval_id, "approved", "同意本次单变量标题测试")

            activated = client.post(
                f"/commercial-trials/{trial_id}/experiments/{experiment_id}/activate",
                json={"manual_confirmation": True},
            )
            assert activated.status_code == 200
            activated_experiment = activated.json()["experiment"]
            assert activated_experiment["status"] == "active"
            assert activated_experiment["started_at"] >= activated_experiment["proposed_at"]
            approved = manager.get_work_order(started_payload["experiment"]["work_order_id"])
            approval = next(item for item in approved["approvals"] if item["approval_id"] == approval_id)
            assert approval["execution_result"]["manual_confirmation"] is True

            store.record_traffic_observation(
                trial_id,
                item_id="item_1",
                candidate_id="rc_1",
                views=18,
                wants=2,
                source="operator",
                evidence_note="更换标题后的商品页截图",
                observed_at=time.time() + 1,
            )
            decided = client.post(
                f"/commercial-trials/{trial_id}/experiments/{experiment_id}/decision",
                json={
                    "decision": "keep_change",
                    "note": "提前结束，咨询和想要均有增加",
                    "allow_early": True,
                },
            )
            assert decided.status_code == 200
            assert decided.json()["experiment"]["decision"] == "keep_change"


def test_cancelling_a_proposal_rejects_approval_and_cancels_queued_edit():
    with TemporaryDirectory() as tmp:
        store = CommercialTrialStore(Path(tmp) / "trials.json")
        manager = WorkOrderManager(Path(tmp) / "work_orders.json")
        queue = ExecutionJobQueue(Path(tmp) / "execution_jobs.json")
        trial = store.create_trial(cookie_id="seller_a", category="商用厨具", candidate_ids=["rc_1"])
        trial_id = trial["trial_id"]
        store.record_traffic_observation(
            trial_id,
            item_id="item_1",
            candidate_id="rc_1",
            views=10,
            wants=1,
            source="operator",
            evidence_note="商品页真实截图",
        )

        def fake_reconcile(raw_trial):
            current = store.get_trial(trial_id) or dict(raw_trial)
            approvals = {
                approval["approval_id"]: approval
                for work_order in manager.list_work_orders(limit=100)
                for approval in work_order.get("approvals") or []
            }
            experiments = []
            for raw_experiment in current.get("experiments") or []:
                experiment = dict(raw_experiment)
                approval = approvals.get(experiment.get("approval_id")) or {}
                experiment.update({
                    "approval_status": approval.get("status") or "missing",
                    "approval_execution_status": approval.get("execution_status") or "",
                    "approval_execution_ok": bool((approval.get("execution_result") or {}).get("ok")),
                })
                experiments.append(experiment)
            return {
                **current,
                "published_items": [{
                    "item_id": "item_1",
                    "candidate_id": "rc_1",
                    "published_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60)),
                }],
                "experiments": experiments,
                "item_performance": [{
                    "item_id": "item_1",
                    "candidate_id": "rc_1",
                    "inquiries": 0,
                    "paid_orders": 0,
                }],
                "stop_required": False,
                "lifecycle": current.get("lifecycle") or "active",
            }

        with patch.object(server, "commercial_trial_store", store), patch.object(
            server, "work_order_manager", manager
        ), patch.object(server, "execution_queue", queue), patch.object(
            server, "_reconcile_commercial_trial", side_effect=fake_reconcile
        ):
            client = TestClient(server.app)
            proposed = client.post(
                f"/commercial-trials/{trial_id}/items/item_1/experiments",
                json={
                    "variable": "title",
                    "hypothesis": "增加规格词提升浏览",
                    "before_value": "商用工作台",
                    "after_value": "不锈钢商用工作台 1200x600",
                    "planned_days": 3,
                },
            ).json()["experiment"]
            manager.decide_approval(proposed["approval_id"], "approved", "同意标题测试")
            job = queue.create_job(proposed["approval_id"], manager)

            cancelled = client.post(
                f"/commercial-trials/{trial_id}/experiments/{proposed['experiment_id']}/cancel",
                json={"note": "本次不再测试"},
            )

            assert cancelled.status_code == 200
            assert cancelled.json()["experiment"]["status"] == "cancelled"
            assert cancelled.json()["cancelled_jobs"] == 1
            assert queue.get_job(job["job_id"])["status"] == "cancelled"
            work_order = manager.get_work_order(proposed["work_order_id"])
            approval = next(item for item in work_order["approvals"] if item["approval_id"] == proposed["approval_id"])
            assert approval["status"] == "rejected"


class CommercialObservationTests(unittest.TestCase):
    def test_browser_metric_availability(self):
        test_browser_listing_metrics_distinguish_missing_fields_from_real_zero()

    def test_sync_metric_persistence(self):
        test_sync_persists_metric_availability_counts_and_observation_time_only()

    def test_observation_and_experiment_store(self):
        test_observation_store_is_monotonic_daily_and_experiment_keeps_baseline()

    def test_item_performance_action(self):
        test_item_performance_uses_observation_window_and_one_prioritized_action()

    def test_observation_experiment_api(self):
        test_commercial_observation_and_experiment_api_preserve_real_item_linkage()

    def test_cancel_proposal_cleans_execution(self):
        test_cancelling_a_proposal_rejects_approval_and_cancels_queued_edit()
