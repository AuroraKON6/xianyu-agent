"""WP06 unit economics and opportunity service tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from quote_fixtures import with_declared_quote_provenance

from xianyu_agent.commerce import (
    MATCH_COMPARABLE,
    MATCH_EXACT,
    MATCH_NOT_MATCH,
    JsonCommerceRepositories,
    build_offer_snapshot,
    build_product_match,
)
from xianyu_agent.opportunity_service import OpportunityError, OpportunityService
from xianyu_agent.product_matching import ProductMatchingService
from xianyu_agent.unit_economics import (
    EconomicsError,
    SCENARIO_BASE,
    SCENARIO_CONSERVATIVE,
    SCENARIO_OPTIMISTIC,
    compute_return_loss,
    compute_unit_economics,
)

UTC = timezone.utc


def money(amount, currency="CNY"):
    return {"state": "known", "amount": str(amount), "currency": currency}


class UnitEconomicsTests(unittest.TestCase):
    def test_golden_example_hand_verifiable(self):
        # master plan 8.3 demo: sale 299, procurement 150, supplier freight 15,
        # packaging 5, platform fee 3% (=8.97), return loss 12, attributable 4
        # -> contribution 104.03, margin 34.79%
        result = compute_unit_economics(
            sale_price=money("299"),
            procurement_cost=money("150"),
            supplier_freight=money("15"),
            packaging=money("5"),
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            expected_return_loss=money("12"),
            attributable_costs=money("4"),
        )
        base = result["scenarios"][SCENARIO_BASE]
        self.assertEqual(base["contribution_profit"]["state"], "known")
        self.assertEqual(Decimal(base["contribution_profit"]["amount"]), Decimal("104.03"))
        self.assertEqual(Decimal(base["contribution_margin"]), Decimal("0.3479"))
        # hand-check: break-even with 3% fee = (150+15+5+12+4)/(1-0.03) = 191.752...
        self.assertEqual(Decimal(base["break_even_price"]["amount"]), Decimal("191.7525773195876288659793814"))

    def test_unknown_freight_keeps_profit_unknown_never_zero(self):
        result = compute_unit_economics(
            sale_price=money("299"),
            procurement_cost=money("150"),
            supplier_freight=None,  # e.g. 到付 or not quoted
            packaging=money("5"),
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            expected_return_loss=money("12"),
            attributable_costs=money("4"),
        )
        base = result["scenarios"][SCENARIO_BASE]
        self.assertEqual(base["contribution_profit"]["state"], "unknown")
        self.assertIn("supplier_freight", " ".join(base["missing"]))
        self.assertIn("supplier_freight", result["evidence_gaps"][0] or "")

    def test_missing_platform_fee_terms_block_profit(self):
        result = compute_unit_economics(
            sale_price=money("299"),
            procurement_cost=money("150"),
            supplier_freight=money("15"),
            packaging=money("5"),
            expected_return_loss=money("12"),
        )
        self.assertEqual(result["scenarios"][SCENARIO_BASE]["contribution_profit"]["state"], "unknown")

    def test_return_loss_from_probability(self):
        loss = compute_return_loss(probability="0.05", loss_amount=money("240"))
        self.assertEqual(loss.amount, Decimal("12"))
        with self.assertRaises(EconomicsError):
            compute_return_loss(probability="1.5", loss_amount=money("10"))

    def test_moq_drives_cash_occupied(self):
        result = compute_unit_economics(
            sale_price=money("299"),
            procurement_cost=money("150"),
            supplier_freight=money("15"),
            packaging=money("5"),
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            expected_return_loss=money("12"),
            moq=5,
        )
        self.assertEqual(Decimal(result["cash_occupied"]["amount"]), Decimal("825"))

    def test_scenario_overrides_must_be_provided_to_differ(self):
        result = compute_unit_economics(
            sale_price=money("299"),
            procurement_cost=money("150"),
            supplier_freight=money("15"),
            packaging=money("5"),
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            expected_return_loss=money("12"),
        )
        self.assertFalse(result["scenarios_distinct"])
        self.assertIn("identical", result["scenarios_note"])

        ranged = compute_unit_economics(
            sale_price=money("299"),
            procurement_cost=money("150"),
            supplier_freight=money("15"),
            packaging=money("5"),
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            expected_return_loss=money("12"),
            scenario_overrides={
                "conservative": {"sale_price": money("270")},  # observed market low
                "optimistic": {"sale_price": money("320")},  # observed market high
            },
        )
        self.assertTrue(ranged["scenarios_distinct"])
        self.assertEqual(
            Decimal(ranged["scenarios"][SCENARIO_CONSERVATIVE]["contribution_profit"]["amount"]),
            Decimal("270") - Decimal("150") - Decimal("15") - Decimal("5") - Decimal("270") * Decimal("0.03") - Decimal("12"),
        )

    def test_currency_mismatch_rejected(self):
        with self.assertRaises(EconomicsError):
            compute_unit_economics(
                sale_price=money("299"),
                procurement_cost=money("150", "USD"),
                supplier_freight=money("15"),
                packaging=money("5"),
                expected_return_loss=money("12"),
            )

    def test_decimal_precision_exact_no_float(self):
        result = compute_unit_economics(
            sale_price=money("0.1"),
            procurement_cost=money("0.03"),
            supplier_freight=money("0.03"),
            packaging=money("0.03"),
            platform_fee_rate="0",
            platform_fee_fixed="0",
            expected_return_loss=money("0"),
        )
        self.assertEqual(Decimal(result["scenarios"][SCENARIO_BASE]["contribution_profit"]["amount"]), Decimal("0.01"))

    def test_optional_components_not_applicable_are_skipped(self):
        result = compute_unit_economics(
            sale_price=money("299"),
            procurement_cost=money("150"),
            supplier_freight=money("15"),
            packaging=money("5"),
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            expected_return_loss=money("12"),
            installation={"state": "not_applicable"},
            after_sales_reserve={"state": "not_applicable"},
            attributable_costs={"state": "not_applicable"},
        )
        self.assertEqual(result["scenarios"][SCENARIO_BASE]["contribution_profit"]["state"], "known")

    def test_zero_or_negative_quantity_rejected(self):
        with self.assertRaises(EconomicsError):
            compute_unit_economics(
                sale_price=money("299"),
                procurement_cost=money("150"),
                supplier_freight=money("15"),
                packaging=money("5"),
                expected_return_loss=money("12"),
                quantity=0,
            )


def market_snapshot(*, price="299", observed=None, name="不锈钢工作台 GZ-1200 全新", spec="120x60x85cm", sku="GZ-1200", condition="全新"):
    observed = observed or datetime.now(UTC)
    return build_offer_snapshot(
        provider_id="market:xianyu_observation",
        source_item_id=sku,
        source_sku_id=sku,
        observed_at=observed,
        attributes={"name": name, "spec": spec},
        condition=condition,
        price_type="list_price_display",
        eligible_price=money(price),
        payload_hash=f"mkt-{sku}-{price}-{observed.isoformat()}",
        account_id="",
        source_ref="market:manual_observation",
    )


def supplier_snapshot(*, price="150", freight="15", observed=None, sku="GZ-1200", moq=1):
    observed = observed or datetime.now(UTC)
    return with_declared_quote_provenance(build_offer_snapshot(
        provider_id="supplier_file:杭州五金供应商",
        source_item_id=sku,
        source_sku_id=sku,
        observed_at=observed,
        attributes={"name": "不锈钢工作台", "spec": "120x60x85cm", "brand": "众商"},
        condition="全新",
        unit="件",
        price_type="supplier_confirmed",
        eligible_price=money(price),
        shipping_quote=money(freight),
        moq=moq,
        payload_hash=f"sup-{sku}-{price}-{freight}-{observed.isoformat()}",
        account_id="",
        source_ref="import_batch:test",
    ))


class OpportunityServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))
        self.service = OpportunityService(self.repos)
        self.market = self.repos.offers.add(market_snapshot())[0]
        self.supplier = self.repos.offers.add(supplier_snapshot())[0]

    def tearDown(self):
        self.tmp.cleanup()

    def _match(self, grade=MATCH_EXACT):
        record = build_product_match(
            left_snapshot_id=self.market["record_id"],
            right_snapshot_id=self.supplier["record_id"],
            grade=grade,
            rule_version="hard-constraints-v1",
        )
        return self.repos.matches.add(record)

    def _create(self, grade=MATCH_EXACT):
        match = self._match(grade)
        return self.service.create_from_match(
            market_snapshot_id=self.market["record_id"],
            supplier_snapshot_id=self.supplier["record_id"],
            match_id=match["record_id"],
        )

    def _evaluate(self, opportunity_id, **kwargs):
        params = dict(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging=money("5"),
            expected_return_loss=money("12"),
            attributable_costs=money("4"),
        )
        params.update(kwargs)
        return self.service.evaluate(opportunity_id, **params)

    def test_full_flow_qualifies_with_exact_match(self):
        opportunity = self._create()
        result = self._evaluate(opportunity["record_id"])
        self.assertTrue(result["qualification"]["qualified"])
        self.assertEqual(result["opportunity"]["status"], "ready_for_test")
        decision = result["decision"]
        self.assertEqual(decision["actor_role"], "pricing_agent")
        self.assertEqual(len(decision["evidence_refs"]), 4)
        self.assertEqual(decision["constraint_checks"][0]["state"], "pass")

    def test_not_match_cannot_create_opportunity(self):
        with self.assertRaises(OpportunityError):
            self._create(grade=MATCH_NOT_MATCH)

    def test_comparable_stays_research(self):
        opportunity = self._create(grade=MATCH_COMPARABLE)
        result = self._evaluate(opportunity["record_id"])
        self.assertFalse(result["qualification"]["qualified"])
        self.assertEqual(result["opportunity"]["status"], "researching")
        self.assertIn("below EXACT", result["decision"]["summary"])

    def test_stale_market_observation_blocks_qualification(self):
        stale_market = self.repos.offers.add(
            market_snapshot(observed=datetime.now(UTC) - timedelta(hours=73))
        )[0]
        match = self.repos.matches.add(
            build_product_match(
                left_snapshot_id=stale_market["record_id"],
                right_snapshot_id=self.supplier["record_id"],
                grade=MATCH_EXACT,
                rule_version="hard-constraints-v1",
            )
        )
        opportunity = self.service.create_from_match(
            market_snapshot_id=stale_market["record_id"],
            supplier_snapshot_id=self.supplier["record_id"],
            match_id=match["record_id"],
        )
        result = self._evaluate(opportunity["record_id"])
        self.assertFalse(result["qualification"]["qualified"])
        self.assertIn("stale", " ".join(result["qualification"]["reasons"]))
        self.assertEqual(result["opportunity"]["status"], "researching")

    def test_unknown_profit_keeps_research(self):
        opportunity = self._create()
        result = self._evaluate(opportunity["record_id"], platform_fee_rate=None, platform_fee_fixed=None)
        self.assertFalse(result["qualification"]["qualified"])
        self.assertEqual(result["opportunity"]["status"], "researching")
        self.assertIn("profit unknown", " ".join(result["qualification"]["reasons"]))

    def test_negative_conservative_profit_not_qualified(self):
        opportunity = self._create()
        result = self._evaluate(
            opportunity["record_id"],
            scenario_overrides={"conservative": {"sale_price": money("180")}},
        )
        self.assertFalse(result["qualification"]["qualified"])

    def test_funnel_counts_honest_projection(self):
        opportunity = self._create()
        self._evaluate(opportunity["record_id"])
        funnel = self.service.funnel()
        self.assertEqual(funnel["discovered_total"], 1)
        self.assertEqual(funnel["cost_complete"], 1)
        self.assertEqual(funnel["positive_conservative_profit"], 1)

    def test_decision_card_projection(self):
        opportunity = self._create()
        self._evaluate(opportunity["record_id"])
        card = self.service.decision_card(opportunity["record_id"])
        self.assertEqual(card["opportunity"]["status"], "ready_for_test")
        self.assertEqual(len(card["decisions"]), 1)
        self.assertIn("scenarios", card["economics"])

    def test_duplicate_create_returns_existing(self):
        first = self._create()
        second = self._create()
        self.assertEqual(first["record_id"], second["record_id"])


class QualificationInvalidationTests(unittest.TestCase):
    """P1 regression: corrections and worse economics must invalidate ready_for_test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))
        self.service = OpportunityService(self.repos)
        self.matcher = ProductMatchingService(self.repos)
        self.market = self.repos.offers.add(market_snapshot())[0]
        self.supplier = self.repos.offers.add(supplier_snapshot())[0]

    def tearDown(self):
        self.tmp.cleanup()

    def _evaluate(self, opportunity_id, **kwargs):
        params = dict(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging=money("5"),
            expected_return_loss=money("12"),
            attributable_costs=money("4"),
        )
        params.update(kwargs)
        return self.service.evaluate(opportunity_id, **params)

    def _exact_match(self):
        record = build_product_match(
            left_snapshot_id=self.market["record_id"],
            right_snapshot_id=self.supplier["record_id"],
            grade="EXACT",
            rule_version="hard-constraints-v1",
        )
        stored = self.repos.matches.add(record)
        return stored["record_id"]

    def test_human_correction_to_not_match_pauses_opportunity(self):
        match_id = self._exact_match()
        opportunity = self.service.create_from_match(
            market_snapshot_id=self.market["record_id"],
            supplier_snapshot_id=self.supplier["record_id"],
            match_id=match_id,
        )
        first = self._evaluate(opportunity["record_id"])
        self.assertEqual(first["opportunity"]["status"], "ready_for_test")

        # operator corrects the match to NOT_MATCH; re-evaluation must
        # judge against the correction, not the original record
        self.matcher.correct_grade(
            match_id, corrected_grade="NOT_MATCH", operator="operator", reason="spec mismatch found"
        )
        second = self._evaluate(opportunity["record_id"])
        self.assertFalse(second["qualification"]["qualified"])
        self.assertIn("below EXACT", " ".join(second["qualification"]["reasons"]))
        self.assertEqual(second["opportunity"]["status"], "paused")
        self.assertIn("invalidated", second["opportunity"]["status_reason"])

    def test_negative_conservative_profit_pauses_ready_opportunity(self):
        match_id = self._exact_match()
        opportunity = self.service.create_from_match(
            market_snapshot_id=self.market["record_id"],
            supplier_snapshot_id=self.supplier["record_id"],
            match_id=match_id,
        )
        self._evaluate(opportunity["record_id"])
        # supplier price rises: conservative scenario turns negative
        pricier = self.repos.offers.add(supplier_snapshot(price="290"))[0]
        updated = self.repos.opportunities.get(opportunity["record_id"])
        self.repos.opportunities.attach(
            updated["record_id"], field="offer_snapshot_ids", value=[pricier["record_id"]]
        )
        result = self._evaluate(opportunity["record_id"])
        self.assertFalse(result["qualification"]["qualified"])
        self.assertEqual(result["opportunity"]["status"], "paused")



