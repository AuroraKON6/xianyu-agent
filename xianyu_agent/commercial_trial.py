"""Evidence-derived Commercial Alpha trial batches (Phase 19).

The legacy :mod:`trial_run` guided planning only and is kept for
compatibility but is clearly deprecated. This module owns the real operating
loop: a JSON-backed atomic store for trial business metadata plus pure
reconciliation helpers that turn current manager state into an ordered,
sanitized evidence chain with one prioritized next action and explicit stop
rules.

Safety boundaries:
- Persist business metadata only. Never persist or return buyer identifiers,
  raw message text, cookies, auth headers, or prompt bodies.
- No automatic publish / message / order / pay / refund. Every risky action
  still flows through WorkOrder + Approval + ExecutionJobQueue.
- Evidence is account/candidate/time scoped and never combines accounts or
  unrelated historical data.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional

# The exact real operating order from the Phase 19 contract.
STAGE_CODES = (
    "account_ready",
    "candidate_pool",
    "publish_ready",
    "approval_created",
    "publish_confirmed",
    "inquiry_observed",
    "inquiry_responded",
    "paid_order",
    "fulfillment_observed",
    "profit_reconciled",
    "review_decision",
)

ACTIVE_LIFECYCLES = frozenset({"active", "paused"})
PAID_OR_LATER_STATUSES = frozenset(
    {"paid", "pending_shipment", "shipped", "delivered", "refund_requested", "disputed"}
)
AFTER_SALES_STATUSES = frozenset({"refund_requested", "disputed"})
FULFILLMENT_STATUSES = frozenset({"shipped", "delivered"})
TRAFFIC_SOURCES = frozenset({"operator", "adapter"})
EXPERIMENT_VARIABLES = frozenset(
    {"title", "cover", "price", "description", "location", "shipping_promise"}
)
EXPERIMENT_DECISIONS = frozenset({"keep_change", "revert", "stop_listing"})

# Internal keys that must never appear in a sanitized trial view.
_LOCAL_TZ = datetime.now().astimezone().tzinfo

# Per-stage operator actions for the first pending stage.
_NEXT_ACTION_BY_STAGE = {
    "account_ready": "fix_account_readiness",
    "candidate_pool": "attach_more_candidates",
    "publish_ready": "complete_candidate_preflight_evidence",
    "approval_created": "create_batch_listing_draft",
    "publish_confirmed": "approve_and_run_publish_job",
    "inquiry_observed": "monitor_for_inquiry",
    "inquiry_responded": "approve_and_send_inquiry_reply",
    "paid_order": "monitor_for_paid_order",
    "fulfillment_observed": "monitor_fulfillment",
    "profit_reconciled": "reconcile_order_profit",
    "review_decision": "decide_trial_after_monitor",
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


def _local_date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=_LOCAL_TZ).date().isoformat()


def default_controls() -> Dict[str, Any]:
    """Return the Phase 19 default trial controls."""
    return {
        "max_candidates": 3,
        "max_published": 1,
        "cash_budget": 1000,
        "max_realized_loss": 100,
        "max_after_sales_rate": 0.5,
        "monitor_days": 14,
        "target_inquiries": 3,
        "target_paid_orders": 1,
        # Pool target count for the candidate_pool stage. Defaults to the
        # max_candidates cap so a batch starts only once its planned pool is
        # attached; operators may set a lower target.
        "target_candidates": 3,
        # Traffic thresholds are decision guards, not platform promises. They
        # can be tuned per category once the operator has enough real history.
        "min_observation_hours": 24,
        "min_views_for_diagnosis": 20,
        "experiment_days": 3,
    }


def validate_controls(raw: Optional[Mapping[str, Any]] = None) -> tuple[Dict[str, Any], List[str]]:
    """Normalize and validate trial controls.

    Returns ``(controls, errors)``. Every control must be a bounded positive
    value; ratio-style controls must fall in ``(0, 1]``. Invalid values are
    rejected with a descriptive error and never silently defaulted.
    """
    controls = dict(default_controls())
    if raw is not None:
        raw_dict = dict(raw)
        for key in controls:
            if key in raw_dict:
                controls[key] = raw_dict[key]

    errors: List[str] = []
    integer_bounds = {
        "max_candidates": (1, 10),
        "max_published": (1, 5),
        "monitor_days": (1, 90),
        "target_inquiries": (1, 100),
        "target_paid_orders": (1, 100),
        "target_candidates": (1, 10),
        "min_observation_hours": (6, 168),
        "min_views_for_diagnosis": (1, 100_000),
        "experiment_days": (1, 14),
    }
    for key, (minimum, maximum) in integer_bounds.items():
        value = _finite_float(controls.get(key))
        if value is None or not float(value).is_integer():
            errors.append(f"{key} must be an integer between {minimum} and {maximum}")
            continue
        normalized = int(value)
        if not minimum <= normalized <= maximum:
            errors.append(f"{key} must be between {minimum} and {maximum}")
            continue
        controls[key] = normalized

    for key, maximum in (("cash_budget", 1_000_000), ("max_realized_loss", 1_000_000)):
        value = _finite_float(controls.get(key))
        if value is None or not 0 < value <= maximum:
            errors.append(f"{key} must be a finite number in (0, {maximum}]")
            continue
        controls[key] = round(value, 2)

    rate = _finite_float(controls.get("max_after_sales_rate"))
    if rate is None or not (0 < rate <= 1):
        errors.append("max_after_sales_rate must be a finite number in (0, 1]")
    else:
        controls["max_after_sales_rate"] = rate

    max_candidates = int(controls.get("max_candidates") or 0)
    max_published = int(controls.get("max_published") or 0)
    target_candidates = int(controls.get("target_candidates") or 0)
    if max_candidates > 0 and not (1 <= target_candidates <= max_candidates):
        errors.append("target_candidates must be between 1 and max_candidates")
    if max_candidates > 0 and max_published > max_candidates:
        errors.append("max_published cannot exceed max_candidates")
    cash_budget = _finite_float(controls.get("cash_budget")) or 0
    max_loss = _finite_float(controls.get("max_realized_loss")) or 0
    if cash_budget > 0 and max_loss > cash_budget:
        errors.append("max_realized_loss cannot exceed cash_budget")
    return controls, errors


def reconcile_trial(
    *,
    trial: Mapping[str, Any],
    account: Optional[Mapping[str, Any]],
    candidates: List[Mapping[str, Any]],
    work_orders: List[Mapping[str, Any]],
    jobs: List[Mapping[str, Any]],
    intake_records: List[Mapping[str, Any]],
    runtime_orders: List[Mapping[str, Any]],
    resale_orders: List[Mapping[str, Any]],
    preflight_results: Mapping[str, Mapping[str, Any]],
    checked_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the live, sanitized evidence chain for one trial.

    Pure and deterministic: all live state is passed in by the caller. The
    returned dict contains only sanitized evidence — IDs, counts, and
    timestamps — never buyer identifiers, raw messages, cookies, auth
    headers, or prompt bodies.
    """
    now = _checked_epoch(checked_at)
    cookie_id = str(trial.get("cookie_id") or "").strip()
    category = str(trial.get("category") or "").strip()
    attached_ids = _unique_ids(trial.get("candidate_ids") or [])
    trial_start = _epoch(trial.get("created_at")) or _epoch(trial.get("started_at")) or 0.0
    controls = dict(trial.get("controls") or default_controls())
    lifecycle = str(trial.get("lifecycle") or "active").strip()

    candidates_by_id = {
        str(candidate.get("candidate_id") or candidate.get("id") or "").strip(): candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
    }
    valid_candidate_ids = [
        candidate_id
        for candidate_id in attached_ids
        if candidate_id in candidates_by_id
        and str(candidates_by_id[candidate_id].get("category") or "").strip() == category
    ]
    candidate_id_set = set(valid_candidate_ids)

    account_ready = _account_ready(account)
    account_stage = _stage(
        "account_ready",
        account_ready,
        "账号就绪" if account_ready else "账号未就绪",
        _account_ready_evidence(account),
        counts={"accounts": 1 if account_ready else 0},
    )

    pool_target = int(controls.get("target_candidates") or 1)
    pool_size = len(valid_candidate_ids)
    pool_ready = pool_size >= pool_target
    pool_stage = _stage(
        "candidate_pool",
        pool_ready,
        "候选池已达标" if pool_ready else "候选池未达标",
        f"valid_candidates={pool_size}/{pool_target}",
        counts={"attached": len(attached_ids), "valid": pool_size, "target": pool_target},
    )

    preflight_passed = [
        candidate_id
        for candidate_id in valid_candidate_ids
        if bool((preflight_results.get(candidate_id) or {}).get("ready"))
    ]
    publish_ready = bool(preflight_passed)
    publish_ready_stage = _stage(
        "publish_ready",
        publish_ready,
        "至少一个候选通过商业预检" if publish_ready else "无候选通过商业预检",
        f"preflight_pass={','.join(preflight_passed) or 'none'}",
        counts={"passed": len(preflight_passed)},
    )

    approvals = _find_approvals(
        work_orders,
        cookie_id=cookie_id,
        candidate_ids=candidate_id_set,
        trial_start=trial_start,
    )
    approval_stage = _stage(
        "approval_created",
        bool(approvals),
        "审批已创建" if approvals else "尚未创建上架审批",
        _approvals_evidence(approvals),
        counts={"approvals": len(approvals)},
    )

    publishes = _find_publish_jobs(jobs, approvals=approvals, trial_start=trial_start)
    published_count = len(publishes)
    publish_stage = _stage(
        "publish_confirmed",
        published_count > 0,
        "发布已确认" if publishes else "发布未确认",
        _publishes_evidence(publishes),
        counts={"published": published_count},
    )

    inquiries = _find_inquiries(
        intake_records,
        cookie_id=cookie_id,
        publishes=publishes,
    )
    inquiry_target = int(controls.get("target_inquiries") or 1)
    inquiry_ready = len(inquiries) >= inquiry_target
    inquiry_stage = _stage(
        "inquiry_observed",
        inquiry_ready,
        "咨询目标已达到" if inquiry_ready else "咨询目标未达到",
        _inquiries_evidence(inquiries, target=inquiry_target),
        counts={"inquiries": len(inquiries), "target": inquiry_target},
    )
    inquiry_responses = _find_inquiry_responses(jobs, inquiries=inquiries)
    response_ready = len(inquiry_responses) >= inquiry_target
    response_stage = _stage(
        "inquiry_responded",
        response_ready,
        "询盘回复目标已达到" if response_ready else "询盘尚未全部形成已发送回复证据",
        _inquiry_responses_evidence(inquiry_responses, target=inquiry_target),
        counts={"responses": len(inquiry_responses), "target": inquiry_target},
    )

    paid_orders = _find_paid_orders(runtime_orders, cookie_id=cookie_id, publishes=publishes)
    paid_target = int(controls.get("target_paid_orders") or 1)
    paid_ready = len(paid_orders) >= paid_target
    paid_stage = _stage(
        "paid_order",
        paid_ready,
        "付款订单目标已达到" if paid_ready else "付款订单目标未达到",
        _paid_orders_evidence(paid_orders, target=paid_target),
        counts={"paid_orders": len(paid_orders), "target": paid_target},
    )
    paid_order_ids = {
        str(order.get("order_id") or "").strip()
        for order in paid_orders
        if str(order.get("order_id") or "").strip()
    }
    traffic_observations = _sanitize_traffic_observations(trial.get("traffic_observations") or [])
    experiments = _enrich_experiment_execution(
        _sanitize_experiments(trial.get("experiments") or []),
        work_orders=work_orders,
        jobs=jobs,
    )
    item_performance = _build_item_performance(
        publishes=publishes,
        observations=traffic_observations,
        experiments=experiments,
        inquiries=inquiries,
        paid_orders=paid_orders,
        controls=controls,
        now=now,
    )
    fulfillment_handoffs = _sanitize_fulfillment_handoffs(trial.get("fulfillment_handoffs") or [])
    purchase_approval_statuses = _approval_statuses(work_orders)
    for handoff in fulfillment_handoffs:
        handoff["approval_status"] = purchase_approval_statuses.get(
            str(handoff.get("approval_id") or ""),
            "missing",
        )
    handoff_by_order_id = {
        str(handoff.get("order_id") or ""): handoff
        for handoff in fulfillment_handoffs
        if str(handoff.get("order_id") or "")
    }
    order_links: List[Dict[str, Any]] = []
    for order in paid_orders:
        order_id = str(order.get("order_id") or "").strip()
        lock = order.get("fulfillment_lock") if isinstance(order.get("fulfillment_lock"), Mapping) else {}
        handoff = handoff_by_order_id.get(order_id)
        order_links.append({
            "order_id": order_id,
            "item_id": str(order.get("item_id") or "").strip(),
            "candidate_id": str(order.get("candidate_id") or "").strip(),
            "status": str(order.get("status") or "").strip(),
            "amount": round(_finite_float(order.get("amount")) or 0.0, 2),
            "created_at": _iso(_epoch(order.get("created_at")) or 0),
            "locked": bool(lock.get("locked")),
            "lock_owner": str(lock.get("owner") or "") if bool(lock.get("locked")) else "",
            "handoff": handoff,
        })

    matched_resale_orders = _matched_resale_orders(
        resale_orders,
        trial_id=str(trial.get("trial_id") or ""),
        cookie_id=cookie_id,
        candidate_ids=candidate_id_set,
        paid_orders=paid_orders,
        trial_start=trial_start,
    )
    fulfilled_orders, fulfillment_gaps = _audited_fulfilled_orders(
        paid_orders=paid_orders,
        matched_resale_orders=matched_resale_orders,
        handoff_by_order_id=handoff_by_order_id,
    )
    fulfillment_ready = len(fulfilled_orders) >= paid_target
    fulfillment_stage = _stage(
        "fulfillment_observed",
        fulfillment_ready,
        "目标订单均已发货或签收" if fulfillment_ready else "目标订单尚未全部发货或签收",
        _fulfillment_evidence(fulfilled_orders, gaps=fulfillment_gaps),
        counts={"fulfilled": len(fulfilled_orders), "target": paid_target},
    )

    completed_profit_orders, profit_gaps = _audited_profit_orders(
        paid_orders=paid_orders,
        matched_resale_orders=matched_resale_orders,
        fulfillment_gaps=fulfillment_gaps,
    )
    profit_ready = len(completed_profit_orders) >= paid_target
    profit_stage = _stage(
        "profit_reconciled",
        profit_ready,
        "目标订单利润已对账" if profit_ready else "目标订单利润未全部对账",
        _profits_evidence(completed_profit_orders, gaps=profit_gaps),
        counts={"reconciled": len(completed_profit_orders), "target": paid_target},
    )

    exposure = _cash_exposure(
        paid_orders=paid_orders,
        matched_resale_orders=matched_resale_orders,
        publishes=publishes,
        candidates_by_id=candidates_by_id,
    )
    committed_cash = _committed_cash_requirement(
        approvals=approvals,
        candidates_by_id=candidates_by_id,
    )
    realized_profit = round(
        sum(_finite_float(order.get("actual_profit")) or 0.0 for order in completed_profit_orders),
        2,
    )
    linked_recorded_losses = round(
        sum(
            min(0.0, _finite_float(order.get("actual_profit")) or 0.0)
            for order in matched_resale_orders
            if str(order.get("status") or "") == "completed"
        ),
        2,
    )
    risk_realized_profit = min(realized_profit, linked_recorded_losses)
    after_sales_count = sum(1 for order in paid_orders if _is_after_sales(order))
    paid_count = len(paid_orders)
    after_sales_ratio = round(after_sales_count / paid_count, 4) if paid_count else 0.0
    publish_commitment_count = _publish_commitment_count(approvals)
    procurement_commitment = round(
        sum(
            _finite_float(handoff.get("total_cost")) or 0.0
            for handoff in fulfillment_handoffs
            if not handoff.get("released_at")
            and str(handoff.get("approval_status") or "") not in {"rejected", "cancelled"}
        ),
        2,
    )
    fulfillment_gaps_by_order = {
        str(item.get("order_id") or ""): list(item.get("reasons") or [])
        for item in fulfillment_gaps
    }
    profit_gaps_by_order = {
        str(item.get("order_id") or ""): list(item.get("reasons") or [])
        for item in profit_gaps
    }
    for link in order_links:
        order_id = str(link.get("order_id") or "")
        link["fulfillment_audit"] = {
            "ready": not fulfillment_gaps_by_order.get(order_id),
            "gaps": fulfillment_gaps_by_order.get(order_id, []),
        }
        link["profit_audit"] = {
            "ready": not profit_gaps_by_order.get(order_id),
            "gaps": profit_gaps_by_order.get(order_id, []),
        }

    stop_required, stop_reasons = _compute_stop(
        account_ready=account_ready,
        exposure=exposure,
        committed_cash=committed_cash,
        procurement_commitment=procurement_commitment,
        realized_profit=risk_realized_profit,
        after_sales_ratio=after_sales_ratio,
        paid_count=paid_count,
        approval_exists=publish_commitment_count > 0,
        publish_exists=bool(publishes),
        controls=controls,
    )

    monitor_days = int(controls.get("monitor_days") or 14)
    monitor_elapsed = bool(trial_start and (now - trial_start) >= monitor_days * 86400.0)
    review_eligible = monitor_elapsed or stop_required or profit_ready
    decision_history = _sanitize_decisions(trial.get("decision_history") or [])
    review_decisions = [decision for decision in decision_history if bool(decision.get("review_eligible"))]
    review_stage = _stage(
        "review_decision",
        review_eligible and bool(review_decisions),
        "已做出复盘决策" if review_eligible and review_decisions else "等待复盘决策",
        _decision_evidence(review_decisions),
        counts={"decisions": len(review_decisions), "eligible": 1 if review_eligible else 0},
    )

    stages = [
        account_stage,
        pool_stage,
        publish_ready_stage,
        approval_stage,
        publish_stage,
        inquiry_stage,
        response_stage,
        paid_stage,
        fulfillment_stage,
        profit_stage,
        review_stage,
    ]
    completed_prefix_count = 0
    for stage in stages:
        if stage["status"] != "pass":
            break
        completed_prefix_count += 1

    max_published = int(controls.get("max_published") or 1)
    committed_candidate_ids = _unique_ids(
        approval.get("candidate_id")
        for approval in approvals
        if str(approval.get("status") or "") not in {"rejected", "cancelled"}
    )
    batch_block_reason, batch_draft_blocked = _batch_draft_blocked(
        lifecycle=lifecycle,
        stop_required=stop_required,
        publish_commitment_count=publish_commitment_count,
        max_published=max_published,
    )
    next_action = _compute_next_action(
        lifecycle=lifecycle,
        stop_required=stop_required,
        stop_reasons=stop_reasons,
        batch_draft_blocked=batch_draft_blocked,
        batch_block_reason=batch_block_reason,
        stages=stages,
        monitor_elapsed=monitor_elapsed,
    )
    traffic_action = _traffic_next_action(item_performance)
    if lifecycle == "active" and not stop_required and traffic_action:
        next_action = traffic_action
    unanswered_inquiries = max(0, min(len(inquiries), inquiry_target) - len(inquiry_responses))
    if lifecycle == "active" and not stop_required and unanswered_inquiries:
        next_action = _action(
            "inquiry_reply_pending",
            "approve_and_send_inquiry_reply",
            "真实询盘尚未形成成功发送记录，先处理回复草稿和审批",
            f"unanswered={unanswered_inquiries}",
        )
    unhanded_paid_orders = [
        link
        for link in order_links
        if not link.get("handoff") and link.get("status") in {"paid", "pending_shipment"}
    ]
    if lifecycle == "active" and not stop_required and unhanded_paid_orders:
        next_action = _action(
            "paid_order_needs_handoff",
            "prepare_paid_order_fulfillment",
            "已付款订单需要履约交接",
            ",".join(str(link.get("order_id") or "") for link in unhanded_paid_orders[:5]),
        )
    pending_handoff_approvals = [
        link
        for link in order_links
        if isinstance(link.get("handoff"), Mapping)
        and str(link["handoff"].get("approval_status") or "") == "pending"
    ]
    if lifecycle == "active" and not stop_required and pending_handoff_approvals:
        next_action = _action(
            "fulfillment_approval_pending",
            "review_paid_order_purchase_approval",
            "已付款订单的采购履约审批尚未处理",
            ",".join(str(link.get("order_id") or "") for link in pending_handoff_approvals[:5]),
        )
    fulfillment_gap_links = [
        link
        for link in order_links
        if isinstance(link.get("handoff"), Mapping)
        and str(link["handoff"].get("approval_status") or "") == "approved"
        and not bool((link.get("fulfillment_audit") or {}).get("ready"))
    ]
    if lifecycle == "active" and not stop_required and fulfillment_gap_links:
        next_action = _action(
            "fulfillment_evidence_incomplete",
            "complete_fulfillment_evidence",
            "采购已批准，但供应商单号、双边物流或平台发货证据尚未闭合",
            ",".join(str(link.get("order_id") or "") for link in fulfillment_gap_links[:5]),
        )
    profit_gap_links = [
        link
        for link in order_links
        if bool((link.get("fulfillment_audit") or {}).get("ready"))
        and not bool((link.get("profit_audit") or {}).get("ready"))
    ]
    if lifecycle == "active" and not stop_required and profit_gap_links:
        next_action = _action(
            "profit_evidence_incomplete",
            "reconcile_order_profit",
            "履约证据已闭合，请核对实收、采购、运费、平台费和账单凭证",
            ",".join(str(link.get("order_id") or "") for link in profit_gap_links[:5]),
        )
    rejected_handoffs = [
        link
        for link in order_links
        if isinstance(link.get("handoff"), Mapping)
        and str(link["handoff"].get("approval_status") or "") == "rejected"
        and not link["handoff"].get("released_at")
    ]
    if lifecycle in {"active", "paused", "stopped"} and rejected_handoffs:
        next_action = _action(
            "fulfillment_handoff_rejected",
            "resolve_rejected_fulfillment_handoff",
            "采购审批已拒绝，请决定关闭订单或解除履约锁",
            ",".join(str(link.get("order_id") or "") for link in rejected_handoffs[:5]),
        )
    cash_budget = _finite_float(controls.get("cash_budget")) or 0.0

    return {
        "trial_id": str(trial.get("trial_id") or ""),
        "cookie_id": cookie_id,
        "category": category,
        "lifecycle": lifecycle,
        "started_at": str(trial.get("started_at") or ""),
        "controls": controls,
        "candidate_ids": valid_candidate_ids,
        "invalid_candidate_ids": [candidate_id for candidate_id in attached_ids if candidate_id not in candidate_id_set],
        "stages": stages,
        "completed_prefix_count": completed_prefix_count,
        "exposure": exposure,
        "committed_cash_requirement": committed_cash,
        "procurement_commitment": procurement_commitment,
        "remaining_cash_budget": round(
            max(0.0, cash_budget - max(exposure, committed_cash, procurement_commitment)),
            2,
        ),
        "realized_profit": realized_profit,
        "risk_realized_profit": risk_realized_profit,
        "after_sales_ratio": after_sales_ratio,
        "paid_order_count": paid_count,
        "paid_order_ids": sorted(paid_order_ids),
        "order_links": order_links,
        "fulfillment_handoffs": fulfillment_handoffs,
        "fulfillment_gaps": fulfillment_gaps,
        "profit_reconciliation_gaps": profit_gaps,
        "after_sales_count": after_sales_count,
        "approval_count": len(approvals),
        "publish_commitment_count": publish_commitment_count,
        "committed_candidate_ids": committed_candidate_ids,
        "published_count": published_count,
        "published_items": [
            {
                "item_id": str(publish.get("external_id") or ""),
                "candidate_id": str(publish.get("candidate_id") or ""),
                "published_at": _iso(_epoch(publish.get("finished_at")) or 0),
            }
            for publish in publishes
        ],
        "traffic_observations": traffic_observations,
        "experiments": experiments,
        "item_performance": item_performance,
        "inquiry_count": len(inquiries),
        "inquiry_response_count": len(inquiry_responses),
        "inquiry_responses": inquiry_responses,
        "monitor_elapsed": monitor_elapsed,
        "review_eligible": review_eligible,
        "stop_required": stop_required,
        "stop_reasons": stop_reasons,
        "batch_draft_blocked": batch_draft_blocked,
        "batch_draft_blocked_reason": batch_block_reason,
        "next_action": next_action,
        "decision_history": decision_history,
        "checked_at": _iso(now),
    }


