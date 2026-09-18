"""WP05 product matching tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from xianyu_agent.commerce import (
    MATCH_COMPARABLE,
    MATCH_EXACT,
    MATCH_NOT_MATCH,
    MATCH_UNKNOWN,
    JsonCommerceRepositories,
    build_offer_snapshot,
)
from xianyu_agent.product_matching import (
    MATCH_RULE_VERSION,
    ProductMatchingService,
    evaluate_labeled_pairs,
    extract_match_attributes,
    grade_pair,
    normalize_condition,
    normalize_dimensions,
    normalize_unit,
)

UTC = timezone.utc
FIXTURE = Path(__file__).parent / "fixtures" / "match_labeled_pairs.jsonl"
HOLDOUT = Path(__file__).parent / "fixtures" / "match_holdout_pairs.jsonl"


def market_snapshot(
    *,
    record_id: str = "",
    name: str = "不锈钢工作台",
    spec: str = "",
    sku: str = "",
    condition: str = "全新",
    price: str = "299",
) -> dict:
    return build_offer_snapshot(
        provider_id="market:xianyu_observation",
        source_item_id=sku or name,
        source_sku_id=sku,
        observed_at=datetime(2026, 9, 18, 6, 0, tzinfo=UTC),
        attributes={"name": name, "spec": spec} if spec else {"name": name},
        condition=condition,
        price_type="list_price_display",
        eligible_price={"state": "known", "amount": price, "currency": "CNY"},
        payload_hash=f"mkt-{sku or name}-{spec}-{condition}-{price}",
        account_id="",
        source_ref="market:manual_observation",
    )


def supplier_snapshot(
    *,
    record_id: str = "",
    sku: str = "GZ-1200",
    name: str = "不锈钢工作台",
    spec: str = "",
    price: str = "150",
    freight: str = "0",
    condition: str = "全新",
) -> dict:
    attributes = {"name": name}
    if spec:
        attributes["spec"] = spec
    return build_offer_snapshot(
        provider_id="supplier_file:杭州五金供应商",
        source_item_id=sku,
        source_sku_id=sku,
        observed_at=datetime(2026, 9, 18, 2, 0, tzinfo=UTC),
        attributes=attributes,
        condition=condition,
        price_type="supplier_confirmed",
        eligible_price={"state": "known", "amount": price, "currency": "CNY"},
        shipping_quote={"state": "known", "amount": freight, "currency": "CNY"},
        payload_hash=f"sup-{sku}-{spec}-{price}-{freight}-{condition}",
        account_id="",
        source_ref="import_batch:test",
    )


class NormalizerTests(unittest.TestCase):
    def test_condition_words(self):
        self.assertEqual(normalize_condition("全新"), "new")
        self.assertEqual(normalize_condition("二手 9成新"), "used")
        self.assertEqual(normalize_condition("95新"), "used")
        self.assertEqual(normalize_condition(""), "unknown")

    def test_dimension_mm_converted_to_cm(self):
        dims = normalize_dimensions("1200x600x850mm")
        self.assertEqual(dims, (Decimal("120"), Decimal("60"), Decimal("85")))

    def test_unit_classes(self):
        self.assertEqual(normalize_unit("件"), "piece")
        self.assertEqual(normalize_unit("张"), "piece")
        self.assertEqual(normalize_unit("套"), "set")
        self.assertEqual(normalize_unit("箱"), "case")
        self.assertEqual(normalize_unit(""), "unknown")


class GradePairTests(unittest.TestCase):
    def _attrs(self, **kwargs):
        base = {
            "name": "不锈钢工作台",
            "brand": "",
            "model": "",
            "dimensions": None,
            "material": "",
            "condition": "new",
            "unit_class": "unknown",
        }
        base.update(kwargs)
        return base

    def test_all_known_equal_is_exact(self):
        left = self._attrs(brand="众商", model="gz1200", dimensions=(Decimal(120), Decimal(60), Decimal(85)), material="不锈钢", unit_class="piece")
        right = self._attrs(brand="众商", model="gz1200", dimensions=(Decimal(120), Decimal(60), Decimal(85)), material="不锈钢", unit_class="piece")
        self.assertEqual(grade_pair(left, right)["grade"], MATCH_EXACT)

    def test_used_vs_new_never_matches(self):
        left = self._attrs(condition="used", model="gz1200", dimensions=(Decimal(120), Decimal(60), Decimal(85)))
        right = self._attrs(condition="new", model="gz1200", dimensions=(Decimal(120), Decimal(60), Decimal(85)))
        result = grade_pair(left, right)
        self.assertEqual(result["grade"], MATCH_NOT_MATCH)
        self.assertTrue(any(c["field"] == "condition" for c in result["conflicts"]))

    def test_dimension_conflict_rejects_even_with_high_name_similarity(self):
        left = self._attrs(name="不锈钢工作台", model="gz1200", dimensions=(Decimal(120), Decimal(60), Decimal(85)))
        right = self._attrs(name="不锈钢工作台", model="gz1200", dimensions=(Decimal(150), Decimal(70), Decimal(85)))
        result = grade_pair(left, right)
        self.assertEqual(result["grade"], MATCH_NOT_MATCH)
        # recall similarity is high but never decides
        self.assertGreaterEqual(result["recall_score"], 0.9)

    def test_no_identity_anchor_is_unknown_with_supplier_confirmation(self):
        left = self._attrs(name="操作台 1.2米")
        right = self._attrs(name="操作台 1.2米")
        result = grade_pair(left, right)
        self.assertEqual(result["grade"], MATCH_UNKNOWN)
        self.assertTrue(result["supplier_confirmation_needed"])

    def test_dims_anchor_with_brand_unknown_is_comparable(self):
        left = self._attrs(dimensions=(Decimal(120), Decimal(60), Decimal(85)))
        right = self._attrs(dimensions=(Decimal(120), Decimal(60), Decimal(85)))
        self.assertEqual(grade_pair(left, right)["grade"], MATCH_COMPARABLE)

    def test_unit_class_conflict(self):
        left = self._attrs(dimensions=(Decimal(120), Decimal(60), Decimal(85)), unit_class="piece")
        right = self._attrs(dimensions=(Decimal(120), Decimal(60), Decimal(85)), unit_class="set")
        self.assertEqual(grade_pair(left, right)["grade"], MATCH_NOT_MATCH)

    def test_feature_count_conflict_rejected(self):
        left = self._attrs(
            name="货架", model="hc1", dimensions=(Decimal(150), Decimal(60), Decimal(180)),
            feature_counts=(("层", 5),),
        )
        right = self._attrs(
            name="货架", model="hc1", dimensions=(Decimal(150), Decimal(60), Decimal(180)),
            feature_counts=(("层", 4),),
        )
        self.assertEqual(grade_pair(left, right)["grade"], MATCH_NOT_MATCH)

    def test_steel_grade_conflict_rejected_but_unstated_downgrades(self):
        dims = (Decimal(120), Decimal(60), Decimal(85))
        base = dict(name="台", brand="众商", model="gz1", dimensions=dims, material="不锈钢", unit_class="piece")
        left = self._attrs(**base, steel_grade="304")
        right_same = self._attrs(**base, steel_grade="304")
        self.assertEqual(grade_pair(left, right_same)["grade"], MATCH_EXACT)
        right_201 = self._attrs(**base, steel_grade="201")
        self.assertEqual(grade_pair(left, right_201)["grade"], MATCH_NOT_MATCH)
        right_unstated = self._attrs(**base, steel_grade="")
        self.assertEqual(grade_pair(left, right_unstated)["grade"], MATCH_COMPARABLE)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))
        self.service = ProductMatchingService(self.repos)

    def tearDown(self):
        self.tmp.cleanup()

    def test_match_snapshots_persists_product_match(self):
        left = self.repos.offers.add(market_snapshot(sku="GZ-1200", spec="120x60x85cm"))[0]
        right = self.repos.offers.add(supplier_snapshot(spec="120x60x85cm"))[0]
        result = self.service.match_snapshots(left["record_id"], right["record_id"])
        self.assertEqual(result["grade"], MATCH_COMPARABLE)  # brand unstated both sides
        stored = self.repos.matches.latest_for_pair(left["record_id"], right["record_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["rule_version"], MATCH_RULE_VERSION)

    def test_human_correction_appends_new_record(self):
        left = self.repos.offers.add(market_snapshot(sku="GZ-1200", spec="120x60x85cm"))[0]
        right = self.repos.offers.add(supplier_snapshot(spec="120x60x85cm"))[0]
        first = self.service.match_snapshots(left["record_id"], right["record_id"])
        corrected = self.service.correct_grade(
            first["match_id"], corrected_grade=MATCH_EXACT, operator="operator", reason="supplier confirmed same SKU"
        )
        self.assertEqual(corrected["grade"], MATCH_EXACT)
        self.assertIsNotNone(corrected["human_correction"])
        latest = self.repos.matches.latest_for_pair(left["record_id"], right["record_id"])
        self.assertEqual(latest["grade"], MATCH_EXACT)
        # original record kept for audit
        self.assertIsNotNone(self.repos.matches.get(first["match_id"]))


class LabeledEvaluationTests(unittest.TestCase):
    def test_tune_split_reports_sample_size_honestly(self):
        report = evaluate_labeled_pairs(FIXTURE)
        self.assertEqual(report["pair_count"], 72)
        self.assertIn("only claimed from a clean holdout", report["sample_size_note"])
        if report["exact_predicted"]:
            self.assertEqual(report["exact_precision"], 1.0)
        self.assertEqual(report["mistakes"], [])

    def test_holdout_split_zero_mistakes(self):
        """The holdout is never used for grader adjustment; it must stay clean."""
        report = evaluate_labeled_pairs(HOLDOUT)
        self.assertEqual(report["pair_count"], 30)
        self.assertEqual(report["mistakes"], [])
        if report["exact_predicted"]:
            self.assertGreaterEqual(report["exact_precision"], 0.95)

    def test_combined_set_reaches_planned_size(self):
        tune = evaluate_labeled_pairs(FIXTURE)
        holdout = evaluate_labeled_pairs(HOLDOUT)
        self.assertGreaterEqual(tune["pair_count"] + holdout["pair_count"], 100)

    def test_fixture_every_label_is_a_valid_grade(self):
        valid = {MATCH_EXACT, MATCH_COMPARABLE, MATCH_NOT_MATCH, MATCH_UNKNOWN}
        import json

        for fixture in (FIXTURE, HOLDOUT):
            for line in fixture.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#"):
                    self.assertIn(json.loads(line)["label"], valid)


if __name__ == "__main__":
    unittest.main()
