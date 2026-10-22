from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


ACTION_SCOPE_SCHEMA_VERSION = 1
DEFAULT_SCOPE_TTL_SECONDS = 24 * 60 * 60
ACTION_SCOPE_TTL_SECONDS = {
    "send_message": 15 * 60,
    "sensitive_reply": 15 * 60,
    "refund_order": 60 * 60,
    "reject_refund": 60 * 60,
    "place_supplier_order": 60 * 60,
    "fulfill_order": 4 * 60 * 60,
    "prepare_purchase_draft": 4 * 60 * 60,
    "publish_listing": 24 * 60 * 60,
    "edit_listing": 24 * 60 * 60,
    "delete_item": 24 * 60 * 60,
}

TARGET_KEYS = (
    "draft_id",
    "candidate_id",
    "item_id",
    "listing_id",
    "external_id",
    "order_id",
    "buyer_id",
    "chat_id",
    "dispute_id",
    "experiment_id",
    "commercial_trial_id",
    "purchase_task_id",
    "supplier_order_id",
    "supplier_snapshot_id",
    "source_message_id",
    "reply_source_message_id",
)
VERSION_KEYS = {
    "expected_revision",
    "expected_draft_revision",
    "draft_revision",
    "candidate_revision",
    "evidence_revision",
    "evidence_fingerprint",
    "approved_evidence_fingerprint",
    "commercial_terms_version",
    "snapshot_version",
    "supplier_snapshot_payload_hash",
    "reply_revision",
    "experiment_revision",
    "policy_version",
}
AMOUNT_KEYS = {
    "amount_cny",
    "refund_amount",
    "purchase_amount",
    "total_cost",
    "expected_cost",
    "price",
    "quantity",
}


class ActionScopeValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "action_scope_invalid")