def render_trial_markdown(reconciled: Mapping[str, Any]) -> str:
    """Render a local Markdown report for one reconciled trial."""
    controls = dict(reconciled.get("controls") or {})
    lines = [
        f"# Commercial Alpha Trial — {reconciled.get('trial_id') or ''}",
        "",
        f"- 账号: {reconciled.get('cookie_id') or '-'}",
        f"- 品类: {reconciled.get('category') or '-'}",
        f"- 生命周期: {reconciled.get('lifecycle') or '-'}",
        f"- 开始时间: {reconciled.get('started_at') or '-'}",
        f"- 检查时间: {reconciled.get('checked_at') or '-'}",
        "",
        "## 控制参数",
        "",
        "| 参数 | 值 |",
        "|---|---|",
        f"| max_candidates | {controls.get('max_candidates', '-')} |",
        f"| max_published | {controls.get('max_published', '-')} |",
        f"| cash_budget | {controls.get('cash_budget', '-')} |",
        f"| max_realized_loss | {controls.get('max_realized_loss', '-')} |",
        f"| max_after_sales_rate | {controls.get('max_after_sales_rate', '-')} |",
        f"| monitor_days | {controls.get('monitor_days', '-')} |",
        f"| target_inquiries | {controls.get('target_inquiries', '-')} |",
        f"| target_paid_orders | {controls.get('target_paid_orders', '-')} |",
        f"| target_candidates | {controls.get('target_candidates', '-')} |",
        f"| min_observation_hours | {controls.get('min_observation_hours', '-')} |",
        f"| min_views_for_diagnosis | {controls.get('min_views_for_diagnosis', '-')} |",
        f"| experiment_days | {controls.get('experiment_days', '-')} |",
        "",
        "## 证据阶段",
        "",
        "| 阶段 | 状态 | 证据 |",
        "|---|---|---|",
    ]
    for stage in reconciled.get("stages") or []:
        lines.append(
            f"| {_markdown_cell(stage.get('code', '-'))} | {_markdown_cell(stage.get('status', '-'))} "
            f"| {_markdown_cell(stage.get('evidence', ''))} |"
        )

    lines.extend([
        "",
        "## 风险指标",
        "",
        f"- 实际采购敞口: ¥{reconciled.get('exposure', 0)}",
        f"- 发布备货需求: ¥{reconciled.get('committed_cash_requirement', 0)}",
        f"- 履约采购承诺: ¥{reconciled.get('procurement_commitment', 0)}",
        f"- 剩余采购预算: ¥{reconciled.get('remaining_cash_budget', 0)}",
        f"- 已实现利润: ¥{reconciled.get('realized_profit', 0)}",
        f"- 售后率: {reconciled.get('after_sales_ratio', 0)}",
        f"- 发布承诺: {reconciled.get('publish_commitment_count', 0)}",
        f"- 真实发布: {reconciled.get('published_count', 0)}",
        f"- 有效咨询: {reconciled.get('inquiry_count', 0)}",
        f"- 已发送回复: {reconciled.get('inquiry_response_count', 0)}",
        f"- 已付款订单: {reconciled.get('paid_order_count', 0)}",
        f"- 风险止损利润口径: ¥{reconciled.get('risk_realized_profit', 0)}",
        f"- stop_required: {'是' if reconciled.get('stop_required') else '否'}",
    ])
    for reason in reconciled.get("stop_reasons") or []:
        lines.append(f"  - {reason}")

    lines.extend([
        "",
        "## 商品观察",
        "",
        "| 商品 | 候选 | 浏览 | 想要 | 咨询 | 付款 | 当前动作 |",
        "|---|---|---:|---:|---:|---:|---|",
    ])
    performance = reconciled.get("item_performance") or []
    if performance:
        for item in performance:
            recommendation = item.get("recommendation") if isinstance(item.get("recommendation"), Mapping) else {}
            lines.append(
                f"| {_markdown_cell(item.get('item_id', '-'))} | {_markdown_cell(item.get('candidate_id', '-'))} "
                f"| {item.get('views', 0)} | {item.get('wants', 0)} | {item.get('inquiries', 0)} "
                f"| {item.get('paid_orders', 0)} | {_markdown_cell(recommendation.get('message', '-'))} |"
            )
    else:
        lines.append("| - | - | 0 | 0 | 0 | 0 | 尚无真实发布 |")

    lines.extend([
        "",
        "## 单变量实验",
        "",
        "| 实验 | 商品 | 变量 | 状态 | 审批 | 执行 | 开始时间 | 激活凭证 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    experiments = reconciled.get("experiments") or []
    if experiments:
        for experiment in experiments:
            lines.append(
                f"| {_markdown_cell(experiment.get('experiment_id', '-'))} "
                f"| {_markdown_cell(experiment.get('item_id', '-'))} "
                f"| {_markdown_cell(experiment.get('variable', '-'))} "
                f"| {_markdown_cell(experiment.get('status', '-'))} "
                f"| {_markdown_cell(experiment.get('approval_status', '-'))} "
                f"| {_markdown_cell(experiment.get('job_status') or experiment.get('approval_execution_status') or '-')} "
                f"| {_markdown_cell(_iso(experiment.get('started_at')) if experiment.get('started_at') else '-')} "
                f"| {_markdown_cell(experiment.get('activation_evidence_ref', '-'))} "
                f"| {_markdown_cell(experiment.get('decision', '-'))} |"
            )
    else:
        lines.append("| - | - | - | 暂无实验 | - | - | - | - | - |")

    lines.extend([
        "",
        "## 履约交接",
        "",
        "| 订单 | 候选 | 状态 | 履约锁 | 采购审批 | 履约证据 | 利润证据 |",
        "|---|---|---|---|---|---|---|",
    ])
    order_links = reconciled.get("order_links") or []
    if order_links:
        for link in order_links:
            handoff = link.get("handoff") if isinstance(link.get("handoff"), Mapping) else {}
            lines.append(
                f"| {_markdown_cell(link.get('order_id', '-'))} | {_markdown_cell(link.get('candidate_id', '-'))} "
                f"| {_markdown_cell(link.get('status', '-'))} | {'已锁定' if link.get('locked') else '未锁定'} "
                f"| {_markdown_cell(handoff.get('approval_id', '未创建'))} "
                f"| {_markdown_cell('完整' if (link.get('fulfillment_audit') or {}).get('ready') else ','.join((link.get('fulfillment_audit') or {}).get('gaps') or []))} "
                f"| {_markdown_cell('完整' if (link.get('profit_audit') or {}).get('ready') else ','.join((link.get('profit_audit') or {}).get('gaps') or []))} |"
            )
    else:
        lines.append("| - | - | 暂无已付款订单 | - | - | - | - |")

    next_action = reconciled.get("next_action") or {}
    lines.extend([
        "",
        "## 下一步",
        "",
        f"- 动作: {next_action.get('action', '-')}",
        f"- 说明: {next_action.get('message', '-')}",
        f"- 详情: {next_action.get('detail', '-')}",
        "",
        "## 决策记录",
        "",
    ])
    decisions = reconciled.get("decision_history") or []
    if decisions:
        for decision in decisions:
            lines.append(
                f"- {decision.get('decided_at') and _iso(decision['decided_at']) or '-'} "
                f"{decision.get('decision')}: {_markdown_cell(decision.get('note', ''))}"
            )
    else:
        lines.append("暂无决策记录。")
    lines.append("")
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


# ---------------------------------------------------------------------------
# Stage evidence helpers
# ---------------------------------------------------------------------------

def _account_ready(account: Optional[Mapping[str, Any]]) -> bool:
    account = account or {}
    return all(
        bool(account.get(key))
        for key in (
            "configured", "enabled", "prompt_ready", "sync_current",
            "runtime_connected", "runtime_fresh", "runtime_unblocked",
        )
    )


def _account_ready_evidence(account: Optional[Mapping[str, Any]]) -> str:
    account = account or {}
    if not bool(account.get("configured")):
        return "account=missing_or_not_configured"
    if not bool(account.get("enabled")):
        return f"account={account.get('cookie_id') or '?'} disabled"
    parts = [
        "enabled=" + ("yes" if account.get("enabled") else "no"),
        "prompt=" + ("yes" if account.get("prompt_ready") else "no"),
        "sync=" + ("yes" if account.get("sync_current") else "no"),
        "runtime=" + ("yes" if account.get("runtime_connected") else "no"),
        "fresh=" + ("yes" if account.get("runtime_fresh") else "no"),
        "unblocked=" + ("yes" if account.get("runtime_unblocked") else "no"),
    ]
    return f"account={account.get('cookie_id') or '?'} " + " ".join(parts)


def _find_approvals(
    work_orders: List[Mapping[str, Any]],
    *,
    cookie_id: str,
    candidate_ids: set[str],
    trial_start: float,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for work_order in work_orders:
        if not isinstance(work_order, Mapping) or str(work_order.get("kind") or "") != "resale_listing":
            continue
        created_at = _epoch(work_order.get("created_at")) or 0.0
        if trial_start and created_at < trial_start:
            continue
        if _find_cookie_id(work_order) != cookie_id:
            continue
        context = work_order.get("context")
        if not isinstance(context, Mapping):
            continue
        candidate_id = str(context.get("candidate_id") or "").strip()
        if candidate_id not in candidate_ids:
            continue
        for approval in work_order.get("approvals") or []:
            if not isinstance(approval, Mapping) or str(approval.get("action") or "") != "publish_listing":
                continue
            approval_id = str(approval.get("approval_id") or "").strip()
            if not approval_id or approval_id in seen:
                continue
            seen.add(approval_id)
            matches.append({
                "work_order_id": str(work_order.get("work_order_id") or ""),
                "approval_id": approval_id,
                "candidate_id": candidate_id,
                "status": str(approval.get("status") or "pending"),
                "execution_status": str(approval.get("execution_status") or ""),
                "created_at": created_at,
            })
    matches.sort(key=lambda item: (item["created_at"], item["approval_id"]))
    return matches


def _approvals_evidence(approvals: List[Mapping[str, Any]]) -> str:
    if not approvals:
        return "no account-scoped resale_listing approval after trial start"
    return " ".join(
        f"{approval.get('approval_id')}={approval.get('status')}"
        for approval in approvals[:5]
    )


def _approval_statuses(work_orders: List[Mapping[str, Any]]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for work_order in work_orders:
        if not isinstance(work_order, Mapping):
            continue
        for approval in work_order.get("approvals") or []:
            if not isinstance(approval, Mapping):
                continue
            approval_id = str(approval.get("approval_id") or "").strip()
            if approval_id:
                statuses[approval_id] = str(approval.get("status") or "pending").strip()
    return statuses


def _find_publish_jobs(
    jobs: List[Mapping[str, Any]],
    *,
    approvals: List[Mapping[str, Any]],
    trial_start: float,
) -> List[Dict[str, Any]]:
    approval_map = {
        str(approval.get("approval_id") or ""): approval
        for approval in approvals
        if str(approval.get("approval_id") or "")
    }
    matches: List[Dict[str, Any]] = []
    seen_external_ids: set[str] = set()
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        approval_id = str(job.get("approval_id") or "")
        approval = approval_map.get(approval_id)
        if approval is None or str(job.get("action") or "") != "publish_listing":
            continue
        if str(job.get("status") or "") != "completed":
            continue
        created_at = _epoch(job.get("created_at")) or 0.0
        if trial_start and created_at < trial_start:
            continue
        result = job.get("result") if isinstance(job.get("result"), Mapping) else {}
        external_id = _extract_external_id(result)
        if not external_id or external_id in seen_external_ids:
            continue
        seen_external_ids.add(external_id)
        matches.append({
            "job_id": str(job.get("job_id") or ""),
            "approval_id": approval_id,
            "candidate_id": str(approval.get("candidate_id") or ""),
            "external_id": external_id,
            "finished_at": _epoch(job.get("finished_at")) or created_at,
        })
    matches.sort(key=lambda item: (item["finished_at"], item["external_id"]))
    return matches


def _extract_external_id(result: Mapping[str, Any]) -> str:
    """Extract a real publish external_id from a publish job result.

    The publish result is commonly nested at ``result.publish_result.external_id``.
    Blank values and stub_* ids are rejected (they are simulator artifacts).
    """
    external = _collect_external_ids(result)
    for candidate in external:
        if candidate and not candidate.lower().startswith("stub_"):
            return candidate
    return ""


def _collect_external_ids(value: Any) -> List[str]:
    """Collect every plausible external item id from a publish result."""
    collected: List[str] = []
    if isinstance(value, Mapping):
        if not bool(value.get("ok", True)):
            return collected
        for key in ("external_id", "item_id", "xianyu_item_id"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                collected.append(raw.strip())
        for key in ("publish_result", "result", "data"):
            child = value.get(key)
            if isinstance(child, Mapping):
                collected.extend(_collect_external_ids(child))
        # Shallow one-level fallback for other nested dicts.
        for child in value.values():
            if isinstance(child, Mapping) and any(
                key in child for key in ("external_id", "item_id", "xianyu_item_id")
            ):
                collected.extend(_collect_external_ids(child))
    return collected


def _publishes_evidence(publishes: List[Mapping[str, Any]]) -> str:
    if not publishes:
        return "no completed publish job with a real external_id"
    return " ".join(
        f"{publish.get('job_id')}={publish.get('external_id')}@{_iso(_epoch(publish.get('finished_at')) or 0)}"
        for publish in publishes[:5]
    )


def _find_inquiries(
    intake_records: List[Mapping[str, Any]],
    *,
    cookie_id: str,
    publishes: List[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    publish_times = {
        str(publish.get("external_id") or ""): _epoch(publish.get("finished_at")) or 0.0
        for publish in publishes
        if str(publish.get("external_id") or "")
    }
    matches: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for record in intake_records:
        if not isinstance(record, Mapping):
            continue
        if str(record.get("status") or "") == "duplicate":
            continue
        if str(record.get("cookie_id") or "") != cookie_id:
            continue
        item_id = str(record.get("item_id") or "").strip()
        after_epoch = publish_times.get(item_id)
        if after_epoch is None:
            continue
        created_at = _epoch(record.get("created_at")) or 0.0
        if after_epoch > 0 and created_at < after_epoch:
            continue
        intake_id = str(record.get("intake_id") or "").strip()
        if not intake_id or intake_id in seen:
            continue
        seen.add(intake_id)
        matches.append({
            "intake_id": intake_id,
            "item_id": item_id,
            "approval_id": str(record.get("approval_id") or "").strip(),
            "work_order_id": str(record.get("work_order_id") or "").strip(),
            "status": str(record.get("status") or "").strip(),
            "created_at": created_at,
        })
    matches.sort(key=lambda item: (item["created_at"], item["intake_id"]))
    return matches


def _inquiries_evidence(inquiries: List[Mapping[str, Any]], *, target: int) -> str:
    if not inquiries:
        return "no non-duplicate intake for this account + published item"
    ids = ",".join(str(inquiry.get("intake_id") or "") for inquiry in inquiries[:5])
    return f"inquiries={len(inquiries)}/{target} ids={ids}"


def _find_inquiry_responses(
    jobs: List[Mapping[str, Any]],
    *,
    inquiries: List[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Match successful send jobs to the exact account/item-scoped intake approvals."""
    inquiry_by_approval = {
        str(inquiry.get("approval_id") or ""): inquiry
        for inquiry in inquiries
        if str(inquiry.get("approval_id") or "")
    }
    responses: List[Dict[str, Any]] = []
    responded_intakes: set[str] = set()
    sorted_jobs = sorted(
        (item for item in jobs if isinstance(item, Mapping)),
        key=lambda item: _epoch(item.get("created_at")) or 0.0,
    )
    for job in sorted_jobs:
        if str(job.get("action") or "") != "send_message":
            continue
        if str(job.get("status") or "") != "completed":
            continue
        approval_id = str(job.get("approval_id") or "").strip()
        inquiry = inquiry_by_approval.get(approval_id)
        if inquiry is None:
            continue
        result = job.get("result") if isinstance(job.get("result"), Mapping) else {}
        if not bool(result.get("ok")):
            continue
        created_at = _epoch(job.get("created_at")) or 0.0
        if created_at < (_epoch(inquiry.get("created_at")) or 0.0):
            continue
        intake_id = str(inquiry.get("intake_id") or "")
        if not intake_id or intake_id in responded_intakes:
            continue
        responded_intakes.add(intake_id)
        responses.append({
            "intake_id": intake_id,
            "item_id": str(inquiry.get("item_id") or ""),
            "approval_id": approval_id,
            "job_id": str(job.get("job_id") or ""),
            "sent_at": _iso(_epoch(job.get("finished_at")) or created_at),
        })
    return responses


def _inquiry_responses_evidence(responses: List[Mapping[str, Any]], *, target: int) -> str:
    if not responses:
        return "no successful send_message job linked to an observed inquiry approval"
    ids = ",".join(str(response.get("job_id") or "") for response in responses[:5])
    return f"responses={len(responses)}/{target} jobs={ids}"


def _find_paid_orders(
    runtime_orders: List[Mapping[str, Any]],
    *,
    cookie_id: str,
    publishes: List[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    publish_map = {
        str(publish.get("external_id") or ""): publish
        for publish in publishes
        if str(publish.get("external_id") or "")
    }
    matches: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for order in runtime_orders:
        if not isinstance(order, Mapping):
            continue
        if _is_synthetic_order_source(order.get("source")):
            continue
        if str(order.get("cookie_id") or "") != cookie_id:
            continue
        item_id = str(order.get("item_id") or "").strip()
        publish = publish_map.get(item_id)
        if publish is None:
            continue
        if not _is_paid_or_later(order):
            continue
        created_at = _epoch(order.get("created_at")) or 0.0
        after_epoch = _epoch(publish.get("finished_at")) or 0.0
        if after_epoch > 0 and created_at < after_epoch:
            continue
        order_id = str(order.get("order_id") or "").strip()
        if not order_id or order_id in seen:
            continue
        seen.add(order_id)
        sanitized = dict(order)
        sanitized.pop("buyer_id", None)
        sanitized["candidate_id"] = str(publish.get("candidate_id") or "")
        matches.append(sanitized)
    matches.sort(key=lambda order: _epoch(order.get("created_at")) or 0.0)
    return matches


def _is_paid_or_later(order: Mapping[str, Any]) -> bool:
    status = str(order.get("status") or "")
    if status in PAID_OR_LATER_STATUSES:
        return True
    if status == "closed":
        return any(
            str(h.get("new_status") or "") in PAID_OR_LATER_STATUSES
            for h in order.get("status_history") or []
            if isinstance(h, Mapping)
        )
    return False


def _is_after_sales(order: Mapping[str, Any]) -> bool:
    status = str(order.get("status") or "")
    if status in AFTER_SALES_STATUSES:
        return True
    return any(
        str(h.get("new_status") or "") in AFTER_SALES_STATUSES
        for h in order.get("status_history") or []
        if isinstance(h, Mapping)
    )


def _fulfillment_reached(order: Mapping[str, Any]) -> bool:
    status = str(order.get("status") or "")
    if status in FULFILLMENT_STATUSES:
        return True
    return any(
        str(h.get("new_status") or "") in FULFILLMENT_STATUSES
        for h in order.get("status_history") or []
        if isinstance(h, Mapping)
    )


def _delivery_reached(order: Mapping[str, Any]) -> bool:
    if str(order.get("status") or "") == "delivered":
        return True
    return any(
        str(item.get("new_status") or "") == "delivered"
        for item in order.get("status_history") or []
        if isinstance(item, Mapping)
    )


def _paid_orders_evidence(paid_orders: List[Mapping[str, Any]], *, target: int) -> str:
    if not paid_orders:
        return "no paid-or-later order for this account + published item"
    parts = [f"{order.get('order_id')}={order.get('status')}" for order in paid_orders[:5]]
    return f"orders={len(paid_orders)}/{target} " + " ".join(parts)


def _is_synthetic_order_source(source: Any) -> bool:
    normalized = str(source or "").strip().lower()
    return bool(normalized) and normalized.startswith(("fake", "stub", "mock", "test", "demo"))


def _fulfillment_evidence(
    fulfilled_orders: List[Mapping[str, Any]],
    *,
    gaps: Optional[List[Mapping[str, Any]]] = None,
) -> str:
    if not fulfilled_orders:
        gap_text = _audit_gap_evidence(gaps or [])
        return "no paid order has complete fulfillment evidence" + (f"; {gap_text}" if gap_text else "")
    parts = []
    for order in fulfilled_orders[:5]:
        parts.append(f"{order.get('order_id')}={order.get('status')}")
    return " ".join(parts)


def _matched_resale_orders(
    resale_orders: List[Mapping[str, Any]],
    *,
    trial_id: str,
    cookie_id: str,
    candidate_ids: set[str],
    paid_orders: List[Mapping[str, Any]],
    trial_start: float,
) -> List[Mapping[str, Any]]:
    paid_items = {
        str(order.get("order_id") or "").strip(): str(order.get("item_id") or "").strip()
        for order in paid_orders
        if str(order.get("order_id") or "").strip()
    }
    matched: List[Mapping[str, Any]] = []
    for order in resale_orders:
        if not isinstance(order, Mapping):
            continue
        if str(order.get("candidate_id") or "") not in candidate_ids:
            continue
        xianyu_order_id = str(order.get("xianyu_order_id") or "").strip()
        item_id = paid_items.get(xianyu_order_id)
        if not xianyu_order_id or item_id is None:
            continue
        if str(order.get("commercial_trial_id") or "").strip() != trial_id:
            continue
        if str(order.get("cookie_id") or "").strip() != cookie_id:
            continue
        if str(order.get("item_id") or "").strip() != item_id:
            continue
        created_at = _epoch(order.get("created_at")) or 0.0
        # Resale records use second-resolution ISO timestamps while trials use
        # epoch fractions. Preserve the time gate without rejecting records
        # created later in the same wall-clock second.
        if trial_start and created_at + 1.0 < trial_start:
            continue
        matched.append(order)
    matched.sort(key=lambda order: _epoch(order.get("created_at")) or 0.0)
    return matched


def _latest_resale_order_by_xianyu_id(
    matched_resale_orders: List[Mapping[str, Any]],
) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for order in matched_resale_orders:
        order_id = str(order.get("xianyu_order_id") or "").strip()
        if order_id:
            result[order_id] = order
    return result


def _audited_fulfilled_orders(
    *,
    paid_orders: List[Mapping[str, Any]],
    matched_resale_orders: List[Mapping[str, Any]],
    handoff_by_order_id: Mapping[str, Mapping[str, Any]],
) -> tuple[List[Mapping[str, Any]], List[Dict[str, Any]]]:
    resale_by_order = _latest_resale_order_by_xianyu_id(matched_resale_orders)
    fulfilled: List[Mapping[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    for runtime_order in paid_orders:
        order_id = str(runtime_order.get("order_id") or "").strip()
        reasons: List[str] = []
        if not _fulfillment_reached(runtime_order):
            reasons.append("platform_order_not_shipped")
        handoff = handoff_by_order_id.get(order_id)
        if not handoff:
            reasons.append("fulfillment_handoff_missing")
        elif str(handoff.get("approval_status") or "") != "approved":
            reasons.append("purchase_approval_not_approved")
        resale_order = resale_by_order.get(order_id)
        if not resale_order:
            reasons.append("linked_resale_order_missing")
        else:
            required_fields = {
                "purchase_task_id": "purchase_task_link_missing",
                "purchase_draft_id": "purchase_draft_link_missing",
                "fulfillment_lock_owner": "fulfillment_lock_link_missing",
                "supplier_order_id": "supplier_order_id_missing",
                "supplier_tracking_number": "supplier_tracking_missing",
                "xianyu_tracking_number": "xianyu_tracking_missing",
            }
            for field, reason in required_fields.items():
                if not str(resale_order.get(field) or "").strip():
                    reasons.append(reason)
            expected_owner = f"commercial_trial:{str(resale_order.get('commercial_trial_id') or '').strip()}"
            if str(resale_order.get("fulfillment_lock_owner") or "") != expected_owner:
                reasons.append("fulfillment_lock_owner_mismatch")
            if handoff and str(handoff.get("lock_owner") or "") != expected_owner:
                reasons.append("handoff_lock_owner_mismatch")
            if str(resale_order.get("status") or "") not in {"shipped", "completed"}:
                reasons.append("resale_order_not_shipped")
        if reasons:
            gaps.append({"order_id": order_id, "reasons": reasons})
        else:
            fulfilled.append(runtime_order)
    return fulfilled, gaps


def _audited_profit_orders(
    *,
    paid_orders: List[Mapping[str, Any]],
    matched_resale_orders: List[Mapping[str, Any]],
    fulfillment_gaps: List[Mapping[str, Any]],
) -> tuple[List[Mapping[str, Any]], List[Dict[str, Any]]]:
    resale_by_order = _latest_resale_order_by_xianyu_id(matched_resale_orders)
    fulfillment_gap_ids = {
        str(item.get("order_id") or "")
        for item in fulfillment_gaps
        if str(item.get("order_id") or "")
    }
    completed: List[Mapping[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    for runtime_order in paid_orders:
        order_id = str(runtime_order.get("order_id") or "").strip()
        reasons: List[str] = []
        if order_id in fulfillment_gap_ids:
            reasons.append("fulfillment_evidence_incomplete")
        if not _delivery_reached(runtime_order):
            reasons.append("platform_delivery_not_confirmed")
        resale_order = resale_by_order.get(order_id)
        if not resale_order:
            reasons.append("linked_resale_order_missing")
        else:
            if str(resale_order.get("status") or "") != "completed":
                reasons.append("resale_order_not_completed")
            completed_at = _epoch(resale_order.get("completed_at"))
            if not completed_at:
                reasons.append("completed_at_missing")
            if len(str(resale_order.get("financial_evidence_note") or "").strip()) < 8:
                reasons.append("financial_evidence_note_missing")
            if len(str(resale_order.get("financial_evidence_ref") or "").strip()) < 2:
                reasons.append("financial_evidence_ref_missing")
            reconciled_at = _epoch(resale_order.get("financial_reconciled_at"))
            if not reconciled_at:
                reasons.append("financial_reconciled_at_missing")
            sale_amount = _finite_float(resale_order.get("sale_amount"))
            supplier_cost = _finite_float(resale_order.get("supplier_cost"))
            shipping_cost = _finite_float(resale_order.get("shipping_cost"))
            platform_fee = _finite_float(resale_order.get("platform_fee"))
            actual_profit = _finite_float(resale_order.get("actual_profit"))
            runtime_amount = _finite_float(runtime_order.get("amount"))
            if sale_amount is None or sale_amount <= 0:
                reasons.append("sale_amount_invalid")
            if runtime_amount is None or runtime_amount <= 0 or (
                sale_amount is not None and abs(runtime_amount - sale_amount) > 0.01
            ):
                reasons.append("sale_amount_not_confirmed_by_platform_order")
            if supplier_cost is None or supplier_cost <= 0:
                reasons.append("supplier_cost_invalid")
            if shipping_cost is None or shipping_cost < 0:
                reasons.append("shipping_cost_invalid")
            if platform_fee is None or platform_fee < 0:
                reasons.append("platform_fee_invalid")
            if None not in {sale_amount, supplier_cost, shipping_cost, platform_fee, actual_profit}:
                expected_profit = round(sale_amount - supplier_cost - shipping_cost - platform_fee, 2)
                if abs(actual_profit - expected_profit) > 0.01:
                    reasons.append("actual_profit_formula_mismatch")
            else:
                reasons.append("actual_profit_invalid")
        if reasons:
            gaps.append({"order_id": order_id, "reasons": list(dict.fromkeys(reasons))})
        else:
            completed.append(resale_order)
    return completed, gaps


def _profits_evidence(
    profits: List[Mapping[str, Any]],
    *,
    gaps: Optional[List[Mapping[str, Any]]] = None,
) -> str:
    if not profits:
        gap_text = _audit_gap_evidence(gaps or [])
        return "no completed order has audited financial evidence" + (f"; {gap_text}" if gap_text else "")
    return " ".join(
        f"{order.get('resale_order_id') or order.get('id')}={_finite_float(order.get('actual_profit')) or 0:.2f}"
        for order in profits[:5]
    )


def _audit_gap_evidence(gaps: List[Mapping[str, Any]]) -> str:
    return " ".join(
        f"{item.get('order_id') or '-'}={','.join(str(reason) for reason in (item.get('reasons') or [])[:4])}"
        for item in gaps[:5]
    )


def _cash_exposure(
    *,
    paid_orders: List[Mapping[str, Any]],
    matched_resale_orders: List[Mapping[str, Any]],
    publishes: List[Mapping[str, Any]],
    candidates_by_id: Mapping[str, Mapping[str, Any]],
) -> float:
    """Return procurement cash committed to paid orders, never sales revenue."""
    resale_by_order_id = {
        str(order.get("xianyu_order_id") or ""): order
        for order in matched_resale_orders
        if str(order.get("xianyu_order_id") or "")
    }
    candidate_by_item_id = {
        str(publish.get("external_id") or ""): str(publish.get("candidate_id") or "")
        for publish in publishes
    }
    exposure = 0.0
    for order in paid_orders:
        order_id = str(order.get("order_id") or "")
        resale_order = resale_by_order_id.get(order_id)
        if resale_order is not None:
            exposure += (_finite_float(resale_order.get("supplier_cost")) or 0.0)
            exposure += (_finite_float(resale_order.get("shipping_cost")) or 0.0)
            continue
        candidate_id = candidate_by_item_id.get(str(order.get("item_id") or ""), "")
        candidate = candidates_by_id.get(candidate_id) or {}
        exposure += (_finite_float(candidate.get("supplier_price")) or 0.0)
        exposure += (_finite_float(candidate.get("supplier_shipping")) or 0.0)
    return round(exposure, 2)


def _publish_commitment_count(approvals: List[Mapping[str, Any]]) -> int:
    """Count non-rejected publish approvals, including completed publishes."""
    return sum(
        1
        for approval in approvals
        if str(approval.get("status") or "") not in {"rejected", "cancelled"}
    )


def _committed_cash_requirement(
    *,
    approvals: List[Mapping[str, Any]],
    candidates_by_id: Mapping[str, Mapping[str, Any]],
) -> float:
    total = 0.0
    for approval in approvals:
        if str(approval.get("status") or "") in {"rejected", "cancelled"}:
            continue
        candidate = candidates_by_id.get(str(approval.get("candidate_id") or "")) or {}
        total += (_finite_float(candidate.get("supplier_price")) or 0.0)
        total += (_finite_float(candidate.get("supplier_shipping")) or 0.0)
    return round(total, 2)


def _sanitize_decisions(values: Any) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, Mapping):
            continue
        decision = str(value.get("decision") or "").strip().lower()
        if decision not in {"continue", "pause", "stop", "complete"}:
            continue
        decisions.append({
            "decision": decision,
            "note": str(value.get("note") or "")[:500],
            "review_eligible": bool(value.get("review_eligible")),
            "decided_at": _epoch(value.get("decided_at")) or 0.0,
        })
    decisions.sort(key=lambda item: item["decided_at"])
    return decisions


def _sanitize_fulfillment_handoffs(values: Any) -> List[Dict[str, Any]]:
    """Return audit-safe paid-order handoff records.

    The trial store never keeps buyer names, addresses, messages, cookies, or
    supplier credentials. These records only preserve the IDs and monetary
    commitment needed to prove which paid order entered which approval flow.
    """
    handoffs: List[Dict[str, Any]] = []
    seen_orders: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, Mapping):
            continue
        order_id = str(value.get("order_id") or "").strip()
        if not order_id or order_id in seen_orders:
            continue
        seen_orders.add(order_id)
        handoffs.append({
            "order_id": order_id,
            "item_id": str(value.get("item_id") or "").strip(),
            "candidate_id": str(value.get("candidate_id") or "").strip(),
            "plan_id": str(value.get("plan_id") or "").strip(),
            "purchase_draft_id": str(value.get("purchase_draft_id") or "").strip(),
            "work_order_id": str(value.get("work_order_id") or "").strip(),
            "approval_id": str(value.get("approval_id") or "").strip(),
            "lock_owner": str(value.get("lock_owner") or "").strip(),
            "quantity": max(1, int(_finite_float(value.get("quantity")) or 1)),
            "total_cost": round(max(0.0, _finite_float(value.get("total_cost")) or 0.0), 2),
            "operator_note": str(value.get("operator_note") or "").strip()[:500],
            "created_at": _epoch(value.get("created_at")) or 0.0,
            "released_at": _epoch(value.get("released_at")) or 0.0,
        })
    handoffs.sort(key=lambda item: (item["created_at"], item["order_id"]))
    return handoffs


def _sanitize_traffic_observations(values: Any) -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, Mapping):
            continue
        observation_id = str(value.get("observation_id") or "").strip()
        item_id = str(value.get("item_id") or "").strip()
        candidate_id = str(value.get("candidate_id") or "").strip()
        source = str(value.get("source") or "").strip().lower()
        observed_at = _epoch(value.get("observed_at")) or 0.0
        views = _finite_float(value.get("views"))
        wants = _finite_float(value.get("wants"))
        if (
            not observation_id
            or observation_id in seen
            or not item_id
            or not candidate_id
            or source not in TRAFFIC_SOURCES
            or observed_at <= 0
            or views is None
            or wants is None
            or not views.is_integer()
            or not wants.is_integer()
            or views < 0
            or wants < 0
            or wants > views
        ):
            continue
        seen.add(observation_id)
        observations.append({
            "observation_id": observation_id,
            "item_id": item_id,
            "candidate_id": candidate_id,
            "source": source,
            "local_date": str(value.get("local_date") or _local_date(observed_at))[:10],
            "views": int(views),
            "wants": int(wants),
            "evidence_note": str(value.get("evidence_note") or "").strip()[:300],
            "evidence_ref": str(value.get("evidence_ref") or "").strip()[:300],
            "observed_at": observed_at,
            "first_observed_at": _epoch(value.get("first_observed_at")) or observed_at,
            "recorded_at": _epoch(value.get("recorded_at")) or observed_at,
            "updated_at": _epoch(value.get("updated_at")) or observed_at,
        })
    observations.sort(key=lambda item: (item["observed_at"], item["observation_id"]))
    return observations


def _sanitize_experiments(values: Any) -> List[Dict[str, Any]]:
    experiments: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, Mapping):
            continue
        experiment_id = str(value.get("experiment_id") or "").strip()
        item_id = str(value.get("item_id") or "").strip()
        candidate_id = str(value.get("candidate_id") or "").strip()
        variable = str(value.get("variable") or "").strip().lower()
        status = str(value.get("status") or "active").strip().lower()
        baseline_id = str(value.get("baseline_observation_id") or "").strip()
        started_at = _epoch(value.get("started_at")) or 0.0
        proposed_at = _epoch(value.get("proposed_at")) or started_at
        if (
            not experiment_id
            or experiment_id in seen
            or not item_id
            or not candidate_id
            or variable not in EXPERIMENT_VARIABLES
            or status not in {"proposed", "active", "decided", "cancelled"}
            or not baseline_id
            or proposed_at <= 0
            or (status in {"active", "decided"} and started_at <= 0)
        ):
            continue
        seen.add(experiment_id)
        planned_days = int(_finite_float(value.get("planned_days")) or 1)
        experiments.append({
            "experiment_id": experiment_id,
            "item_id": item_id,
            "candidate_id": candidate_id,
            "variable": variable,
            "hypothesis": str(value.get("hypothesis") or "").strip()[:300],
            "before_value": str(value.get("before_value") or "").strip()[:300],
            "after_value": str(value.get("after_value") or "").strip()[:300],
            "planned_days": max(1, min(14, planned_days)),
            "baseline_observation_id": baseline_id,
            "baseline_views": max(0, int(_finite_float(value.get("baseline_views")) or 0)),
            "baseline_wants": max(0, int(_finite_float(value.get("baseline_wants")) or 0)),
            "baseline_inquiries": max(0, int(_finite_float(value.get("baseline_inquiries")) or 0)),
            "baseline_paid_orders": max(0, int(_finite_float(value.get("baseline_paid_orders")) or 0)),
            "proposed_at": proposed_at,
            "started_at": started_at,
            "status": status,
            "work_order_id": str(value.get("work_order_id") or "").strip(),
            "approval_id": str(value.get("approval_id") or "").strip(),
            "activation_mode": str(value.get("activation_mode") or "").strip().lower(),
            "activation_note": str(value.get("activation_note") or "").strip()[:500],
            "activation_evidence_ref": str(value.get("activation_evidence_ref") or "").strip()[:300],
            "decision": str(value.get("decision") or "").strip().lower(),
            "decision_note": str(value.get("decision_note") or "").strip()[:500],
            "ending_observation_id": str(value.get("ending_observation_id") or "").strip(),
            "decided_at": _epoch(value.get("decided_at")) or 0.0,
            "cancelled_at": _epoch(value.get("cancelled_at")) or 0.0,
            "cancellation_note": str(value.get("cancellation_note") or "").strip()[:500],
            "result": _sanitize_experiment_result(value.get("result")),
        })
    experiments.sort(key=lambda item: (item["proposed_at"], item["experiment_id"]))
    return experiments


def _sanitize_experiment_result(value: Any) -> Dict[str, int]:
    value = value if isinstance(value, Mapping) else {}
    return {
        key: int(_finite_float(value.get(key)) or 0)
        for key in ("views_delta", "wants_delta", "inquiries_delta", "paid_orders_delta")
    }


def _enrich_experiment_execution(
    experiments: List[Mapping[str, Any]],
    *,
    work_orders: List[Mapping[str, Any]],
    jobs: List[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    approvals: Dict[str, Dict[str, Any]] = {}
    for work_order in work_orders:
        if not isinstance(work_order, Mapping):
            continue
        for approval in work_order.get("approvals") or []:
            if not isinstance(approval, Mapping):
                continue
            approval_id = str(approval.get("approval_id") or "").strip()
            if approval_id:
                approvals[approval_id] = {
                    "status": str(approval.get("status") or "pending"),
                    "execution_status": str(approval.get("execution_status") or ""),
                    "execution_ok": bool(
                        isinstance(approval.get("execution_result"), Mapping)
                        and approval["execution_result"].get("ok")
                    ),
                }
    jobs_by_approval: Dict[str, List[Mapping[str, Any]]] = {}
    for job in jobs:
        if not isinstance(job, Mapping) or str(job.get("action") or "") != "edit_listing":
            continue
        approval_id = str(job.get("approval_id") or "").strip()
        if approval_id:
            jobs_by_approval.setdefault(approval_id, []).append(job)

    enriched: List[Dict[str, Any]] = []
    for raw in experiments:
        experiment = dict(raw)
        approval_id = str(experiment.get("approval_id") or "")
        approval = approvals.get(approval_id) or {}
        linked_jobs = sorted(
            jobs_by_approval.get(approval_id, []),
            key=lambda item: _epoch(item.get("created_at")) or 0,
        )
        job = linked_jobs[-1] if linked_jobs else {}
        job_result = job.get("result") if isinstance(job.get("result"), Mapping) else {}
        experiment.update({
            "approval_status": str(approval.get("status") or "missing"),
            "approval_execution_status": str(approval.get("execution_status") or ""),
            "approval_execution_ok": bool(approval.get("execution_ok")),
            "job_id": str(job.get("job_id") or ""),
            "job_status": str(job.get("status") or ""),
            "job_result_ok": bool(job_result.get("ok")),
            "manual_required": str(experiment.get("variable") or "") not in {"title", "price"},
        })
        enriched.append(experiment)
    return enriched


def _build_item_performance(
    *,
    publishes: List[Mapping[str, Any]],
    observations: List[Mapping[str, Any]],
    experiments: List[Mapping[str, Any]],
    inquiries: List[Mapping[str, Any]],
    paid_orders: List[Mapping[str, Any]],
    controls: Mapping[str, Any],
    now: float,
) -> List[Dict[str, Any]]:
    performance: List[Dict[str, Any]] = []
    observation_hours = int(controls.get("min_observation_hours") or 24)
    view_threshold = int(controls.get("min_views_for_diagnosis") or 20)
    for publish in publishes:
        item_id = str(publish.get("external_id") or "")
        candidate_id = str(publish.get("candidate_id") or "")
        published_at = _epoch(publish.get("finished_at")) or 0.0
        item_observations = [
            dict(item)
            for item in observations
            if str(item.get("item_id") or "") == item_id
            and (_epoch(item.get("observed_at")) or 0) >= published_at
        ]
        latest = item_observations[-1] if item_observations else None
        previous = item_observations[-2] if len(item_observations) > 1 else None
        item_experiments = [dict(item) for item in experiments if str(item.get("item_id") or "") == item_id]
        active = next((item for item in reversed(item_experiments) if item.get("status") == "active"), None)
        pending = next((item for item in reversed(item_experiments) if item.get("status") == "proposed"), None)
        inquiry_count = sum(1 for item in inquiries if str(item.get("item_id") or "") == item_id)
        paid_count = sum(1 for item in paid_orders if str(item.get("item_id") or "") == item_id)
        views = int(latest.get("views") or 0) if latest else 0
        wants = int(latest.get("wants") or 0) if latest else 0
        latest_at = _epoch(latest.get("observed_at")) if latest else None
        hours_since_latest = round(max(0.0, now - latest_at) / 3600.0, 1) if latest_at else None
        observation_due = latest is None or bool(hours_since_latest is not None and hours_since_latest >= observation_hours)
        experiment_due = bool(
            active
            and now >= (_epoch(active.get("started_at")) or 0) + int(active.get("planned_days") or 1) * 86400
        )
        has_result_observation = bool(
            active
            and any(
                str(item.get("observation_id") or "") != str(active.get("baseline_observation_id") or "")
                and (_epoch(item.get("observed_at")) or 0) > (_epoch(active.get("started_at")) or 0)
                for item in item_observations
            )
        )
        recommendation = _item_recommendation(
            has_observation=latest is not None,
            observation_due=observation_due,
            views=views,
            view_threshold=view_threshold,
            inquiries=inquiry_count,
            paid_orders=paid_count,
            pending_experiment=pending,
            active_experiment=active,
            experiment_due=experiment_due,
            has_result_observation=has_result_observation,
        )
        performance.append({
            "item_id": item_id,
            "candidate_id": candidate_id,
            "published_at": _iso(published_at),
            "observation_count": len(item_observations),
            "latest_observation": latest,
            "views": views,
            "wants": wants,
            "views_delta": views - int(previous.get("views") or 0) if previous else views,
            "wants_delta": wants - int(previous.get("wants") or 0) if previous else wants,
            "inquiries": inquiry_count,
            "paid_orders": paid_count,
            "want_rate": round(wants / views, 4) if views else 0.0,
            "inquiry_rate": round(inquiry_count / views, 4) if views else 0.0,
            "hours_since_latest_observation": hours_since_latest,
            "observation_due": observation_due,
            "active_experiment": active,
            "pending_experiment": pending,
            "latest_experiment": item_experiments[-1] if item_experiments else None,
            "experiment_due": experiment_due,
            "has_result_observation": has_result_observation,
            "recommendation": recommendation,
        })
    return performance


def _item_recommendation(
    *,
    has_observation: bool,
    observation_due: bool,
    views: int,
    view_threshold: int,
    inquiries: int,
    paid_orders: int,
    pending_experiment: Optional[Mapping[str, Any]],
    active_experiment: Optional[Mapping[str, Any]],
    experiment_due: bool,
    has_result_observation: bool,
) -> Dict[str, str]:
    if paid_orders > 0:
        return _action("paid", "fulfill_and_reconcile", "已有付款，优先履约并核算真实利润")
    if pending_experiment:
        approval_status = str(pending_experiment.get("approval_status") or "missing")
        job_status = str(pending_experiment.get("job_status") or "")
        if approval_status == "rejected":
            return _action("experiment_rejected", "cancel_or_revise_experiment", "实验变更审批已拒绝，请取消或重新提案")
        if approval_status != "approved":
            return _action("experiment_approval", "review_experiment_approval", "实验变更等待人工审批")
        if pending_experiment.get("manual_required"):
            return _action("experiment_manual_change", "confirm_manual_listing_change", "审批已通过，人工修改商品后提交凭证确认")
        if job_status != "completed" or not pending_experiment.get("job_result_ok"):
            return _action("experiment_execution", "execute_experiment_change", "审批已通过，等待商品编辑任务成功执行")
        return _action("experiment_confirmation", "sync_and_activate_experiment", "编辑已提交，重新同步商品并确认后开始计时")
    if active_experiment:
        if experiment_due and has_result_observation:
            return _action("experiment_due", "decide_experiment", "实验窗口已满，依据新快照保留或回退")
        if observation_due:
            return _action("experiment_snapshot_due", "record_traffic_snapshot", "记录实验结果快照，只评估当前变量")
        return _action("experiment_running", "hold_other_variables", "实验进行中，保持其他变量不变")
    if not has_observation:
        return _action("baseline_missing", "record_traffic_snapshot", "先记录浏览与想要基线")
    if not observation_due:
        return _action("observation_window", "monitor_item", "观察窗口未满，暂不改动商品")
    if inquiries > 0:
        return _action("inquiry_no_payment", "diagnose_conversion", "有咨询无付款，检查规格、交付、信任与到手价")
    if views >= view_threshold:
        return _action("views_no_inquiry", "start_conversion_experiment", "有浏览无咨询，只测试价格、首图或详情中的一个变量")
    return _action("low_views", "start_discovery_experiment", "浏览不足，只测试标题、首图或所在地中的一个变量")


def _traffic_next_action(item_performance: List[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    priorities = {
        "experiment_due": 1,
        "experiment_snapshot_due": 2,
        "experiment_rejected": 3,
        "experiment_approval": 4,
        "experiment_manual_change": 5,
        "experiment_execution": 6,
        "experiment_confirmation": 7,
        "baseline_missing": 8,
        "inquiry_no_payment": 9,
        "views_no_inquiry": 10,
        "low_views": 11,
        "experiment_running": 12,
        "observation_window": 13,
    }
    candidates: List[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    for item in item_performance:
        recommendation = item.get("recommendation") if isinstance(item.get("recommendation"), Mapping) else {}
        code = str(recommendation.get("code") or "")
        if code in priorities:
            candidates.append((priorities[code], item, recommendation))
    if not candidates:
        return None
    _, item, recommendation = min(candidates, key=lambda value: value[0])
    return _action(
        str(recommendation.get("code") or "traffic_review"),
        str(recommendation.get("action") or "review_item"),
        str(recommendation.get("message") or "复核商品表现"),
        f"item_id={item.get('item_id') or ''}",
    )


def _decision_evidence(decision_history: List[Mapping[str, Any]]) -> str:
    if not decision_history:
        return "no operator decision recorded"
    latest = decision_history[-1]
    return (
        f"decision={latest.get('decision')} "
        f"decided_at={_iso(_epoch(latest.get('decided_at')) or 0.0)}"
    )


def _compute_stop(
    *,
    account_ready: bool,
    exposure: float,
    committed_cash: float,
    procurement_commitment: float,
    realized_profit: float,
    after_sales_ratio: float,
    paid_count: int,
    approval_exists: bool,
    publish_exists: bool,
    controls: Mapping[str, Any],
) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    cash_budget = _finite_float(controls.get("cash_budget")) or 0
    if exposure > cash_budget:
        reasons.append("exposure_exceeds_cash_budget")
    if committed_cash > cash_budget:
        reasons.append("committed_cash_exceeds_budget")
    if procurement_commitment > cash_budget:
        reasons.append("procurement_commitment_exceeds_budget")
    max_loss = _finite_float(controls.get("max_realized_loss")) or 0
    if max_loss > 0 and realized_profit <= -max_loss:
        reasons.append("realized_loss_exceeds_max")
    max_rate = _finite_float(controls.get("max_after_sales_rate")) or 0
    if paid_count >= 1 and max_rate > 0 and after_sales_ratio >= max_rate:
        reasons.append("after_sales_rate_exceeds_threshold")
    if (approval_exists or publish_exists) and not account_ready:
        reasons.append("account_lost_readiness_after_commitment")
    return bool(reasons), reasons


def _batch_draft_blocked(
    *,
    lifecycle: str,
    stop_required: bool,
    publish_commitment_count: int,
    max_published: int,
) -> tuple[str, bool]:
    if lifecycle not in ACTIVE_LIFECYCLES:
        return f"lifecycle_{lifecycle}", True
    if lifecycle == "paused":
        return "trial_paused", True
    if stop_required:
        return "stop_required", True
    if publish_commitment_count >= max_published:
        return "max_publish_commitments_reached", True
    return "", False


def _compute_next_action(
    *,
    lifecycle: str,
    stop_required: bool,
    stop_reasons: List[str],
    batch_draft_blocked: bool,
    batch_block_reason: str,
    stages: List[Mapping[str, Any]],
    monitor_elapsed: bool,
) -> Dict[str, Any]:
    if lifecycle in {"stopped", "completed"}:
        return _action("trial_finished", "review_trial_report", "试运行已结束，请复盘报告")
    if stop_required:
        return _action(
            "stop_required", "decide_trial",
            "停止条件触发，需要人工决策",
            "; ".join(stop_reasons),
        )
    if lifecycle == "paused":
        return _action("trial_paused", "resume_or_stop_trial", "试运行已暂停，请选择继续或停止")
    for stage in stages:
        if stage["status"] != "pass":
            action = _NEXT_ACTION_BY_STAGE.get(str(stage.get("code") or ""), "review_trial")
            return _action(
                str(stage.get("code") or ""),
                action,
                str(stage.get("message") or ""),
                str(stage.get("evidence") or ""),
            )
    if batch_draft_blocked:
        if batch_block_reason == "max_publish_commitments_reached":
            return _action("max_publish_commitments_reached", "monitor_trial", "已达发布承诺上限，转入审批或监控阶段")
        return _action("trial_blocked", "review_trial_blocker", "批次草稿被阻断", batch_block_reason)
    if monitor_elapsed:
        return _action("monitor_elapsed", "decide_trial_after_monitor", "监控期已满，请做出运营决策")
    return _action("monitor", "monitor_trial", "全阶段已达成，进入监控期")


def _action(code: str, action: str, message: str, detail: str = "") -> Dict[str, Any]:
    return {
        "code": code,
        "action": action,
        "message": message,
        "detail": detail,
    }


def _stage(code: str, passed: bool, message: str, evidence: str, *, counts: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {
        "code": code,
        "status": "pass" if passed else "pending",
        "message": message,
        "evidence": evidence,
        "counts": dict(counts or {}),
    }


# ---------------------------------------------------------------------------
# Atomic JSON-backed store
# ---------------------------------------------------------------------------

class CommercialTrialStore:
    """Persistent store for trial business metadata.

    Only business metadata is persisted: cookie_id, category, lifecycle,
    controls, candidate_ids, and the decision history. Reconciliation output is
    computed live and never stored. Writes are atomic (tmp file + replace) and
    guarded by a re-entrant lock.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = RLock()
        self._trials: Dict[str, Dict[str, Any]] = {}
        self._load()

    def create_trial(
        self,
        *,
        cookie_id: str,
        category: str,
        controls: Optional[Mapping[str, Any]] = None,
        candidate_ids: Optional[List[str]] = None,
        launch_key: str = "",
        launch_mode: str = "",
    ) -> Dict[str, Any]:
        cookie_id = str(cookie_id or "").strip()
        category = str(category or "").strip()
        if not cookie_id:
            raise ValueError("cookie_id is required")
        if not category:
            raise ValueError("category is required")
        controls, errors = validate_controls(controls)
        if errors:
            raise ValueError("; ".join(errors))
        candidate_ids = [str(cid or "").strip() for cid in (candidate_ids or []) if str(cid or "").strip()]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_ids must be unique")
        if len(candidate_ids) > int(controls["max_candidates"]):
            raise ValueError("candidate_ids exceed max_candidates")
        launch_key = str(launch_key or "").strip()
        launch_mode = str(launch_mode or "").strip()
        if launch_key and len(launch_key) > 128:
            raise ValueError("launch_key is too long")

        now = time.time()
        trial: Dict[str, Any] = {
            "trial_id": f"ct_{uuid.uuid4().hex[:12]}",
            "cookie_id": cookie_id,
            "category": category,
            "lifecycle": "active",
            "controls": controls,
            "candidate_ids": candidate_ids,
            "traffic_observations": [],
            "experiments": [],
            "fulfillment_handoffs": [],
            "decision_history": [],
            "launch_key": launch_key,
            "launch_mode": launch_mode,
            "created_at": now,
            "started_at": _iso(now),
            "updated_at": now,
        }
        with self._lock:
            if self._active_trial_for_cookie(cookie_id) is not None:
                raise ValueError("account already has an active or paused trial")
            self._trials[trial["trial_id"]] = trial
            self._save()
        return _copy(trial)

    def get_trial(self, trial_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            trial = self._trials.get(trial_id)
            return _copy(trial) if trial else None

    def list_trials(self) -> List[Dict[str, Any]]:
        with self._lock:
            trials = sorted(
                self._trials.values(), key=lambda item: _epoch(item.get("created_at")) or 0, reverse=True
            )
            return [_copy(item) for item in trials]

    def active_trials(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                _copy(item) for item in self._trials.values()
                if str(item.get("lifecycle") or "") in ACTIVE_LIFECYCLES
            ]

    def active_trial_for_cookie(self, cookie_id: str) -> Optional[Dict[str, Any]]:
        cookie_id = str(cookie_id or "").strip()
        with self._lock:
            return _copy(self._active_trial_for_cookie(cookie_id)) if cookie_id else None

    def trial_for_launch_key(self, launch_key: str) -> Optional[Dict[str, Any]]:
        launch_key = str(launch_key or "").strip()
        if not launch_key:
            return None
        with self._lock:
            for trial in self._trials.values():
                if str(trial.get("launch_key") or "") == launch_key:
                    return _copy(trial)
        return None

    def _active_trial_for_cookie(self, cookie_id: str) -> Optional[Dict[str, Any]]:
        for trial in self._trials.values():
            if (
                str(trial.get("cookie_id") or "") == cookie_id
                and str(trial.get("lifecycle") or "") in ACTIVE_LIFECYCLES
            ):
                return trial
        return None

    def attach_candidate(self, trial_id: str, candidate_id: str) -> Dict[str, Any]:
        candidate_id = str(candidate_id or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        with self._lock:
            trial = self._require(trial_id)
            if str(trial.get("lifecycle") or "") not in ACTIVE_LIFECYCLES:
                raise ValueError("trial is not active")
            if candidate_id in trial["candidate_ids"]:
                raise ValueError("candidate already attached")
            if len(trial["candidate_ids"]) >= int(trial["controls"].get("max_candidates") or 0):
                raise ValueError("max candidates reached")
            trial["candidate_ids"].append(candidate_id)
            trial["updated_at"] = time.time()
            self._save()
        return _copy(trial)

    def record_traffic_observation(
        self,
        trial_id: str,
        *,
        item_id: str,
        candidate_id: str,
        views: int,
        wants: int,
        source: str,
        evidence_note: str = "",
        evidence_ref: str = "",
        observed_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        item_id = str(item_id or "").strip()
        candidate_id = str(candidate_id or "").strip()
        source = str(source or "").strip().lower()
        note = str(evidence_note or "").strip()
        reference = str(evidence_ref or "").strip()
        if not item_id or not candidate_id:
            raise ValueError("item_id and candidate_id are required")
        if source not in TRAFFIC_SOURCES:
            raise ValueError("source must be operator or adapter")
        if source == "operator" and len(note) < 4:
            raise ValueError("operator observation requires an evidence note")
        try:
            clean_views = int(views)
            clean_wants = int(wants)
        except (TypeError, ValueError):
            raise ValueError("views and wants must be integers")
        if clean_views < 0 or clean_wants < 0 or clean_wants > clean_views:
            raise ValueError("traffic counts must satisfy 0 <= wants <= views")
        observed = time.time() if observed_at is None else float(observed_at)
        if not math.isfinite(observed) or observed <= 0 or observed > time.time() + 300:
            raise ValueError("observed_at is invalid")

        with self._lock:
            trial = self._require(trial_id)
            if str(trial.get("lifecycle") or "") not in ACTIVE_LIFECYCLES:
                raise ValueError("finished trial observations are immutable")
            if observed < (_epoch(trial.get("created_at")) or 0):
                raise ValueError("observation predates the trial")
            observations = _sanitize_traffic_observations(trial.get("traffic_observations") or [])
            item_observations = [item for item in observations if item["item_id"] == item_id]
            if item_observations:
                latest = item_observations[-1]
                if clean_views < int(latest["views"]) or clean_wants < int(latest["wants"]):
                    raise ValueError("cumulative traffic counts cannot decrease for the same item")
                if observed < float(latest["observed_at"]):
                    raise ValueError("observed_at cannot precede the latest item observation")

            local_date = _local_date(observed)
            existing = next(
                (
                    item for item in reversed(observations)
                    if item["item_id"] == item_id
                    and item["source"] == source
                    and item["local_date"] == local_date
                ),
                None,
            )
            active_experiment = next(
                (
                    item for item in reversed(_sanitize_experiments(trial.get("experiments") or []))
                    if item["item_id"] == item_id and item["status"] == "active"
                ),
                None,
            )
            if (
                existing is not None
                and active_experiment
                and existing["observation_id"] == active_experiment["baseline_observation_id"]
                and observed > active_experiment["started_at"]
            ):
                existing = None
            now = time.time()
            if existing is None:
                record = {
                    "observation_id": f"to_{uuid.uuid4().hex[:12]}",
                    "item_id": item_id,
                    "candidate_id": candidate_id,
                    "source": source,
                    "local_date": local_date,
                    "views": clean_views,
                    "wants": clean_wants,
                    "evidence_note": note[:300],
                    "evidence_ref": reference[:300],
                    "observed_at": observed,
                    "first_observed_at": observed,
                    "recorded_at": now,
                    "updated_at": now,
                }
                observations.append(record)
            else:
                if existing["candidate_id"] != candidate_id:
                    raise ValueError("item candidate linkage cannot change")
                existing.update({
                    "views": clean_views,
                    "wants": clean_wants,
                    "evidence_note": note[:300] or existing.get("evidence_note", ""),
                    "evidence_ref": reference[:300] or existing.get("evidence_ref", ""),
                    "observed_at": observed,
                    "updated_at": now,
                })
                record = existing
            observations.sort(key=lambda item: (item["observed_at"], item["observation_id"]))
            trial["traffic_observations"] = observations
            trial["updated_at"] = now
            self._save()
        return _copy(record)

    def start_experiment(
        self,
        trial_id: str,
        *,
        item_id: str,
        candidate_id: str,
        variable: str,
        hypothesis: str,
        before_value: str,
        after_value: str,
        planned_days: int,
        baseline_inquiries: int,
        baseline_paid_orders: int,
    ) -> Dict[str, Any]:
        item_id = str(item_id or "").strip()
        candidate_id = str(candidate_id or "").strip()
        variable = str(variable or "").strip().lower()
        hypothesis = str(hypothesis or "").strip()
        before_value = str(before_value or "").strip()
        after_value = str(after_value or "").strip()
        if not item_id or not candidate_id:
            raise ValueError("item_id and candidate_id are required")
        if variable not in EXPERIMENT_VARIABLES:
            raise ValueError("unsupported experiment variable")
        if len(hypothesis) < 4 or not before_value or not after_value:
            raise ValueError("hypothesis, before_value and after_value are required")
        if before_value == after_value:
            raise ValueError("before_value and after_value must differ")
        if not 1 <= int(planned_days) <= 14:
            raise ValueError("planned_days must be between 1 and 14")

        with self._lock:
            trial = self._require(trial_id)
            if str(trial.get("lifecycle") or "") != "active":
                raise ValueError("experiment requires an active trial")
            experiments = _sanitize_experiments(trial.get("experiments") or [])
            if any(item["item_id"] == item_id and item["status"] in {"proposed", "active"} for item in experiments):
                raise ValueError("item already has a pending or active experiment")
            observations = [
                item
                for item in _sanitize_traffic_observations(trial.get("traffic_observations") or [])
                if item["item_id"] == item_id
            ]
            if not observations:
                raise ValueError("a traffic baseline is required before starting an experiment")
            baseline = observations[-1]
            if baseline["candidate_id"] != candidate_id:
                raise ValueError("item candidate linkage does not match the baseline")
            now = time.time()
            record = {
                "experiment_id": f"te_{uuid.uuid4().hex[:12]}",
                "item_id": item_id,
                "candidate_id": candidate_id,
                "variable": variable,
                "hypothesis": hypothesis[:300],
                "before_value": before_value[:300],
                "after_value": after_value[:300],
                "planned_days": int(planned_days),
                "baseline_observation_id": baseline["observation_id"],
                "baseline_views": int(baseline["views"]),
                "baseline_wants": int(baseline["wants"]),
                "baseline_inquiries": max(0, int(baseline_inquiries)),
                "baseline_paid_orders": max(0, int(baseline_paid_orders)),
                "proposed_at": now,
                "started_at": now,
                "status": "active",
                "decision": "",
                "decision_note": "",
                "ending_observation_id": "",
                "decided_at": 0.0,
                "result": {},
            }
            experiments.append(record)
            trial["experiments"] = experiments
            trial["updated_at"] = now
            self._save()
        return _copy(record)

    def propose_experiment(
        self,
        trial_id: str,
        *,
        item_id: str,
        candidate_id: str,
        variable: str,
        hypothesis: str,
        before_value: str,
        after_value: str,
        planned_days: int,
        baseline_inquiries: int,
        baseline_paid_orders: int,
    ) -> Dict[str, Any]:
        """Create an experiment proposal without claiming the item changed."""
        item_id = str(item_id or "").strip()
        candidate_id = str(candidate_id or "").strip()
        variable = str(variable or "").strip().lower()
        hypothesis = str(hypothesis or "").strip()
        before_value = str(before_value or "").strip()
        after_value = str(after_value or "").strip()
        try:
            clean_days = int(planned_days)
        except (TypeError, ValueError):
            raise ValueError("planned_days must be an integer")
        if not item_id or not candidate_id:
            raise ValueError("item_id and candidate_id are required")
        if variable not in EXPERIMENT_VARIABLES:
            raise ValueError("unsupported experiment variable")
        if len(hypothesis) < 4 or not before_value or not after_value:
            raise ValueError("hypothesis, before_value and after_value are required")
        if before_value == after_value:
            raise ValueError("before_value and after_value must differ")
        if not 1 <= clean_days <= 14:
            raise ValueError("planned_days must be between 1 and 14")
        with self._lock:
            trial = self._require(trial_id)
            if str(trial.get("lifecycle") or "") != "active":
                raise ValueError("experiment proposal requires an active trial")
            experiments = _sanitize_experiments(trial.get("experiments") or [])
            if any(
                item["item_id"] == item_id and item["status"] in {"proposed", "active"}
                for item in experiments
            ):
                raise ValueError("item already has a pending or active experiment")
            observations = [
                item
                for item in _sanitize_traffic_observations(trial.get("traffic_observations") or [])
                if item["item_id"] == item_id
            ]
            if not observations:
                raise ValueError("a traffic baseline is required before proposing an experiment")
            baseline = observations[-1]
            if baseline["candidate_id"] != candidate_id:
                raise ValueError("item candidate linkage does not match the baseline")
            now = time.time()
            record = {
                "experiment_id": f"te_{uuid.uuid4().hex[:12]}",
                "item_id": item_id,
                "candidate_id": candidate_id,
                "variable": variable,
                "hypothesis": hypothesis[:300],
                "before_value": before_value[:300],
                "after_value": after_value[:300],
                "planned_days": clean_days,
                "baseline_observation_id": baseline["observation_id"],
                "baseline_views": int(baseline["views"]),
                "baseline_wants": int(baseline["wants"]),
                "baseline_inquiries": max(0, int(baseline_inquiries)),
                "baseline_paid_orders": max(0, int(baseline_paid_orders)),
                "proposed_at": now,
                "started_at": 0.0,
                "status": "proposed",
                "work_order_id": "",
                "approval_id": "",
                "activation_mode": "",
                "activation_note": "",
                "activation_evidence_ref": "",
                "decision": "",
                "decision_note": "",
                "ending_observation_id": "",
                "decided_at": 0.0,
                "cancelled_at": 0.0,
                "cancellation_note": "",
                "result": {},
            }
            experiments.append(record)
            trial["experiments"] = experiments
            trial["updated_at"] = now
            self._save()
        return _copy(record)

    def link_experiment_execution(
        self,
        trial_id: str,
        experiment_id: str,
        *,
        work_order_id: str,
        approval_id: str,
    ) -> Dict[str, Any]:
        with self._lock:
            trial = self._require(trial_id)
            experiments = _sanitize_experiments(trial.get("experiments") or [])
            record = next((item for item in experiments if item["experiment_id"] == experiment_id), None)
            if record is None:
                raise KeyError(f"Experiment not found: {experiment_id}")
            if record["status"] != "proposed":
                raise ValueError("only proposed experiments can be linked")
            existing = (record.get("work_order_id"), record.get("approval_id"))
            requested = (str(work_order_id or "").strip(), str(approval_id or "").strip())
            if not all(requested):
                raise ValueError("work_order_id and approval_id are required")
            if any(existing) and existing != requested:
                raise ValueError("experiment execution linkage is immutable")
            record["work_order_id"], record["approval_id"] = requested
            trial["experiments"] = experiments
            trial["updated_at"] = time.time()
            self._save()
        return _copy(record)

    def activate_experiment(
        self,
        trial_id: str,
        experiment_id: str,
        *,
        mode: str,
        note: str,
        evidence_ref: str,
    ) -> Dict[str, Any]:
        mode = str(mode or "").strip().lower()
        note = str(note or "").strip()
        evidence_ref = str(evidence_ref or "").strip()
        if mode not in {"synced", "manual"}:
            raise ValueError("activation mode must be synced or manual")
        if len(note) < 4:
            raise ValueError("activation requires an evidence note")
        if mode == "manual" and len(evidence_ref) < 2:
            raise ValueError("manual activation requires an evidence reference")
        with self._lock:
            trial = self._require(trial_id)
            experiments = _sanitize_experiments(trial.get("experiments") or [])
            record = next((item for item in experiments if item["experiment_id"] == experiment_id), None)
            if record is None:
                raise KeyError(f"Experiment not found: {experiment_id}")
            if record["status"] == "active":
                return _copy(record)
            if record["status"] != "proposed":
                raise ValueError("only proposed experiments can be activated")
            now = time.time()
            record.update({
                "status": "active",
                "started_at": now,
                "activation_mode": mode,
                "activation_note": note[:500],
                "activation_evidence_ref": evidence_ref[:300],
            })
            trial["experiments"] = experiments
            trial["updated_at"] = now
            self._save()
        return _copy(record)

    def cancel_experiment(self, trial_id: str, experiment_id: str, note: str) -> Dict[str, Any]:
        note = str(note or "").strip()
        if len(note) < 4:
            raise ValueError("cancellation requires a note")
        with self._lock:
            trial = self._require(trial_id)
            experiments = _sanitize_experiments(trial.get("experiments") or [])
            record = next((item for item in experiments if item["experiment_id"] == experiment_id), None)
            if record is None:
                raise KeyError(f"Experiment not found: {experiment_id}")
            if record["status"] in {"decided", "cancelled"}:
                return _copy(record)
            if record["status"] == "active":
                raise ValueError("active experiment must be decided, not cancelled")
            now = time.time()
            record.update({
                "status": "cancelled",
                "cancelled_at": now,
                "cancellation_note": note[:500],
            })
            trial["experiments"] = experiments
            trial["updated_at"] = now
            self._save()
        return _copy(record)

    def decide_experiment(
        self,
        trial_id: str,
        experiment_id: str,
        *,
        decision: str,
        note: str,
        current_inquiries: int,
        current_paid_orders: int,
        allow_early: bool = False,
    ) -> Dict[str, Any]:
        experiment_id = str(experiment_id or "").strip()
        decision = str(decision or "").strip().lower()
        note = str(note or "").strip()
        if decision not in EXPERIMENT_DECISIONS:
            raise ValueError("unsupported experiment decision")
        if len(note) < 4:
            raise ValueError("experiment decision requires a note")
        with self._lock:
            trial = self._require(trial_id)
            experiments = _sanitize_experiments(trial.get("experiments") or [])
            record = next((item for item in experiments if item["experiment_id"] == experiment_id), None)
            if record is None:
                raise KeyError(f"Experiment not found: {experiment_id}")
            if record["status"] != "active":
                return _copy(record)
            observations = [
                item
                for item in _sanitize_traffic_observations(trial.get("traffic_observations") or [])
                if item["item_id"] == record["item_id"]
                and item["observation_id"] != record["baseline_observation_id"]
                and item["observed_at"] > record["started_at"]
            ]
            if not observations:
                raise ValueError("a post-change traffic snapshot is required before deciding")
            due_at = record["started_at"] + int(record["planned_days"]) * 86400
            now = time.time()
            if now < due_at and not allow_early:
                raise ValueError("experiment window has not elapsed")
            if now < due_at and len(note) < 8:
                raise ValueError("early decision requires a specific reason")
            ending = observations[-1]
            record.update({
                "status": "decided",
                "decision": decision,
                "decision_note": note[:500],
                "ending_observation_id": ending["observation_id"],
                "decided_at": now,
                "result": {
                    "views_delta": int(ending["views"]) - int(record["baseline_views"]),
                    "wants_delta": int(ending["wants"]) - int(record["baseline_wants"]),
                    "inquiries_delta": max(0, int(current_inquiries)) - int(record["baseline_inquiries"]),
                    "paid_orders_delta": max(0, int(current_paid_orders)) - int(record["baseline_paid_orders"]),
                },
            })
            trial["experiments"] = experiments
            trial["updated_at"] = now
            self._save()
        return _copy(record)

    def fulfillment_handoff_for_order(self, trial_id: str, order_id: str) -> Optional[Dict[str, Any]]:
        clean_order_id = str(order_id or "").strip()
        with self._lock:
            trial = self._require(trial_id)
            for handoff in _sanitize_fulfillment_handoffs(trial.get("fulfillment_handoffs") or []):
                if handoff["order_id"] == clean_order_id:
                    return _copy(handoff)
        return None

    def record_fulfillment_handoff(
        self,
        trial_id: str,
        handoff: Mapping[str, Any],
    ) -> Dict[str, Any]:
        clean = _sanitize_fulfillment_handoffs([handoff])
        if not clean:
            raise ValueError("valid fulfillment handoff is required")
        record = clean[0]
        required = (
            "order_id", "item_id", "candidate_id", "plan_id", "purchase_draft_id",
            "work_order_id", "approval_id", "lock_owner",
        )
        if any(not str(record.get(key) or "").strip() for key in required):
            raise ValueError("fulfillment handoff is missing required linkage")
        if float(record.get("total_cost") or 0) <= 0:
            raise ValueError("fulfillment handoff total_cost must be positive")
        with self._lock:
            trial = self._require(trial_id)
            handoffs = _sanitize_fulfillment_handoffs(trial.get("fulfillment_handoffs") or [])
            existing = next((item for item in handoffs if item["order_id"] == record["order_id"]), None)
            if existing is not None:
                return _copy(existing)
            handoffs.append(record)
            trial["fulfillment_handoffs"] = handoffs
            trial["updated_at"] = time.time()
            self._save()
        return _copy(record)

    def release_fulfillment_handoff(self, trial_id: str, order_id: str) -> Dict[str, Any]:
        clean_order_id = str(order_id or "").strip()
        with self._lock:
            trial = self._require(trial_id)
            handoffs = _sanitize_fulfillment_handoffs(trial.get("fulfillment_handoffs") or [])
            record = next((item for item in handoffs if item["order_id"] == clean_order_id), None)
            if record is None:
                raise KeyError(f"Fulfillment handoff not found: {clean_order_id}")
            if not record.get("released_at"):
                record["released_at"] = time.time()
                trial["fulfillment_handoffs"] = handoffs
                trial["updated_at"] = time.time()
                self._save()
        return _copy(record)

    def decide(
        self,
        trial_id: str,
        decision: str,
        note: str = "",
        *,
        allow_complete: bool = False,
        review_eligible: bool = False,
    ) -> Dict[str, Any]:
        normalized = str(decision or "").strip().lower()
        if normalized not in {"continue", "pause", "stop", "complete"}:
            raise ValueError("decision must be one of continue, pause, stop, complete")
        if normalized == "complete" and not allow_complete:
            raise ValueError("complete requires the monitor window to have elapsed or profit to be reconciled")
        lifecycle_map = {
            "continue": "active",
            "pause": "paused",
            "stop": "stopped",
            "complete": "completed",
        }
        with self._lock:
            trial = self._require(trial_id)
            if str(trial.get("lifecycle") or "") in {"stopped", "completed"}:
                raise ValueError("finished trial decisions are immutable")
            trial["decision_history"].append(
                {
                    "decision": normalized,
                    "note": str(note or "").strip()[:500],
                    "review_eligible": bool(review_eligible),
                    "decided_at": time.time(),
                }
            )
            trial["lifecycle"] = lifecycle_map[normalized]
            trial["updated_at"] = time.time()
            self._save()
        return _copy(trial)

    def _require(self, trial_id: str) -> Dict[str, Any]:
        trial = self._trials.get(trial_id)
        if not trial:
            raise KeyError(f"Trial not found: {trial_id}")
        return trial

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_trials = data.get("trials") if isinstance(data, Mapping) else data
        if not isinstance(raw_trials, list):
            return
        for item in raw_trials:
            if not isinstance(item, Mapping):
                continue
            trial_id = str(item.get("trial_id") or "").strip()
            if not trial_id:
                continue
            item = dict(item)
            controls, errors = validate_controls(item.get("controls") if isinstance(item.get("controls"), Mapping) else None)
            if errors:
                controls = default_controls()
            item["controls"] = controls
            item.setdefault("candidate_ids", [])
            item.setdefault("traffic_observations", [])
            item.setdefault("experiments", [])
            item.setdefault("fulfillment_handoffs", [])
            item.setdefault("decision_history", [])
            item.setdefault("lifecycle", "active")
            item["candidate_ids"] = _unique_ids(item.get("candidate_ids") or [])[: int(controls["max_candidates"])]
            item["traffic_observations"] = _sanitize_traffic_observations(item.get("traffic_observations") or [])
            item["experiments"] = _sanitize_experiments(item.get("experiments") or [])
            item["fulfillment_handoffs"] = _sanitize_fulfillment_handoffs(item.get("fulfillment_handoffs") or [])
            self._trials[trial_id] = item

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        trials = sorted(self._trials.values(), key=lambda item: _epoch(item.get("created_at")) or 0)
        tmp_path.write_text(
            json.dumps({"trials": trials}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)


def _checked_epoch(checked_at: Optional[str]) -> float:
    if checked_at is None:
        return time.time()
    epoch = _epoch(checked_at)
    return epoch if epoch is not None else time.time()


def _epoch(value: Any) -> Optional[float]:
    numeric = _finite_float(value)
    if numeric is not None:
        return numeric
    return _iso_to_epoch(str(value or ""))


def _unique_ids(values: Any) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    if values is None or isinstance(values, (str, bytes, Mapping)):
        return result
    try:
        iterator = iter(values)
    except TypeError:
        return result
    for value in iterator:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _iso_to_epoch(value: str) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text[-1:] in {"Z", "z"} else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).timestamp()
    return time.mktime(parsed.timetuple())


def _find_cookie_id(value: Any) -> str:
    if isinstance(value, Mapping):
        direct = value.get("cookie_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for child in value.values():
            found = _find_cookie_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_cookie_id(child)
            if found:
                return found
    return ""


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
