from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


MESSAGE_INTAKE_RUNBOOKS: Dict[str, Dict[str, Any]] = {
    "bridge_stale": {
        "code": "bridge_stale",
        "title": "Feishu CLI bridge is stale",
        "severity": "warning",
        "symptoms": [
            "No inbound buyer message has been observed within the configured freshness window.",
            "The console health panel shows bridge: stale.",
        ],
        "checks": [
            "Confirm the Feishu CLI long connection process is still running and bound to the expected app.",
            "Send a harmless test buyer-message event through /feishu/events and verify /message-intake/health becomes fresh.",
            "Check local service health at /health and confirm the service is reachable on the configured port.",
        ],
        "recovery_steps": [
            "Restart the Feishu CLI bridge and wait for one fresh structured buyer-message event.",
            "If the CLI is healthy but no events arrive, verify app binding, permissions, and the local bridge URL.",
            "Use the incident bundle export before deleting local logs or pruning the ledger.",
        ],
        "safety_boundary": [
            "Recovery checks must not approve, enqueue, execute, or send buyer messages.",
            "Any draft reply still requires the existing approval gate before it can be sent.",
        ],
        "related_endpoints": [
            "/health",
            "/feishu/events",
            "/message-intake/health",
            "/message-intake/incident-bundle",
        ],
    },
    "duplicate_rate": {
        "code": "duplicate_rate",
        "title": "Inbound duplicate rate is high",
        "severity": "warning",
        "symptoms": [
            "Duplicate inbound-message records exceed the configured threshold.",
            "Operators may see repeated events for the same buyer message.",
        ],
        "checks": [
            "Verify Feishu CLI retries are not replaying the same event without stable message_id values.",
            "Check whether the bridge payload includes source, cookie_id, message_id, buyer_id, chat_id, and item_id.",
            "Inspect /message-intake/recent for duplicate records that point at the same original intake.",
        ],
        "recovery_steps": [
            "Ensure the bridge always forwards the original message_id when one is available.",
            "If only payload hashing is available, keep source, cookie_id, buyer_id, chat_id, item_id, and message text stable.",
            "Do not remove duplicate suppression unless a human has confirmed the upstream event contract changed.",
        ],
        "safety_boundary": [
            "Duplicate recovery must not create extra approvals for the same buyer message.",
            "Existing approval and execution gates remain unchanged.",
        ],
        "related_endpoints": [
            "/message-intake/health",
            "/message-intake/recent",
            "/message-intake/metrics",
            "/message-intake/incident-bundle",
        ],
    },
    "generation_failures": {
        "code": "generation_failures",
        "title": "Auto-reply generation is failing",
        "severity": "warning",
        "symptoms": [
            "Inbound messages are falling back to manual_reply or generation_failed status.",
            "The model provider may be unavailable or returning unusable replies.",
        ],
        "checks": [
            "Confirm /health reports the expected DeepSeek model and configuration state.",
            "Check recent generation_error summaries in the incident bundle.",
            "Verify operator-visible WorkOrders exist for manual handling when replies cannot be generated.",
        ],
        "recovery_steps": [
            "Restore the model provider or credentials, then retry only the affected intake records from the console.",
            "Use /message-intake/{intake_id}/retry after recovery to create a new pending draft approval.",
            "Keep manual replies visible until a human approves any regenerated draft.",
        ],
        "safety_boundary": [
            "Retry only creates pending approvals; it must not approve, enqueue, execute, or send messages.",
            "Human approval is still required for every send_message action.",
        ],
        "related_endpoints": [
            "/health",
            "/message-intake/health",
            "/message-intake/{intake_id}/retry",
            "/message-intake/incident-bundle",
        ],
    },
    "model_recovery": {
        "code": "model_recovery",
        "title": "Model outage recovery",
        "severity": "info",
        "symptoms": [
            "Generation failures recently occurred and the provider has now recovered.",
            "Operators need a safe way to regenerate draft replies.",
        ],
        "checks": [
            "Confirm /health reports the expected model and the provider is reachable.",
            "Open /message-intake/recent and identify only manual_reply or generation_failed records to retry.",
            "Review any risk flags before approving a regenerated reply.",
        ],
        "recovery_steps": [
            "Retry one affected intake first and inspect the pending approval snapshot.",
            "Retry additional affected records in small batches only after the first draft looks correct.",
            "Approve and queue replies through the normal approval center only after human review.",
        ],
        "safety_boundary": [
            "Model recovery must not bypass approval, queue, or execution controls.",
            "Regenerated replies remain drafts until a human approves them.",
        ],
        "related_endpoints": [
            "/health",
            "/message-intake/health",
            "/message-intake/recent",
            "/message-intake/{intake_id}/retry",
        ],
    },
}


