"""Deterministic evidence packet for one real Xianyu launch candidate.

This module is deliberately independent from accounts, databases and browser
state. It answers one narrower question: has the operator recorded enough
current, attributable product evidence to make a one-item commercial decision?

The packet does not prove that a screenshot or supplier statement is true. It
does reject missing, stale and obvious placeholder references, versions the
facts that were approved, and gives the operator an ordered list of work that
still has to be completed.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .candidate_evidence_store import parse_artifact_reference


QUOTE_MAX_AGE_SECONDS = 24 * 60 * 60
STOCK_MAX_AGE_SECONDS = 24 * 60 * 60
MARKET_MAX_AGE_SECONDS = 72 * 60 * 60
IMAGE_RIGHTS_TYPES = {"owned", "supplier_authorized", "licensed"}
SOURCE_PLATFORMS = {"pdd", "1688"}
READY_STOCK_STATUSES = {"in_stock", "limited"}
RETURN_POLICIES = {"supported", "conditional", "not_supported"}

GATE_CODES = (
    "channel_scope",
    "product_identity",
    "supplier_source",
    "supplier_verified",
    "supplier_quote_evidence",
    "supplier_stock_evidence",
    "supplier_fulfillment_evidence",
    "image_assets",
    "image_rights",
    "market_baseline",
    "unit_economics",
    "manual_decision_current",
)

MATERIAL_FIELDS = (
    "title",
    "category",
    "source_platform",
    "source_url",
    "supplier_name",
    "supplier_sku",
    "supplier_price",
    "supplier_shipping",
    "target_sale_price",
    "specifications",
    "supplier_verified",
    "supplier_verification_note",
    "supplier_quote_checked_at",
    "supplier_quote_evidence_ref",
    "minimum_order_quantity",
    "stock_status",
    "stock_quantity",
    "stock_checked_at",
    "stock_evidence_ref",
    "fulfillment_verified",
    "fulfillment_verification_note",
    "fulfillment_evidence_ref",
    "neutral_shipping_confirmed",
    "dispatch_time_hours",
    "supplier_return_policy",
    "damage_liability_note",
    "images",
    "image_rights_type",
    "image_rights_evidence",
    "image_rights_evidence_ref",
    "competitor_links",
    "competitor_price_min",
    "competitor_price_max",
    "competitor_checked_at",
    "competitor_evidence_ref",
    "estimated_demand_signal",
    "sales_platform",
    "market_scope",
    "economics",
    "commercial_terms_version",
)

_PLACEHOLDER_TITLES = {
    "t", "test", "testing", "demo", "sample", "placeholder",
    "测试", "测试商品", "样品", "占位", "占位商品",
}
_PLACEHOLDER_REF_VALUES = {
    "test", "demo", "sample", "placeholder", "none", "n/a", "na",
    "done", "yes", "ok", "已完成", "已确认", "截图", "见截图",
}
_EVIDENCE_FILE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".html", ".htm",
    ".md", ".txt", ".json",
}
_SENSITIVE_PATTERN = re.compile(
    r"(?i)(cookie\s*[=:]|authorization\s*[=:]|bearer\s+[a-z0-9._-]+|"
    r"token\s*[=:]|password\s*[=:]|\b1\d{10}\b|\b\d{15,18}[0-9xX]\b)"
)
_LOCAL_TZ = datetime.now().astimezone().tzinfo


def candidate_evidence_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Return a stable fingerprint for every fact covered by manual approval."""
    payload = {field: _json_safe(candidate.get(field)) for field in MATERIAL_FIELDS}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def evaluate_candidate_activation(
    *,
    candidate: Mapping[str, Any],
    settings: Any,
    checked_at: Optional[str] = None,
    evidence_artifacts: Optional[Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]] = None,
    require_archived_evidence: bool = False,
) -> Dict[str, Any]:
    """Build the ordered, read-only one-product activation packet."""
    candidate = dict(candidate or {})
    artifact_lookup = _normalize_artifact_lookup(evidence_artifacts)
    candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
    checked_dt = _parse_datetime(checked_at) or datetime.now(_LOCAL_TZ)
    now = checked_dt if checked_dt.tzinfo is not None else checked_dt.replace(tzinfo=_LOCAL_TZ)
    checked_at_z = _as_utc_z(now)
    fingerprint = candidate_evidence_fingerprint(candidate)
    gates: List[Dict[str, Any]] = []

    sales_platform = str(candidate.get("sales_platform") or "xianyu").strip().lower()
    market_scope = str(candidate.get("market_scope") or "domestic").strip().lower()
    gates.append(_gate(
        "channel_scope",
        sales_platform == "xianyu" and market_scope == "domestic",
        "确认项目与销售渠道",
        f"sales_platform={sales_platform or 'missing'}/market_scope={market_scope or 'missing'}",
        "operator",
        "将候选限定为闲鱼国内项目，跨境候选留在独立项目",
    ))

    title = str(candidate.get("title") or "").strip()
    category = str(candidate.get("category") or "").strip()
    sku = str(candidate.get("supplier_sku") or "").strip()
    specs_count = _meaningful_spec_count(candidate.get("specifications"))
    identity_ready = (
        len(title) >= 4
        and title.casefold() not in _PLACEHOLDER_TITLES
        and len(category) >= 2
        and len(sku) >= 2
        and specs_count >= 2
    )
    gates.append(_gate(
        "product_identity",
        identity_ready,
        "锁定商品身份与精确 SKU",
        "title={}/category={}/sku={}/specs={}".format(
            "ok" if len(title) >= 4 and title.casefold() not in _PLACEHOLDER_TITLES else "invalid",
            "ok" if len(category) >= 2 else "missing",
            "ok" if len(sku) >= 2 else "missing",
            specs_count,
        ),
        "operator",
        "填写真实商品标题、平台类目、供应商 SKU，并至少记录两个可核对规格",
    ))

    source_platform = str(candidate.get("source_platform") or "").strip().lower()
    source_url = str(candidate.get("source_url") or "").strip()
    supplier_name = str(candidate.get("supplier_name") or "").strip()
    source_ready = (
        source_platform in SOURCE_PLATFORMS
        and _valid_external_url(source_url)
        and len(supplier_name) >= 2
    )
    gates.append(_gate(
        "supplier_source",
        source_ready,
        "绑定可追溯供应商来源",
        "platform={}/url={}/supplier={}".format(
            source_platform or "missing",
            "valid" if _valid_external_url(source_url) else "invalid",
            "present" if len(supplier_name) >= 2 else "missing",
        ),
        "operator",
        "记录当前供应商平台、真实商品链接和可识别的供应商简称",
    ))

    supplier_verified = bool(candidate.get("supplier_verified"))
    supplier_note = str(candidate.get("supplier_verification_note") or "").strip()
    gates.append(_gate(
        "supplier_verified",
        supplier_verified and _substantive_text(supplier_note),
        "核实供应商身份与可交易状态",
        "verified={}/note={}".format(
            "yes" if supplier_verified else "no",
            "substantive" if _substantive_text(supplier_note) else "insufficient",
        ),
        "operator",
        "记录核对对象、沟通渠道和结论；一句“已确认”不能作为证据",
    ))

    supplier_price = _finite(candidate.get("supplier_price"))
    supplier_shipping = _finite(candidate.get("supplier_shipping"))
    moq = _positive_int(candidate.get("minimum_order_quantity"))
    quote_ref = str(candidate.get("supplier_quote_evidence_ref") or "").strip()
    quote_ref_valid, quote_ref_status, quote_observed_at = _archived_evidence_state(
        reference=quote_ref,
        candidate_id=candidate_id,
        expected_kind="supplier_quote",
        expected_observed_at=candidate.get("supplier_quote_checked_at"),
        artifacts=artifact_lookup,
        require_archived=require_archived_evidence,
    )
    quote_fresh, quote_age = _freshness(
        quote_observed_at or candidate.get("supplier_quote_checked_at"), now, QUOTE_MAX_AGE_SECONDS,
    )
    quote_ready = bool(
        supplier_price is not None and supplier_price > 0
        and supplier_shipping is not None and supplier_shipping >= 0
        and moq is not None
        and quote_fresh
        and quote_ref_valid
    )
    gates.append(_gate(
        "supplier_quote_evidence",
        quote_ready,
        "确认实付采购价、运费与起订量",
        "price={}/shipping={}/moq={}/age={}/ref={}".format(
            _money_or_unknown(supplier_price),
            _money_or_unknown(supplier_shipping),
            moq if moq is not None else "invalid",
            quote_age,
            quote_ref_status,
        ),
        "operator",
        "在 24 小时内核对精确 SKU 的实付价、运费和最低采购量，并记录页面或沟通凭证引用",
    ))

    stock_status = str(candidate.get("stock_status") or "").strip().lower()
    stock_quantity = _positive_int(candidate.get("stock_quantity"))
    stock_ref = str(candidate.get("stock_evidence_ref") or "").strip()
    stock_ref_valid, stock_ref_status, stock_observed_at = _archived_evidence_state(
        reference=stock_ref,
        candidate_id=candidate_id,
        expected_kind="supplier_stock",
        expected_observed_at=candidate.get("stock_checked_at"),
        artifacts=artifact_lookup,
        require_archived=require_archived_evidence,
    )
    stock_fresh, stock_age = _freshness(
        stock_observed_at or candidate.get("stock_checked_at"), now, STOCK_MAX_AGE_SECONDS,
    )
    stock_ready = bool(
        stock_status in READY_STOCK_STATUSES
        and stock_quantity is not None
        and stock_fresh
        and stock_ref_valid
    )
    gates.append(_gate(
        "supplier_stock_evidence",
        stock_ready,
        "确认当前可售库存",
        "status={}/quantity={}/age={}/ref={}".format(
            stock_status or "missing",
            stock_quantity if stock_quantity is not None else "invalid",
            stock_age,
            stock_ref_status,
        ),
        "operator",
        "在 24 小时内确认至少一件可发库存；预售或库存未知不能进入首轮 Alpha",
    ))

    fulfillment_verified = bool(candidate.get("fulfillment_verified"))
    fulfillment_note = str(candidate.get("fulfillment_verification_note") or "").strip()
    fulfillment_ref = str(candidate.get("fulfillment_evidence_ref") or "").strip()
    fulfillment_ref_valid, fulfillment_ref_status, _ = _archived_evidence_state(
        reference=fulfillment_ref,
        candidate_id=candidate_id,
        expected_kind="supplier_fulfillment",
        expected_observed_at=None,
        artifacts=artifact_lookup,
        require_archived=require_archived_evidence,
    )
    dispatch_hours = _positive_number(candidate.get("dispatch_time_hours"))
    return_policy = str(candidate.get("supplier_return_policy") or "").strip().lower()
    damage_note = str(candidate.get("damage_liability_note") or "").strip()
    fulfillment_ready = bool(
        fulfillment_verified
        and _substantive_text(fulfillment_note)
        and fulfillment_ref_valid
        and bool(candidate.get("neutral_shipping_confirmed"))
        and dispatch_hours is not None and dispatch_hours <= 168
        and return_policy in RETURN_POLICIES
        and _substantive_text(damage_note)
    )
    gates.append(_gate(
        "supplier_fulfillment_evidence",
        fulfillment_ready,
        "确认供应商履约与售后边界",
        "verified={}/neutral_shipping={}/dispatch={}/return={}/damage={}/ref={}".format(
            "yes" if fulfillment_verified else "no",
            "yes" if candidate.get("neutral_shipping_confirmed") else "no",
            f"{dispatch_hours:g}h" if dispatch_hours is not None else "invalid",
            return_policy or "missing",
            "substantive" if _substantive_text(damage_note) else "insufficient",
            fulfillment_ref_status,
        ),
        "operator",
        "确认正常无价格信息发货、出库时效、退换条件和破损责任，并保存凭证引用",
    ))

    image_count = _valid_image_count(candidate.get("images"))
    gates.append(_gate(
        "image_assets",
        image_count >= 1,
        "准备可发布商品图片",
        f"valid_images={image_count}",
        "operator",
        "至少提供一张自有或已获准使用的真实商品图片；示例域名和空字符串无效",
    ))

    rights_type = str(candidate.get("image_rights_type") or "").strip().lower()
    rights_note = str(candidate.get("image_rights_evidence") or "").strip()
    rights_ref = str(candidate.get("image_rights_evidence_ref") or "").strip()
    rights_ref_valid, rights_ref_status, _ = _archived_evidence_state(
        reference=rights_ref,
        candidate_id=candidate_id,
        expected_kind="image_rights",
        expected_observed_at=None,
        artifacts=artifact_lookup,
        require_archived=require_archived_evidence,
    )
    gates.append(_gate(
        "image_rights",
        rights_type in IMAGE_RIGHTS_TYPES
        and _substantive_text(rights_note)
        and rights_ref_valid,
        "确认图片使用权",
        "type={}/note={}/ref={}".format(
            rights_type or "missing",
            "substantive" if _substantive_text(rights_note) else "insufficient",
            rights_ref_status,
        ),
        "operator",
        "记录自有实拍、供应商授权或可商用许可的结论及对应凭证引用",
    ))

    competitor_links = _valid_unique_urls(candidate.get("competitor_links"))
    price_min = _positive_number(candidate.get("competitor_price_min"))
    price_max = _positive_number(candidate.get("competitor_price_max"))
    demand_note = str(candidate.get("estimated_demand_signal") or "").strip()
    market_ref = str(candidate.get("competitor_evidence_ref") or "").strip()
    market_ref_valid, market_ref_status, market_observed_at = _archived_evidence_state(
        reference=market_ref,
        candidate_id=candidate_id,
        expected_kind="market_baseline",
        expected_observed_at=candidate.get("competitor_checked_at"),
        artifacts=artifact_lookup,
        require_archived=require_archived_evidence,
    )
    market_fresh, market_age = _freshness(
        market_observed_at or candidate.get("competitor_checked_at"), now, MARKET_MAX_AGE_SECONDS,
    )
    market_ready = bool(
        len(competitor_links) >= 2
        and price_min is not None and price_max is not None and price_min <= price_max
        and _substantive_text(demand_note)
        and market_fresh
        and market_ref_valid
    )
    gates.append(_gate(
        "market_baseline",
        market_ready,
        "建立当前竞品与需求基线",
        "links={}/price_range={}/age={}/signal={}/ref={}".format(
            len(competitor_links),
            "valid" if price_min is not None and price_max is not None and price_min <= price_max else "invalid",
            market_age,
            "substantive" if _substantive_text(demand_note) else "insufficient",
            market_ref_status,
        ),
        "operator+pi",
        "保留至少两个可比闲鱼链接、展示价格区间、观察时间和可复核的需求信号",
    ))

    economics = candidate.get("economics") if isinstance(candidate.get("economics"), Mapping) else {}
    target_price = _positive_number(candidate.get("target_sale_price"))
    gross_profit = _finite(economics.get("gross_profit"))
    gross_margin = _finite(economics.get("gross_margin"))
    min_amount = _finite(getattr(settings, "min_profit_amount", 0)) or 0.0
    min_rate = _finite(getattr(settings, "min_profit_rate", 0)) or 0.0
    economics_ready = bool(
        target_price is not None
        and gross_profit is not None and gross_profit >= min_amount
        and gross_margin is not None and gross_margin >= min_rate
    )
    gates.append(_gate(
        "unit_economics",
        economics_ready,
        "核算单件贡献利润",
        "sale={}/gross_profit={}/gross_margin={}/floor={}/{}".format(
            _money_or_unknown(target_price),
            _money_or_unknown(gross_profit),
            f"{gross_margin * 100:.1f}%" if gross_margin is not None else "unknown",
            f"¥{min_amount:.2f}",
            f"{min_rate * 100:.1f}%",
        ),
        "system",
        "按采购价、运费、目标售价、退款损耗和当前费率计算，达到项目毛利额与毛利率下限",
    ))

    approved_fingerprint = str(candidate.get("approved_evidence_fingerprint") or "").strip()
    approval_current = bool(
        candidate.get("manual_decision") == "approve"
        and candidate.get("status") == "approved"
        and approved_fingerprint == fingerprint
    )
    gates.append(_gate(
        "manual_decision_current",
        approval_current,
        "对当前证据修订作人工决策",
        "decision={}/status={}/revision={}/fingerprint={}".format(
            str(candidate.get("manual_decision") or "none"),
            str(candidate.get("status") or "none"),
            int(candidate.get("evidence_revision") or 1),
            "match" if approved_fingerprint == fingerprint and approved_fingerprint else "stale_or_missing",
        ),
        "operator",
        "全部证据完成后重新 approve；任何商品事实或证据变化都会使旧批准失效",
    ))

    failed = [gate for gate in gates if gate["status"] != "pass"]
    evidence_failed = [gate for gate in failed if gate["code"] != "manual_decision_current"]
    return {
        "ok": True,
        "ready": not failed,
        "evidence_ready": not evidence_failed,
        "status": "ready" if not failed else "blocked",
        "candidate_id": candidate_id,
        "candidate_title": title[:120],
        "checked_at": checked_at_z,
        "evidence_revision": int(candidate.get("evidence_revision") or 1),
        "evidence_fingerprint": fingerprint,
        "approved_evidence_fingerprint_matches": approval_current,
        "completion": {
            "total": len(gates),
            "passed": len(gates) - len(failed),
            "remaining": len(failed),
        },
        "gates": gates,
        "failed_gate_codes": [gate["code"] for gate in failed],
        "next_step": failed[0] if failed else None,
        "source_limits": [
            "系统校验证据结构、时效、候选绑定与文件哈希，不替代操作者核对证据内容",
            "平台类目、费用、退货和保证金仍以目标账号执行当天页面为准",
            "竞品展示价格不是成交价，需求信号只能支持小规模验证",
        ],
    }


