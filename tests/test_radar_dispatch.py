"""G2A radar dispatcher contract tests (A40-A44, A47).

Synthetic fixtures only; no model calls, no platform access. Restart is
simulated by rebuilding every store from the same data directory.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from xianyu_agent.agent_jobs import JOB_AWAITING_INPUT, JOB_SUCCEEDED, AgentJobStore
from xianyu_agent.commerce.contracts import (
    MATCH_EXACT,
    Money,
    build_offer_snapshot,
    utc_now,
)
from xianyu_agent.commerce.repositories import JsonCommerceRepositories
from xianyu_agent.radar_dispatch import (
    EVENT_PROCESSED,
    RADAR_ROLE_MATCH,
    RADAR_ROLES,
    RadarEventStore,
    RadarExecutor,
    RadarDispatcher,
    build_radar,
)
from xianyu_agent.opportunity_service import OpportunityService
from xianyu_agent.product_matching import MATCH_RULE_VERSION, ProductMatchingService

from decimal import Decimal


def _supplier_snapshot(repos, sku="SKU-R1", price="160"):
    observed = utc_now()
    stored, _ = repos.offers.add(
        build_offer_snapshot(
            provider_id="supplier_file:杭州五金",
            source_item_id=sku,
            source_sku_id=sku,
            observed_at=observed,
            fetched_at=observed,
            expires_at=observed + timedelta(hours=24),
            unit="件",
            quantity=None,
            condition="全新",
            attributes={"name": f"商品{sku}", "quote_provenance": {
                "schema_version": 1, "source_kind": "supplier_original",
                "source_reference": "fixture:radar",
                "source_observed_at": observed.isoformat(),
                "valid_until": (observed + timedelta(hours=24)).isoformat(),
            }},
            price_type="supplier_confirmed",
            eligible_price=Money.known(Decimal(price)).to_dict(),
            shipping_quote=Money.known(Decimal("20")).to_dict(),
            stock_state="in_stock", stock_quantity=50, moq=1,
            tier_prices=[], tax_mode="unknown", seller_ref="杭州五金供应商",
            payload_hash=f"fixture:radar:{sku}:{price}:{observed.isoformat()}",
            account_id="", source_ref="import_batch:impb_fixture",
        )
    )
    return stored


def _market_snapshot(repos, sku="SKU-R1", price="300"):
    observed = utc_now()
    stored, _ = repos.offers.add(
        build_offer_snapshot(
            provider_id="market:xianyu_observation",
            source_item_id=f"mkt-{sku}",
            source_sku_id=sku,
            observed_at=observed,
            fetched_at=observed,
            expires_at=observed + timedelta(hours=72),
            unit="件",
            quantity=None,
            condition="全新",
            attributes={"title": f"市场观察 {sku}"},
            price_type="display",
            eligible_price=Money.known(Decimal(price)).to_dict(),
            shipping_quote=Money.unknown("not observed").to_dict(),
            stock_state="unknown", stock_quantity=None, moq=None,
            tier_prices=[], tax_mode="unknown", seller_ref="",
            payload_hash=f"fixture:radar-market:{sku}:{price}:{observed.isoformat()}",
            account_id="", source_ref="observation:fixture",
        )
    )
    return stored


class RadarDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="radar-"))
        self.repos = JsonCommerceRepositories(self.tmp)
        self.jobs = AgentJobStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dispatcher(self, **kwargs):
        dispatcher, events, jobs = build_radar(self.repos, self.tmp, jobs=self.jobs, **kwargs)
        return dispatcher, events

    # ------------------------------------------------------------------- A40

    def test_a40_event_advances_to_opportunity_card(self):
        supplier = _supplier_snapshot(self.repos)
        market = _market_snapshot(self.repos)
        dispatcher, events = self._dispatcher()
        event = events.record(
            kind="intake_confirmed",
            scope_sku="SKU-R1",
            supplier_snapshot_id=supplier["record_id"],
            market_snapshot_id=market["record_id"],
        )
        # tick 1: submit match stage; executor runs it in the same tick
        report = dispatcher.tick()
        self.assertEqual(report["events_advanced"], 1)
        # tick 2..n: advance until processed
        for _ in range(5):
            report = dispatcher.tick()
            stored_event = events.list()[0]
            if stored_event["status"] == EVENT_PROCESSED:
                break
        stored_event = events.list()[0]
        self.assertEqual(stored_event["status"], EVENT_PROCESSED)
        opportunities = self.repos.opportunities.list()
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0]["status"], "researching")
        match_jobs = [j for j in self.jobs.list() if j.get("role") == RADAR_ROLE_MATCH]
        self.assertEqual(len(match_jobs), 1)
        self.assertEqual(match_jobs[0]["status"], JOB_SUCCEEDED)

    def test_a40_missing_market_parks_event_awaiting_input(self):
        supplier = _supplier_snapshot(self.repos, sku="SKU-NOMKT")
        dispatcher, events = self._dispatcher()
        events.record(
            kind="intake_confirmed", scope_sku="SKU-NOMKT",
            supplier_snapshot_id=supplier["record_id"],
        )
        for _ in range(4):
            dispatcher.tick()
        stored_event = events.list()[0]
        self.assertEqual(stored_event["status"], "awaiting_input")
        self.assertIn("no market observation", stored_event["note"])
        job = self.jobs.list(role=RADAR_ROLE_MATCH)[0]
        self.assertEqual(job["status"], JOB_AWAITING_INPUT)

    # ------------------------------------------------------------------- A41

    def test_a41_duplicate_events_do_not_duplicate_jobs(self):
        supplier = _supplier_snapshot(self.repos, sku="SKU-DUP")
        market = _market_snapshot(self.repos, sku="SKU-DUP")
        dispatcher, events = self._dispatcher()
        first = events.record(
            kind="intake_confirmed", scope_sku="SKU-DUP",
            supplier_snapshot_id=supplier["record_id"],
            market_snapshot_id=market["record_id"],
            input_digest="digest-v1",
        )
        second = events.record(
            kind="intake_confirmed", scope_sku="SKU-DUP",
            supplier_snapshot_id=supplier["record_id"],
            market_snapshot_id=market["record_id"],
            input_digest="digest-v1",
        )
        self.assertEqual(first["record_id"], second["record_id"])  # idempotent event
        for _ in range(6):
            dispatcher.tick()
        self.assertEqual(len(events.list()), 1)
        self.assertEqual(len(self.jobs.list()), 2)  # exactly one match + one opportunity job
        self.assertEqual(len(self.repos.opportunities.list()), 1)

        # a NEW input version creates a new event and a new evaluation, not a duplicate
        newer = _supplier_snapshot(self.repos, sku="SKU-DUP", price="180")
        third = events.record(
            kind="intake_confirmed", scope_sku="SKU-DUP",
            supplier_snapshot_id=newer["record_id"],
            market_snapshot_id=market["record_id"],
            input_digest="digest-v2",
        )
        self.assertNotEqual(third["record_id"], first["record_id"])
        for _ in range(6):
            dispatcher.tick()
        self.assertEqual(len(self.repos.opportunities.list()), 1)  # natural key dedupes the card

    # ------------------------------------------------------------------- A42

    def test_a42_role_filtered_claims(self):
        supplier = _supplier_snapshot(self.repos, sku="SKU-ROLE")
        market = _market_snapshot(self.repos, sku="SKU-ROLE")
        dispatcher, events = self._dispatcher()
        events.record(
            kind="intake_confirmed", scope_sku="SKU-ROLE",
            supplier_snapshot_id=supplier["record_id"],
            market_snapshot_id=market["record_id"],
        )
        pi_job = self.jobs.submit(role="supplier_quote_gap_review", idempotency_key="pi-job-1")
        # submit the radar stage WITHOUT executing it, so both a radar job
        # and a Pi analysis job sit queued in the shared store
        dispatcher._advance_event(events.list()[0])
        # an unfiltered worker (legacy semantics) can still claim anything
        legacy = self.jobs.claim_next(worker_id="legacy-worker")
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy["role"], RADAR_ROLE_MATCH)  # priority 3 beats default 5
        # release it again by completing
        self.jobs.complete(
            legacy["record_id"], worker_id="legacy-worker",
            fencing_token=legacy["fencing_token"], result_ref="match:fixture",
        )
        # the radar executor refuses the Pi analysis job and platform writes
        radar_claim = self.jobs.claim_next(worker_id="radar-worker", roles=RADAR_ROLES)
        while radar_claim is not None:
            self.assertNotEqual(radar_claim["role"], "supplier_quote_gap_review")
            self.assertFalse(radar_claim.get("write_required"))
            self.jobs.complete(
                radar_claim["record_id"], worker_id="radar-worker",
                fencing_token=radar_claim["fencing_token"], result_ref="x",
            )
            radar_claim = self.jobs.claim_next(worker_id="radar-worker", roles=RADAR_ROLES)
        # the Pi job is still queued, untouched by radar workers
        self.assertEqual(self.jobs.get(pi_job["record_id"])["status"], "queued")

    # ------------------------------------------------------------------- A43

    def test_a43_single_dispatcher_instance_and_stop_boundary(self):
        dispatcher, events = self._dispatcher()
        self.assertTrue(dispatcher.start(interval_seconds=1))
        self.assertFalse(dispatcher.start(interval_seconds=1))  # no second instance
        self.assertTrue(dispatcher.status()["background_thread_alive"])
        dispatcher.stop()
        self.assertFalse(dispatcher.status()["background_thread_alive"])
        # after stop the manual tick still works
        report = dispatcher.tick()
        self.assertIn("tick_at", report)

    # ------------------------------------------------------------------- A44

    def test_a44_resume_from_persisted_state_after_restart(self):
        supplier = _supplier_snapshot(self.repos, sku="SKU-RS")
        market = _market_snapshot(self.repos, sku="SKU-RS")
        dispatcher1, events1 = self._dispatcher()
        events1.record(
            kind="intake_confirmed", scope_sku="SKU-RS",
            supplier_snapshot_id=supplier["record_id"],
            market_snapshot_id=market["record_id"],
        )
        dispatcher1.tick()  # match stage submitted+executed
        # "crash": rebuild everything from disk (no in-memory carry-over)
        dispatcher2, events2 = self._dispatcher()
        for _ in range(5):
            dispatcher2.tick()
        self.assertEqual(events2.list()[0]["status"], EVENT_PROCESSED)
        self.assertEqual(len(self.repos.opportunities.list()), 1)
        self.assertEqual(len(self.jobs.list()), 2)

    # ------------------------------------------------------------------- A47

    def test_a47_inputs_scoped_to_event_object(self):
        supplier_a = _supplier_snapshot(self.repos, sku="SKU-A47")
        _supplier_snapshot(self.repos, sku="SKU-OTHER")
        market = _market_snapshot(self.repos, sku="SKU-A47")
        _market_snapshot(self.repos, sku="SKU-OTHER")
        dispatcher, events = self._dispatcher()
        events.record(
            kind="intake_confirmed", scope_sku="SKU-A47",
            supplier_snapshot_id=supplier_a["record_id"],
            market_snapshot_id=market["record_id"],
        )
        for _ in range(6):
            dispatcher.tick()
        match_job = self.jobs.list(role=RADAR_ROLE_MATCH)[0]
        self.assertEqual(
            match_job["input_refs"],
            [supplier_a["record_id"], market["record_id"]],
        )
        match = self.repos.matches.list()[0]
        self.assertEqual(
            {match["left_snapshot_id"], match["right_snapshot_id"]},
            {market["record_id"], supplier_a["record_id"]},
        )

    # ----------------------------------------------------------- Pi gating

    def test_a48_reminders_dedupe_and_resolve(self):
        supplier = _supplier_snapshot(self.repos, sku="SKU-REM")
        dispatcher, events = self._dispatcher()
        events.record(
            kind="intake_confirmed", scope_sku="SKU-REM",
            supplier_snapshot_id=supplier["record_id"],
        )
        # no market observation yet: repeated ticks must produce ONE
        # reminder row whose repeat_count grows, not duplicate rows
        for _ in range(4):
            dispatcher.tick()
        stored_event = events.list()[0]
        self.assertEqual(stored_event["status"], "awaiting_input")
        status = dispatcher.status()
        self.assertEqual(len(status["open_reminders"]), 1)
        reminder = status["open_reminders"][0]
        self.assertEqual(reminder["sku"], "SKU-REM")
        self.assertEqual(reminder["repeat_count"], 0)  # first occurrence
        # a later re-scan of the same cause bumps the SAME row (dedup), not a new one
        events.update(stored_event["record_id"], lambda e: e.update(status="pending"))
        dispatcher.tick()
        dispatcher.tick()
        status = dispatcher.status()
        self.assertEqual(len(status["open_reminders"]), 1)
        self.assertGreaterEqual(status["open_reminders"][0]["repeat_count"], 1)

        # a new market observation starts a NEW event chain that can
        # process; its success resolves the SKU's open reminder
        market = _market_snapshot(self.repos, sku="SKU-REM")
        supplier = _supplier_snapshot(self.repos, sku="SKU-REM", price="160")
        events.record(
            kind="market_observation", scope_sku="SKU-REM",
            supplier_snapshot_id=supplier["record_id"],
            market_snapshot_id=market["record_id"],
            input_digest="digest-with-market",
            idempotency_key="radar-reminder-resolution",
        )
        for _ in range(8):
            dispatcher.tick()
        stored = [e for e in events.list() if e.get("idempotency_key") == "radar-reminder-resolution"][0]
        self.assertEqual(stored["status"], EVENT_PROCESSED)
        status = dispatcher.status()
        self.assertEqual(len(status["open_reminders"]), 0)
        # the ledger still holds the resolved row for audit
        resolved = [r for r in dispatcher._reminders._store.all()
                    if r.get("status") == "resolved"]
        self.assertEqual(len(resolved), 1)

    def test_pi_review_only_with_permitted_use(self):
        submitted = []
        supplier = _supplier_snapshot(self.repos, sku="SKU-PI")
        market = _market_snapshot(self.repos, sku="SKU-PI")
        dispatcher, events = self._dispatcher(
            eligibility_lookup=lambda sid: {"eligible": False, "reasons": ["no_active_confirmation"]},
            pi_submission=lambda opportunity_id, key: submitted.append((opportunity_id, key)),
        )
        events.record(
            kind="intake_confirmed", scope_sku="SKU-PI",
            supplier_snapshot_id=supplier["record_id"],
            market_snapshot_id=market["record_id"],
        )
        for _ in range(6):
            dispatcher.tick()
        stored_event = events.list()[0]
        self.assertEqual(stored_event["status"], EVENT_PROCESSED)
        self.assertIn("external-model use not permitted", stored_event["note"])
        self.assertEqual(submitted, [])  # unapproved input never leaves for a model

        permitted, events_p = self._dispatcher(
            eligibility_lookup=lambda sid: {"eligible": True, "reasons": []},
            pi_submission=lambda opportunity_id, key: submitted.append((opportunity_id, key)),
        )
        supplier2 = _supplier_snapshot(self.repos, sku="SKU-PI2")
        market2 = _market_snapshot(self.repos, sku="SKU-PI2")
        events_p.record(
            kind="intake_confirmed", scope_sku="SKU-PI2",
            supplier_snapshot_id=supplier2["record_id"],
            market_snapshot_id=market2["record_id"],
        )
        for _ in range(6):
            permitted.tick()
        self.assertEqual(len(submitted), 1)
        pi2_event = [e for e in events_p.list() if e["scope_sku"] == "SKU-PI2"][0]
        self.assertEqual(pi2_event["status"], EVENT_PROCESSED)
        self.assertIn("Pi review submitted", pi2_event["note"])


if __name__ == "__main__":
    unittest.main()
