from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional


REPLY_ACTIONS = {"send_message", "sensitive_reply"}
OUTBOX_ACTIVE_STATUSES = {
    "pending_approval",
    "ready_to_enqueue",
    "queued",
    "running",
    "verification_required",
}


def build_reply_metadata(
    *,
    action: str,
    cookie_id: str,
    buyer_id: str,
    message: str,
    chat_id: str = "",
    item_id: str = "",
    order_id: str = "",
    dispute_id: str = "",
    source: str = "manual",
    source_event_key: str = "",
    source_message_id: str = "",
    reply_revision: int = 1,
    idempotency_key: str = "",
) -> Dict[str, Any]:
    """Build immutable identifiers that bind one reply proposal to its scope."""
    clean_action = str(action or "send_message").strip()
    if clean_action not in REPLY_ACTIONS:
        raise ValueError(f"unsupported reply action: {clean_action}")
    clean_cookie_id = str(cookie_id or "").strip()
    clean_buyer_id = str(buyer_id or "").strip()
    clean_message = str(message or "").strip()
    if not clean_cookie_id or not clean_buyer_id or not clean_message:
        raise ValueError("cookie_id, buyer_id, and message are required for a reply proposal")
    try:
        revision = max(1, int(reply_revision or 1))
    except (TypeError, ValueError):
        revision = 1

    message_sha256 = hashlib.sha256(clean_message.encode("utf-8")).hexdigest()
    scope = {
        "action": clean_action,
        "cookie_id": clean_cookie_id,
        "buyer_id": clean_buyer_id,
        "chat_id": str(chat_id or "").strip(),
        "item_id": str(item_id or "").strip(),
        "order_id": str(order_id or "").strip(),
        "dispute_id": str(dispute_id or "").strip(),
        "source": str(source or "manual").strip(),
        "source_event_key": str(source_event_key or "").strip(),
        "source_message_id": str(source_message_id or "").strip(),
        "reply_revision": revision,
        "message_sha256": message_sha256,
    }
    clean_idempotency_key = str(idempotency_key or "").strip()
    if not clean_idempotency_key:
        if scope["source_event_key"]:
            clean_idempotency_key = "reply:" + _sha256_json({
                "source_event_key": scope["source_event_key"],
                "reply_revision": revision,
                "message_sha256": message_sha256,
                "action": clean_action,
            })
        else:
            clean_idempotency_key = f"reply:manual:{uuid.uuid4().hex}"
    if len(clean_idempotency_key) > 240:
        clean_idempotency_key = "reply:" + hashlib.sha256(clean_idempotency_key.encode("utf-8")).hexdigest()

    return {
        "reply_idempotency_key": clean_idempotency_key,
        "reply_action_digest": _sha256_json(scope),
        "reply_message_sha256": message_sha256,
        "reply_revision": revision,
        "reply_source": scope["source"],
        "reply_source_event_key": scope["source_event_key"],
        "reply_source_message_id": scope["source_message_id"],
    }


def find_reply_by_idempotency_key(work_order_manager, idempotency_key: str) -> Optional[Dict[str, Any]]:
    clean_key = str(idempotency_key or "").strip()
    if not clean_key:
        return None
    for work_order in work_order_manager.list_work_orders(limit=1000):
        for approval in work_order.get("approvals", []):
            if approval.get("action") not in REPLY_ACTIONS:
                continue
            payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
            if str(payload.get("reply_idempotency_key") or "") == clean_key:
                return {
                    "work_order": work_order,
                    "approval": approval,
                }
    return None


def validate_reply_idempotency_match(
    existing: Mapping[str, Any],
    proposed_metadata: Mapping[str, Any],
) -> None:
    """Reject reuse of one idempotency key for a different immutable reply."""
    approval = existing.get("approval") if isinstance(existing, Mapping) else None
    payload = approval.get("payload") if isinstance(approval, Mapping) else None
    existing_digest = str((payload or {}).get("reply_action_digest") or "")
    proposed_digest = str(proposed_metadata.get("reply_action_digest") or "")
    if existing_digest and proposed_digest and existing_digest != proposed_digest:
        raise ValueError(
            "reply idempotency key already belongs to a different buyer, message, scope, or revision"
        )


