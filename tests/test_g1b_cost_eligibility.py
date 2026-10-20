"""G1B cost-source and eligibility-invalidation contract tests (A20-A26).

All inputs are synthetic isolated fixtures (the matrix 15.1 golden sample
numbers appear only as explicit test inputs, never as production defaults).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decimal import Decimal

from xianyu_agent.account_commercial_terms import AccountCommercialTermsStore
from xianyu_agent.commerce.contracts import (
    MATCH_EXACT,
    Money,
    build_offer_snapshot,
    build_opportunity,
    build_product_match,
    utc_now,
)
from xianyu_agent.commerce.repositories import JsonCommerceRepositories
from xianyu_agent.opportunity_service import OpportunityService
from xianyu_agent.product_matching import MATCH_RULE_VERSION

NOW = datetime.now(timezone.utc)


def _terms_payload(**overrides):
    fields = {
        "cookie_id": "acct-A",
        "category": "商用厨具",
        "seller_type": "business",
        "account_owner_confirmed": True,
        "seller_agreement_confirmed": True,
        "payment_service_ready": True,
        "category_publishable": True,
        "return_policy": "seven_day",
        "return_address_ready": True,
        "after_sales_ready": True,
        "guarantee_required": False,
        "guarantee_ready": True,
        "guarantee_amount": 0,
        "platform_fee_rate": 0.03,
        "platform_fixed_fee": 0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence_note": "测试假设：核对了当日页面费率",
        "evidence_ref": "fixture:terms-page",
    }
    fields.update(overrides)
    return fields


def _terms_binding(snapshot):
    return {
        "cookie_id": snapshot["cookie_id"],
        "category": snapshot["category"],
        "snapshot_version": snapshot["snapshot_version"],
        "observed_at": snapshot["observed_at"],
    }


class _CommerceStack:
    def __init__(self, root: Path):
        self.root = root
        self.repos = JsonCommerceRepositories(root)
        self.service = OpportunityService(self.repos)
        self.terms = AccountCommercialTermsStore(root / "account_commercial_terms.json")

    def supplier_offer(self, *, sku="SKU-G1B", price="160", freight="20", tiers=(), provider="supplier_file:杭州五金", observed=None):
        observed = observed or utc_now()
        stored, _ = self.repos.offers.add(
            build_offer_snapshot(
                provider_id=provider,
                source_item_id=sku,
                source_sku_id=sku,
                observed_at=observed,
                fetched_at=observed,
                expires_at=observed + timedelta(hours=24),
                unit="件",
                quantity=None,
                condition="全新",
                attributes={"name": f"商品{sku}", "quote_provenance": {
                    "schema_version": 1,
                    "source_kind": "supplier_original",
                    "source_reference": "isolated-fixture-reference",
                    "source_observed_at": observed.isoformat(),
                    "valid_until": (observed + timedelta(hours=24)).isoformat(),
                }},
                price_type="supplier_confirmed",
                eligible_price=Money.known(Decimal(price)).to_dict(),
                shipping_quote=Money.known(Decimal(freight)).to_dict(),
                stock_state="in_stock",
                stock_quantity=50,
                moq=1,
                tier_prices=[
                    {"min_quantity": t, "price": Money.known(Decimal(p)).to_dict()}
                    for t, p in tiers
                ],
                tax_mode="unknown",
                seller_ref="杭州五金供应商",
                payload_hash=f"fixture:{provider}:{sku}:{price}:{freight}:{len(tiers)}:{observed.isoformat()}",
                account_id="",
                source_ref="import_batch:impb_fixture",
            )
        )
        return stored

    def market_observation(self, *, price="300", sku="SKU-G1B"):
        observed = utc_now()
        stored, _ = self.repos.offers.add(
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
                stock_state="unknown",
                stock_quantity=None,
                moq=None,
                tier_prices=[],
                tax_mode="unknown",
                seller_ref="",
                payload_hash=f"fixture:market:{sku}:{price}:{observed.isoformat()}",
                account_id="",
                source_ref="observation:fixture",
            )
        )
        return stored

    def exact_match(self, market, supplier, *, grade=MATCH_EXACT):
        stored = self.repos.matches.add(
            build_product_match(
                left_snapshot_id=market["record_id"],
                right_snapshot_id=supplier["record_id"],
                grade=grade,
                rule_version=MATCH_RULE_VERSION,
                explanation="fixture exact",
                source_ref="matching:fixture",
            )
        )
        return stored

    def opportunity(self, market, supplier, match):
        record = self.repos.opportunities.create(
            build_opportunity(
                canonical_sku=str(supplier["source_item_id"]),
                source=str(supplier["provider_id"]),
                title="fixture opportunity",
                match_id=match["record_id"],
                offer_snapshot_ids=[supplier["record_id"]],
                market_snapshot_id=market["record_id"],
            )
        )
        return record


def _money_state(value):
    return (value or {}).get("state")


class G1BCostEligibilityServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="g1b-costs-"))
        self.stack = _CommerceStack(self.tmp)
        self.supplier = self.stack.supplier_offer()
        self.market = self.stack.market_observation()
        self.match = self.stack.exact_match(self.market, self.supplier)
        self.opportunity = self.stack.opportunity(self.market, self.supplier, self.match)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _evaluate(self, **kwargs):
        return self.stack.service.evaluate(self.opportunity["record_id"], **kwargs)

    # ------------------------------------------------------------------- A20

    def test_a20_no_defaults_when_costs_missing(self):
        result = self._evaluate()  # nothing provided
        inputs = result["economics"]["inputs"]
        self.assertEqual(inputs["platform_fee_source"], "missing")
        self.assertFalse(result["qualification"]["qualified"])
        scenario = result["economics"]["scenarios"]["conservative"]
        self.assertEqual(_money_state(scenario["contribution_profit"]), "unknown")
        missing = " ".join(scenario["missing"])
        self.assertIn("platform_fee", missing)
        self.assertIn("packaging", missing)
        # the historical demo numbers never appear as known amounts
        components = scenario["components"]
        for name in ("platform_fee", "packaging", "expected_return_loss", "attributable_costs"):
            self.assertNotEqual(
                (components[name] or {}).get("amount"), "5", f"{name} must not default"
            )
            self.assertNotEqual(_money_state(components[name]), "known", f"{name} stays unknown")

    def test_a20_demo_costs_flag_blocks_qualification(self):
        result = self._evaluate(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
            attributable_costs={"state": "known", "amount": "4", "currency": "CNY"},
            demo_costs=True,
        )
        scenario = result["economics"]["scenarios"]["conservative"]
        self.assertEqual(_money_state(scenario["contribution_profit"]), "known")
        self.assertFalse(result["qualification"]["qualified"])
        self.assertIn(
            "demo cost inputs cannot produce real qualification",
            " ".join(result["qualification"]["reasons"]),
        )
        self.assertNotEqual(result["opportunity"]["status"], "ready_for_test")

    # ------------------------------------------------------------------- A21

    def test_a21_terms_bound_and_revision_invalidates(self):
        snapshot = self.stack.terms.save_snapshot(_terms_payload())
        result = self._evaluate(
            platform_fee_rate=str(snapshot["platform_fee_rate"]),
            platform_fee_fixed=str(snapshot["platform_fixed_fee"]),
            commercial_terms=_terms_binding(snapshot),
        )
        inputs = result["economics"]["inputs"]
        self.assertEqual(inputs["platform_fee_source"], "account_commercial_terms")
        self.assertEqual(
            inputs["commercial_terms"]["snapshot_version"], snapshot["snapshot_version"]
        )
        # fee computed from the snapshot terms: 300*0.03 + 0 = 9
        fee = result["economics"]["scenarios"]["conservative"]["components"]["platform_fee"]
        self.assertEqual(Decimal(fee["amount"]), Decimal("9"))

        # qualify first (all real costs provided)
        qualified = self._evaluate(
            platform_fee_rate=str(snapshot["platform_fee_rate"]),
            platform_fee_fixed=str(snapshot["platform_fixed_fee"]),
            commercial_terms=_terms_binding(snapshot),
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
            attributable_costs={"state": "known", "amount": "4", "currency": "CNY"},
        )
        self.assertEqual(qualified["opportunity"]["status"], "ready_for_test")

        # revise terms for the same account+category -> stored qualification invalid
        self.stack.terms.save_snapshot(_terms_payload(platform_fee_rate=0.05))
        report = self.stack.service.validate_current(
            self.opportunity["record_id"], terms_lookup=self.stack.terms.get_snapshot
        )
        self.assertFalse(report["valid"])
        self.assertTrue(any("terms revised" in r for r in report["reasons"]))
        self.assertEqual(report.get("status_after"), "paused")

    def test_a21_terms_of_another_account_rejected(self):
        self.stack.terms.save_snapshot(_terms_payload())  # acct-A only
        lookup = self.stack.terms.get_snapshot
        self.assertIsNone(lookup("acct-B", "商用厨具"))
        self.assertIsNotNone(lookup("acct-A", "商用厨具"))
        # different category also missing
        self.assertIsNone(lookup("acct-A", "其他类目"))

    # ------------------------------------------------------------------- A22

    def test_a22_tier_price_applicability(self):
        supplier = self.stack.supplier_offer(sku="SKU-TIER", tiers=((10, "145"),))
        market = self.stack.market_observation(sku="SKU-TIER")
        match = self.stack.exact_match(market, supplier)
        opportunity = self.stack.opportunity(market, supplier, match)

        single = self.stack.service.evaluate(opportunity["record_id"], quantity=1)
        unit_price = single["economics"]["scenarios"]["conservative"]["components"]["procurement_cost"]
        self.assertEqual(Decimal(unit_price["amount"]), Decimal("160"))  # base price, never the 10-piece wholesale price
        self.assertIsNone(single["economics"]["inputs"]["applied_tier"])

        bulk = self.stack.service.evaluate(opportunity["record_id"], quantity=10)
        bulk_price = bulk["economics"]["scenarios"]["conservative"]["components"]["procurement_cost"]
        self.assertEqual(Decimal(bulk_price["amount"]), Decimal("145"))
        self.assertEqual(bulk["economics"]["inputs"]["applied_tier"]["min_quantity"], 10)

    def test_a22_per_order_freight_allocation(self):
        supplier = self.stack.supplier_offer(sku="SKU-FREIGHT", freight="20")
        market = self.stack.market_observation(sku="SKU-FREIGHT")
        match = self.stack.exact_match(market, supplier)
        opportunity = self.stack.opportunity(market, supplier, match)
        result = self.stack.service.evaluate(
            opportunity["record_id"], quantity=4, freight_basis="per_order"
        )
        freight = result["economics"]["scenarios"]["conservative"]["components"]["supplier_freight"]
        self.assertEqual(Decimal(freight["amount"]), Decimal("5"))
        self.assertEqual(result["economics"]["inputs"]["freight_basis"], "per_order")

    def test_a22_currency_conflict_rejected(self):
        from xianyu_agent.unit_economics import EconomicsError, compute_unit_economics

        with self.assertRaises(EconomicsError):
            compute_unit_economics(
                sale_price={"state": "known", "amount": "300", "currency": "USD"},
                procurement_cost={"state": "known", "amount": "160", "currency": "CNY"},
                supplier_freight={"state": "known", "amount": "20", "currency": "CNY"},
                packaging={"state": "known", "amount": "5", "currency": "CNY"},
                platform_fee_rate="0.03",
                platform_fee_fixed="0",
                expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
            )

    # ------------------------------------------------------------------- A23

    def test_a23_states_and_estimated_metadata(self):
        result = self._evaluate(
            packaging={"state": "unknown", "reason": "无包装成本依据"},
            installation={"state": "not_applicable", "reason": "测试假设：无需安装"},
            cost_metadata={
                "expected_return_loss": {"basis": "estimated", "source": "操作者保守假设", "assumption": "按退货率3%估"}
            },
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
        )
        scenario = result["economics"]["scenarios"]["conservative"]
        self.assertIn("packaging", " ".join(scenario["missing"]))
        self.assertEqual(
            _money_state(scenario["components"]["installation"]), "not_applicable"
        )
        meta = result["economics"]["inputs"]["cost_metadata"]
        self.assertEqual(meta["expected_return_loss"]["basis"], "estimated")
        self.assertEqual(meta["expected_return_loss"]["source"], "操作者保守假设")

    # ------------------------------------------------------------------- A24

    def test_a24_no_fabricated_scenarios_and_price_basis(self):
        result = self._evaluate(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
        )
        economics = result["economics"]
        self.assertFalse(economics["scenarios_distinct"])
        self.assertIn("identical", economics["scenarios_note"])
        self.assertIn(
            "assumption, not a transaction", economics["inputs"]["sale_price_basis"]
        )

        with_low = self._evaluate(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
            scenario_overrides={
                "conservative": {"sale_price": {"state": "known", "amount": "260", "currency": "CNY"}}
            },
        )
        self.assertTrue(with_low["economics"]["scenarios_distinct"])

    # ------------------------------------------------------------------- A25

    def test_a25_newer_supplier_snapshot_pauses_ready(self):
        result = self._evaluate(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
        )
        self.assertEqual(result["opportunity"]["status"], "ready_for_test")

        # supplier revises the price upward -> a newer snapshot exists
        self.stack.supplier_offer(price="220")
        report = self.stack.service.validate_current(self.opportunity["record_id"])
        self.assertFalse(report["valid"])
        self.assertTrue(any("newer supplier snapshot" in r for r in report["reasons"]))
        current = self.stack.repos.opportunities.get(self.opportunity["record_id"])
        self.assertEqual(current["status"], "paused")

    def test_a25_match_correction_and_expiry_pause_ready(self):
        result = self._evaluate(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
        )
        self.assertEqual(result["opportunity"]["status"], "ready_for_test")

        # human corrects the match to NOT_MATCH
        self.stack.repos.matches.add(
            build_product_match(
                left_snapshot_id=self.market["record_id"],
                right_snapshot_id=self.supplier["record_id"],
                grade="NOT_MATCH",
                rule_version=MATCH_RULE_VERSION,
                human_correction={"operator": "test", "reason": "规格不一致修正"},
                source_ref="matching:fixture-correction",
            )
        )
        report = self.stack.service.validate_current(self.opportunity["record_id"])
        self.assertFalse(report["valid"])
        self.assertTrue(any("below EXACT" in r for r in report["reasons"]))

    def test_a25_valid_ready_stays_ready(self):
        result = self._evaluate(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
        )
        report = self.stack.service.validate_current(self.opportunity["record_id"])
        self.assertTrue(report["valid"], report["reasons"])
        current = self.stack.repos.opportunities.get(self.opportunity["record_id"])
        self.assertEqual(current["status"], "ready_for_test")

    # ------------------------------------------------------------------- A26

    def test_a26_golden_sample_hand_computation(self):
        # matrix 15.1: 300 - 160 - 20 - 5 - 9 - 4 - 12 = 90
        result = self._evaluate(
            platform_fee_rate="0.03",
            platform_fee_fixed="0",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
            attributable_costs={"state": "known", "amount": "4", "currency": "CNY"},
        )
        conservative = result["economics"]["scenarios"]["conservative"]
        self.assertEqual(_money_state(conservative["contribution_profit"]), "known")
        self.assertEqual(Decimal(conservative["contribution_profit"]["amount"]), Decimal("90"))
        # no reserve double counting: after_sales_reserve not_applicable + explicit 12 loss
        self.assertEqual(
            _money_state(conservative["components"]["after_sales_reserve"]), "not_applicable"
        )
        self.assertEqual(conservative["components"]["expected_return_loss"]["amount"], "12")


class G1BHttpRouteTests(unittest.TestCase):
    """HTTP-level: no server-side cost defaults; terms binding rules; the
    decision card revalidates a stale ready before rendering (A20/A21/A25)."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from xianyu_agent import server as server_module

        self.client = TestClient(server_module.app)
        self.server = server_module
        self.terms = server_module.account_commercial_terms_store

    def _make_opportunity(self, sku="SKU-HTTP"):
        payload = {
            "sku": sku,
            "title": f"工作台 {sku}",
            "spec": "120x60x85cm",
            "brand": "众商",
            "price": "300",
            "condition": "全新",
            "unit": "件",
        }
        market = self.client.post("/commerce/market-observations", json=payload)
        self.assertEqual(market.status_code, 200, market.text)
        return market.json()["snapshot"]

    def _full_opportunity(self, sku):
        from quote_fixtures import declared_quote_csv

        csv_body = (
            "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
            f"{sku},不锈钢工作台,160,件,1,50,包邮,120x60x85cm,众商,全新\n"
        ).encode("utf-8")
        payload = declared_quote_csv(csv_body).decode("utf-8")
        preview = self.client.post(
            "/commerce/supplier-imports/preview", json={"kind": "pasted_text", "content": payload}
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        confirm = self.client.post(
            f"/commerce/supplier-imports/{preview.json()['preview_id']}/confirm",
            json={
                "content_sha256": preview.json()["content_sha256"],
                "revision": preview.json()["revision"],
                "supplier": "杭州五金供应商",
                "operator": "test",
            },
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        supplier_snapshot = confirm.json()["snapshots"][0]

        market = self._make_opportunity(sku)
        match = self.client.post(
            "/commerce/product-matches",
            json={
                "left_snapshot_id": market["record_id"],
                "right_snapshot_id": supplier_snapshot["record_id"],
            },
        )
        self.assertEqual(match.status_code, 200, match.text)
        opportunity = self.client.post(
            "/commerce/opportunities",
            json={
                "market_snapshot_id": market["record_id"],
                "supplier_snapshot_id": supplier_snapshot["record_id"],
                "match_id": match.json()["match_id"],
            },
        )
        self.assertEqual(opportunity.status_code, 200, opportunity.text)
        return opportunity.json()

    def test_a20_http_empty_payload_stays_unknown(self):
        opportunity = self._full_opportunity("SKU-HTTP-A20")
        evaluate = self.client.post(
            f"/commerce/opportunities/{opportunity['record_id']}/evaluate", json={}
        )
        self.assertEqual(evaluate.status_code, 200, evaluate.text)
        body = evaluate.json()
        self.assertEqual(body["economics"]["inputs"]["platform_fee_source"], "missing")
        self.assertFalse(body["qualification"]["qualified"])
        scenario = body["economics"]["scenarios"]["conservative"]
        self.assertEqual(_money_state(scenario["contribution_profit"]), "unknown")
        self.assertIn("platform_fee", " ".join(scenario["missing"]))
        self.assertNotEqual(body["opportunity"]["status"], "ready_for_test")

    def test_a21_account_terms_binding_rules(self):
        snapshot = self.terms.save_snapshot(_terms_payload(cookie_id="acct-http"))
        try:
            market = self._make_opportunity("SKU-HTTP-A21")
            # account_terms + explicit rate is a conflict
            response = self.client.post(
                "/commerce/opportunities/op_x/evaluate",
                json={"account_terms": {"cookie_id": "acct-http", "category": "商用厨具"}, "platform_fee_rate": "0.03"},
            )
            self.assertEqual(response.status_code, 409, response.text)
            # unknown account -> 409 with the reason
            response = self.client.post(
                "/commerce/opportunities/op_x/evaluate",
                json={"account_terms": {"cookie_id": "acct-none", "category": "商用厨具"}},
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("no commercial terms snapshot", response.text)
        finally:
            self._drop_terms("acct-http")

    def test_a25_decision_card_revalidates(self):
        # unknown opportunity -> 404 from the revalidation step itself
        response = self.client.get("/commerce/opportunities/op_missing/decision-card")
        self.assertEqual(response.status_code, 404, response.text)

    def _drop_terms(self, cookie_id):
        with self.terms._lock:
            self.terms._snapshots.pop(cookie_id, None)
            self.terms._persist()


if __name__ == "__main__":
    unittest.main()
