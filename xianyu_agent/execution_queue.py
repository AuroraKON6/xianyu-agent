from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"queued", "running"}


class ExecutionJobQueue:
    """Persistent execution job queue backed by JSON file.

    Lifecycle: queued -> running -> completed / failed / cancelled
    Failed jobs can be retried (reset to queued, attempts preserved).
    """

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = Path(storage_path or Path("data") / "execution_jobs.json")
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._load()

    def configure(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
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

        # Check for active jobs on this approval
        for job in self._jobs.values():
            if job.get("approval_id") == approval_id and job.get("status") in ACTIVE_STATUSES:
                raise ValueError(
                    f"Active job already exists for approval {approval_id}: {job['job_id']}"
                )

        now = time.time()
        job = {
            "job_id": f"job_{uuid.uuid4().hex[:12]}",
            "approval_id": approval_id,
            "work_order_id": approval.get("work_order_id", ""),
            "action": approval.get("action", ""),
            "status": "queued",
            "attempts": 0,
            "max_attempts": 3,
            "next_retry_at": None,
            "result": None,
            "error": None,
            "evidence": _default_evidence(),
            "operator_next_action": "run_when_ready",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }
        self._jobs[job["job_id"]] = job
        self._save()
        return _copy(job)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Execution job not found: {job_id}")
        return _copy(job)

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.get("created_at", 0))
        return [_copy(j) for j in jobs[-limit:]]

    def get_jobs_for_approval(self, approval_id: str) -> List[Dict[str, Any]]:
        return [_copy(j) for j in self._jobs.values() if j.get("approval_id") == approval_id]

    def run_next(self, action_executor, work_order_manager, executor_context: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Find queued jobs whose next_retry_at has passed, execute them.

        Returns a list of result dicts with job_id, ok, status.
        """
        now = time.time()
        ready = [
            j for j in self._jobs.values()
            if j["status"] == "queued" and (j.get("next_retry_at") is None or j["next_retry_at"] <= now)
        ]
        results = []
        for job in ready:
            result = self._execute_job(job, action_executor, work_order_manager, executor_context)
            results.append(result)
        return results

    def run_pending(self, action_executor, work_order_manager, executor_context: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Alias for run_next."""
        return self.run_next(action_executor, work_order_manager, executor_context)

    def retry_job(self, job_id: str) -> Dict[str, Any]:
        """Reset a failed job back to queued status.

        Raises ValueError if job is not in 'failed' status.
        """
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Execution job not found: {job_id}")
        if job["status"] != "failed":
            raise ValueError(f"Cannot retry job with status '{job['status']}'; must be 'failed'")

        job["status"] = "queued"
        job["error"] = None
        job["result"] = None
        job["next_retry_at"] = None
        job["operator_next_action"] = "run_when_ready"
        job["updated_at"] = time.time()
        self._save()
        return _copy(job)

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a queued job.

        Raises ValueError if job is not in 'queued' status.
        """
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Execution job not found: {job_id}")
        if job["status"] != "queued":
            raise ValueError(f"Cannot cancel job with status '{job['status']}'; must be 'queued'")

        job["status"] = "cancelled"
        job["updated_at"] = time.time()
        job["finished_at"] = time.time()
        self._save()
        return _copy(job)

    def quarantine_jobs_for_work_orders(
        self,
        work_order_ids: List[str],
        reason: str = "unassigned_work_quarantined",
    ) -> Dict[str, Any]:
        """Cancel non-running jobs for explicitly quarantined work orders."""
        selected = {str(item or "").strip() for item in work_order_ids if str(item or "").strip()}
        matched = [job for job in self._jobs.values() if str(job.get("work_order_id") or "") in selected]
        blocked = [
            str(job.get("job_id") or "")
            for job in matched
            if str(job.get("status") or "") in {"running", "completed"}
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
            cancelled.append(str(job.get("job_id") or ""))
        if cancelled:
            self._save()
        return {"ok": True, "cancelled": len(cancelled), "job_ids": cancelled, "blocked_job_ids": []}

    def record_evidence(self, job_id: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Attach operator or adapter evidence to a job without executing it."""
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
        self._save()
        return _copy(job)

    def _execute_job(self, job: Dict[str, Any], action_executor, work_order_manager, executor_context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a single job: mark running, call executor, mark completed/failed."""
        job["status"] = "running"
        job["started_at"] = time.time()
        job["attempts"] = job.get("attempts", 0) + 1
        job["updated_at"] = time.time()

        # On retry, reset the approval's execution state so execute_approval can run again
        if job["attempts"] > 1:
            self._reset_approval_execution(job["approval_id"], work_order_manager)

        self._save()

        try:
            execution_result = work_order_manager.execute_approval(
                job["approval_id"],
                action_executor=action_executor,
                executor_context=executor_context or {},
            )
            handler_result = execution_result.get("execution_result", execution_result)
            evidence = _evidence_from_result(handler_result)
            if evidence:
                job["evidence"] = evidence

            if handler_result.get("ok"):
                job["status"] = "completed"
                job["result"] = handler_result
                job["error"] = None
                job["operator_next_action"] = str(
                    handler_result.get("operator_next_action") or "review_execution_result"
                )
            else:
                job["status"] = "failed"
                job["result"] = handler_result
                job["error"] = handler_result.get("error", "Handler returned ok=False")
                job["operator_next_action"] = str(
                    handler_result.get("operator_next_action")
                    or job.get("evidence", {}).get("operator_next_action")
                    or "inspect_failed_job"
                )

            job["finished_at"] = time.time()
            job["updated_at"] = time.time()
            self._save()
            return {"job_id": job["job_id"], "ok": handler_result.get("ok", False), "status": job["status"]}

        except Exception as exc:
            logger.exception("Execution job '%s' raised", job["job_id"])
            job["status"] = "failed"
            job["error"] = str(exc)
            job["result"] = {"ok": False, "error": str(exc)}
            job["evidence"] = _normalize_evidence({"detected_blocker": "executor_exception"})
            job["operator_next_action"] = "inspect_failed_job"
            job["finished_at"] = time.time()
            job["updated_at"] = time.time()
            self._save()
            return {"job_id": job["job_id"], "ok": False, "status": "failed"}

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

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw if isinstance(raw, list) else []:
            job_id = item.get("job_id")
            if job_id:
                item.setdefault("evidence", _default_evidence())
                item.setdefault("operator_next_action", "")
                self._jobs[job_id] = item

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        records = sorted(self._jobs.values(), key=lambda j: j.get("created_at", 0))
        self.storage_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _default_evidence() -> Dict[str, Any]:
    return {
        "before_screenshot": "",
        "after_screenshot": "",
        "page_url": "",
        "detected_blocker": "",
        "cooldown_seconds": 0,
        "operator_next_action": "",
    }


def _normalize_evidence(value: Any) -> Dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, dict) else {}
    evidence = _default_evidence()
    evidence.update(
        {
            "before_screenshot": str(raw.get("before_screenshot") or "")[:500],
            "after_screenshot": str(raw.get("after_screenshot") or "")[:500],
            "page_url": str(raw.get("page_url") or raw.get("url") or "")[:1000],
            "detected_blocker": str(raw.get("detected_blocker") or raw.get("blocker") or "")[:200],
            "operator_next_action": str(raw.get("operator_next_action") or "")[:200],
        }
    )
    try:
        evidence["cooldown_seconds"] = int(raw.get("cooldown_seconds") or 0)
    except (TypeError, ValueError):
        evidence["cooldown_seconds"] = 0
    return evidence


def _evidence_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    evidence = result.get("evidence")
    if isinstance(evidence, dict):
        normalized = _normalize_evidence(evidence)
    else:
        normalized = _normalize_evidence(result)
    if result.get("operator_next_action") and not normalized.get("operator_next_action"):
        normalized["operator_next_action"] = str(result.get("operator_next_action") or "")[:200]
    if any(value not in ("", 0, 0.0, None) for value in normalized.values()):
        return normalized
    return {}


execution_queue = ExecutionJobQueue()
