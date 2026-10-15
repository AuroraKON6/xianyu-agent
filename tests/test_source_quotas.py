"""A46 source-quota contract tests: retries count, restarts persist,
unknown limits forbid unattended scans, exhaustion degrades honestly."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xianyu_agent.source_quotas import (
    QuotaExhausted,
    SourceQuotaLedger,
    UnattendedQuotaUnknown,
)

NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)


class SourceQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="quota-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retries_count_as_attempts(self):
        ledger = SourceQuotaLedger(self.tmp, limits={"s1": 3})
        # page fails with 429 then retries: BOTH consume quota
        ledger.check_and_consume("s1", now=NOW)
        ledger.check_and_consume("s1", now=NOW)
        state = ledger.check_and_consume("s1", now=NOW)
        self.assertEqual(state["attempts"], 3)
        self.assertTrue(state["exhausted"])
        with self.assertRaises(QuotaExhausted):
            ledger.check_and_consume("s1", now=NOW)

    def test_counters_survive_restart(self):
        first = SourceQuotaLedger(self.tmp, limits={"s1": 5})
        for _ in range(3):
            first.check_and_consume("s1", now=NOW)
        # "restart": a fresh instance over the same data dir
        second = SourceQuotaLedger(self.tmp, limits={"s1": 5})
        self.assertEqual(second.status("s1", now=NOW)["attempts"], 3)
        second.check_and_consume("s1", now=NOW)
        self.assertEqual(second.status("s1", now=NOW)["attempts"], 4)

    def test_unknown_limit_forbids_unattended_allows_manual(self):
        ledger = SourceQuotaLedger(self.tmp)  # no contract limits at all
        with self.assertRaises(UnattendedQuotaUnknown):
            ledger.check_and_consume("s1", now=NOW)  # unattended scan refused
        state = ledger.check_and_consume("s1", manual=True, now=NOW)  # explicit manual OK
        self.assertEqual(state["attempts"], 1)
        self.assertFalse(state["limit_known"])

    def test_windows_roll_by_day(self):
        ledger = SourceQuotaLedger(self.tmp, limits={"s1": 1})
        ledger.check_and_consume("s1", now=NOW)
        with self.assertRaises(QuotaExhausted):
            ledger.check_and_consume("s1", now=NOW + timedelta(hours=1))
        # next UTC day: a fresh window
        state = ledger.check_and_consume("s1", now=NOW + timedelta(days=1))
        self.assertEqual(state["attempts"], 1)
        self.assertEqual(state["window"], "2026-10-13")

    def test_sources_are_independent(self):
        ledger = SourceQuotaLedger(self.tmp, limits={"s1": 1})
        ledger.check_and_consume("s1", now=NOW)
        state = ledger.check_and_consume("s2", manual=True, now=NOW)  # unknown limit, manual
        self.assertEqual(state["source"], "s2")


if __name__ == "__main__":
    unittest.main()
