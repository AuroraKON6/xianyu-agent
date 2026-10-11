import copy
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.pi_jobs import JOB_TOKEN_HEADER, ModelUsageLedger, PiTaskRunner
from xianyu_agent.pi_usage_reconciliation import build_usage_reconciliation_router


class UsageReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.token = "test-owner-secret-not-a-real-credential-12345678"
        self.digest = hashlib.sha256(self.token.encode()).hexdigest()
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.runner = self.new_runner()
        self.job = self.runner.submit_quote_review_job()
        claimed = self.runner._jobs.claim_next(worker_id="test")
        self.runner._jobs.start(self.job["record_id"], worker_id="test", fencing_token=claimed["fencing_token"])
        self.runner.model_usage.begin_attempt(day="2026-09-22", job_id=self.job["record_id"], attempt=1)
        self.runner.model_usage.record(day="2026-09-22", job_id=self.job["record_id"], attempt=1, total_tokens=None)
        self.runner._jobs.fail(self.job["record_id"], worker_id="test", fencing_token=claimed["fencing_token"],
                               error="unknown provider result", retryable=False)
        self.client = self.new_client(self.runner)

    def new_runner(self):
        runner = PiTaskRunner(AgentJobStore(self.root), JsonCommerceRepositories(self.root), data_dir=self.root)
        runner.start()
        return runner

    def new_client(self, runner, digest=None, operator="owner-test"):
        app = FastAPI()
        app.include_router(build_usage_reconciliation_router(
            runner, token_sha256=self.digest if digest is None else digest, operator=operator))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def proposal(self, resolution="settle", total_tokens=15):
        return {
            "job_id": self.job["record_id"], "attempt": 1,
            "resolution": resolution, "total_tokens": total_tokens,
            "idempotency_key": "reconcile-test-1",
            "reason": "Operator checked the original offline evidence record.",
            "evidence": {
                "kind": "provider_usage" if resolution == "settle" else "dispatch_not_sent",
                "reference": "offline-evidence-record-1",
                "job_id": self.job["record_id"], "attempt": 1,
                "total_tokens": total_tokens,
                "statement": "This isolated fixture records the manually verified result.",
            },
        }

    def preview(self, proposal=None, client=None):
        proposal = proposal or self.proposal()
        response = (client or self.client).post("/commerce/pi-usage/preview", json=proposal, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return {**proposal, "expected_state": response.json()["expected_state"],
                "approval_digest": response.json()["approval_digest"], "confirmed": True}

    def commit(self, request, client=None):
        return (client or self.client).post("/commerce/pi-usage/reconcile", json=request, headers=self.headers)

    def test_disabled_authentication_and_job_sessions_fail_closed(self):
        for digest, operator in (("", "owner"), (self.digest, ""), ("bad", "owner")):
            client = self.new_client(self.runner, digest=digest, operator=operator)
            self.assertEqual(client.get("/commerce/pi-usage/unresolved", headers=self.headers).status_code, 503)
        self.assertEqual(self.client.get("/commerce/pi-usage/unresolved").status_code, 401)
        self.assertEqual(self.client.get("/commerce/pi-usage/unresolved",
                                        headers={"Authorization": "Bearer wrong"}).status_code, 401)
        headers = {**self.headers, JOB_TOKEN_HEADER: "even-an-expired-job-token"}
        self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=self.proposal(), headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/commerce/pi-usage/unresolved", headers=headers).status_code, 403)

    def test_preview_is_read_only_and_commit_keeps_atomic_audit(self):
        path = self.runner.model_usage._store.path
        before = path.read_bytes()
        request = self.preview()
        self.assertEqual(path.read_bytes(), before)
        response = self.commit(request)
        self.assertEqual(response.status_code, 200, response.text)
        entry = ModelUsageLedger(self.root)._store.all()[0]
        self.assertEqual(entry["total_tokens"], 15)
        self.assertEqual(entry["business_day"], "2026-09-22")
        self.assertEqual(entry["status"], "settled")
        audit = entry["reconciliation"]
        self.assertEqual(audit["operator"], "owner-test")
        self.assertIsNone(audit["before"]["total_tokens"])
        self.assertEqual(audit["evidence"], request["evidence"])
        self.assertEqual(len(audit["evidence_sha256"]), 64)
        self.assertNotIn(self.token, path.read_text(encoding="utf-8"))
        self.assertEqual(self.runner._jobs.get(self.job["record_id"])["status"], "failed")
        self.assertEqual(self.runner._decisions.list(), [])

    def test_idempotent_replay_after_reload_returns_original_receipt(self):
        request = self.preview()
        first = self.commit(request)
        self.assertEqual(first.status_code, 200)
        path = self.runner.model_usage._store.path
        before = path.read_bytes()
        restarted = self.new_client(self.new_runner())
        second = self.commit(request, restarted)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["receipt"], first.json()["receipt"])
        self.assertTrue(second.json()["idempotent"])
        self.assertEqual(path.read_bytes(), before)

    def test_same_key_different_request_or_actor_is_conflict(self):
        request = self.preview()
        self.assertEqual(self.commit(request).status_code, 200)
        changed = {**request, "reason": "Another explanation with the same key."}
        self.assertEqual(self.commit(changed).status_code, 409)
        different_owner = self.new_client(self.new_runner(), operator="other-owner")
        self.assertEqual(self.commit(request, different_owner).status_code, 409)

    def test_operator_spoof_missing_confirmation_and_unbound_evidence_rejected(self):
        proposal = self.proposal()
        for fields in ({"operator": "forged-owner"}, {"total_tokens": -1},
                       {"total_tokens": True}, {"total_tokens": "15"}):
            response = self.client.post("/commerce/pi-usage/preview", json={**proposal, **fields}, headers=self.headers)
            self.assertEqual(response.status_code, 422)
        proposal["evidence"]["job_id"] = "another-job"
        self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=proposal, headers=self.headers).status_code, 422)
        request = self.preview()
        request["confirmed"] = False
        self.assertEqual(self.commit(request).status_code, 422)
        del request["confirmed"]
        self.assertEqual(self.commit(request).status_code, 422)
        self.assertIsNone(ModelUsageLedger(self.root)._store.all()[0]["total_tokens"])

    def test_changed_proposal_or_ledger_or_job_requires_new_preview(self):
        request = self.preview()
        changed = copy.deepcopy(request)
        changed["total_tokens"] = changed["evidence"]["total_tokens"] = 20
        self.assertEqual(self.commit(changed).status_code, 409)
        with self.runner.model_usage._store as store:
            store._items[0]["settlement_conflicts"] = [{"total_tokens": 99}]
        self.assertEqual(self.commit(request).status_code, 409)
        request = self.preview()
        with self.runner._jobs._store as store:
            store._items[0]["error"] = "new investigation state"
        self.assertEqual(self.commit(request).status_code, 409)

    def test_inflight_runner_and_leased_job_cannot_be_reconciled(self):
        self.runner._execute_lock.acquire()
        try:
            self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=self.proposal(), headers=self.headers).status_code, 409)
        finally:
            self.runner._execute_lock.release()
        with self.runner._jobs._store as store:
            store._items[0]["status"] = "running"
        self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=self.proposal(), headers=self.headers).status_code, 409)

    def test_settled_usage_cannot_be_lowered_by_manual_reconciliation(self):
        self.assertEqual(self.commit(self.preview()).status_code, 200)
        proposal = self.proposal(total_tokens=0)
        proposal["idempotency_key"] = "different-key"
        self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=proposal, headers=self.headers).status_code, 409)
        self.assertEqual(ModelUsageLedger(self.root)._store.all()[0]["total_tokens"], 15)

    def test_release_requires_no_dispatch_evidence_and_no_observed_output(self):
        proposal = self.proposal(resolution="release", total_tokens=0)
        bad = copy.deepcopy(proposal)
        bad["evidence"]["kind"] = "provider_usage"
        self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=bad, headers=self.headers).status_code, 422)
        with self.runner._jobs._store as store:
            store._items[0]["result_ref"] = "decision:observed-output"
        self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=proposal, headers=self.headers).status_code, 409)
        with self.runner._jobs._store as store:
            store._items[0]["result_ref"] = None
        response = self.commit(self.preview(proposal))
        self.assertEqual(response.status_code, 200, response.text)
        entry = ModelUsageLedger(self.root)._store.all()[0]
        self.assertEqual(entry["status"], "released")
        self.assertEqual(entry["total_tokens"], 0)
        self.assertTrue(ModelUsageLedger(self.root).begin_attempt(day="2026-09-23", job_id="new", attempt=1))

    def test_persist_failure_leaves_unknown_and_no_receipt_then_retry_succeeds(self):
        request = self.preview()
        with patch.object(self.runner.model_usage._store, "_persist", side_effect=OSError("disk full")):
            self.assertEqual(self.commit(request).status_code, 503)
        entry = ModelUsageLedger(self.root)._store.all()[0]
        self.assertIsNone(entry["total_tokens"])
        self.assertNotIn("reconciliation", entry)
        restarted = self.new_client(self.new_runner())
        self.assertEqual(self.commit(request, restarted).status_code, 200)

    def test_receipt_replays_after_persist_succeeded_but_response_failed(self):
        request = self.preview()
        original = self.runner.model_usage._store._persist

        def write_then_fail():
            original()
            raise OSError("response lost after file replacement")

        with patch.object(self.runner.model_usage._store, "_persist", side_effect=write_then_fail):
            self.assertEqual(self.commit(request).status_code, 503)
        saved = ModelUsageLedger(self.root)._store.all()[0]
        self.assertEqual(saved["total_tokens"], 15)
        response = self.commit(request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["idempotent"])
        self.assertEqual(response.json()["receipt"], saved["reconciliation"])

    def test_reconciled_known_usage_still_enforces_daily_budget(self):
        from xianyu_agent.commerce.contracts import utc_now

        with self.runner.model_usage._store as store:
            store._items[0]["business_day"] = utc_now().strftime("%Y-%m-%d")
        self.assertEqual(self.commit(self.preview(self.proposal(total_tokens=100))).status_code, 200)
        self.runner.daily_token_budget = 10
        self.runner.submit_quote_review_job(idempotency_key="after-daily-limit")
        factory = Mock()
        self.assertEqual(self.runner.run_once(pi_factory=factory)["outcome"], "budget_exhausted_day")
        factory.assert_not_called()

    def test_release_refuses_dangling_decision_without_completed_job(self):
        proposal = self.proposal(resolution="release", total_tokens=0)
        decision = {"source_ref": f"agent_job:{self.job['record_id']}", "input_versions": {"job_attempt": 1}}
        with patch.object(self.runner._decisions, "list", return_value=[decision]):
            response = self.client.post("/commerce/pi-usage/preview", json=proposal, headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_missing_evidence_does_not_create_receipt(self):
        proposal = self.proposal()
        del proposal["evidence"]
        self.assertEqual(self.client.post("/commerce/pi-usage/preview", json=proposal, headers=self.headers).status_code, 422)
        self.assertNotIn("reconciliation", ModelUsageLedger(self.root)._store.all()[0])

    def test_application_mount_is_authenticated_in_an_isolated_process(self):
        script = """
from fastapi.testclient import TestClient
from xianyu_agent import server
def forbidden(*args, **kwargs):
    raise AssertionError('provider must not be called')
server._pi_job_client_factory = forbidden
client = TestClient(server.app)
page = client.get('/console')
assert page.status_code == 200 and '/console/pi-usage.js' in page.text
script = client.get('/console/pi-usage.js')
assert script.status_code == 200 and 'pi-usage-panel' in script.text
assert client.get('/commerce/pi-usage/unresolved').status_code == 401
import os
response = client.get('/commerce/pi-usage/unresolved', headers={'Authorization': 'Bearer ' + os.environ['TEST_OWNER_TOKEN']})
assert response.status_code == 200, response.text
assert response.json()['total'] == 0
client.close()
print('RECONCILIATION_ROUTER_AUTHENTICATED')
"""
        root = self.root / "isolated-server"
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "DATA_DIR": str(root), "DB_PATH": str(root / "test.db"),
                 "XIANYU_ADAPTER": "stub", "SHADOW_OBSERVER_ENABLED": "0",
                 "DEEPSEEK_API_KEY": "", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                 "PI_USAGE_RECONCILE_OPERATOR": "owner-test", "PI_USAGE_RECONCILE_TOKEN_SHA256": self.digest,
                 "TEST_OWNER_TOKEN": self.token},
            capture_output=True, timeout=40, creationflags=0x08000000 if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertIn(b"RECONCILIATION_ROUTER_AUTHENTICATED", result.stdout)

    def test_competing_confirmations_cannot_both_change_consumption(self):
        first = self.preview()
        second_proposal = self.proposal(total_tokens=20)
        second_proposal["idempotency_key"] = "competing-key"
        second = self.preview(second_proposal)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.commit, request) for request in (first, second)]
            codes = [future.result().status_code for future in futures]
        self.assertEqual(sorted(codes), [200, 409])
        self.assertIn(ModelUsageLedger(self.root)._store.all()[0]["total_tokens"], (15, 20))

    def test_other_unknown_still_blocks_and_no_provider_is_called(self):
        with self.runner.model_usage._store as store:
            store._items.append({"business_day": "2026-09-21", "job_id": "legacy", "total_tokens": None})
        self.assertEqual(self.commit(self.preview()).status_code, 200)
        pending = self.client.get("/commerce/pi-usage/unresolved", headers=self.headers).json()
        self.assertEqual(pending["total"], 1)
        self.assertFalse(pending["entries"][0]["reconcilable"])
        self.runner.submit_quote_review_job(idempotency_key="after-manual-reconciliation")
        factory = Mock()
        self.assertEqual(self.runner.run_once(pi_factory=factory)["outcome"], "budget_unresolved_usage")
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
