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
        # R02: the shared runner's boundary flag is raised by the app
        # runtime (or run-next), no longer at import time; a lifespan
        # shutdown elsewhere in the suite may have stopped it.
        if not runner.status["started"] or runner.status["stopped"]:
            runner.start()
        from unittest.mock import patch
        from xianyu_agent.pi_jobs import ModelUsageLedger

        isolated_usage = tempfile.TemporaryDirectory()
        self.addCleanup(isolated_usage.cleanup)
        usage_patch = patch.object(runner, "model_usage", ModelUsageLedger(Path(isolated_usage.name)))
        usage_patch.start()
        self.addCleanup(usage_patch.stop)
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


class Round5ModelUsageTests(unittest.TestCase):
    """Round 5: consumption must be recorded per model ATTEMPT, including
    rejected/failed/timeout paths; unknown usage is never zero."""

    def test_overspend_rejection_still_records_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp, per_job_token_budget=500)
            runner.submit_quote_review_job()
            outcome = runner.run_once(
                pi_factory=lambda env: FakePi(usage={"input": 900, "output": 100, "totalTokens": 1000})
            )
            self.assertEqual(outcome["outcome"], "budget_exceeded")
            day = datetime.now(UTC).strftime("%Y-%m-%d")
            self.assertEqual(runner.model_usage.used_today(day=day), 1000,
                             "rejected-overspend attempt must still consume the daily ledger")

    def test_second_rejected_attempt_accumulates(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp, per_job_token_budget=500)
            for _ in range(2):
                runner.submit_quote_review_job()
                runner.run_once(
                    pi_factory=lambda env: FakePi(usage={"input": 900, "output": 100, "totalTokens": 1000})
                )
            day = datetime.now(UTC).strftime("%Y-%m-%d")
            self.assertEqual(runner.model_usage.used_today(day=day), 2000)

    def test_invalid_output_records_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            runner.run_once(pi_factory=lambda env: FakePi(message="不是JSON"))
            day = datetime.now(UTC).strftime("%Y-%m-%d")
            self.assertEqual(runner.model_usage.used_today(day=day), 15)

    def test_prompt_failure_records_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            runner.run_once(pi_factory=lambda env: FakePi(error=RuntimeError("rpc died")))
            day = datetime.now(UTC).strftime("%Y-%m-%d")
            entries = runner.model_usage.entries_today(day=day)
            self.assertEqual(len(entries), 1, "failed attempt must still create a ledger entry")
            self.assertIsNone(entries[0]["total_tokens"], "unknown usage stays None, never zero")
            self.assertEqual(runner.model_usage.used_today(day=day), 0)


class Round5ValidityWithoutMarkerTests(unittest.TestCase):
    """Round 5: validity derives from job state (status/result_ref/attempt),
    so a failed invalidation-marker write cannot resurrect a stale decision."""

    def test_cancelled_job_dangling_decision_hidden_after_marker_write_failure(self):
        from xianyu_agent.pi_jobs import decision_validity

        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            claimed = runner._jobs.claim_next(worker_id="pi_worker_1")
            runner._jobs.start(job["record_id"], worker_id="pi_worker_1", fencing_token=claimed["fencing_token"])
            # crash window: decision written, completion never happened
            decision = runner._decision_for_attempt(
                claimed,
                review={"findings": [], "summary": "late"},
                payload={"session_id": "s", "provider": "p", "model": "m", "tool_calls": [], "usage": {}},
                cost={"model": "m", "total_tokens": None},
            )
            # job gets cancelled; the invalidation marker write FAILS
            runner._jobs.cancel(job["record_id"], actor="operator", reason="cancel")
            original_persist = repos.decisions._store._persist
            repos.decisions._store._persist = lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
            try:
                runner._invalidate_decision(job, decision_id=decision["record_id"], reason="lease lost")
            except RuntimeError:
                pass  # marker write failed -- exactly the crash window
            repos.decisions._store._persist = original_persist
            self.assertFalse(
                any(d["decision_type"] == "decision_invalidated" for d in repos.decisions.list())
            )

            # RESTART: fresh store instances over the same files, validity
            # recomputed from the persisted job record alone
            restarted_jobs = AgentJobStore(Path(tmp))
            restarted_repos = JsonCommerceRepositories(Path(tmp))
            restarted_job = restarted_jobs.get(job["record_id"])
            self.assertEqual(restarted_job["status"], "cancelled")
            classification = decision_validity(restarted_job, restarted_repos.decisions.list())
            self.assertEqual([d["record_id"] for d in classification["valid"]], [])
            self.assertIn(decision["record_id"], [d["record_id"] for d in classification["stale"]])

    def test_succeeded_job_only_validates_result_ref_decision(self):
        from xianyu_agent.commerce.contracts import build_decision_record
        from xianyu_agent.pi_jobs import decision_validity

        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            outcome = runner.run_once(pi_factory=lambda env: FakePi())
            job_id = outcome["job_id"]
            dangling = repos.decisions.append(
                build_decision_record(
                    summary="dangling duplicate",
                    actor_role="supplier_quote_gap_review",
                    decision_type="supplier_quote_gap_review",
                    source_ref=f"agent_job:{job_id}",
                    result={"findings": []},
                )
            )
            job = AgentJobStore(Path(tmp)).get(job_id)
            classification = decision_validity(job, repos.decisions.list())
            self.assertEqual([d["record_id"] for d in classification["valid"]], [outcome["decision_id"]])
            self.assertIn(dangling["record_id"], [d["record_id"] for d in classification["stale"]])


