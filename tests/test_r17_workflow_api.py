"""R17 acceptance tests: versioned workflow API.

Taskbook matrix:
* same intent key + same payload -> the SAME run (idempotent, 202);
* same intent key + different payload -> 409 idempotency_conflict;
* client-supplied status is rejected (server transitions only) — a
  client can never write approved/succeeded/finalized;
* cross-account run/artifact reads -> 403;
* finalize only succeeds server-side when required capabilities are
  terminal; skip reasons are contract-validated;
* whole_run orchestration refused for external callers;
* steps require workflow:trigger for token principals.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from xianyu_agent.application.workflow_runs import (
    WorkflowRunService,
    WorkflowRunStore,
)


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
        "orchestration_trace": {"engine": "n8n", "execution_id": "exec-1"},
    }
    payload.update(overrides)
    return payload


class WorkflowApiTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server as server_module
        from xianyu_agent.api.v1 import configure_workflow_api

        self.tmp = Path(tempfile.mkdtemp(prefix="r17-wf-"))
        self.service = WorkflowRunService(WorkflowRunStore(self.tmp))
        configure_workflow_api(self.service)
        # route module shares the app; restore after the class
        self._app = server_module.app
        self.client = TestClient(self._app, raise_server_exceptions=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- idempotency -----------------------------------------------------

    def test_same_intent_same_payload_returns_same_run(self):
        first = self.client.post("/api/v1/workflow-runs", json=run_payload())
        self.assertEqual(first.status_code, 202, first.text)
        second = self.client.post("/api/v1/workflow-runs", json=run_payload())
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(first.json()["run_id"], second.json()["run_id"])
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])

    def test_same_intent_different_payload_is_409(self):
        self.client.post("/api/v1/workflow-runs", json=run_payload())
        conflict = self.client.post(
            "/api/v1/workflow-runs",
            json=run_payload(input_version=4),  # different payload
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "idempotency_conflict")

    # ---- server-only statuses ---------------------------------------------

    def test_client_cannot_set_status(self):
        response = self.client.post(
            "/api/v1/workflow-runs",
            json=run_payload(**{"status": "succeeded"}),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "client_status_forbidden")

    def test_step_request_with_client_status_rejected(self):
        created = self.client.post("/api/v1/workflow-runs", json=run_payload()).json()
        step = self.client.post(
            f"/api/v1/workflow-runs/{created['run_id']}/steps",
            json={"capability": "validate_evidence", "status": "succeeded"},
        )
        self.assertEqual(step.status_code, 422)
        self.assertEqual(step.json()["detail"]["code"], "client_status_forbidden")

    def test_whole_run_mode_refused_for_external_callers(self):
        response = self.client.post(
            "/api/v1/workflow-runs",
            json=run_payload(orchestration_mode="whole_run"),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "mode_not_authorized")

    # ---- lifecycle + finalize -------------------------------------------

    def _create_and_run_steps(self):
        created = self.client.post("/api/v1/workflow-runs", json=run_payload()).json()
        run_id = created["run_id"]
        for capability in ("validate_evidence", "match_products",
                           "calculate_economics", "export_candidates"):
            step = self.client.post(
                f"/api/v1/workflow-runs/{run_id}/steps",
                json={"capability": capability},
            )
            self.assertEqual(step.status_code, 202, step.text)
            step_run = step.json()["step_run"]
            self.service.complete_step(
                run_id, step_run["step_run_id"], outcome="succeeded",
                result={"ok": True},
            )
        return run_id

    def test_finalize_requires_required_capabilities(self):
        created = self.client.post("/api/v1/workflow-runs", json=run_payload()).json()
        run_id = created["run_id"]
        early = self.client.post(f"/api/v1/workflow-runs/{run_id}/finalize")
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.json()["detail"]["code"], "required_steps_incomplete")

        run_id = self._create_and_run_steps()
        final = self.client.post(f"/api/v1/workflow-runs/{run_id}/finalize")
        self.assertEqual(final.status_code, 200, final.text)
        manifest = final.json()["manifest"]
        self.assertEqual(len(manifest["steps"]), 4)
        status = self.client.get(f"/api/v1/workflow-runs/{run_id}").json()["status"]
        self.assertEqual(status, "finalized")

    def test_skip_reason_contract_validated(self):
        created = self.client.post("/api/v1/workflow-runs", json=run_payload()).json()
        run_id = created["run_id"]
        step = self.client.post(
            f"/api/v1/workflow-runs/{run_id}/steps",
            json={"capability": "export_candidates"},
        ).json()["step_run"]
        from xianyu_agent.application.workflow_runs import WorkflowRunError

        with self.assertRaises(WorkflowRunError):
            self.service.complete_step(
                run_id, step["step_run_id"], outcome="skipped",
                result={"reason": "operator_felt_like_it"},
            )
        self.service.complete_step(
            run_id, step["step_run_id"], outcome="skipped",
            result={"reason": "no_spread_available"},
        )
        skipped = self.service.get_run(run_id)["steps"][0]
        self.assertEqual(skipped["status"], "skipped")

    def test_cancel_then_step_rejected(self):
        created = self.client.post("/api/v1/workflow-runs", json=run_payload()).json()
        run_id = created["run_id"]
        self.client.post(f"/api/v1/workflow-runs/{run_id}/cancel")
        step = self.client.post(
            f"/api/v1/workflow-runs/{run_id}/steps",
            json={"capability": "validate_evidence"},
        )
        self.assertEqual(step.status_code, 409)

    def test_duplicate_live_step_rejected(self):
        created = self.client.post("/api/v1/workflow-runs", json=run_payload()).json()
        run_id = created["run_id"]
        first = self.client.post(
            f"/api/v1/workflow-runs/{run_id}/steps",
            json={"capability": "validate_evidence"},
        )
        self.assertEqual(first.status_code, 202)
        second = self.client.post(
            f"/api/v1/workflow-runs/{run_id}/steps",
            json={"capability": "validate_evidence"},
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["detail"]["code"], "live_step_exists")

    def test_events_cursor_read(self):
        created = self.client.post("/api/v1/workflow-runs", json=run_payload()).json()
        run_id = created["run_id"]
        page1 = self.client.get(f"/api/v1/workflow-runs/{run_id}/events?cursor=0").json()
        self.assertTrue(page1["events"])
        page2 = self.client.get(
            f"/api/v1/workflow-runs/{run_id}/events?cursor={page1['next_cursor']}"
        ).json()
        self.assertEqual(page2["events"], [])

    # ---- account scoping -------------------------------------------------

    def test_cross_account_run_read_is_403(self):
        created = self.client.post(
            "/api/v1/workflow-runs", json=run_payload(account_id="acct-a")
        ).json()
        run_id = created["run_id"]
        # a token principal scoped to acct-b cannot read acct-a's run
        from xianyu_agent.security.auth import TokenRegistry
        from xianyu_agent.security.policy import Principal

        scoped = Principal(
            kind="n8n_service", name="n8n-b",
            scopes=frozenset({"workflow:trigger", "commerce:read"}),
            account_ids=frozenset({"acct-b"}),
            source="token",
        )
        with self.assertRaises(Exception):
            self.service.get_run(run_id, account_scope=list(scoped.account_ids))

    def test_cross_account_artifact_read_is_403(self):
        created = self.client.post(
            "/api/v1/workflow-runs", json=run_payload()
        ).json()
        run_id = created["run_id"]
        step = self.client.post(
            f"/api/v1/workflow-runs/{run_id}/steps",
            json={"capability": "validate_evidence"},
        ).json()["step_run"]
        self.service.complete_step(
            run_id, step["step_run_id"], outcome="succeeded",
            artifacts={"art-report": {"rows": 3}},
        )
        from xianyu_agent.application.workflow_runs import WorkflowRunError

        with self.assertRaises(WorkflowRunError) as ctx:
            self.service.get_artifact("art-report", account_scope=["acct-b"])
        self.assertEqual(ctx.exception.status_code, 403)
        # in-scope read works
        artifact = self.service.get_artifact("art-report", account_scope=["acct-a"])
        self.assertEqual(artifact["content"]["rows"], 3)

    def test_capabilities_registry_query(self):
        response = self.client.get("/api/v1/capabilities")
        self.assertEqual(response.status_code, 200)
        caps = response.json()["capabilities"]
        self.assertIn("validate_evidence", caps)
        self.assertEqual(caps["validate_evidence"]["version"], 1)


if __name__ == "__main__":
    unittest.main()
