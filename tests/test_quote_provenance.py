"""Source declarations are fixture facts, never proof of a real supplier."""
import csv
import io
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.budget_reservations import ShadowAuthorizationLog
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import build_offer_snapshot, build_product_match
from xianyu_agent.inventory import InventoryAllocationService, InventoryError
from xianyu_agent.operating_mandates import OperatingMandateStore
from xianyu_agent.opportunity_service import OpportunityService
from xianyu_agent.pi_jobs import PiTaskRunner
from xianyu_agent.providers.freshness import classify_snapshot
from xianyu_agent.providers.supplier_file import SupplierFileProvider
from xianyu_agent.shadow_observer import ShadowObserver
from xianyu_agent.supplier_import import SupplierImportService

UTC = timezone.utc


def money(value):
    return {"state": "known", "amount": str(value), "currency": "CNY"}


class QuoteProvenanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.now = datetime.now(UTC)
        self.repos = JsonCommerceRepositories(self.root)
        self.importer = SupplierImportService(self.repos, self.root)
        self.inventory = InventoryAllocationService(self.root, safety_margin=0)

    def imported(self, **changes):
        row = dict(sku="FIXTURE-1", name="Fixture table", price="100", currency="CNY", unit="pc",
                   moq="1", stock="10", freight="10", spec="120x60x85cm", condition="new",
                   source_kind="supplier_original", source_reference="fixture-quote-reference",
                   source_observed_at=(self.now - timedelta(minutes=5)).isoformat(),
                   valid_until=(self.now + timedelta(hours=1)).isoformat())
        row.update(changes)
        content = io.StringIO()
        writer = csv.DictWriter(content, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        preview = self.importer.preview_bytes(content.getvalue().encode(), kind="csv")
        result = self.importer.confirm(preview["preview_id"], content_sha256=preview["content_sha256"],
                                       revision=1, supplier="isolated-source-fixture", operator="fixture",
                                       include_needs_confirmation=True)
        return result["snapshots"][0], result["batch"]

    def opportunity(self, supplier):
        market, _ = self.repos.offers.add(build_offer_snapshot(
            provider_id="market:xianyu_observation", source_item_id="FIXTURE-1", observed_at=self.now,
            payload_hash="fixture-market", eligible_price=money(300), unit="pc", source_ref="fixture-market"))
        match = self.repos.matches.add(build_product_match(left_snapshot_id=market["record_id"],
                    right_snapshot_id=supplier["record_id"], grade="EXACT", rule_version="fixture"))
        service = OpportunityService(self.repos)
        opportunity = service.create_from_match(market_snapshot_id=market["record_id"],
                            supplier_snapshot_id=supplier["record_id"], match_id=match["record_id"])
        with patch("xianyu_agent.opportunity_service.utc_now", return_value=datetime.now(UTC)):
            result = service.evaluate(opportunity["record_id"], platform_fee_rate="0", platform_fee_fixed="0",
                                      packaging=money(0), after_sales_reserve=money(0), attributable_costs=money(0),
                                      expected_return_loss=money(0))
        return service, result

    def test_actual_observation_and_import_time_are_separate_and_reloadable(self):
        snapshot, batch = self.imported()
        expected = (self.now - timedelta(minutes=5)).isoformat()
        self.assertEqual(snapshot["observed_at"], expected)
        self.assertNotEqual(snapshot["fetched_at"], expected)
        self.assertEqual(snapshot["expires_at"], (self.now + timedelta(hours=1)).isoformat())
        saved = JsonCommerceRepositories(self.root).offers.get(snapshot["record_id"])
        self.assertEqual(saved, snapshot)
        self.assertEqual(saved["attributes"]["quote_provenance"]["source_kind"], "supplier_original")
        self.assertEqual(self.importer.batch_status(batch["record_id"]), "committed")

    def test_pasted_csv_timestamps_are_not_misread_as_key_value_pairs(self):
        from quote_fixtures import declared_quote_csv
        text = declared_quote_csv(b'sku,price,currency,unit,stock,moq,freight\nPASTE-1,"1,500.25",CNY,pc,10,1,0\n').decode()
        preview = self.importer.preview_pasted_text(text)
        self.assertEqual(preview["summary"]["ok"], 1)
        self.assertEqual(preview["rows"][0]["fields"]["price"]["amount"], "1500.25")
        result = self.importer.confirm(preview["preview_id"], content_sha256=preview["content_sha256"],
                                       revision=1, supplier="pasted-fixture", operator="fixture")
        self.assertEqual(classify_snapshot(result["snapshots"][0])["freshness"], "current")

    def test_old_quote_does_not_become_current_when_imported_today(self):
        snapshot, _ = self.imported(source_observed_at=(self.now - timedelta(hours=30)).isoformat())
        self.assertEqual(classify_snapshot(snapshot)["freshness"], "expired")
        self.assertEqual(SupplierFileProvider(self.repos, self.importer).fetch_offers()["records"], [])

    def test_missing_future_naive_and_expired_facts_never_supply_inventory(self):
        cases = [{"source_observed_at": ""}, {"source_observed_at": self.now.replace(tzinfo=None).isoformat()},
                 {"source_observed_at": (self.now + timedelta(minutes=5)).isoformat()},
                 {"valid_until": (self.now - timedelta(minutes=1)).isoformat()}, {"valid_until": ""},
                 {"source_kind": "unknown"}, {"source_reference": ""}]
        for values in cases:
            with self.subTest(values=values):
                snapshot, _ = self.imported(**values)
                self.assertNotEqual(classify_snapshot(snapshot)["freshness"], "current")
                self.inventory.register_supplier_snapshot(account_id="fixture-account", sku="FIXTURE-1", snapshot=snapshot)
                self.assertEqual(self.inventory.allocation(account_id="fixture-account", sku="FIXTURE-1")["available"], 0)

    def test_demo_marker_cannot_be_overridden_by_original_label(self):
        for fields in ({"source_kind": "demo"}, {"source_kind": "supplier_original", "notes": "source=demo"}):
            snapshot, _ = self.imported(**fields)
            self.assertNotEqual(classify_snapshot(snapshot)["freshness"], "current")
            self.assertEqual(classify_snapshot(snapshot)["source_kind"], "demo")

    def test_legacy_snapshot_without_provenance_is_not_grandfathered(self):
        snapshot, _ = self.imported()
        snapshot["attributes"].pop("quote_provenance", None)
        self.assertNotEqual(classify_snapshot(snapshot)["freshness"], "current")

    def test_metadata_and_snapshot_timestamps_must_agree(self):
        snapshot, _ = self.imported()
        snapshot["observed_at"] = self.now.isoformat()
        self.assertNotEqual(classify_snapshot(snapshot)["freshness"], "current")

    def test_expiry_rechecked_on_inventory_read_lock_and_restart(self):
        snapshot, _ = self.imported(valid_until=(self.now + timedelta(minutes=1)).isoformat())
        self.inventory.register_supplier_snapshot(account_id="a", sku="FIXTURE-1", snapshot=snapshot)
        self.assertEqual(self.inventory.allocation(account_id="a", sku="FIXTURE-1")["available"], 10)
        later = self.now + timedelta(minutes=2)
        restarted = InventoryAllocationService(self.root, safety_margin=0)
        allocation = restarted.allocation(account_id="a", sku="FIXTURE-1", now=later)
        self.assertEqual(allocation["available"], 0)
        self.assertEqual(allocation["freshness"]["freshness"], "expired")
        with self.assertRaises(InventoryError):
            restarted.lock_for_order(account_id="a", sku="FIXTURE-1", order_id="test", quantity=1, now=later)

    def test_qualified_opportunity_pauses_after_quote_expiry(self):
        snapshot, _ = self.imported(valid_until=(self.now + timedelta(minutes=1)).isoformat())
        service, first = self.opportunity(snapshot)
        self.assertTrue(first["qualification"]["qualified"])
        with patch("xianyu_agent.opportunity_service.utc_now", return_value=self.now + timedelta(minutes=2)):
            second = service.evaluate(first["opportunity"]["record_id"], platform_fee_rate="0", platform_fee_fixed="0",
                     packaging=money(0), after_sales_reserve=money(0), attributable_costs=money(0), expected_return_loss=money(0))
        self.assertFalse(second["qualification"]["qualified"])
        self.assertEqual(second["opportunity"]["status"], "paused")

    def test_demo_profit_cannot_qualify_an_opportunity(self):
        snapshot, _ = self.imported(source_kind="demo")
        _, result = self.opportunity(snapshot)
        self.assertFalse(result["qualification"]["qualified"])

    def test_pi_inputs_expose_provenance_and_ineligibility_without_hiding_quotes(self):
        snapshot, _ = self.imported(source_kind="demo")
        runner = PiTaskRunner(AgentJobStore(self.root), self.repos, data_dir=self.root, import_service=self.importer)
        inputs = runner._collect_quote_inputs()
        self.assertEqual(inputs["committed_offer_count"], 1)
        self.assertEqual(inputs["business_eligible_offer_count"], 0)
        self.assertEqual(inputs["offers"][0]["freshness_meta"]["source_kind"], "demo")
        self.assertEqual(inputs["offers"][0]["record_id"], snapshot["record_id"])

    def test_shadow_rechecks_current_source_and_revocation_before_counting(self):
        snapshot, batch = self.imported()
        _, result = self.opportunity(snapshot)
        class Drafts:
            def list_drafts(self):
                return []
        log = ShadowAuthorizationLog(self.root)
        observer = ShadowObserver(self.root, draft_service=Drafts(), opportunities=self.repos.opportunities,
                    mandates=OperatingMandateStore(self.root), shadow_log=log,
                    offers=self.repos.offers, import_service=self.importer)
        self.importer.revoke_batch(batch["record_id"], operator="fixture", reason="fixture withdrawal")
        observer.run_tick()
        self.assertEqual(log.metrics()["total"], 1)
        self.assertEqual(log.metrics()["real_source_samples"], 0)
        self.assertFalse(log.list_events()[0]["provenance"]["eligible"])
        observer.run_tick()
        self.assertEqual(log.metrics()["total"], 1)

    def test_demo_and_unattributed_shadow_records_never_satisfy_trial_count(self):
        log = ShadowAuthorizationLog(self.root)
        for index in range(105):
            log.record(decision={"allowed": False}, action_summary="fixture",
                       event_id=f"opportunity:{index}", now=self.now - timedelta(days=index % 7))
        metrics = ShadowAuthorizationLog(self.root).metrics()
        self.assertEqual(metrics["total"], 105)
        self.assertEqual(metrics["real_source_samples"], 0)
        self.assertEqual(metrics["real_source_days"], 0)
        self.assertFalse(metrics["trial_sample_gate_met"])

    def test_current_original_quote_counts_once_without_authorizing_actions(self):
        snapshot, _ = self.imported(source_kind="real")
        self.opportunity(snapshot)
        class Drafts:
            def list_drafts(self):
                return []
        def observer():
            repos = JsonCommerceRepositories(self.root)
            return ShadowObserver(self.root, draft_service=Drafts(), opportunities=repos.opportunities,
                       mandates=OperatingMandateStore(self.root), shadow_log=ShadowAuthorizationLog(self.root),
                       offers=repos.offers, import_service=SupplierImportService(repos, self.root))
        observer().run_tick()
        observer().run_tick()
        log = ShadowAuthorizationLog(self.root)
        self.assertEqual(log.metrics()["total"], 1)
        self.assertEqual(log.metrics()["real_source_samples"], 1)
        self.assertEqual(log.metrics()["real_source_days"], 1)
        self.assertFalse(log.metrics()["autonomy_authorized"])
        self.assertFalse(log.list_events()[0]["provenance"]["provider_verified"])

    def test_shadow_cursor_failure_replay_keeps_original_provenance(self):
        snapshot, batch = self.imported()
        self.opportunity(snapshot)
        class Drafts:
            def list_drafts(self):
                return []
        log = ShadowAuthorizationLog(self.root)
        observer = ShadowObserver(self.root, draft_service=Drafts(), opportunities=self.repos.opportunities,
                    mandates=OperatingMandateStore(self.root), shadow_log=log,
                    offers=self.repos.offers, import_service=self.importer)
        with patch.object(observer._cursor, "_persist", side_effect=OSError("fixture cursor write failure")):
            with self.assertRaises(OSError):
                observer.run_tick()
        before = copy.deepcopy(log.list_events())
        self.importer.revoke_batch(batch["record_id"], operator="fixture", reason="later withdrawal")
        observer.run_tick()
        self.assertEqual(log.list_events(), before)
        self.assertEqual(log.metrics()["real_source_samples"], 1)  # Historical eligibility, not current release.

    def test_aware_offsets_and_xlsx_fields_reach_the_same_gate(self):
        from test_supplier_import import build_minimal_xlsx
        local = timezone(timedelta(hours=8))
        observed = (self.now - timedelta(minutes=5)).astimezone(local).isoformat()
        snapshot, _ = self.imported(source_observed_at=observed)
        self.assertEqual(classify_snapshot(snapshot)["freshness"], "current")
        payload = build_minimal_xlsx([
            ["sku", "price", "currency", "unit", "moq", "stock", "freight",
             "source_kind", "source_reference", "source_observed_at", "valid_until"],
            ["XLSX-1", 100, "CNY", "pc", 1, 10, 10, "supplier_original",
             "xlsx-fixture", observed, (self.now + timedelta(hours=1)).isoformat()]])
        preview = self.importer.preview_bytes(payload, kind="xlsx")
        result = self.importer.confirm(preview["preview_id"], content_sha256=preview["content_sha256"],
                                       revision=1, supplier="xlsx-fixture", operator="fixture")
        facts = classify_snapshot(result["snapshots"][0])
        self.assertEqual(facts["freshness"], "current")
        self.assertEqual(facts["source_observed_at"], observed)

    def test_reimport_identical_bytes_does_not_refresh_or_duplicate_snapshot(self):
        original, batch = self.imported(source_observed_at=(self.now - timedelta(hours=30)).isoformat())
        replay, replay_batch = self.imported(source_observed_at=(self.now - timedelta(hours=30)).isoformat())
        self.assertEqual(original, replay)
        self.assertEqual(batch, replay_batch)
        self.assertEqual(len(self.repos.offers.list()), 1)
        self.assertEqual(classify_snapshot(replay)["freshness"], "expired")

    def test_expiry_boundary_and_timestamp_overflow_fail_closed(self):
        snapshot, _ = self.imported(valid_until=(self.now + timedelta(minutes=1)).isoformat())
        boundary = self.now + timedelta(minutes=1)
        self.assertEqual(classify_snapshot(snapshot, now=boundary)["freshness"], "expired")
        for value in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"):
            with self.subTest(value=value):
                snapshot, _ = self.imported(source_observed_at=value)
                self.assertNotEqual(classify_snapshot(snapshot)["freshness"], "current")

    def test_legacy_inventory_projection_does_not_gain_source_by_reload(self):
        snapshot, _ = self.imported()
        record = self.inventory.register_supplier_snapshot(account_id="a", sku="FIXTURE-1", snapshot=snapshot)
        with self.inventory._store as store:
            store._items[0].pop("supplier_time_provenance")
        restarted = InventoryAllocationService(self.root, safety_margin=0)
        self.assertEqual(restarted.allocation(account_id="a", sku="FIXTURE-1")["available"], 0)
        self.assertEqual(record["sellable_base"], 10)

    def test_shadow_count_threshold_uses_only_bound_original_samples(self):
        snapshot, _ = self.imported()
        self.opportunity(snapshot)
        class Drafts:
            def list_drafts(self):
                return []
        log = ShadowAuthorizationLog(self.root)
        observer = ShadowObserver(self.root, draft_service=Drafts(), opportunities=self.repos.opportunities,
                    mandates=OperatingMandateStore(self.root), shadow_log=log,
                    offers=self.repos.offers, import_service=self.importer)
        observer.run_tick()
        facts = log.list_events()[0]["provenance"]
        for index in range(99):
            log.record(decision={"allowed": False}, action_summary="fixture only", event_id=f"opportunity:fixture-{index}",
                       provenance=facts, now=self.now - timedelta(days=index % 7))
        metrics = log.metrics()
        self.assertEqual(metrics["real_source_samples"], 100)
        self.assertEqual(metrics["real_source_days"], 7)
        self.assertTrue(metrics["trial_sample_gate_met"])
        self.assertFalse(metrics["autonomy_authorized"])
        log.record(decision={"allowed": False}, action_summary="demo fixture", event_id="opportunity:demo",
                   provenance=dict(facts, source_kind="demo"))
        log.record(decision={"allowed": False}, action_summary="preview fixture", provenance=facts)
        self.assertEqual(log.metrics()["real_source_samples"], 100)
        self.assertEqual(log.metrics()["excluded_source_samples"], 2)


if __name__ == "__main__":
    unittest.main()
