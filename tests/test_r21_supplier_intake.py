"""R21-file acceptance tests: supplier quote file intake.

The fixture/replay matrix (missing fields, versions, expiry, original
hash, idempotent reimport) is already covered by the 19+16 existing
tests; this file covers the two R21 gaps:

* supply-chain entity difference checks — the five entities behind one
  supply relationship (quote/promising/collection/shipping/returns) are
  DECLARED, compared against the declared supplier, and every difference
  is surfaced as a review item (never silently accepted, never claimed
  as verified);
* category profile samples parse and carry the operator checklist.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def base_declaration(**overrides):
    from xianyu_agent.supplier_intake import IntakeDeclaration

    payload = dict(
        batch_id="batch-1",
        content_sha256="a" * 64,
        supplier="杭州五金供应商",
        source_kind="supplier_original",
        origin_ref="email:quote-2026-10",
        supplier_identity_ref="biz-license:91330100MA2XYZ",
        use_permission_ref="chat:permission-granted",
        local_review_permitted=True,
        issued_at="2026-10-20T08:00:00+00:00",
        received_at="2026-10-20T09:00:00+00:00",
        valid_until="2026-10-27T00:00:00+00:00",
    )
    payload.update(overrides)
    return IntakeDeclaration(**payload)


def make_batch_and_snapshots():
    """A minimal committed batch with one complete snapshot (mirrors the
    existing fixture patterns in test_supplier_intake)."""
    snapshot = {
        "record_id": "snap-1",
        "provider_id": "supplier_file:杭州五金供应商",
        "source_item_id": "GZ-1200",
        "source_sku_id": "GZ-1200",
        "source_ref": "import_batch:batch-1",
        "seller_ref": "杭州五金供应商",
        "scope": "domestic_xianyu",
        "observed_at": "2026-10-20T08:30:00+00:00",
        "fetched_at": "2026-10-20T09:30:00+00:00",
        "expires_at": "2026-10-28T00:00:00+00:00",
        "attributes": {"spec": "120x60x85cm", "quote_provenance": {
            "source_kind": "supplier_original",
            "source_reference": "email:quote-2026-10",
            "eligible": True, "expired": False, "reasons": [],
        }},
        "condition": "全新",
        "unit": "件",
        "eligible_price": {"state": "known", "amount": "160", "currency": "CNY"},
        "shipping_quote": {"state": "known", "amount": "15", "currency": "CNY"},
        "stock_state": "in_stock",
        "stock_quantity": 50,
        "moq": 2,
    }
    batch = {
        "record_id": "batch-1",
        "status": "committed",
        "supplier": "杭州五金供应商",
        "provider_id": "supplier_file:杭州五金供应商",
        "content_sha256": "a" * 64,
        "snapshot_ids": ["snap-1"],
    }
    return batch, [snapshot]


class SupplyEntityDifferenceTests(unittest.TestCase):
    def _assess(self, declaration):
        from xianyu_agent.supplier_intake import assess_supplier_intake

        batch, snapshots = make_batch_and_snapshots()
        return assess_supplier_intake(
            batch, snapshots, declaration,
            quote_bytes=b"fixture-bytes",
            now=__import__("datetime").datetime(
                2026, 10, 21, tzinfo=__import__("datetime").timezone.utc),
        )

    def test_all_entities_same_as_supplier_is_declaration_not_verification(self):
        declaration = base_declaration(
            entities={
                "quote_entity": "杭州五金供应商",
                "promising_entity": "杭州五金供应商",
                "collection_entity": "杭州五金供应商",
                "shipping_entity": "杭州五金供应商",
                "returns_entity": "杭州五金供应商",
            })
        report = self._assess(declaration)
        entity = report["supply_entities"]
        self.assertTrue(entity)
        for entry in entity.values():
            self.assertEqual(entry["state"], "same_as_supplier")
            self.assertIn("not a verification", entry["detail"])
        # provider_verified stays False: declaration != verification
        self.assertFalse(report["provider_verified"])

    def test_entity_difference_surfaces_as_review_item(self):
        declaration = base_declaration(
            entities={
                "quote_entity": "杭州五金供应商",
                "promising_entity": "杭州五金供应商",
                "collection_entity": "1688平台担保交易",       # platform escrow
                "shipping_entity": "绍兴仓储物流公司",           # different warehouse
                "returns_entity": "杭州五金供应商",
            })
        report = self._assess(declaration)
        entity = report["supply_entities"]
        self.assertEqual(entity["collection_entity"]["state"], "different_from_supplier")
        self.assertEqual(entity["shipping_entity"]["state"], "different_from_supplier")
        self.assertEqual(entity["quote_entity"]["state"], "same_as_supplier")
        # the difference detail names both parties and says it's a review item
        detail = entity["shipping_entity"]["detail"]
        self.assertIn("绍兴仓储物流公司", detail)
        self.assertIn("review items", detail)

    def test_undeclared_entities_flagged_as_gap(self):
        declaration = base_declaration()  # entities left at defaults
        report = self._assess(declaration)
        entity = report["supply_entities"]
        for entry in entity.values():
            self.assertEqual(entry["state"], "undeclared")
        gap_codes = [g["code"] for g in report["gaps"]]
        self.assertIn("supply_entities_declared", gap_codes)
        self.assertNotEqual(report["status"], "ready_for_human_review")

    def test_declaration_rejects_unknown_entity_fields(self):
        from pydantic import ValidationError

        from xianyu_agent.supplier_intake import IntakeDeclaration

        with self.assertRaises(ValidationError):
            IntakeDeclaration(supplier="x", entities={
                "quote_entity": "a", "promising_entity": "a",
                "collection_entity": "a", "shipping_entity": "a",
                "returns_entity": "a", "rogue_entity": "b",
            })


class CategoryProfileTests(unittest.TestCase):
    def test_profiles_parse_and_carry_checklist(self):
        directory = PROJECT_ROOT / "config" / "category-profiles"
        profiles = list(directory.glob("*.json"))
        self.assertTrue(profiles, "no category profiles found")
        for path in profiles:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("category", data)
            self.assertIn("version", data)
            checklist = data.get("supply_entities_checklist", {})
            for entity in ("quote_entity", "promising_entity",
                           "collection_entity", "shipping_entity",
                           "returns_entity"):
                self.assertIn(entity, checklist,
                              f"{path.name}: checklist missing {entity}")


if __name__ == "__main__":
    unittest.main()
