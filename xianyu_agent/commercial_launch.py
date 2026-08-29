from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional

from .commercial_trial import default_controls, validate_controls


GATE_CODES = (
    "historical_work_resolved",
    "account_configured",
    "account_enabled",
    "live_adapter",
    "sync_capabilities",
    "prompt_ready",
    "sync_current",
    "runtime_connected",
    "runtime_fresh",
    "runtime_unblocked",
    "candidate_selected",
    "candidate_manual_approved",
    "candidate_category_valid",
    "account_commercial_terms_ready",
    "candidate_preflight_ready",
    "launch_controls_valid",
    "procurement_cost_known",
    "cash_budget_covers_unit",
    "launch_scope_one_item",
    "no_active_trial",
)

NON_LIVE_ADAPTERS = {"", "unknown", "stub"}
UNASSIGNED_BLOCKING_QUEUES = (
    "pending_approvals",
    "queued_execution",
    "failed_jobs",
    "pending_reply_drafts",
)

ACTIVATION_GUIDANCE = {
    "historical_work_resolved": ("01 清理历史任务", "operator", "逐项归属、关闭或取消全部未归属审批、执行失败和待回复任务", "账户运营队列中未归属数量归零"),
    "account_configured": ("02 建立卖家账号", "operator", "录入由本人持有或明确授权运营的卖家账号会话", "GET /accounts 可见该 cookie_id"),
    "account_enabled": ("02 建立卖家账号", "operator", "启用首发账号并确认不与另一个项目混用", "账号 enabled=true"),
    "live_adapter": ("03 接通平台执行", "system", "启动真实浏览器适配器并完成健康检查", "适配器 ready=true 且不是 stub"),
    "sync_capabilities": ("03 接通平台执行", "system", "确认适配器能读取该账号的订单和商品", "orders/listings 两项同步能力为 true"),
    "prompt_ready": ("04 初始化账号运营", "operator", "初始化该账号专属回复 Prompt", "账号 Prompt profile ready=true"),
    "sync_current": ("04 初始化账号运营", "system", "同步该账号当前订单和商品状态", "订单、商品同步均在新鲜度窗口内"),
    "runtime_connected": ("04 初始化账号运营", "system", "建立账号运行时连接", "connection_status=connected 或 degraded"),
    "runtime_fresh": ("04 初始化账号运营", "operator", "在平台完成一次真实认证活动或必要的人机验证", "token/cookie 与心跳证据均在有效期"),
    "runtime_unblocked": ("04 初始化账号运营", "operator", "处理登录、验证码或平台风控提示", "无 pending_verification_reason 和 runtime error"),
    "candidate_selected": ("05 确定首发商品", "operator", "只选择一个已完成基础调研的候选商品", "请求中存在一个 candidate_id"),
    "candidate_manual_approved": ("05 确定首发商品", "operator", "审核商品风险、需求和利润后做 approve 决策", "候选 status=approved 且 manual_decision=approve"),
    "candidate_category_valid": ("05 确定首发商品", "operator", "只使用闲鱼国内候选，并填写与平台发布页一致的真实类目", "sales_platform=xianyu、market_scope=domestic 且 category 非空"),
    "account_commercial_terms_ready": ("06 核验账号商业条款", "operator", "在当前账号该类目发布页核验经营身份、支付、买保/退货、保证金和实际费率，并保存证据", "账号+类目快照不超过24小时，全部子门禁通过，候选利润已按该版本重算"),
    "candidate_preflight_ready": ("07 完成商品发布证据", "operator", "核对供应商报价、库存、履约、图片权利、规格和利润下限", "发布预检全部门禁通过"),
    "launch_controls_valid": ("08 固定试运营止损", "operator", "确认采购预算、最大亏损、监控周期和诊断阈值", "试运营参数通过 validate_controls"),
    "procurement_cost_known": ("08 固定试运营止损", "operator", "记录单件采购价和运费", "supplier_price>0 且 supplier_shipping>=0"),
    "cash_budget_covers_unit": ("08 固定试运营止损", "operator", "预留至少一件商品的采购现金", "cash_budget >= 单件采购价+运费"),
    "launch_scope_one_item": ("09 锁定最小批次", "system", "保持一个账号、一个候选、最多一个发布承诺", "max_candidates=target_candidates=max_published=1"),
    "no_active_trial": ("09 锁定最小批次", "operator", "先结束现有活跃批次，避免库存和消息归属混淆", "active_trials=0"),
}


