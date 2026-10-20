"""Pi job chain offline tests (FakePi: no real model, no real subprocess).

Layer separation: these are the MOCK layer; real Pi runs are produced by
scripts/run_pi_quote_review_real.py and counted separately.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from xianyu_agent import server
from xianyu_agent.agent_jobs import AgentJobStore, JOB_SUCCEEDED
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.pi_jobs import (
    JOB_TOKEN_HEADER,
    JOB_WORKER_ID,
    JobPolicyError,
    PiTaskRunner,
    ReadOnlyJobGate,
    validate_review_output,
)

UTC = timezone.utc
VALID_REVIEW = '{"findings": [{"sku": "GZ-1", "field": "freight", "severity": "warn", "note": "运费未知"}], "summary": "1 个缺口"}'


class FakePi:
    """Mimics PiRpcClient.prompt's result shape."""

    def __init__(self, message=VALID_REVIEW, usage=None, error=None, side_effect=None):
        self.message = message
        self.usage = usage
        self.error = error
        self.side_effect = side_effect
        self.calls = 0

    def prompt(self, message, context=None):
        self.calls += 1
        if self.side_effect:
            self.side_effect()
        if self.error:
            raise self.error
        return {
            "ok": True,
            "result": {
                "message": self.message,
                "session_id": "job-session-1",
                "provider": "deepseek",
                "model": "deepseek-flash",
                "turns": 1,
                "tool_calls": [{"tool_call_id": "t1", "name": "xianyu_business_snapshot", "ok": True}],
                "usage": self.usage or {},
            },
        }


def make_runner(tmp, **kwargs):
    root = Path(tmp)
    jobs = AgentJobStore(root)
    repos = JsonCommerceRepositories(root)
    runner = PiTaskRunner(jobs, repos, **kwargs)
    runner.start()
    return runner, jobs, repos


class RunnerBoundaryTests(unittest.TestCase):
    def test_repeated_start_refused_and_stop_stops_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            with self.assertRaises(RuntimeError):
                runner.start()
            runner.submit_quote_review_job()
            runner.stop()
            self.assertIsNone(runner.run_once(pi_factory=lambda env: FakePi()))
            self.assertEqual(jobs.list(status="queued")[0]["status"], "queued")  # untouched

    def test_run_before_start_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = PiTaskRunner(AgentJobStore(root), JsonCommerceRepositories(root))
            with self.assertRaises(RuntimeError):
                runner.run_once(pi_factory=lambda env: FakePi())


class GateUnitTests(unittest.TestCase):
    def test_readonly_policy(self):
        gate = ReadOnlyJobGate()
        token = gate.issue("job_x", max_tool_calls=2)
        record = gate.authorize(token, method="GET", path="/commerce/offers")
        self.assertEqual(record["job_id"], "job_x")
        gate.authorize(token, method="GET", path="/health")
        with self.assertRaises(JobPolicyError):  # cap reached
            gate.authorize(token, method="GET", path="/health")
        with self.assertRaises(JobPolicyError):  # writes rejected
            gate.authorize(token, method="POST", path="/commerce/market-observations")
        with self.assertRaises(JobPolicyError):  # non-allowlisted path
            gate.authorize(token, method="GET", path="/some/secret")

    def test_unknown_token_rejected(self):
        gate = ReadOnlyJobGate()
        with self.assertRaises(JobPolicyError):
            gate.authorize("nope", method="GET", path="/health")


class GateMiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.token = server.pi_job_gate.issue("job_mw", max_tool_calls=1)

    def tearDown(self):
        server.pi_job_gate.release(self.token)

    def test_allowed_get_passes_and_counts(self):
        response = self.client.get("/health", headers={JOB_TOKEN_HEADER: self.token})
        self.assertEqual(response.status_code, 200)

    def test_write_rejected_403(self):
        response = self.client.post(
            "/commerce/market-observations",
            headers={JOB_TOKEN_HEADER: self.token},
            json={"sku": "X", "price": "1"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("read-only", response.json()["detail"])

    def test_cap_rejected_403(self):
        self.client.get("/health", headers={JOB_TOKEN_HEADER: self.token})
        second = self.client.get("/health", headers={JOB_TOKEN_HEADER: self.token})
        self.assertEqual(second.status_code, 403)
        self.assertIn("cap", second.json()["detail"])

    def test_no_token_untouched(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)


class ExecutionLifecycleTests(unittest.TestCase):
    def test_happy_path_records_decision_with_unknown_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            outcome = runner.run_once(pi_factory=lambda env: FakePi(usage=None))
            self.assertEqual(outcome["outcome"], "succeeded")
            stored = jobs.get(job["record_id"])
            self.assertEqual(stored["status"], JOB_SUCCEEDED)
            self.assertIn("decision:", stored["result_ref"])
            decisions = repos.decisions.list()
            self.assertEqual(len(decisions), 1)
            decision = decisions[0]
            self.assertEqual(decision["decision_type"], "supplier_quote_gap_review")
            # unknown usage recorded as unknown, never zero
            self.assertIsNone(decision["cost"]["total_tokens"])
            self.assertIn("not zero", decision["cost"]["note"])
            self.assertIn("model token usage", " ".join(decision["unknowns"]))

    def test_known_usage_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            runner.submit_quote_review_job()
            outcome = runner.run_once(
                pi_factory=lambda env: FakePi(usage={"input": 100, "output": 20, "totalTokens": 120})
            )
            self.assertEqual(outcome["outcome"], "succeeded")
            decision = repos.decisions.list()[0]
            self.assertEqual(decision["cost"]["total_tokens"], 120)
            self.assertNotIn("model token usage", " ".join(decision["unknowns"]))

    def test_pi_failure_fails_job_requeueably(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            outcome = runner.run_once(pi_factory=lambda env: FakePi(error=RuntimeError("pi down")))
            self.assertEqual(outcome["outcome"], "failed")
            stored = jobs.get(job["record_id"])
            self.assertEqual(stored["status"], "queued")  # requeued for retry
            self.assertEqual(repos.decisions.list(), [])  # no decision from a failed attempt

    def test_invalid_output_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            outcome = runner.run_once(pi_factory=lambda env: FakePi(message="我觉得不错"))
            self.assertEqual(outcome["outcome"], "invalid_output")
            stored = jobs.get(job["record_id"])
            self.assertEqual(stored["status"], "queued")
            self.assertIn("not valid JSON", stored["error"])

    def test_retry_does_not_duplicate_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            runner.run_once(pi_factory=lambda env: FakePi(message="not json", usage={"totalTokens": 15}))
            outcome = runner.run_once(pi_factory=lambda env: FakePi())
            self.assertEqual(outcome["outcome"], "succeeded")
            decisions = repos.decisions.list()
            self.assertEqual(len(decisions), 1)

    def test_deadline_exceeded_job_never_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp, deadline_seconds=0.0)
            job = runner.submit_quote_review_job()
            outcome = runner.run_once(
                pi_factory=lambda env: FakePi(),
                now=datetime.now(UTC) + timedelta(seconds=5),
            )
            self.assertEqual(outcome["outcome"], "rejected_deadline")
            self.assertEqual(jobs.get(job["record_id"])["status"], "failed")
            self.assertEqual(repos.decisions.list(), [])


class StaleResultTests(unittest.TestCase):
    def test_result_after_lease_loss_is_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()

            def lose_lease_during_run():
                # lease expires mid-run and recovery bumps the fencing token
                jobs.recover_expired_leases(now=datetime.now(UTC) + timedelta(hours=2))

            outcome = runner.run_once(
                pi_factory=lambda env: FakePi(side_effect=lose_lease_during_run)
            )
            self.assertEqual(outcome["outcome"], "discarded_stale")
            # no decision was written from the stale attempt
            self.assertEqual(repos.decisions.list(), [])
            stored = jobs.get(job["record_id"])
            self.assertEqual(stored["status"], "queued")  # requeued by recovery


class ReconcileTests(unittest.TestCase):
    def test_crash_between_decision_and_completion_is_healed(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            claimed = jobs.claim_next(worker_id=JOB_WORKER_ID)
            self.assertEqual(claimed["record_id"], job["record_id"])
            jobs.start(job["record_id"], worker_id=JOB_WORKER_ID, fencing_token=claimed["fencing_token"])

            # crash window: the decision was written, completion was not
            runner._decision_for_attempt(
                claimed,
                review=validate_review_output(VALID_REVIEW),
                payload={"session_id": "s", "provider": "deepseek", "model": "m", "tool_calls": [], "usage": {}},
                cost={"model": "m", "total_tokens": None},
            )
            self.assertEqual(len(repos.decisions.list()), 1)

            report = runner.reconcile_task_decisions(now=datetime.now(UTC) + timedelta(hours=3))
            self.assertEqual(len(report["healed_completions"]), 1)
            stored = jobs.get(job["record_id"])
            self.assertEqual(stored["status"], JOB_SUCCEEDED)
            self.assertIn("decision:", stored["result_ref"])
            # no duplicate decision after healing
            self.assertEqual(len(repos.decisions.list()), 1)

    def test_lease_recovery_without_decision_requeues(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, jobs, repos = make_runner(tmp)
            job = runner.submit_quote_review_job()
            jobs.claim_next(worker_id=JOB_WORKER_ID)
            report = runner.reconcile_task_decisions(now=datetime.now(UTC) + timedelta(hours=3))
            self.assertEqual(report["healed_completions"], [])
            self.assertEqual(report["lease_recoveries"], 1)
            self.assertEqual(jobs.get(job["record_id"])["status"], "queued")


class SchemaTests(unittest.TestCase):
    def test_fenced_json_accepted(self):
        payload = validate_review_output("```json\n" + VALID_REVIEW + "\n```")
        self.assertEqual(len(payload["findings"]), 1)

    def test_bad_severity_rejected(self):
        with self.assertRaises(ValueError):
            validate_review_output('{"findings": [{"sku": "A", "field": "f", "severity": "huge"}], "summary": "x"}')


class EvidenceEndpointTests(unittest.TestCase):
    def test_task_decision_inputs_traceability(self):
        client = TestClient(server.app)
        submit = client.post("/commerce/pi-tasks/quote-review", json={})
        self.assertEqual(submit.status_code, 200, submit.text)
        job_id = submit.json()["record_id"]
        # R02: the runner boundary flag is raised by the app runtime, no
        # longer at import time — ensure it is started for run_once (a
        # lifespan shutdown elsewhere in the suite may have stopped it).
        if not server.pi_task_runner.status["started"] or server.pi_task_runner.status["stopped"]:
            server.pi_task_runner.start()
        # mock layer only: execute through the runner with a FakePi
        # (real Pi runs belong to scripts/run_pi_quote_review_real.py)
        outcome = server.pi_task_runner.run_once(pi_factory=lambda env: FakePi())
        self.assertEqual(outcome["outcome"], "succeeded")
        evidence = client.get(f"/commerce/pi-tasks/{job_id}/evidence")
        self.assertEqual(evidence.status_code, 200, evidence.text)
        body = evidence.json()
        self.assertEqual(body["job"]["record_id"], job_id)
        self.assertTrue(body["decision_ids"])
        self.assertIn("input_refs", body["inputs"])
        # unknown usage rendered unknown, not zero
        self.assertIsNone(body["pi"]["cost_display"]["total_tokens"])


if __name__ == "__main__":
    unittest.main()
