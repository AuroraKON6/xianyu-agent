"""Shared helpers for Phase 18 evidence-based publish preflight tests.

These helpers configure an enabled seller account with full readiness (prompt,
sync, runtime) and build fully-evidenced candidates so HTTP tests can exercise
the strict preflight boundary without repeating setup in every module.

Evidence timestamps default to real now but can be pinned to an explicit
``checked_at`` base so pure preflight tests stay deterministic on any calendar
date or timezone.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Optional

from xianyu_agent.account_prompt_profiles import build_bootstrap_ai_reply_settings
from xianyu_agent.account_runtime import AccountRuntimeStore
from xianyu_agent.account_commercial_terms import AccountCommercialTermsStore
from xianyu_agent.db_manager import DBManager

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"


def iso_now(base: Optional[float] = None) -> str:
    """Return a naive-local ISO timestamp for epoch `base` (defaults to now)."""
    return time.strftime(_ISO_FORMAT, time.localtime(time.time() if base is None else base))


def iso_hours_ago(hours: float, base: Optional[str] = None) -> str:
    """Return a naive-local ISO timestamp `hours` before (or after if negative)
    the `base` ISO string, which defaults to real now."""
    base_epoch = _iso_to_epoch(base) if base is not None else time.time()
    return iso_now(base_epoch - hours * 3600)


def iso_seconds_ago(seconds: float, base: Optional[str] = None) -> str:
    """Return a naive-local ISO timestamp `seconds` before the `base` ISO string."""
    base_epoch = _iso_to_epoch(base) if base is not None else time.time()
    return iso_now(base_epoch - seconds)


def _iso_to_epoch(value: str) -> float:
    return time.mktime(time.strptime(value.strip(), _ISO_FORMAT))


def add_preflight_account(db: DBManager, cookie_id: str = "acct_1") -> None:
    """Configure one enabled account with fresh prompt, sync, and runtime evidence."""
    db.save_cookie(cookie_id, f"cookie-value-{cookie_id}", "preflight test account")
    db.save_cookie_status(cookie_id, True)
    db.save_ai_reply_settings(cookie_id, build_bootstrap_ai_reply_settings({}))
    now = time.time()
    db.save_account_sync_state(cookie_id, "orders", adapter="stub", last_count=1, last_synced_at=now)
    db.save_account_sync_state(cookie_id, "listings", adapter="stub", last_count=1, last_synced_at=now)


def build_runtime_store(tmp: Path, cookie_id: str = "acct_1") -> AccountRuntimeStore:
    """Return a runtime store with the account recorded as authenticated."""
    store = AccountRuntimeStore(tmp / "runtime.json")
    store.record_authenticated_activity(cookie_id)
    return store


def build_commercial_terms_store(
    tmp: Path,
    manager,
    candidate: Dict,
    cookie_id: str = "acct_1",
    *,
    platform_fee_rate: float = 0.08,
    platform_fixed_fee: float = 0.0,
) -> AccountCommercialTermsStore:
    """Persist ready account/category terms and reprice the supplied candidate."""
    store = AccountCommercialTermsStore(tmp / "commercial_terms.json")
    record_ready_commercial_terms(
        store,
        manager,
        candidate,
        cookie_id,
        platform_fee_rate=platform_fee_rate,
        platform_fixed_fee=platform_fixed_fee,
    )
    return store


def record_ready_commercial_terms(
    store: AccountCommercialTermsStore,
    manager,
    candidate: Dict,
    cookie_id: str = "acct_1",
    *,
    platform_fee_rate: float = 0.08,
    platform_fixed_fee: float = 0.0,
) -> Dict:
    """Record ready terms in an existing store and return the repriced candidate."""
    prior_decision = candidate.get("manual_decision")
    prior_note = str(candidate.get("manual_decision_note") or "")
    snapshot = store.save_snapshot({
        "cookie_id": cookie_id,
        "category": candidate["category"],
        "seller_type": "business",
        "account_owner_confirmed": True,
        "seller_agreement_confirmed": True,
        "payment_service_ready": True,
        "category_publishable": True,
        "return_policy": "seven_day",
        "return_exception_basis": "",
        "return_address_ready": True,
        "after_sales_ready": True,
        "guarantee_required": False,
        "guarantee_ready": False,
        "guarantee_amount": 0,
        "platform_fee_rate": platform_fee_rate,
        "platform_fixed_fee": platform_fixed_fee,
        "observed_at": iso_now(),
        "evidence_note": "测试账号当前类目发布确认页已逐项核对。",
        "evidence_ref": "test-fixture/current-category-page.png",
    })
    manager.update_candidate_economics_from_commercial_terms(
        candidate["candidate_id"],
        cookie_id=cookie_id,
        category=candidate["category"],
        platform_fee_rate=snapshot["platform_fee_rate"],
        platform_fixed_fee=snapshot["platform_fixed_fee"],
        snapshot_version=snapshot["snapshot_version"],
        observed_at=snapshot["observed_at"],
        evidence_ref=snapshot["evidence_ref"],
        return_policy=snapshot["return_policy"],
        return_exception_basis=snapshot["return_exception_basis"],
    )
    if prior_decision == "approve":
        manager.set_decision(
            candidate["candidate_id"],
            "approve",
            prior_note or "测试夹具在商业条款重算后重新批准当前证据修订。",
        )
    return manager.get_candidate(candidate["candidate_id"])


def evidence_verify_payload(now_iso: Optional[str] = None) -> Dict:
    """Return a verify payload that satisfies every candidate-evidence gate.

    ``now_iso`` pins the evidence timestamps; omit to use real now.
    """
    now_iso = now_iso or iso_now()
    return {
        "supplier_verified": True,
        "supplier_verification_note": "供应商已确认价格、库存和发货时效。",
        "supplier_quote_checked_at": now_iso,
        "supplier_quote_evidence_ref": "test-fixture/supplier-quote-current.png",
        "minimum_order_quantity": 1,
        "stock_status": "in_stock",
        "stock_quantity": 5,
        "stock_checked_at": now_iso,
        "stock_evidence_ref": "test-fixture/supplier-stock-current.png",
        "fulfillment_verified": True,
        "fulfillment_verification_note": "供应商可正常发货，破损责任已确认。",
        "fulfillment_evidence_ref": "test-fixture/supplier-fulfillment-current.png",
        "neutral_shipping_confirmed": True,
        "dispatch_time_hours": 48,
        "supplier_return_policy": "conditional",
        "damage_liability_note": "错货由供应商补发，破损需在签收后提供外包装和商品照片。",
        "image_rights_type": "owned",
        "image_rights_evidence": "自有实拍照片，拍摄记录见供应商核对备注。",
        "image_rights_evidence_ref": "test-fixture/owned-image-record.png",
        "competitor_links": [
            "https://www.goofish.com/item?id=comparable-ops-table-1",
            "https://www.goofish.com/item?id=comparable-ops-table-2",
        ],
        "competitor_price_min": 1180.0,
        "competitor_price_max": 1480.0,
        "competitor_checked_at": now_iso,
        "competitor_evidence_ref": "test-fixture/competitor-baseline-current.png",
        "estimated_demand_signal": "当前搜索页存在两个规格相近的有效在售链接，仅作为小规模验证方向。",
    }


def archive_candidate_evidence_fixtures(manager, candidate_id: str) -> Dict:
    """Archive all five strict evidence kinds in the manager's isolated data dir."""
    candidate = manager.get_candidate(candidate_id)
    if candidate is None:
        raise ValueError("candidate not found")
    fallback_time = (
        candidate.get("supplier_quote_checked_at")
        or candidate.get("stock_checked_at")
        or candidate.get("competitor_checked_at")
        or iso_now()
    )
    artifact_times = {
        "supplier_quote": candidate.get("supplier_quote_checked_at") or fallback_time,
        "supplier_stock": candidate.get("stock_checked_at") or fallback_time,
        "supplier_fulfillment": fallback_time,
        "image_rights": fallback_time,
        "market_baseline": candidate.get("competitor_checked_at") or fallback_time,
    }
    for kind, observed_at in artifact_times.items():
        manager.archive_candidate_evidence(
            candidate_id,
            kind=kind,
            content=(
                f"candidate={candidate_id}\nkind={kind}\n"
                "fixture=operator evidence archived in the isolated test directory\n"
            ).encode("utf-8"),
            original_filename=f"{kind}.txt",
            mime_type="text/plain",
            observed_at=observed_at,
            note="Isolated test artifact for the strict candidate evidence archive.",
        )
    return manager.get_candidate(candidate_id)


