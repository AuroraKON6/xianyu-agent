"""FastAPI dependencies for scope/account enforcement (taskbook R03).

Routes pull these in via ``Depends``; the auth middleware guarantees
``request.state.principal`` exists before handlers run. Dependencies
translate :class:`AuthDecisionError` into HTTP 401/403.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from ..security.policy import AuthDecisionError, AuthPolicy, Principal

_policy = AuthPolicy()


def _raise_http(exc: AuthDecisionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.reason)


def get_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="no authenticated principal on request")
    return principal


def _make_scope_dependency(scope: str):
    def dependency(request: Request) -> Principal:
        principal = get_principal(request)
        try:
            _policy.authorize(principal, scope=scope)
        except AuthDecisionError as exc:
            raise _raise_http(exc) from exc
        return principal

    dependency.__name__ = "require_%s" % scope.replace(":", "_").replace("*", "all")
    return dependency


require_research_read = _make_scope_dependency("research:read")
require_commerce_read = _make_scope_dependency("commerce:read")
require_commerce_write = _make_scope_dependency("commerce:write")
require_approvals_grant = _make_scope_dependency("approvals:grant")
require_jobs_run = _make_scope_dependency("jobs:run")
require_security_admin = _make_scope_dependency("security:admin")


def require_account_access(principal: Principal, account_id: str) -> Principal:
    try:
        _policy.authorize(principal, account_id=account_id)
    except AuthDecisionError as exc:
        raise _raise_http(exc) from exc
    return principal


def guard_approval(principal: Principal, *, proposed_by: str = "") -> None:
    """Approvals: scope + human-actor + distinct-proposer checks in one."""
    try:
        _policy.authorize(principal, scope="approvals:grant", proposed_by=proposed_by)
    except AuthDecisionError as exc:
        raise _raise_http(exc) from exc
