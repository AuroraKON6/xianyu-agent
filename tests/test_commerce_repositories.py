"""WP01 repository tests: uniqueness, immutability, revision conflicts."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from xianyu_agent.commerce import contracts
from xianyu_agent.commerce.repositories import (
    DuplicateRecord,
    JsonCommerceRepositories,
    RevisionConflict,
)

UTC = timezone.utc
OBSERVED = datetime(2026, 9, 18, 2, 0, tzinfo=UTC)


def snapshot(**overrides):
    params = dict(
        provider_id="supplier_file:batch001",
        source_item_id="SUP-1001",
        source_sku_id="SUP-1001-A",
        observed_at=OBSERVED,
        payload_hash="hash1",
        eligible_price=contracts.Money.known(Decimal("150")).to_dict(),
        account_id="",
        source_ref="import:batch001",
    )
    params.update(overrides)
    return contracts.build_offer_snapshot(**params)


class OfferRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_immutable_and_idempotent_by_natural_key(self):
        record, created = self.repos.offers.add(snapshot())
        self.assertTrue(created)
        again, created2 = self.repos.offers.add(snapshot(record_id="offer_different_id"))
        self.assertFalse(created2)
        self.assertEqual(again["record_id"], record["record_id"])
        self.assertEqual(len(self.repos.offers.list()), 1)

    def test_new_observation_appends_not_overwrites(self):
        first, _ = self.repos.offers.add(snapshot())
        second, created = self.repos.offers.add(
            snapshot(observed_at=datetime(2026, 9, 19, 2, 0, tzinfo=UTC), payload_hash="hash2")
        )
        self.assertTrue(created)
        self.assertNotEqual(first["record_id"], second["record_id"])
        latest = self.repos.offers.latest(
            provider_id="supplier_file:batch001", source_item_id="SUP-1001", source_sku_id="SUP-1001-A"
        )
        self.assertEqual(latest["record_id"], second["record_id"])

    def test_account_isolation_filter(self):
        self.repos.offers.add(snapshot(account_id="acc-A"))
        self.repos.offers.add(snapshot(account_id="acc-B", payload_hash="hash-b"))
        only_a = self.repos.offers.list(account_id="acc-A")
        self.assertEqual(len(only_a), 1)
        self.assertEqual(only_a[0]["account_id"], "acc-A")
        supplier_only = self.repos.offers.list(account_id="")
        self.assertEqual(len(supplier_only), 0)

    def test_persistence_roundtrip(self):
        self.repos.offers.add(snapshot())
        reopened = JsonCommerceRepositories(Path(self.tmp.name))
        self.assertEqual(len(reopened.offers.list()), 1)


class MatchRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _match(self, grade="EXACT"):
        return contracts.build_product_match(
            left_snapshot_id="offer_left",
            right_snapshot_id="offer_right",
            grade=grade,
            rule_version="hard-v1",
        )

    def test_same_grade_idempotent_new_grade_appends(self):
        first = self.repos.matches.add(self._match())
        same = self.repos.matches.add(self._match())
        self.assertEqual(first["record_id"], same["record_id"])
        revised = self.repos.matches.add(self._match(grade="NOT_MATCH"))
        self.assertNotEqual(first["record_id"], revised["record_id"])
        current = self.repos.matches.latest_for_pair("offer_left", "offer_right")
        self.assertEqual(current["grade"], "NOT_MATCH")
        self.assertEqual(len(self.repos.matches.list()), 2)

    def test_list_filter_by_grade(self):
        self.repos.matches.add(self._match())
        self.repos.matches.add(self._match(grade="NOT_MATCH"))
        self.assertEqual(len(self.repos.matches.list(grade="EXACT")), 1)


class OpportunityRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))
        self.record = self.repos.opportunities.create(
            contracts.build_opportunity(canonical_sku="SKU-1", source="supplier_file:batch001")
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_natural_key_rejected(self):
        with self.assertRaises(DuplicateRecord):
            self.repos.opportunities.create(
                contracts.build_opportunity(canonical_sku="SKU-1", source="supplier_file:batch001")
            )

    def test_transition_with_expected_revision(self):
        updated = self.repos.opportunities.transition(
            self.record["record_id"],
            to_status="researching",
            reason="match attached",
            expected_revision=1,
        )
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(RevisionConflict):
            self.repos.opportunities.transition(
                self.record["record_id"],
                to_status="ready_for_test",
                reason="stale writer",
                expected_revision=1,
            )

    def test_attach_managed_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.repos.opportunities.attach(self.record["record_id"], field="status", value="validated")

    def test_attach_decision_id_appends_once(self):
        rid = self.record["record_id"]
        self.repos.opportunities.attach(rid, field="decision_ids", value="dec_1")
        self.repos.opportunities.attach(rid, field="decision_ids", value="dec_1")
        updated = self.repos.opportunities.get(rid)
        self.assertEqual(updated["decision_ids"], ["dec_1"])

    def test_history_persisted_with_reason(self):
        self.repos.opportunities.transition(
            self.record["record_id"], to_status="rejected", reason="no profit under conservative scenario"
        )
        stored = self.repos.opportunities.get(self.record["record_id"])
        self.assertEqual(stored["status"], "rejected")
        self.assertEqual(stored["status_reason"], "no profit under conservative scenario")
        self.assertEqual(len(stored["history"]), 2)


class DecisionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _record(self, record_id="dec_1"):
        return contracts.build_decision_record(
            summary="price 299 conservative profit ok",
            actor_role="pricing",
            decision_type="opportunity_review",
            opportunity_id="opp_1",
            record_id=record_id,
        )

    def test_append_only(self):
        stored = self.repos.decisions.append(self._record())
        self.assertEqual(stored["record_id"], "dec_1")
        with self.assertRaises(DuplicateRecord):
            self.repos.decisions.append(self._record())

    def test_filter_by_opportunity(self):
        self.repos.decisions.append(self._record())
        self.repos.decisions.append(self._record(record_id="dec_2"))
        self.assertEqual(len(self.repos.decisions.list(opportunity_id="opp_1")), 2)
        self.assertEqual(len(self.repos.decisions.list(opportunity_id="opp_other")), 0)


class FileLayoutTests(unittest.TestCase):
    def test_files_live_under_commerce_directory_and_are_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            repos = JsonCommerceRepositories(Path(tmp))
            repos.offers.add(snapshot())
            repos.opportunities.create(
                contracts.build_opportunity(canonical_sku="S", source="src")
            )
            commerce_dir = Path(tmp) / "commerce"
            # only aggregates that received a write create their file
            names = sorted(p.name for p in commerce_dir.iterdir())
            self.assertEqual(names, ["offers.json", "opportunities.json"])
            for name in names:
                payload = json.loads((commerce_dir / name).read_text(encoding="utf-8"))
                self.assertIsInstance(payload, list)
            # no stray tmp files left behind
            self.assertFalse(list(commerce_dir.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
