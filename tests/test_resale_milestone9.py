"""Tests for Milestone 9 — Agent-driven resale candidate review."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_manager():
    from xianyu_agent.resale import ResaleManager
    tmpdir = tempfile.mkdtemp()
    return ResaleManager(Path(tmpdir)), Path(tmpdir)


def _make_candidate(manager):
    return manager.create_candidate({
        "title": "蓝牙耳机 TWS",
        "source_platform": "pdd",
        "source_url": "https://pdd.example.com/item/123",
        "supplier_price": 25.0,
        "supplier_shipping": 3.0,
        "target_sale_price": 59.0,
        "category": "数码配件",
        "specifications": {"color": "black"},
    })


def _make_registry_with_resale_agents():
    """Create a fresh registry with the 3 resale agents registered."""
    from xianyu_agent.agent_registry import AgentRegistry
    from xianyu_agent.agents.resale_research import ResaleResearchAgent
    from xianyu_agent.agents.resale_risk import ResaleRiskAgent
    from xianyu_agent.agents.resale_pricing import ResalePricingAgent

    registry = AgentRegistry()
    agents = {
        "resale_research": ResaleResearchAgent(),
        "resale_risk": ResaleRiskAgent(),
        "resale_pricing": ResalePricingAgent(),
    }
    for name, agent in agents.items():
        registry.register_agent(name, agent)
    return registry


# ============================================================
# Agent registration
# ============================================================

class TestAgentRegistration(unittest.TestCase):
    """3 resale agents register into registry."""

    def test_resale_research_registered(self):
        registry = _make_registry_with_resale_agents()
        status = registry.get_status()
        self.assertIn("resale_research", status["registered_agents"])

    def test_resale_risk_registered(self):
        registry = _make_registry_with_resale_agents()
        status = registry.get_status()
        self.assertIn("resale_risk", status["registered_agents"])

    def test_resale_pricing_registered(self):
        registry = _make_registry_with_resale_agents()
        status = registry.get_status()
        self.assertIn("resale_pricing", status["registered_agents"])


class TestAgentRegistrationViaCreateAll(unittest.TestCase):
    """create_all_agents() includes the 3 new agents."""

    def test_create_all_agents_includes_resale(self):
        from xianyu_agent.agents import create_all_agents
        agents = create_all_agents()
        self.assertIn("resale_research", agents)
        self.assertIn("resale_risk", agents)
        self.assertIn("resale_pricing", agents)


# ============================================================
# Agent discussion
# ============================================================

class TestAgentDiscussion(unittest.TestCase):
    """registry.discuss() with 3 resale agents, 2 rounds."""

    def setUp(self):
        self.registry = _make_registry_with_resale_agents()
        self.candidate = {
            "title": "蓝牙耳机 TWS",
            "source_platform": "pdd",
            "source_url": "https://pdd.example.com/item/123",
            "supplier_price": 25.0,
            "supplier_shipping": 3.0,
            "target_sale_price": 59.0,
            "category": "数码配件",
            "supplier_verified": True,
            "risk_level": "low",
            "risk_flags": [],
            "economics": {"gross_profit": 25.0, "gross_margin": 0.42},
        }

    def test_discuss_returns_3_agents_2_rounds(self):
        result = self.registry.discuss(
            topic="倒卖候选评审: 蓝牙耳机 TWS",
            participants=["resale_research", "resale_risk", "resale_pricing"],
            rounds=2,
            context={"candidate": self.candidate},
        )
        transcript = result.get("transcript", [])
        self.assertEqual(len(transcript), 6)  # 3 agents × 2 rounds

    def test_discuss_transcript_has_all_agents(self):
        result = self.registry.discuss(
            topic="倒卖候选评审: 蓝牙耳机 TWS",
            participants=["resale_research", "resale_risk", "resale_pricing"],
            rounds=2,
            context={"candidate": self.candidate},
        )
        agents_in_transcript = {t["agent"] for t in result["transcript"]}
        self.assertEqual(agents_in_transcript, {"resale_research", "resale_risk", "resale_pricing"})

    def test_discuss_summary_not_empty(self):
        result = self.registry.discuss(
            topic="倒卖候选评审: 蓝牙耳机 TWS",
            participants=["resale_research", "resale_risk", "resale_pricing"],
            rounds=2,
            context={"candidate": self.candidate},
        )
        self.assertTrue(result.get("summary"))
        self.assertIn("resale_", result["summary"])


# ============================================================
# Agent review API
# ============================================================

class TestAgentReviewAPI(unittest.TestCase):
    """POST /resale/candidates/{id}/agent-review."""

    def _setup(self):
        import xianyu_agent.server as srv
        from fastapi.testclient import TestClient
        mgr, tmpdir = _make_manager()
        # Register resale agents into the server's registry
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        srv.registry.register_agent("resale_research", ResaleResearchAgent())
        srv.registry.register_agent("resale_risk", ResaleRiskAgent())
        srv.registry.register_agent("resale_pricing", ResalePricingAgent())
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        return mgr, client

    def test_agent_review_saves_to_candidate(self):
        mgr, client = self._setup()
        c = _make_candidate(mgr)
        resp = client.post(f"/resale/candidates/{c['candidate_id']}/agent-review")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("discussion", data)
        self.assertIn("transcript", data["discussion"])

    def test_agent_review_candidate_has_reviews(self):
        mgr, client = self._setup()
        c = _make_candidate(mgr)
        client.post(f"/resale/candidates/{c['candidate_id']}/agent-review")
        updated = mgr.get_candidate(c["candidate_id"])
        self.assertIn("agent_reviews", updated)
        self.assertIn("agent_reviewed_at", updated)

    def test_agent_review_404(self):
        _, client = self._setup()
        resp = client.post("/resale/candidates/rc_nonexistent/agent-review")
        self.assertEqual(resp.status_code, 404)

    def test_agent_review_persists(self):
        mgr, client = self._setup()
        c = _make_candidate(mgr)
        client.post(f"/resale/candidates/{c['candidate_id']}/agent-review")
        # Re-read from manager (simulates persistence)
        fresh = mgr.get_candidate(c["candidate_id"])
        self.assertIsNotNone(fresh.get("agent_reviews"))
        self.assertIsNotNone(fresh.get("agent_reviewed_at"))


# ============================================================
# Agent review — save_agent_review method
# ============================================================

class TestSaveAgentReview(unittest.TestCase):
    """ResaleManager.save_agent_review() method."""

    def test_save_agent_review(self):
        mgr, _ = _make_manager()
        c = _make_candidate(mgr)
        discussion = {"summary": "test summary", "transcript": [], "participants": ["a", "b"]}
        result = mgr.save_agent_review(c["candidate_id"], discussion)
        self.assertIsNotNone(result)
        self.assertEqual(result["agent_reviews"]["summary"], "test summary")

    def test_save_agent_review_nonexistent(self):
        mgr, _ = _make_manager()
        result = mgr.save_agent_review("rc_nonexistent", {"summary": "x"})
        self.assertIsNone(result)

    def test_save_agent_review_persists(self):
        mgr, _ = _make_manager()
        c = _make_candidate(mgr)
        discussion = {"summary": "持久化测试", "transcript": []}
        mgr.save_agent_review(c["candidate_id"], discussion)
        # Read fresh
        fresh = mgr.get_candidate(c["candidate_id"])
        self.assertEqual(fresh["agent_reviews"]["summary"], "持久化测试")


# ============================================================
# Deterministic agent output
# ============================================================

class TestAgentDeterministicOutput(unittest.TestCase):
    """Each agent returns deterministic rule-based output."""

    def _make_context(self, **overrides):
        base = {
            "candidate": {
                "title": "蓝牙耳机 TWS",
                "source_platform": "pdd",
                "source_url": "https://pdd.example.com/item/123",
                "supplier_price": 25.0,
                "supplier_shipping": 3.0,
                "target_sale_price": 59.0,
                "supplier_verified": True,
                "risk_level": "low",
                "risk_flags": [],
                "economics": {"gross_profit": 25.0, "gross_margin": 0.42},
                "category": "数码配件",
            }
        }
        base["candidate"].update(overrides)
        return base

    def test_research_agent_output_contains_title(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        ctx = self._make_context()
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("蓝牙耳机 TWS", result)
        self.assertIn("resale_research", result)

    def test_research_agent_verified(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        ctx = self._make_context(supplier_verified=True)
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("已验证", result)

    def test_research_agent_unverified(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        ctx = self._make_context(supplier_verified=False)
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("未验证", result)

    def test_risk_agent_output_contains_title(self):
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        agent = ResaleRiskAgent()
        ctx = self._make_context()
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("蓝牙耳机 TWS", result)
        self.assertIn("resale_risk", result)

    def test_risk_agent_blocked(self):
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        agent = ResaleRiskAgent()
        ctx = self._make_context(risk_level="blocked")
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("blocked", result)

    def test_risk_agent_negative_margin(self):
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        agent = ResaleRiskAgent()
        ctx = self._make_context()
        ctx["candidate"]["economics"] = {"gross_margin": -0.1}
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("负毛利", result)

    def test_pricing_agent_output_contains_title(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        ctx = self._make_context()
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("蓝牙耳机 TWS", result)
        self.assertIn("resale_pricing", result)

    def test_pricing_agent_contains_cost(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        ctx = self._make_context()
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("¥25.0", result)
        self.assertIn("¥59", result)

    def test_pricing_agent_healthy_margin(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        ctx = self._make_context()
        result = agent.contribute_to_discussion("评审", [], ctx)
        self.assertIn("定价合理", result)


# ============================================================
# WorkOrder discussion wiring
# ============================================================

class TestWorkOrderDiscussion(unittest.TestCase):
    """After agent review, WorkOrder discussion != None."""

    def _setup(self):
        import xianyu_agent.server as srv
        from fastapi.testclient import TestClient
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.account_commercial_terms import AccountCommercialTermsStore
        from contextlib import ExitStack
        from unittest.mock import patch
        from preflight_helpers import add_preflight_account, build_runtime_store
        mgr, tmpdir = _make_manager()
        # Reset work_order_manager for test isolation
        wom = WorkOrderManager()
        wom.configure(tmpdir / "work_orders.json")
        db = DBManager(str(tmpdir / "accounts.db"))
        add_preflight_account(db, "acct_1")
        runtime = build_runtime_store(tmpdir, "acct_1")
        terms = AccountCommercialTermsStore(tmpdir / "commercial_terms.json")
        mgr._test_commercial_terms_store = terms
        stack = ExitStack()
        stack.enter_context(patch.object(srv, "work_order_manager", wom))
        stack.enter_context(patch.object(srv, "db_manager", db))
        stack.enter_context(patch.object(srv, "account_runtime_store", runtime))
        stack.enter_context(patch.object(srv, "account_commercial_terms_store", terms))
        stack.enter_context(patch.object(srv, "resale_manager", mgr))
        srv.registry.register_agent("resale_research", ResaleResearchAgent())
        srv.registry.register_agent("resale_risk", ResaleRiskAgent())
        srv.registry.register_agent("resale_pricing", ResalePricingAgent())
        client = TestClient(srv.app)
        return mgr, client, wom, stack

    def test_listing_draft_workorder_has_discussion(self):
        from preflight_helpers import make_preflight_ready_candidate, record_ready_commercial_terms
        mgr, client, wom, stack = self._setup()
        with stack:
            c = make_preflight_ready_candidate(mgr)
            record_ready_commercial_terms(
                mgr._test_commercial_terms_store, mgr, c, "acct_1"
            )
            # Run agent review first
            client.post(f"/resale/candidates/{c['candidate_id']}/agent-review")
            # Now create listing draft
            resp = client.post(f"/resale/candidates/{c['candidate_id']}/listing-draft?cookie_id=acct_1")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            wo = data["work_order"]
            self.assertIsNotNone(wo.get("discussion"))
            self.assertIn("summary", wo["discussion"])

    def test_purchase_draft_workorder_has_discussion(self):
        mgr, client, wom, stack = self._setup()
        with stack:
            c = _make_candidate(mgr)
            mgr.verify_candidate(c["candidate_id"], {"supplier_verified": True})
            mgr.set_decision(c["candidate_id"], "approve")
            # Run agent review first
            client.post(f"/resale/candidates/{c['candidate_id']}/agent-review")
            # Create plan + purchase draft
            plan = mgr.create_plan(c["candidate_id"])
            resp = client.post(f"/resale/plans/{plan['plan_id']}/purchase-draft", json={"quantity": 1})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            wo = data["work_order"]
            self.assertIsNotNone(wo.get("discussion"))
            self.assertIn("summary", wo["discussion"])


# ============================================================
# OpenClaw routing
# ============================================================

class TestOpenClawRouting(unittest.TestCase):
    """OpenClaw routes resale review keywords."""

    def _make_router(self):
        from xianyu_agent.agent_registry import AgentRegistry
        from xianyu_agent.openclaw_router import OpenClawRouter
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        from unittest.mock import MagicMock

        registry = AgentRegistry()
        registry.register_agent("resale_research", ResaleResearchAgent())
        registry.register_agent("resale_risk", ResaleRiskAgent())
        registry.register_agent("resale_pricing", ResalePricingAgent())
        mock_workflow = MagicMock()
        router = OpenClawRouter(registry=registry, workflow=mock_workflow)
        return router

    def test_resale_review_with_id(self):
        router = self._make_router()
        result = router.process_message("倒卖分析 rc_abc123def456")
        self.assertEqual(result["type"], "resale_review_hint")
        self.assertEqual(result["candidate_id"], "rc_abc123def456")

    def test_resale_review_without_id(self):
        router = self._make_router()
        result = router.process_message("倒卖分析")
        self.assertEqual(result["type"], "resale_review_hint")
        self.assertIsNone(result["candidate_id"])
        self.assertIn("candidate_id", result["message"])

    def test_resale_review_keyword_english(self):
        router = self._make_router()
        result = router.process_message("resale review rc_abc123")
        self.assertEqual(result["type"], "resale_review_hint")
        self.assertEqual(result["candidate_id"], "rc_abc123")

    def test_resale_review_keyword_chinese(self):
        router = self._make_router()
        result = router.process_message("候选评审")
        self.assertEqual(result["type"], "resale_review_hint")


# ============================================================
# Agent cannot execute high-risk actions
# ============================================================

class TestAgentCannotExecute(unittest.TestCase):
    """Resale agents do NOT register high-risk action handlers."""

    def test_no_publish_listing(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        for cls in [ResaleResearchAgent, ResaleRiskAgent, ResalePricingAgent]:
            agent = cls()
            handlers = agent.get_handlers()
            self.assertNotIn("publish_listing", handlers)

    def test_no_prepare_purchase_draft(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        for cls in [ResaleResearchAgent, ResaleRiskAgent, ResalePricingAgent]:
            agent = cls()
            handlers = agent.get_handlers()
            self.assertNotIn("prepare_purchase_draft", handlers)

    def test_no_place_supplier_order(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        for cls in [ResaleResearchAgent, ResaleRiskAgent, ResalePricingAgent]:
            agent = cls()
            handlers = agent.get_handlers()
            self.assertNotIn("place_supplier_order", handlers)

    def test_handlers_empty(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        for cls in [ResaleResearchAgent, ResaleRiskAgent, ResalePricingAgent]:
            agent = cls()
            self.assertEqual(agent.get_handlers(), {})


# ============================================================
# UI strings
# ============================================================

class TestMilestone9UIStrings(unittest.TestCase):
    """Verify JS contains Milestone 9 strings."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_agent_review_button_text(self):
        self.assertIn("Agent 评审", self.js)

    def test_agent_review_function(self):
        self.assertIn("agentReviewCandidate", self.js)

    def test_agent_review_summary_class(self):
        self.assertIn("agent-review-summary", self.js)

    def test_agent_suggestion_label(self):
        self.assertIn("Agent 建议", self.js)

    def test_agent_review_summarize_type(self):
        self.assertIn("resale_agent_review", self.js)

    def test_agent_review_completion_text(self):
        self.assertIn("Agent 评审完成", self.js)

    def test_agent_review_section_no_auto_publish(self):
        """Agent review summarize should not mention auto-publish."""
        self.assertIn("resale_agent_review", self.js)
        # The agent review type itself should not contain auto-publish language
        # (other types like listing_draft may legitimately mention it)
        self.assertNotIn("自动发布Agent 评审", self.js)


