"""Pi outbound material gate tests (G1B-6 / A45: unapproved inputs never
leave for a model). Fake Pi client captures the outbound payload; nothing
here touches a real model or live data.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from quote_fixtures import declared_quote_csv

from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.pi_jobs import PiTaskRunner
from xianyu_agent.supplier_import import SupplierImportService


def _csv(sku, price, source_kind="supplier_original"):
    body = (
        "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
        f"{sku},不锈钢工作台,{price},件,1,50,20,120x60x85cm,众商,全新\n"
    ).encode("utf-8")
    payload = declared_quote_csv(body)
    if source_kind == "demo":
        text = payload.decode("utf-8")
        payload = text.replace("supplier_original", "demo").encode("utf-8")
    return payload


class _FakePi:
    """Mimics PiRpcClient.prompt's result shape; captures the context."""

    calls = []

    def __init__(self, _env) -> None:
        self.captured = None

    def prompt(self, message, context=None):
        self.captured = {"message": message, "context": context}
        type(self).calls.append(self.captured)
        return {
            "ok": True,
            "result": {
                "message": json.dumps(
                    {"findings": [], "summary": "fixture: no gaps"}, ensure_ascii=False
                ),
                "session_id": "job-session-gate",
                "provider": "deepseek",
                "model": "deepseek-flash",
                "turns": 1,
                "tool_calls": [],
                "usage": {"total_tokens": 12},
            },
        }

    def stop(self):
        pass


class PiOutboundGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pi-gate-"))
        self.repos = JsonCommerceRepositories(self.tmp)
        self.imports = SupplierImportService(self.repos, self.tmp)
        self.jobs = AgentJobStore(self.tmp)
        _FakePi.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _import(self, sku, price, source_kind="supplier_original"):
        payload = _csv(sku, price, source_kind)
        preview = self.imports.preview_bytes(payload, kind="csv", filename="q.csv")
        result = self.imports.confirm(
            preview["preview_id"], content_sha256=preview["content_sha256"],
            revision=preview["revision"], supplier="杭州五金供应商", operator="test",
        )
        return result["snapshots"][0]

    def _runner(self, gate=None):
        runner = PiTaskRunner(
            self.jobs, self.repos, data_dir=self.tmp, import_service=self.imports,
            outbound_gate=gate,
        )
        runner.start()
        self.addCleanup(runner.stop)
        return runner

    def test_gate_disabled_keeps_legacy_behavior(self):
        self._import("SKU-OPEN", "160")
        runner = self._runner(gate=None)
        inputs = runner._collect_quote_inputs()
        self.assertEqual(len(inputs["offers"]), 1)
        self.assertEqual(inputs["withheld_external_model_count"], 0)

    def test_unapproved_real_material_withheld_from_collector_and_model(self):
        real = self._import("SKU-SECRET", "160")  # real, unconfirmed
        runner = self._runner(gate=lambda snapshot: {"eligible": False, "reasons": ["no_active_confirmation"]})
        inputs = runner._collect_quote_inputs()
        self.assertEqual(inputs["committed_offer_count"], 0)
        self.assertEqual(inputs["withheld_external_model_count"], 1)
        withheld = inputs["withheld_offers"][0]
        self.assertEqual(withheld["record_id"], real["record_id"])
        # the withheld entry carries ids and reasons ONLY
        self.assertEqual(set(withheld.keys()), {"record_id", "sku", "withheld_reasons", "note"})
        # a real model dispatch never contains the withheld material fields
        runner.submit_quote_review_job()
        runner.run_once(pi_factory=_FakePi)
        self.assertTrue(_FakePi.calls)
        payload = json.dumps(_FakePi.calls[-1]["context"], ensure_ascii=False, default=str)
        self.assertNotIn("120x60x85cm", payload)  # spec text absent from withheld fields
        self.assertNotIn('"160"', payload)  # the price never leaves
        self.assertIn("no_active_confirmation", payload)  # the honest reason does go out
        job = self.jobs.list()[0]
        self.assertEqual(job["status"], "succeeded")

    def test_approved_real_material_and_demo_pass(self):
        approved = self._import("SKU-OK", "160")
        demo = self._import("SKU-DEMO", "30", source_kind="demo")
        runner = self._runner(
            gate=lambda snapshot: (
                {"eligible": True, "reasons": []}
                if snapshot.get("record_id") == approved["record_id"]
                else {"eligible": False, "reasons": ["no_active_confirmation"]}
            )
        )
        inputs = runner._collect_quote_inputs()
        by_id = {o["record_id"]: o for o in inputs["offers"]}
        self.assertIn(approved["record_id"], by_id)
        self.assertEqual(by_id[approved["record_id"]]["outbound_basis"], "confirmed_external_model")
        # demo source bypasses the gate entirely (explicitly synthetic)
        self.assertIn(demo["record_id"], by_id)
        self.assertEqual(by_id[demo["record_id"]]["outbound_basis"], "demo_source")

    def test_broken_gate_fails_closed(self):
        self._import("SKU-BROKEN", "160")
        runner = self._runner(gate=lambda snapshot: (_ for _ in ()).throw(RuntimeError("boom")))
        inputs = runner._collect_quote_inputs()
        self.assertEqual(inputs["committed_offer_count"], 0)
        self.assertTrue(any("gate_error" in r for r in inputs["withheld_offers"][0]["withheld_reasons"]))

    def test_pricing_collector_masks_unapproved_money(self):
        from xianyu_agent.commerce.contracts import (
            Money, build_offer_snapshot, build_opportunity, build_product_match, utc_now,
        )
        from xianyu_agent.product_matching import MATCH_RULE_VERSION
        from xianyu_agent.opportunity_service import OpportunityService

        self._import("SKU-PRICE", "160")
        supplier = [o for o in self.repos.offers.list() if o["source_item_id"] == "SKU-PRICE"][-1]
        observed = utc_now()
        market, _ = self.repos.offers.add(build_offer_snapshot(
            provider_id="market:xianyu_observation", source_item_id="mkt-SKU-PRICE",
            source_sku_id="SKU-PRICE", observed_at=observed, fetched_at=observed,
            expires_at=observed + timedelta(hours=72), unit="件", quantity=None,
            condition="全新", attributes={"title": "x"}, price_type="display",
            eligible_price=Money.known(Decimal("300")).to_dict(),
            shipping_quote=Money.unknown("x").to_dict(), stock_state="unknown",
            stock_quantity=None, moq=None, tier_prices=[], tax_mode="unknown", seller_ref="",
            payload_hash="fixture:gate-market", account_id="", source_ref="observation:fixture",
        ))
        match = self.repos.matches.add(build_product_match(
            left_snapshot_id=market["record_id"], right_snapshot_id=supplier["record_id"],
            grade="EXACT", rule_version=MATCH_RULE_VERSION, explanation="fixture",
            source_ref="matching:fixture",
        ))
        record = self.repos.opportunities.create(build_opportunity(
            canonical_sku="SKU-PRICE", source=str(supplier["provider_id"]), title="t",
            match_id=match["record_id"], offer_snapshot_ids=[supplier["record_id"]],
            market_snapshot_id=market["record_id"],
        ))
        OpportunityService(self.repos).evaluate(record["record_id"])

        runner = self._runner(gate=lambda snapshot: {"eligible": False, "reasons": ["no_active_confirmation"]})
        inputs = runner._collect_pricing_inputs()
        row = inputs["opportunities"][0]
        self.assertTrue(row["material_withheld"])
        self.assertEqual(row["conservative_profit"]["state"], "unknown")
        self.assertIn("not confirmed", row["conservative_profit"]["reason"])
        self.assertEqual(row["cash_occupied"]["state"], "unknown")
        self.assertEqual(inputs["withheld_material_count"], 1)


if __name__ == "__main__":
    unittest.main()
