"""Authorization policy: principals, scopes, account rules (taskbook R03).

Every entry surface (HTTP UI/n8n, Pi tool sessions, future MCP) resolves
to a :class:`Principal` before any business handler runs. Rules:

* missing credentials never silently become an operator;
* ``local_user`` (loopback operator session) is the only principal kind
  that may hold ``approvals:grant`` — model/worker principals are
  structurally refused the scope, so a model can never approve its own
  proposal ("模型自批" impossible by construction);
* account access is a data boundary: a principal only reaches accounts
  listed in its grant (``*`` only for the local operator);
* approval actors and proposal actors are distinguishable —
  :meth:`Principal.can_approve` refuses token principals whose name
  matches the proposing actor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional

SCOPE_WILDCARD = "*"

PRINCIPAL_KINDS = ("local_user", "n8n_service", "analysis_agent", "worker")

AUDIENCES = ("http", "job")

# Scopes token principals may never hold regardless of configuration:
# approving and admin are human/operator acts.
FORBIDDEN_SCOPES_BY_KIND: Dict[str, FrozenSet[str]] = {
    "analysis_agent": frozenset({"approvals:grant", "security:admin"}),
    "worker": frozenset({"approvals:grant", "security:admin"}),
    "n8n_service": frozenset({"approvals:grant", "security:admin"}),
}

KNOWN_SCOPES = (
    SCOPE_WILDCARD,
    "commerce:read",
    "commerce:write",
    "research:read",
    "research:write",
    "approvals:grant",
    "jobs:run",
    "jobs:claim",
    "jobs:report",
    "workflow:trigger",
    "security:admin",
)


class AuthDecisionError(Exception):
    """Authorization refusal with an HTTP status code attached."""

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


@dataclass(frozen=True)
class Principal:
    kind: str
    name: str
    scopes: FrozenSet[str] = frozenset()
    account_ids: FrozenSet[str] = frozenset()
    audience: str = "http"
    source: str = "token"  # "token" | "local" | "job"
    expires_at: Optional[float] = None

    def has_scope(self, scope: str) -> bool:
        return SCOPE_WILDCARD in self.scopes or scope in self.scopes

    def allows_account(self, account_id: str) -> bool:
        return SCOPE_WILDCARD in self.account_ids or account_id in self.account_ids

    def can_approve(self, proposed_by: str = "") -> bool:
        if not self.has_scope("approvals:grant"):
            return False
        if self.kind != "local_user":
            return False
        # a token principal may never approve under its own proposer name
        if (
            self.source == "token"
            and proposed_by
            and proposed_by == self.name
        ):
            return False
        return True

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "scopes": sorted(self.scopes),
            "account_ids": sorted(self.account_ids),
            "audience": self.audience,
            "source": self.source,
        }


LOCAL_OPERATOR = Principal(
    kind="local_user",
    name="local-operator",
    scopes=frozenset({SCOPE_WILDCARD}),
    account_ids=frozenset({SCOPE_WILDCARD}),
    audience="http",
    source="local",
)

# Pi job-tool sessions: read-only research surface, never operator.
JOB_SESSION = Principal(
    kind="analysis_agent",
    name="pi-job-session",
    scopes=frozenset({"research:read"}),
    account_ids=frozenset(),
    audience="job",
    source="job",
)


class AuthPolicy:
    """Validates token records and answers authorization questions."""

    def validate_token_record(self, record: Mapping[str, Any]) -> None:
        kind = str(record.get("kind") or "")
        if kind not in PRINCIPAL_KINDS or kind == "local_user":
            raise AuthDecisionError(
                400, "token record kind must be one of %s" % (",".join(PRINCIPAL_KINDS[1:]),)
            )
        scopes = frozenset(str(s) for s in (record.get("scopes") or ()))
        forbidden = FORBIDDEN_SCOPES_BY_KIND.get(kind, frozenset()) & scopes
        if forbidden:
            raise AuthDecisionError(
                400,
                "principal kind %r may not hold scopes %s (model self-approval "
                "and admin acts are structurally refused)"
                % (kind, ",".join(sorted(forbidden))),
            )
        unknown = scopes - set(KNOWN_SCOPES)
        if unknown:
            raise AuthDecisionError(400, "unknown scopes: %s" % ",".join(sorted(unknown)))
        audience = str(record.get("audience") or "http")
        if audience not in AUDIENCES:
            raise AuthDecisionError(400, "unknown audience %r" % audience)
        if not str(record.get("name") or "").strip():
            raise AuthDecisionError(400, "token record requires a name")

    def authorize(
        self,
        principal: Principal,
        *,
        scope: str = "",
        account_id: str = "",
        proposed_by: str = "",
    ) -> None:
        if scope and not principal.has_scope(scope):
            raise AuthDecisionError(
                403, "principal %r lacks scope %r" % (principal.name, scope)
            )
        if account_id and not principal.allows_account(account_id):
            raise AuthDecisionError(
                403,
                "principal %r is not authorized for account %r" % (principal.name, account_id),
            )
        if scope == "approvals:grant" and not principal.can_approve(proposed_by):
            raise AuthDecisionError(
                403,
                "principal %r may not approve (proposer=%r)"
                % (principal.name, proposed_by or "?"),
            )
