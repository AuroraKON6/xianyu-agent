"""Expanded real-public-source run: 6 wholesale + 8 retail observations from
sources.md through the full pipeline, then an honest spread summary.

No product code changes; isolated directory; no confirmation, no platform
action. Display-price spreads are REPORTED, never claimed as profit
(freight/fees/packaging all unobserved -> unknown).
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from run_demo import market_observation  # noqa: E402

from xianyu_agent.commerce.repositories import JsonCommerceRepositories  # noqa: E402
from xianyu_agent.radar_dispatch import build_radar  # noqa: E402
from xianyu_agent.supplier_import import SupplierImportService  # noqa: E402

DATA = HERE / "data-expanded"
OBSERVED_AT = datetime.now(timezone.utc)

WHOLESALE = [
    # sku, name, price, spec, brand, ref
    ("MX-201-DBL", "美星双层加厚不锈钢工作台 201不锈钢打荷操作台厨房商用", "360", "双层加厚 201不锈钢", "美星", "public-web:https://s.1688.com (search: 美星双层加厚 201)"),
    ("CJ-304-DBL", "304不锈钢工作台 厂家定制双层厨房操作台 201加厚实验室桌子带靠背", "473.7", "双层 304不锈钢", "未具名", "public-web:https://www.1688.com (search: 304 双层)"),
    ("HXL-ZSJ", "不锈钢展示层架 商用（鸿鑫隆）", "191.10", "不锈钢 层架", "鸿鑫隆", "public-web:https://s.1688.com (search: 不锈钢货架 商用 四层)"),
    ("LSJ-65", "沥水碗架 65cm 黑色标准款（区间68-92，取上限）", "92", "不锈钢 65cm", "未具名", "public-web:1688 沥水架检索（区间价取保守上限）"),
    ("CD-304-SX", "304不锈钢水槽 三眼双星水池 厨房洗碗池 商用饭店用（成都）", "458", "304 双星水池", "未具名", "public-web:https://s.1688.com/kq/-不锈钢双星水池批发"),
    ("304-SXG", "304不锈钢水池水槽柜 家/商用厨房双星洗菜洗手盆洗碗池", "439.85", "304 双星水槽柜", "未具名", "public-web:同上检索"),
    ("CMT-TJ", "不锈钢调料台（创美特）", "295", "不锈钢 调料台", "创美特", "public-web:https://s.1688.com (search: 不锈钢调料台 批发)"),
    ("HT-CC", "不锈钢推车定做 商用餐车（区间611-1499，取海涛具体价）", "735", "不锈钢 定做推车", "海涛", "public-web:https://s.1688.com (search: 不锈钢推车定做)"),
]
RETAIL = [
    # sku(对齐批发SKU 或 None=仅观察), title, price, note
    ("MX-201-DBL", "不锈钢工作台 桌厨房加工 台面层架（小规格入门款）", "131.88", "sources.md M1"),
    ("MX-201-DBL", "无品牌不锈钢工作台 商用打包台（加厚壁管款）", "761", "sources.md M2"),
    ("CJ-304-DBL", "加厚双层不锈钢工作台 商用操作台餐馆切菜桌多层打包台", "350", "sources.md M8：商用加厚，已售100+/500+评价"),
    ("HXL-ZSJ", "不锈钢厨房置物架落地多层（简易/家用款）", "61.25", "sources.md M3：家用款"),
    ("HXL-ZSJ", "304不锈钢货架 无尘车间中型四层架子", "1193.70", "sources.md M4：304 重型"),
    ("LSJ-65", "食品级不锈钢盆洗菜盆沥水篮 漏盆（加厚家用）", "28.2", "sources.md M5：家用小规格"),
    ("LSJ-65", "加厚不锈钢沥水卷帘 可折叠沥水架", "8.50", "sources.md M6：家用小件"),
    ("CD-304-SX", "不锈钢水槽 单双星水池商用厨房定制加厚三星洗碗池", "538.75", "sources.md M7"),
    ("304-SXG", "304不锈钢水槽 单槽双盆 台面一体式带支架厨房柜", "310", "sources.md M9：月销100+"),
    ("CMT-TJ", "304不锈钢工作台 厨房操作台（打荷台、调料台）", "300", "sources.md M10：约300起，500+人付款"),
    ("CMT-TJ", "304加厚不锈钢工作台 餐饮饭店打包台打荷台可定制", "1150", "sources.md M11：加厚定制款"),
    ("HT-CC", "304不锈钢收碗车 餐厅双层送餐车小推车", "208", "sources.md M13：已售100+/900+评价"),
    ("HT-CC", "加厚三层不锈钢小推车 送餐车收碗车双层", "161", "sources.md M12：三层款"),
    (None, "威旻保鲜工作台 组装款 80x40x80cm 单通（京东工业品，5000+评价）", "0", "仅记录存在性与规格线索，无展示价——不入价目表"),
]


def main() -> int:
    if DATA.exists():
        shutil.rmtree(DATA)
    repos = JsonCommerceRepositories(DATA)
    imports = SupplierImportService(repos, DATA)

    header = ("货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色,"
              "source_kind,source_reference,source_observed_at,valid_until")
    valid_until = (OBSERVED_AT + timedelta(hours=24)).isoformat()
    rows = [f"{sku},{name},{price},件,,,,{spec},{brand},全新,"
            f"supplier_original,{ref},{OBSERVED_AT.isoformat()},{valid_until}"
            for sku, name, price, spec, brand, ref in WHOLESALE]
    payload = (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")
    preview = imports.preview_bytes(payload, kind="csv", filename="public-web-wholesale-expanded.csv")
    batch = imports.confirm(
        preview["preview_id"], content_sha256=preview["content_sha256"],
        revision=preview["revision"], supplier="公开网页批发观察-扩充（未经确认）",
        operator="glm-real-source-demo", include_needs_confirmation=True,
    )["batch"]
    print(f"[1] wholesale import: {batch['record_id']} rows={len(batch['snapshot_ids'])}")

    market_ids = {}
    for sku, title, price, note in RETAIL:
        if price == "0":
            continue  # 无展示价，仅存在性记录，不入观察账
        stored = market_observation(
            repos, sku=sku, title=title, price=price,
            url="https://my.world.taobao.com" if "M1" in note or "M2" in note else "https://www.taobao.com",
            note=note,
        )
        market_ids.setdefault(sku, []).append(stored["record_id"])
    print(f"[2] retail observations: {sum(len(v) for v in market_ids.values())} across {len(market_ids)} SKUs")

    dispatcher, events, _ = build_radar(repos, DATA)
    for sku, snapshot in ((s["source_item_id"], s) for s in imports.list_batches()[-1] and []):
        pass
    from xianyu_agent.commerce.contracts import utc_now  # noqa: E402

    for row in WHOLESALE:
        snap = [s for s in repos.offers.list() if s.get("source_item_id") == row[0]
                and str(s.get("provider_id", "")).startswith("supplier_file:")][-1]
        events.record(
            kind="intake_confirmed", scope_sku=row[0],
            supplier_snapshot_id=snap["record_id"],
            input_digest=f"{batch['content_sha256'][:16]}:{row[0]}",
            idempotency_key=f"demo-x:{row[0]}",
        )
    for _ in range(30):
        dispatcher.tick()
    statuses = [(e["scope_sku"], e["status"]) for e in events.list()]
    processed = sum(1 for _, s in statuses if s == "processed")
    print(f"[3] radar: {processed}/{len(statuses)} processed; awaiting: {[s for s in statuses if s[1] != 'processed']}")

    summary = {"generated_at": OBSERVED_AT.isoformat(), "pairs": []}
    for opportunity in repos.opportunities.list():
        economics = opportunity.get("economics") or {}
        inputs = economics.get("inputs") or {}
        conservative = (economics.get("scenarios") or {}).get("conservative") or {}
        supplier_snap = repos.offers.get(inputs.get("supplier_snapshot_id") or "") or {}
        market_snap = repos.offers.get(inputs.get("market_snapshot_id") or "") or {}
        summary["pairs"].append({
            "sku": opportunity.get("canonical_sku"),
            "status": opportunity.get("status"),
            "match_grade": inputs.get("match_grade"),
            "wholesale_display": (supplier_snap.get("eligible_price") or {}).get("amount"),
            "retail_display": (market_snap.get("eligible_price") or {}).get("amount"),
            "display_spread_note": "展示价差仅供研究方向参考；运费/费率/包装/退货全未观察，贡献利润 unknown",
            "profit_state": (conservative.get("contribution_profit") or {}).get("state"),
            "missing_costs": conservative.get("missing"),
        })
    (HERE / "expanded_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[4] summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