def build_action_scope_envelope(
    *,
    approval_id: str,
    work_order_id: str,
    action: str,
    payload: Mapping[str, Any],
    draft_snapshot: Mapping[str, Any],
    context: Optional[Mapping[str, Any]] = None,
    requested_at: Optional[float] = None,
    existing: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Bind an approval to one account, target set, payload, snapshot, and lifetime."""
    current = dict(existing or {})
    clean_action = str(action or "").strip()
    requested = _as_float(current.get("requested_at")) or _as_float(requested_at) or time.time()
    ttl_seconds = _scope_ttl(clean_action)
    valid_until = _as_float(current.get("valid_until")) or requested + ttl_seconds
    correlation_id = str(current.get("correlation_id") or f"corr_{uuid.uuid4().hex}")[:120]
    account_values = {
        value
        for value in (
            _first_value("cookie_id", payload),
            _first_value("cookie_id", draft_snapshot),
            _first_value("cookie_id", context or {}),
        )
        if value
    }
    if len(account_values) > 1:
        raise ActionScopeValidationError(
            "account_scope_conflict",
            "Approval payload, snapshot, and work-order context reference different accounts",
        )
    cookie_id = next(iter(account_values), "default")
    target_refs = _target_refs(payload, draft_snapshot, context or {})
    if not target_refs:
        target_refs = {"work_order_id": str(work_order_id or "")}
    expected_versions: Dict[str, Any] = {}
    _collect_named_values(payload, "payload", VERSION_KEYS, expected_versions)
    _collect_named_values(draft_snapshot, "snapshot", VERSION_KEYS, expected_versions)
    payload_sha256 = canonical_sha256(payload)
    snapshot_sha256 = canonical_sha256(draft_snapshot)
    expected_revision = canonical_sha256(
        expected_versions if expected_versions else {"draft_snapshot_sha256": snapshot_sha256}
    )
    generated_idempotency_key = "action:" + canonical_sha256(
        {
            "action": clean_action,
            "cookie_id": cookie_id,
            "target_refs": target_refs,
            "expected_revision": expected_revision,
            "payload_sha256": payload_sha256,
            "draft_snapshot_sha256": snapshot_sha256,
        }
    )
    idempotency_key = str(
        current.get("idempotency_key")
        or payload.get("reply_idempotency_key")
        or payload.get("idempotency_key")
        or generated_idempotency_key
    )[:300]
    amounts: Dict[str, Any] = {}
    _collect_named_values(payload, "payload", AMOUNT_KEYS, amounts)

    envelope: Dict[str, Any] = {
        "schema_version": ACTION_SCOPE_SCHEMA_VERSION,
        "approval_id": str(approval_id or ""),
        "work_order_id": str(work_order_id or ""),
        "action": clean_action,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "cookie_id": cookie_id,
        "target_refs": target_refs,
        "expected_revision": expected_revision,
        "expected_versions": expected_versions,
        "amounts": amounts,
        "payload_sha256": payload_sha256,
        "draft_snapshot_sha256": snapshot_sha256,
        "requested_at": requested,
        "ttl_seconds": ttl_seconds,
        "valid_until": valid_until,
    }
    envelope["action_digest"] = canonical_sha256(envelope)
    return envelope


def require_valid_action_scope(
    approval: Mapping[str, Any],
    *,
    now: Optional[float] = None,
    require_approval_signature: bool = True,
    allow_expired: bool = False,
) -> Dict[str, Any]:
    """Recompute an approval envelope and reject mutation, expiry, or an unsigned action."""
    envelope = approval.get("scope_envelope")
    if not isinstance(envelope, Mapping) or not envelope:
        raise ActionScopeValidationError(
            "scope_envelope_missing",
            "Approval has no action scope envelope; recreate the action plan",
        )
    if int(envelope.get("schema_version") or 0) != ACTION_SCOPE_SCHEMA_VERSION:
        raise ActionScopeValidationError(
            "scope_schema_unsupported",
            "Approval action scope schema is unsupported; recreate the action plan",
        )

    try:
        rebuilt = build_action_scope_envelope(
            approval_id=str(approval.get("approval_id") or ""),
            work_order_id=str(approval.get("work_order_id") or ""),
            action=str(approval.get("action") or ""),
            payload=approval.get("payload") if isinstance(approval.get("payload"), Mapping) else {},
            draft_snapshot=(
                approval.get("draft_snapshot")
                if isinstance(approval.get("draft_snapshot"), Mapping)
                else {}
            ),
            context={
                "cookie_id": str(envelope.get("cookie_id") or "default"),
                **(
                    dict(envelope.get("target_refs") or {})
                    if isinstance(envelope.get("target_refs"), Mapping)
                    else {}
                ),
            },
            requested_at=_as_float(approval.get("requested_at")),
            existing=envelope,
        )
    except ActionScopeValidationError as exc:
        raise ActionScopeValidationError(
            "action_scope_mismatch",
            "Approval payload, snapshot, account, target, version, or scope metadata changed",
        ) from exc
    stored_digest = str(envelope.get("action_digest") or "")
    if not stored_digest or not _constant_time_equal(stored_digest, rebuilt["action_digest"]):
        raise ActionScopeValidationError(
            "action_scope_mismatch",
            "Approval payload, snapshot, account, target, version, or scope metadata changed",
        )
    if require_approval_signature:
        approved_digest = str(approval.get("approved_action_digest") or "")
        if not approved_digest:
            raise ActionScopeValidationError(
                "approval_signature_missing",
                "Approved action digest is missing; approve a current action plan",
            )
        if not _constant_time_equal(approved_digest, stored_digest):
            raise ActionScopeValidationError(
                "approval_signature_mismatch",
                "Human approval does not match the current action digest",
            )
    current_time = _as_float(now) or time.time()
    if not allow_expired and current_time > _as_float(envelope.get("valid_until")):
        raise ActionScopeValidationError(
            "action_scope_expired",
            "Approval action scope expired; recreate and approve a fresh action plan",
        )
    return rebuilt


def listing_draft_revision(draft: Mapping[str, Any]) -> str:
    """Hash only fields that can change the browser publication payload."""
    selected_images = list(draft.get("selected_images") or draft.get("images") or [])
    projection = {
        "title": str(draft.get("title") or ""),
        "category": str(draft.get("category") or ""),
        "price": draft.get("price"),
        "stock": draft.get("stock"),
        "condition": str(draft.get("condition") or "new"),
        "location": str(draft.get("location") or ""),
        "description": str(draft.get("description") or ""),
        "tags": list(draft.get("tags") or []),
        "selected_images": selected_images,
        "delivery_type": str(draft.get("delivery_type") or "manual"),
        "delivery_payload": str(draft.get("delivery_payload") or ""),
        "source": str(draft.get("source") or "execute-approved"),
        "raw_text": str(draft.get("raw_text") or ""),
    }
    image_revisions = []
    for value in selected_images:
        path_text = str(value or "")
        item = {"reference_sha256": canonical_sha256(path_text), "content_sha256": ""}
        try:
            path = Path(path_text)
            if path.is_file():
                item["content_sha256"] = _sha256_file(path)
        except OSError:
            pass
        image_revisions.append(item)
    projection["selected_image_revisions"] = image_revisions
    return canonical_sha256(projection)


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _target_refs(*sources: Mapping[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for key in TARGET_KEYS:
        value = _first_value(key, *sources)
        if value:
            result[key] = value[:300]
    return result


def _first_value(key: str, *sources: Mapping[str, Any]) -> str:
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _collect_named_values(
    value: Any,
    path: str,
    names: set[str],
    output: Dict[str, Any],
    depth: int = 0,
) -> None:
    if depth > 6:
        return
    if isinstance(value, Mapping):
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)
            child = value.get(raw_key)
            child_path = f"{path}.{key}"
            if key in names and child not in (None, ""):
                output[child_path] = _json_safe(child)
            _collect_named_values(child, child_path, names, output, depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value[:100]):
            _collect_named_values(child, f"{path}[{index}]", names, output, depth + 1)


def _scope_ttl(action: str) -> int:
    return int(ACTION_SCOPE_TTL_SECONDS.get(action, DEFAULT_SCOPE_TTL_SECONDS))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, set):
        return sorted((_json_safe(child) for child in value), key=lambda child: repr(child))
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left), str(right))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
