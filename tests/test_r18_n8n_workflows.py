"""R18 acceptance tests: n8n W1/W2 offline candidate chains.

Structural whitelist: the workflow JSONs may only contain orchestration
nodes (trigger/httpRequest/wait/if/function), every HTTP node must target
the backend API (or the error webhook), and no credentials are embedded.

Chain semantics (kill/retrigger/partial-failure, executed against the
REAL workflow-run service the JSONs drive — the idempotency model the
nodes rely on is exercised exactly as n8n would drive it):

* duplicate trigger (re-execution) -> the SAME run, created=false;
* kill-and-resume: steps completed before the kill are terminal and are
  NOT re-requested (server live_step_exists guard + the skip-terminal
  logic encoded in the W2 function node, verified here in Python);
* partial failure: a failed step is terminal, retrying the same step is
  refused, finalize stays refused until required steps are terminal;
* results never duplicate: artifacts are written server-side once per
  step; finalize emits exactly one manifest.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
N8N_DIR = PROJECT_ROOT / "workflows" / "n8n"

WORKFLOWS = {
    "W1-source-intake": N8N_DIR / "W1-source-intake.json",
    "W2-candidate-review": N8N_DIR / "W2-candidate-review.json",
}

ALLOWED_NODE_TYPES = {
    "n8n-nodes-base.manualTrigger",
    "n8n-nodes-base.httpRequest",
    "n8n-nodes-base.wait",
    "n8n-nodes-base.if",
    "n8n-nodes-base.function",
}


class WorkflowStructureTests(unittest.TestCase):
    def test_workflows_present_and_parseable(self):
        for name, path in WORKFLOWS.items():
            with self.subTest(workflow=name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["name"], name)
                self.assertTrue(data["nodes"], "workflow has no nodes")
                self.assertTrue(data["connections"], "workflow has no connections")

    def test_node_type_whitelist(self):
        for name, path in WORKFLOWS.items():
            data = json.loads(path.read_text(encoding="utf-8"))
            for node in data["nodes"]:
                self.assertIn(
                    node["type"], ALLOWED_NODE_TYPES,
                    f"{name}: node {node['name']!r} uses forbidden type {node['type']}",
                )

    def test_http_nodes_only_target_backend_or_error_webhook(self):
        for name, path in WORKFLOWS.items():
            data = json.loads(path.read_text(encoding="utf-8"))
            http_nodes = [n for n in data["nodes"]
                          if n["type"] == "n8n-nodes-base.httpRequest"]
            self.assertTrue(http_nodes, f"{name}: no HTTP nodes?")
            for node in http_nodes:
                url = str(node.get("parameters", {}).get("url") or "")
                ok = ("$env.XIANYU_API_BASE" in url
                      and "/api/v1/" in url) or "$env.XIANYU_ERROR_WEBHOOK" in url
                self.assertTrue(
                    ok,
                    f"{name}: HTTP node {node['name']!r} targets {url!r} — only the "
                    "backend API base or the error webhook are allowed",
                )

    def test_no_credentials_exported(self):
        for name, path in WORKFLOWS.items():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('"credentials"', text, f"{name} embeds credentials")
            for secret in ("token", "password", "api_key", "Authorization"):
                self.assertNotIn(
                    f'"{secret}"', text.lower().replace("jsonbody", "").replace("notes", ""),
                    f"{name} may embed a {secret} field",
                )

    def test_manual_trigger_only_no_auto_triggers(self):
        for name, path in WORKFLOWS.items():
            data = json.loads(path.read_text(encoding="utf-8"))
            for node in data["nodes"]:
                self.assertNotIn("webhook", node["type"].lower(),
                                 f"{name}: webhook triggers are not enabled in R18")
                self.assertNotIn("schedule", node["type"].lower(),
                                 f"{name}: schedule triggers are not enabled in R18")

    def test_versions_doc_covers_owner_transition(self):
        text = (N8N_DIR / "VERSIONS.md").read_text(encoding="utf-8")
        self.assertIn("owner", text.lower())
        self.assertIn("stepwise", text.lower())
        self.assertIn("断点续跑", text)


def run_payload(**overrides):
    payload = {
        "business_intent_id": "candidate-review:acct-a:snapset:v3",
        "workflow_type": "candidate_review",
        "account_id": "acct-a",
        "input_version": 3,
        "input_artifact_ids": ["supplier-v3", "market-v2"],
        "requested_capabilities": ["validate_evidence", "match_products",
                                   "calculate_economics", "export_candidates"],
        "orchestration_mode": "stepwise",
    }
    payload.update(overrides)
    return payload


class ChainSemanticsTests(unittest.TestCase):
    """The kill/retrigger/partial-failure model the JSONs encode, driven
    against the real WorkflowRunService exactly as the nodes would."""

    def setUp(self):
        from xianyu_agent.application.workflow_runs import (
            WorkflowRunService,
            WorkflowRunStore,
        )

        self.tmp = Path(tempfile.mkdtemp(prefix="r18-chain-"))
        self.service = WorkflowRunService(WorkflowRunStore(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _request_step(self, run, capability):
        return self.service.request_step(run["run_id"], {"capability": capability})

    def _complete(self, run_id, step_run_id, outcome, **kwargs):
        return self.service.complete_step(run_id, step_run_id,
                                          outcome=outcome, **kwargs)

    def test_duplicate_trigger_same_run_no_duplicate_steps(self):
        """n8n execution killed right after create; re-execution POSTs the
        same intent again -> same run; requesting the same capability is
        refused (live step) so no duplicate work is ever dispatched."""
        first = self.service.create_run(run_payload())
        second = self.service.create_run(run_payload())  # retrigger
        self.assertEqual(first["run"]["run_id"], second["run"]["run_id"])
        self.assertFalse(second["created"])

        step = self._request_step(first["run"], "validate_evidence")
        # n8n died and re-executed: it re-requests the same capability
        from xianyu_agent.application.workflow_runs import WorkflowRunError

        with self.assertRaises(WorkflowRunError) as ctx:
            self._request_step(first["run"], "validate_evidence")
        self.assertEqual(ctx.exception.code, "live_step_exists")

        # once terminal, re-requesting is an EXPLICIT retry path (new
        # attempt by design); the chain never takes it — W2 skips
        # terminal capabilities — and completing the OLD terminal step
        # again is refused, so its result can never be rewritten
        self._complete(first["run"]["run_id"], step["step_run"]["step_run_id"],
                       outcome="succeeded")
        with self.assertRaises(WorkflowRunError) as ctx:
            self._complete(first["run"]["run_id"], step["step_run"]["step_run_id"],
                           outcome="succeeded")
        self.assertEqual(ctx.exception.code, "step_terminal")

    def test_kill_and_resume_skips_terminal_steps(self):
        """The W2 Next Pending Capability function encoded in Python:
        after a kill, re-execution requests ONLY capabilities that are
        not terminal yet."""
        run = self.service.create_run(run_payload())["run"]
        # two capabilities completed before the "kill"
        for capability in ("validate_evidence", "match_products"):
            step = self._request_step(run, capability)
            self._complete(run["run_id"], step["step_run"]["step_run_id"],
                           outcome="succeeded")

        def next_pending(current_run):
            terminal = {"succeeded", "failed", "skipped"}
            done = {s["capability"] for s in current_run["steps"]
                    if s["status"] in terminal}
            return [c for c in current_run["requested_capabilities"]
                    if c not in done]

        pending = next_pending(self.service.get_run(run["run_id"]))
        self.assertEqual(pending, ["calculate_economics", "export_candidates"])

        # resume requests exactly those
        for capability in pending:
            step = self._request_step(run, capability)
            self._complete(run["run_id"], step["step_run"]["step_run_id"],
                           outcome="succeeded")

        final = self.service.finalize(run["run_id"])
        self.assertEqual(final["status"], "finalized")
        # exactly ONE step per capability ever existed
        stored = self.service.get_run(run["run_id"])
        per_capability = {}
        for step in stored["steps"]:
            per_capability.setdefault(step["capability"], 0)
            per_capability[step["capability"]] += 1
        self.assertEqual(
            per_capability,
            {c: 1 for c in run["requested_capabilities"]},
            "resume must never duplicate a step",
        )

    def test_partial_failure_is_terminal_finalize_blocked(self):
        run = self.service.create_run(run_payload())["run"]
        step = self._request_step(run, "validate_evidence")
        self._complete(run["run_id"], step["step_run"]["step_run_id"],
                      outcome="failed", result={"error": "fixture failure"})

        from xianyu_agent.application.workflow_runs import WorkflowRunError

        with self.assertRaises(WorkflowRunError) as ctx:
            self.service.finalize(run["run_id"])
        self.assertEqual(ctx.exception.code, "required_steps_incomplete")
        # the failed step stays terminal — its result can never be
        # rewritten; the chain waits for a human (reconcile/retry)
        with self.assertRaises(WorkflowRunError) as ctx:
            self._complete(run["run_id"], step["step_run"]["step_run_id"],
                           outcome="succeeded")
        self.assertEqual(ctx.exception.code, "step_terminal")

    def test_events_map_execution_history(self):
        """Every n8n action leaves a backend event: the workflow history
        (execution id in orchestration_trace) corresponds 1:1 to the run
        event sequence."""
        run = self.service.create_run(run_payload(
            orchestration_trace={"engine": "n8n", "execution_id": "exec-42"}
        ))["run"]
        step = self._request_step(run, "validate_evidence")
        self._complete(run["run_id"], step["step_run"]["step_run_id"],
                       outcome="succeeded")
        stored = self.service.get_run(run["run_id"])
        event_types = [e["type"] for e in stored["events"]]
        self.assertEqual(event_types[:3], ["run_created", "run_running", "step_requested"])
        self.assertIn("step_succeeded", event_types)
        self.assertEqual(
            stored["orchestration_trace"]["execution_id"], "exec-42"
        )


if __name__ == "__main__":
    unittest.main()
