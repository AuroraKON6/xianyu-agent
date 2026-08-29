from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional


RUNTIME_HEARTBEAT_MAX_AGE_SECONDS = 300.0
NON_LIVE_ADAPTERS = {"", "unknown", "stub"}


def build_first_run_readiness(
    *,
    account_profiles: List[Dict[str, Any]],
    account_queues: Dict[str, Any],
    adapter_health: Dict[str, Any],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Build evidence-based gates for the first controlled real-account run.

    Account-scoped evidence must come from one account. This prevents a prompt-
    ready account and a different runtime-ready account from being combined into
    a false positive.
    """
    now_ts = time.time() if now is None else float(now)
    profiles = [profile for profile in account_profiles or [] if isinstance(profile, Mapping)]
    queues = [queue for queue in (account_queues or {}).get("accounts", []) if isinstance(queue, Mapping)]
    unassigned_queues = ((account_queues or {}).get("unassigned") or {}).get("queues") or {}
    sync_capabilities = dict((adapter_health or {}).get("sync_capabilities") or {})

    profiles_by_id = {
        str(profile.get("cookie_id") or "").strip(): profile
        for profile in profiles
        if str(profile.get("cookie_id") or "").strip()
    }
    queues_by_id = {
        str(queue.get("cookie_id") or "").strip(): queue
        for queue in queues
        if str(queue.get("cookie_id") or "").strip()
    }
    account_ids = sorted(set(profiles_by_id) | set(queues_by_id))
    account_readiness = [
        _build_account_readiness(
            cookie_id,
            profile=profiles_by_id.get(cookie_id, {}),
            queue=queues_by_id.get(cookie_id, {}),
            now=now_ts,
        )
        for cookie_id in account_ids
    ]
    account_readiness.sort(
        key=lambda row: (
            bool(row.get("ready")),
            int(row.get("completed_prefix_count") or 0),
            int(row.get("passed_count") or 0),
            row.get("cookie_id") or "",
        ),
        reverse=True,
    )
    selected = account_readiness[0] if account_readiness else None
    selected_gates = {
        gate["id"]: bool(gate["passed"])
        for gate in (selected or {}).get("gates", [])
    }

    has_account = bool(account_readiness)
    adapter_name = str((adapter_health or {}).get("adapter") or "").strip().lower()
    adapter_ready = bool((adapter_health or {}).get("ready")) and adapter_name not in NON_LIVE_ADAPTERS
    sync_ready = bool(sync_capabilities.get("orders") and sync_capabilities.get("listings"))
    no_unassigned_work = not any(
        int(unassigned_queues.get(key) or 0) > 0
        for key in ("pending_approvals", "queued_execution", "failed_jobs", "pending_reply_drafts")
    )

    gates = [
        _gate(
            "account_configured",
            has_account,
            "Add the first seller account",
            "Import or confirm at least one Xianyu seller account before the real run.",
        ),
        _gate(
            "account_enabled",
            selected_gates.get("account_enabled", False),
            "Enable the selected seller account",
            "The account selected for the trial must be enabled for operations.",
        ),
        _gate(
            "adapter_ready",
            adapter_ready,
            "Confirm a live execution adapter",
            "A real trial requires a configured live adapter; the local stub is simulation only.",
        ),
        _gate(
            "sync_capabilities",
            sync_ready,
            "Confirm sync capabilities",
            "The active adapter must expose both order and listing sync methods.",
        ),
        _gate(
            "prompt_ready",
            selected_gates.get("prompt_ready", False),
            "Complete the selected account Prompt profile",
            "The same enabled account used for the trial must have all required Prompt keys.",
        ),
        _gate(
            "inventory_context",
            selected_gates.get("inventory_context", False),
            "Sync or create listing inventory",
            "The selected account needs current listing context for decisions and replies.",
        ),
        _gate(
            "sync_current",
            selected_gates.get("sync_current", False),
            "Run complete account sync",
            "Both listing and order snapshots must be fresh for the selected account.",
        ),
        _gate(
            "runtime_connected",
            selected_gates.get("runtime_connected", False),
            "Connect the selected account runtime",
            "The selected account must have a live or degraded-but-working runtime connection.",
        ),
        _gate(
            "runtime_fresh",
            selected_gates.get("runtime_fresh", False),
            "Refresh account runtime evidence",
            f"Heartbeat and authenticated credential evidence must be no older than {int(RUNTIME_HEARTBEAT_MAX_AGE_SECONDS)} seconds.",
        ),
        _gate(
            "runtime_unblocked",
            selected_gates.get("runtime_unblocked", False),
            "Resolve runtime verification blockers",
            "Clear login, verification, and unresolved runtime errors for the selected account.",
        ),
        _gate(
            "no_unassigned_work",
            no_unassigned_work,
            "Resolve historical unassigned work",
            "Review each legacy task, then explicitly claim or quarantine it before real account operations.",
        ),
    ]
    ready = all(gate["passed"] for gate in gates)
    next_step = {
        "gate_id": "ready",
        "title": "Run one controlled listing experiment",
        "detail": "Publish one approved listing on the selected account, monitor inquiries and fulfillment, then save the daily snapshot.",
    }
    for gate in gates:
        if not gate["passed"]:
            next_step = {
                "gate_id": gate["id"],
                "title": gate["title"],
                "detail": gate["detail"],
            }
            break

    return {
        "ready_for_first_run": ready,
        "selected_account": _selected_account_summary(selected),
        "account_readiness": account_readiness,
        "gates": gates,
        "next_step": next_step,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts)),
    }


def _build_account_readiness(
    cookie_id: str,
    *,
    profile: Mapping[str, Any],
    queue: Mapping[str, Any],
    now: float,
) -> Dict[str, Any]:
    gates = [
        _gate("account_enabled", _account_enabled(profile, queue), "Account enabled", "Enable this account."),
        _gate("prompt_ready", _queue_prompt_ready(queue), "Prompt ready", "Complete required Prompt keys."),
        _gate(
            "inventory_context",
            _has_inventory_context(profile, queue),
            "Inventory context ready",
            "Sync or create listing inventory for this account.",
        ),
        _gate("sync_current", _has_complete_fresh_sync(queue), "Account sync current", "Sync orders and listings."),
        _gate("runtime_connected", _runtime_connected(queue), "Runtime connected", "Connect the account runtime."),
        _gate(
            "runtime_fresh",
            _runtime_fresh(queue, now=now),
            "Runtime evidence fresh",
            "Refresh the authenticated runtime session.",
        ),
        _gate(
            "runtime_unblocked",
            _runtime_unblocked(queue),
            "Runtime unblocked",
            "Resolve login, verification, or runtime errors.",
        ),
    ]
    passed_count = sum(1 for gate in gates if gate["passed"])
    completed_prefix_count = 0
    for gate in gates:
        if not gate["passed"]:
            break
        completed_prefix_count += 1
    return {
        "cookie_id": cookie_id,
        "ready": passed_count == len(gates),
        "passed_count": passed_count,
        "completed_prefix_count": completed_prefix_count,
        "total_gates": len(gates),
        "gates": gates,
    }


def _selected_account_summary(selected: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not selected:
        return None
    return {
        "cookie_id": str(selected.get("cookie_id") or ""),
        "ready": bool(selected.get("ready")),
        "passed_count": int(selected.get("passed_count") or 0),
        "completed_prefix_count": int(selected.get("completed_prefix_count") or 0),
        "total_gates": int(selected.get("total_gates") or 0),
    }


def _account_enabled(profile: Mapping[str, Any], queue: Mapping[str, Any]) -> bool:
    return profile.get("enabled") is not False and queue.get("enabled") is not False


def _queue_prompt_ready(queue: Mapping[str, Any]) -> bool:
    if queue.get("enabled") is False:
        return False
    prompt = queue.get("prompt_profile") or {}
    return bool(isinstance(prompt, Mapping) and prompt.get("ready"))


def _has_inventory_context(profile: Mapping[str, Any], queue: Mapping[str, Any]) -> bool:
    queue_count = int((queue.get("queues") or {}).get("items") or 0)
    profile_count = int((profile.get("counts") or {}).get("items") or 0)
    return queue_count > 0 or profile_count > 0


def _has_complete_fresh_sync(queue: Mapping[str, Any]) -> bool:
    freshness = queue.get("sync_freshness") or {}
    if not isinstance(freshness, Mapping):
        return False
    return all(
        isinstance(freshness.get(resource), Mapping) and bool(freshness[resource].get("fresh"))
        for resource in ("orders", "listings")
    )


def _runtime_connected(queue: Mapping[str, Any]) -> bool:
    if queue.get("enabled") is False:
        return False
    runtime = queue.get("account_runtime") or {}
    if not isinstance(runtime, Mapping):
        return False
    return str(runtime.get("connection_status") or "") in {"connected", "degraded"}


def _runtime_fresh(queue: Mapping[str, Any], *, now: float) -> bool:
    if not _runtime_connected(queue):
        return False
    runtime = queue.get("account_runtime") or {}
    heartbeat = float(runtime.get("last_heartbeat_at") or 0)
    heartbeat_age = max(0.0, now - heartbeat) if heartbeat else float("inf")
    return bool(
        heartbeat_age <= RUNTIME_HEARTBEAT_MAX_AGE_SECONDS
        and runtime.get("token_fresh")
        and runtime.get("cookie_fresh")
    )


def _runtime_unblocked(queue: Mapping[str, Any]) -> bool:
    if queue.get("enabled") is False:
        return False
    runtime = queue.get("account_runtime") or {}
    if not isinstance(runtime, Mapping):
        return False
    status = str(runtime.get("connection_status") or "")
    return bool(
        status in {"connected", "degraded"}
        and not runtime.get("pending_verification_reason")
        and not runtime.get("last_error")
    )


def _gate(gate_id: str, passed: bool, title: str, detail: str) -> Dict[str, Any]:
    return {
        "id": gate_id,
        "passed": bool(passed),
        "title": title,
        "detail": detail,
    }