def get_message_intake_runbooks(alert_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    codes = alert_codes or list(MESSAGE_INTAKE_RUNBOOKS.keys())
    result = []
    seen = set()
    for code in codes:
        normalized = str(code or "").strip()
        if not normalized or normalized in seen:
            continue
        runbook = MESSAGE_INTAKE_RUNBOOKS.get(normalized)
        if runbook:
            seen.add(normalized)
            result.append(_copy(runbook))
    return result


class MessageIntakeLedger:
    """Persistent ledger for inbound buyer-message intake observability."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = Path(storage_path or Path("data") / "message_intake.json")
        self._records: Dict[str, Dict[str, Any]] = {}
        self._load()

    def configure(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
        self._records = {}
        self._load()

    def event_key(
        self,
        *,
        source: str,
        cookie_id: str,
        buyer_id: str,
        chat_id: str,
        item_id: str,
        message: str,
        message_id: str = "",
    ) -> str:
        if message_id:
            return f"message_id:{source}:{cookie_id}:{message_id}"
        raw = "\n".join(
            [
                source.strip(),
                cookie_id.strip(),
                buyer_id.strip(),
                chat_id.strip(),
                item_id.strip(),
                message.strip(),
            ]
        )
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def find_created(self, event_key: str) -> Optional[Dict[str, Any]]:
        for record in sorted(self._records.values(), key=lambda item: item.get("created_at", 0)):
            if record.get("event_key") == event_key and record.get("status") != "duplicate":
                return _copy(record)
        return None

    def record_created(
        self,
        *,
        event_key: str,
        source: str,
        buyer_id: str,
        chat_id: str,
        item_id: str,
        incoming_message_summary: str,
        reply_summary: str,
        risk_flags: List[str],
        work_order_id: str,
        approval_id: str,
        cookie_id: str = "",
        status: str = "created",
        generation_error: str = "",
        envelope_id: str = "",
        source_message_id: str = "",
        ack_status: str = "",
        created_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        now = time.time() if created_at is None else float(created_at)
        record = {
            "intake_id": f"intake_{uuid.uuid4().hex[:12]}",
            "event_key": event_key,
            "source": source,
            "cookie_id": cookie_id,
            "envelope_id": envelope_id,
            "source_message_id": source_message_id,
            "ack_status": ack_status,
            "buyer_id": buyer_id,
            "chat_id": chat_id,
            "item_id": item_id,
            "incoming_message_summary": incoming_message_summary,
            "reply_summary": reply_summary,
            "risk_flags": list(risk_flags or []),
            "work_order_id": work_order_id,
            "approval_id": approval_id,
            "status": status,
            "generation_error": generation_error,
            "created_at": now,
            "updated_at": now,
        }
        self._records[record["intake_id"]] = record
        self._save()
        return _copy(record)

    def record_duplicate(
        self,
        *,
        event_key: str,
        source: str,
        duplicate_of: str,
        envelope_id: str = "",
        source_message_id: str = "",
        ack_status: str = "",
        created_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        now = time.time() if created_at is None else float(created_at)
        record = {
            "intake_id": f"intake_{uuid.uuid4().hex[:12]}",
            "event_key": event_key,
            "source": source,
            "envelope_id": envelope_id,
            "source_message_id": source_message_id,
            "ack_status": ack_status,
            "buyer_id": "",
            "chat_id": "",
            "item_id": "",
            "incoming_message_summary": "",
            "reply_summary": "",
            "risk_flags": [],
            "work_order_id": "",
            "approval_id": "",
            "status": "duplicate",
            "duplicate_of": duplicate_of,
            "created_at": now,
            "updated_at": now,
        }
        self._records[record["intake_id"]] = record
        self._save()
        return _copy(record)

    def get_record(self, intake_id: str) -> Optional[Dict[str, Any]]:
        record = self._records.get(intake_id)
        return _copy(record) if record else None

    def record_retry_result(
        self,
        *,
        intake_id: str,
        status: str,
        reply_summary: str = "",
        approval_id: str = "",
        generation_error: str = "",
        risk_flags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        record = self._records.get(intake_id)
        if not record:
            raise KeyError(f"Intake record not found: {intake_id}")
        now = time.time()
        record["status"] = status
        record["reply_summary"] = reply_summary
        record["approval_id"] = approval_id
        record["generation_error"] = generation_error
        if risk_flags is not None:
            record["risk_flags"] = list(risk_flags)
        record["retry_count"] = int(record.get("retry_count") or 0) + 1
        record["retried_at"] = now
        record["updated_at"] = now
        self._save()
        return _copy(record)

    def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        records = sorted(self._records.values(), key=lambda item: item.get("created_at", 0), reverse=True)
        return [_copy(record) for record in records[: max(0, int(limit))]]

    def quarantine_for_work_orders(
        self,
        work_order_ids: List[str],
        reason: str = "unassigned_work_quarantined",
    ) -> Dict[str, Any]:
        """Close pending intake records belonging to explicitly quarantined work."""
        selected = {str(item or "").strip() for item in work_order_ids if str(item or "").strip()}
        now = time.time()
        quarantined = []
        for record in self._records.values():
            if str(record.get("work_order_id") or "") not in selected:
                continue
            if str(record.get("status") or "") not in {"created", "retry_created", "retry_failed"}:
                continue
            record["status"] = "quarantined"
            record["quarantine_reason"] = str(reason or "unassigned_work_quarantined")
            record["updated_at"] = now
            quarantined.append(str(record.get("intake_id") or ""))
        if quarantined:
            self._save()
        return {"ok": True, "quarantined": len(quarantined), "intake_ids": quarantined}

    def metrics(self) -> Dict[str, Any]:
        records = list(self._records.values())
        non_duplicates = [record for record in records if record.get("status") != "duplicate"]
        manual_statuses = {"manual_reply", "generation_failed", "retry_manual_reply", "retry_generation_failed"}
        approval_statuses = {"created", "retry_created"}
        return {
            "total_records": len(records),
            "created_intakes": sum(1 for record in records if record.get("status") == "created"),
            "retry_created_intakes": sum(1 for record in records if record.get("status") == "retry_created"),
            "duplicate_intakes": sum(1 for record in records if record.get("status") == "duplicate"),
            "manual_reply_fallbacks": sum(
                1 for record in records if record.get("status") in manual_statuses
            ),
            "generation_failures": sum(
                1 for record in records if record.get("status") in {"generation_failed", "retry_generation_failed"}
            ),
            "risk_flagged_intakes": sum(1 for record in non_duplicates if record.get("risk_flags")),
            "pending_approvals": sum(
                1 for record in records if record.get("status") in approval_statuses and record.get("approval_id")
            ),
        }

    def prune(
        self,
        *,
        max_age_seconds: Optional[float] = None,
        max_records: Optional[int] = None,
        now: Optional[float] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        current_time = time.time() if now is None else float(now)
        records = list(self._records.values())
        kept = records
        if max_age_seconds is not None and max_age_seconds >= 0:
            cutoff = current_time - float(max_age_seconds)
            kept = [record for record in kept if float(record.get("created_at") or 0) >= cutoff]
        if max_records is not None and int(max_records) >= 0:
            kept = sorted(kept, key=lambda item: item.get("created_at", 0), reverse=True)[: int(max_records)]
        kept_ids = {record.get("intake_id") for record in kept}
        removed = [record for record in records if record.get("intake_id") not in kept_ids]
        if not dry_run:
            self._records = {record["intake_id"]: record for record in kept if record.get("intake_id")}
            self._save()
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "removed": len(removed),
            "kept": len(kept),
            "total_before": len(records),
            "max_age_seconds": max_age_seconds,
            "max_records": max_records,
        }

    def health(
        self,
        *,
        window_seconds: float = 3600,
        failure_threshold: int = 3,
        duplicate_rate_threshold: float = 0.5,
        duplicate_min_events: int = 5,
        stale_after_seconds: float = 1800,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        current_time = time.time() if now is None else float(now)
        records = list(self._records.values())
        if not records:
            return {
                "ok": True,
                "bridge_status": "empty",
                "last_event_at": None,
                "last_event_age_seconds": None,
                "last_source": "",
                "window_seconds": window_seconds,
                "recent_events": 0,
                "duplicate_rate": 0,
                "generation_failures": 0,
                "alerts": [],
            }

        last_record = max(records, key=lambda item: item.get("created_at", 0))
        last_event_at = float(last_record.get("created_at") or 0)
        last_event_age = max(0, int(current_time - last_event_at))
        cutoff = current_time - float(window_seconds)
        recent = [record for record in records if float(record.get("created_at") or 0) >= cutoff]
        duplicates = [record for record in recent if record.get("status") == "duplicate"]
        failures = [
            record for record in recent
            if record.get("status") in {"generation_failed", "retry_generation_failed"}
        ]
        duplicate_rate = (len(duplicates) / len(recent)) if recent else 0
        alerts = []
        if len(failures) >= int(failure_threshold):
            alerts.append(
                {
                    "code": "generation_failures",
                    "severity": "warning",
                    "count": len(failures),
                    "message": "Repeated auto-reply generation failures in the intake window.",
                }
            )
        if len(recent) >= int(duplicate_min_events) and duplicate_rate >= float(duplicate_rate_threshold):
            alerts.append(
                {
                    "code": "duplicate_rate",
                    "severity": "warning",
                    "count": len(duplicates),
                    "rate": round(duplicate_rate, 4),
                    "message": "Duplicate inbound-message rate is above the configured threshold.",
                }
            )
        bridge_status = "fresh"
        if last_event_age > float(stale_after_seconds):
            bridge_status = "stale"
            alerts.append(
                {
                    "code": "bridge_stale",
                    "severity": "warning",
                    "age_seconds": last_event_age,
                    "message": "No inbound message has been observed recently.",
                }
            )
        return {
            "ok": not alerts,
            "bridge_status": bridge_status,
            "last_event_at": last_event_at,
            "last_event_age_seconds": last_event_age,
            "last_source": str(last_record.get("source") or ""),
            "window_seconds": window_seconds,
            "recent_events": len(recent),
            "duplicate_rate": round(duplicate_rate, 4),
            "generation_failures": len(failures),
            "alerts": alerts,
        }

    def incident_bundle(
        self,
        *,
        limit: int = 20,
        now: Optional[float] = None,
        health_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = dict(health_options or {})
        health = self.health(now=now, **options)
        metrics = self.metrics()
        recent_records = [
            record
            for record in self.list_recent(limit=max(1, int(limit)) * 2)
            if record.get("status") != "duplicate"
        ][: max(0, int(limit))]
        runbook_codes = _runbook_codes_for_alerts(health.get("alerts") or [])
        return {
            "ok": True,
            "type": "message_intake_incident_bundle",
            "dry_run": True,
            "generated_at": time.time() if now is None else float(now),
            "health": health,
            "metrics": metrics,
            "alerts": _copy({"alerts": health.get("alerts") or []})["alerts"],
            "runbooks": get_message_intake_runbooks(runbook_codes),
            "recent_intakes": [_sanitize_incident_record(record) for record in recent_records],
            "privacy": {
                "source_event_ids": "omitted",
                "buyer_and_chat_ids": "hashed",
                "approval_ids": "omitted",
                "raw_payloads": "omitted",
            },
            "safety_boundary": [
                "This export is diagnostic only and does not mutate WorkOrders, Approvals, or ExecutionJobs.",
                "It never approves, enqueues, executes, or sends buyer messages.",
            ],
        }

    def dry_run_health_check(
        self,
        *,
        window_seconds: float = 3600,
        failure_threshold: int = 3,
        duplicate_rate_threshold: float = 0.5,
        duplicate_min_events: int = 5,
        stale_after_seconds: float = 1800,
        notification_hooks: Optional[List[str]] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        health = self.health(
            window_seconds=window_seconds,
            failure_threshold=failure_threshold,
            duplicate_rate_threshold=duplicate_rate_threshold,
            duplicate_min_events=duplicate_min_events,
            stale_after_seconds=stale_after_seconds,
            now=now,
        )
        runbook_codes = _runbook_codes_for_alerts(health.get("alerts") or [])
        return {
            "ok": True,
            "type": "message_intake_health_check",
            "dry_run": True,
            "generated_at": time.time() if now is None else float(now),
            "health": health,
            "metrics": self.metrics(),
            "runbooks": get_message_intake_runbooks(runbook_codes),
            "notification_previews": build_message_intake_notification_previews(
                health,
                notification_hooks=notification_hooks or [],
            ),
            "mutations": [],
            "safety_boundary": [
                "Dry-run health checks do not mutate WorkOrders, Approvals, ExecutionJobs, or the intake ledger.",
                "Notification hooks are previews here; no message is sent from this endpoint.",
            ],
        }

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for record in raw if isinstance(raw, list) else []:
            intake_id = record.get("intake_id")
            if intake_id:
                self._records[intake_id] = record

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        records = sorted(self._records.values(), key=lambda item: item.get("created_at", 0))
        self.storage_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _runbook_codes_for_alerts(alerts: List[Dict[str, Any]]) -> List[str]:
    codes = [str(alert.get("code") or "") for alert in alerts]
    if "generation_failures" in codes and "model_recovery" not in codes:
        codes.append("model_recovery")
    return [code for code in codes if code in MESSAGE_INTAKE_RUNBOOKS]


def build_message_intake_notification_previews(
    health: Dict[str, Any],
    *,
    notification_hooks: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    hooks = [str(hook or "").strip() for hook in (notification_hooks or [])]
    hooks = [hook for hook in hooks if hook]
    if not hooks:
        return []
    alerts = health.get("alerts") or []
    alert_codes = [str(alert.get("code") or "alert") for alert in alerts]
    if not alerts:
        return [
            {
                "hook": hook,
                "status": "skipped",
                "dry_run": True,
                "reason": "no alerts",
                "message": "Message intake health check has no active alerts.",
                "alert_codes": [],
            }
            for hook in hooks
        ]
    message = "Message intake health alerts: " + ", ".join(alert_codes)
    previews = []
    for hook in hooks:
        supported = hook in {"console", "local_file", "feishu_cli"}
        previews.append(
            {
                "hook": hook,
                "status": "preview" if supported else "unsupported",
                "dry_run": True,
                "message": message if supported else f"Unsupported local notification hook: {hook}",
                "alert_codes": alert_codes,
                "target": _notification_target(hook),
            }
        )
    return previews


def _notification_target(hook: str) -> str:
    return {
        "console": "operator console alert panel",
        "local_file": "data/message_intake_notifications.jsonl",
        "feishu_cli": "local Feishu CLI operator notification",
    }.get(hook, "")


def _sanitize_incident_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "intake_id": str(record.get("intake_id") or ""),
        "status": str(record.get("status") or ""),
        "source": str(record.get("source") or ""),
        "buyer_ref": _hash_ref(record.get("buyer_id")),
        "chat_ref": _hash_ref(record.get("chat_id")),
        "item_ref": _hash_ref(record.get("item_id")),
        "work_order_ref": _hash_ref(record.get("work_order_id")),
        "incoming_summary": _safe_summary(record.get("incoming_message_summary"), 160),
        "reply_summary": _safe_summary(record.get("reply_summary"), 160),
        "risk_flags": list(record.get("risk_flags") or []),
        "generation_error_summary": _safe_summary(record.get("generation_error"), 120),
        "retry_count": int(record.get("retry_count") or 0),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _hash_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "hash:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _safe_summary(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return ""
    lowered = text.lower()
    secret_markers = ["bearer ", "api_key", "apikey", "token=", "secret=", "sk-"]
    if any(marker in lowered for marker in secret_markers):
        return "[redacted]"
    if len(text) > int(limit):
        return text[: max(0, int(limit) - 3)] + "..."
    return text


message_intake_ledger = MessageIntakeLedger()
