"""Tests for bridge events ledger."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xianyu_agent.bridge_events import BridgeEventLedger


class BridgeEventLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger = BridgeEventLedger(Path(self.tmpdir) / "bridge_events.json")

    def test_record_creates_event(self):
        event = self.ledger.record(
            direction="xianyu_to_ecommerce",
            event_type="review_result",
            candidate_id="rc_001",
            idempotency_key="rc_001_review_2026-06-05",
            payload={"score": 30},
        )
        self.assertEqual(event["direction"], "xianyu_to_ecommerce")
        self.assertEqual(event["candidate_id"], "rc_001")
        self.assertEqual(event["status"], "sent")

    def test_duplicate_returns_existing(self):
        self.ledger.record(
            direction="xianyu_to_ecommerce",
            event_type="review_result",
            candidate_id="rc_001",
            idempotency_key="key1",
        )
        event2 = self.ledger.record(
            direction="xianyu_to_ecommerce",
            event_type="review_result",
            candidate_id="rc_001",
            idempotency_key="key1",
        )
        self.assertEqual(event2["duplicate_count"], 1)

    def test_list_events_filters(self):
        self.ledger.record(direction="xianyu_to_ecommerce", event_type="a", candidate_id="rc_1", idempotency_key="k1")
        self.ledger.record(direction="ecommerce_to_xianyu", event_type="b", candidate_id="rc_2", idempotency_key="k2")
        self.ledger.record(direction="xianyu_to_ecommerce", event_type="a", candidate_id="rc_3", idempotency_key="k3")

        all_events = self.ledger.list_events()
        self.assertEqual(len(all_events), 3)

        filtered = self.ledger.list_events(direction="xianyu_to_ecommerce")
        self.assertEqual(len(filtered), 2)

        filtered2 = self.ledger.list_events(event_type="b")
        self.assertEqual(len(filtered2), 1)

    def test_metrics(self):
        self.ledger.record(direction="xianyu_to_ecommerce", event_type="a", candidate_id="rc_1", idempotency_key="k1")
        self.ledger.record(direction="ecommerce_to_xianyu", event_type="b", candidate_id="rc_2", idempotency_key="k2")
        # Duplicate
        self.ledger.record(direction="xianyu_to_ecommerce", event_type="a", candidate_id="rc_1", idempotency_key="k1")

        m = self.ledger.metrics()
        self.assertEqual(m["total_events"], 2)
        self.assertEqual(m["xianyu_to_ecommerce"], 1)
        self.assertEqual(m["ecommerce_to_xianyu"], 1)
        self.assertEqual(m["duplicates_suppressed"], 1)

    def test_persistence(self):
        path = Path(self.tmpdir) / "persist_test.json"
        ledger1 = BridgeEventLedger(path)
        ledger1.record(direction="d", event_type="t", candidate_id="c", idempotency_key="k1")

        ledger2 = BridgeEventLedger(path)
        events = ledger2.list_events()
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