def _normalize_artifact_lookup(
    value: Optional[Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]],
) -> Dict[str, Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return {
            str(key): item
            for key, item in value.items()
            if isinstance(item, Mapping)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {
            str(item.get("artifact_id") or ""): item
            for item in value
            if isinstance(item, Mapping) and str(item.get("artifact_id") or "")
        }
    return {}


def _archived_evidence_state(
    *,
    reference: str,
    candidate_id: str,
    expected_kind: str,
    expected_observed_at: Any,
    artifacts: Mapping[str, Mapping[str, Any]],
    require_archived: bool,
) -> Tuple[bool, str, Optional[str]]:
    artifact_id = parse_artifact_reference(reference)
    if not artifact_id:
        legacy_valid = _valid_evidence_ref(reference)
        if require_archived:
            return False, "archive_required" if legacy_valid else "invalid", None
        return legacy_valid, "legacy_valid" if legacy_valid else "invalid", None

    artifact = artifacts.get(artifact_id)
    if not artifact:
        if require_archived:
            return False, "archive_missing", None
        return True, "legacy_valid", None
    integrity = artifact.get("integrity") if isinstance(artifact.get("integrity"), Mapping) else {}
    digest = str(artifact.get("sha256") or "")
    observed_at = str(artifact.get("observed_at") or "")
    valid = bool(
        artifact.get("artifact_id") == artifact_id
        and str(artifact.get("candidate_id") or "") == candidate_id
        and str(artifact.get("kind") or "") == expected_kind
        and str(artifact.get("status") or "") == "active"
        and bool(integrity.get("ok"))
        and re.fullmatch(r"[a-f0-9]{64}", digest)
    )
    if valid and expected_observed_at is not None:
        expected_dt = _parse_datetime(expected_observed_at)
        artifact_dt = _parse_datetime(observed_at)
        valid = bool(
            expected_dt is not None
            and artifact_dt is not None
            and abs((expected_dt - artifact_dt).total_seconds()) <= 1
        )
        if not valid:
            return False, "observed_at_mismatch", observed_at or None
    if valid:
        return True, "archive_verified", observed_at or None
    if str(artifact.get("candidate_id") or "") != candidate_id:
        return False, "candidate_mismatch", observed_at or None
    if str(artifact.get("kind") or "") != expected_kind:
        return False, "kind_mismatch", observed_at or None
    if str(artifact.get("status") or "") != "active":
        return False, "not_active", observed_at or None
    if not integrity.get("ok"):
        return False, "integrity_failed", observed_at or None
    return False, "archive_invalid", observed_at or None


def _gate(code: str, passed: bool, title: str, evidence: str, owner: str, action: str) -> Dict[str, Any]:
    return {
        "code": code,
        "status": "pass" if passed else "blocked",
        "title": title,
        "evidence": evidence,
        "owner": owner,
        "action": action,
    }


def _substantive_text(value: str, minimum: int = 8) -> bool:
    text = str(value or "").strip()
    return (
        len(text) >= minimum
        and text.casefold() not in _PLACEHOLDER_REF_VALUES
        and not _SENSITIVE_PATTERN.search(text)
    )


def _valid_external_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or _SENSITIVE_PATTERN.search(text):
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.rstrip(".").casefold()
    if _placeholder_host(host):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_unspecified
    )


def _placeholder_host(host: str) -> bool:
    labels = host.split(".")
    if host in {"localhost", "example.com", "example.org", "example.net"}:
        return True
    if labels[-1:] and labels[-1] in {"example", "test", "invalid", "localhost"}:
        return True
    return any(host.endswith("." + suffix) for suffix in ("example.com", "example.org", "example.net"))


def _valid_evidence_ref(value: Any) -> bool:
    text = str(value or "").strip()
    if len(text) < 8 or text.casefold() in _PLACEHOLDER_REF_VALUES:
        return False
    if _SENSITIVE_PATTERN.search(text) or "example.com" in text.casefold():
        return False
    if text.lower().startswith(("http://", "https://")):
        return _valid_external_url(text)
    lowered = text.casefold()
    if any(lowered.endswith(ext) for ext in _EVIDENCE_FILE_EXTENSIONS):
        return ".." not in text.replace("\\", "/").split("/")
    return bool(re.fullmatch(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff .:/\\-]{7,299}", text))


def _valid_image_count(images: Any) -> int:
    if not isinstance(images, (list, tuple)):
        return 0
    count = 0
    for value in images:
        text = str(value or "").strip()
        if _valid_external_url(text):
            count += 1
            continue
        lowered = text.casefold()
        if len(text) >= 8 and any(lowered.endswith(ext) for ext in {".png", ".jpg", ".jpeg", ".webp"}):
            if "example.com" not in lowered and ".." not in text.replace("\\", "/").split("/"):
                count += 1
    return count


def _valid_unique_urls(values: Any) -> List[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not _valid_external_url(text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _meaningful_spec_count(specs: Any) -> int:
    if not isinstance(specs, Mapping):
        return 0
    count = 0
    for key, value in specs.items():
        if not str(key).strip() or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        count += 1
    return count


def _freshness(value: Any, now: datetime, max_age_seconds: int) -> Tuple[bool, str]:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False, "missing"
    age_seconds = (now - parsed).total_seconds()
    if age_seconds < 0:
        return False, f"future({age_seconds / 3600:.1f}h)"
    if age_seconds > max_age_seconds:
        return False, f"stale({age_seconds / 3600:.1f}h)"
    return True, f"{age_seconds / 3600:.1f}h"


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text[-1:] in {"Z", "z"} else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TZ)
    return parsed


def _as_utc_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=_LOCAL_TZ)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_number(value: Any) -> Optional[float]:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _positive_int(value: Any) -> Optional[int]:
    number = _finite(value)
    if number is None or number < 1 or not float(number).is_integer():
        return None
    return int(number)


def _money_or_unknown(value: Optional[float]) -> str:
    return f"¥{value:.2f}" if value is not None else "unknown"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value
