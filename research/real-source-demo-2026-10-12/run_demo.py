"""Real-public-source pipeline demo (G2C dry run with REAL observed prices).

Runs the full commerce pipeline against an ISOLATED demo data dir using the
verbatim public-web observations archived in sources.md -- no fixture
numbers, no fabricated fields, no material confirmation, no platform
action. Product code is untouched; this is a data-driven demonstration.

Usage: python research/real-source-demo-2026-10-12/run_demo.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from xianyu_agent.candidate_evidence_store import CandidateEvidenceStore  # noqa: E402
from xianyu_agent.commerce.contracts import (  # noqa: E402
    Money,
    build_offer_snapshot,
    utc_now,
)
from xianyu_agent.commerce.repositories import JsonCommerceRepositories  # noqa: E402
from xianyu_agent.radar_dispatch import build_radar  # noqa: E402
from xianyu_agent.supplier_import import SupplierImportService  # noqa: E402

DATA = HERE / "data"
OBSERVED_AT = datetime.now(timezone.utc)


def supplier_import(repos, imports):
    # verbatim from sources.md W1/W2; ONLY observed fields, nothing inferred.
    # provenance columns are merged INTO each data row (import contract).
    refs = [
        "public-web:https://s.1688.com (search: 美星双层加厚 201)",
        "public-web:https://www.1688.com (search: 304 双层)",
    ]
    header = ("货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色,"
              "source_kind,source_reference,source_observed_at,valid_until")
    valid_until = (OBSERVED_AT + timedelta(hours=24)).isoformat()
    rows = []
    for row, ref in zip(
        [
            "MX-201-DBL,美星双层加厚不锈钢工作台 201不锈钢打荷操作台厨房商用,360,件,,,,双层加厚 201不锈钢,美星,全新",
            "CHANGJIA-304-DBL,304不锈钢工作台 厂家定制双层厨房操作台 201加厚实验室桌子带靠背,473.7,件,,,,双层 304不锈钢,未具名,全新",
        ],
        refs,
    ):
        rows.append(f"{row},supplier_original,{ref},{OBSERVED_AT.isoformat()},{valid_until}")
    payload = (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")
    preview = imports.preview_bytes(payload, kind="csv", filename="public-web-wholesale-2026-10-12.csv")
    result = imports.confirm(
        preview["preview_id"], content_sha256=preview["content_sha256"],
        revision=preview["revision"], supplier="公开网页批发观察（未经确认）", operator="glm-real-source-demo",
        include_needs_confirmation=True,  # freight/MOQ/stock were NOT observed -> stay unknown
    )
    return payload, result


def market_observation(repos, *, sku, title, price, url, note):
    observed = utc_now()
    stored, _ = repos.offers.add(build_offer_snapshot(
        provider_id="market:public_web_observation",
        source_item_id=sku, source_sku_id=sku,
        observed_at=observed, fetched_at=observed,
        expires_at=observed + timedelta(hours=72),
        unit="件", quantity=None, condition="全新",
        attributes={
            "title": title,
            "observation_provenance": {
                "kind": "public_web_search", "url": url, "observed_via": "web_search 2026-10-12",
                "note": note,
            },
        },
        price_type="display",
        eligible_price=Money.known(Decimal(price)).to_dict(),
        shipping_quote=Money.unknown("not observed").to_dict(),
        stock_state="unknown", stock_quantity=None, moq=None, tier_prices=[],
        tax_mode="unknown", seller_ref="",
        payload_hash=f"demo:{sku}:{price}:{observed.isoformat()}",
        account_id="", source_ref="market:public_web_observation",
    ))
    return stored


def main() -> int:
    if DATA.exists():
        import shutil

        shutil.rmtree(DATA)
    repos = JsonCommerceRepositories(DATA)
    imports = SupplierImportService(repos, DATA)
    evidence = CandidateEvidenceStore(DATA / "candidate_evidence")
    dispatcher, events, _ = build_radar(repos, DATA)  # no Pi submission channel

    payload, batch = supplier_import(repos, imports)
    snapshots = {s["source_item_id"]: s for s in batch["snapshots"]}
    print(f"[1] supplier import: batch={batch['batch']['record_id']} "
          f"snapshots={list(snapshots)} supplier={batch['batch']['supplier']}")

    markets = [
        market_observation(
            repos, sku="MX-201-DBL", title="不锈钢工作台 桌厨房加工 台面层架（小规格入门款）",
            price="131.88", url="https://my.world.taobao.com",
            note="sources.md M1：小规格入门款展示价，材质/尺寸未展示",
        ),
        market_observation(
            repos, sku="MX-201-DBL", title="无品牌不锈钢工作台 商用打包台（加厚壁管款）",
            price="761", url="https://my.world.taobao.com",
            note="sources.md M2：加厚款展示价，材质/尺寸未展示",
        ),
    ]
    print(f"[2] market observations: {[m['record_id'] for m in markets]}")

    for sku, snapshot in snapshots.items():
        events.record(
            kind="intake_confirmed", scope_sku=sku,
            supplier_snapshot_id=snapshot["record_id"],
            input_digest=str(batch["batch"]["content_sha256"])[:16] + f":{sku}",
            idempotency_key=f"demo-real:{sku}",
        )
    for _ in range(10):
        dispatcher.tick()
    processed = [e for e in events.list() if e.get("status") == "processed"]
    print(f"[3] radar events processed: {len(processed)}/{len(events.list())} "
          f"(others: {[(e['scope_sku'], e['status'], e.get('note', '')[:60]) for e in events.list() if e.get('status') != 'processed']})")

    report = {"generated_at": OBSERVED_AT.isoformat(), "supplier_batch": batch["batch"]["record_id"]}
    for opportunity in repos.opportunities.list():
        card = {}
        dispatcher._jobs  # noqa: B018
        from xianyu_agent.intake_confirmation import IntakeConfirmationService  # noqa: E402

        service = IntakeConfirmationService(repos, imports, evidence, DATA)
        economics = opportunity.get("economics") or {}
        inputs = economics.get("inputs") or {}
        conservative = (economics.get("scenarios") or {}).get("conservative") or {}
        card = {
            "opportunity_id": opportunity["record_id"],
            "sku": opportunity.get("canonical_sku"),
            "status": opportunity.get("status"),
            "match_grade": inputs.get("match_grade"),
            "sale_price_basis": inputs.get("sale_price_basis"),
            "conservative_profit": conservative.get("contribution_profit"),
            "missing_costs": conservative.get("missing"),
            "material_use": service.eligibility(inputs.get("supplier_snapshot_id") or "", use="local_analysis"),
        }
        report[opportunity["record_id"]] = card
    print("[4] cards:")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    (HERE / "pipeline_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
