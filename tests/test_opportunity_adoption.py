"""G3A opportunity adoption bridge tests (A70-A74, S/H levels).

Synthetic fixtures over a temp data directory: the supplier snapshot comes
from a REAL import batch and the material confirmation goes through the
REAL preview/confirm path, so adoption eligibility is exercised end to
end. Day-0/publishing stay in the old chain and are NOT performed here
(real account -> WAITING_INPUT / NOT_AUTHORIZED).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quote_fixtures import declared_quote_csv

from xianyu_agent.candidate_evidence_store import CandidateEvidenceStore
from xianyu_agent.product_matching import MATCH_RULE_VERSION
from xianyu_agent.commerce.contracts import (
    Money,
    build_offer_snapshot,
    build_opportunity,
    build_product_match,
    utc_now,
)
from xianyu_agent.commerce.repositories import JsonCommerceRepositories
from xianyu_agent.intake_confirmation import IntakeConfirmationService
from xianyu_agent.opportunity_adoption import OpportunityAdoptionService
from xianyu_agent.opportunity_service import OpportunityService
from xianyu_agent.product_matching import ProductMatchingService
from xianyu_agent.resale import ResaleManager
from xianyu_agent.supplier_import import SupplierImportService
from xianyu_agent.supplier_intake import IntakeDeclaration, OfferEvidence

SUPPLIER = "杭州五金供应商"
CSV_TEMPLATE = (
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
    "{sku},不锈钢工作台,{price},件,1,50,包邮,120x60x85cm,众商,全新\n"
)


def _market(repos, sku, price="300"):
    observed = utc_now()
    stored, _ = repos.offers.add(
        build_offer_snapshot(
            provider_id="market:xianyu_observation",
            source_item_id=f"mkt-{sku}", source_sku_id=sku,
            observed_at=observed, fetched_at=observed,
            expires_at=observed + timedelta(hours=72),
            unit="件", quantity=None, condition="全新",
            attributes={"title": f"市场观察 {sku}"},
            price_type="display",
            eligible_price=Money.known(__import__("decimal").Decimal(price)).to_dict(),
            shipping_quote=Money.unknown("not observed").to_dict(),
            stock_state="unknown", stock_quantity=None, moq=None,
            tier_prices=[], tax_mode="unknown", seller_ref="",
            payload_hash=f"fixture:adoption-market:{sku}:{price}:{observed.isoformat()}",
            account_id="", source_ref="observation:fixture",
        )
    )
    return stored


class _Stack:
    def __init__(self, root: Path):
        self.root = root
        self.repos = JsonCommerceRepositories(root)
        self.imports = SupplierImportService(self.repos, root)
        self.evidence = CandidateEvidenceStore(root / "candidate_evidence")
        self.resale = ResaleManager(root)
        self.opportunities = OpportunityService(self.repos)
        self.intake = IntakeConfirmationService(self.repos, self.imports, self.evidence, root)
        self.adoption = OpportunityAdoptionService(
            self.repos, self.resale,
            opportunities=self.opportunities,
            eligibility_lookup=lambda sid, use: self.intake.eligibility(sid, use=use),
            data_dir=root,
        )

    def import_supplier(self, sku, price):
        payload = declared_quote_csv(CSV_TEMPLATE.format(sku=sku, price=price).encode("utf-8"))
        preview = self.imports.preview_bytes(payload, kind="csv", filename="quote.csv")
        result = self.imports.confirm(
            preview["preview_id"], content_sha256=preview["content_sha256"],
            revision=preview["revision"], supplier=SUPPLIER, operator="test",
        )
        return result["batch"], result["snapshots"][0], payload

    def confirm_material(self, batch, snapshot, payload, now):
        terms = OfferEvidence(
            currency="CNY", price_unit="件", quantity_unit="件", freight_basis="per_unit",
            freight_destination="杭州仓", tax_terms="含税出厂价（夹具假设）",
            delivery_terms="48小时内出库", after_sales_terms="错货破损供应商承担",
            stock_observed_at=(now - timedelta(hours=1)).isoformat(),
            stock_valid_until=(now + timedelta(hours=8)).isoformat(),
            evidence_ref="fixture:row",
        )
        declaration = IntakeDeclaration(
            batch_id=batch["record_id"], content_sha256=batch["content_sha256"],
            supplier=SUPPLIER, source_kind="supplier_original",
            origin_ref="isolated-fixture-reference",
            supplier_identity_ref="fixture:identity", use_permission_ref="fixture:permission",
            local_review_permitted=True, external_llm_permitted=False,
            issued_at=(now - timedelta(minutes=95)).isoformat(),
            received_at=(now - timedelta(minutes=50)).isoformat(),
            valid_until=(now + timedelta(hours=20)).isoformat(),
            offers={snapshot["record_id"]: terms},
        )
        preview = self.intake.preview(
            str(batch["record_id"]), content=payload, filename="quote.csv",
            declaration=declaration, now=now,
        )
        return self.intake.confirm(
            preview_id=preview["preview_id"], preview_digest=preview["digest"],
            idempotency_key="fixture-adoption-0001", allowed_uses=["local_analysis"],
            valid_until=(now + timedelta(hours=6)).isoformat(),
            verification_note="夹具确认：核对了原件、声明与快照一致性",
            operator="操作者甲", now=now,
        )

    def candidate(self, sku):
        return self.resale.create_candidate({
            "title": "不锈钢工作台",
            "source_platform": "pdd",
            "source_url": "https://fixture.example/item",
            "supplier_name": SUPPLIER,
            "supplier_sku": sku,
            "supplier_price": 160,
            "supplier_shipping": 20,
            "target_sale_price": 300,
            "category": "商用厨具",
        })

    def opportunity_for(self, snapshot, sku):
        market = _market(self.repos, sku)
        match = self.repos.matches.add(build_product_match(
            left_snapshot_id=market["record_id"], right_snapshot_id=snapshot["record_id"],
            grade="EXACT", rule_version=MATCH_RULE_VERSION, explanation="fixture",
            source_ref="matching:fixture",
        ))
        record = self.repos.opportunities.create(build_opportunity(
            canonical_sku=sku, source=str(snapshot["provider_id"]), title="fixture",
            match_id=match["record_id"], offer_snapshot_ids=[snapshot["record_id"]],
            market_snapshot_id=market["record_id"],
        ))
        self.opportunities.evaluate(record["record_id"])
        return self.repos.opportunities.get(record["record_id"])


class OpportunityAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adoption-"))
        self.stack = _Stack(self.tmp)
        # real-clock anchor: import stamps fetched_at with the real clock and
        # every gate requires imported_at <= assessment moment
        imported_marker = datetime.now(timezone.utc)
        self.batch, self.snapshot, self.payload = self.stack.import_supplier("SKU-AD", "160")
        self.NOW = imported_marker + timedelta(minutes=5)
        self.opportunity = self.stack.opportunity_for(self.snapshot, "SKU-AD")
        self.candidate = self.stack.candidate("SKU-AD")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _confirm(self):
        return self.stack.confirm_material(self.batch, self.snapshot, self.payload, self.NOW)

    def _adopt(self, **overrides):
        fields = {
            "opportunity_id": self.opportunity["record_id"],
            "candidate_id": self.candidate["candidate_id"],
            "account_id": "acct-1",
            "operator": "操作者甲",
            "reason": "保守利润与规格核对后采纳该商机",
            "idempotency_key": "adopt-key-00001",
        }
        fields.update(overrides)
        return self.stack.adoption.adopt(**fields)

    # ------------------------------------------------------------------- A70

    def test_a70_adoption_maps_and_records_input_version(self):
        self._confirm()
        result = self._adopt()
        record = result["adoption"]
        self.assertEqual(record["status"], "adopted")
        self.assertEqual(record["opportunity_id"], self.opportunity["record_id"])
        self.assertEqual(record["candidate_id"], self.candidate["candidate_id"])
        self.assertTrue(record["input_digest"])
        self.assertIn("material confirmed", record["material_note"])
        # five evidence categories are NOT auto-passed: candidate untouched
        fresh = self.stack.resale.get_candidate(self.candidate["candidate_id"])
        self.assertFalse(fresh["supplier_verified"])
        self.assertEqual(fresh["evidence_revision"], 1)
        self.assertIsNone(fresh["manual_decision"])
        self.assertEqual(self.stack.evidence.list_candidate_artifacts(self.candidate["candidate_id"]), [])

    def test_a70_adoption_requires_confirmed_material(self):
        with self.assertRaises(Exception) as ctx:
            self._adopt(idempotency_key="adopt-key-noconfirm")
        self.assertIn("material not confirmed", str(ctx.exception))

    # ------------------------------------------------------------------- A71

    def test_a71_duplicate_scope_and_account_isolation(self):
        self._confirm()
        first = self._adopt()
        again = self._adopt(idempotency_key="another-key-001")
        self.assertTrue(again.get("already_adopted"))
        self.assertEqual(first["adoption"]["record_id"], again["adoption"]["record_id"])
        self.assertEqual(len(self.stack.adoption.list_adoptions()), 1)

        other = self._adopt(idempotency_key="another-key-002", account_id="acct-2")
        self.assertNotEqual(other["adoption"]["record_id"], first["adoption"]["record_id"])
        self.assertEqual(len(self.stack.adoption.list_adoptions()), 2)

        with self.assertRaises(Exception) as ctx:
            self._adopt(idempotency_key="another-key-002", account_id="acct-3")
        self.assertIn("different payload", str(ctx.exception))

    # ------------------------------------------------------------------- A72

    def test_a72_input_change_marks_stale_without_rewriting_history(self):
        self._confirm()
        self._adopt()
        # supplier revises price -> a newer snapshot for the same provider+SKU
        batch2, snapshot2, _payload2 = self.stack.import_supplier("SKU-AD", "220")
        self.assertNotEqual(snapshot2["record_id"], self.snapshot["record_id"])
        report = self.stack.adoption.recheck(self.stack.adoption.list_adoptions()[0]["record_id"])
        self.assertFalse(report["valid"])
        reasons = "; ".join(report["reasons"])
        self.assertTrue("newer supplier snapshot" in reasons or "inputs changed" in reasons, reasons)
        record = self.stack.adoption.list_adoptions()[0]
        self.assertEqual(record["status"], "stale")
        fresh = self.stack.resale.get_candidate(self.candidate["candidate_id"])
        self.assertEqual(fresh["evidence_revision"], 1)  # history untouched

    def test_a72_material_lapse_marks_stale(self):
        self._confirm()
        self._adopt()
        for record in self.stack.intake.list_confirmations():
            self.stack.intake.revoke(record["record_id"], operator="o", reason="供应商撤回材料使用许可")
        report = self.stack.adoption.recheck(self.stack.adoption.list_adoptions()[0]["record_id"])
        self.assertFalse(report["valid"])
        self.assertTrue(any("material confirmation lapsed" in r for r in report["reasons"]))

    # ------------------------------------------------------------------- A73

    def test_a73_adoption_is_not_product_approval(self):
        self._confirm()
        self._adopt()
        fresh = self.stack.resale.get_candidate(self.candidate["candidate_id"])
        self.assertNotEqual(fresh["status"], "approved")
        self.assertIsNone(fresh["manual_decision"])
        self.assertFalse(fresh["listing_draft_id"])

    # ------------------------------------------------------------------- A74

    def test_a74_model_suggestion_never_auto_approves(self):
        self._confirm()
        self._adopt()
        decisions = self.stack.repos.decisions.list(opportunity_id=self.opportunity["record_id"])
        self.assertTrue(decisions)
        for decision in decisions:
            self.assertNotEqual(decision.get("authorization_type"), "auto")
        fresh = self.stack.resale.get_candidate(self.candidate["candidate_id"])
        self.assertIsNone(fresh["manual_decision"])
        self.assertEqual(fresh["status"], "evaluating")


if __name__ == "__main__":
    unittest.main()
