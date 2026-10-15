"""Persistent incremental radar flow (G2A): events -> staged jobs -> cards.

A thin application service that composes the existing stores and services;
it owns no new business facts beyond radar events and stage bookkeeping:

- ``RadarEventStore`` records trigger events (intake confirmation, market
  observation, manual/cost/rules change) idempotently by key+digest, so a
  repeated event for the same input version never re-enqueues paid work.
- ``RadarDispatcher`` advances each pending event through deterministic
  stages (match -> opportunity/economics card), resuming purely from
  persisted job state after restarts. Stage jobs carry idempotency keys
  derived from project+scope+input digest+stage+rule version (plan G2A-2).
- ``RadarExecutor`` claims ONLY radar roles (``roles=`` filter on the
  shared AgentJobStore) and runs deterministic matching/economics without
  any model call; Pi review is submitted only when the intake
  confirmation service explicitly permits external-model use of the
  exact snapshot, otherwise the event honestly parks as awaiting_input.
- One business-writer process: the optional background thread lives
  inside the server's explicit lifecycle (``RADAR_DISPATCHER_ENABLED``,
  default off) and ``tick()`` is also exposed as a manual operation.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .agent_jobs import (
    JOB_AWAITING_INPUT,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_SUCCEEDED,
    JOB_TIMED_OUT,
    JOB_UNKNOWN,
    AgentJobStore,
)
from .commerce.contracts import utc_now
from .commerce.repositories import LockedJsonList
from .opportunity_service import OpportunityError, OpportunityService
from .product_matching import ProductMatchingService

RADAR_RULE_VERSION = "radar-v1"
RADAR_ROLE_MATCH = "radar_match"
RADAR_ROLE_OPPORTUNITY = "radar_opportunity"
RADAR_ROLES = (RADAR_ROLE_MATCH, RADAR_ROLE_OPPORTUNITY)
RADAR_WORKER_ID = "radar-worker"
EVENT_PENDING = "pending"
EVENT_PROCESSED = "processed"
EVENT_AWAITING_INPUT = "awaiting_input"
REMINDER_OPEN = "open"
REMINDER_RESOLVED = "resolved"


class RadarReminderStore:
    """Deduplicated radar reminders (A48).

    A reminder is keyed by (sku, kind, input_digest): repeated triggers of
    the same cause bump ``repeat_count`` instead of creating a second row,
    and it resolves only when the underlying event processes successfully.
    Reminders are observations for the operator -- they never authorize or
    execute anything by themselves.
    """

    def __init__(self, data_dir: Path) -> None:
        self._store = LockedJsonList(Path(data_dir) / "commerce" / "radar_reminders.json")

    def _key(self, sku: str, kind: str, input_digest: str) -> str:
        return hashlib.sha256(f"{sku}\0{kind}\0{input_digest}".encode("utf-8")).hexdigest()[:16]

    def record(self, *, sku: str, kind: str, reason: str, input_digest: str) -> Dict[str, Any]:
        key = self._key(sku, kind, input_digest)

        def mutate(items: LockedJsonList) -> Dict[str, Any]:
            for row in items._items:
                if str(row.get("reminder_key")) == key and str(row.get("status")) == REMINDER_OPEN:
                    row["last_seen_at"] = utc_now().isoformat()
                    row["repeat_count"] = int(row.get("repeat_count") or 0) + 1
                    row["reason"] = reason or row.get("reason")
                    return dict(row)
            row = {
                "record_id": f"rem_{key}",
                "record_type": "radar_reminder",
                "reminder_key": key,
                "sku": sku,
                "kind": kind,
                "input_digest": input_digest,
                "reason": reason,
                "status": REMINDER_OPEN,
                "first_seen_at": utc_now().isoformat(),
                "last_seen_at": utc_now().isoformat(),
                "repeat_count": 0,
                "resolved_at": None,
            }
            items._items.append(row)
            return dict(row)

        with self._store as items:
            return mutate(items)

    def resolve(self, *, sku: str, input_digest: str = "") -> int:
        resolved = 0

        def mutate(items: LockedJsonList) -> None:
            nonlocal resolved
            for row in items._items:
                if str(row.get("status")) != REMINDER_OPEN or str(row.get("sku")) != sku:
                    continue
                if input_digest and str(row.get("input_digest")) != input_digest:
                    continue
                row["status"] = REMINDER_RESOLVED
                row["resolved_at"] = utc_now().isoformat()
                resolved += 1

        with self._store as items:
            mutate(items)
        return resolved

    def open_list(self, *, sku: str = "") -> List[Dict[str, Any]]:
        def predicate(row: Mapping[str, Any]) -> bool:
            if str(row.get("status")) != REMINDER_OPEN:
                return False
            return (not sku) or str(row.get("sku")) == sku

        return self._store.find(predicate)

    def stats(self) -> Dict[str, Any]:
        rows = self._store.all()
        by_status: Dict[str, int] = {}
        for row in rows:
            by_status[str(row.get("status"))] = by_status.get(str(row.get("status")), 0) + 1
        return {"total": len(rows), "by_status": by_status}


class RadarError(ValueError):
    pass


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RadarEventStore:
    def __init__(self, data_dir: Path) -> None:
        self._store = LockedJsonList(Path(data_dir) / "commerce" / "radar_events.json")

    def record(
        self,
        *,
        kind: str,
        scope_sku: str,
        supplier_snapshot_id: str = "",
        market_snapshot_id: str = "",
        project_id: str = "default",
        account_id: str = "",
        input_digest: str = "",
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        if not kind.strip() or not scope_sku.strip():
            raise RadarError("radar event requires kind and scope_sku")
        digest = input_digest.strip() or _digest(
            {"kind": kind, "sku": scope_sku, "supplier": supplier_snapshot_id, "market": market_snapshot_id}
        )
        key = idempotency_key.strip() or f"radar:{project_id}:{account_id}:{scope_sku}:{kind}:{digest}"
        existing = self._store.find(lambda e: str(e.get("idempotency_key")) == key)
        if existing:
            return existing[0]
        event = {
            "record_id": f"evt_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}",
            "record_type": "radar_event",
            "kind": kind,
            "scope_sku": scope_sku,
            "supplier_snapshot_id": supplier_snapshot_id,
            "market_snapshot_id": market_snapshot_id,
            "project_id": project_id,
            "account_id": account_id,
            "input_digest": digest,
            "idempotency_key": key,
            "rule_version": RADAR_RULE_VERSION,
            "status": EVENT_PENDING,
            "created_at": utc_now().isoformat(),
            "processed_at": None,
            "next_check_at": None,
            "note": "",
        }
        return self._store.append(event)

    def pending(self, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        moment = now or utc_now()
        result = []
        for event in self._store.find(lambda e: str(e.get("status")) == EVENT_PENDING):
            nxt = event.get("next_check_at")
            if not nxt:
                result.append(event)
                continue
            try:
                due = datetime.fromisoformat(str(nxt))
            except ValueError:
                result.append(event)
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=moment.tzinfo)
            if due <= moment:
                result.append(event)
        return result

    def update(self, event_id: str, mutate) -> Dict[str, Any]:
        return self._store.update(event_id, mutate)

    def list(self, *, status: str = "") -> List[Dict[str, Any]]:
        return self._store.find(lambda e: (not status) or str(e.get("status")) == status)

    def stats(self) -> Dict[str, Any]:
        events = self._store.all()
        by_status: Dict[str, int] = {}
        for event in events:
            by_status[str(event.get("status"))] = by_status.get(str(event.get("status")), 0) + 1
        return {"total": len(events), "by_status": by_status}


class RadarExecutor:
    """Deterministic stage worker; claims only radar roles, never models."""

    def __init__(self, repos, *, matching: ProductMatchingService, opportunities: OpportunityService) -> None:
        self._repos = repos
        self._matching = matching
        self._opportunities = opportunities

    def execute_claimed(self, jobs: AgentJobStore, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        job = jobs.claim_next(worker_id=RADAR_WORKER_ID, roles=RADAR_ROLES, now=now)
        if job is None:
            return None
        jobs.start(job["record_id"], worker_id=RADAR_WORKER_ID, fencing_token=job["fencing_token"])
        role = str(job.get("role"))
        try:
            if role == RADAR_ROLE_MATCH:
                result_ref = self._run_match(job)
            else:
                result_ref = self._run_opportunity(job)
        except RadarError as exc:
            jobs.mark_awaiting_input(
                job["record_id"], worker_id=RADAR_WORKER_ID, fencing_token=job["fencing_token"], note=str(exc)
            )
            return {"job_id": job["record_id"], "outcome": "awaiting_input", "note": str(exc)}
        jobs.complete(job["record_id"], worker_id=RADAR_WORKER_ID, fencing_token=job["fencing_token"], result_ref=result_ref)
        return {"job_id": job["record_id"], "outcome": "succeeded", "result_ref": result_ref}

    def _run_match(self, job: Mapping[str, Any]) -> str:
        supplier_id = str(job.get("input_refs") and job["input_refs"][0] or "")
        supplier = self._repos.offers.get(supplier_id)
        if supplier is None:
            raise RadarError(f"supplier snapshot missing: {supplier_id}")
        sku = str(supplier.get("source_item_id") or "")
        market_id = ""
        refs = list(job.get("input_refs") or [])
        if len(refs) > 1:
            market_id = str(refs[1])
        if not market_id:
            # any market-side observation provider counts (manual console
            # observations AND public-web observations share the market:
            # prefix); pick the freshest for this SKU
            candidates = [
                record
                for record in self._repos.offers.list()
                if str(record.get("provider_id") or "").startswith("market:")
                and str(record.get("source_sku_id") or "") == sku
            ]
            if not candidates:
                raise RadarError(f"no market observation recorded for SKU {sku}; record one to continue")
            market_id = sorted(candidates, key=lambda r: str(r.get("observed_at") or ""))[-1]["record_id"]
        result = self._matching.match_snapshots(market_id, supplier_id)
        return str(result.get("match_id") or "")

    def _run_opportunity(self, job: Mapping[str, Any]) -> str:
        refs = list(job.get("input_refs") or [])
        match = self._repos.matches.get(str(refs[0])) if refs else None
        if match is None:
            raise RadarError(f"match snapshot missing: {refs[0] if refs else '(none)'}")
        market_id = str(match.get("left_snapshot_id") or "")
        supplier_id = str(match.get("right_snapshot_id") or "")
        market = self._repos.offers.get(market_id)
        supplier = self._repos.offers.get(supplier_id)
        if market is None or supplier is None:
            raise RadarError("match endpoints missing from ledger")
        opportunity = self._opportunities.create_from_match(
            market_snapshot_id=market_id,
            supplier_snapshot_id=supplier_id,
            match_id=str(match["record_id"]),
        )
        # Codex D1: a new supplier snapshot must replace the old evaluation
        # input; the existing opportunity may still reference a stale snapshot
        current_supplier_ids = list(opportunity.get("offer_snapshot_ids") or [])
        if supplier_id not in current_supplier_ids:
            self._repos.opportunities.attach(
                str(opportunity["record_id"]),
                field="offer_snapshot_ids",
                value=[supplier_id],
            )
        result = self._opportunities.evaluate(str(opportunity["record_id"]))
        return str(result["opportunity"]["record_id"])


class RadarDispatcher:
    """Advances events through stages; owns lifecycle but no second writer."""

    def __init__(
        self,
        jobs: AgentJobStore,
        events: RadarEventStore,
        executor: RadarExecutor,
        *,
        eligibility_lookup=None,
        pi_submission=None,
        reminders: Optional[RadarReminderStore] = None,
    ) -> None:
        self._jobs = jobs
        self._events = events
        self._executor = executor
        # eligibility_lookup(snapshot_id) -> {"eligible": bool, "reasons": [...]}
        self._eligibility = eligibility_lookup
        # pi_submission(opportunity_id, idempotency_key) -> job dict or raises
        self._pi_submission = pi_submission
        self._reminders = reminders
        self._lock = threading.RLock()
        self._stopped = False  # only stop() sets True; manual tick() must not be blocked
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ lifecycle

    def start(self, *, interval_seconds: float = 60.0) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False  # one dispatcher per process, explicit restart only
            self._stopped = False
            self._thread = threading.Thread(target=self._loop, args=(interval_seconds,), daemon=True,
                                            name="radar-dispatcher")
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=10)

    def _loop(self, interval_seconds: float) -> None:
        import time

        while not self._stopped:
            try:
                self.tick()
            except Exception:  # keep the loop alive; failures surface via events
                pass
            for _ in range(int(max(interval_seconds, 1.0))):
                if self._stopped:
                    return
                time.sleep(1)

    # ----------------------------------------------------------------- tick

    def tick(self, *, now: Optional[datetime] = None, max_jobs: int = 8) -> Dict[str, Any]:
        with self._lock:
            if self._stopped and self._thread is not None:
                # background mode stopped: manual ticks still allowed
                pass
            recovered = self._jobs.recover_expired_leases(now=now)
            advanced = 0
            executed: List[Dict[str, Any]] = []
            for event in self._events.pending(now=now):
                advanced += self._advance_event(event)
            for _ in range(max_jobs):
                # Codex D6: check stop BEFORE claiming; a tick that already
                # entered must not lease new work once stop() has been called
                if self._stopped:
                    break
                outcome = self._executor.execute_claimed(self._jobs, now=now)
                if outcome is None:
                    break
                executed.append(outcome)
            return {
                "tick_at": (now or utc_now()).isoformat(),
                "leases_recovered": len(recovered),
                "events_advanced": advanced,
                "jobs_executed": executed,
                "events": self._events.stats(),
                "jobs": self._jobs.stats(),
            }

    # -------------------------------------------------------------- stages

    def _job_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        found = self._jobs.list()
        for job in found:
            if str(job.get("idempotency_key")) == key:
                return job
        return None

    def _advance_event(self, event: Mapping[str, Any]) -> int:
        event_id = str(event.get("record_id"))
        sku = str(event.get("scope_sku") or "")
        supplier_id = str(event.get("supplier_snapshot_id") or "")

        def park(kind: str, reason: str) -> None:
            self._events.update(event_id, lambda e: e.update(status=EVENT_AWAITING_INPUT, note=reason))
            if self._reminders is not None:
                self._reminders.record(
                    sku=sku, kind=kind, reason=reason,
                    input_digest=str(event.get("input_digest") or ""),
                )

        if not supplier_id:
            snapshot = self._latest_supplier_snapshot(sku)
            if snapshot is None:
                park("no_supplier_snapshot", "no committed supplier snapshot for SKU")
                return 0
            supplier_id = str(snapshot["record_id"])
            self._events.update(event_id, lambda e: e.update(supplier_snapshot_id=supplier_id))

        match_key = f"{event.get('idempotency_key')}:match"
        match_job = self._job_by_key(match_key)
        if match_job is None:
            self._jobs.submit(
                role=RADAR_ROLE_MATCH,
                goal_id=event_id,
                project_id=str(event.get("project_id") or "default"),
                input_refs=[supplier_id] + ([str(event.get("market_snapshot_id"))] if event.get("market_snapshot_id") else []),
                idempotency_key=match_key,
                priority=3,
            )
            return 1
        status = str(match_job.get("status"))
        if status == JOB_AWAITING_INPUT:
            park("match_awaiting_input", str(match_job.get("awaiting_note") or "match awaiting input"))
            return 0
        if status in (JOB_FAILED, JOB_UNKNOWN, JOB_TIMED_OUT):
            park(
                "match_failed",
                f"match stage ended {status}: {match_job.get('error') or ''}",
            )
            self._events.update(event_id, lambda e: e.update(
                next_check_at=(utc_now() + timedelta(hours=1)).isoformat()))
            return 0
        if status != JOB_SUCCEEDED:
            return 0  # queued/leased/running: resume happens on later ticks

        opportunity_key = f"{event.get('idempotency_key')}:opportunity"
        opportunity_job = self._job_by_key(opportunity_key)
        if opportunity_job is None:
            self._jobs.submit(
                role=RADAR_ROLE_OPPORTUNITY,
                goal_id=event_id,
                project_id=str(event.get("project_id") or "default"),
                input_refs=[str(match_job.get("result_ref") or "")],
                idempotency_key=opportunity_key,
                priority=3,
            )
            return 1
        status = str(opportunity_job.get("status"))
        if status == JOB_AWAITING_INPUT:
            park("opportunity_awaiting_input", str(opportunity_job.get("awaiting_note") or "opportunity awaiting input"))
            return 0
        if status in (JOB_FAILED, JOB_UNKNOWN, JOB_TIMED_OUT):
            park(
                "opportunity_failed",
                f"opportunity stage ended {status}: {opportunity_job.get('error') or ''}",
            )
            self._events.update(event_id, lambda e: e.update(
                next_check_at=(utc_now() + timedelta(hours=1)).isoformat()))
            return 0
        if status != JOB_SUCCEEDED:
            return 0

        opportunity_id = str(opportunity_job.get("result_ref") or "")
        pi_note = self._maybe_submit_pi(event, supplier_id, opportunity_id)
        self._events.update(event_id, lambda e: e.update(
            status=EVENT_PROCESSED,
            processed_at=utc_now().isoformat(),
            note=pi_note))
        # success resolves every open reminder for this SKU (A48)
        if self._reminders is not None:
            self._reminders.resolve(sku=sku)
        return 0

    def _latest_supplier_snapshot(self, sku: str) -> Optional[Dict[str, Any]]:
        candidates = [
            record for record in self._executor._repos.offers.list()  # noqa: SLF001
            if str(record.get("source_item_id") or "") == sku
            and str(record.get("provider_id") or "").startswith("supplier_file")
        ]
        if not candidates:
            return None
        # Codex D2: return the LATEST by observed_at, not the first found
        return max(candidates, key=lambda r: str(r.get("observed_at") or ""))

    def _events_available_suppliers(self) -> List[Dict[str, Any]]:
        return self._executor._repos.offers.list()  # noqa: SLF001 - composition root wiring

    def _maybe_submit_pi(self, event: Mapping[str, Any], supplier_id: str, opportunity_id: str) -> str:
        if self._pi_submission is None:
            return "deterministic card only (no Pi submission channel configured)"
        if self._eligibility is not None:
            outcome = self._eligibility(supplier_id)
            if not outcome.get("eligible"):
                return "Pi review not submitted: external-model use not permitted for this snapshot (" + \
                    ", ".join(outcome.get("reasons") or ["unconfirmed"]) + ")"
        key = f"{event.get('idempotency_key')}:pi-review"
        try:
            self._pi_submission(opportunity_id, key)
        except Exception as exc:
            return f"Pi review submission failed: {exc}"
        return "Pi review submitted (budgeted separately by the Pi runner)"

    # ---------------------------------------------------------------- reads

    def status(self) -> Dict[str, Any]:
        return {
            "rule_version": RADAR_RULE_VERSION,
            "background_thread_alive": bool(self._thread is not None and self._thread.is_alive()),
            "events": self._events.stats(),
            "jobs": self._jobs.stats(),
            "reminders": self._reminders.stats() if self._reminders is not None else None,
            "open_reminders": self._reminders.open_list() if self._reminders is not None else [],
        }


def build_radar(repos, data_dir: Path, *, jobs: Optional[AgentJobStore] = None,
                eligibility_lookup=None, pi_submission=None, reminders: Optional[RadarReminderStore] = None):
    """Composition helper shared by the server and isolated tests.

    Pass the process-wide ``jobs`` store so every writer shares one
    in-memory ledger; a second store instance over the same file would
    silently diverge.
    """
    jobs = jobs or AgentJobStore(Path(data_dir))
    events = RadarEventStore(Path(data_dir))
    executor = RadarExecutor(
        repos,
        matching=ProductMatchingService(repos),
        opportunities=OpportunityService(repos),
    )
    dispatcher = RadarDispatcher(
        jobs, events, executor,
        eligibility_lookup=eligibility_lookup,
        pi_submission=pi_submission,
        reminders=reminders if reminders is not None else RadarReminderStore(Path(data_dir)),
    )
    return dispatcher, events, jobs
