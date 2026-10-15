"""Persistent agent job store (WP07).

Jobs are durable facts owned by this store -- not chat history in the
agent registry. Python workers (and later the Pi runner) lease jobs
atomically, renew leases, and complete/fail them with fencing tokens;
the store serializes write jobs per account and recovers abandoned
leases on restart.

Semantics (WP07 backlog):
- ``claim_next`` is atomic within the process: one job goes to exactly
  one worker; a second concurrent claim gets the next job or None.
- Every lease bumps a monotonic ``fencing_token``; state-changing calls
  must repeat the worker id and token, so a stale worker (lost a lease,
  recovered later) can never overwrite the new owner's progress.
- Write-required jobs serialize per ``account_id``: while one is
  leased/running, no other write job for that account can be claimed.
  Read/analysis jobs never block each other.
- Expired leases recover by kind: analysis jobs requeue while attempts
  remain; write jobs become ``unknown`` because their side effects on
  the platform cannot be assumed (the existing ExecutionJob
  verification path stays authoritative for platform writes).
- ``idempotency_key`` submissions are deduplicated; resubmitting the
  same key returns the stored job instead of creating work twice.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .commerce.contracts import build_agent_job, utc_now
from .commerce.repositories import LockedJsonList

JOB_QUEUED = "queued"
JOB_LEASED = "leased"
JOB_RUNNING = "running"
JOB_AWAITING_INPUT = "awaiting_input"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
JOB_TIMED_OUT = "timed_out"
JOB_UNKNOWN = "unknown"

ACTIVE_STATES = (JOB_QUEUED, JOB_LEASED, JOB_RUNNING, JOB_AWAITING_INPUT)
LEASE_STATES = (JOB_LEASED, JOB_RUNNING)


class AgentJobError(RuntimeError):
    pass


class FencingError(AgentJobError):
    pass


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


class AgentJobStore:
    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "agent_jobs.json")

    # ---- submission ----

    def submit(
        self,
        *,
        role: str,
        goal_id: str = "",
        project_id: str = "",
        account_id: str = "",
        input_refs: Sequence[str] = (),
        input_versions: Optional[Mapping[str, int]] = None,
        priority: int = 5,
        deadline: Optional[datetime] = None,
        model_budget: Optional[Mapping[str, Any]] = None,
        parent_job_id: str = "",
        idempotency_key: str = "",
        write_required: bool = False,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        if max_attempts < 1:
            raise AgentJobError("max_attempts must be >= 1")
        if write_required and not account_id:
            raise AgentJobError("write jobs must declare an account_id")
        job = build_agent_job(
            role=role,
            goal_id=goal_id,
            project_id=project_id,
            account_id=account_id,
            input_refs=list(input_refs),
            input_versions=dict(input_versions or {}),
            priority=priority,
            deadline=deadline,
            status=JOB_QUEUED,
            attempt=0,
            model_budget=dict(model_budget or {}),
            parent_job_id=parent_job_id,
            idempotency_key=idempotency_key,
        )
        job["write_required"] = bool(write_required)
        job["max_attempts"] = int(max_attempts)
        job["result_ref"] = None
        job["error"] = None
        job["cancel_reason"] = None
        job["cost"] = None
        # Codex D3: dedup + append must be atomic inside one store lock;
        # the old find-then-append allowed a concurrent submit with the same
        # key to slip through after both find() calls returned empty.
        with self._store as items:
            if idempotency_key:
                for existing in items._items:
                    if str(existing.get("idempotency_key")) == idempotency_key:
                        return dict(existing)
            items._items.append(job)
        return dict(job)

    # ---- claiming ----

    def _account_write_busy(self, items: LockedJsonList, account_id: str) -> bool:
        return any(
            str(job.get("account_id")) == account_id
            and job.get("write_required")
            and str(job.get("status")) in LEASE_STATES
            for job in items._items
        )

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 300.0,
        now: Optional[datetime] = None,
        roles: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Atomically lease the next queued job this worker may run.

        ``roles`` restricts the claim to the listed roles: analysis workers
        declare their own roles so they can never grab platform-write or
        unrelated-pipeline jobs, and those workers never grab analysis jobs
        (G2A/A42). Without the filter the previous behavior is unchanged.
        """
        if not worker_id.strip():
            raise AgentJobError("worker_id required")
        moment = now or utc_now()
        allowed = {str(role) for role in roles} if roles is not None else None
        with self._store as items:
            candidates = [
                j for j in items._items
                if str(j.get("status")) == JOB_QUEUED
                and (allowed is None or str(j.get("role")) in allowed)
                # Codex D5: a worker that declares analysis roles must never
                # lease a platform-write envelope, even if the role matches
                and (allowed is None or not j.get("write_required"))
            ]
            candidates.sort(key=lambda j: (int(j.get("priority") or 5), str(j.get("created_at") or "")))
            for job in candidates:
                if job.get("write_required") and self._account_write_busy(items, str(job.get("account_id"))):
                    continue
                job["status"] = JOB_LEASED
                job["lease_owner"] = worker_id
                job["lease_until"] = _iso(moment + timedelta(seconds=lease_seconds))
                job["fencing_token"] = int(job.get("fencing_token") or 0) + 1
                job["attempt"] = int(job.get("attempt") or 0) + 1
                job["lease_claimed_at"] = _iso(moment)
                return dict(job)
        return None

    # ---- lease-scoped operations ----

    def _get_locked(self, items: LockedJsonList, job_id: str) -> Dict[str, Any]:
        for job in items._items:
            if str(job.get("record_id")) == job_id:
                return job
        raise KeyError(f"agent job not found: {job_id}")

    def _assert_lease(self, job: Mapping[str, Any], worker_id: str, fencing_token: int) -> None:
        if str(job.get("lease_owner")) != worker_id:
            raise FencingError(f"job {job.get('record_id')} leased to {job.get('lease_owner')!r}, not {worker_id!r}")
        if int(job.get("fencing_token") or 0) != int(fencing_token):
            raise FencingError(
                f"stale fencing token {fencing_token} for job {job.get('record_id')} "
                f"(current {job.get('fencing_token')})"
            )

    def start(self, job_id: str, *, worker_id: str, fencing_token: int) -> Dict[str, Any]:
        with self._store as items:
            job = self._get_locked(items, job_id)
            self._assert_lease(job, worker_id, fencing_token)
            if str(job.get("status")) != JOB_LEASED:
                raise AgentJobError(f"cannot start job in status {job.get('status')}")
            job["status"] = JOB_RUNNING
            return dict(job)

    def heartbeat(self, job_id: str, *, worker_id: str, fencing_token: int, lease_seconds: float = 300.0) -> Dict[str, Any]:
        with self._store as items:
            job = self._get_locked(items, job_id)
            self._assert_lease(job, worker_id, fencing_token)
            if str(job.get("status")) not in LEASE_STATES:
                raise AgentJobError(f"cannot heartbeat job in status {job.get('status')}")
            job["lease_until"] = _iso(utc_now() + timedelta(seconds=lease_seconds))
            job["last_heartbeat_at"] = _iso(utc_now())
            return dict(job)

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        fencing_token: int,
        result_ref: str = "",
        cost: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._store as items:
            job = self._get_locked(items, job_id)
            self._assert_lease(job, worker_id, fencing_token)
            job["status"] = JOB_SUCCEEDED
            job["result_ref"] = result_ref or None
            job["cost"] = dict(cost) if cost else None
            job["lease_owner"] = ""
            job["lease_until"] = None
            job["completed_at"] = _iso(utc_now())
            return dict(job)

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        fencing_token: int,
        error: str,
        retryable: bool = True,
    ) -> Dict[str, Any]:
        if not error.strip():
            raise AgentJobError("failure requires an error description")
        with self._store as items:
            job = self._get_locked(items, job_id)
            self._assert_lease(job, worker_id, fencing_token)
            job["error"] = error
            attempts_left = int(job.get("max_attempts") or 3) - int(job.get("attempt") or 0)
            if retryable and attempts_left > 0:
                job["status"] = JOB_QUEUED  # requeue for another claim
                job["lease_owner"] = ""
                job["lease_until"] = None
            else:
                job["status"] = JOB_FAILED
                job["lease_owner"] = ""
                job["lease_until"] = None
                job["completed_at"] = _iso(utc_now())
            return dict(job)

    def mark_awaiting_input(self, job_id: str, *, worker_id: str, fencing_token: int, note: str) -> Dict[str, Any]:
        with self._store as items:
            job = self._get_locked(items, job_id)
            self._assert_lease(job, worker_id, fencing_token)
            job["status"] = JOB_AWAITING_INPUT
            job["awaiting_note"] = note
            job["lease_owner"] = ""
            job["lease_until"] = None
            return dict(job)

    def resume(self, job_id: str, *, actor: str) -> Dict[str, Any]:
        with self._store as items:
            job = self._get_locked(items, job_id)
            if str(job.get("status")) != JOB_AWAITING_INPUT:
                raise AgentJobError(f"cannot resume job in status {job.get('status')}")
            job["status"] = JOB_QUEUED
            job["awaiting_note"] = None
            job["resumed_by"] = actor
            return dict(job)

    def mark_unknown(self, job_id: str, *, worker_id: str, fencing_token: int, note: str) -> Dict[str, Any]:
        with self._store as items:
            job = self._get_locked(items, job_id)
            self._assert_lease(job, worker_id, fencing_token)
            job["status"] = JOB_UNKNOWN
            job["error"] = note
            job["lease_owner"] = ""
            job["lease_until"] = None
            return dict(job)

    def cancel(self, job_id: str, *, actor: str, reason: str) -> Dict[str, Any]:
        if not reason.strip():
            raise AgentJobError("cancellation requires a reason")
        with self._store as items:
            job = self._get_locked(items, job_id)
            if str(job.get("status")) in (JOB_SUCCEEDED, JOB_FAILED, JOB_CANCELLED, JOB_UNKNOWN):
                raise AgentJobError(f"cannot cancel job in terminal status {job.get('status')}")
            job["status"] = JOB_CANCELLED
            job["cancel_reason"] = reason
            job["cancelled_by"] = actor
            job["lease_owner"] = ""
            job["lease_until"] = None
            return dict(job)

    # ---- recovery ----

    def recover_expired_leases(self, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Reclaim jobs whose leases expired without a terminal state.

        Analysis jobs requeue while attempts remain, then fail as
        timed_out. Write jobs become ``unknown`` -- their platform side
        effects must be verified through the existing execution
        verification path, never assumed absent.
        """
        moment = now or utc_now()
        recovered: List[Dict[str, Any]] = []
        with self._store as items:
            for job in items._items:
                if str(job.get("status")) not in LEASE_STATES:
                    continue
                lease_until = _parse(job.get("lease_until"))
                if lease_until is None or lease_until > moment:
                    continue
                job["lease_owner"] = ""
                job["lease_until"] = None
                job["recovery_note"] = f"lease expired at {job.get('lease_until')}"
                if job.get("write_required"):
                    job["status"] = JOB_UNKNOWN
                    job["error"] = "write job lease expired; platform outcome uncertain, verification required"
                elif int(job.get("max_attempts") or 3) - int(job.get("attempt") or 0) > 0:
                    job["status"] = JOB_QUEUED
                else:
                    job["status"] = JOB_TIMED_OUT
                recovered.append(dict(job))
        return recovered

    # ---- reads ----

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda j: str(j.get("record_id")) == job_id)
        return found[0] if found else None

    def list(self, *, status: str = "", role: str = "", account_id: str = "") -> List[Dict[str, Any]]:
        def predicate(job: Mapping[str, Any]) -> bool:
            if status and str(job.get("status")) != status:
                return False
            if role and str(job.get("role")) != role:
                return False
            if account_id and str(job.get("account_id") or "") != account_id:
                return False
            return True

        return self._store.find(predicate)

    def stats(self) -> Dict[str, Any]:
        jobs = self._store.all()
        by_status: Dict[str, int] = {}
        for job in jobs:
            by_status[str(job.get("status"))] = by_status.get(str(job.get("status")), 0) + 1
        return {"total": len(jobs), "by_status": by_status}