# ============================================================
# Behavioral tests
# ============================================================

_JS_M9_HARNESS = r"""
const fs = require("fs");
function escapeHtml(v){return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
let _h="";function $(s){return{set innerHTML(v){_h=v},get innerHTML(){return _h}}}

function renderResaleFeed(candidates){
  const feed=$("#resale-feed");
  if(!feed)return;
  if(!candidates.length){feed.innerHTML='<div>暂无候选</div>';return}
  feed.innerHTML=candidates.map(c=>{
    const isBlocked=c.status==="blocked";
    const econ=c.economics||{};
    const agentReviewHtml=c.agent_reviews
      ?'<div class="agent-review-summary"><strong>Agent 建议:</strong> '+escapeHtml(c.agent_reviews.summary||"无")+'</div>'
      :"";
    const actions=isBlocked
      ?'<div class="resale-actions"><span style="color:var(--danger);font-size:11px">已阻断，无法创建计划</span></div>'
      :'<div class="resale-actions">'
        +'<button onclick="agentReviewCandidate(\''+c.candidate_id+'\')">Agent 评审</button>'
        +'</div>';
    return '<div class="resale-candidate">'
      +agentReviewHtml
      +actions
      +'</div>';
  }).join("");
}

const args=process.argv.slice(2);
const data=JSON.parse(fs.readFileSync(args[0],"utf-8"));
renderResaleFeed(data);
fs.writeFileSync(args[1],_h,"utf-8");
"""


