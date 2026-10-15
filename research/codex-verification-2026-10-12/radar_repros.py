"""Review evidence only: expected invariants, intentionally red on unfixed code.

No expectedFailure decorators: a reproduced defect must fail normally.
Business modules, documentation, existing tests and live data are read-only.
Missing upstream time alone is NOT a defect; it has a passing control case.
"""
from __future__ import annotations

import argparse
import importlib
import itertools
import os
import socket
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


NOW = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = Path(__file__).with_name("radar-repros.log")
sys.dont_write_bytecode = True

SANDBOX_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "BROWSER_ADAPTER": "stub",
    "ACTION_ADAPTER": "stub",
    "PI_ENABLED": "0",
    "PI_RPC_ENABLED": "0",
    "RADAR_DISPATCHER_ENABLED": "0",
    "MARKET_CHANNEL_CONFIGURED": "0",
    "MARKET_CHANNEL_PROBE_AUTHORIZED": "0",
}


def deny_external_action(*_args, **_kwargs):
    raise AssertionError("Review sandbox forbids network and external processes")


def external_action_audit(event, _args):
    if event.startswith("socket.") or event in {
        "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    }:
        deny_external_action()


def configure_environment(data_dir):
    for key, value in SANDBOX_ENV.items():
        os.environ[key] = value
    os.environ["DATA_DIR"] = str(data_dir)


def import_review_modules():
    # Deliberately do not import server, settings, intake, Pi or an adapter.
    sys.path.insert(0, str(REPO_ROOT))
    names = {
        "jobs": "agent_jobs",
        "radar": "radar_dispatch",
        "quotas": "source_quotas",
        "market": "market_channel",
        "contracts": "commerce.contracts",
        "repos": "commerce.repositories",
        "opportunities": "opportunity_service",
        "freshness": "providers.freshness",
    }
    return SimpleNamespace(**{
        key: importlib.import_module("xianyu_agent." + name)
        for key, name in names.items()
    })


class ScriptedClient:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def fetch_page(self, cursor):
        self.calls.append(cursor)
        if not self.steps:
            raise AssertionError("Unexpected extra stub request")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class RadarInvariantRepros(unittest.TestCase):
    modules = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="codex-radar-case-")
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        configure_environment(self.data_dir)
        self.m = self.modules
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (
            self.m.jobs, self.m.radar, self.m.market,
            self.m.contracts, self.m.opportunities,
        ):
            stack.enter_context(patch.object(module, "utc_now", return_value=NOW))
        ids = itertools.count(1)
        stack.enter_context(patch.object(
            self.m.contracts, "new_id",
            side_effect=lambda prefix: "{}_repro_{:04d}".format(prefix, next(ids)),
        ))

    def repositories(self):
        return self.m.repos.JsonCommerceRepositories(self.data_dir)

    def market_item(self, **overrides):
        raw = {
            "product_id": "p1", "title": "fixture", "price": "299",
            "item_time": NOW.isoformat(),
        }
        raw.update(overrides)
        return raw

    def snapshot(self, repos, *, provider, item_id, price, tag, observed=NOW):
        snapshot = self.m.contracts.build_offer_snapshot(
            provider_id=provider, source_item_id=item_id, source_sku_id="SKU-R1",
            observed_at=observed, fetched_at=observed,
            expires_at=observed + timedelta(days=1), unit="\u4ef6", condition="new",
            attributes={
                "name": "fixture", "title": "fixture",
                "spec": "120x60x85cm", "brand": "fixture",
            },
            price_type="supplier_confirmed" if provider.startswith("supplier") else "display",
            eligible_price=self.m.contracts.Money.known(price).to_dict(),
            shipping_quote=self.m.contracts.Money.known("20").to_dict(),
            stock_state="unknown", stock_quantity=None,
            payload_hash=tag, source_ref="fixture:radar-review",
        )
        return repos.offers.add(snapshot)[0]

    def simple_dispatcher(self):
        jobs = self.m.jobs.AgentJobStore(self.data_dir)
        events = self.m.radar.RadarEventStore(self.data_dir)
        repos = SimpleNamespace(offers=SimpleNamespace(list=lambda: []))
        executor = self.m.radar.RadarExecutor(repos, matching=None, opportunities=None)
        executor._run_match = lambda _job: "match-fixture"
        dispatcher = self.m.radar.RadarDispatcher(jobs, events, executor)
        return dispatcher, events, jobs, executor

    def test_a41_new_quote_uses_new_supplier_snapshot(self):
        """radar_dispatch.py:287; opportunity_service.py:88: new quote must replace evaluation inputs."""
        repos = self.repositories()
        dispatcher, events, jobs = self.m.radar.build_radar(repos, self.data_dir)
        old = self.snapshot(repos, provider="supplier_file:fixture", item_id="SKU-R1",
                            price="160", tag="supplier-v1")
        market = self.snapshot(repos, provider="market:xianyu_observation", item_id="mkt-R1",
                               price="300", tag="market-v1")
        events.record(kind="intake_confirmed", scope_sku="SKU-R1",
                      supplier_snapshot_id=old["record_id"],
                      market_snapshot_id=market["record_id"], input_digest="v1")
        for _ in range(5):
            dispatcher.tick(now=NOW)
        new = self.snapshot(repos, provider="supplier_file:fixture", item_id="SKU-R1",
                            price="180", tag="supplier-v2", observed=NOW + timedelta(seconds=1))
        event = events.record(kind="intake_confirmed", scope_sku="SKU-R1",
                              supplier_snapshot_id=new["record_id"],
                              market_snapshot_id=market["record_id"], input_digest="v2")
        for _ in range(5):
            dispatcher.tick(now=NOW)
        card = repos.opportunities.list()[0]
        match_job = next(job for job in jobs.list(role=self.m.radar.RADAR_ROLE_MATCH)
                         if job["goal_id"] == event["record_id"])
        stored_event = next(row for row in events.list() if row["record_id"] == event["record_id"])
        self.assertEqual(stored_event["status"], "processed")
        self.assertEqual(match_job["input_refs"][0], new["record_id"])
        actual = {
            "supplier_refs": card["offer_snapshot_ids"],
            "economics_supplier": card["economics"]["inputs"]["supplier_snapshot_id"],
        }
        self.assertEqual(actual, {
            "supplier_refs": [new["record_id"]], "economics_supplier": new["record_id"],
        }, "New quote was matched, but the processed card still evaluates old inputs")

    def test_a41_implicit_supplier_selection_uses_latest_version(self):
        """radar_dispatch.py:477: implicit selection must use latest supplier version."""
        repos = self.repositories()
        old = self.snapshot(repos, provider="supplier_file:fixture", item_id="SKU-R1",
                            price="160", tag="v1")
        new = self.snapshot(repos, provider="supplier_file:fixture", item_id="SKU-R1",
                            price="180", tag="v2", observed=NOW + timedelta(seconds=1))
        dispatcher, _events, _jobs = self.m.radar.build_radar(repos, self.data_dir)
        selected = dispatcher._latest_supplier_snapshot("SKU-R1")
        self.assertEqual(selected["record_id"], new["record_id"],
                         "Selected first/old snapshot {} instead of latest {}".format(
                             old["record_id"], new["record_id"]))

    def test_a41_concurrent_job_submission_is_idempotent(self):
        """agent_jobs.py:99/123: inherited check-append race must not create duplicate jobs."""
        jobs = self.m.jobs.AgentJobStore(self.data_dir)
        barrier = threading.Barrier(2)
        find = jobs._store.find

        def synchronized_find(predicate):
            rows = find(predicate)
            barrier.wait(timeout=5)
            return rows

        with patch.object(jobs._store, "find", synchronized_find):
            with ThreadPoolExecutor(max_workers=2) as workers:
                rows = list(workers.map(
                    lambda _i: jobs.submit(role=self.m.radar.RADAR_ROLE_MATCH,
                                           idempotency_key="same-input"), range(2)))
        durable = self.m.jobs.AgentJobStore(self.data_dir).list()
        self.assertEqual((len(durable), len({row["record_id"] for row in rows})), (1, 1),
                         "Same key admitted two jobs after synchronized empty lookups")

    def test_a42_radar_claim_excludes_write_required_jobs(self):
        """agent_jobs.py:158: an analysis role must not admit a write-required envelope."""
        jobs = self.m.jobs.AgentJobStore(self.data_dir)
        try:
            jobs.submit(role=self.m.radar.RADAR_ROLE_MATCH, account_id="fixture-account",
                        write_required=True, input_refs=["supplier-fixture"])
        except (self.m.jobs.AgentJobError, self.m.contracts.ContractError):
            return  # Rejecting inconsistent submission is also a valid boundary.
        claimed = jobs.claim_next(worker_id="radar-worker", roles=self.m.radar.RADAR_ROLES, now=NOW)
        actual = None if claimed is None else {
            "role": claimed["role"], "write_required": claimed["write_required"],
            "status": claimed["status"],
        }
        self.assertIsNone(actual, "Radar must not lease a platform-write-required job: {!r}".format(actual))

    def test_a43_stop_boundary_prevents_background_claim(self):
        """radar_dispatch.py:335/358: a background tick already entered must honor stop."""
        dispatcher, _events, jobs, executor = self.simple_dispatcher()
        job = jobs.submit(role=self.m.radar.RADAR_ROLE_MATCH, input_refs=["supplier-fixture"])
        entered, gate = threading.Event(), threading.Event()
        executions = []
        original_tick = dispatcher.tick

        def run(_job):
            executions.append(dispatcher._stopped)
            return "match-fixture"

        def controlled_tick():
            entered.set()
            if not gate.wait(timeout=5):
                raise AssertionError("Background scheduling gate timed out")
            return original_tick(now=NOW)

        executor._run_match = run
        thread = None
        try:
            with patch.object(dispatcher, "tick", controlled_tick):
                self.assertTrue(dispatcher.start(interval_seconds=1))
                self.assertTrue(entered.wait(timeout=5), "Background tick did not enter")
                thread = dispatcher._thread
                join = thread.join

                def release_after_stop(timeout=None):
                    self.assertTrue(dispatcher._stopped)
                    gate.set()
                    join(timeout=timeout)

                with patch.object(thread, "join", release_after_stop):
                    dispatcher.stop()
        finally:
            gate.set()
            dispatcher.stop()
            if thread is not None:
                thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "Review must not leave a worker running")
        self.assertEqual({"executions": executions, "job_status": jobs.get(job["record_id"])["status"]},
                         {"executions": [], "job_status": "queued"},
                         "Background claimed/executed after stop set its boundary")

    def test_a44_completion_persistence_failure_is_visible_and_recoverable(self):
        """radar_dispatch.py:247/428/455: exhausted save failures must not strand a silent event."""
        dispatcher, events, jobs, _executor = self.simple_dispatcher()
        events.record(kind="manual", scope_sku="SKU", supplier_snapshot_id="supplier-fixture",
                      market_snapshot_id="market-fixture")
        complete = jobs.complete
        failures = []

        def failed_complete(*args, **kwargs):
            with patch.object(jobs._store, "_persist", side_effect=OSError("injected disk full")):
                return complete(*args, **kwargs)

        with patch.object(jobs, "complete", failed_complete):
            for attempt in range(3):
                try:
                    dispatcher.tick(now=NOW + timedelta(seconds=301 * attempt))
                except OSError as exc:
                    failures.append(str(exc))
        dispatcher.tick(now=NOW + timedelta(seconds=903))
        durable_jobs = self.m.jobs.AgentJobStore(self.data_dir).list()
        durable_events = self.m.radar.RadarEventStore(self.data_dir).list()
        job, event = durable_jobs[0], durable_events[0]
        actual = {"job_status": job["status"], "job_error": job["error"],
                  "event_status": event["status"], "event_note": event["note"],
                  "save_failures": failures}
        self.assertFalse(job["status"] == "timed_out" and event["status"] == "pending",
                         "Terminal job is invisible to event recovery: {!r}".format(actual))

    def test_a46_concurrent_quota_admission_is_atomic(self):
        """source_quotas.py:101/110: limit one must admit exactly one concurrent attempt."""
        ledger = self.m.quotas.SourceQuotaLedger(self.data_dir, limits={"fixture-source": 1})
        original = ledger.status
        barrier, local = threading.Barrier(2), threading.local()

        def synchronized_status(source, **kwargs):
            state = original(source, **kwargs)
            if not getattr(local, "initial_check_done", False):
                local.initial_check_done = True
                barrier.wait(timeout=5)
            return state

        def consume(_index):
            try:
                ledger.check_and_consume("fixture-source", now=NOW)
                return "admitted"
            except self.m.quotas.QuotaExhausted:
                return "denied"

        with patch.object(ledger, "status", synchronized_status):
            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes = list(workers.map(consume, range(2)))
        state = ledger.status("fixture-source", now=NOW)
        actual = {"admitted": outcomes.count("admitted"), "denied": outcomes.count("denied"),
                  "attempts": state["attempts"], "remaining": state["remaining"]}
        self.assertEqual(actual, {"admitted": 1, "denied": 1, "attempts": 1, "remaining": 0})

    def test_a46_fetch_retries_do_not_bypass_quota(self):
        """market_channel.py:448/456: a precharged last unit must not authorize an unmetered retry."""
        source = self.m.market.MARKET_CHANNEL_PROVIDER
        ledger = self.m.quotas.SourceQuotaLedger(self.data_dir, limits={source: 1})
        ledger.check_and_consume(source, now=NOW)  # Caller precharges the first request only.
        client = ScriptedClient([
            self.m.market.ChannelRateLimited("429"),
            {"items": [], "empty": True, "next_cursor": ""},
        ])
        sleeps = []
        report = self.m.market.ChannelFetchSession(
            self.repositories(), client, page_retries=1, sleep=sleeps.append, now=NOW,
        ).fetch()
        state = ledger.status(source, now=NOW)
        actual = {"requests": len(client.calls), "charged_attempts": state["attempts"],
                  "status": report["status"], "failed_pages": report["failed_pages"], "sleeps": sleeps}
        self.assertLessEqual(len(client.calls), 1,
                             "Retry must be blocked when the last contract unit is spent: {!r}".format(actual))
        self.assertEqual(state["attempts"], len(client.calls))

    def test_a46_quota_persistence_failure_rolls_back(self):
        """PASS control: a failed quota save must not leak an in-memory consumed unit."""
        ledger = self.m.quotas.SourceQuotaLedger(self.data_dir, limits={"fixture-source": 1})
        with patch.object(ledger._store, "_persist", side_effect=OSError("injected disk full")):
            with self.assertRaises(OSError):
                ledger.check_and_consume("fixture-source", now=NOW)
        self.assertEqual(ledger.status("fixture-source", now=NOW)["attempts"], 0)
        reopened = self.m.quotas.SourceQuotaLedger(self.data_dir, limits={"fixture-source": 1})
        self.assertEqual(reopened.status("fixture-source", now=NOW)["attempts"], 0)

    def test_a50_non_mapping_probe_returns_structured_failure(self):
        """market_channel.py:126: malformed probe data must be a structured unavailable result."""
        client = SimpleNamespace(probe=lambda: ["invalid"])
        try:
            result = self.m.market.probe_channel(client, channel_configured=True)
        except Exception as exc:
            self.fail("Expected structured unavailable/protocol_error, got {}: {}".format(
                type(exc).__name__, exc))
        self.assertEqual((result["status"], result["error_kind"]), ("unavailable", "protocol_error"))

    def test_a51_conditional_price_text_is_not_full_price(self):
        """market_channel.py:167: numeric fields cannot erase deposit/coupon conditions in price text."""
        inputs = [("50", "50\u5b9a\u91d1", "deposit"), ("269", "\u5238\u540e269", "post_coupon")]
        actual = []
        for price, text, _kind in inputs:
            result = self.m.market.classify_price(full_price=price, raw_text=text)
            actual.append((result["kind"], result["money"].state))
        self.assertEqual(actual, [(kind, "unknown") for _price, _text, kind in inputs],
                         "Deposit and coupon text must not become known full payable prices")

    def test_a51_a52_quantity_change_preserves_new_payload(self):
        """market_channel.py:245: changed quantity must survive immutable snapshot deduplication."""
        repos = self.repositories()
        records = []
        for quantity in ("1", "10"):
            normalized = self.m.market.normalize_market_item(
                self.market_item(quantity=quantity), fetched_at=NOW)
            stored, created = repos.offers.add(normalized["snapshot"])
            records.append((created, stored["attributes"]["market_channel"]["quantity_raw"]))
        self.assertEqual(records, [(True, "1"), (True, "10")],
                         "Quantity is missing from the payload hash; changed source evidence was discarded")

    def test_a53_existing_snapshot_is_deduped_and_resumable(self):
        """market_channel.py:416: encountering an already-stored item must still update seen_ids."""
        repos = self.repositories()
        raw = self.market_item()
        repos.offers.add(self.m.market.normalize_market_item(raw, fetched_at=NOW)["snapshot"])
        client = ScriptedClient([
            {"items": [raw], "next_cursor": "c2"},
            {"items": [raw], "next_cursor": "c3"},
        ])
        report = self.m.market.ChannelFetchSession(repos, client, max_pages=2, now=NOW).fetch()
        actual = {"results": len(report["snapshot_ids"]), "duplicates": report["duplicates_skipped"],
                  "resume": report["resume_token"]}
        self.assertEqual(actual, {"results": 1, "duplicates": 1,
                                  "resume": {"cursor": "c3", "seen_ids": ["p1"], "keyword": ""}})

    def test_a53_malformed_page_is_not_successful_empty(self):
        """market_channel.py:393: missing page fields must not masquerade as a successful empty page."""
        client = ScriptedClient([{}])
        try:
            report = self.m.market.ChannelFetchSession(self.repositories(), client, now=NOW).fetch()
        except self.m.market.MarketChannelError:
            return  # Structured protocol rejection is also a valid outcome.
        self.assertNotEqual(report["status"], "complete",
                            "Empty mapping became complete with failed_pages={!r}".format(report["failed_pages"]))
        self.assertTrue(report["failed_pages"], "Malformed response needs visible protocol evidence")

    def test_a54_future_upstream_time_is_not_silently_current(self):
        """market_channel.py:218/223: future source time must be rejected or quarantined, not refreshed."""
        future = NOW + timedelta(days=10)
        raw = self.market_item(item_time=future.isoformat())
        observations = []
        for fetched in (NOW, NOW + timedelta(days=5)):
            try:
                normalized = self.m.market.normalize_market_item(raw, fetched_at=fetched)
            except self.m.market.MarketChannelError:
                continue  # Explicit rejection does not silently launder source time.
            classification = self.m.freshness.classify_snapshot(normalized["snapshot"], now=fetched)
            observations.append({
                "upstream": future.isoformat(), "fetched": fetched.isoformat(),
                "observed": normalized["snapshot"]["observed_at"],
                "basis": normalized["observed_time_basis"],
                "freshness": classification["freshness"],
                "business_eligible": classification["business_eligible"],
            })
        self.assertFalse(any(row["freshness"] == "current" or row["business_eligible"]
                             for row in observations),
                         "Future time silently became a usable current observation: {!r}".format(observations))

    def test_a54_missing_upstream_time_can_be_current(self):
        """PASS control: missing source time alone can represent a legitimate new current observation."""
        raw = {"product_id": "p1", "price": "299"}
        for fetched in (NOW, NOW + timedelta(days=5)):
            normalized = self.m.market.normalize_market_item(raw, fetched_at=fetched)
            classification = self.m.freshness.classify_snapshot(normalized["snapshot"], now=fetched)
            self.assertEqual(normalized["snapshot"]["observed_at"], fetched.isoformat())
            self.assertEqual(classification["freshness"], "current")
            self.assertTrue(classification["business_eligible"])


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    loader = unittest.TestLoader()
    names = loader.getTestCaseNames(RadarInvariantRepros)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="all", choices=["all"] + names,
                        help="Run all cases or one stable unittest method name")
    parser.add_argument("--log", action="store_true",
                        help="Write this run to radar-repros.log in UTF-8; without it, stdout only")
    args = parser.parse_args()
    sys.addaudithook(external_action_audit)
    with ExitStack() as sandbox:
        sandbox.enter_context(patch("socket.gethostname", return_value="review-stub"))
        for target in (
            "socket.create_connection", "socket.getaddrinfo", "socket.socket.connect",
            "socket.socket.connect_ex", "socket.socket.send", "socket.socket.sendall",
            "socket.socket.sendto",
        ):
            sandbox.enter_context(patch(target, side_effect=deny_external_action))
        bootstrap = sandbox.enter_context(tempfile.TemporaryDirectory(prefix="codex-radar-bootstrap-"))
        configure_environment(bootstrap)
        RadarInvariantRepros.modules = import_review_modules()
        log = sandbox.enter_context(LOG_PATH.open("w", encoding="utf-8", newline="\n")) if args.log else None
        stream = Tee(sys.stdout, log) if log is not None else sys.stdout
        stream.write("REVIEW_ONLY; TEMP_DATA_DIR_PER_CASE; STUB_ONLY; UTF8; NETWORK_AND_EXTERNAL_PROCESSES_DENIED\n")
        selected = names if args.case == "all" else [args.case]
        stream.write("SELECTED_CASES={}\n".format(len(selected)))
        suite = unittest.TestSuite(RadarInvariantRepros(name) for name in selected)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        exit_code = 0 if result.wasSuccessful() else 1
        stream.write("SUMMARY cases={} failures={} errors={} skipped={} exit={}\n".format(
            result.testsRun, len(result.failures), len(result.errors), len(result.skipped), exit_code))
        stream.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
