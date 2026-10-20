"""R03 acceptance tests: unified authentication and account authorization.

Proves, at the policy layer and through real middleware round-trips:

* missing credentials never become an operator (strict mode / non-loopback
  -> 401; loopback local-operator mode is the only token-less pass);
* unknown token -> 401, expired token -> 401, wrong audience -> 403;
* cross-account access is refused by the account grant;
* token records granting approvals to model/worker principals are
  refused at load time (structural anti-self-approval);
* the legacy approval routes now require the approvals scope — an
  n8n-style service token gets 403 where the loopback operator passes.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xianyu_agent.api.dependencies import (
    guard_approval,
    require_approvals_grant,
    require_commerce_read,
)
from xianyu_agent.security.auth import AuthMiddleware, TokenRegistry
from xianyu_agent.security.policy import (
    AuthDecisionError,
    AuthPolicy,
    JOB_SESSION,
    LOCAL_OPERATOR,
    Principal,
)
from fastapi import Depends


N8N_TOKEN = "n8n-test-token-1234"
AGENT_TOKEN = "agent-test-token-5678"


def _registry(extra_records=()):
    records = [
        {
            "token": N8N_TOKEN,
            "kind": "n8n_service",
            "name": "n8n-main",
            "scopes": ["workflow:trigger", "commerce:read"],
            "account_ids": ["acc-1"],
            "audience": "http",
        },
        {
            "token": AGENT_TOKEN,
            "kind": "analysis_agent",
            "name": "quote-reviewer",
            "scopes": ["research:read"],
            "account_ids": [],
            "audience": "http",
        },
    ]
    return TokenRegistry(list(records) + list(extra_records))


def _build_app(registry, *, strict=False, local_operator=True):
    app = FastAPI()
    app.add_middleware(
        AuthMiddleware,
        registry=registry,
        local_operator_enabled=local_operator,
        strict=strict,
    )

    @app.get("/commerce/data", dependencies=[Depends(require_commerce_read)])
    def commerce_data():
        return {"ok": True}

    @app.post("/approvals/{approval_id}/decision", dependencies=[Depends(require_approvals_grant)])
    def decide(approval_id: str):
        return {"decided": approval_id}

    return app


class TokenRegistryTests(unittest.TestCase):
    def test_unknown_token_rejected(self):
        registry = _registry()
        with self.assertRaises(AuthDecisionError) as ctx:
            registry.authenticate("no-such-token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_expired_token_rejected(self):
        registry = _registry(
            [
                {
                    "token": "expired-token",
                    "kind": "worker",
                    "name": "old-worker",
                    "scopes": ["jobs:report"],
                    "expires_at": time.time() - 10,
                }
            ]
        )
        with self.assertRaises(AuthDecisionError) as ctx:
            registry.authenticate("expired-token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_audience_rejected(self):
        registry = _registry()
        with self.assertRaises(AuthDecisionError) as ctx:
            registry.authenticate(AGENT_TOKEN, audience="job")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_model_principal_cannot_be_granted_approval_scope(self):
        with self.assertRaises(AuthDecisionError) as ctx:
            _registry(
                [
                    {
                        "token": "rogue",
                        "kind": "analysis_agent",
                        "name": "rogue-agent",
                        "scopes": ["approvals:grant"],  # must be refused at load
                    }
                ]
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("self-approval", ctx.exception.reason)

    def test_local_user_kind_is_not_a_token_kind(self):
        with self.assertRaises(AuthDecisionError):
            _registry([{"token": "x", "kind": "local_user", "name": "fake-local"}])

    def test_duplicate_tokens_refused(self):
        with self.assertRaises(AuthDecisionError):
            _registry(
                [
                    {"token": N8N_TOKEN, "kind": "worker", "name": "dup"},
                ]
            )


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = AuthPolicy()

    def test_local_operator_can_approve(self):
        self.assertTrue(LOCAL_OPERATOR.can_approve(""))

    def test_agent_principal_never_approves(self):
        agent = Principal(
            kind="analysis_agent",
            name="quote-reviewer",
            scopes=frozenset({"approvals:grant"}),  # forged directly
            source="token",
        )
        self.assertFalse(agent.can_approve("someone-else"))
        self.assertFalse(agent.can_approve(agent.name))

    def test_job_session_is_read_only(self):
        self.assertFalse(JOB_SESSION.can_approve(""))
        self.assertTrue(JOB_SESSION.has_scope("research:read"))
        self.assertFalse(JOB_SESSION.has_scope("commerce:write"))

    def test_cross_account_denied(self):
        principal = Principal(
            kind="n8n_service",
            name="n8n-main",
            scopes=frozenset({"commerce:read"}),
            account_ids=frozenset({"acc-1"}),
        )
        with self.assertRaises(AuthDecisionError) as ctx:
            self.policy.authorize(principal, scope="commerce:read", account_id="acc-2")
        self.assertEqual(ctx.exception.status_code, 403)


class MiddlewareIntegrationTests(unittest.TestCase):
    def test_loopback_without_token_passes_as_local_operator(self):
        client = TestClient(_build_app(_registry()))
        response = client.get("/commerce/data")
        self.assertEqual(response.status_code, 200)

    def test_strict_mode_refuses_tokenless_requests(self):
        client = TestClient(_build_app(_registry(), strict=True))
        response = client.get("/commerce/data")
        self.assertEqual(response.status_code, 401)
        # health-style exempt path stays observable
        app = _build_app(_registry(), strict=True)
        client = TestClient(app)
        self.assertEqual(client.get("/health").status_code, 404)  # route absent, but NOT 401

    def test_unknown_token_gets_401(self):
        client = TestClient(_build_app(_registry()))
        response = client.get("/commerce/data", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)

    def test_non_bearer_scheme_rejected(self):
        client = TestClient(_build_app(_registry()))
        response = client.get("/commerce/data", headers={"Authorization": "Basic abc"})
        self.assertEqual(response.status_code, 401)

    def test_n8n_token_read_allowed_but_approval_refused(self):
        client = TestClient(_build_app(_registry()))
        headers = {"Authorization": "Bearer %s" % N8N_TOKEN}
        self.assertEqual(client.get("/commerce/data", headers=headers).status_code, 200)
        response = client.post("/approvals/a1/decision", headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_agent_token_cannot_read_commerce(self):
        client = TestClient(_build_app(_registry()))
        response = client.get(
            "/commerce/data", headers={"Authorization": "Bearer %s" % AGENT_TOKEN}
        )
        self.assertEqual(response.status_code, 403)

    def test_job_token_header_never_becomes_operator(self):
        client = TestClient(_build_app(_registry()))
        response = client.get("/commerce/data", headers={"X-Job-Token": "any-job-token"})
        self.assertEqual(response.status_code, 403)


class ServerApprovalRouteTests(unittest.TestCase):
    """The legacy approval routes must obey the same rules (R03)."""

    @classmethod
    def setUpClass(cls):
        from xianyu_agent import server

        cls.server = server
        cls.saved_registry = getattr(server.app.state, "auth_registry", None)
        server.app.state.auth_registry = _registry()

    @classmethod
    def tearDownClass(cls):
        if cls.saved_registry is None:
            cls.server.app.state.auth_registry = None
        else:
            cls.server.app.state.auth_registry = cls.saved_registry

    def test_local_operator_reaches_route_logic(self):
        # no lifespan context on purpose: the shared server runtime boots
        # exactly once per process (test_app_factory owns that contract)
        client = TestClient(self.server.app)
        response = client.post(
            "/approvals/does-not-exist/decision", json={"decision": "approve", "note": ""}
        )
        # authenticated as operator -> passes the dependency and reaches
        # the route body, which rejects the unknown id (400/404 depending
        # on the manager's error type) — never 401/403
        self.assertIn(response.status_code, (400, 404))

    def test_service_token_is_refused_on_approval_route(self):
        client = TestClient(self.server.app)
        response = client.post(
            "/approvals/does-not-exist/decision",
            json={"decision": "approve", "note": ""},
            headers={"Authorization": "Bearer %s" % N8N_TOKEN},
        )
        self.assertEqual(response.status_code, 403)

    def test_bad_token_is_refused_everywhere(self):
        client = TestClient(self.server.app)
        response = client.post(
            "/approvals/does-not-exist/decision",
            json={"decision": "approve", "note": ""},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        self.assertEqual(response.status_code, 401)


class GuardApprovalTests(unittest.TestCase):
    def test_guard_approval_rejects_forged_agent(self):
        from fastapi import HTTPException

        forged = Principal(
            kind="analysis_agent",
            name="forged",
            scopes=frozenset({"approvals:grant"}),
            source="token",
        )
        with self.assertRaises(HTTPException) as ctx:
            guard_approval(forged, proposed_by="operator")
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
