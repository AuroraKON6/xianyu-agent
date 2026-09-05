from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .action_scope import build_action_scope_envelope
from .work_orders import RISKY_ACTIONS


BLOCKING_JOB_STATUSES = {"running", "completed"}
QUARANTINE_JOB_STATUSES = {"queued", "failed"}


def resolve_account_cookie_id(
    *,
    account_ids: Iterable[str],
    requested: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve a task's account id without exposing cookie values."""
    account_id_list = [str(item) for item in account_ids if str(item or "").strip()]
    account_ids_set = set(account_id_list)
    requested = str(requested or "").strip()
    context_cookie = _find_cookie_id(context or {})

    for candidate in (requested, context_cookie):
        candidate = str(candidate or "").strip()
        if not candidate:
            continue
        if candidate != "default" or candidate in account_ids_set:
            return candidate

    if len(account_id_list) == 1:
        return account_id_list[0]
    return requested or context_cookie


def review_unassigned_work(
    *,
    work_order_manager: Any,
    execution_queue: Any = None,
    message_intake_ledger: Any = None,
    account_ids: Optional[Iterable[str]] = None,
    limit: int = 500,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a sanitized, versioned review of work that has no seller account."""
    now_ts = time.time() if now is None else float(now)
    safe_limit = max(1, min(int(limit or 500), 2000))
    work_orders = _list_from_manager(work_order_manager, "list_work_orders", safe_limit)
    jobs = _list_from_manager(execution_queue, "list_jobs", safe_limit)
    intakes = _list_from_manager(message_intake_ledger, "list_recent", safe_limit)
    configured_accounts = None if account_ids is None else {
        str(item or "").strip() for item in account_ids if str(item or "").strip()
    }

    jobs_by_work_order: Dict[str, List[Mapping[str, Any]]] = {}
    for job in jobs:
        work_order_id = str(job.get("work_order_id") or "")
        if work_order_id:
            jobs_by_work_order.setdefault(work_order_id, []).append(job)
    intakes_by_work_order: Dict[str, List[Mapping[str, Any]]] = {}
    for record in intakes:
        work_order_id = str(record.get("work_order_id") or "")
        if work_order_id:
            intakes_by_work_order.setdefault(work_order_id, []).append(record)

    items: List[Dict[str, Any]] = []
    token_rows: List[Dict[str, Any]] = []
    for work_order in work_orders:
        if not isinstance(work_order, Mapping):
            continue
        source_cookie_ids = sorted(_find_cookie_ids(work_order))
        if configured_accounts is None:
            if source_cookie_ids:
                continue
        elif len(source_cookie_ids) == 1 and source_cookie_ids[0] in configured_accounts:
            continue
        context = work_order.get("context") if isinstance(work_order.get("context"), Mapping) else {}
        if str(context.get("account_attribution_state") or "") == "quarantined":
            continue
        work_order_id = str(work_order.get("work_order_id") or "")
        if not work_order_id:
            continue

        approvals = [item for item in work_order.get("approvals") or [] if isinstance(item, Mapping)]
        related_jobs = jobs_by_work_order.get(work_order_id, [])
        related_intakes = intakes_by_work_order.get(work_order_id, [])
        actions = sorted({str(item.get("action") or "") for item in approvals if str(item.get("action") or "")})
        approval_execution_statuses = sorted(
            {str(item.get("execution_status") or "") for item in approvals if str(item.get("execution_status") or "")}
        )
        job_statuses = sorted({str(item.get("status") or "") for item in related_jobs if str(item.get("status") or "")})
        intake_statuses = sorted(
            {str(item.get("status") or "") for item in related_intakes if str(item.get("status") or "")}
        )
        blocked_reasons = []
        if len(source_cookie_ids) > 1:
            blocked_reasons.append("mixed_account_attribution")
        if any(status in BLOCKING_JOB_STATUSES for status in job_statuses):
            blocked_reasons.append("execution_job_already_running_or_completed")
        if any(status in BLOCKING_JOB_STATUSES for status in approval_execution_statuses):
            blocked_reasons.append("approval_execution_already_running_or_completed")

        created_at = float(work_order.get("created_at") or 0)
        age_hours = max(0, int((now_ts - created_at) // 3600)) if created_at else 0
        has_platform_action = bool(set(actions) & RISKY_ACTIONS)
        if blocked_reasons:
            recommendation = "manual_audit"
        elif has_platform_action or age_hours >= 24:
            recommendation = "quarantine"
        else:
            recommendation = "claim"

        item = {
            "work_order_id": work_order_id,
            "kind": str(work_order.get("kind") or ""),
            "owner": str(work_order.get("owner") or ""),
            "status": str(work_order.get("status") or ""),
            "risk_level": str(work_order.get("risk_level") or ""),
            "created_at": created_at,
            "age_hours": age_hours,
            "actions": actions,
            "attribution_issue": "missing_cookie_id" if not source_cookie_ids else "unknown_cookie_id",
            "source_cookie_ids": source_cookie_ids,
            "pending_approvals": sum(1 for approval in approvals if approval.get("status") == "pending"),
            "approved_approvals": sum(1 for approval in approvals if approval.get("status") == "approved"),
            "job_statuses": job_statuses,
            "pending_intakes": sum(
                1 for record in related_intakes if str(record.get("status") or "") in {"created", "retry_created"}
            ),
            "recommended_resolution": recommendation,
            "resolution_allowed": not blocked_reasons,
            "blocked_reasons": blocked_reasons,
        }
        items.append(item)
        token_rows.append(
            {
                "work_order_id": work_order_id,
                "source_cookie_ids": source_cookie_ids,
                "updated_at": float(work_order.get("updated_at") or 0),
                "status": str(work_order.get("status") or ""),
                "approvals": sorted(
                    [
                        (
                            str(approval.get("approval_id") or ""),
                            str(approval.get("status") or ""),
                            str(approval.get("execution_status") or ""),
                        )
                        for approval in approvals
                    ]
                ),
                "jobs": sorted(
                    [
                        (str(job.get("job_id") or ""), str(job.get("status") or ""), float(job.get("updated_at") or 0))
                        for job in related_jobs
                    ]
                ),
                "intakes": sorted(
                    [
                        (
                            str(record.get("intake_id") or ""),
                            str(record.get("status") or ""),
                            float(record.get("updated_at") or 0),
                        )
                        for record in related_intakes
                    ]
                ),
            }
        )

    items.sort(key=lambda item: (float(item.get("created_at") or 0), item.get("work_order_id") or ""))
    token_rows.sort(key=lambda item: item["work_order_id"])
    preview_token = hashlib.sha256(
        json.dumps(token_rows, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "count": len(items),
        "preview_token": preview_token,
        "items": items,
        "summary": {
            "claim": sum(1 for item in items if item["recommended_resolution"] == "claim"),
            "quarantine": sum(1 for item in items if item["recommended_resolution"] == "quarantine"),
            "manual_audit": sum(1 for item in items if item["recommended_resolution"] == "manual_audit"),
        },
    }


def repair_work_order_account_attribution(
    *,
    work_order_manager: Any,
    account_ids: Iterable[str],
    execution_queue: Any = None,
    message_intake_ledger: Any = None,
    target_cookie_id: str = "",
    dry_run: bool = True,
    limit: int = 500,
    mode: str = "claim",
    work_order_ids: Optional[Iterable[str]] = None,
    preview_token: str = "",
    confirmation: str = "",
) -> Dict[str, Any]:
    """Review or explicitly resolve selected unassigned WorkOrders."""
    account_id_list = [str(item) for item in account_ids if str(item or "").strip()]
    target = _resolve_repair_target(account_id_list, target_cookie_id)
    review = review_unassigned_work(
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
        account_ids=account_id_list,
        limit=limit,
    )
    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "mode": str(mode or "claim").strip().lower(),
        "target_cookie_id": target,
        "planned_updates": review["count"],
        "updated": 0,
        "claimed": 0,
        "quarantined": 0,
        "cancelled_jobs": 0,
        "quarantined_intakes": 0,
        "work_order_ids": [],
        "preview_token": review["preview_token"],
        "items": review["items"],
        "summary": review["summary"],
        "skipped_reason": "",
    }
    if dry_run:
        return result

    normalized_mode = result["mode"]
    if normalized_mode not in {"claim", "quarantine"}:
        return _skip(result, "invalid_resolution_mode")
    if normalized_mode == "claim":
        if not target:
            return _skip(result, "target_account_required")
        if target not in set(account_id_list):
            return _skip(result, "target_account_not_configured")

    selected_ids = list(dict.fromkeys(str(item or "").strip() for item in work_order_ids or [] if str(item or "").strip()))
    if not selected_ids:
        return _skip(result, "work_order_ids_required")
    if not str(preview_token or "").strip():
        return _skip(result, "preview_token_required")
    if str(preview_token).strip() != review["preview_token"]:
        return _skip(result, "stale_preview_token")

    review_by_id = {item["work_order_id"]: item for item in review["items"]}
    if any(work_order_id not in review_by_id for work_order_id in selected_ids):
        return _skip(result, "unknown_or_resolved_work_order")
    blocked = [item for item in (review_by_id[work_order_id] for work_order_id in selected_ids) if not item["resolution_allowed"]]
    if blocked:
        result["blocked_work_order_ids"] = [item["work_order_id"] for item in blocked]
        return _skip(result, "work_order_resolution_blocked")
    if normalized_mode == "claim":
        existing_jobs = [
            item
            for item in (review_by_id[work_order_id] for work_order_id in selected_ids)
            if item.get("job_statuses")
        ]
        if existing_jobs:
            result["blocked_work_order_ids"] = [item["work_order_id"] for item in existing_jobs]
            return _skip(result, "existing_execution_job_requires_quarantine_or_recreation")

    expected_confirmation = f"{normalized_mode.upper()} {len(selected_ids)}"
    if str(confirmation or "").strip() != expected_confirmation:
        result["expected_confirmation"] = expected_confirmation
        return _skip(result, "confirmation_mismatch")

    cancelled_jobs = 0
    quarantined_intakes = 0
    if normalized_mode == "quarantine":
        if execution_queue is not None and hasattr(execution_queue, "quarantine_jobs_for_work_orders"):
            job_result = execution_queue.quarantine_jobs_for_work_orders(selected_ids)
            if not job_result.get("ok"):
                result["blocked_job_ids"] = list(job_result.get("blocked_job_ids") or [])
                return _skip(result, "work_order_resolution_blocked")
            cancelled_jobs = int(job_result.get("cancelled") or 0)
        if message_intake_ledger is not None and hasattr(message_intake_ledger, "quarantine_for_work_orders"):
            intake_result = message_intake_ledger.quarantine_for_work_orders(selected_ids)
            quarantined_intakes = int(intake_result.get("quarantined") or 0)

    updated = 0
    for work_order_id in selected_ids:
        if normalized_mode == "claim":
            changed = _apply_work_order_cookie_id(work_order_manager, work_order_id, target)
        else:
            changed = _quarantine_work_order(work_order_manager, work_order_id)
        if changed:
            updated += 1
    if updated and hasattr(work_order_manager, "_save"):
        work_order_manager._save()

    result.update(
        {
            "updated": updated,
            "claimed": updated if normalized_mode == "claim" else 0,
            "quarantined": updated if normalized_mode == "quarantine" else 0,
            "cancelled_jobs": cancelled_jobs,
            "quarantined_intakes": quarantined_intakes,
            "work_order_ids": selected_ids,
        }
    )
    return result


def _skip(result: Dict[str, Any], reason: str) -> Dict[str, Any]:
    result["ok"] = False
    result["skipped_reason"] = reason
    return result


def _list_from_manager(manager: Any, method_name: str, limit: int) -> List[Mapping[str, Any]]:
    if manager is None:
        return []
    method = getattr(manager, method_name, None)
    if not callable(method):
        return []
    try:
        values = method(limit=limit)
    except TypeError:
        values = method()
    return [item for item in values or [] if isinstance(item, Mapping)]


def _resolve_repair_target(account_ids: List[str], target_cookie_id: str) -> str:
    target = str(target_cookie_id or "").strip()
    if target:
        return target
    if len(account_ids) == 1:
        return account_ids[0]
    return ""


def _apply_work_order_cookie_id(work_order_manager: Any, work_order_id: str, cookie_id: str) -> bool:
    work_orders = getattr(work_order_manager, "_work_orders", None)
    if not isinstance(work_orders, dict) or work_order_id not in work_orders:
        return False
    work_order = work_orders[work_order_id]

    now = time.time()
    _replace_cookie_id_values(work_order, cookie_id)
    context = work_order.setdefault("context", {})
    if isinstance(context, dict):
        context["cookie_id"] = cookie_id
        context["account_attribution_state"] = "claimed"
        context["account_attribution_resolved_at"] = now
    approval_reset = False
    for approval in work_order.get("approvals") or []:
        if not isinstance(approval, dict):
            continue
        payload = approval.setdefault("payload", {})
        if isinstance(payload, dict):
            payload.setdefault("cookie_id", cookie_id)
        draft_snapshot = approval.setdefault("draft_snapshot", {})
        if isinstance(draft_snapshot, dict):
            draft_snapshot.setdefault("cookie_id", cookie_id)
        if approval.get("status") in {"pending", "approved"}:
            approval_reset = True
            approval["status"] = "pending"
            approval["requested_at"] = now
            approval["decided_at"] = None
            approval["decision_note"] = ""
            approval["approved_action_digest"] = ""
            approval["execution_status"] = None
            approval["execution_result"] = None
            approval["scope_envelope"] = build_action_scope_envelope(
                approval_id=str(approval.get("approval_id") or ""),
                work_order_id=work_order_id,
                action=str(approval.get("action") or ""),
                payload=payload if isinstance(payload, dict) else {},
                draft_snapshot=draft_snapshot if isinstance(draft_snapshot, dict) else {},
                context=context if isinstance(context, dict) else {},
                requested_at=now,
            )
    if approval_reset:
        action_plan = work_order.get("action_plan")
        if isinstance(action_plan, dict):
            action_plan["status"] = "needs_approval"
        if hasattr(work_order_manager, "_refresh_status_from_approvals"):
            work_order_manager._refresh_status_from_approvals(work_order)
    events = work_order.setdefault("events", [])
    if isinstance(events, list):
        events.append(
            {
                "type": "account_attribution_claimed",
                "created_at": now,
                "cookie_id": cookie_id,
            }
        )
    if hasattr(work_order_manager, "_touch"):
        work_order_manager._touch(work_order)
    else:
        work_order["updated_at"] = now
    return True


def _quarantine_work_order(work_order_manager: Any, work_order_id: str) -> bool:
    work_orders = getattr(work_order_manager, "_work_orders", None)
    if not isinstance(work_orders, dict) or work_order_id not in work_orders:
        return False
    work_order = work_orders[work_order_id]
    context = work_order.setdefault("context", {})
    if isinstance(context, dict) and context.get("account_attribution_state") == "quarantined":
        return False

    now = time.time()
    if isinstance(context, dict):
        context["account_attribution_state"] = "quarantined"
        context["quarantine_reason"] = "unassigned_before_first_live_account"
        context["account_attribution_resolved_at"] = now
    for approval in work_order.get("approvals") or []:
        if not isinstance(approval, dict) or approval.get("status") not in {"pending", "approved"}:
            continue
        approval["status"] = "rejected"
        approval["decision_note"] = "Quarantined before first live account; explicit recreation required."
        approval["decided_at"] = now
    action_plan = work_order.get("action_plan")
    if isinstance(action_plan, dict):
        action_plan["status"] = "quarantined"
    work_order["status"] = "quarantined"
    events = work_order.setdefault("events", [])
    if isinstance(events, list):
        events.append(
            {
                "type": "unassigned_work_quarantined",
                "created_at": now,
                "reason": "unassigned_before_first_live_account",
            }
        )
    if hasattr(work_order_manager, "_touch"):
        work_order_manager._touch(work_order)
    else:
        work_order["updated_at"] = now
    return True


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


def _find_cookie_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        direct = value.get("cookie_id")
        if isinstance(direct, str) and direct.strip():
            found.add(direct.strip())
        for child in value.values():
            found.update(_find_cookie_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_find_cookie_ids(child))
    return found


def _replace_cookie_id_values(value: Any, cookie_id: str) -> None:
    if isinstance(value, dict):
        if "cookie_id" in value:
            value["cookie_id"] = cookie_id
        for child in value.values():
            _replace_cookie_id_values(child, cookie_id)
    elif isinstance(value, list):
        for child in value:
            _replace_cookie_id_values(child, cookie_id)
