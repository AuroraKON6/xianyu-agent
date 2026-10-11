import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.budget_reservations import ShadowAuthorizationLog
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import utc_now
from xianyu_agent.operating_mandates import OperatingMandateStore
from xianyu_agent.pi_jobs import ModelUsageLedger, PiTaskRunner, decision_validity
from xianyu_agent.reply_drafts import DRAFT_COUNTER_OFFER
from xianyu_agent.shadow_observer import ShadowObserver


def new_runner(root):
    runner = PiTaskRunner(AgentJobStore(root), JsonCommerceRepositories(root), data_dir=root)
    runner.start()
    return runner


def fake_client():
    return SimpleNamespace(
        prompt=Mock(return_value={
            "ok": True,
            "result": {
                "message": '{"findings": [], "summary": "ok"}',
                "usage": {"input": 10, "output": 5, "totalTokens": 15},
                "model": "mock-model",
            },
        }),
        stop=Mock(),
    )


class AttemptSettlementTests(unittest.TestCase):
    def test_replay_is_idempotent_after_reload_and_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            self.assertTrue(ledger.begin_attempt(day="2026-09-22", job_id="job", attempt=1))
            first = ledger.record(day="2026-09-22", job_id="job", attempt=1,
                                  total_tokens=15, model="mock-model")
            restarted = ModelUsageLedger(root)
            self.assertEqual(first, restarted.record(day="2026-09-22", job_id="job", attempt=1,
                                                    total_tokens=15, model="mock-model"))
            self.assertFalse(restarted.begin_attempt(day="2026-09-22", job_id="job", attempt=1))
            self.assertTrue(restarted.begin_attempt(day="2026-09-22", job_id="job", attempt=2))
            restarted.record(day="2026-09-22", job_id="job", attempt=2, total_tokens=20)
            restarted.record(day="2026-09-22", job_id="job", attempt=1,
                             total_tokens=15, model="mock-model")
            self.assertEqual(restarted.used_today(day="2026-09-22"), 35)
            self.assertEqual(len(restarted.entries_today(day="2026-09-22")), 2)
            with self.assertRaises(ValueError):
                restarted.record(day="2026-09-22", job_id="job", total_tokens=15)

    def test_conflicting_settlement_does_not_change_persisted_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            ledger.begin_attempt(day="2026-09-22", job_id="job", attempt=1)
            ledger.record(day="2026-09-22", job_id="job", attempt=1, total_tokens=15, model="original")
            original = ledger.entries_today(day="2026-09-22")
            for changed in ({"total_tokens": 16}, {"total_tokens": None},
                            {"model": "other"}, {"day": "2026-09-23"}):
                with self.subTest(changed=changed):
                    request = dict(day="2026-09-22", job_id="job", attempt=1,
                                   total_tokens=15, model="original")
                    request.update(changed)
                    with self.assertRaises(ValueError):
                        ledger.record(**request)
                    reloaded = ModelUsageLedger(root).entries_today(day="2026-09-22")[0]
                    conflicts = reloaded.pop("settlement_conflicts")
                    self.assertEqual([reloaded], original)
                    self.assertTrue(conflicts)

    def test_unknown_settlement_cannot_silently_release_liability(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ModelUsageLedger(Path(directory))
            ledger.begin_attempt(day="2026-09-22", job_id="job", attempt=1)
            ledger.record(day="2026-09-22", job_id="job", attempt=1, total_tokens=None)
            with self.assertRaises(ValueError):
                ledger.record(day="2026-09-22", job_id="job", attempt=1, total_tokens=0)
            self.assertFalse(ledger.begin_attempt(day="2026-09-23", job_id="job", attempt=2))

    def test_invalid_usage_cannot_clear_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            ledger.begin_attempt(day="2026-09-22", job_id="job", attempt=1)
            for usage in (-1, True, 1.5, "15"):
                with self.subTest(usage=usage), self.assertRaises(ValueError):
                    ledger.record(day="2026-09-22", job_id="job", attempt=1, total_tokens=usage)
            self.assertIsNone(ModelUsageLedger(root).entries_today(day="2026-09-22")[0]["total_tokens"])

    def test_no_intent_cannot_create_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ModelUsageLedger(Path(directory))
            with self.assertRaises(ValueError):
                ledger.record(day="2026-09-22", job_id="job", attempt=1, total_tokens=15)
            self.assertEqual(ledger.entries_today(day="2026-09-22"), [])

    def test_legacy_unknown_blocks_without_rewriting_old_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "commerce" / "model_usage.json"
            path.parent.mkdir()
            records = [
                {"business_day": "2026-09-22", "job_id": "known", "total_tokens": 15},
                {"business_day": "2026-09-21", "job_id": "unknown", "total_tokens": None},
            ]
            path.write_text(json.dumps(records), encoding="utf-8")
            ledger = ModelUsageLedger(root)
            self.assertEqual(ledger.used_today(day="2026-09-22"), 15)
            self.assertFalse(ledger.begin_attempt(day="2026-09-23", job_id="next", attempt=1))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), records)