class R09CostSheetAcceptanceTests(unittest.TestCase):
    """Taskbook R09 counter-examples: partial fee terms, return freight,
    tax applicability, honest break-even, stress overrides, currency
    conflict, and same-input determinism across entry points."""

    def _full_inputs(self):
        return dict(
            sale_price=money("300"),
            procurement_cost=money("160"),
            supplier_freight=money("15"),
            packaging=money("5"),
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            expected_return_loss=money("12"),
        )

    def test_missing_rate_keeps_fee_and_profit_unknown(self):
        inputs = self._full_inputs()
        inputs.pop("platform_fee_rate")
        inputs["platform_fee_fixed"] = "2"
        result = compute_unit_economics(**inputs)
        base = result["scenarios"][SCENARIO_BASE]
        self.assertEqual(base["components"]["platform_fee"]["state"], "unknown")
        self.assertIn("without rate", base["components"]["platform_fee"]["reason"])
        self.assertEqual(base["contribution_profit"]["state"], "unknown")

    def test_missing_fixed_fee_keeps_fee_and_profit_unknown(self):
        inputs = self._full_inputs()
        inputs.pop("platform_fee_fixed")
        result = compute_unit_economics(**inputs)
        base = result["scenarios"][SCENARIO_BASE]
        self.assertEqual(base["components"]["platform_fee"]["state"], "unknown")
        self.assertIn("without fixed", base["components"]["platform_fee"]["reason"])
        self.assertEqual(base["contribution_profit"]["state"], "unknown")

    def test_return_freight_alone_is_not_a_quantified_loss(self):
        result = compute_unit_economics(
            sale_price=money("300"), procurement_cost=money("160"),
            supplier_freight=money("15"), packaging=money("5"),
            platform_fee_rate="0.03", platform_fee_fixed="0",
            return_freight=money("18"),
        )
        component = result["scenarios"][SCENARIO_BASE]["components"]["expected_return_loss"]
        self.assertEqual(component["state"], "unknown")
        self.assertIn("not provided", component["reason"])

    def test_return_freight_times_probability_is_the_expected_loss(self):
        loss = compute_return_loss(probability="0.1", loss_amount=None, return_freight=money("18"))
        self.assertEqual(loss.state, "known")
        self.assertEqual(loss.amount, Decimal("1.8"))

    def test_procurement_price_increase_stress_flips_profit(self):
        inputs = self._full_inputs()
        inputs["scenario_overrides"] = {
            SCENARIO_CONSERVATIVE: {"procurement_cost": money("220")},
        }
        result = compute_unit_economics(**inputs)
        base = result["scenarios"][SCENARIO_BASE]["contribution_profit"]
        conservative = result["scenarios"][SCENARIO_CONSERVATIVE]["contribution_profit"]
        self.assertEqual(base["state"], "known")
        self.assertEqual(conservative["state"], "known")
        self.assertGreater(Decimal(base["amount"]), Decimal(conservative["amount"]))

    def test_currency_conflict_refused(self):
        with self.assertRaises(EconomicsError):
            compute_unit_economics(
                sale_price=money("300"), procurement_cost=money("160", "USD"),
                supplier_freight=money("15"), packaging=money("5"),
                platform_fee_rate="0.03", platform_fee_fixed="0",
                expected_return_loss=money("12"),
            )

    def test_tax_applicability_is_recorded_never_assumed(self):
        # absent: metadata says not_assessed, no component invented
        result = compute_unit_economics(**self._full_inputs())
        self.assertEqual(result["tax_applicability"]["state"], "not_assessed")
        self.assertNotIn("tax", result["scenarios"][SCENARIO_BASE]["components"])
        # applies without amount: unknown, rate never assumed
        result = compute_unit_economics(
            **self._full_inputs(),
            tax={"applicability": "applies", "rate": "0.13", "source": "supplier quote 2026-10"},
        )
        component = result["scenarios"][SCENARIO_BASE]["components"]["tax"]
        self.assertEqual(component["state"], "unknown")
        self.assertIn("never assumed", component["reason"])
        self.assertEqual(result["scenarios"][SCENARIO_BASE]["tax"]["declared_rate"], "0.13")
        # not_applicable with source: skipped from the sum
        result = compute_unit_economics(
            **self._full_inputs(),
            tax={"applicability": "not_applicable", "source": "consumer tax-inclusive quote"},
        )
        self.assertEqual(result["scenarios"][SCENARIO_BASE]["components"]["tax"]["state"], "not_applicable")
        self.assertEqual(result["scenarios"][SCENARIO_BASE]["contribution_profit"]["state"], "known")
        # applies with amount: counted
        result = compute_unit_economics(
            **self._full_inputs(),
            tax={"applicability": "applies", "amount": money("10"), "source": "quote states ex-tax"},
        )
        taxed = Decimal(result["scenarios"][SCENARIO_BASE]["contribution_profit"]["amount"])
        plain = compute_unit_economics(**self._full_inputs())
        untaxed = Decimal(plain["scenarios"][SCENARIO_BASE]["contribution_profit"]["amount"])
        self.assertEqual(untaxed - taxed, Decimal("10"))

    def test_break_even_is_unknown_when_fee_is_unknown(self):
        inputs = self._full_inputs()
        inputs.pop("platform_fee_rate")
        inputs.pop("platform_fee_fixed")
        result = compute_unit_economics(**inputs)
        be = result["scenarios"][SCENARIO_BASE]["break_even_price"]
        self.assertEqual(be["state"], "unknown")
        self.assertIn("fee-excluded", be["reason"])

    def test_same_cost_sheet_same_result_across_calls(self):
        first = compute_unit_economics(**self._full_inputs())
        second = compute_unit_economics(**self._full_inputs())
        self.assertEqual(
            first["scenarios"][SCENARIO_BASE]["components"],
            second["scenarios"][SCENARIO_BASE]["components"],
        )
        self.assertEqual(
            first["scenarios"][SCENARIO_CONSERVATIVE]["contribution_profit"],
            second["scenarios"][SCENARIO_CONSERVATIVE]["contribution_profit"],
        )
        self.assertEqual(first["rule_version"], "unit-economics-v2")

    def test_moq_cash_occupied_scales_with_batch(self):
        result = compute_unit_economics(**self._full_inputs(), moq=10)
        cash = result["cash_occupied"]
        self.assertEqual(cash["state"], "known")
        self.assertEqual(Decimal(cash["amount"]), Decimal("1750"))  # (160+15) * 10



if __name__ == "__main__":
    unittest.main()
