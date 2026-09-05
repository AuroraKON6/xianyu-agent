from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional


PENDING_REPLY_DRAFT_STATUSES = {"created", "retry_created"}
ACTIVE_EXECUTION_STATUSES = {"queued", "running"}
SENSITIVE_KEYS = {
    "buyer_id",
    "chat_id",
    "cookie",
    "cookies",
    "cookie_value",
    "raw_cookie",
    "authorization",
    "token",
    "api_key",
    "secret",
}


def build_account_ops_queues(
    *,
    account_profiles: List[Dict[str, Any]],
    prompt_profile_provider: Callable[[str], Dict[str, Any]],
    work_order_manager: Any = None,
    execution_queue: Any = None,
    message_intake_ledger: Any = None,
    adapter_health: Optional[Dict[str, Any]] = None,
    account_runtime_states: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Group daily operator queues by account without exposing raw buyer data."""
    sync_capabilities = dict((adapter_health or {}).get("sync_capabilities") or {})
    runtime_states = account_runtime_states or {}
    accounts = [
        _new_account_queue(profile, prompt_profile_provider, sync_capabilities, runtime_states)
        for profile in account_profiles
    ]
    by_cookie = {item["cookie_id"]: item for item in accounts}
    unassigned = _new_bucket("unassigned", enabled=True, sync_capabilities=sync_capabilities)

    work_orders = _list_from_manager(work_order_manager, "list_work_orders", limit=500)
    approval_to_cookie: Dict[str, str] = {}
    work_order_to_cookie: Dict[str, str] = {}

    for work_order in work_orders:
        if not isinstance(work_order, Mapping):
            continue
        work_order_id = str(work_order.get("work_order_id") or "")
        work_cookie = _find_cookie_id(work_order.get("context"), work_order)
        if work_order_id and work_cookie:
            work_order_to_cookie[work_order_id] = work_cookie
        for approval in _iter_approvals(work_order):
            approval_id = str(approval.get("approval_id") or "")
            cookie_id = _find_cookie_id(approval, work_order.get("context"), work_order) or work_cookie
            if approval_id and cookie_id:
                approval_to_cookie[approval_id] = cookie_id
            bucket = _bucket_for_cookie(by_cookie, unassigned, cookie_id)
            if approval.get("status") == "pending":
                bucket["queues"]["pending_approvals"] += 1
                _append_recent(bucket, "approvals", _approval_summary(approval, work_order))

    jobs = _list_from_manager(execution_queue, "list_jobs", limit=500)
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        cookie_id = (
            _find_cookie_id(job)
            or approval_to_cookie.get(str(job.get("approval_id") or ""))
            or work_order_to_cookie.get(str(job.get("work_order_id") or ""))
        )
        bucket = _bucket_for_cookie(by_cookie, unassigned, cookie_id)
        status = str(job.get("status") or "")
        if status in ACTIVE_EXECUTION_STATUSES:
            bucket["queues"]["queued_execution"] += 1
            _append_recent(bucket, "execution_jobs", _job_summary(job))
        elif status == "verification_required":
            bucket["queues"]["verification_required"] += 1
            _append_recent(bucket, "execution_jobs", _job_summary(job))
        elif status == "failed":
            bucket["queues"]["failed_jobs"] += 1
            _append_recent(bucket, "execution_jobs", _job_summary(job))

    intakes = _list_from_manager(message_intake_ledger, "list_recent", limit=200)
    for record in intakes:
        if not isinstance(record, Mapping):
            continue
        cookie_id = (
            _find_cookie_id(record)
            or approval_to_cookie.get(str(record.get("approval_id") or ""))
            or work_order_to_cookie.get(str(record.get("work_order_id") or ""))
        )
        bucket = _bucket_for_cookie(by_cookie, unassigned, cookie_id)
        if str(record.get("status") or "") in PENDING_REPLY_DRAFT_STATUSES:
            bucket["queues"]["pending_reply_drafts"] += 1
            _append_recent(bucket, "message_intakes", _intake_summary(record))

    for account in accounts:
        account["next_actions"] = _next_actions(account)
    unassigned["next_actions"] = _next_actions(unassigned)

    return {
        "accounts": accounts,
        "unassigned": unassigned,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _new_account_queue(
    profile: Dict[str, Any],
    prompt_profile_provider: Callable[[str], Dict[str, Any]],
    sync_capabilities: Dict[str, Any],
    account_runtime_states: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    cookie_id = str(profile.get("cookie_id") or "")
    counts = dict(profile.get("counts") or {})
    prompt_profile = _safe_prompt_profile(_call_prompt_profile(prompt_profile_provider, cookie_id))
    bucket = _new_bucket(cookie_id, enabled=bool(profile.get("enabled", True)), sync_capabilities=sync_capabilities)
    bucket.update(
        {
            "remark": str(profile.get("remark") or ""),
            "capabilities": _safe_dict(profile.get("capabilities") or {}),
            "profile_recommendations": list(profile.get("recommended_next_actions") or []),
            "prompt_profile": prompt_profile,
            "sync_freshness": _safe_dict(profile.get("sync_freshness") or {}),
            "account_runtime": _safe_runtime_state(account_runtime_states.get(cookie_id), cookie_id),
        }
    )
    bucket["queues"]["items"] = int(counts.get("items") or 0)
    bucket["queues"]["orders_total"] = int(counts.get("orders_total") or 0)
    return bucket


def _new_bucket(cookie_id: str, *, enabled: bool, sync_capabilities: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cookie_id": cookie_id,
        "enabled": enabled,
        "remark": "",
        "queues": {
            "pending_approvals": 0,
            "queued_execution": 0,
            "verification_required": 0,
            "failed_jobs": 0,
            "pending_reply_drafts": 0,
            "items": 0,
            "orders_total": 0,
        },
        "prompt_profile": {"enabled": False, "ready": False, "missing_required_keys": []},
        "sync_capabilities": dict(sync_capabilities),
        "sync_freshness": {},
        "account_runtime": _safe_runtime_state(None, cookie_id),
        "next_actions": [],
        "recent": {
            "approvals": [],
            "execution_jobs": [],
            "message_intakes": [],
        },
    }


def _list_from_manager(manager: Any, method_name: str, *, limit: int) -> List[Dict[str, Any]]:
    if manager is None:
        return []
    method = getattr(manager, method_name, None)
    if not callable(method):
        return []
    try:
        records = method(limit=limit)
    except TypeError:
        records = method()
    return list(records or [])


def _iter_approvals(work_order: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    approvals = work_order.get("approvals") or []
    for approval in approvals if isinstance(approvals, list) else []:
        if isinstance(approval, Mapping):
            yield approval


def _bucket_for_cookie(
    by_cookie: Dict[str, Dict[str, Any]],
    unassigned: Dict[str, Any],
    cookie_id: Optional[str],
) -> Dict[str, Any]:
    if cookie_id and cookie_id in by_cookie:
        return by_cookie[cookie_id]
    return unassigned


def _find_cookie_id(*values: Any) -> str:
    for value in values:
        found = _find_cookie_id_in_value(value)
        if found:
            return found
    return ""


def _find_cookie_id_in_value(value: Any) -> str:
    if isinstance(value, Mapping):
        direct = value.get("cookie_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for child in value.values():
            found = _find_cookie_id_in_value(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_cookie_id_in_value(child)
            if found:
                return found
    return ""


def _call_prompt_profile(provider: Callable[[str], Dict[str, Any]], cookie_id: str) -> Dict[str, Any]:
    try:
        return dict(provider(cookie_id) or {})
    except Exception:
        return {"enabled": False, "ready": False, "missing_required_keys": ["profile_error"]}


def _safe_prompt_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    keys = {
        "enabled",
        "ready",
        "model_name",
        "required_keys",
        "optional_keys",
        "supported_keys",
        "configured_keys",
        "configured_required_keys",
        "missing_required_keys",
        "unknown_keys",
        "prompt_lengths",
        "limits",
        "recommended_next_actions",
    }
    return {key: _sanitize_value(value) for key, value in profile.items() if key in keys}


def _approval_summary(approval: Mapping[str, Any], work_order: Mapping[str, Any]) -> Dict[str, Any]:
    snapshot = approval.get("draft_snapshot") if isinstance(approval.get("draft_snapshot"), Mapping) else {}
    return {
        "approval_id": str(approval.get("approval_id") or ""),
        "work_order_id": str(approval.get("work_order_id") or work_order.get("work_order_id") or ""),
        "action": str(approval.get("action") or ""),
        "status": str(approval.get("status") or ""),
        "summary": _summary_text(
            snapshot.get("message_summary"),
            snapshot.get("title"),
            snapshot.get("reason_summary"),
            work_order.get("title"),
        ),
    }


def _job_summary(job: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": str(job.get("job_id") or ""),
        "approval_id": str(job.get("approval_id") or ""),
        "work_order_id": str(job.get("work_order_id") or ""),
        "action": str(job.get("action") or ""),
        "status": str(job.get("status") or ""),
        "error": _summary_text(job.get("error")),
    }


def _intake_summary(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "intake_id": str(record.get("intake_id") or ""),
        "work_order_id": str(record.get("work_order_id") or ""),
        "approval_id": str(record.get("approval_id") or ""),
        "status": str(record.get("status") or ""),
        "incoming_message_summary": _summary_text(record.get("incoming_message_summary")),
        "reply_summary": _summary_text(record.get("reply_summary")),
        "risk_flags": list(record.get("risk_flags") or []),
    }


def _append_recent(bucket: Dict[str, Any], key: str, item: Dict[str, Any], *, limit: int = 5) -> None:
    recent = bucket.setdefault("recent", {}).setdefault(key, [])
    recent.append(_sanitize_value(item))
    del recent[limit:]


def _next_actions(bucket: Dict[str, Any]) -> List[Dict[str, Any]]:
    queues = bucket.get("queues") or {}
    actions: List[Dict[str, Any]] = []
    runtime = bucket.get("account_runtime") or {}
    runtime_status = str(runtime.get("connection_status") or "unknown")
    runtime_blocked = bool(runtime.get("pending_verification_reason")) or runtime_status in {"error", "manual_verification"}
    runtime_unfresh = runtime.get("token_fresh") is False or runtime.get("cookie_fresh") is False
    if runtime_blocked:
        actions.append(
            {
                "kind": "runtime_blocked",
                "priority": "high",
                "title": "Resolve account runtime blocker",
                "detail": str(runtime.get("pending_verification_reason") or runtime.get("last_error") or runtime_status),
            }
        )
    elif runtime_status not in {"connected", "degraded"}:
        actions.append(
            {
                "kind": "runtime_connect",
                "priority": "high",
                "title": "Connect account runtime",
                "detail": "Start or confirm the account runtime before the live loop.",
            }
        )
    elif runtime_unfresh:
        actions.append(
            {
                "kind": "runtime_refresh",
                "priority": "medium",
                "title": "Refresh token/cookie state",
                "detail": "Runtime is connected but token or cookie freshness is not confirmed.",
            }
        )
    if not bucket.get("enabled", True):
        actions.append(
            {
                "kind": "account_disabled",
                "priority": "high",
                "title": "账号未启用",
                "detail": "先启用账号或确认它只作为备用账号。",
            }
        )
    if queues.get("verification_required", 0):
        actions.append(
            {
                "kind": "verification_required",
                "priority": "high",
                "title": "核对平台实际结果",
                "detail": "先在闲鱼确认动作是否发生，再记录已执行或未执行；不要直接重试。",
                "count": int(queues.get("verification_required") or 0),
            }
        )
    if queues.get("failed_jobs", 0):
        actions.append(
            {
                "kind": "failed_jobs",
                "priority": "high",
                "title": "处理失败执行任务",
                "detail": "查看失败原因后重试或取消。",
                "count": int(queues.get("failed_jobs") or 0),
            }
        )
    if queues.get("pending_approvals", 0):
        actions.append(
            {
                "kind": "pending_approvals",
                "priority": "high",
                "title": "审批待执行草稿",
                "detail": "先处理发布、发消息、退款等会触达平台的动作。",
                "count": int(queues.get("pending_approvals") or 0),
            }
        )
    if queues.get("pending_reply_drafts", 0):
        actions.append(
            {
                "kind": "pending_reply_drafts",
                "priority": "medium",
                "title": "检查买家回复草稿",
                "detail": "确认话术、价格和商品信息后再放入执行队列。",
                "count": int(queues.get("pending_reply_drafts") or 0),
            }
        )
    if queues.get("queued_execution", 0):
        actions.append(
            {
                "kind": "queued_execution",
                "priority": "medium",
                "title": "运行执行队列",
                "detail": "队列里已有审批通过的动作等待执行。",
                "count": int(queues.get("queued_execution") or 0),
            }
        )

    prompt = bucket.get("prompt_profile") or {}
    if not prompt.get("ready"):
        missing = ", ".join(str(item) for item in prompt.get("missing_required_keys") or [])
        actions.append(
            {
                "kind": "prompt_profile",
                "priority": "medium",
                "title": "补齐账号 Prompt",
                "detail": f"缺少必需配置: {missing}" if missing else "AI 回复配置未就绪。",
            }
        )

    sync = bucket.get("sync_capabilities") or {}
    if sync.get("listings") and not queues.get("items", 0):
        actions.append(
            {
                "kind": "sync_listings",
                "priority": "low",
                "title": "同步商品库存",
                "detail": "让账号队列拥有已发布商品上下文。",
            }
        )
    freshness = bucket.get("sync_freshness") or {}
    stale_resources = [
        resource
        for resource, state in freshness.items()
        if isinstance(state, Mapping) and not state.get("fresh")
    ]
    if stale_resources:
        actions.append(
            {
                "kind": "sync_stale",
                "priority": "medium",
                "title": "Sync account data",
                "detail": "Refresh stale resources: " + ", ".join(sorted(str(item) for item in stale_resources)),
            }
        )
    if not actions:
        actions.append(
            {
                "kind": "account_ready",
                "priority": "low",
                "title": "账号队列正常",
                "detail": "保持小批量验证和日复盘节奏。",
            }
        )
    return actions[:6]


def _summary_text(*values: Any, max_len: int = 120) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text[:max_len]
    return ""


def _safe_dict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _sanitize_value(val) for key, val in value.items() if str(key).lower() not in SENSITIVE_KEYS}


def _safe_runtime_state(value: Any, cookie_id: str) -> Dict[str, Any]:
    allowed = {
        "cookie_id",
        "connection_status",
        "last_heartbeat_at",
        "last_message_at",
        "last_auth_verified_at",
        "last_token_refresh_at",
        "last_cookie_refresh_at",
        "token_fresh",
        "cookie_fresh",
        "reconnect_count",
        "last_error",
        "manual_takeover_count",
        "manual_takeover_chats",
        "pending_verification_reason",
        "updated_at",
    }
    if not isinstance(value, Mapping):
        value = {}
    state = {key: _sanitize_value(val) for key, val in value.items() if key in allowed}
    state.setdefault("cookie_id", cookie_id)
    state.setdefault("connection_status", "unknown")
    state.setdefault("last_heartbeat_at", 0)
    state.setdefault("last_message_at", 0)
    state.setdefault("last_auth_verified_at", 0)
    state.setdefault("last_token_refresh_at", 0)
    state.setdefault("last_cookie_refresh_at", 0)
    state.setdefault("token_fresh", False)
    state.setdefault("cookie_fresh", False)
    state.setdefault("reconnect_count", 0)
    state.setdefault("last_error", "")
    state.setdefault("manual_takeover_count", 0)
    state.setdefault("manual_takeover_chats", [])
    state.setdefault("pending_verification_reason", "")
    state.setdefault("updated_at", 0)
    return state


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_value(val)
            for key, val in value.items()
            if str(key).lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value
