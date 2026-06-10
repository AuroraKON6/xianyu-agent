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

            if handler_result.get("ok"):
                job["status"] = "completed"
                job["result"] = handler_result
                job["error"] = None
            else:
                job["status"] = "failed"
                job["result"] = handler_result
                job["error"] = handler_result.get("error", "Handler returned ok=False")

            job["finished_at"] = time.time()
            job["updated_at"] = time.time()
            self._save()
            return {"job_id": job["job_id"], "ok": handler_result.get("ok", False), "status": job["status"]}

        except Exception as exc:
            logger.exception("Execution job '%s' raised", job["job_id"])
            job["status"] = "failed"
            job["error"] = str(exc)
            job["result"] = {"ok": False, "error": str(exc)}
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
                self._jobs[job_id] = item

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        records = sorted(self._jobs.values(), key=lambda j: j.get("created_at", 0))
        self.storage_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


execution_queue = ExecutionJobQueue()
