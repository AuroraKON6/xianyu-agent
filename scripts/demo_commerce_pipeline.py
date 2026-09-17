"""End-to-end thin-loop demonstration (WP01/03/05/06/09 minimal).

Runs the full commerce pipeline in an isolated temporary data dir:

    supplier quote file (preview -> confirm)
      -> market observation
      -> same-product hard-constraint match
      -> contribution economics (3 scenarios)
      -> opportunity qualification
      -> traceable decision card
      -> funnel projection

The data is the golden example from the master plan section 8.3 and is
SYNTHETIC DEMO DATA, not a real supplier quote or market fact. The
output is saved as evidence under output/commerce_demo/.

Usage:
    .venv/Scripts/python.exe scripts/demo_commerce_pipeline.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xianyu_agent.commerce import JsonCommerceRepositories  # noqa: E402
from xianyu_agent.opportunity_service import OpportunityService  # noqa: E402
from xianyu_agent.product_matching import ProductMatchingService  # noqa: E402
from xianyu_agent.supplier_import import SupplierImportService  # noqa: E402
from xianyu_agent.unit_economics import SCENARIO_BASE, SCENARIO_CONSERVATIVE  # noqa: E402

QUOTE_CSV = (
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
    "GZ-1200,商用不锈钢工作台,150,件,2,10,15,120x60x85cm,众商,全新\n"
)

DEMO_NOTE = (
    "SYNTHETIC DEMO DATA: numbers follow master plan 8.3; no real supplier, "
    "no real market listing, no platform action, no profit claimed"
)


def main() -> int:
    out_dir = ROOT / "output" / "commerce_demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"demo_note": DEMO_NOTE, "executed_at": datetime.now(timezone.utc).isoformat()}

    with tempfile.TemporaryDirectory() as tmp:
        repos = JsonCommerceRepositories(Path(tmp))
        importer = SupplierImportService(repos, Path(tmp))
        matcher = ProductMatchingService(repos)
        opportunities = OpportunityService(repos)

        # 1. supplier quote import: preview -> confirm
        preview = importer.preview_bytes(QUOTE_CSV.encode("utf-8"), kind="csv", filename="demo_quote.csv")
        evidence["step1_preview_summary"] = preview["summary"]
        confirm = importer.confirm(
            preview["preview_id"],
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="演示供应商（合成数据）",
            operator="demo",
        )
        supplier_snapshot = confirm["snapshots"][0]
        evidence["step1_confirm"] = {
            "batch_id": confirm["batch"]["record_id"],
            "snapshot_id": supplier_snapshot["record_id"],
            "price": supplier_snapshot["eligible_price"],
            "freight": supplier_snapshot["shipping_quote"],
        }

        # 2. market observation (manual, synthetic)
        from xianyu_agent.commerce.contracts import PRICE_TYPE_LIST, build_offer_snapshot

        market_snapshot = build_offer_snapshot(
            provider_id="market:xianyu_observation",
            source_item_id="GZ-1200",
            source_sku_id="GZ-1200",
            observed_at=datetime.now(timezone.utc),
            attributes={"name": "不锈钢工作台 GZ-1200 全新", "spec": "120x60x85cm", "brand": "众商"},
            condition="全新",
            unit="件",
            price_type=PRICE_TYPE_LIST,
            eligible_price={"state": "known", "amount": "299", "currency": "CNY"},
            payload_hash="demo-market-observation-1",
            account_id="",
            source_ref="market:manual_observation",
        )
        market_snapshot, _ = repos.offers.add(market_snapshot)
        evidence["step2_market_observation"] = {
            "snapshot_id": market_snapshot["record_id"],
            "price": market_snapshot["eligible_price"],
        }

        # 3. hard-constraint match
        match_result = matcher.match_snapshots(market_snapshot["record_id"], supplier_snapshot["record_id"])
        evidence["step3_match"] = {
            "match_id": match_result["match_id"],
            "grade": match_result["grade"],
            "explanation": match_result["explanation"],
            "recall_score": match_result["recall_score"],
        }

        # 4. opportunity + full contribution economics
        opportunity = opportunities.create_from_match(
            market_snapshot_id=market_snapshot["record_id"],
            supplier_snapshot_id=supplier_snapshot["record_id"],
            match_id=match_result["match_id"],
        )
        evaluation = opportunities.evaluate(
            opportunity["record_id"],
            platform_fee_rate="0.03",
            packaging={"state": "known", "amount": "5", "currency": "CNY"},
            expected_return_loss={"state": "known", "amount": "12", "currency": "CNY"},
            attributable_costs={"state": "known", "amount": "4", "currency": "CNY"},
            scenario_overrides={
                "conservative": {"sale_price": {"state": "known", "amount": "270", "currency": "CNY"}},
                "optimistic": {"sale_price": {"state": "known", "amount": "320", "currency": "CNY"}},
            },
        )
        base = evaluation["economics"]["scenarios"][SCENARIO_BASE]
        conservative = evaluation["economics"]["scenarios"][SCENARIO_CONSERVATIVE]
        evidence["step4_economics"] = {
            "opportunity_id": evaluation["opportunity"]["record_id"],
            "status": evaluation["opportunity"]["status"],
            "qualification": evaluation["qualification"],
            "base_profit": base["contribution_profit"],
            "conservative_profit": conservative["contribution_profit"],
            "cash_occupied": evaluation["economics"]["cash_occupied"],
        }

        # 5. decision card
        card = opportunities.decision_card(evaluation["opportunity"]["record_id"])
        latest_decision = card["decisions"][-1]
        evidence["step5_decision_card"] = {
            "decision_id": latest_decision["record_id"],
            "actor_role": latest_decision["actor_role"],
            "summary": latest_decision["summary"],
            "recommendation": latest_decision["recommendation"],
            "constraint_checks": latest_decision["constraint_checks"],
            "unknowns": latest_decision["unknowns"],
            "evidence_refs": latest_decision["evidence_refs"],
            "authorization_type": latest_decision["authorization_type"],
        }
        evidence["step6_funnel"] = card["funnel"]

    evidence_path = out_dir / "thin_loop_evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    print(DEMO_NOTE)
    print(f"1. import      : batch {evidence['step1_confirm']['batch_id']} price {evidence['step1_confirm']['price']['amount']}")
    print(f"2. observation : {evidence['step2_market_observation']['snapshot_id']} @299")
    print(f"3. match       : {evidence['step3_match']['grade']} (recall {evidence['step3_match']['recall_score']} report-only)")
    print(f"4. economics   : base profit {evidence['step4_economics']['base_profit']['amount']} | conservative {evidence['step4_economics']['conservative_profit']['amount']}")
    print(f"5. status      : {evidence['step4_economics']['status']} qualified={evidence['step4_economics']['qualification']['qualified']}")
    print(f"6. decision    : {evidence['step5_decision_card']['decision_id']} ({evidence['step5_decision_card']['actor_role']})")
    print(f"funnel         : {evidence['step6_funnel']}")
    print(f"evidence       : {evidence_path}")

    # golden-value assertion: the demo must reproduce 8.3 by computation
    assert Decimal(evidence["step4_economics"]["base_profit"]["amount"]) == Decimal("104.03"), "golden example broken"
    return 0


if __name__ == "__main__":
    sys.exit(main())