def list_reply_outbox(
    *,
    work_order_manager,
    execution_queue,
    message_intake_ledger=None,
    status: str = "",
    cookie_id: str = "",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Project reply proposals, approvals, jobs, receipts, and verification into one view."""
    jobs = execution_queue.list_jobs(limit=1000) if execution_queue is not None else []
    jobs_by_approval: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        approval_id = str(job.get("approval_id") or "")
        if approval_id:
            jobs_by_approval.setdefault(approval_id, []).append(job)

    intake_by_approval: Dict[str, Dict[str, Any]] = {}
    intake_by_work_order: Dict[str, Dict[str, Any]] = {}
    if message_intake_ledger is not None:
        for record in message_intake_ledger.list_recent(limit=1000):
            if str(record.get("status") or "") == "duplicate":
                continue
            approval_id = str(record.get("approval_id") or "")
            work_order_id = str(record.get("work_order_id") or "")
            if approval_id:
                intake_by_approval[approval_id] = record
            if work_order_id:
                existing = intake_by_work_order.get(work_order_id)
                if existing is None or float(record.get("updated_at") or 0) >= float(existing.get("updated_at") or 0):
                    intake_by_work_order[work_order_id] = record

    items = []
    for work_order in work_order_manager.list_work_orders(limit=1000):
        for approval in work_order.get("approvals", []):
            action = str(approval.get("action") or "")
            if action not in REPLY_ACTIONS:
                continue
            approval_id = str(approval.get("approval_id") or "")
            approval_jobs = sorted(
                jobs_by_approval.get(approval_id, []),
                key=lambda job: (float(job.get("created_at") or 0), str(job.get("job_id") or "")),
            )
            job = approval_jobs[-1] if approval_jobs else None
            intake = intake_by_approval.get(approval_id) or intake_by_work_order.get(
                str(work_order.get("work_order_id") or "")
            )
            item = _project_reply(work_order, approval, job, intake)
            if status and item["status"] != str(status).strip():
                continue
            if cookie_id and item["cookie_id"] != str(cookie_id).strip():
                continue
            items.append(item)

    items.sort(key=lambda item: (float(item.get("created_at") or 0), item.get("outbox_id", "")), reverse=True)
    return items[: max(0, int(limit))]


def summarize_reply_outbox(items: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    records = list(items)
    for item in records:
        state = str(item.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return {
        "total": len(records),
        "counts": counts,
        "requires_operator": sum(
            1
            for item in records
            if str(item.get("status") or "")
            in {"pending_approval", "ready_to_enqueue", "failed", "verification_required"}
        ),
        "sent": counts.get("sent", 0),
        "verification_required": counts.get("verification_required", 0),
        "generated_at": time.time(),
    }


def outbox_item_for_approval(
    approval_id: str,
    *,
    work_order_manager,
    execution_queue,
    message_intake_ledger=None,
) -> Optional[Dict[str, Any]]:
    clean_approval_id = str(approval_id or "").strip()
    for item in list_reply_outbox(
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
        limit=1000,
    ):
        if item.get("approval_id") == clean_approval_id:
            return item
    return None


def _project_reply(
    work_order: Mapping[str, Any],
    approval: Mapping[str, Any],
    job: Optional[Mapping[str, Any]],
    intake: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
    snapshot = approval.get("draft_snapshot") if isinstance(approval.get("draft_snapshot"), dict) else {}
    context = work_order.get("context") if isinstance(work_order.get("context"), dict) else {}
    approval_id = str(approval.get("approval_id") or "")
    message = str(payload.get("message") or "")
    message_sha256 = str(payload.get("reply_message_sha256") or "")
    if not message_sha256 and message:
        message_sha256 = hashlib.sha256(message.encode("utf-8")).hexdigest()
    status_value = _project_status(approval, job)
    verification = dict(job.get("verification") or {}) if isinstance(job, Mapping) else {}
    return {
        "outbox_id": "reply_" + hashlib.sha256(approval_id.encode("utf-8")).hexdigest()[:16],
        "status": status_value,
        "action": str(approval.get("action") or ""),
        "work_order_id": str(work_order.get("work_order_id") or ""),
        "approval_id": approval_id,
        "execution_job_id": str(job.get("job_id") or "") if isinstance(job, Mapping) else "",
        "intake_id": str((intake or {}).get("intake_id") or ""),
        "cookie_id": str(payload.get("cookie_id") or snapshot.get("cookie_id") or context.get("cookie_id") or ""),
        "buyer_id": str(payload.get("buyer_id") or snapshot.get("buyer_id") or context.get("buyer_id") or ""),
        "chat_id": str(payload.get("chat_id") or snapshot.get("chat_id") or context.get("chat_id") or ""),
        "item_id": str(payload.get("item_id") or snapshot.get("item_id") or context.get("item_id") or ""),
        "order_id": str(payload.get("order_id") or snapshot.get("order_id") or context.get("order_id") or ""),
        "dispute_id": str(payload.get("dispute_id") or snapshot.get("dispute_id") or context.get("dispute_id") or ""),
        "source": str(
            payload.get("reply_source")
            or payload.get("source")
            or snapshot.get("source")
            or (intake or {}).get("source")
            or "manual"
        ),
        "source_message_id": str(
            payload.get("reply_source_message_id")
            or (intake or {}).get("source_message_id")
            or ""
        ),
        "message_summary": _summary(
            snapshot.get("reply_summary")
            or snapshot.get("message_summary")
            or (intake or {}).get("reply_summary")
            or message,
            160,
        ),
        "message_sha256": message_sha256,
        "reply_revision": int(payload.get("reply_revision") or 1),
        "idempotency_key": str(payload.get("reply_idempotency_key") or ""),
        "action_digest": str(payload.get("reply_action_digest") or ""),
        "risk_flags": list(snapshot.get("risk_flags") or (intake or {}).get("risk_flags") or []),
        "approval_status": str(approval.get("status") or ""),
        "execution_status": str(job.get("status") or "") if isinstance(job, Mapping) else "",
        "attempts": int(job.get("attempts") or 0) if isinstance(job, Mapping) else 0,
        "fencing_token": int(job.get("fencing_token") or 0) if isinstance(job, Mapping) else 0,
        "operator_next_action": str(job.get("operator_next_action") or "") if isinstance(job, Mapping) else _next_action(status_value),
        "error": _summary(job.get("error"), 240) if isinstance(job, Mapping) else "",
        "verification": _sanitize_verification(verification),
        "execution_evidence": _sanitize_execution_evidence(
            job.get("evidence") if isinstance(job, Mapping) else {}
        ),
        "receipt": _sanitize_receipt(job),
        "created_at": float(approval.get("requested_at") or work_order.get("created_at") or 0),
        "updated_at": float(
            (job or {}).get("updated_at")
            or approval.get("decided_at")
            or work_order.get("updated_at")
            or 0
        ),
    }


def _project_status(approval: Mapping[str, Any], job: Optional[Mapping[str, Any]]) -> str:
    approval_status = str(approval.get("status") or "")
    if approval_status == "pending":
        return "pending_approval"
    if approval_status == "rejected":
        return "rejected"
    if approval_status != "approved":
        return "draft"
    if not isinstance(job, Mapping):
        return "ready_to_enqueue"
    job_status = str(job.get("status") or "")
    if job_status == "completed":
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        return "sent" if result.get("ok") else "completed_without_receipt"
    return {
        "queued": "queued",
        "running": "running",
        "verification_required": "verification_required",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(job_status, job_status or "ready_to_enqueue")


def _sanitize_receipt(job: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(job, Mapping):
        return {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    nested = result.get("send_result") if isinstance(result.get("send_result"), dict) else {}
    verification_evidence = (
        result.get("verification_evidence")
        if isinstance(result.get("verification_evidence"), dict)
        else {}
    )
    receipt = {
        "ok": bool(result.get("ok")),
        "adapter": str(nested.get("adapter") or "")[:80],
        "external_message_id": str(
            nested.get("message_id")
            or nested.get("external_id")
            or verification_evidence.get("external_id")
            or ""
        )[:300],
        "page_url": str(nested.get("page_url") or verification_evidence.get("page_url") or "")[:1000],
        "manual_confirmation": bool(result.get("manual_confirmation")),
        "verification_resolution": str(result.get("verification_resolution") or "")[:80],
    }
    return receipt if any(value not in ("", False, None) for value in receipt.values()) else {}


def _sanitize_verification(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not value:
        return {}
    evidence = value.get("evidence") if isinstance(value.get("evidence"), dict) else {}
    return {
        "required_at": value.get("required_at"),
        "reason": str(value.get("reason") or "")[:200],
        "resolved_at": value.get("resolved_at"),
        "resolution": str(value.get("resolution") or "")[:80],
        "note": _summary(value.get("note"), 240),
        "evidence": {
            "external_id": str(evidence.get("external_id") or "")[:300],
            "page_url": str(evidence.get("page_url") or "")[:1000],
            "screenshot": str(evidence.get("screenshot") or "")[:500],
            "checked_by": str(evidence.get("checked_by") or "")[:120],
            "checked_at": evidence.get("checked_at"),
        },
    }


def _sanitize_execution_evidence(value: Any) -> Dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    return {
        "evidence_id": str(raw.get("evidence_id") or "")[:120],
        "adapter": str(raw.get("adapter") or "")[:80],
        "adapter_status": str(raw.get("adapter_status") or "")[:80],
        "after_screenshot": str(raw.get("after_screenshot") or "")[:500],
        "evidence_manifest": str(raw.get("evidence_manifest") or "")[:500],
        "screenshot_sha256": str(raw.get("screenshot_sha256") or "")[:80],
        "page_url": str(raw.get("page_url") or "")[:1000],
        "page_title": str(raw.get("page_title") or "")[:200],
        "captured_at": raw.get("captured_at"),
        "action_stage": str(raw.get("action_stage") or "")[:100],
        "write_attempted": bool(raw.get("write_attempted")),
        "capture_status": str(raw.get("capture_status") or "")[:80],
        "capture_error": _summary(raw.get("capture_error"), 240),
        "detected_blocker": str(raw.get("detected_blocker") or "")[:200],
    }


def _next_action(status: str) -> str:
    return {
        "pending_approval": "review_reply_draft",
        "ready_to_enqueue": "enqueue_approved_reply",
        "rejected": "none",
    }.get(status, "")


def _summary(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _sha256_json(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
