"""WP07 persistent agent job store tests."""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xianyu_agent.agent_jobs import (
    AgentJobError,
    AgentJobStore,
    FencingError,
    JOB_AWAITING_INPUT,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    JOB_TIMED_OUT,
    JOB_UNKNOWN,
)
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.decision_records import decision_from_job

UTC = timezone.utc


class AgentJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgentJobStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _submit(self, **overrides):
        params = dict(role="matching", priority=5)
        params.update(overrides)
        return self.store.submit(**params)

    def test_submit_and_idempotency_key_dedup(self):
        first = self._submit(idempotency_key="opp-1-match")
        second = self._submit(idempotency_key="opp-1-match")
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(self.store.list()), 1)

    def test_write_jobs_require_account(self):
        with self.assertRaises(AgentJobError):
            self._submit(write_required=True)

    def test_claim_priority_then_fifo(self):
        low = self._submit(priority=9)
        urgent = self._submit(priority=1)
        later_urgent = self._submit(priority=1)
        first = self.store.claim_next(worker_id="w1")
        second = self.store.claim_next(worker_id="w1")
        self.assertEqual(first["record_id"], urgent["record_id"])
        self.assertEqual(second["record_id"], later_urgent["record_id"])
        self.assertEqual(self.store.claim_next(worker_id="w1")["record_id"], low["record_id"])
        self.assertIsNone(self.store.claim_next(worker_id="w1"))

    def test_claim_race_single_job_one_winner(self):
        self._submit()
        results = []
        lock = threading.Lock()

        def claim(worker):
            job = self.store.claim_next(worker_id=worker)
            with lock:
                results.append((worker, job["record_id"] if job else None))

        threads = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r in results if r[1] is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(sum(1 for r in results if r[1] is None), 3)

    def test_per_account_write_serialization(self):
        write_a1 = self._submit(role="publish", account_id="acc-A", write_required=True, priority=1)
        write_a2 = self._submit(role="publish", account_id="acc-A", write_required=True, priority=1)
        write_b = self._submit(role="publish", account_id="acc-B", write_required=True, priority=2)
        read_a = self._submit(role="analysis", account_id="acc-A", priority=3)

        first = self.store.claim_next(worker_id="w1")
        self.assertEqual(first["record_id"], write_a1["record_id"])
        # second write for the same account is blocked while leased
        blocked = self.store.claim_next(worker_id="w2")
        self.assertEqual(blocked["record_id"], write_b["record_id"])  # different account proceeds
        read_job = self.store.claim_next(worker_id="w3")
        self.assertEqual(read_job["record_id"], read_a["record_id"])  # reads never blocked
        still_blocked = self.store.claim_next(worker_id="w4")
        self.assertIsNone(still_blocked)
        # completing the first write releases the account
        self.store.complete(write_a1["record_id"], worker_id="w1", fencing_token=first["fencing_token"], result_ref="done")
        released = self.store.claim_next(worker_id="w5")
        self.assertEqual(released["record_id"], write_a2["record_id"])

    def test_lifecycle_start_heartbeat_complete(self):
        job = self._submit()
        claimed = self.store.claim_next(worker_id="w1")
        self.assertEqual(claimed["status"], "leased")
        self.assertEqual(claimed["attempt"], 1)
        running = self.store.start(job["record_id"], worker_id="w1", fencing_token=claimed["fencing_token"])
        self.assertEqual(running["status"], JOB_RUNNING)
        beat = self.store.heartbeat(job["record_id"], worker_id="w1", fencing_token=claimed["fencing_token"])
        self.assertIn("lease_until", beat)
        done = self.store.complete(
            job["record_id"],
            worker_id="w1",
            fencing_token=claimed["fencing_token"],
            result_ref="decision:dec_1",
            cost={"input_tokens": 1200, "output_tokens": 300, "model": "deepseek", "amount": "0.012"},
        )
        self.assertEqual(done["status"], JOB_SUCCEEDED)
        self.assertEqual(done["result_ref"], "decision:dec_1")

    def test_stale_fencing_token_rejected(self):
        job = self._submit()
        claimed = self.store.claim_next(worker_id="w1")
        token = claimed["fencing_token"]
        # a recovery path bumps the token behind the worker's back
        self.store.recover_expired_leases(now=datetime.now(UTC) + timedelta(hours=1))
        with self.assertRaises(FencingError):
            self.store.complete(job["record_id"], worker_id="w1", fencing_token=token)
        with self.assertRaises(FencingError):
            self.store.heartbeat(job["record_id"], worker_id="w1", fencing_token=token)

    def test_fail_requeues_until_attempts_exhausted(self):
        self._submit(max_attempts=2)
        claimed = self.store.claim_next(worker_id="w1")
        requeued = self.store.fail(
            claimed["record_id"], worker_id="w1", fencing_token=claimed["fencing_token"], error="transient"
        )
        self.assertEqual(requeued["status"], JOB_QUEUED)
        self.assertEqual(requeued["attempt"], 1)
        claimed2 = self.store.claim_next(worker_id="w2")
        self.assertEqual(claimed2["attempt"], 2)
        failed = self.store.fail(
            claimed2["record_id"], worker_id="w2", fencing_token=claimed2["fencing_token"], error="again"
        )
        self.assertEqual(failed["status"], JOB_FAILED)

    def test_non_retryable_fail_fails_immediately(self):
        job = self._submit()
        claimed = self.store.claim_next(worker_id="w1")
        failed = self.store.fail(
            job["record_id"], worker_id="w1", fencing_token=claimed["fencing_token"], error="bad input", retryable=False
        )
        self.assertEqual(failed["status"], JOB_FAILED)

    def test_expired_analysis_job_requeues_write_job_goes_unknown(self):
        analysis = self._submit(role="analysis")
        write = self._submit(role="publish", account_id="acc-1", write_required=True)
        claimed_analysis = self.store.claim_next(worker_id="w1")
        claimed_write = self.store.claim_next(worker_id="w2")
        recovered = self.store.recover_expired_leases(now=datetime.now(UTC) + timedelta(hours=2))
        by_id = {j["record_id"]: j for j in recovered}
        self.assertEqual(by_id[claimed_analysis["record_id"]]["status"], JOB_QUEUED)
        self.assertEqual(by_id[claimed_write["record_id"]]["status"], JOB_UNKNOWN)
        self.assertIn("verification required", by_id[claimed_write["record_id"]]["error"])

    def test_expired_analysis_exhausted_attempts_times_out(self):
        job = self._submit(max_attempts=1)
        claimed = self.store.claim_next(worker_id="w1")
        recovered = self.store.recover_expired_leases(now=datetime.now(UTC) + timedelta(hours=1))
        by_id = {j["record_id"]: j for j in recovered}
        self.assertEqual(by_id[job["record_id"]]["status"], JOB_TIMED_OUT)

    def test_cancel_queued_and_leased(self):
        queued = self._submit()
        cancelled = self.store.cancel(queued["record_id"], actor="operator", reason="duplicate")
        self.assertEqual(cancelled["status"], JOB_CANCELLED)
        leased_job = self._submit()
        claimed = self.store.claim_next(worker_id="w1")
        cancelled_leased = self.store.cancel(leased_job["record_id"], actor="operator", reason="operator stop")
        self.assertEqual(cancelled_leased["status"], JOB_CANCELLED)
        # terminal states cannot be cancelled again
        with self.assertRaises(AgentJobError):
            self.store.cancel(leased_job["record_id"], actor="operator", reason="again")

    def test_awaiting_input_and_resume(self):
        job = self._submit()
        claimed = self.store.claim_next(worker_id="w1")
        waiting = self.store.mark_awaiting_input(
            job["record_id"], worker_id="w1", fencing_token=claimed["fencing_token"], note="need supplier confirmation"
        )
        self.assertEqual(waiting["status"], JOB_AWAITING_INPUT)
        # waiting jobs are not claimable
        self.assertIsNone(self.store.claim_next(worker_id="w2"))
        resumed = self.store.resume(job["record_id"], actor="operator")
        self.assertEqual(resumed["status"], JOB_QUEUED)

    def test_restart_persistence_and_continuation(self):
        job = self._submit()
        claimed = self.store.claim_next(worker_id="w1")
        reopened = AgentJobStore(Path(self.tmp.name))
        stored = reopened.get(job["record_id"])
        self.assertEqual(stored["status"], "leased")
        self.assertEqual(stored["lease_owner"], "w1")
        # the old lease can still complete after restart (lease intact)
        done = reopened.complete(
            job["record_id"], worker_id="w1", fencing_token=claimed["fencing_token"], result_ref="ok"
        )
        self.assertEqual(done["status"], JOB_SUCCEEDED)
        self.assertEqual(reopened.stats()["by_status"][JOB_SUCCEEDED], 1)


class DecisionFromJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))
        self.store = AgentJobStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_decision_links_job_and_records_unknown_cost_honestly(self):
        job = self.store.submit(role="pricing", goal_id="opp-1", input_versions={"economics": 3})
        record = decision_from_job(
            job,
            summary="suggest price 299",
            decision_type="opportunity_review",
            recommendation="advance",
            unknowns=["platform fee not confirmed"],
            model_version="deepseek/deepseek-v4-flash",
            cost=None,
        )
        stored = self.repos.decisions.append(record)
        self.assertEqual(stored["actor_role"], "pricing")
        self.assertEqual(stored["source_ref"], f"agent_job:{job['record_id']}")
        self.assertIn(
            {"ref_type": "agent_job", "ref_id": job["record_id"]},
            stored["evidence_refs"],
        )
        self.assertEqual(stored["input_versions"]["job_inputs"], {"economics": 3})
        # no cost provided -> empty dict, not fabricated zeros
        self.assertEqual(stored["cost"], {})

    def test_summary_required(self):
        with self.assertRaises(ValueError):
            decision_from_job(self.store.submit(role="r"), summary="  ", decision_type="x")


if __name__ == "__main__":
    unittest.main()
