from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"queued", "running", "verification_required"}
EXTERNAL_WRITE_ACTIONS = {
    "publish_listing",
    "edit_listing",
    "fulfill_order",
    "send_message",
    "sensitive_reply",
    "delete_item",
    "refund_order",
}
DEFAULT_LEASE_SECONDS = 300
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 3600

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


class ExecutionJobQueue:
    """Persistent execution job queue backed by JSON file.

    Lifecycle: queued -> running -> completed / failed / verification_required.
    Claims are protected by a process lock, an OS file lock, a lease, and a
    monotonically increasing fencing token.  An expired lease is never
    auto-retried because the external platform may have accepted the write.
    """

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = Path(storage_path or Path("data") / "execution_jobs.json")
        self._thread_lock = _path_lock(self.storage_path)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._load()

    def configure(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
        self._thread_lock = _path_lock(self.storage_path)
        self._jobs = {}
        self._load()

    def create_job(self, approval_id: str, work_order_manager) -> Dict[str, Any]:
        """Create an execution job for an approved approval.

        Raises ValueError if:
        - approval is not in 'approved' status
        - approval already has an active (queued/running) job
        """
        approval = self._find_approval(approval_id, work_order_manager)
        if approval is None:
            raise KeyError(f"Approval not found: {approval_id}")

        if approval.get("status") != "approved":
            raise ValueError(
                f"Cannot create job for approval with status '{approval.get('status')}'; must be 'approved'"
            )

        work_order_manager.validate_approval_scope(approval_id)

        with self._locked_state(refresh=True):
            payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
            action = str(approval.get("action") or "")
            scope_envelope = (
                dict(approval.get("scope_envelope") or {})
                if isinstance(approval.get("scope_envelope"), dict)
                else {}
            )
            idempotency_key = str(
                scope_envelope.get("idempotency_key")
                or payload.get("reply_idempotency_key")
                or payload.get("idempotency_key")
                or f"approval:{approval_id}:action:{action}"
            )[:300]
            for job in self._jobs.values():
                if job.get("approval_id") == approval_id and job.get("status") in ACTIVE_STATUSES:
                    raise ValueError(
                        f"Active job already exists for approval {approval_id}: {job['job_id']}"
                    )
                if (
                    idempotency_key
                    and job.get("idempotency_key") == idempotency_key
                    and job.get("status") in ACTIVE_STATUSES.union({"completed"})
                ):
                    raise ValueError(
                        f"Execution idempotency key already used by job {job['job_id']}"
                    )

            now = time.time()
            job = {
                "job_id": f"job_{uuid.uuid4().hex[:12]}",
                "approval_id": approval_id,
                "work_order_id": approval.get("work_order_id", ""),
                "action": action,
                "idempotency_key": idempotency_key,
                "scope_envelope": scope_envelope,
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
                "next_retry_at": None,
                "result": None,
                "error": None,
                "evidence": _default_evidence(),
                "verification": _default_verification(),
                "operator_next_action": "run_when_ready",
                "fencing_token": 0,
                "lease_owner": "",
                "lease_token": "",
                "lease_expires_at": None,
                "last_heartbeat_at": None,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
            }
            self._jobs[job["job_id"]] = job
            self._save_unlocked()
            return _copy(job)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._locked_state(refresh=True):
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Execution job not found: {job_id}")
            return _copy(job)

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._locked_state(refresh=True):
            jobs = sorted(self._jobs.values(), key=lambda j: j.get("created_at", 0))
            return [_copy(j) for j in jobs[-limit:]]

    def get_jobs_for_approval(self, approval_id: str) -> List[Dict[str, Any]]:
        with self._locked_state(refresh=True):
            return [_copy(j) for j in self._jobs.values() if j.get("approval_id") == approval_id]

    def run_next(
        self,
        action_executor,
        work_order_manager,
        executor_context: Optional[Dict] = None,
        *,
        worker_id: str = "",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_jobs: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Claim ready jobs, execute them, and finalize only with the current fence."""
        clean_worker_id = str(worker_id or f"worker_{os.getpid()}_{uuid.uuid4().hex[:8]}")[:120]
        clean_lease_seconds = _normalize_lease_seconds(lease_seconds)
        claimed, expired = self._claim_ready_jobs(
            clean_worker_id,
            clean_lease_seconds,
            max_jobs=max_jobs,
        )
        for job in expired:
            self._mark_approval_verification_required(job, work_order_manager)

        results = []
        for claim in claimed:
            result = self._execute_claimed_job(
                claim,
                action_executor,
                work_order_manager,
                executor_context,
            )
            results.append(result)
        return results

    def run_pending(
        self,
        action_executor,
        work_order_manager,
        executor_context: Optional[Dict] = None,
        *,
        worker_id: str = "",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> List[Dict[str, Any]]:
        """Execute all ready jobs using the same lease protocol as run_next."""
        return self.run_next(
            action_executor,
            work_order_manager,
            executor_context,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> Dict[str, Any]:
        """Renew a running claim when the owner and fencing token still match."""
        now = time.time()
        with self._locked_state(refresh=True):
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Execution job not found: {job_id}")
            _require_current_claim(job, worker_id, lease_token, fencing_token)
            if job.get("status") != "running":
                raise ValueError(f"Cannot renew lease for status '{job.get('status')}'")
            job["last_heartbeat_at"] = now
            job["lease_expires_at"] = now + _normalize_lease_seconds(lease_seconds)
            job["updated_at"] = now
            self._save_unlocked()
            return _copy(job)

    def reconcile_expired_leases(self, work_order_manager) -> List[Dict[str, Any]]:
        """Move abandoned running jobs to verification_required without claiming new work."""
        _, expired = self._claim_ready_jobs(
            "lease_reconciler",
            DEFAULT_LEASE_SECONDS,
            max_jobs=0,
        )
        for job in expired:
            self._mark_approval_verification_required(job, work_order_manager)
        return expired

    def retry_job(self, job_id: str) -> Dict[str, Any]:
        """Reset a failed job back to queued status.

        Raises ValueError if job is not in 'failed' status.
        """
        with self._locked_state(refresh=True):
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Execution job not found: {job_id}")
            if job["status"] != "failed":
                raise ValueError(f"Cannot retry job with status '{job['status']}'; must be 'failed'")
            if int(job.get("attempts") or 0) >= int(job.get("max_attempts") or 3):
                raise ValueError("Cannot retry job because max_attempts has been reached")
            scope_envelope = job.get("scope_envelope") if isinstance(job.get("scope_envelope"), dict) else {}
            valid_until = _as_float(scope_envelope.get("valid_until"))
            if valid_until and valid_until < time.time():
                raise ValueError("Cannot retry job because the approved action scope expired")

            job["status"] = "queued"
            job["error"] = None
            job["result"] = None
            job["next_retry_at"] = None
            job["operator_next_action"] = "run_when_ready"
            _clear_lease(job)
            job["updated_at"] = time.time()
            self._save_unlocked()
            return _copy(job)

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a queued job.

        Raises ValueError if job is not in 'queued' status.
        """
        with self._locked_state(refresh=True):
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Execution job not found: {job_id}")
            if job["status"] != "queued":
                raise ValueError(f"Cannot cancel job with status '{job['status']}'; must be 'queued'")

            now = time.time()
            job["status"] = "cancelled"
            job["operator_next_action"] = "none"
            job["updated_at"] = now
            job["finished_at"] = now
            _clear_lease(job)
            self._save_unlocked()
            return _copy(job)

    def quarantine_jobs_for_work_orders(
        self,
        work_order_ids: List[str],
        reason: str = "unassigned_work_quarantined",
    ) -> Dict[str, Any]:
        """Cancel non-running jobs for explicitly quarantined work orders."""
        selected = {str(item or "").strip() for item in work_order_ids if str(item or "").strip()}
        with self._locked_state(refresh=True):
            matched = [job for job in self._jobs.values() if str(job.get("work_order_id") or "") in selected]
            blocked = [
                str(job.get("job_id") or "")
                for job in matched
                if str(job.get("status") or "") in {"running", "completed", "verification_required"}
            ]
            if blocked:
                return {"ok": False, "cancelled": 0, "job_ids": [], "blocked_job_ids": blocked}

            now = time.time()
            cancelled = []
            for job in matched:
                if str(job.get("status") or "") not in {"queued", "failed"}:
                    continue
                job["status"] = "cancelled"
                job["cancellation_reason"] = str(reason or "unassigned_work_quarantined")
                job["operator_next_action"] = "none"
                job["updated_at"] = now
                job["finished_at"] = now
                _clear_lease(job)
                cancelled.append(str(job.get("job_id") or ""))
            if cancelled:
                self._save_unlocked()
            return {"ok": True, "cancelled": len(cancelled), "job_ids": cancelled, "blocked_job_ids": []}

    def record_evidence(self, job_id: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Attach operator or adapter evidence to a job without executing it."""
        with self._locked_state(refresh=True):
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Execution job not found: {job_id}")
            normalized = _normalize_evidence(evidence)
            current = _normalize_evidence(job.get("evidence") or {})
            current.update({key: value for key, value in normalized.items() if value not in ("", 0, 0.0, None)})
            job["evidence"] = current
            next_action = str(evidence.get("operator_next_action") or "").strip()
            if next_action:
                job["operator_next_action"] = next_action
            elif current.get("detected_blocker"):
                job["operator_next_action"] = "inspect_execution_blocker"
            job["updated_at"] = time.time()
            self._save_unlocked()
            return _copy(job)

    def resolve_verification(
        self,
        job_id: str,
        *,
        resolution: str,
        note: str,
        evidence: Optional[Dict[str, Any]],
        work_order_manager,
    ) -> Dict[str, Any]:
        """Close an uncertain external result after an explicit platform check."""
        clean_resolution = str(resolution or "").strip().lower()
        if clean_resolution not in {"confirmed_completed", "confirmed_not_executed"}:
            raise ValueError("resolution must be confirmed_completed or confirmed_not_executed")
        clean_note = str(note or "").strip()
        if not clean_note:
            raise ValueError("verification note is required")
        clean_evidence = _normalize_verification_evidence(evidence or {})
        if clean_resolution == "confirmed_completed" and not any(
            clean_evidence.get(key) for key in ("external_id", "page_url", "screenshot")
        ):
            raise ValueError("confirmed_completed requires external_id, page_url, or screenshot evidence")

        with self._locked_state(refresh=True):
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Execution job not found: {job_id}")
            if job.get("status") != "verification_required":
                raise ValueError(
                    f"Cannot resolve verification for status '{job.get('status')}'; must be 'verification_required'"
                )
            work_order_manager.validate_approval_scope(
                str(job.get("approval_id") or ""),
                now=time.time(),
                allow_expired=True,
            )

            now = time.time()
            job["fencing_token"] = int(job.get("fencing_token") or 0) + 1
            job["verification"] = {
                "required_at": (job.get("verification") or {}).get("required_at") or now,
                "reason": (job.get("verification") or {}).get("reason") or "external_result_unknown",
                "resolved_at": now,
                "resolution": clean_resolution,
                "note": clean_note[:1000],
                "evidence": clean_evidence,
            }
            if clean_resolution == "confirmed_completed":
                job["status"] = "completed"
                job["result"] = {
                    "ok": True,
                    "manual_confirmation": True,
                    "verification_resolution": clean_resolution,
                    "verification_evidence": clean_evidence,
                }
                job["error"] = None
                job["operator_next_action"] = "review_execution_result"
            else:
                job["status"] = "failed"
                job["result"] = {
                    "ok": False,
                    "manual_confirmation": True,
                    "confirmed_not_executed": True,
                    "verification_resolution": clean_resolution,
                    "verification_evidence": clean_evidence,
                }
                job["error"] = "Platform check confirmed that the action was not executed"
                job["operator_next_action"] = "retry_after_verified_non_execution"
            job["finished_at"] = now
            job["updated_at"] = now
            _clear_lease(job)
            resolved = _copy(job)
            self._save_unlocked()

        if clean_resolution == "confirmed_completed":
            self._complete_approval_from_verification(resolved, work_order_manager)
        else:
            self._mark_approval_failed_after_verification(resolved, work_order_manager)
        return resolved

    def _claim_ready_jobs(
        self,
        worker_id: str,
        lease_seconds: int,
        *,
        max_jobs: Optional[int],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        now = time.time()
        claimed: List[Dict[str, Any]] = []
        expired: List[Dict[str, Any]] = []
        changed = False
        with self._locked_state(refresh=True):
            for job in self._jobs.values():
                if job.get("status") != "running":
                    continue
                expires_at = _as_float(job.get("lease_expires_at"))
                if expires_at > now:
                    continue
                job["status"] = "verification_required"
                job["error"] = "Execution lease expired before a durable platform result was recorded"
                job["result"] = {
                    "ok": False,
                    "verification_required": True,
                    "outcome_uncertain": True,
                    "error": job["error"],
                }
                job["evidence"] = _normalize_evidence({
                    "detected_blocker": "execution_lease_expired",
                    "operator_next_action": "verify_platform_before_retry",
                })
                job["verification"] = {
                    "required_at": now,
                    "reason": "execution_lease_expired",
                    "resolved_at": None,
                    "resolution": "",
                    "note": "",
                    "evidence": {},
                }
                job["operator_next_action"] = "verify_platform_before_retry"
                job["updated_at"] = now
                expired.append(_copy(job))
                changed = True

            ready = sorted(
                (
                    job for job in self._jobs.values()
                    if job.get("status") == "queued"
                    and (
                        job.get("next_retry_at") is None
                        or _as_float(job.get("next_retry_at")) <= now
                    )
                ),
                key=lambda item: (item.get("created_at", 0), item.get("job_id", "")),
            )
            if max_jobs is not None:
                ready = ready[:max(0, int(max_jobs))]
            for job in ready:
                if int(job.get("attempts") or 0) >= int(job.get("max_attempts") or 3):
                    job["status"] = "failed"
                    job["error"] = "max_attempts reached"
                    job["operator_next_action"] = "inspect_failed_job"
                    job["finished_at"] = now
                    job["updated_at"] = now
                    changed = True
                    continue
                job["status"] = "running"
                job["attempts"] = int(job.get("attempts") or 0) + 1
                job["fencing_token"] = int(job.get("fencing_token") or 0) + 1
                job["lease_owner"] = worker_id
                job["lease_token"] = uuid.uuid4().hex
                job["lease_expires_at"] = now + lease_seconds
                job["last_heartbeat_at"] = now
                job["started_at"] = now
                job["finished_at"] = None
                job["updated_at"] = now
                job["operator_next_action"] = "execution_in_progress"
                claimed.append(_copy(job))
                changed = True
            if changed:
                self._save_unlocked()
        return claimed, expired

    def _execute_claimed_job(
        self,
        claim: Dict[str, Any],
        action_executor,
        work_order_manager,
        executor_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        job_id = str(claim.get("job_id") or "")
        action = str(claim.get("action") or "")
        try:
            with self._locked_state(refresh=True):
                current = self._jobs.get(job_id)
                if not current:
                    raise KeyError(f"Execution job not found: {job_id}")
                _require_current_claim(
                    current,
                    str(claim.get("lease_owner") or ""),
                    str(claim.get("lease_token") or ""),
                    int(claim.get("fencing_token") or 0),
                )
                if current.get("status") != "running":
                    return {
                        "job_id": job_id,
                        "ok": False,
                        "status": current.get("status"),
                        "stale_claim": True,
                    }

            if int(claim.get("attempts") or 0) > 1:
                self._reset_approval_execution(claim["approval_id"], work_order_manager)

            context = dict(executor_context or {})
            context["execution_claim"] = {
                "job_id": job_id,
                "idempotency_key": claim.get("idempotency_key", ""),
                "worker_id": claim.get("lease_owner", ""),
                "lease_token": claim.get("lease_token", ""),
                "fencing_token": claim.get("fencing_token", 0),
            }
            execution_result = work_order_manager.execute_approval(
                claim["approval_id"],
                action_executor=action_executor,
                executor_context=context,
            )
            handler_result = execution_result.get("execution_result", execution_result)
            if not isinstance(handler_result, dict):
                handler_result = {"ok": False, "error": "Executor returned a non-object result"}
            verification_reason = _verification_reason(handler_result)
            return self._finalize_claim(
                claim,
                handler_result,
                work_order_manager,
                verification_reason=verification_reason,
            )
        except Exception as exc:
            logger.exception("Execution job '%s' raised", job_id)
            uncertain = action in EXTERNAL_WRITE_ACTIONS
            result = {
                "ok": False,
                "error": str(exc),
                "verification_required": uncertain,
                "outcome_uncertain": uncertain,
                "operator_next_action": (
                    "verify_platform_before_retry" if uncertain else "inspect_failed_job"
                ),
                "evidence": {
                    "detected_blocker": "executor_exception",
                    "operator_next_action": (
                        "verify_platform_before_retry" if uncertain else "inspect_failed_job"
                    ),
                },
            }
            return self._finalize_claim(
                claim,
                result,
                work_order_manager,
                verification_reason="executor_exception_after_external_action" if uncertain else "",
            )

    def _finalize_claim(
        self,
        claim: Dict[str, Any],
        handler_result: Dict[str, Any],
        work_order_manager,
        *,
        verification_reason: str,
    ) -> Dict[str, Any]:
        job_id = str(claim.get("job_id") or "")
        now = time.time()
        with self._locked_state(refresh=True):
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Execution job not found: {job_id}")
            try:
                _require_current_claim(
                    job,
                    str(claim.get("lease_owner") or ""),
                    str(claim.get("lease_token") or ""),
                    int(claim.get("fencing_token") or 0),
                )
            except ValueError:
                return {
                    "job_id": job_id,
                    "ok": False,
                    "status": job.get("status"),
                    "stale_claim": True,
                }
            if job.get("status") not in {"running", "verification_required"}:
                return {
                    "job_id": job_id,
                    "ok": False,
                    "status": job.get("status"),
                    "stale_claim": True,
                }

            evidence = _evidence_from_result(handler_result)
            if evidence:
                job["evidence"] = evidence
            job["result"] = handler_result
            if verification_reason:
                job["status"] = "verification_required"
                job["error"] = str(
                    handler_result.get("error")
                    or "External platform result is unknown; verify before retrying"
                )
                existing_verification = job.get("verification") or {}
                job["verification"] = {
                    "required_at": existing_verification.get("required_at") or now,
                    "reason": verification_reason,
                    "resolved_at": None,
                    "resolution": "",
                    "note": "",
                    "evidence": {},
                }
                job["operator_next_action"] = "verify_platform_before_retry"
                job["finished_at"] = None
            elif handler_result.get("ok"):
                job["status"] = "completed"
                job["error"] = None
                job["operator_next_action"] = str(
                    handler_result.get("operator_next_action") or "review_execution_result"
                )
                job["finished_at"] = now
            else:
                job["status"] = "failed"
                job["error"] = str(handler_result.get("error") or "Handler returned ok=False")
                job["operator_next_action"] = str(
                    handler_result.get("operator_next_action")
                    or job.get("evidence", {}).get("operator_next_action")
                    or "inspect_failed_job"
                )
                job["finished_at"] = now
            job["updated_at"] = now
            _clear_lease(job)
            finalized = _copy(job)
            self._save_unlocked()

        if finalized.get("status") == "verification_required":
            self._mark_approval_verification_required(finalized, work_order_manager)
        return {
            "job_id": job_id,
            "ok": finalized.get("status") == "completed",
            "status": finalized.get("status"),
        }

    def _find_approval(self, approval_id: str, work_order_manager) -> Optional[Dict[str, Any]]:
        """Look up an approval across all work orders."""
        for work_order in work_order_manager.list_work_orders(limit=500):
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    return approval
        return None

    def _reset_approval_execution(self, approval_id: str, work_order_manager) -> None:
        """Reset an approval's execution state so it can be re-executed (for retries)."""
        for work_order in work_order_manager._work_orders.values():
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    approval["execution_status"] = None
                    approval["execution_result"] = None
                    approval["execution_started_at"] = None
                    approval["executed_at"] = None
                    work_order_manager._save()
                    return

    def _mark_approval_verification_required(self, job: Dict[str, Any], work_order_manager) -> None:
        target = self._approval_target(str(job.get("approval_id") or ""), work_order_manager)
        if target is None:
            logger.warning("Approval missing while marking verification_required for job '%s'", job.get("job_id"))
            return
        work_order, approval = target
        job_id = str(job.get("job_id") or "")
        already_recorded = any(
            event.get("type") == "execution_verification_required" and event.get("job_id") == job_id
            for event in work_order.get("events", [])
        )
        approval["execution_status"] = "verification_required"
        approval["execution_result"] = {
            "ok": False,
            "verification_required": True,
            "job_id": job_id,
            "reason": (job.get("verification") or {}).get("reason") or "external_result_unknown",
            "result": job.get("result"),
        }
        approval["executed_at"] = None
        work_order["status"] = "verification_required"
        if not already_recorded:
            work_order.setdefault("events", []).append({
                "type": "execution_verification_required",
                "created_at": time.time(),
                "approval_id": approval.get("approval_id", ""),
                "action": approval.get("action", ""),
                "job_id": job_id,
                "reason": (job.get("verification") or {}).get("reason") or "external_result_unknown",
            })
        work_order_manager._touch(work_order)
        work_order_manager._save()

    def _complete_approval_from_verification(self, job: Dict[str, Any], work_order_manager) -> None:
        self._mark_approval_verification_required(job, work_order_manager)
        result = dict(job.get("result") or {})
        result["execution_job_id"] = str(job.get("job_id") or "")
        result["verification"] = dict(job.get("verification") or {})
        work_order_manager.complete_approval_manually(str(job.get("approval_id") or ""), result)

    def _mark_approval_failed_after_verification(self, job: Dict[str, Any], work_order_manager) -> None:
        target = self._approval_target(str(job.get("approval_id") or ""), work_order_manager)
        if target is None:
            logger.warning("Approval missing while resolving verification for job '%s'", job.get("job_id"))
            return
        work_order, approval = target
        now = time.time()
        approval["execution_status"] = "failed"
        approval["execution_result"] = {
            "ok": False,
            "confirmed_not_executed": True,
            "job_id": str(job.get("job_id") or ""),
            "verification": dict(job.get("verification") or {}),
        }
        approval["executed_at"] = now
        work_order.setdefault("events", []).append({
            "type": "execution_verification_resolved",
            "created_at": now,
            "approval_id": approval.get("approval_id", ""),
            "action": approval.get("action", ""),
            "job_id": str(job.get("job_id") or ""),
            "resolution": "confirmed_not_executed",
        })
        work_order_manager._refresh_execution_status(work_order)
        work_order_manager._touch(work_order)
        work_order_manager._save()

    def _approval_target(self, approval_id: str, work_order_manager):
        for work_order in work_order_manager._work_orders.values():
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    return work_order, approval
        return None

    def _load(self) -> None:
        with self._locked_state(refresh=False):
            self._reload_unlocked()

    @contextmanager
    def _locked_state(self, *, refresh: bool):
        with self._thread_lock:
            with self._exclusive_file_lock():
                if refresh:
                    self._reload_unlocked()
                yield

    @contextmanager
    def _exclusive_file_lock(self):
        lock_path = self.storage_path.with_name(f"{self.storage_path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _reload_unlocked(self) -> None:
        self._jobs = {}
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw if isinstance(raw, list) else []:
            job_id = item.get("job_id")
            if job_id:
                _normalize_loaded_job(item)
                self._jobs[job_id] = item

    def _save_unlocked(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        records = sorted(self._jobs.values(), key=lambda j: j.get("created_at", 0))
        temp_path = self.storage_path.with_name(f".{self.storage_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.storage_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _default_evidence() -> Dict[str, Any]:
    return {
        "evidence_id": "",
        "adapter": "",
        "adapter_status": "",
        "before_screenshot": "",
        "after_screenshot": "",
        "evidence_manifest": "",
        "screenshot_sha256": "",
        "screenshot_bytes": 0,
        "page_url": "",
        "page_title": "",
        "captured_at": 0.0,
        "action_stage": "",
        "write_attempted": False,
        "account_scope": "",
        "target_scope_sha256": "",
        "capture_status": "",
        "capture_error": "",
        "error_summary": "",
        "detected_blocker": "",
        "cooldown_seconds": 0,
        "operator_next_action": "",
    }


def _default_verification() -> Dict[str, Any]:
    return {
        "required_at": None,
        "reason": "",
        "resolved_at": None,
        "resolution": "",
        "note": "",
        "evidence": {},
    }


def _normalize_loaded_job(job: Dict[str, Any]) -> None:
    job.setdefault("idempotency_key", f"approval:{job.get('approval_id', '')}:action:{job.get('action', '')}")
    job["evidence"] = _normalize_evidence(job.get("evidence") or {})
    job.setdefault("scope_envelope", {})
    job.setdefault("verification", _default_verification())
    job.setdefault("operator_next_action", "")
    job.setdefault("fencing_token", 0)
    job.setdefault("lease_owner", "")
    job.setdefault("lease_token", "")
    job.setdefault("lease_expires_at", None)
    job.setdefault("last_heartbeat_at", None)


def _clear_lease(job: Dict[str, Any]) -> None:
    job["lease_owner"] = ""
    job["lease_token"] = ""
    job["lease_expires_at"] = None
    job["last_heartbeat_at"] = None


def _normalize_lease_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_LEASE_SECONDS
    return max(MIN_LEASE_SECONDS, min(MAX_LEASE_SECONDS, seconds))


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _require_current_claim(
    job: Dict[str, Any],
    worker_id: str,
    lease_token: str,
    fencing_token: int,
) -> None:
    if str(job.get("lease_owner") or "") != str(worker_id or ""):
        raise ValueError("execution lease owner mismatch")
    if not lease_token or str(job.get("lease_token") or "") != str(lease_token):
        raise ValueError("execution lease token mismatch")
    if int(job.get("fencing_token") or 0) != int(fencing_token or 0):
        raise ValueError("execution fencing token mismatch")


def _verification_reason(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    if result.get("verification_required") or result.get("outcome_uncertain") or result.get("outcome_unknown"):
        return str(result.get("verification_reason") or "external_result_unknown")[:200]
    for key in (
        "publish_result",
        "adapter_result",
        "fulfillment_result",
        "send_result",
        "delete_result",
        "refund_result",
    ):
        nested = result.get(key)
        if not isinstance(nested, dict):
            continue
        raw = nested.get("raw") if isinstance(nested.get("raw"), dict) else {}
        if (
            nested.get("verification_required")
            or nested.get("outcome_uncertain")
            or nested.get("outcome_unknown")
            or raw.get("verification_required")
            or raw.get("outcome_uncertain")
            or raw.get("outcome_unknown")
        ):
            return str(
                nested.get("verification_reason")
                or raw.get("verification_reason")
                or "external_result_unknown"
            )[:200]
    return ""


def _normalize_verification_evidence(value: Dict[str, Any]) -> Dict[str, Any]:
    raw = dict(value or {})
    return {
        "external_id": str(raw.get("external_id") or raw.get("platform_reference") or "")[:300],
        "page_url": str(raw.get("page_url") or raw.get("url") or "")[:1000],
        "screenshot": str(raw.get("screenshot") or raw.get("after_screenshot") or "")[:500],
        "checked_by": str(raw.get("checked_by") or "operator")[:120],
        "checked_at": _as_float(raw.get("checked_at")) or time.time(),
    }


def _normalize_evidence(value: Any) -> Dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, dict) else {}
    evidence = _default_evidence()
    evidence.update(
        {
            "evidence_id": str(raw.get("evidence_id") or "")[:120],
            "adapter": str(raw.get("adapter") or "")[:80],
            "adapter_status": str(raw.get("adapter_status") or "")[:80],
            "before_screenshot": str(raw.get("before_screenshot") or "")[:500],
            "after_screenshot": str(raw.get("after_screenshot") or "")[:500],
            "evidence_manifest": str(raw.get("evidence_manifest") or "")[:500],
            "screenshot_sha256": str(raw.get("screenshot_sha256") or "")[:80],
            "page_url": str(raw.get("page_url") or raw.get("url") or "")[:1000],
            "page_title": str(raw.get("page_title") or "")[:200],
            "action_stage": str(raw.get("action_stage") or raw.get("stage") or "")[:100],
            "write_attempted": bool(raw.get("write_attempted")),
            "account_scope": str(raw.get("account_scope") or "")[:80],
            "target_scope_sha256": str(raw.get("target_scope_sha256") or "")[:80],
            "capture_status": str(raw.get("capture_status") or "")[:80],
            "capture_error": str(raw.get("capture_error") or "")[:500],
            "error_summary": str(raw.get("error_summary") or "")[:500],
            "detected_blocker": str(raw.get("detected_blocker") or raw.get("blocker") or "")[:200],
            "operator_next_action": str(raw.get("operator_next_action") or "")[:200],
        }
    )
    evidence["captured_at"] = _as_float(raw.get("captured_at"))
    try:
        evidence["screenshot_bytes"] = max(0, int(raw.get("screenshot_bytes") or 0))
    except (TypeError, ValueError):
        evidence["screenshot_bytes"] = 0
    try:
        evidence["cooldown_seconds"] = int(raw.get("cooldown_seconds") or 0)
    except (TypeError, ValueError):
        evidence["cooldown_seconds"] = 0
    return evidence


def _evidence_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    candidates = [result]
    if isinstance(result.get("evidence"), dict):
        candidates.append(result["evidence"])
    for key in (
        "publish_result",
        "adapter_result",
        "fulfillment_result",
        "send_result",
        "delete_result",
        "refund_result",
    ):
        nested = result.get(key)
        if not isinstance(nested, dict):
            continue
        candidates.append(nested)
        if isinstance(nested.get("evidence"), dict):
            candidates.append(nested["evidence"])
        raw = nested.get("raw")
        if isinstance(raw, dict):
            candidates.append(raw)
            if isinstance(raw.get("evidence"), dict):
                candidates.append(raw["evidence"])

    normalized = _default_evidence()
    for candidate in candidates:
        current = _normalize_evidence(candidate)
        for key, value in current.items():
            if value not in ("", 0, 0.0, False, None):
                normalized[key] = value
    if result.get("operator_next_action") and not normalized.get("operator_next_action"):
        normalized["operator_next_action"] = str(result.get("operator_next_action") or "")[:200]
    if any(value not in ("", 0, 0.0, None) for value in normalized.values()):
        return normalized
    return {}


execution_queue = ExecutionJobQueue()
