"""Versioned workflow API v1 (taskbook R17; contract: contracts/workflow-api.v1.yaml).

External orchestrators (n8n) and operators call this surface. Auth goes
through the R03 layer: run/artifact reads are account-scoped by the
caller's principal; writes require the workflow:trigger scope.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

from ...application.workflow_runs import WorkflowRunError
from ..dependencies import get_principal

router = APIRouter(prefix="/api/v1", tags=["workflow-api-v1"])

_service = None  # composition root injects the shared service


def configure_workflow_api(service) -> None:
    global _service
    _service = service


def _service_or_503():
    if _service is None:
        raise HTTPException(status_code=503, detail="workflow API not configured")
    return _service


def _wrap(exc: WorkflowRunError) -> HTTPException:
    return HTTPException(status_code=exc.status_code,
                         detail={"code": exc.code, "message": str(exc)})


def _account_scope(principal) -> list:
    return list(principal.account_ids or ["*"])


@router.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    """Pure query: versioned capability registry (no warmup, no cost)."""
    return _service_or_503().capabilities()


@router.post("/workflow-runs", status_code=202)
def create_workflow_run(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Create a run keyed by business_intent_id (idempotent).

    202 accepted (queued intent), never an external-action completion.
    Same intent + same payload -> same run; different payload -> 409.
    """
    service = _service_or_503()
    try:
        result = service.create_run(payload)
        return {
            "run_id": result["run"]["run_id"],
            "status": result["run"]["status"],
            "created": result["created"],
            "run": result["run"],
        }
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc


@router.get("/workflow-runs/{run_id}")
def get_workflow_run(run_id: str, principal=Depends(get_principal)) -> Dict[str, Any]:
    service = _service_or_503()
    try:
        return service.get_run(run_id, account_scope=_account_scope(principal))
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc


@router.post("/workflow-runs/{run_id}/steps", status_code=202)
def request_step(
    run_id: str,
    payload: Dict[str, Any] = Body(...),
    principal=Depends(get_principal),
) -> Dict[str, Any]:
    service = _service_or_503()
    if principal.source == "token" and not principal.has_scope("workflow:trigger"):
        raise HTTPException(
            status_code=403,
            detail={"code": "scope_forbidden",
                    "message": "requesting steps requires the workflow:trigger scope"},
        )
    try:
        run = service.get_run(run_id, account_scope=_account_scope(principal))
        return service.request_step(run["run_id"], payload, actor=principal.name)
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc


@router.post("/workflow-runs/{run_id}/cancel")
def cancel_workflow_run(
    run_id: str,
    payload: Dict[str, Any] = Body(default={}),
    principal=Depends(get_principal),
):
    service = _service_or_503()
    try:
        run = service.get_run(run_id, account_scope=_account_scope(principal))
        return service.cancel(
            run["run_id"],
            actor=principal.name,
            expected_revision=payload.get("expected_revision"),
        )
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc


@router.post("/workflow-runs/{run_id}/resume")
def resume_workflow_run(
    run_id: str,
    payload: Dict[str, Any] = Body(default={}),
    principal=Depends(get_principal),
):
    service = _service_or_503()
    try:
        run = service.get_run(run_id, account_scope=_account_scope(principal))
        return service.resume(
            run["run_id"],
            actor=principal.name,
            expected_revision=payload.get("expected_revision"),
        )
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc


@router.post("/workflow-runs/{run_id}/finalize")
def finalize_workflow_run(run_id: str, principal=Depends(get_principal)):
    service = _service_or_503()
    try:
        run = service.get_run(run_id, account_scope=_account_scope(principal))
        return service.finalize(run["run_id"])
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc


@router.get("/workflow-runs/{run_id}/events")
def workflow_run_events(
    run_id: str, cursor: int = 0, principal=Depends(get_principal)
):
    service = _service_or_503()
    try:
        return service.events(run_id, cursor=cursor,
                              account_scope=_account_scope(principal))
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, principal=Depends(get_principal)):
    service = _service_or_503()
    try:
        return service.get_artifact(artifact_id, account_scope=_account_scope(principal))
    except WorkflowRunError as exc:
        raise _wrap(exc) from exc