class Round5ShadowIdempotentAccrualTests(unittest.TestCase):
    """Round 5: shadow usage accrual is idempotent by event id; replay
    reuses the saved verdict instead of re-evaluating."""

    def test_cursor_write_failure_retry_does_not_double_accrue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repos, facts, drafts, mandates, budgets, log, observer = setup_shadow_env(tmp)
            mandates.issue(
                operator="o",
                account_id="acc-1",
                action_types=["price_adjust"],
                per_transaction_limit={"state": "known", "amount": "500", "currency": "CNY"},
                daily_limit={"state": "known", "amount": "500", "currency": "CNY"},
                valid_until=datetime.now(UTC) + timedelta(days=1),
            )
            facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0)
            drafts.draft_reply(cookie_id="acc-1", chat_id="c0", item_id="item-1", buyer_text="60卖吗", floor=50.0)

            original_cursor_persist = observer._cursor._persist
            state = {"failed": False}

            def failing_cursor_persist():
                if not state["failed"]:
                    state["failed"] = True
                    raise RuntimeError("disk full")
                return original_cursor_persist()

            observer._cursor._persist = failing_cursor_persist
            with self.assertRaises(RuntimeError):
                observer.run_tick()  # log written, cursor write failed
            observer._cursor._persist = original_cursor_persist

            result = observer.run_tick()  # retry replays the event
            self.assertEqual(result["reply_drafts"], 1)
            usage_entries = [
                e for e in observer._shadow_usage.all() if str(e.get("event_id", "")).startswith("draft:")
            ]
            self.assertEqual(len(usage_entries), 1, "shadow usage must accrue once per event")
            # and the replayed verdict is the SAVED one (allowed), not re-evaluated
            self.assertEqual(log.metrics()["total"], 1)
            self.assertEqual(log.metrics()["allowed"], 1)

            # the next event within the same daily amount sees a single accrual
            drafts.draft_reply(cookie_id="acc-1", chat_id="c1", item_id="item-1", buyer_text="70卖吗", floor=50.0)
            observer.run_tick()
            metrics = log.metrics()
            self.assertEqual(metrics["total"], 2)
            self.assertEqual(metrics["allowed"], 2, "60+70 <= 500 with single accrual: both allowed")

    def test_record_replay_reuses_saved_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repos, facts, drafts, mandates, budgets, log, observer = setup_shadow_env(tmp)
            # no mandates at all -> first evaluation denied
            facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0, stock_available=2)
            drafts.draft_reply(cookie_id="acc-1", chat_id="c0", item_id="item-1", buyer_text="还有货吗")
            observer._record(
                event_id="draft:fixed", action_type="faq_reply",
                account_id="acc-1", sku="item-1", summary="s",
            )
            # a mandate appears later; replay must NOT flip the saved verdict
            mandates.issue(
                operator="o", account_id="acc-1", action_types=["faq_reply"],
                valid_until=datetime.now(UTC) + timedelta(days=1),
            )
            observer._record(
                event_id="draft:fixed", action_type="faq_reply",
                account_id="acc-1", sku="item-1", summary="s",
            )
            metrics = log.metrics()
            self.assertEqual(metrics["total"], 1)
            self.assertEqual(metrics["allowed"], 0, "replay must reuse the saved denied verdict")


if __name__ == "__main__":
    unittest.main()
