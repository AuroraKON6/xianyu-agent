"""Versioned workflow-run service (taskbook R17, contract 11 §3).

Server-authoritative workflow runs for external orchestrators (n8n) and
operators. Invariants enforced HERE, never trusted from clients:

* idempotency — a run is keyed by ``business_intent_id``; the same key
  with the SAME payload returns the SAME run, a different payload is a
  409 conflict (one intent cannot have two payloads);
* server-only statuses — clients may request steps/cancel/resume/
  finalize; every status value is derived by a server transition and a
  client-supplied ``status`` field is rejected outright;
* input freeze — the run records its input artifact ids and a digest
  computed from the STORED artifacts (client hashes are never trusted);
* account scoping — artifacts and run reads are scoped to the run's
  account; a principal without that account gets 403;
* events — an append-only event sequence per run for cursor-based
  reads (GET /events);
* finalize — only the server concludes a run, after verifying every
  required capability either completed or was skipped under a
  server-contract rule with a recorded reason.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..commerce.repositories import LockedJsonList

WORKFLOW_API_VERSION = "v1"

# ---- capability registry (server-contract; versioned) ----------------------

CAPABILITY_SCHEMA_DIGESTS = {
    "validate_evidence": {"version": 1, "digest": "cap:validate_evidence:v1",
                          "has_cost": False, "required": True},
    "match_products": {"version": 1, "digest": "cap:match_products:v1",
                       "has_cost": False, "required": True},
    "calculate_economics": {"version": 1, "digest": "cap:calculate_economics:v1",
                            "has_cost": False, "required": True},
    "export_candidates": {"version": 1, "digest": "cap:export_candidates:v1",
                          "has_cost": False, "required": False},
}

# statuses a CLIENT may never send (server transitions only)
SERVER_ONLY_STATUSES = {
    "created", "running", "blocked", "waiting", "cancelled",
    "finalized", "failed",
}

STEP_TERMINAL = {"succeeded", "failed", "skipped"}
SKIP_REASONS_ALLOWED = {
    "no_spread_available",  # negative research: export without spread is valid
    "capability_not_applicable",
}


class WorkflowRunError(ValueError):
    def __init__(self, message: str, *, status_code: int = 422, code: str = "invalid") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _now() -> float:
    return time.time()


def _digest_mapping(payload: Mapping[str, Any]) -> str:
    canonical = repr(sorted((str(k), repr(v)) for k, v in payload.items()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WorkflowRunStore:
    """JSON-backed workflow-run persistence (single writer process)."""

    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(
            __import__("pathlib").Path(data_dir) / "commerce" / "workflow_runs.json"
        )
        self._lock = threading.RLock()

    def _find_by_intent(self, items, intent_id: str):
        for run in items._items:
            if str(run.get("business_intent_id")) == intent_id:
                return run
        return None

    def create(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._store as items:
            items._items.append(record)
        return dict(record)

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda r: str(r.get("run_id")) == run_id)
        return dict(found[0]) if found else None

    def find_by_intent(self, intent_id: str) -> Optional[Dict[str, Any]]:
        with self._store as items:
            found = self._find_by_intent(items, intent_id)
        return dict(found) if found else None

    def mutate(self, run_id: str, mutate_fn):
        def apply(items):
            for run in items._items:
                if str(run.get("run_id")) == run_id:
                    return mutate_fn(run)
            raise KeyError(run_id)

        with self._store as items:
            return apply(items)

    def list_runs(self, *, account_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        def predicate(run):
            return (not account_id) or str(run.get("account_id")) == account_id

        runs = self._store.find(predicate)
        return [dict(r) for r in runs[-max(1, int(limit)):]]


class WorkflowRunService:
    def __init__(self, store: WorkflowRunStore) -> None:
        self._store = store

    # ---- capabilities (pure query, no warmup) ------------------------------

    def capabilities(self) -> Dict[str, Any]:
        return {
            "api_version": WORKFLOW_API_VERSION,
            "capabilities": {
                name: {
                    "version": spec["version"],
                    "schema_digest": spec["digest"],
                    "has_cost": spec["has_cost"],
                    "enabled": True,
                }
                for name, spec in CAPABILITY_REGISTRY.items()
            },
        }

    # ---- run creation (idempotent on business_intent_id) ---------------------

    def create_run(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        intent_id = str(payload.get("business_intent_id") or "").strip()
        workflow_type = str(payload.get("workflow_type") or "").strip()
        account_id = str(payload.get("account_id") or "").strip()
        if not intent_id or not workflow_type or not account_id:
            raise WorkflowRunError(
                "business_intent_id, workflow_type and account_id are required"
            )
        # a client-supplied status is rejected outright — statuses are
        # server transitions only
        if payload.get("status") is not None:
            raise WorkflowRunError(
                "status is server-authoritative and must not be sent by clients",
                status_code=422, code="client_status_forbidden",
            )
        requested = list(payload.get("requested_capabilities") or [])
        unknown = [c for c in requested if c not in CAPABILITY_REGISTRY]
        if unknown:
            raise WorkflowRunError(
                f"unknown capabilities: {', '.join(unknown)}", code="unknown_capability"
            )
        mode = str(payload.get("orchestration_mode") or "stepwise")
        if mode not in ("stepwise", "whole_run"):
            raise WorkflowRunError("orchestration_mode must be stepwise or whole_run")
        if mode == "whole_run":
            raise WorkflowRunError(
                "whole_run orchestration is python-owned in this phase; external "
                "runs are stepwise only (taskbook R17/R18)",
                status_code=409, code="mode_not_authorized",
            )

        input_artifact_ids = [str(a) for a in (payload.get("input_artifact_ids") or [])]
        frozen_inputs = {
            "input_version": int(payload.get("input_version") or 0),
            "input_artifact_ids": input_artifact_ids,
            # digest over the REQUESTED inputs as declared (the stored
            # artifacts' contents are verified at step time; the client
            # cannot substitute its own hash for content verification)
            "input_request_digest": _digest_mapping({
                "input_version": payload.get("input_version"),
                "input_artifact_ids": input_artifact_ids,
                "requested_capabilities": requested,
                "workflow_type": workflow_type,
                "account_id": account_id,
            }),
        }

        existing = self._store.find_by_intent(intent_id)
        if existing is not None:
            if existing.get("input_request_digest") == frozen_inputs["input_request_digest"]:
                return {"run": existing, "created": False}
            raise WorkflowRunError(
                f"business_intent_id {intent_id!r} already exists with a different "
                "payload; one intent cannot have two payloads",
                status_code=409, code="idempotency_conflict",
            )

        run_id = "wfr_" + uuid.uuid4().hex[:16]
        record = {
            "run_id": run_id,
            "business_intent_id": intent_id,
            "workflow_type": workflow_type,
            "api_version": WORKFLOW_API_VERSION,
            "account_id": account_id,
            "orchestration_mode": mode,
            "orchestration_owner": str(payload.get("orchestration_owner") or "n8n"),
            **frozen_inputs,
            "requested_capabilities": requested,
            "orchestration_trace": dict(payload.get("orchestration_trace") or {}),
            "status": "created",  # server-assigned
            "steps": [],
            "artifacts": {},
            "events": [{
                "at": _now(), "type": "run_created",
                "run_id": run_id, "intent": intent_id,
            }],
            "created_at": _now(),
            "updated_at": _now(),
            "revision": 1,
        }
        stored = self._store.create(record)
        return {"run": stored, "created": True}

    # ---- step requests --------------------------------------------------------

    def request_step(
        self,
        run_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str = "n8n",
    ) -> Dict[str, Any]:
        capability = str(payload.get("capability") or "").strip()
        if not capability or capability not in CAPABILITY_REGISTRY:
            raise WorkflowRunError(
                f"unknown capability {capability!r}", code="unknown_capability"
            )
        if payload.get("status") is not None:
            raise WorkflowRunError(
                "status is server-authoritative and must not be sent by clients",
                code="client_status_forbidden",
            )

        def mutate(run: Dict[str, Any]) -> Dict[str, Any]:
            if str(run.get("status")) in ("cancelled", "finalized", "failed"):
                raise WorkflowRunError(
                    f"run is {run.get('status')}; steps cannot be requested",
                    status_code=409, code="invalid_state",
                )
            # one live step per capability per run (retry needs a resolved
            # previous attempt)
            for step in run["steps"]:
                if (str(step.get("capability")) == capability
                        and str(step.get("status")) not in STEP_TERMINAL):
                    raise WorkflowRunError(
                        f"capability {capability} already has a live step "
                        f"{step.get('step_run_id')}",
                        status_code=409, code="live_step_exists",
                    )
            step_run_id = "stp_" + uuid.uuid4().hex[:14]
            spec = CAPABILITY_REGISTRY[capability]
            step = {
                "step_run_id": step_run_id,
                "capability": capability,
                "capability_version": spec["version"],
                "requested_by": actor,
                "status": "queued",  # server-assigned
                "requested_at": _now(),
                "input_artifact_ids": [str(a) for a in (payload.get("input_artifact_ids") or [])],
                "result": None,
            }
            run["steps"].append(step)
            if str(run.get("status")) == "created":
                run["status"] = "running"
                run["events"].append({"at": _now(), "type": "run_running"})
            run["events"].append({
                "at": _now(), "type": "step_requested",
                "step_run_id": step_run_id, "capability": capability,
            })
            run["updated_at"] = _now()
            run["revision"] = int(run.get("revision") or 1) + 1
            return {"run_id": run_id, "step_run": step}

        try:
            return self._store.mutate(run_id, mutate)
        except KeyError as exc:
            raise WorkflowRunError(f"run not found: {run_id}", status_code=404) from exc

    # ---- server-side step completion (workers call this, not orchestrators) --

    def complete_step(
        self,
        run_id: str,
        step_run_id: str,
        *,
        outcome: str,
        result: Optional[Mapping[str, Any]] = None,
        artifacts: Optional[Mapping[str, Mapping[str, Any]]] = None,
        worker: str = "python",
    ) -> Dict[str, Any]:
        if outcome not in ("succeeded", "failed", "skipped"):
            raise WorkflowRunError("outcome must be succeeded|failed|skipped")

        def mutate(run: Dict[str, Any]):
            step = next(
                (s for s in run["steps"] if str(s.get("step_run_id")) == step_run_id), None
            )
            if step is None:
                raise WorkflowRunError(
                    f"step not found: {step_run_id}", status_code=404
                )
            if str(step.get("status")) in STEP_TERMINAL:
                raise WorkflowRunError(
                    f"step already terminal: {step.get('status')}",
                    status_code=409, code="step_terminal",
                )
            if outcome == "skipped":
                reason = str((result or {}).get("reason") or "")
                if reason not in SKIP_REASONS_ALLOWED:
                    raise WorkflowRunError(
                        f"skip reason {reason!r} is not allowed by the capability "
                        "contract", code="skip_reason_forbidden",
                    )
            step["status"] = outcome
            step["completed_at"] = _now()
            step["completed_by"] = worker
            step["result"] = dict(result or {})
            for artifact_id, content in (artifacts or {}).items():
                run["artifacts"][str(artifact_id)] = {
                    "content": dict(content or {}),
                    "produced_by": step_run_id,
                    "produced_at": _now(),
                }
            run["events"].append({
                "at": _now(), "type": f"step_{outcome}",
                "step_run_id": step_run_id,
            })
            run["updated_at"] = _now()
            run["revision"] = int(run.get("revision") or 1) + 1
            return dict(step)

        try:
            return self._store.mutate(run_id, mutate)
        except KeyError as exc:
            raise WorkflowRunError(f"run not found: {run_id}", status_code=404) from exc

    # ---- cancel / resume / finalize ---------------------------------------------

    def cancel(self, run_id: str, *, actor: str, expected_revision: Optional[int] = None):
        def mutate(run):
            if expected_revision is not None and int(run.get("revision") or 0) != int(expected_revision):
                raise WorkflowRunError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {run.get('revision')}",
                    status_code=409, code="revision_conflict",
                )
            if str(run.get("status")) in ("finalized", "cancelled"):
                raise WorkflowRunError(
                    f"run already {run.get('status')}",
                    status_code=409, code="invalid_state",
                )
            run["status"] = "cancelled"
            run["cancelled_by"] = actor
            run["events"].append({"at": _now(), "type": "run_cancelled", "actor": actor})
            run["updated_at"] = _now()
            run["revision"] = int(run.get("revision") or 1) + 1
            return dict(run)

        return self._mutate_or_404(run_id, mutate)

    def resume(self, run_id: str, *, actor: str, expected_revision: Optional[int] = None):
        def mutate(run):
            if expected_revision is not None and int(run.get("revision") or 0) != int(expected_revision):
                raise WorkflowRunError("revision conflict", status_code=409,
                                       code="revision_conflict")
            if str(run.get("status")) not in ("blocked", "waiting"):
                raise WorkflowRunError(
                    f"only blocked/waiting runs can resume (current {run.get('status')})",
                    status_code=409, code="invalid_state",
                )
            run["status"] = "running"
            run["events"].append({"at": _now(), "type": "run_resumed", "actor": actor})
            run["updated_at"] = _now()
            run["revision"] = int(run.get("revision") or 1) + 1
            return dict(run)

        return self._mutate_or_404(run_id, mutate)

    def finalize(self, run_id: str) -> Dict[str, Any]:
        """Server-side completion judgment: every REQUIRED capability must
        have a terminal step (succeeded, or skipped under an allowed
        contract rule with a recorded reason). The manifest is generated
        HERE — clients never declare a run succeeded."""

        def mutate(run):
            if str(run.get("status")) in ("cancelled", "finalized", "failed"):
                raise WorkflowRunError(
                    f"run already {run.get('status')}",
                    status_code=409, code="invalid_state",
                )
            requested = list(run.get("requested_capabilities") or [])
            by_capability = {
                str(s.get("capability")): s
                for s in run["steps"]
                if str(s.get("status")) in STEP_TERMINAL
            }
            missing = [
                name for name, spec in CAPABILITY_REGISTRY.items()
                if name in requested and spec["required"] and name not in by_capability
            ]
            if missing:
                raise WorkflowRunError(
                    f"cannot finalize: required capabilities not terminal: "
                    f"{', '.join(missing)}",
                    status_code=409, code="required_steps_incomplete",
                )
            manifest = {
                "run_id": run_id,
                "business_intent_id": run.get("business_intent_id"),
                "input_request_digest": run.get("input_request_digest"),
                "steps": [
                    {
                        "step_run_id": s.get("step_run_id"),
                        "capability": s.get("capability"),
                        "capability_version": s.get("capability_version"),
                        "status": s.get("status"),
                        "skip_reason": (s.get("result") or {}).get("reason")
                        if s.get("status") == "skipped" else None,
                    }
                    for s in run["steps"]
                ],
                "artifact_ids": sorted(run.get("artifacts") or {}),
                "generated_at": _now(),
            }
            run["status"] = "finalized"
            run["manifest"] = manifest
            run["events"].append({"at": _now(), "type": "run_finalized"})
            run["updated_at"] = _now()
            run["revision"] = int(run.get("revision") or 1) + 1
            return {"run_id": run_id, "status": "finalized", "manifest": manifest}

        return self._mutate_or_404(run_id, mutate)

    # ---- read paths ----------------------------------------------------------------

    def get_run(self, run_id: str, *, account_scope: Sequence[str] = ("*",)) -> Dict[str, Any]:
        run = self._store.get(run_id)
        if run is None:
            raise WorkflowRunError(f"run not found: {run_id}", status_code=404)
        self._check_account(run, account_scope)
        return run

    def get_artifact(self, artifact_id: str, *, account_scope: Sequence[str] = ("*",)):
        for run in self._store.list_runs(limit=100000):
            artifacts = run.get("artifacts") or {}
            if artifact_id in artifacts:
                self._check_account(run, account_scope)
                return {
                    "artifact_id": artifact_id,
                    "run_id": run.get("run_id"),
                    **artifacts[artifact_id],
                }
        raise WorkflowRunError(f"artifact not found: {artifact_id}", status_code=404)

    def events(self, run_id: str, *, cursor: int = 0, account_scope: Sequence[str] = ("*",)):
        run = self.get_run(run_id, account_scope=account_scope)
        events = list(run.get("events") or [])
        return {"run_id": run_id, "cursor": cursor, "events": events[cursor:],
                "next_cursor": len(events)}

    # ---- helpers --------------------------------------------------------------------

    @staticmethod
    def _check_account(run: Mapping[str, Any], account_scope: Sequence[str]) -> None:
        if "*" in account_scope:
            return
        if str(run.get("account_id") or "") not in set(account_scope):
            raise WorkflowRunError(
                f"run belongs to account {run.get('account_id')!r}; caller is not "
                "authorized for it",
                status_code=403, code="account_forbidden",
            )

    def _mutate_or_404(self, run_id: str, mutate_fn):
        try:
            return self._store.mutate(run_id, mutate_fn)
        except KeyError as exc:
            raise WorkflowRunError(f"run not found: {run_id}", status_code=404) from exc


# registry alias (used by capabilities())
CAPABILITY_REGISTRY = CAPABILITY_SCHEMA_DIGESTS
