"""WP02 provider capability registry tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xianyu_agent.providers import (
    PROVIDER_STATUS_CONFIGURED,
    PROVIDER_STATUS_UNVERIFIED,
    PROVIDER_STATUS_VERIFIED,
    ProviderCapabilityRegistry,
    ProviderError,
    build_capability_record,
    default_capability_seed,
    is_capability_check_stale,
    normalize_provider_id,
)

UTC = timezone.utc


class ProviderIdNormalizationTests(unittest.TestCase):
    def test_aliases_normalize_to_canonical_ids(self):
        self.assertEqual(normalize_provider_id("pdd"), "pinduoduo_duoduojinbao")
        self.assertEqual(normalize_provider_id("PDD "), "pinduoduo_duoduojinbao")
        self.assertEqual(normalize_provider_id("pinduoduo"), "pinduoduo_duoduojinbao")
        self.assertEqual(normalize_provider_id("1688"), "alibaba_1688_open")
        self.assertEqual(normalize_provider_id("闲鱼"), "xianyu_observation")
        self.assertEqual(normalize_provider_id("supplier_file:杭州五金"), "supplier_file:杭州五金")

    def test_empty_rejected(self):
        with self.assertRaises(ProviderError):
            normalize_provider_id("  ")


class CapabilityRecordTests(unittest.TestCase):
    def _record(self, **overrides):
        fields = dict(status=PROVIDER_STATUS_UNVERIFIED, checked_at=datetime.now(UTC))
        fields.update(overrides)
        checked = fields.pop("checked_at")
        return build_capability_record(provider_id="test_provider", checked_at=checked, **fields)

    def test_verified_requires_20_real_samples_and_evidence(self):
        with self.assertRaises(ProviderError) as ctx:
            self._record(status=PROVIDER_STATUS_VERIFIED, real_sample_count=3, raw_evidence_refs=["x"])
        self.assertIn("20 real samples", str(ctx.exception))
        with self.assertRaises(ProviderError):
            self._record(status=PROVIDER_STATUS_VERIFIED, real_sample_count=25, raw_evidence_refs=[])
        with self.assertRaises(ProviderError):
            self._record(status=PROVIDER_STATUS_VERIFIED, real_sample_count=25)  # no refs
        ok = self._record(
            status=PROVIDER_STATUS_VERIFIED,
            real_sample_count=25,
            raw_evidence_refs=["evidence.jsonl#1", "evidence.jsonl#2"],
            cost_basis="official price list 2026-09-01",
        )
        self.assertEqual(ok["status"], PROVIDER_STATUS_VERIFIED)

    def test_unknown_fields_default_to_unknown_not_yes(self):
        record = self._record()
        self.assertEqual(record["quota"], "unknown")
        self.assertEqual(record["freight_supported"], "unknown")

    def test_invalid_status_rejected(self):
        with self.assertRaises(ProviderError):
            self._record(status="PRETTY_SURE_IT_WORKS")

    def test_naive_checked_at_rejected(self):
        with self.assertRaises(ProviderError):
            build_capability_record(
                provider_id="p",
                checked_at=datetime(2026, 9, 18, 12, 0),
                status=PROVIDER_STATUS_UNVERIFIED,
            )

    def test_verified_staleness_window(self):
        fresh = self._record(
            status=PROVIDER_STATUS_VERIFIED,
            real_sample_count=25,
            raw_evidence_refs=["e"],
            cost_basis="dated evidence",
            checked_at=datetime.now(UTC),
        )
        self.assertFalse(is_capability_check_stale(fresh))
        old = dict(fresh)
        old["checked_at"] = (datetime.now(UTC) - timedelta(days=91)).isoformat()
        self.assertTrue(is_capability_check_stale(old))
        # non-VERIFIED records are never stale-flagged by this rule
        unverified = self._record(checked_at=datetime.now(UTC) - timedelta(days=400))
        self.assertFalse(is_capability_check_stale(unverified))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = ProviderCapabilityRegistry(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_reflects_2026_09_18_investigation(self):
        created = self.registry.seed_defaults()
        self.assertEqual(len(created), 5)
        by_id = {r["provider_id"]: r for r in self.registry.list()}
        self.assertEqual(by_id["supplier_file"]["status"], PROVIDER_STATUS_CONFIGURED)
        self.assertEqual(by_id["pinduoduo_duoduojinbao"]["status"], PROVIDER_STATUS_UNVERIFIED)
        self.assertEqual(by_id["alibaba_1688_open"]["status"], PROVIDER_STATUS_UNVERIFIED)
        self.assertEqual(by_id["xianyu_browser_observation"]["status"], PROVIDER_STATUS_UNVERIFIED)
        # no source claims VERIFIED
        self.assertNotIn(PROVIDER_STATUS_VERIFIED, {r["status"] for r in by_id.values()})
        # re-seed is idempotent
        self.assertEqual(len(self.registry.seed_defaults()), 0)

    def test_usable_sources_exclude_unverified(self):
        self.registry.seed_defaults()
        usable = {r["provider_id"] for r in self.registry.usable_sources()}
        self.assertEqual(usable, {"supplier_file", "xianyu_observation"})

    def test_upsert_and_persistence(self):
        self.registry.seed_defaults()
        record = self.registry.get("supplier_file")
        record["status"] = PROVIDER_STATUS_CONFIGURED
        record["next_action"] = "waiting for real supplier file"
        self.registry.upsert(record)
        reopened = ProviderCapabilityRegistry(Path(self.tmp.name))
        self.assertEqual(reopened.get("supplier_file")["next_action"], "waiting for real supplier file")
        self.assertEqual(len(reopened.list()), 5)

    def test_default_seed_builds_without_verified_claims(self):
        # guard: the shipped seed itself satisfies the VERIFIED rule by
        # never claiming it
        records = default_capability_seed(checked_at=datetime.now(UTC))
        for record in records:
            self.assertNotEqual(record["status"], PROVIDER_STATUS_VERIFIED)


if __name__ == "__main__":
    unittest.main()
