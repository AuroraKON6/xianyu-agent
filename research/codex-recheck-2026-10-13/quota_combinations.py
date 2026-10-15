"""Offline expected-invariant probes; red means the invariant is not met."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
NOW = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)


def block_external(event, args):
    if (event.startswith("socket.") and event != "socket.gethostname") or event in {
        "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    }:
        raise AssertionError("Offline quota probes forbid network and child processes")


class Client:
    def __init__(self, steps=None):
        self.calls = 0
        self.steps = list(steps or [])

    def fetch_page(self, cursor):
        self.calls += 1
        if self.steps:
            step = self.steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        return {"items": [], "empty": True, "next_cursor": ""}


class QuotaCombinations(unittest.TestCase):
    observations = []

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="codex-quota-combination-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.repos = Repositories(self.directory)

    def observe(self, **facts):
        self.observations.append({"case": self._testMethodName, **facts})
        print(json.dumps(self.observations[-1], sort_keys=True))

    def test_q1_status_after_restart_without_limits(self):
        first = QuotaLedger(self.directory, limits={SOURCE: 2})
        first.check_and_consume(SOURCE, now=NOW)
        reopened = QuotaLedger(self.directory)
        try:
            state = reopened.status(SOURCE, now=NOW)
        except Exception as error:
            self.observe(error_type=type(error).__name__, error=str(error))
            self.fail("Persisted contract status must be readable after restart: {!r}".format(error))
        self.observe(state=state)
        self.assertEqual((state["attempts"], state["limit"]), (1, 2))

    def test_q2_fresh_sessions_cannot_reuse_a_spent_precharge(self):
        quota = QuotaLedger(self.directory, limits={SOURCE: 1})
        quota.check_and_consume(SOURCE, now=NOW)
        previous_client = Client()
        FetchSession(self.repos, previous_client, now=NOW).fetch()
        self.assertEqual(previous_client.calls, 1)
        clients = [Client() for _ in range(3)]
        for client in clients:
            FetchSession(self.repos, client, now=NOW).fetch()
        state = quota.status(SOURCE, now=NOW)
        self.observe(previous_transport_attempts=previous_client.calls,
                     extra_transport_attempts=sum(c.calls for c in clients), state=state)
        self.assertEqual(sum(c.calls for c in clients), 0,
                         "No verified unspent admission was passed; an exhausted source must not fetch")

    def test_q3_unknown_contract_cannot_fetch_unattended(self):
        client = Client()
        report = FetchSession(self.repos, client, now=NOW, quota_manual=False).fetch()
        self.observe(transport_attempts=client.calls, status=report["status"])
        self.assertEqual(client.calls, 0,
                         "Unknown contract with manual=False must refuse before any transport attempt")

    def test_q4_lowered_limit_survives_restart(self):
        QuotaLedger(self.directory, limits={SOURCE: 5}).check_and_consume(SOURCE, now=NOW)
        tightened = QuotaLedger(self.directory, limits={SOURCE: 2})
        tightened.check_and_consume(SOURCE, now=NOW)
        reopened = QuotaLedger(self.directory)
        admitted = False
        try:
            result = reopened.check_and_consume(SOURCE, now=NOW)
            admitted = True
        except QuotaExhausted as error:
            result = {"rejected": str(error)}
        self.observe(admitted_after_limit_two=result, was_admitted=admitted)
        self.assertFalse(admitted, "Restart must not resurrect the superseded limit of five")

    def test_q5_rate_limit_retry_has_positive_cooldown(self):
        quota = QuotaLedger(self.directory, limits={SOURCE: 5})
        quota.check_and_consume(SOURCE, now=NOW)
        client = Client([RateLimited("429"), {"items": [], "empty": True, "next_cursor": ""}])
        sleeps = []
        report = FetchSession(self.repos, client, now=NOW, sleep=sleeps.append,
                              quota_ledger=quota, page_retries=1).fetch()
        self.observe(transport_attempts=client.calls, sleeps=sleeps, status=report["status"])
        self.assertEqual(client.calls, 2)
        self.assertTrue(sleeps and all(delay > 0 for delay in sleeps),
                        "A 429 cannot be followed by an immediate zero-delay retry")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="all")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    for key in ("SHADOW_OBSERVER_ENABLED", "RADAR_DISPATCHER_ENABLED", "AUTO_REPLY_ENABLED",
                "MARKET_CHANNEL_PROBE_AUTHORIZED", "PI_ENABLED", "PI_RPC_ENABLED"):
        os.environ[key] = "0"
    os.environ["DEEPSEEK_API_KEY"] = ""
    os.environ["MIMO_API_KEY"] = ""
    sys.addaudithook(block_external)
    from xianyu_agent.commerce.repositories import JsonCommerceRepositories as Repositories
    from xianyu_agent.market_channel import (
        ChannelFetchSession as FetchSession, ChannelRateLimited as RateLimited,
        MARKET_CHANNEL_PROVIDER as SOURCE,
    )
    from xianyu_agent.source_quotas import SourceQuotaLedger as QuotaLedger, QuotaExhausted
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(QuotaCombinations) if args.case == "all" else (
        loader.loadTestsFromName(args.case, QuotaCombinations)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.evidence:
        args.evidence.write_text(json.dumps({
            "cases": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "observations": QuotaCombinations.observations,
            "network_and_child_processes": "denied_by_audit_hook",
        }, indent=2), encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() else 1)
