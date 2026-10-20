"""R08 acceptance tests: quota admission, persistence and retry cooldown.

Ports the 2026-10-20 review's five red probes (Q1-Q5) verbatim plus the
taskbook's additional cases: two processes racing for the last admission,
a lost transport response still being counted, and permission revocation
closing the source across restarts.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from xianyu_agent.commerce.repositories import JsonCommerceRepositories
from xianyu_agent.market_channel import (
    MARKET_CHANNEL_PROVIDER as SOURCE,
    ChannelFetchSession,
    ChannelRateLimited,
    ChannelServerError,
)
from xianyu_agent.source_quotas import (
    QuotaExhausted,
    SourceQuotaLedger,
    UnattendedQuotaUnknown,
)

NOW = datetime(2026, 10, 13, 9, 0, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


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
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r08-quota-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.repos = JsonCommerceRepositories(self.directory)

    def test_q1_status_after_restart_without_limits(self):
        first = SourceQuotaLedger(self.directory, limits={SOURCE: 2})
        first.check_and_consume(SOURCE, now=NOW)
        reopened = SourceQuotaLedger(self.directory)
        state = reopened.status(SOURCE, now=NOW)
        self.assertEqual((state["attempts"], state["limit"]), (1, 2))

    def test_q2_fresh_sessions_cannot_reuse_a_spent_precharge(self):
        quota = SourceQuotaLedger(self.directory, limits={SOURCE: 1})
        quota.check_and_consume(SOURCE, now=NOW)
        previous_client = Client()
        FetchSession = ChannelFetchSession
        FetchSession(self.repos, previous_client, now=NOW).fetch()
        self.assertEqual(previous_client.calls, 1)
        clients = [Client() for _ in range(3)]
        for client in clients:
            FetchSession(self.repos, client, now=NOW).fetch()
        state = quota.status(SOURCE, now=NOW)
        self.assertEqual(sum(c.calls for c in clients), 0,
                         "No verified unspent admission was passed; an exhausted source must not fetch")

    def test_q3_unknown_contract_cannot_fetch_unattended(self):
        client = Client()
        report = ChannelFetchSession(self.repos, client, now=NOW, quota_manual=False).fetch()
        self.assertEqual(client.calls, 0,
                         "Unknown contract with manual=False must refuse before any transport attempt")
        self.assertEqual(report["status"], "quota_unknown")

    def test_q4_lowered_limit_survives_restart(self):
        SourceQuotaLedger(self.directory, limits={SOURCE: 5}).check_and_consume(SOURCE, now=NOW)
        tightened = SourceQuotaLedger(self.directory, limits={SOURCE: 2})
        tightened.check_and_consume(SOURCE, now=NOW)
        reopened = SourceQuotaLedger(self.directory)
        with self.assertRaises(QuotaExhausted):
            reopened.check_and_consume(SOURCE, now=NOW)

    def test_q5_rate_limit_retry_has_positive_cooldown(self):
        quota = SourceQuotaLedger(self.directory, limits={SOURCE: 5})
        quota.check_and_consume(SOURCE, now=NOW)
        client = Client([ChannelRateLimited("429"), {"items": [], "empty": True, "next_cursor": ""}])
        sleeps = []
        report = ChannelFetchSession(
            self.repos, client, now=NOW, sleep=sleeps.append,
            quota_ledger=quota, page_retries=1).fetch()
        self.assertEqual(client.calls, 2)
        self.assertTrue(sleeps and all(delay > 0 for delay in sleeps),
                        "A 429 cannot be followed by an immediate zero-delay retry")
        self.assertEqual(report["status"], "complete")
        # the precharge counted the first request, the metered retry the
        # second: retries always count
        self.assertEqual(quota.status(SOURCE, now=NOW)["attempts"], 2)


class AdditionalQuotaAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r08-extra-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_two_processes_racing_for_last_admission_exactly_one_wins(self):
        child_code = "\n".join([
            "import sys",
            "sys.path.insert(0, %r)" % str(PROJECT_ROOT),
            "from datetime import datetime, timezone",
            "from xianyu_agent.source_quotas import SourceQuotaLedger",
            "now = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)",
            "ledger = SourceQuotaLedger(%r)" % str(self.directory),
            "ledger.check_and_consume('race-source', now=now)",
            "print('ADMITTED')",
        ])
        # seed the window with 1 of 2 used
        SourceQuotaLedger(self.directory, limits={"race-source": 2}).check_and_consume(
            "race-source", now=NOW
        )
        python = str(VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("XIANYU_TEST_DOTENV", None)
        processes = [
            subprocess.Popen(
                [python, "-c", child_code],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
            for _ in range(2)
        ]
        results = []
        for process in processes:
            out, err = process.communicate(timeout=120)
            results.append((process.returncode, out.strip(), err.strip()[-300:]))
        admitted = [r for r in results if r[0] == 0 and "ADMITTED" in r[1]]
        refused = [r for r in results if r[0] != 0]
        self.assertEqual(len(admitted), 1, "exactly one process must win the last admission: %r" % (results,))
        self.assertEqual(len(refused), 1, "the loser must fail closed (QuotaExhausted): %r" % (results,))
        state = SourceQuotaLedger(self.directory).status("race-source", now=NOW)
        self.assertEqual(state["attempts"], 2)  # no over-admission, no lost update

    def test_lost_transport_response_still_counts_the_attempt(self):
        repos = JsonCommerceRepositories(self.directory)
        quota = SourceQuotaLedger(self.directory, limits={SOURCE: 1})
        quota.check_and_consume(SOURCE, now=NOW)  # precharge (1/1 used, ticket issued)
        client = Client([ChannelServerError("connection reset mid-response")])
        report = ChannelFetchSession(
            repos, client, now=NOW, quota_ledger=quota, page_retries=0
        ).fetch()
        self.assertEqual(client.calls, 1)
        self.assertEqual(report["status"], "degraded")
        # the precharge was claimed by the (lost) attempt; nothing resets it
        state = quota.status(SOURCE, now=NOW)
        self.assertEqual(state["attempts"], 1)
        self.assertTrue(state["exhausted"])
        follow_up = Client()
        ChannelFetchSession(repos, follow_up, now=NOW, quota_ledger=quota).fetch()
        self.assertEqual(follow_up.calls, 0, "a lost response consumed the admission; no free retry")

    def test_revoked_permission_closes_the_source_across_restart(self):
        ledger = SourceQuotaLedger(self.directory, limits={SOURCE: 5})
        ledger.check_and_consume(SOURCE, now=NOW)
        ledger.revoke(SOURCE, now=NOW)
        reopened = SourceQuotaLedger(self.directory)
        with self.assertRaises(QuotaExhausted):
            reopened.check_and_consume(SOURCE, now=NOW)
        with self.assertRaises(QuotaExhausted):
            reopened.check_and_consume(SOURCE, manual=True, now=NOW)
        client = Client()
        report = ChannelFetchSession(
            JsonCommerceRepositories(self.directory), client, now=NOW,
            quota_ledger=reopened, quota_manual=False,
        ).fetch()
        self.assertEqual(client.calls, 0)
        # revoked = managed with limit 0: refused as quota_exhausted
        self.assertEqual(report["status"], "degraded")
        self.assertIn("quota_exhausted", str(report["failed_pages"]))


if __name__ == "__main__":
    unittest.main()
