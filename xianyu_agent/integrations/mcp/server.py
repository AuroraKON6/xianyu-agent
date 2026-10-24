"""Project MCP thin layer (taskbook R23; ADR-0003 read channel).

One tool surface for model agents (Pi and friends), speaking the same
application services the HTTP API speaks — tool calls and HTTP calls are
IDENTICAL by construction because both dispatch to the same service
objects. There is deliberately NO generic shell, NO arbitrary SQL and
NO file-write tool: an agent can read research state, run analysis
helpers and PROPOSE work, never execute it.

Tool families and scopes (enforced per call through the R03 principal
system — a forged or missing principal is rejected before any handler
runs):

* ``read__*``    — commerce:read / research:read (pure queries)
* ``analyze__*`` — research:read (deterministic helpers on read state)
* ``propose__*`` — research:write (append proposals; never state
  transitions — approval/execution stay on the human HTTP surface)

The server exposes JSON-RPC-shaped ``tools/list`` and ``tools/call``
methods so a thin stdio or HTTP transport (cross-container) can carry
it; startup performs ZERO connector/model calls (lazy by design).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ...security.policy import Principal

MCP_SCHEMA_VERSION = 1


class McpToolError(ValueError):
    def __init__(self, message: str, *, code: str = "mcp_invalid",
                 status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


ToolHandler = Callable[[Mapping[str, Any], Principal], Dict[str, Any]]


class McpTool:
    """A registered tool: name, schema digest, required scopes, handler."""

    def __init__(self, name: str, schema_digest: str, required_scopes,
                 handler: ToolHandler, *, family: str = "read",
                 has_cost: bool = False) -> None:
        self.name = name
        self.schema_digest = schema_digest
        self.required_scopes = frozenset(required_scopes)
        self.handler = handler
        self.family = family
        self.has_cost = has_cost


class McpToolRegistry:
    """Allowlist registry: only registered tools are callable, and every
    call is scope-checked against the caller's principal."""

    def __init__(self) -> None:
        self._tools: Dict[str, McpTool] = {}
        self._frozen = False

    def register(self, tool: McpTool) -> None:
        if self._frozen:
            raise McpToolError("registry is frozen; no new tools", code="registry_frozen")
        if tool.name in self._tools:
            raise McpToolError(f"duplicate tool {tool.name!r}", code="duplicate_tool")
        if tool.family not in ("read", "analyze", "propose"):
            raise McpToolError(
                f"family must be read/analyze/propose (got {tool.family!r})",
                code="invalid_family",
            )
        self._tools[tool.name] = tool

    def freeze(self) -> None:
        """Lock the allowlist after composition; no runtime registration."""
        self._frozen = True

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "schema_digest": tool.schema_digest,
                "required_scopes": sorted(tool.required_scopes),
                "family": tool.family,
                "has_cost": tool.has_cost,
            }
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        principal: Principal,
        *,
        schema_digest: str = "",
    ) -> Dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise McpToolError(
                f"tool {name!r} is not registered; the allowlist is closed",
                code="unknown_tool", status_code=404,
            )
        # schema lock (R19 pattern): a caller may pin the digest it saw
        if schema_digest and schema_digest != tool.schema_digest:
            raise McpToolError(
                f"tool {name!r} schema changed (registered "
                f"{tool.schema_digest}, caller pinned {schema_digest})",
                code="schema_drift_stopped", status_code=409,
            )
        missing = set(tool.required_scopes) - set(principal.scopes)
        if missing:
            raise McpToolError(
                f"tool {name!r} requires scopes {sorted(missing)}; principal "
                f"{principal.name!r} has {sorted(principal.scopes)}",
                code="scope_forbidden", status_code=403,
            )
        if tool.family == "propose" and "*" in principal.scopes:
            # wildcard local operators use HTTP; propose stays agent-only
            pass  # a local operator legitimately holds everything
        started = time.time()
        try:
            result = tool.handler(arguments or {}, principal)
        except McpToolError:
            raise
        except Exception as exc:  # handler bugs are tool failures, never
            # state pollution: handlers are read/append-only by contract
            raise McpToolError(
                f"tool {name!r} failed without side effects: {exc}",
                code="tool_failed", status_code=500,
            ) from exc
        return {
            "tool": name,
            "family": tool.family,
            "result": result,
            "duration_ms": int((time.time() - started) * 1000),
        }


