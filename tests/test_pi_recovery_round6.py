"""Offline R6 recovery drills; no provider or platform is contacted."""

import hashlib
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
from xianyu_agent.pi_jobs import PiTaskRunner, ModelUsageLedger, decision_validity
from xianyu_agent.reply_drafts import DRAFT_FACT_ANSWER
from xianyu_agent.shadow_observer import ShadowObserver


def make_runner(root):
    runner = PiTaskRunner(AgentJobStore(root), JsonCommerceRepositories(root),
                          data_dir=root, deadline_seconds=86400)
    runner.start()
    return runner


def known_result():
    return {"ok": True, "result": {
        "message": '{"findings": [], "summary": "offline"}',
        "model": "fake", "session_id": "offline", "tool_calls": [],
        "usage": {"input": 10, "output": 5, "totalTokens": 15},
    }}


class AttemptSettlementTests(unittest.TestCase):
    def test_settlement_replay_is_idempotent_after_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1)
            first = ledger.record(day="2026-09-22", job_id="j", attempt=1, total_tokens=15, model="fake")
            for _ in range(2):
                ledger = ModelUsageLedger(root)
                self.assertEqual(ledger.record(day="2026-09-22", job_id="j", attempt=1,
                                               total_tokens=15, model="fake"), first)
                self.assertEqual(ledger.used_today(day="2026-09-22"), 15)
                self.assertEqual(len(ledger.entries_today(day="2026-09-22")), 1)
            self.assertFalse(ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1))

    def test_conflicting_settlement_rejected_and_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1)
            ledger.record(day="2026-09-22", job_id="j", attempt=1, total_tokens=15, model="fake")
            for _ in range(2):
                ledger = ModelUsageLedger(root)
                with self.assertRaisesRegex(ValueError, "conflicting settlement"):
                    ledger.record(day="2026-09-22", job_id="j", attempt=1, total_tokens=99, model="fake")
            entry = ModelUsageLedger(root).entries_today(day="2026-09-22")[0]
            self.assertEqual(entry["total_tokens"], 15)
            self.assertEqual(len(entry["settlement_conflicts"]), 1)
            self.assertEqual(entry["settlement_conflicts"][0]["total_tokens"], 99)

    def test_attempts_are_separate_and_unknown_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1)
            ledger.record(day="2026-09-22", job_id="j", attempt=1, total_tokens=15)
            self.assertTrue(ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=2))
            ledger.record(day="2026-09-22", job_id="j", attempt=2, total_tokens=None)
            ledger = ModelUsageLedger(root)
            ledger.record(day="2026-09-22", job_id="j", attempt=1, total_tokens=15)
            with self.assertRaisesRegex(ValueError, "conflicting settlement"):
                ledger.record(day="2026-09-22", job_id="j", attempt=2, total_tokens=20)
            self.assertFalse(ledger.begin_attempt(day="2026-09-23", job_id="j", attempt=3))
            entries = ModelUsageLedger(root).entries_today(day="2026-09-22")
            self.assertEqual([e["attempt"] for e in entries], [1, 2])
            self.assertEqual([e["total_tokens"] for e in entries], [15, None])

    def test_settlement_cannot_move_attempt_to_another_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1)
            with self.assertRaisesRegex(ValueError, "conflicting settlement"):
                ledger.record(day="2026-09-23", job_id="j", attempt=1, total_tokens=15)
            restarted = ModelUsageLedger(root)
            self.assertIsNone(restarted.entries_today(day="2026-09-22")[0]["total_tokens"])
            self.assertEqual(restarted.entries_today(day="2026-09-23"), [])

    def test_settlement_requires_original_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ModelUsageLedger(Path(directory))
            with self.assertRaisesRegex(ValueError, "attempt not found"):
                ledger.record(day="2026-09-22", job_id="j", attempt=2, total_tokens=15)
            self.assertEqual(ledger.entries_today(day="2026-09-22"), [])

    def test_legacy_known_and_unknown_records_preserved(self):
        for total in (15, None):
            with self.subTest(total=total), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ledger = ModelUsageLedger(root)
                old = {"job_id": "legacy", "business_day": "2026-09-21", "total_tokens": total}
                ledger._store.append(old)
                restarted = ModelUsageLedger(root)
                self.assertEqual(restarted.begin_attempt(day="2026-09-22", job_id="new", attempt=1),
                                 total is not None)
                self.assertEqual(ModelUsageLedger(root).entries_today(day="2026-09-21"), [old])