class ModelWriteWindowTests(unittest.TestCase):
    def test_intent_write_failure_never_constructs_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = new_runner(root)
            runner.submit_quote_review_job()
            factory = Mock(return_value=fake_client())
            with patch.object(runner.model_usage._store, "_persist", side_effect=OSError("intent failed")):
                with self.assertRaises(OSError):
                    runner.run_once(pi_factory=factory)
            factory.assert_not_called()
            self.assertEqual(ModelUsageLedger(root)._store.all(), [])

    def test_provider_entry_observes_durable_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = new_runner(root)
            job = runner.submit_quote_review_job()
            client = fake_client()

            def construct(env):
                rows = ModelUsageLedger(root)._store.all()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["job_id"], job["record_id"])
                self.assertEqual(rows[0]["attempt"], 1)
                self.assertEqual(rows[0]["status"], "pending")
                return client

            self.assertEqual(runner.run_once(pi_factory=construct)["outcome"], "succeeded")

    def test_output_write_failures_keep_known_usage_without_valid_decision(self):
        for window in ("evidence", "decision", "complete"):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runner = new_runner(root)
                job = runner.submit_quote_review_job()
                client = fake_client()
                if window == "evidence":
                    failure = patch.object(runner, "_write_evidence_file", side_effect=OSError(window))
                else:
                    target = runner._decisions._store if window == "decision" else runner._jobs._store
                    failure = patch.object(target, "_persist", side_effect=OSError(window))
                    if window == "complete":
                        original_decision = runner._decision_for_attempt

                        def decision_then_fail(*args, **kwargs):
                            result = original_decision(*args, **kwargs)
                            failure.start()
                            return result

                        runner._decision_for_attempt = decision_then_fail
                try:
                    if window == "complete":
                        with self.assertRaises(OSError):
                            runner.run_once(pi_factory=lambda env: client)
                    else:
                        with failure, self.assertRaises(OSError):
                            runner.run_once(pi_factory=lambda env: client)
                finally:
                    failure.stop()
                ledger = ModelUsageLedger(root)
                self.assertEqual(ledger.used_today(day=utc_now().strftime("%Y-%m-%d")), 15)
                self.assertEqual(len(ledger._store.all()), 1)
                restarted_job = AgentJobStore(root).get(job["record_id"])
                decisions = JsonCommerceRepositories(root).decisions.list()
                self.assertEqual(decision_validity(restarted_job, decisions)["valid"], [])
                client.prompt.assert_called_once()

    def test_settlement_failure_preserves_pending_and_blocks_next_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = new_runner(root)
            runner.submit_quote_review_job()
            client = fake_client()
            failure = patch.object(runner.model_usage._store, "_persist", side_effect=OSError("usage failed"))
            client.prompt.side_effect = lambda *args, **kwargs: (failure.start(), client.prompt.return_value)[1]
            try:
                with self.assertRaises(OSError):
                    runner.run_once(pi_factory=lambda env: client)
            finally:
                failure.stop()
            self.assertEqual(ModelUsageLedger(root)._store.all()[0]["status"], "pending")
            second = new_runner(root)
            second.submit_quote_review_job(idempotency_key="next")
            factory = Mock(return_value=fake_client())
            self.assertEqual(second.run_once(pi_factory=factory)["outcome"], "budget_unresolved_usage")
            factory.assert_not_called()

    def test_terminated_provider_process_recovers_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "provider-entered"
            script = """
import sys, time
from pathlib import Path
from types import SimpleNamespace
from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.pi_jobs import PiTaskRunner
root = Path(sys.argv[1])
runner = PiTaskRunner(AgentJobStore(root), JsonCommerceRepositories(root), data_dir=root)
runner.start()
runner.submit_quote_review_job()
def prompt(*args, **kwargs):
    (root / 'provider-entered').write_text('ready', encoding='utf-8')
    time.sleep(120)
runner.run_once(pi_factory=lambda env: SimpleNamespace(prompt=prompt, stop=lambda: None))
"""
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(root)],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "DATA_DIR": str(root), "DB_PATH": str(root / "test.db"),
                     "XIANYU_ADAPTER": "stub", "DEEPSEEK_API_KEY": ""},
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            try:
                deadline = time.monotonic() + 20
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), "fake provider entry not reached")
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
            self.assertIsNotNone(process.returncode)
            self.assertEqual(ModelUsageLedger(root)._store.all()[0]["status"], "pending")
            restarted = new_runner(root)
            restarted.submit_quote_review_job(idempotency_key="after-process-death")
            factory = Mock(return_value=fake_client())
            self.assertEqual(restarted.run_once(pi_factory=factory)["outcome"], "budget_unresolved_usage")
            factory.assert_not_called()