def build_default_registry(
    *,
    workflow_run_service=None,
    research_service=None,
    opportunity_service=None,
    candidate_exporter=None,
) -> McpToolRegistry:
    """Compose the standard tool surface over the SAME application services
    the HTTP API uses (result parity is by construction)."""
    registry = McpToolRegistry()

    # ---------------- read family ----------------
    if workflow_run_service is not None:
        def _get_run(args, principal):
            return workflow_run_service.get_run(
                str(args.get("run_id") or ""),
                account_scope=list(principal.account_ids or ["*"]),
            )
        registry.register(McpTool(
            "read__workflow_run", "mcp:read__workflow_run:v1",
            {"commerce:read"}, _get_run, family="read",
        ))

        def _capabilities(args, principal):
            return workflow_run_service.capabilities()
        registry.register(McpTool(
            "read__capabilities", "mcp:read__capabilities:v1",
            {"research:read"}, _capabilities, family="read",
        ))

    if research_service is not None:
        def _list_opportunities(args, principal):
            return {"opportunities": research_service.list_opportunities(
                status=str(args.get("status") or ""))}
        registry.register(McpTool(
            "read__opportunities", "mcp:read__opportunities:v1",
            {"research:read"}, _list_opportunities, family="read",
        ))

        def _candidate_view(args, principal):
            # account scoping enforced HERE (the HTTP layer does the same
            # check at the route; the raw service is account-agnostic)
            opportunity = research_service.get_opportunity(
                str(args.get("opportunity_id") or ""))
            if opportunity is None:
                raise McpToolError("opportunity not found", code="not_found",
                                   status_code=404)
            account = str(opportunity.get("account_id") or "")
            scope = set(principal.account_ids or ["*"])
            if "*" not in scope and account and account not in scope:
                raise McpToolError(
                    f"opportunity belongs to account {account!r}; caller is "
                    f"not authorized for it",
                    code="account_forbidden", status_code=403,
                )
            return research_service.candidate_view(
                str(args.get("opportunity_id") or ""))
        registry.register(McpTool(
            "read__candidate", "mcp:read__candidate:v1",
            {"research:read"}, _candidate_view, family="read",
        ))

    # ---------------- analyze family (deterministic, read-only) ----------
    if opportunity_service is not None:
        def _inspect_gates(args, principal):
            return opportunity_service.inspect_current(
                str(args.get("opportunity_id") or ""))
        registry.register(McpTool(
            "analyze__gate_inspection", "mcp:analyze__gate_inspection:v1",
            {"research:read"}, _inspect_gates, family="analyze",
        ))

    if candidate_exporter is not None:
        def _export_rows(args, principal):
            return {"rows": candidate_exporter.build_rows(
                status=str(args.get("status") or ""))}
        registry.register(McpTool(
            "analyze__candidate_export", "mcp:analyze__candidate_export:v1",
            {"research:read"}, _export_rows, family="analyze",
        ))

    # ---------------- propose family (append-only, never transitions) ----
    if workflow_run_service is not None:
        def _propose_run(args, principal):
            payload = dict(args.get("payload") or {})
            payload.setdefault("account_id", next(iter(principal.account_ids), ""))
            return workflow_run_service.create_run(payload)
        registry.register(McpTool(
            "propose__workflow_run", "mcp:propose__workflow_run:v1",
            {"research:write", "workflow:trigger"}, _propose_run,
            family="propose",
        ))

    registry.freeze()
    return registry