class RunnerWriteWindowTests(unittest.TestCase):
    def test_intent_write_failure_never_constructs_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = make_runner(root)
            runner.submit_quote_review_job()
            factory = Mock()
            with patch.object(runner.model_usage._store, "_persist", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    runner.run_once(pi_factory=factory)
            factory.assert_not_called()
            self.assertEqual(ModelUsageLedger(root)._store.all(), [])
            self.assertEqual(JsonCommerceRepositories(root).decisions.list(), [])

    def test_result_usage_write_failure_blocks_retry_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = make_runner(root)
            runner.submit_quote_review_job()
            original = runner.model_usage._store._persist
            calls = []

            def fail_settlement():
                calls.append(True)
                if len(calls) == 2:
                    raise OSError("usage write failed")
                original()

            client = SimpleNamespace(prompt=Mock(return_value=known_result()), stop=Mock())
            with patch.object(runner.model_usage._store, "_persist", side_effect=fail_settlement):
                with self.assertRaises(OSError):
                    runner.run_once(pi_factory=lambda env: client)
            client.prompt.assert_called_once()
            client.stop.assert_called_once()
            self.assertEqual(list((root / "commerce" / "pi_evidence").glob("*.json")), [])
            restarted = make_runner(root)
            restarted.reconcile_task_decisions(now=utc_now() + timedelta(hours=1))
            factory = Mock()
            self.assertEqual(restarted.run_once(pi_factory=factory)["outcome"], "budget_unresolved_usage")
            factory.assert_not_called()
            entry = ModelUsageLedger(root)._store.all()[0]
            self.assertEqual(entry["status"], "pending")
            self.assertIsNone(entry["total_tokens"])

    def test_evidence_decision_and_completion_failures_keep_known_cost(self):
        for window in ("evidence", "decision", "completion"):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runner = make_runner(root)
                job = runner.submit_quote_review_job()
                client = SimpleNamespace(prompt=Mock(return_value=known_result()), stop=Mock())
                if window == "evidence":
                    original_write = Path.write_text

                    def fail_evidence(path, *args, **kwargs):
                        if path.parent.name == "pi_evidence":
                            raise OSError("evidence disk full")
                        return original_write(path, *args, **kwargs)

                    fault = patch.object(Path, "write_text", new=fail_evidence)
                elif window == "decision":
                    fault = patch.object(runner._decisions._store, "_persist", side_effect=OSError("decision disk full"))
                else:
                    original = runner._jobs._store._persist

                    def fail_completion():
                        if any(j["status"] == "succeeded" for j in runner._jobs._store._items):
                            raise OSError("completion disk full")
                        original()

                    fault = patch.object(runner._jobs._store, "_persist", side_effect=fail_completion)
                with fault, self.assertRaises(OSError):
                    runner.run_once(pi_factory=lambda env: client)
                client.prompt.assert_called_once()
                restarted = make_runner(root)
                entries = restarted.model_usage._store.all()
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["total_tokens"], 15)
                before = decision_validity(restarted._jobs.get(job["record_id"]), restarted._decisions.list())
                self.assertEqual(before["valid"], [])
                for _ in range(2):
                    restarted.reconcile_task_decisions(now=utc_now() + timedelta(hours=1))
                after = decision_validity(restarted._jobs.get(job["record_id"]), restarted._decisions.list())
                self.assertEqual(len(after["valid"]), 1 if window == "completion" else 0)
                self.assertEqual(restarted.model_usage._store.all(), entries)

    def test_provider_entry_process_kill_keeps_unresolved_intent(self):
        child_code = """
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.pi_jobs import PiTaskRunner
root = Path(sys.argv[1])
runner = PiTaskRunner(AgentJobStore(root), JsonCommerceRepositories(root), data_dir=root, deadline_seconds=86400)
runner.start()
runner.submit_quote_review_job()
def prompt(*args, **kwargs):
    (root / 'provider_entered').write_text('fake-provider', encoding='ascii')
    threading.Event().wait(30)
    raise RuntimeError('parent failed to terminate test worker')
runner.run_once(pi_factory=lambda env: SimpleNamespace(prompt=prompt, stop=lambda: None))
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, DATA_DIR=str(root), DB_PATH=str(root / "test.db"), XIANYU_ADAPTER="stub")
            process = subprocess.Popen(
                [sys.executable, "-c", child_code, str(root)],
                cwd=str(Path(__file__).resolve().parents[1]), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                limit = time.monotonic() + 15
                while not (root / "provider_entered").exists() and process.poll() is None and time.monotonic() < limit:
                    time.sleep(0.02)
                self.assertTrue((root / "provider_entered").exists(), "child did not enter fake provider")
                process.kill()
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
            self.assertNotEqual(process.returncode, 0)
            restarted = make_runner(root)
            entries = restarted.model_usage._store.all()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["attempt"], 1)
            self.assertIsNone(entries[0]["total_tokens"])
            for _ in range(2):
                restarted.reconcile_task_decisions(now=utc_now() + timedelta(hours=1))
            factory = Mock()
            self.assertEqual(restarted.run_once(pi_factory=factory)["outcome"], "budget_unresolved_usage")
            factory.assert_not_called()
            self.assertEqual(ModelUsageLedger(root)._store.all(), entries)
            self.assertEqual(JsonCommerceRepositories(root).decisions.list(), [])


class RecoveryRound6Tests(unittest.TestCase):
    def test_unknown_usage_blocks_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def runner():
                result = PiTaskRunner(AgentJobStore(root), JsonCommerceRepositories(root), data_dir=root)
                result.start()
                return result
            first = runner()
            first.submit_quote_review_job()
            client = SimpleNamespace(prompt=lambda *a, **k: (_ for _ in ()).throw(TimeoutError()), stop=lambda: None)
            first.run_once(pi_factory=lambda env: client)
            second = runner()
            with patch.object(client, "prompt") as prompt:
                self.assertEqual(second.run_once(pi_factory=lambda env: client)["outcome"], "budget_unresolved_usage")
                prompt.assert_not_called()

    def test_crash_intent_survives_next_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            self.assertTrue(ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1))
            restarted = ModelUsageLedger(root)
            self.assertFalse(restarted.begin_attempt(day="2026-09-23", job_id="j2", attempt=1))

    def test_known_result_settles_intent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ModelUsageLedger(Path(directory))
            ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1)
            ledger.record(day="2026-09-22", job_id="j", total_tokens=15)
            self.assertEqual(ledger.used_today(day="2026-09-22"), 15)
            self.assertEqual(len(ledger.entries_today(day="2026-09-22")), 1)
            self.assertTrue(ledger.begin_attempt(day="2026-09-22", job_id="j2", attempt=1))

    def test_settlement_write_failure_preserves_block_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = ModelUsageLedger(root)
            ledger.begin_attempt(day="2026-09-22", job_id="j", attempt=1)
            with patch.object(ledger._store, "_persist", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    ledger.record(day="2026-09-22", job_id="j", total_tokens=15)
            restarted = ModelUsageLedger(root)
            self.assertFalse(restarted.begin_attempt(day="2026-09-23", job_id="j2", attempt=1))
            self.assertIsNone(restarted.entries_today(day="2026-09-22")[0]["total_tokens"])

    def test_all_shadow_write_windows_recover(self):
        for failing_store in ("log", "usage", "cursor"):
            with self.subTest(window=failing_store), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                mandates = OperatingMandateStore(root)
                mandates.issue(operator="test", account_id="a", action_types=["faq_reply"],
                               max_actions=1, valid_until=utc_now() + timedelta(days=1))
                drafts = [{"record_id": str(i), "draft_type": DRAFT_FACT_ANSWER,
                           "cookie_id": "a", "item_id": "sku", "chat_id": "c"} for i in range(2)]
                def observer():
                    return ShadowObserver(root, draft_service=SimpleNamespace(list_drafts=lambda: drafts),
                                          opportunities=JsonCommerceRepositories(root).opportunities,
                                          mandates=OperatingMandateStore(root), shadow_log=ShadowAuthorizationLog(root))
                first = observer()
                store = {"log": first._log._store, "usage": first._shadow_usage, "cursor": first._cursor}[failing_store]
                with patch.object(store, "_persist", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        first.run_tick()
                second = observer()
                second.run_tick()
                second.run_tick()
                self.assertEqual(second._log.metrics()["allowed"], 1)
                self.assertEqual(len(second._shadow_usage.all()), 1)
                self.assertEqual(second._log.metrics()["total"], 2)

    def test_replay_restores_original_day_amount_and_mandate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = ShadowAuthorizationLog(root)
            original = utc_now() - timedelta(days=1)
            amount = {"state": "known", "amount": "60", "currency": "CNY"}
            saved = log.record(decision={"allowed": True, "matched_mandate": {"mandate_id": "old"}},
                               action_summary="test", event_id="event", now=original, allowance_amount=amount)
            observer = ShadowObserver(root, draft_service=None, opportunities=None,
                                      mandates=OperatingMandateStore(root), shadow_log=ShadowAuthorizationLog(root))
            observer._record(event_id="event", action_type="faq_reply", account_id="other", sku="other",
                             summary="changed", amount={"state": "known", "amount": "99", "currency": "CNY"})
            entry = observer._shadow_usage.all()[0]
            self.assertEqual(entry["business_day"], saved["business_day"])
            self.assertEqual(entry["amount"], amount)
            self.assertEqual(entry["mandate_id"], "old")


class ShadowProjectionRecoveryTests(unittest.TestCase):
    def test_saved_verdict_recovers_before_new_event_even_without_source(self):
        for cursor_already_advanced in (False, True):
            with self.subTest(cursor=cursor_already_advanced), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                mandates = OperatingMandateStore(root)
                mandate = mandates.issue(operator="test", account_id="a", action_types=["faq_reply"],
                                         max_actions=1, valid_until=utc_now() + timedelta(days=1))
                log = ShadowAuthorizationLog(root)
                log.record(decision={"allowed": True, "matched_mandate": {"mandate_id": mandate["record_id"]}},
                           action_summary="original removed draft", event_id="draft:old", allowance_amount=None)
                observer = ShadowObserver(root,
                    draft_service=SimpleNamespace(list_drafts=lambda: [{
                        "record_id": "new", "draft_type": DRAFT_FACT_ANSWER,
                        "cookie_id": "a", "item_id": "sku", "chat_id": "c"}]),
                    opportunities=JsonCommerceRepositories(root).opportunities,
                    mandates=OperatingMandateStore(root), shadow_log=ShadowAuthorizationLog(root))
                if cursor_already_advanced:
                    observer._mark_processed("draft", "old")
                observer.run_tick()
                observer.run_tick()
                self.assertEqual(observer._log.metrics()["allowed"], 1)
                self.assertEqual(observer._shadow_usage.all()[0]["event_id"], "draft:old")
                self.assertEqual(len(observer._shadow_usage.all()), 1)

    def test_inconsistent_existing_projection_blocks_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = ShadowAuthorizationLog(root)
            saved = log.record(decision={"allowed": True, "matched_mandate": {"mandate_id": "old"}},
                               action_summary="saved", event_id="draft:old",
                               allowance_amount={"state": "known", "amount": "60", "currency": "CNY"})
            observer = ShadowObserver(root, draft_service=None, opportunities=None,
                                      mandates=OperatingMandateStore(root), shadow_log=log)
            observer._shadow_usage.append({"event_id": "draft:old", "mandate_id": "old",
                                           "business_day": saved["business_day"],
                                           "amount": {"state": "known", "amount": "70", "currency": "CNY"}})
            with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                observer._restore_allowance(saved)
            self.assertEqual(observer._shadow_usage.all()[0]["amount"]["amount"], "70")


class NonemptyDemoChainTests(unittest.TestCase):
    def test_demo_quotes_to_persisted_decision_and_reloaded_evidence(self):
        from xianyu_agent.supplier_import import SupplierImportService

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repos = JsonCommerceRepositories(root)
            importer = SupplierImportService(repos, root)
            quote = (
                "sku,name,price,unit,moq,stock,freight,spec,brand,condition,notes\n"
                "DEMO-1,Demo table,150,pc,1,10,15,120x60x85cm,DEMO,new,source=demo\n"
                "DEMO-2,Demo rack,200,pc,1,5,,120x60x180cm,DEMO,new,source=demo\n"
            ).encode("utf-8")
            preview = importer.preview_bytes(quote, kind="csv", filename="source-demo.csv")
            confirmed = importer.confirm(
                preview["preview_id"], content_sha256=preview["content_sha256"],
                revision=preview["revision"], supplier="DEMO-NOT-A-SUPPLIER", operator="offline-test",
                include_needs_confirmation=True,
            )
            self.assertEqual(confirmed["batch"]["status"], "committed")
            offers_before = repos.offers.list()
            runner = PiTaskRunner(AgentJobStore(root), repos, data_dir=root, import_service=importer)
            runner.start()
            job = runner.submit_quote_review_job()
            received = {}

            def prompt(message, context):
                received.update(context["inputs"])
                self.assertEqual(received["committed_offer_count"], 2)
                for offer in received["offers"]:
                    self.assertEqual(offer["attributes"]["notes"], "source=demo")
                result = known_result()
                result["result"]["message"] = json.dumps({
                    "findings": [{"sku": "DEMO-2", "field": "freight", "severity": "block",
                                  "note": "Demo freight unknown; request supplier confirmation."}],
                    "summary": "source=demo; offline fake model; not commercial evidence",
                })
                return result

            client = SimpleNamespace(prompt=Mock(side_effect=prompt), stop=Mock())
            outcome = runner.run_once(pi_factory=lambda env: client)
            self.assertEqual(outcome["outcome"], "succeeded")
            self.assertEqual(repos.offers.list(), offers_before)
            self.assertEqual(repos.opportunities.list(), [])
            restarted_repos = JsonCommerceRepositories(root)
            restarted_job = AgentJobStore(root).get(job["record_id"])
            valid = decision_validity(restarted_job, restarted_repos.decisions.list())["valid"]
            self.assertEqual(len(valid), 1)
            decision = valid[0]
            evidence = json.loads(Path(decision["input_versions"]["evidence_file"]).read_text(encoding="utf-8"))
            self.assertEqual(evidence["inputs"], received)
            self.assertEqual(decision["input_versions"]["inputs_sha256"], hashlib.sha256(
                json.dumps(received, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
            self.assertEqual(evidence["usage"]["totalTokens"], 15)
            self.assertEqual(decision["result"]["findings"][0]["sku"], "DEMO-2")
            client.prompt.assert_called_once()
            client.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
