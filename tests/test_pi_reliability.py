"""Pi-chain reliability gaps: red-first regression tests (operator round 4).

Layer: MOCK (FakePi). Real Pi runs stay in scripts/run_pi_quote_review_real.py.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.budget_reservations import BudgetReservationStore, ShadowAuthorizationLog
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.operating_mandates import OperatingMandateStore
from xianyu_agent.pi_jobs import ModelUsageLedger, PiTaskRunner
from xianyu_agent.price_commitments import PriceCommitmentStore, ProductFactsStore
from xianyu_agent.reply_drafts import DraftReplyService
from xianyu_agent.shadow_observer import ShadowObserver

UTC = timezone.utc
VALID = '{"findings": [], "summary": "ok"}'


class FakePi:
    def __init__(self, message=VALID, usage=None, error=None, side_effect=None):
        self.message = message
        self.usage = usage or {"input": 10, "output": 5, "totalTokens": 15}
        self.error = error
        self.side_effect = side_effect
        self.stopped = False

    def prompt(self, message, context=None):
        if self.side_effect:
            self.side_effect()
        if self.error:
            raise self.error
        return {
            "ok": True,
            "result": {
                "message": self.message,
                "session_id": "s",
                "provider": "deepseek",
                "model": "deepseek-flash",
                "turns": 1,
                "tool_calls": [{"tool_call_id": "t1", "name": "xianyu_business_snapshot", "ok": True}],
                "usage": self.usage,
            },
        }

    def stop(self):
        self.stopped = True


def make_runner(tmp, **kwargs):
    root = Path(tmp)
    repos = JsonCommerceRepositories(root)
    runner = PiTaskRunner(AgentJobStore(root), repos, data_dir=root, **kwargs)
    runner.start()
    return runner, repos


class WorkerSerializationTests(unittest.TestCase):
    def test_second_run_once_returns_busy_while_first_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            park = threading.Event()
            release = threading.Event()
            first_pi = FakePi(side_effect=lambda: (park.set(), release.wait(timeout=10))[1])

            results = {}

            def first():
                results["first"] = runner.run_once(pi_factory=lambda env: first_pi)

            t = threading.Thread(target=first)
            t.start()
            self.assertTrue(park.wait(timeout=5))
            results["second"] = runner.run_once(pi_factory=lambda env: FakePi())
            release.set()
            t.join(timeout=10)

            self.assertEqual(results["first"]["outcome"], "succeeded")
            self.assertEqual(results["second"]["outcome"], "busy")

    def test_stop_wins_over_pending_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            park = threading.Event()
            release = threading.Event()
            started = threading.Event()

            def factory(env):
                started.set()
                park.set()
                release.wait(timeout=10)
                return FakePi()

            results = {}

            def first():
                results["first"] = runner.run_once(pi_factory=factory)

            t = threading.Thread(target=first)
            t.start()
            self.assertTrue(park.wait(timeout=5))
            runner.stop()
            results["after_stop"] = runner.run_once(pi_factory=lambda env: FakePi())
            release.set()
            t.join(timeout=10)

            self.assertEqual(results["after_stop"], None)  # no claim after stop
            self.assertEqual(results["first"]["outcome"], "succeeded")


class ClientCleanupTests(unittest.TestCase):
    def test_client_stopped_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            pi = FakePi()
            runner.run_once(pi_factory=lambda env: pi)
            self.assertTrue(pi.stopped)

    def test_client_stopped_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            pi = FakePi(error=RuntimeError("model down"))
            runner.run_once(pi_factory=lambda env: pi)
            self.assertTrue(pi.stopped)


class DeadlineAndBudgetTests(unittest.TestCase):
    def test_result_after_deadline_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp, deadline_seconds=60)
            job = runner.submit_quote_review_job()
            start = datetime.now(UTC)
            outcome = runner.run_once(
                pi_factory=lambda env: FakePi(),
                now=start,
                now_post=start + timedelta(seconds=120),
            )
            self.assertEqual(outcome["outcome"], "rejected_deadline_result")
            stored = AgentJobStore(Path(tmp)).get(job["record_id"])
            self.assertEqual(stored["status"], "failed")
            self.assertIn("after deadline", stored["error"])
            self.assertEqual(repos.decisions.list(), [])

    def test_per_job_token_budget_rejects_overspend(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp, per_job_token_budget=100)
            runner.submit_quote_review_job()
            outcome = runner.run_once(
                pi_factory=lambda env: FakePi(usage={"input": 900, "output": 100, "totalTokens": 1000})
            )
            self.assertEqual(outcome["outcome"], "budget_exceeded")
            self.assertEqual(repos.decisions.list(), [])

    def test_daily_token_budget_blocks_next_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp, daily_token_budget=10)
            runner.submit_quote_review_job()
            ok = runner.run_once(pi_factory=lambda env: FakePi())  # 15 tokens recorded
            self.assertEqual(ok["outcome"], "succeeded")
            runner.submit_quote_review_job()
            blocked = runner.run_once(pi_factory=lambda env: FakePi())
            self.assertEqual(blocked["outcome"], "budget_exhausted_day")
            # the blocked job requeues: each retry is rejected by the
            # budget check BEFORE any model call, until its deadline ends it
            blocked_job = [j for j in AgentJobStore(Path(tmp)).list() if j["record_id"] == blocked["job_id"]]
            self.assertEqual(blocked_job[0]["status"], "queued")
            self.assertIn("budget", blocked_job[0]["error"])
            self.assertEqual(repos.decisions.list().__len__(), 1)  # only the first job produced a decision


class DecisionCommitProtocolTests(unittest.TestCase):
    def test_cancel_after_heartbeat_invalidates_late_decision(self):
        """Cancel lands between the result heartbeat and the decision write:
        the fenced complete fails, the late decision gets an invalidation
        record, and the evidence endpoint stops showing it as valid."""
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            original_heartbeat = runner._jobs.heartbeat

            def cancel_right_after_heartbeat(job_id, **kwargs):
                result = original_heartbeat(job_id, **kwargs)
                runner._jobs.cancel(job_id, actor="operator", reason="人工取消")
                return result

            runner._jobs.heartbeat = cancel_right_after_heartbeat
            outcome = runner.run_once(pi_factory=lambda env: FakePi())
            self.assertEqual(outcome["outcome"], "discarded_stale")

            records = repos.decisions.list()
            types = [d["decision_type"] for d in records]
            self.assertIn("supplier_quote_gap_review", types)
            self.assertIn("decision_invalidated", types)
            # the evidence view must not count the invalidated decision
            invalidated = {d["target_ref"] for d in records if d["decision_type"] == "decision_invalidated"}
            valid = [d for d in records if d["record_id"] not in invalidated and d["decision_type"] != "decision_invalidated"]
            self.assertEqual(len(valid), 0)

    def test_evidence_endpoint_hides_invalidated_decisions(self):
        from fastapi.testclient import TestClient
        from xianyu_agent import server

        client = TestClient(server.app)
        submit = client.post("/commerce/pi-tasks/quote-review", json={})
        job_id = submit.json()["record_id"]
        runner = server.pi_task_runner
        original_complete = runner._jobs.complete

        def complete_then_cancel(job_id, **kwargs):
            original_complete(job_id, **kwargs)
            return runner._jobs.get(job_id)

        # normal run first
        outcome = runner.run_once(pi_factory=lambda env: FakePi())
        self.assertEqual(outcome["outcome"], "succeeded")
        # simulate a late invalidation arriving afterwards for that decision
        from xianyu_agent.commerce.contracts import build_decision_record

        runner._decisions.append(
            build_decision_record(
                summary="late invalidation",
                actor_role="operator",
                decision_type="decision_invalidated",
                target_ref=outcome["decision_id"],
                result={"invalidated": True, "reason": "stale"},
            )
        )
        evidence = client.get(f"/commerce/pi-tasks/{job_id}/evidence").json()
        self.assertNotIn(outcome["decision_id"], evidence["decision_ids"])
        self.assertEqual(evidence["invalidated_decision_ids"], [outcome["decision_id"]])


def setup_shadow_env(tmp):
    root = Path(tmp)
    repos = JsonCommerceRepositories(root)
    facts = ProductFactsStore(root / "facts.json")
    commitments = PriceCommitmentStore(root / "commitments.json")
    drafts = DraftReplyService(root, facts, commitments)
    mandates = OperatingMandateStore(root)
    budgets = BudgetReservationStore(root)
    log = ShadowAuthorizationLog(root)
    observer = ShadowObserver(
        root,
        draft_service=drafts,
        opportunities=repos.opportunities,
        mandates=mandates,
        shadow_log=log,
        budget_store=budgets,
    )
    return root, repos, facts, drafts, mandates, budgets, log, observer


class ShadowUsageSemanticsTests(unittest.TestCase):
    def _setup(self, tmp):
        return setup_shadow_env(tmp)

    def test_max_actions_one_allows_only_first_of_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repos, facts, drafts, mandates, budgets, log, observer = self._setup(tmp)
            mandates.issue(
                operator="o",
                account_id="acc-1",
                action_types=["price_adjust"],
                per_transaction_limit={"state": "known", "amount": "500", "currency": "CNY"},
                daily_limit={"state": "known", "amount": "1000", "currency": "CNY"},
                max_actions=1,
                valid_until=datetime.now(UTC) + timedelta(days=1),
            )
            facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0)
            for i in range(3):
                drafts.draft_reply(
                    cookie_id="acc-1", chat_id=f"c{i}", item_id="item-1",
                    buyer_text="200卖吗", floor=180.0,
                )
            observer.run_tick()
            metrics = log.metrics()
            self.assertEqual(metrics["total"], 3)
            self.assertEqual(metrics["allowed"], 1, "max_actions=1 must allow exactly the first shadow action")

    def test_daily_amount_shadow_accumulation_denies_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repos, facts, drafts, mandates, budgets, log, observer = self._setup(tmp)
            mandates.issue(
                operator="o",
                account_id="acc-1",
                action_types=["price_adjust"],
                per_transaction_limit={"state": "known", "amount": "100", "currency": "CNY"},
                daily_limit={"state": "known", "amount": "100", "currency": "CNY"},
                valid_until=datetime.now(UTC) + timedelta(days=1),
            )
            facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0)
            drafts.draft_reply(cookie_id="acc-1", chat_id="c0", item_id="item-1", buyer_text="60卖吗", floor=50.0)
            drafts.draft_reply(cookie_id="acc-1", chat_id="c1", item_id="item-1", buyer_text="70卖吗", floor=50.0)
            observer.run_tick()
            metrics = log.metrics()
            self.assertEqual(metrics["allowed"], 1, "60 allowed; 60+70 > 100 daily must deny the second")


class ShadowLogConsistencyTests(unittest.TestCase):
    def _setup(self, tmp):
        return setup_shadow_env(tmp)
    def test_log_write_failure_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repos, facts, drafts, mandates, budgets, log, observer = self._setup(tmp)
            facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0, stock_available=2)
            drafts.draft_reply(cookie_id="acc-1", chat_id="c0", item_id="item-1", buyer_text="还有货吗")

            original_persist = log._store._persist
            state = {"failed": False}

            def failing_persist():
                if not state["failed"]:
                    state["failed"] = True
                    raise RuntimeError("disk full")
                return original_persist()

            log._store._persist = failing_persist
            with self.assertRaises(RuntimeError):
                observer.run_tick()
            log._store._persist = original_persist

            # the event was NOT consumed: retry records it
            result = observer.run_tick()
            self.assertEqual(result["reply_drafts"], 1)
            self.assertEqual(log.metrics()["total"], 1)
            # and a third tick does not duplicate (event-id idempotent log)
            again = observer.run_tick()
            self.assertEqual(again["reply_drafts"], 0)
            self.assertEqual(log.metrics()["total"], 1)


class EvidenceSnapshotTests(unittest.TestCase):
    def test_decision_persists_inputs_and_tool_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            outcome = runner.run_once(pi_factory=lambda env: FakePi())
            job_id = outcome["job_id"]
            decision = repos.decisions.list()[0]
            evidence_ref = [
                r for r in decision["evidence_refs"] if r["ref_type"] == "pi_evidence_file"
            ][0]
            path = Path(evidence_ref["ref_id"])
            self.assertTrue(path.is_file(), f"evidence file missing: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("inputs", payload)
            self.assertIn("tool_calls", payload)
            self.assertEqual(len(payload["tool_calls"]), 1)
            self.assertIn("inputs_sha256", decision["input_versions"])
            # the persisted inputs are the actual snapshot, not a generic ref
            self.assertEqual(payload["inputs"]["committed_offer_count"], 0)

    def test_quote_inputs_filter_committed_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos = JsonCommerceRepositories(root)
            from xianyu_agent.supplier_import import SupplierImportService

            importer = SupplierImportService(repos, root)
            quote = (
                "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
                "GZ-9,台,150,件,1,5,15,120x60x85cm,众商,全新\n"
            ).encode("utf-8")
            preview = importer.preview_bytes(quote, kind="csv")
            confirm = importer.confirm(
                preview["preview_id"],
                content_sha256=preview["content_sha256"],
                revision=preview["revision"],
                supplier="S",
                operator="t",
            )
            importer.revoke_batch(confirm["batch"]["record_id"], operator="t", reason="错误版本")

            runner = PiTaskRunner(AgentJobStore(root), repos, data_dir=root, import_service=importer)
            runner.start()
            inputs = runner._collect_quote_inputs()
            self.assertEqual(inputs["committed_offer_count"], 0)
            self.assertEqual(inputs["excluded_non_committed"], 1)


if __name__ == "__main__":
    unittest.main()