class ShadowAmountRecoveryTests(unittest.TestCase):
    def test_every_write_window_preserves_daily_amount_after_two_replays(self):
        for window in ("log", "usage", "cursor"):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                mandates = OperatingMandateStore(root)
                mandates.issue(operator="test", account_id="account", action_types=["price_adjust"],
                               per_transaction_limit={"state": "known", "amount": "500", "currency": "CNY"},
                               daily_limit={"state": "known", "amount": "100", "currency": "CNY"},
                               valid_until=utc_now() + timedelta(days=1))
                drafts = [{"record_id": str(amount), "draft_type": DRAFT_COUNTER_OFFER,
                           "cookie_id": "account", "item_id": "sku", "offer_amount": amount}
                          for amount in (60, 70)]

                def observer():
                    return ShadowObserver(root, draft_service=SimpleNamespace(list_drafts=lambda: drafts),
                                          opportunities=JsonCommerceRepositories(root).opportunities,
                                          mandates=OperatingMandateStore(root), shadow_log=ShadowAuthorizationLog(root))

                first = observer()
                store = {"log": first._log._store, "usage": first._shadow_usage, "cursor": first._cursor}[window]
                with patch.object(store, "_persist", side_effect=OSError(window)), self.assertRaises(OSError):
                    first.run_tick()
                second = observer()
                second.run_tick()
                second.run_tick()
                self.assertEqual(second._log.metrics()["allowed"], 1)
                self.assertEqual(second._log.metrics()["total"], 2)
                usage = second._shadow_usage.all()
                self.assertEqual(len(usage), 1)
                self.assertEqual(float(usage[0]["amount"]["amount"]), 60)

    def test_legacy_allowed_without_amount_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = ShadowAuthorizationLog(root)
            saved = log.record(decision={"allowed": True, "matched_mandate": {"mandate_id": "old"}},
                               action_summary="legacy", event_id="draft:legacy")
            with log._store as store:
                del store._items[0]["allowance_amount"]
            observer = ShadowObserver(root,
                                      draft_service=SimpleNamespace(list_drafts=lambda: [
                                          {"record_id": "legacy", "draft_type": DRAFT_COUNTER_OFFER,
                                           "cookie_id": "account", "item_id": "sku", "offer_amount": 60}]),
                                      opportunities=JsonCommerceRepositories(root).opportunities,
                                      mandates=OperatingMandateStore(root), shadow_log=ShadowAuthorizationLog(root))
            with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                observer.run_tick()
            self.assertEqual(observer._cursor.all(), [])
            self.assertEqual(observer._shadow_usage.all(), [])
            self.assertEqual(observer._log.find_by_event(saved["event_id"])["record_id"], saved["record_id"])


if __name__ == "__main__":
    unittest.main()