def make_preflight_ready_candidate(
    manager,
    *,
    checked_at: Optional[str] = None,
    supplier_quote_age_hours: Optional[float] = None,
    stock_age_hours: Optional[float] = None,
    **overrides,
) -> Dict:
    """Create a candidate that passes every preflight gate.

    ``checked_at`` pins the evidence timestamps and should match the preflight
    ``checked_at`` argument for deterministic pure tests. A positive hour age
    for supplier_quote_age_hours / stock_age_hours makes that evidence stale; a
    negative age places it in the future. Extra overrides are merged into the
    create payload, so callers can also drop images, blank specifications, etc.
    """
    payload = {
        "title": "商用不锈钢操作台 带制冷",
        "description": "适合餐饮后厨、食堂备餐区使用。",
        "source_platform": "pdd",
        "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=ops-table",
        "supplier_name": "测试夹具供应商A",
        "supplier_sku": "OPS-1200-304",
        "supplier_price": 680.0,
        "supplier_shipping": 80.0,
        "target_sale_price": 1280.0,
        "category": "商用厨具",
        "specifications": {"material": "304不锈钢", "size": "120x60x80cm"},
        "images": ["test-fixture/owned-ops-table.jpg"],
    }
    payload.update(overrides)
    candidate = manager.create_candidate(payload)
    candidate_id = candidate["candidate_id"]
    verify = evidence_verify_payload(now_iso=checked_at)
    if supplier_quote_age_hours is not None:
        verify["supplier_quote_checked_at"] = iso_hours_ago(supplier_quote_age_hours, base=checked_at)
    if stock_age_hours is not None:
        verify["stock_checked_at"] = iso_hours_ago(stock_age_hours, base=checked_at)
    manager.verify_candidate(candidate_id, verify)
    archive_candidate_evidence_fixtures(manager, candidate_id)
    manager.set_decision(candidate_id, "approve")
    return manager.get_candidate(candidate_id)