def day_zero_controls(raw: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    controls = dict(default_controls())
    controls.update({"max_candidates": 1, "target_candidates": 1, "max_published": 1})
    if raw is not None:
        controls.update(dict(raw))
    return controls


def build_commercial_launch_preview(
    *,
    cookie_id: str,
    candidate: Optional[Mapping[str, Any]],
    account: Optional[Mapping[str, Any]],
    account_version: Optional[Mapping[str, Any]],
    adapter_health: Optional[Mapping[str, Any]],
    unassigned_review: Optional[Mapping[str, Any]],
    unassigned_queue_counts: Optional[Mapping[str, Any]],
    active_trials: List[Mapping[str, Any]],
    candidate_preflight: Optional[Mapping[str, Any]],
    commercial_terms: Optional[Mapping[str, Any]] = None,
    controls: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a sanitized and versioned Day-0 launch decision."""
    cookie_id = str(cookie_id or "").strip()
    candidate = dict(candidate or {})
    account = dict(account or {})
    account_version = dict(account_version or {})
    adapter_health = dict(adapter_health or {})
    unassigned_review = dict(unassigned_review or {})
    queue_counts = dict(unassigned_queue_counts or {})
    candidate_preflight = dict(candidate_preflight or {})
    commercial_terms = dict(commercial_terms or {})
    active_trials = [dict(item) for item in active_trials or [] if isinstance(item, Mapping)]

    normalized_controls, control_errors = validate_controls(day_zero_controls(controls))
    candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
    category = str(candidate.get("category") or "").strip()
    sales_platform = str(candidate.get("sales_platform") or "xianyu").strip().lower()
    market_scope = str(candidate.get("market_scope") or "domestic").strip().lower()
    supplier_price = _finite(candidate.get("supplier_price"))
    supplier_shipping = _finite(candidate.get("supplier_shipping"))
    cost_known = bool(
        supplier_price is not None
        and supplier_price > 0
        and supplier_shipping is not None
        and supplier_shipping >= 0
    )
    unit_cost = round((supplier_price or 0) + (supplier_shipping or 0), 2) if cost_known else None
    cash_budget = _finite(normalized_controls.get("cash_budget")) or 0
    adapter_name = str(adapter_health.get("adapter") or "").strip().lower()
    sync_capabilities = dict(adapter_health.get("sync_capabilities") or {})
    unresolved_count = int(unassigned_review.get("count") or 0)
    unresolved_queue_total = sum(int(queue_counts.get(key) or 0) for key in UNASSIGNED_BLOCKING_QUEUES)
    preflight_failures = list(candidate_preflight.get("failed_gate_codes") or [])
    if not preflight_failures:
        preflight_failures = [
            str(gate.get("code") or "")
            for gate in candidate_preflight.get("gates") or []
            if isinstance(gate, Mapping) and str(gate.get("status") or "") not in {"pass", "passed"}
        ]
    commercial_terms_failures = list(commercial_terms.get("failed_gate_codes") or [])

    gates = [
        _gate(
            "historical_work_resolved",
            unresolved_count == 0 and unresolved_queue_total == 0,
            "历史任务归属清理",
            f"review={unresolved_count}, queues={unresolved_queue_total}",
            "review_unassigned_work",
        ),
        _gate(
            "account_configured",
            bool(cookie_id and account.get("explicit") and account.get("configured")),
            "卖家账号配置",
            f"account={cookie_id or 'none'}",
            "configure_account",
        ),
        _gate(
            "account_enabled",
            bool(account.get("account_enabled", account.get("enabled"))),
            "卖家账号启用",
            "enabled=yes" if account.get("account_enabled", account.get("enabled")) else "enabled=no",
            "enable_account",
        ),
        _gate(
            "live_adapter",
            bool(adapter_health.get("ready")) and adapter_name not in NON_LIVE_ADAPTERS,
            "真实执行适配器",
            f"adapter={adapter_name or 'unknown'}",
            "check_adapter",
        ),
        _gate(
            "sync_capabilities",
            bool(sync_capabilities.get("orders") and sync_capabilities.get("listings")),
            "订单与商品同步能力",
            "orders={}/listings={}".format(
                "yes" if sync_capabilities.get("orders") else "no",
                "yes" if sync_capabilities.get("listings") else "no",
            ),
            "check_adapter",
        ),
        _account_gate(account, "prompt_ready", "账号 Prompt", "bootstrap_prompt"),
        _account_gate(account, "sync_current", "同步新鲜度", "sync_account"),
        _account_gate(account, "runtime_connected", "账号运行时连接", "connect_runtime"),
        _account_gate(account, "runtime_fresh", "账号认证新鲜度", "refresh_runtime"),
        _account_gate(account, "runtime_unblocked", "登录与验证阻断", "resolve_runtime_blocker"),
        _gate(
            "candidate_selected",
            bool(candidate_id),
            "首发候选选择",
            f"candidate={candidate_id or 'none'}",
            "select_candidate",
        ),
        _gate(
            "candidate_manual_approved",
            bool(candidate_id and candidate.get("manual_decision") == "approve" and candidate.get("status") == "approved"),
            "候选人工决策",
            "decision={}/status={}".format(
                str(candidate.get("manual_decision") or "none"),
                str(candidate.get("status") or "none"),
            ),
            "approve_candidate",
        ),
        _gate(
            "candidate_category_valid",
            bool(category and sales_platform == "xianyu" and market_scope == "domestic"),
            "候选渠道与类目",
            f"sales_platform={sales_platform}/market_scope={market_scope}/category={category or 'none'}",
            "complete_candidate_category",
        ),
        _gate(
            "account_commercial_terms_ready",
            bool(candidate_id and category and commercial_terms.get("ready")),
            "账号商业条款与实际费率",
            "failed=" + (
                ",".join(str(code) for code in commercial_terms_failures)
                or ("none" if candidate_id and category else "not_evaluated")
            ),
            "complete_commercial_terms",
        ),
        _gate(
            "candidate_preflight_ready",
            bool(candidate_id and candidate_preflight.get("ready")),
            "候选发布证据",
            "failed=" + (",".join(preflight_failures) or ("none" if candidate_id else "not_evaluated")),
            "complete_candidate_preflight",
        ),
        _gate(
            "launch_controls_valid",
            not control_errors,
            "试运营参数",
            "errors=" + (";".join(control_errors) or "none"),
            "fix_launch_controls",
        ),
        _gate(
            "procurement_cost_known",
            cost_known,
            "首单采购成本",
            f"unit_cost={unit_cost if unit_cost is not None else 'unknown'}",
            "complete_candidate_cost",
        ),
        _gate(
            "cash_budget_covers_unit",
            bool(cost_known and cash_budget >= float(unit_cost or 0)),
            "单件采购预算",
            f"budget={round(cash_budget, 2)}/unit_cost={unit_cost if unit_cost is not None else 'unknown'}",
            "increase_budget_or_change_candidate",
        ),
        _gate(
            "launch_scope_one_item",
            bool(
                normalized_controls.get("max_candidates") == 1
                and normalized_controls.get("target_candidates") == 1
                and normalized_controls.get("max_published") == 1
            ),
            "首轮单品范围",
            "max_candidates={}/target_candidates={}/max_published={}".format(
                normalized_controls.get("max_candidates"),
                normalized_controls.get("target_candidates"),
                normalized_controls.get("max_published"),
            ),
            "reduce_launch_scope",
        ),
        _gate(
            "no_active_trial",
            len(active_trials) == 0,
            "活跃批次排他性",
            f"active_trials={len(active_trials)}",
            "finish_existing_trial",
        ),
    ]
    failed = [gate["code"] for gate in gates if gate["status"] != "pass"]
    next_gate = next((gate for gate in gates if gate["status"] != "pass"), None)
    token_payload = {
        "cookie_id": cookie_id,
        "candidate": {
            "candidate_id": candidate_id,
            "updated_at": str(candidate.get("updated_at") or ""),
            "category": category,
            "sales_platform": sales_platform,
            "market_scope": market_scope,
            "status": str(candidate.get("status") or ""),
            "manual_decision": str(candidate.get("manual_decision") or ""),
            "supplier_price": supplier_price,
            "supplier_shipping": supplier_shipping,
        },
        "account": {key: account.get(key) for key in (
            "explicit",
            "configured",
            "enabled",
            "account_enabled",
            "prompt_ready",
            "sync_current",
            "runtime_connected",
            "runtime_fresh",
            "runtime_unblocked",
        )},
        "account_version": account_version,
        "adapter": {
            "name": adapter_name,
            "ready": bool(adapter_health.get("ready")),
            "sync_capabilities": sync_capabilities,
        },
        "unassigned": {
            "count": unresolved_count,
            "preview_token": str(unassigned_review.get("preview_token") or ""),
            "queues": {key: int(queue_counts.get(key) or 0) for key in UNASSIGNED_BLOCKING_QUEUES},
        },
        "active_trials": sorted(
            [
                {
                    "trial_id": str(item.get("trial_id") or ""),
                    "lifecycle": str(item.get("lifecycle") or ""),
                    "updated_at": item.get("updated_at"),
                }
                for item in active_trials
            ],
            key=lambda item: item["trial_id"],
        ),
        "preflight": {
            "ready": bool(candidate_preflight.get("ready")),
            "failed_gate_codes": sorted(preflight_failures),
            "gates": [
                {
                    "code": str(gate.get("code") or ""),
                    "status": str(gate.get("status") or ""),
                    "evidence": str(gate.get("evidence") or ""),
                }
                for gate in candidate_preflight.get("gates") or []
                if isinstance(gate, Mapping)
            ],
        },
        "commercial_terms": {
            "ready": bool(commercial_terms.get("ready")),
            "failed_gate_codes": sorted(str(code) for code in commercial_terms_failures),
            "snapshot_version": str((commercial_terms.get("snapshot") or {}).get("snapshot_version") or ""),
            "observed_at": str((commercial_terms.get("snapshot") or {}).get("observed_at") or ""),
            "platform_fee_rate": (commercial_terms.get("snapshot") or {}).get("platform_fee_rate"),
            "platform_fixed_fee": (commercial_terms.get("snapshot") or {}).get("platform_fixed_fee"),
            "gates": [
                {
                    "code": str(gate.get("code") or ""),
                    "status": str(gate.get("status") or ""),
                    "evidence": str(gate.get("evidence") or ""),
                }
                for gate in commercial_terms.get("gates") or []
                if isinstance(gate, Mapping)
            ],
        },
        "controls": normalized_controls,
        "gate_statuses": [(gate["code"], gate["status"]) for gate in gates],
    }
    preview_token = hashlib.sha256(
        json.dumps(token_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    activation_plan = _build_activation_plan(gates)
    return {
        "ok": True,
        "ready": not failed,
        "status": "ready" if not failed else "blocked",
        "cookie_id": cookie_id,
        "candidate_id": candidate_id,
        "candidate_title": str(candidate.get("title") or "")[:120],
        "category": category,
        "unit_procurement_cost": unit_cost,
        "controls": normalized_controls,
        "gates": gates,
        "commercial_terms": commercial_terms,
        "activation_plan": activation_plan,
        "failed_gate_codes": failed,
        "next_step": {
            "gate_code": next_gate["code"] if next_gate else "ready",
            "action": next_gate["action"] if next_gate else "start_day_zero_trial",
            "title": next_gate["title"] if next_gate else "启动首轮试运营",
            "detail": next_gate["evidence"] if next_gate else "All launch evidence is current.",
        },
        "preview_token": preview_token,
        "confirmation": "START 1",
    }


def _account_gate(account: Mapping[str, Any], code: str, title: str, action: str) -> Dict[str, Any]:
    passed = bool(account.get(code))
    return _gate(code, passed, title, f"{code}=" + ("yes" if passed else "no"), action)


def _gate(code: str, passed: bool, title: str, evidence: str, action: str) -> Dict[str, Any]:
    return {
        "code": code,
        "status": "pass" if passed else "blocked",
        "title": title,
        "evidence": evidence,
        "action": action,
    }


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _build_activation_plan(gates: List[Mapping[str, Any]]) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    for sequence, gate in enumerate(gates, start=1):
        code = str(gate.get("code") or "")
        stage, owner, evidence_required, verification = ACTIVATION_GUIDANCE.get(
            code,
            ("待处理", "operator", "补充该门禁要求的当前证据", "重新运行启动预览"),
        )
        steps.append({
            "sequence": sequence,
            "code": code,
            "status": str(gate.get("status") or "blocked"),
            "stage": stage,
            "owner": owner,
            "title": str(gate.get("title") or code),
            "action": str(gate.get("action") or ""),
            "current_evidence": str(gate.get("evidence") or ""),
            "evidence_required": evidence_required,
            "verification": verification,
        })
    remaining = [step for step in steps if step["status"] != "pass"]
    return {
        "total_steps": len(steps),
        "completed_steps": len(steps) - len(remaining),
        "remaining_steps": len(remaining),
        "next_step": remaining[0] if remaining else None,
        "steps": steps,
    }
