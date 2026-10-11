"""Opt-in, authenticated human reconciliation of unresolved Pi consumption."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import uuid
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .commerce.contracts import utc_now
from .pi_jobs import JOB_TOKEN_HEADER, PiTaskRunner


def _digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ReconciliationError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    kind: Literal["provider_usage", "dispatch_not_sent"]
    reference: str = Field(min_length=3, max_length=256)
    job_id: str = Field(min_length=1, max_length=160)
    attempt: int = Field(ge=1)
    total_tokens: int = Field(ge=0)
    statement: str = Field(min_length=10, max_length=2000)


class ReconciliationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    job_id: str = Field(min_length=1, max_length=160)
    attempt: int = Field(ge=1)
    resolution: Literal["settle", "release"]
    total_tokens: int = Field(ge=0)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{8,128}$")
    reason: str = Field(min_length=10, max_length=1000)
    evidence: Evidence

    @model_validator(mode="after")
    def evidence_matches_proposal(self):
        if (self.evidence.job_id, self.evidence.attempt, self.evidence.total_tokens) != (
            self.job_id, self.attempt, self.total_tokens
        ):
            raise ValueError("evidence must identify the same job, attempt and consumption")
        expected_kind = "provider_usage" if self.resolution == "settle" else "dispatch_not_sent"
        if self.evidence.kind != expected_kind:
            raise ValueError("evidence kind does not support this resolution")
        if self.resolution == "release" and self.total_tokens != 0:
            raise ValueError("release requires evidence of no dispatch and zero consumption")
        return self


class ReconciliationConfirmation(ReconciliationProposal):
    expected_state: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool

    @model_validator(mode="after")
    def explicit_confirmation(self):
        if not self.confirmed:
            raise ValueError("explicit human evidence confirmation is required")
        return self


def _proposal_fields(request: ReconciliationProposal) -> dict:
    return request.model_dump(exclude={"expected_state", "approval_digest", "confirmed"})


class UsageReconciliationService:
    """Shares the runner's execution fence and commits audit with ledger state.

    Credentials are checked by the router. Direct callers are trusted local
    code, not an alternative authentication boundary. JSON storage retains
    the project's single-process constraint.
    """

    def __init__(self, runner: PiTaskRunner):
        self.runner = runner
        self.store = runner.model_usage._store

    @contextmanager
    def _guard(self):
        if not self.runner._execute_lock.acquire(blocking=False):
            raise ReconciliationError("model execution in progress; reconciliation refused")
        try:
            with self.runner._jobs._store._lock, self.runner._decisions._store._lock:
                yield
        finally:
            self.runner._execute_lock.release()

    def unresolved(self) -> dict:
        with self._guard():
            entries = self.store.find(lambda row: row.get("total_tokens") is None)
            for entry in entries:
                job = self.runner._jobs.get(str(entry.get("job_id") or ""))
                entry["reconcilable"] = bool(
                    type(entry.get("attempt")) is int and entry["attempt"] > 0
                    and entry.get("status") in ("pending", "unknown") and job
                    and job.get("status") not in ("leased", "running")
                )
            return {"entries": entries, "total": len(entries),
                    "note": "unknown consumption remains blocking until individually reconciled"}

    def _candidate(self, request: ReconciliationProposal):
        matches = self.store.find(lambda entry: entry.get("job_id") == request.job_id
                                  and entry.get("attempt") == request.attempt)
        if not matches:
            raise ReconciliationError("attempt not found", 404)
        if len(matches) != 1:
            raise ReconciliationError("ambiguous attempt; manual investigation required")
        entry = matches[0]
        if (entry.get("total_tokens") is not None or entry.get("status") not in ("pending", "unknown")
                or entry.get("reconciliation")):
            raise ReconciliationError("only unresolved attempts may be reconciled")
        job = self.runner._jobs.get(request.job_id)
        if job is None:
            raise ReconciliationError("job not found; investigate the orphan ledger entry", 404)
        if job.get("status") in ("leased", "running"):
            raise ReconciliationError("job still leased or running; recover or cancel it before reconciliation")
        if request.resolution == "release":
            decisions = self.runner._decisions.list()
            has_output = any(
                decision.get("source_ref") == f"agent_job:{request.job_id}"
                and (decision.get("input_versions") or {}).get("job_attempt") == request.attempt
                for decision in decisions
            )
            if job.get("result_ref") or entry.get("model") or has_output:
                raise ReconciliationError("observed model output contradicts no-dispatch evidence")
        return entry, job

    def preview(self, request: ReconciliationProposal, operator: str) -> dict:
        with self._guard(), self.store._lock:
            entry, job = self._candidate(request)
            state = _digest({"entry": entry, "job": job})
            approval = _digest({"operator": operator, "proposal": _proposal_fields(request), "state": state})
            return {
                "expected_state": state, "approval_digest": approval,
                "operator": operator, "before": entry,
                "after": {"status": "settled" if request.resolution == "settle" else "released",
                          "total_tokens": request.total_tokens, "business_day": entry.get("business_day")},
                "evidence_sha256": _digest(request.evidence.model_dump()),
                "warning": "Operator must verify evidence authenticity; this API does not contact the provider. "
                           "Reconciliation does not restart jobs, authorize trading, or override other unknown entries.",
            }

    def reconcile(self, request: ReconciliationConfirmation, operator: str) -> dict:
        fingerprint = _digest({"operator": operator, "request": request.model_dump()})
        with self._guard(), self.store._lock:
            receipts = [row["reconciliation"] for row in self.store.all()
                        if (row.get("reconciliation") or {}).get("idempotency_key") == request.idempotency_key]
            if receipts:
                if len(receipts) != 1 or receipts[0].get("request_sha256") != fingerprint:
                    raise ReconciliationError("idempotency key already used with different request or identity")
                return {"receipt": receipts[0], "idempotent": True}
            entry, job = self._candidate(request)
            state = _digest({"entry": entry, "job": job})
            approval = _digest({"operator": operator, "proposal": _proposal_fields(request), "state": state})
            if request.expected_state != state or request.approval_digest != approval:
                raise ReconciliationError("state or proposal changed; obtain and confirm a new preview")
            recorded_at = utc_now().isoformat()
            receipt = {
                "reconciliation_id": f"usage-recon-{uuid.uuid4().hex}",
                "job_id": request.job_id, "attempt": request.attempt,
                "business_day": entry.get("business_day"),
                "operator": operator, "recorded_at": recorded_at,
                "idempotency_key": request.idempotency_key, "request_sha256": fingerprint,
                "expected_state": state, "approval_digest": approval,
                "resolution": request.resolution, "total_tokens": request.total_tokens,
                "reason": request.reason, "before": entry,
                "evidence": request.evidence.model_dump(),
                "evidence_sha256": _digest(request.evidence.model_dump()),
                "verification": "operator-attested; not provider-verified",
            }
            try:
                with self.store as store:
                    current = next(row for row in store._items
                                   if row.get("job_id") == request.job_id and row.get("attempt") == request.attempt)
                    current.update({
                        "status": "settled" if request.resolution == "settle" else "released",
                        "total_tokens": request.total_tokens,
                        "reconciliation": copy.deepcopy(receipt),
                    })
                    timestamp = "settled_at" if request.resolution == "settle" else "released_at"
                    current[timestamp] = recorded_at
            except OSError:
                self.store._load()
                raise
            return {"receipt": receipt, "idempotent": False}


def build_usage_reconciliation_router(runner: PiTaskRunner, *, token_sha256: str = "", operator: str = ""):
    router = APIRouter(prefix="/commerce/pi-usage", tags=["operator usage reconciliation"])
    service = UsageReconciliationService(runner)
    configured_digest = token_sha256.strip().lower()
    configured_operator = operator.strip()

    def authenticate(request: Request) -> str:
        if (not re.fullmatch(r"[0-9a-f]{64}", configured_digest)
                or not configured_operator or len(configured_operator) > 160):
            raise HTTPException(status_code=503, detail="usage reconciliation is disabled")
        if JOB_TOKEN_HEADER.lower() in request.headers:
            raise HTTPException(status_code=403, detail="agent job sessions cannot reconcile consumption")
        scheme, separator, token = request.headers.get("Authorization", "").partition(" ")
        if (scheme.lower() != "bearer" or not separator or not 32 <= len(token) <= 512
                or not hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), configured_digest)):
            raise HTTPException(status_code=401, detail="operator credential required",
                                headers={"WWW-Authenticate": "Bearer"})
        return configured_operator

    def invoke(action, *args):
        try:
            return action(*args)
        except ReconciliationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="ledger storage unavailable; retry with the same idempotency key") from exc

    @router.get("/unresolved")
    def unresolved(identity: str = Depends(authenticate)):
        return invoke(service.unresolved)

    @router.post("/preview")
    def preview(request: ReconciliationProposal, identity: str = Depends(authenticate)):
        return invoke(service.preview, request, identity)

    @router.post("/reconcile")
    def reconcile(request: ReconciliationConfirmation, identity: str = Depends(authenticate)):
        return invoke(service.reconcile, request, identity)

    return router