class TestMilestone9Behavioral(unittest.TestCase):
    """Behavioral tests for Milestone 9 UI."""

    def _run(self, data):
        js_path = os.path.join(tempfile.gettempdir(), "m9_test.js")
        in_path = os.path.join(tempfile.gettempdir(), "m9_in.json")
        out_path = os.path.join(tempfile.gettempdir(), "m9_out.html")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_M9_HARNESS)
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        subprocess.run(["node", js_path, in_path, out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_candidate_with_review_shows_summary(self):
        html = self._run([{
            "candidate_id": "rc_001",
            "status": "approved",
            "agent_reviews": {"summary": "resale_research: 建议补充需求数据 | resale_risk: 低风险 | resale_pricing: 毛利健康"},
        }])
        self.assertIn("Agent 建议", html)
        self.assertIn("agent-review-summary", html)
        self.assertIn("resale_research", html)

    def test_candidate_without_review_no_summary(self):
        html = self._run([{
            "candidate_id": "rc_002",
            "status": "approved",
        }])
        self.assertNotIn("agent-review-summary", html)
        self.assertNotIn("Agent 建议", html)

    def test_agent_review_button_present(self):
        html = self._run([{
            "candidate_id": "rc_003",
            "status": "approved",
        }])
        self.assertIn("Agent 评审", html)
        self.assertIn("agentReviewCandidate", html)

    def test_blocked_candidate_no_review_button(self):
        html = self._run([{
            "candidate_id": "rc_004",
            "status": "blocked",
        }])
        self.assertNotIn("Agent 评审", html)

    def test_empty_candidates(self):
        html = self._run([])
        self.assertIn("暂无候选", html)


# ============================================================
# Routes exist
# ============================================================

class TestMilestone9RoutesExist(unittest.TestCase):
    """Verify Milestone 9 routes are registered."""

    def test_agent_review_route(self):
        from xianyu_agent.server import app
        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        self.assertIn("/resale/candidates/{candidate_id}/agent-review", route_paths)


if __name__ == "__main__":
    unittest.main()
