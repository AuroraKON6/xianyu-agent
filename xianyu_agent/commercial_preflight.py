"""Evidence-based commercial publish preflight.

Pure, deterministic evaluation of the business gates that must pass before a
resale candidate may be turned into a real listing approval.

The module never reads the database, cookies, filesystem, or any live runtime
payload. Callers assemble a sanitized account-resolution dict and a candidate
snapshot and pass them in. The result is sanitized by construction: evidence
summaries describe presence, freshness, and thresholds only — never cookie
values, auth headers, prompt bodies, secrets, or raw runtime payloads.

Gate order is the business order from the Phase 18 contract:
  a)  explicit configured/enabled account
  b)  account-scoped readiness on that same account (inventory NOT required)
  c)  current account/category commercial terms when supplied by the caller
  d)  candidate belongs to the domestic Xianyu sales channel
  e)  candidate not blocked / high risk
  f)  supplier verified + note
  g)  supplier quote and stock checked within the last 24h (future timestamps
      and non-parseable timestamps fail)
  h)  gross_profit >= min_profit_amount (finite numbers only)
  i)  gross_margin >= min_profit_rate (finite numbers only)
  j)  fulfillment verified + note
  k)  at least one nonblank candidate image
  l)  image_rights_type in {owned, supplier_authorized, licensed}
  m)  image_rights_evidence present
  n)  title/category nonblank and specifications have at least one meaningful
      nonblank key/value pair
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from .candidate_activation import evaluate_candidate_activation

EVIDENCE_MAX_AGE_SECONDS = 24 * 60 * 60
IMAGE_RIGHTS_TYPES = ("owned", "supplier_authorized", "licensed")

# The order here is the report order and the next-step priority order.
GATE_CODES = (
    "account_configured",
    "account_enabled",
    "prompt_ready",
    "sync_current",
    "runtime_connected",
    "runtime_fresh",
    "runtime_unblocked",
    "candidate_channel_valid",
    "candidate_not_blocked",
    "candidate_activation_ready",
    "supplier_verified",
    "supplier_quote_fresh",
    "stock_checked_fresh",
    "min_profit_amount",
    "min_profit_rate",
    "fulfillment_verified",
    "candidate_images",
    "image_rights_type",
    "image_rights_evidence",
    "structured_fields",
)

_LOCAL_TZ = datetime.now().astimezone().tzinfo


def run_commercial_preflight(
    *,
    candidate: Mapping[str, Any],
    account: Optional[Mapping[str, Any]],
    settings: Any,
    checked_at: Optional[str] = None,
    commercial_terms: Optional[Mapping[str, Any]] = None,
    candidate_evidence_artifacts: Optional[Mapping[str, Mapping[str, Any]]] = None,
    require_archived_candidate_evidence: bool = False,
) -> Dict[str, Any]:
    """Evaluate every publish gate and return a sanitized, deterministic result.

    Args:
        candidate: A resale candidate snapshot (see resale.ResaleManager).
        account: A sanitized account-resolution dict assembled by the caller:
            cookie_id (str), explicit (bool), configured (bool), enabled (bool),
            prompt_ready (bool), sync_current (bool), runtime_connected (bool),
            runtime_fresh (bool), runtime_unblocked (bool). May be None when no
            account was resolved.
        settings: Object exposing min_profit_amount and min_profit_rate.
        checked_at: ISO timestamp used as the "now" for freshness checks and
            stamped on the result. Defaults to the current local time.

    Returns:
        A dict with ready/status, ordered gates (stable codes/status/message/
        evidence), ordered next_step, sanitized evidence_summary, and a compact
        snapshot intended for the approval payload.
    """
    account = dict(account or {})
    checked_dt = _parse_datetime(checked_at) or datetime.now(_LOCAL_TZ)
    checked_at_str = _as_utc_z(checked_dt)
    now_local = checked_dt if checked_dt.tzinfo is not None else checked_dt.replace(tzinfo=_LOCAL_TZ)

    gates: List[Dict[str, Any]] = []
    gates.append(_account_configured_gate(account))
    account_enabled = bool(account.get("account_enabled", account.get("enabled")))
    gates.append(_bool_gate("account_enabled", account_enabled,
                            "账号已启用", "账号未启用",
                            "enabled=" + ("yes" if account_enabled else "no")))
    gates.append(_bool_gate("prompt_ready", bool(account.get("prompt_ready")),
                            "账号 Prompt 已就绪", "账号 Prompt 未就绪",
                            "prompt_ready=" + ("yes" if account.get("prompt_ready") else "no")))
    gates.append(_bool_gate("sync_current", bool(account.get("sync_current")),
                            "账号订单与商品同步均已更新", "订单或商品同步未更新",
                            "orders=" + ("fresh" if account.get("sync_current") else "stale") +
                            "/listings=" + ("fresh" if account.get("sync_current") else "stale")))
    gates.append(_bool_gate("runtime_connected", bool(account.get("runtime_connected")),
                            "账号运行已连接", "账号运行未连接",
                            "runtime=" + ("connected" if account.get("runtime_connected") else "not_connected")))
    gates.append(_bool_gate("runtime_fresh", bool(account.get("runtime_fresh")),
                            "账号认证证据在有效期内", "账号认证证据过期",
                            "runtime_fresh=" + ("yes" if account.get("runtime_fresh") else "no")))
    gates.append(_bool_gate("runtime_unblocked", bool(account.get("runtime_unblocked")),
                            "账号运行无阻断", "账号存在运行阻断",
                            "runtime_unblocked=" + ("yes" if account.get("runtime_unblocked") else "no")))

    if commercial_terms is not None:
        terms = dict(commercial_terms)
        failed_terms = list(terms.get("failed_gate_codes") or [])
        gates.append(_bool_gate(
            "commercial_terms_ready",
            bool(terms.get("ready")),
            "账号商业条款与候选费率已核验",
            "账号商业条款、买家保障或候选费率尚未就绪",
            "failed=" + (",".join(str(code) for code in failed_terms) or "none"),
        ))

    sales_platform = str(candidate.get("sales_platform") or "xianyu").strip().lower()
    market_scope = str(candidate.get("market_scope") or "domestic").strip().lower()
    gates.append(_bool_gate(
        "candidate_channel_valid",
        sales_platform == "xianyu" and market_scope == "domestic",
        "候选属于闲鱼国内项目",
        "候选属于其他销售渠道或跨境项目",
        f"sales_platform={sales_platform}/market_scope={market_scope}",
    ))

    risk_level = str(candidate.get("risk_level") or "low").lower()
    candidate_blocked = risk_level in {"blocked", "high"} or candidate.get("status") == "blocked"
    gates.append(_bool_gate(
        "candidate_not_blocked",
        not candidate_blocked,
        "候选未被阻断", "候选已阻断或为高风险",
        "risk_level=" + risk_level,
    ))

    candidate_activation = evaluate_candidate_activation(
        candidate=candidate,
        settings=settings,
        checked_at=checked_at_str,
        evidence_artifacts=candidate_evidence_artifacts,
        require_archived_evidence=require_archived_candidate_evidence,
    )
    activation_failures = list(candidate_activation.get("failed_gate_codes") or [])
    gates.append(_bool_gate(
        "candidate_activation_ready",
        bool(candidate_activation.get("ready")),
        "一品启动证据包与当前人工决策一致",
        "一品启动证据包不完整或人工决策已过期",
        "failed=" + (",".join(str(code) for code in activation_failures) or "none"),
    ))

    supplier_verified = bool(candidate.get("supplier_verified"))
    supplier_note = str(candidate.get("supplier_verification_note") or "").strip()
    gates.append(_bool_gate(
        "supplier_verified",
        supplier_verified and bool(supplier_note),
        "供应商已验证且备注完整", "供应商未验证或备注为空",
        "verified=" + ("yes" if supplier_verified else "no") +
        " note=" + ("present" if supplier_note else "absent"),
    ))

    gates.append(_fresh_evidence_gate(
        "supplier_quote_fresh", candidate.get("supplier_quote_checked_at"),
        now_local, "供应商报价",
    ))
    gates.append(_fresh_evidence_gate(
        "stock_checked_fresh", candidate.get("stock_checked_at"),
        now_local, "库存核验",
    ))

    economics = candidate.get("economics") if isinstance(candidate.get("economics"), Mapping) else {}
    gross_profit = _finite_float(economics.get("gross_profit"))
    gross_margin = _finite_float(economics.get("gross_margin"))
    min_amount = _finite_float(getattr(settings, "min_profit_amount", 0)) or 0.0
    min_rate = _finite_float(getattr(settings, "min_profit_rate", 0)) or 0.0
    gates.append(_bool_gate(
        "min_profit_amount",
        gross_profit is not None and gross_profit >= min_amount,
        "毛利额达到下限", "毛利额低于下限或不可用",
        _profit_evidence(gross_profit, min_amount),
    ))
    gates.append(_bool_gate(
        "min_profit_rate",
        gross_margin is not None and gross_margin >= min_rate,
        "毛利率达到下限", "毛利率低于下限或不可用",
        _margin_evidence(gross_margin, min_rate),
    ))

    fulfillment_verified = bool(candidate.get("fulfillment_verified"))
    fulfillment_note = str(candidate.get("fulfillment_verification_note") or "").strip()
    gates.append(_bool_gate(
        "fulfillment_verified",
        fulfillment_verified and bool(fulfillment_note),
        "履约已验证且备注完整", "履约未验证或备注为空",
        "fulfillment=" + ("verified" if fulfillment_verified else "not_verified") +
        " note=" + ("present" if fulfillment_note else "absent"),
    ))

    images = candidate.get("images")
    image_count = _count_images(images)
    gates.append(_bool_gate(
        "candidate_images",
        image_count >= 1,
        "至少一张候选图片", "缺少有效候选图片",
        f"images={image_count}",
    ))

    rights_type = str(candidate.get("image_rights_type") or "").strip()
    gates.append(_bool_gate(
        "image_rights_type",
        rights_type in IMAGE_RIGHTS_TYPES,
        "图片授权类型有效", "图片授权类型无效",
        "rights_type=" + (rights_type or "missing"),
    ))

    rights_evidence = str(candidate.get("image_rights_evidence") or "").strip()
    gates.append(_bool_gate(
        "image_rights_evidence",
        bool(rights_evidence),
        "图片授权证据已记录", "缺少图片授权证据",
        "evidence=" + ("present" if rights_evidence else "absent"),
    ))

    title_ok = bool(str(candidate.get("title") or "").strip())
    category_ok = bool(str(candidate.get("category") or "").strip())
    specs = candidate.get("specifications")
    specs_count = _meaningful_spec_count(specs)
    specs_ok = specs_count >= 1
    gates.append(_bool_gate(
        "structured_fields",
        title_ok and category_ok and specs_ok,
        "标题、类目和规格完整", "标题、类目或规格不完整",
        "title=" + ("ok" if title_ok else "missing") +
        " category=" + ("ok" if category_ok else "missing") +
        " specs=" + str(specs_count),
    ))

    ready = all(gate["status"] == "pass" for gate in gates)
    next_step = [
        {"code": gate["code"], "message": gate["message"], "evidence": gate["evidence"]}
        for gate in gates
        if gate["status"] == "fail"
    ]

    evidence_summary = _evidence_summary(gates)
    snapshot = {
        "checked_at": checked_at_str,
        "candidate_activation": {
            "evidence_revision": candidate_activation.get("evidence_revision"),
            "evidence_fingerprint": candidate_activation.get("evidence_fingerprint"),
            "ready": bool(candidate_activation.get("ready")),
            "failed_gate_codes": activation_failures,
        },
        "gates": [
            {"code": gate["code"], "status": gate["status"], "evidence": gate["evidence"]}
            for gate in gates
        ],
    }
    if commercial_terms is not None:
        terms_snapshot = commercial_terms.get("snapshot")
        if isinstance(terms_snapshot, Mapping):
            snapshot["commercial_terms"] = {
                "snapshot_version": str(terms_snapshot.get("snapshot_version") or ""),
                "cookie_id": str(terms_snapshot.get("cookie_id") or ""),
                "category": str(terms_snapshot.get("category") or ""),
                "observed_at": str(terms_snapshot.get("observed_at") or ""),
                "platform_fee_rate": terms_snapshot.get("platform_fee_rate"),
                "platform_fixed_fee": terms_snapshot.get("platform_fixed_fee"),
                "evidence_ref": str(terms_snapshot.get("evidence_ref") or "")[:300],
            }

    return {
        "ready": ready,
        "status": "ready" if ready else "blocked",
        "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or ""),
        "cookie_id": str(account.get("cookie_id") or ""),
        "checked_at": checked_at_str,
        "gates": gates,
        "next_step": next_step,
        "evidence_summary": evidence_summary,
        "candidate_activation": candidate_activation,
        "snapshot": snapshot,
    }


def _account_configured_gate(account: Mapping[str, Any]) -> Dict[str, Any]:
    explicit = bool(account.get("explicit"))
    configured = bool(account.get("configured"))
    enabled = bool(account.get("enabled"))
    cookie_id = str(account.get("cookie_id") or "").strip()
    if not explicit:
        return _gate("account_configured", False, "缺少明确的 cookie_id", "未指定卖家账号")
    if not configured:
        return _gate("account_configured", False, "指定的账号不存在", f"cookie_id={cookie_id or '?'} 未配置")
    if not enabled:
        return _gate("account_configured", False, "指定的账号未启用", f"cookie_id={cookie_id} 已停用")
    return _gate("account_configured", True, "已指定启用账号", f"cookie_id={cookie_id}")


def _fresh_evidence_gate(
    code: str,
    raw_value: Any,
    now: datetime,
    label: str,
) -> Dict[str, Any]:
    parsed = _parse_datetime(raw_value)
    if parsed is None:
        return _gate(code, False, f"{label}未记录核验时间", f"{label}=missing")
    age_seconds = (now - parsed).total_seconds()
    if age_seconds < 0:
        return _gate(
            code, False,
            f"{label}核验时间在未来",
            f"{label}_checked_at={_as_utc_z(parsed)} age={age_seconds / 3600:.1f}h (future)",
        )
    if age_seconds > EVIDENCE_MAX_AGE_SECONDS:
        return _gate(
            code, False,
            f"{label}超过 24 小时未重新核验",
            f"{label}_age={age_seconds / 3600:.1f}h > 24h",
        )
    return _gate(
        code, True,
        f"{label}在 24 小时内已核验",
        f"{label}_checked_at={_as_utc_z(parsed)} age={age_seconds / 3600:.1f}h",
    )


def _profit_evidence(gross_profit: Optional[float], min_amount: float) -> str:
    if gross_profit is None:
        return "gross_profit=unavailable"
    return f"gross_profit=¥{gross_profit:.2f} >= ¥{min_amount:.2f}"


def _margin_evidence(gross_margin: Optional[float], min_rate: float) -> str:
    if gross_margin is None:
        return "gross_margin=unavailable"
    return f"gross_margin={gross_margin * 100:.1f}% >= {min_rate * 100:.1f}%"


def _bool_gate(code: str, passed: bool, pass_message: str, fail_message: str, evidence: str) -> Dict[str, Any]:
    return _gate(code, passed, pass_message if passed else fail_message, evidence)


def _gate(code: str, passed: bool, message: str, evidence: str) -> Dict[str, Any]:
    return {
        "code": code,
        "status": "pass" if passed else "fail",
        "message": message,
        "evidence": evidence,
    }


def _evidence_summary(gates: List[Dict[str, Any]]) -> str:
    failed = [g for g in gates if g["status"] == "fail"]
    if not failed:
        return "全部发布门禁通过"
    parts = [f"{g['code']}: {g['evidence']}" for g in failed]
    return "；".join(parts)


def _count_images(images: Any) -> int:
    if not isinstance(images, (list, tuple)):
        return 0
    return sum(1 for item in images if isinstance(item, str) and item.strip())


def _meaningful_spec_count(specs: Any) -> int:
    """Count specification pairs with a nonblank key and a meaningful value.

    Numeric and boolean values (including 0 and False) count as meaningful;
    only None and blank strings are treated as empty.
    """
    if not isinstance(specs, Mapping):
        return 0
    count = 0
    for key, value in specs.items():
        if not str(key).strip():
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        count += 1
    return count


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


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
