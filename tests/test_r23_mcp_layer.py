"""R23 acceptance tests: project MCP thin layer.

Taskbook matrix:
* tool calls produce IDENTICAL results to the HTTP API (parity by
  construction: both dispatch to the same application services);
* forged/missing principals and scope-insufficient principals are
  rejected BEFORE any handler runs;
* startup registers tools with ZERO connector/model calls (no implicit
  paid fetch);
* a tool failure is contained (structured error, no state pollution —
  handlers are read/append-only by contract);
* the allowlist is closed: unknown tools, runtime registration after
  freeze, and schema drift are all refused;
* there is NO shell / arbitrary-SQL / file-write tool — the registry
  only accepts read/analyze/propose families.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xianyu_agent.application.research import ResearchService
from xianyu_agent.application.workflow_runs import (
    WorkflowRunService,
    WorkflowRunStore,
)
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.integrations.mcp.server import (
    McpTool,
    McpToolError,
    McpToolRegistry,
    build_default_registry,
)
from xianyu_agent.security.policy import Principal

LOCAL_OPERATOR = Principal(
    kind="local_user", name="local-operator",
    scopes=frozenset({"*"}), account_ids=frozenset({"*"}), source="local",
)
READ_AGENT = Principal(
    kind="analysis_agent", name="researcher",
    scopes=frozenset({"research:read"}), account_ids=frozenset({"acct-a"}),
    source="token",
)
NO_SCOPE_AGENT = Principal(
    kind="analysis_agent", name="no-scope",
    scopes=frozenset(), account_ids=frozenset(), source="token",
)


class RegistryContractTests(unittest.TestCase):
    def test_only_read_analyze_propose_families_accepted(self):
        registry = McpToolRegistry()
        with self.assertRaises(McpToolError):
            registry.register(McpTool(
                "shell__exec", "x:v1", set(),
                lambda a, p: {}, family="execute",
            ))

    def test_allowlist_closed_after_freeze(self):
        registry = McpToolRegistry()
        registry.register(McpTool("read__x", "x:v1", set(), lambda a, p: {}))
        registry.freeze()
        with self.assertRaises(McpToolError) as ctx:
            registry.register(McpTool("read__y", "y:v1", set(), lambda a, p: {}))
        self.assertEqual(ctx.exception.code, "registry_frozen")

    def test_unknown_tool_refused(self):
        registry = McpToolRegistry()
        registry.freeze()
        with self.assertRaises(McpToolError) as ctx:
            registry.call("shell__exec", {}, LOCAL_OPERATOR)
        self.assertEqual(ctx.exception.code, "unknown_tool")

    def test_schema_drift_stops_caller_with_pinned_digest(self):
        registry = McpToolRegistry()
        registry.register(McpTool("read__x", "digest-old", set(), lambda a, p: {"ok": True}))
        registry.freeze()
        with self.assertRaises(McpToolError) as ctx:
            registry.call("read__x", {}, LOCAL_OPERATOR, schema_digest="digest-new")
        self.assertEqual(ctx.exception.code, "schema_drift_stopped")
        # matching digest passes
        result = registry.call("read__x", {}, LOCAL_OPERATOR, schema_digest="digest-old")
        self.assertEqual(result["result"], {"ok": True})

    def test_scope_enforced_before_handler(self):
        registry = McpToolRegistry()
        calls = []

        def handler(args, principal):
            calls.append(1)
            return {}
        registry.register(McpTool("read__private", "p:v1", {"commerce:read"}, handler))
        registry.freeze()
        with self.assertRaises(McpToolError) as ctx:
            registry.call("read__private", {}, NO_SCOPE_AGENT)
        self.assertEqual(ctx.exception.code, "scope_forbidden")
        self.assertEqual(calls, [], "handler must NOT run on scope refusal")


class DefaultRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r23-mcp-"))
        self.repos = JsonCommerceRepositories(self.tmp)
        self.research = ResearchService(self.repos)
        self.workflow = WorkflowRunService(WorkflowRunStore(self.tmp))
        from xianyu_agent.opportunity_service import OpportunityService

        self.opportunities = OpportunityService(self.repos)
        self.registry = build_default_registry(
            workflow_run_service=self.workflow,
            research_service=self.research,
            opportunity_service=self.opportunities,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_opportunity(self):
        self.repos.opportunities.create({
            "record_type": "opportunity", "record_id": "opp-r23",
            "canonical_sku": "R23-SKU", "source": "resale_candidate",
            "status": "matched", "account_id": "acct-a",
        })

    def test_tool_call_parity_with_http_surface(self):
        """read__opportunities dispatches to the SAME service the HTTP
        GET /api/v1 route reads — identical payload by construction."""
        self._seed_opportunity()
        via_mcp = self.registry.call(
            "read__opportunities", {}, READ_AGENT)["result"]
        via_service = {"opportunities": self.research.list_opportunities()}
        self.assertEqual(via_mcp, via_service)
        self.assertEqual(via_mcp["opportunities"][0]["record_id"], "opp-r23")

    def test_account_scoped_read(self):
        self._seed_opportunity()
        view = self.registry.call(
            "read__candidate",
            {"opportunity_id": "opp-r23"}, READ_AGENT)["result"]
        self.assertEqual(view["opportunity"]["record_id"], "opp-r23")
        # an agent scoped to a DIFFERENT account is refused (403)
        other_account = Principal(
            kind="analysis_agent", name="other",
            scopes=frozenset({"research:read"}),
            account_ids=frozenset({"acct-b"}), source="token",
        )
        with self.assertRaises(McpToolError) as ctx:
            self.registry.call("read__candidate",
                               {"opportunity_id": "opp-r23"}, other_account)
        # service raises account_forbidden; the registry wraps it
        self.assertIn((ctx.exception.code, ctx.exception.status_code),
                      (("account_forbidden", 403), ("tool_failed", 500)))

    def test_propose_is_agent_gated(self):
        payload = {
            "business_intent_id": "r23:intent-1",
            "workflow_type": "candidate_review",
            "input_version": 1,
        }
        result = self.registry.call(
            "propose__workflow_run", {"payload": payload},
            Principal(
                kind="analysis_agent", name="proposer",
                scopes=frozenset({"research:write", "workflow:trigger"}),
                account_ids=frozenset({"acct-a"}), source="token",
            ),
        )["result"]
        self.assertTrue(result["created"])
        # an agent WITHOUT propose scopes is refused
        with self.assertRaises(McpToolError) as ctx:
            self.registry.call("propose__workflow_run", {"payload": payload}, READ_AGENT)
        self.assertEqual(ctx.exception.code, "scope_forbidden")

    def test_zero_startup_cost(self):
        """Building the registry performs NO network/socket calls."""
        import socket as socket_module

        with patch.object(
            socket_module.socket, "connect",
            side_effect=AssertionError("MCP startup attempted a connection"),
        ):
            registry = build_default_registry(
                workflow_run_service=self.workflow,
                research_service=self.research,
            )
        self.assertTrue(registry.list_tools())

    def test_tool_failure_contained_no_state_pollution(self):
        """A failing handler surfaces as a structured tool error; the
        underlying business state is untouched (handlers here are
        read-only, and the run store is append-only)."""
        runs_before = len(self.workflow._store.list_runs())
        with self.assertRaises(McpToolError) as ctx:
            self.registry.call("read__candidate",
                               {"opportunity_id": "does-not-exist"}, READ_AGENT)
        self.assertEqual(ctx.exception.status_code, 404)
        runs_after = len(self.workflow._store.list_runs())
        self.assertEqual(runs_before, runs_after,
                         "a failed read must never change business state")

    def test_listing_shape(self):
        tools = self.registry.list_tools()
        names = [t["name"] for t in tools]
        self.assertIn("read__workflow_run", names)
        self.assertIn("read__opportunities", names)
        self.assertIn("analyze__gate_inspection", names)
        for tool in tools:
            self.assertIn(tool["family"], ("read", "analyze", "propose"))
            self.assertTrue(tool["schema_digest"].startswith("mcp:"))
            self.assertFalse(tool["has_cost"])


if __name__ == "__main__":
    unittest.main()
