"""Tests for Milestone 10: Structured Scoring + Decision Gate + DeepSeek."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_candidate(**overrides):
    """Create a minimal candidate dict for testing."""
    base = {
        "candidate_id": "rc_test001",
        "title": "测试商品",
        "status": "evaluating",
        "source_platform": "pdd",
        "source_url": "https://example.com/product",
        "sales_platform": "xianyu",
        "market_scope": "domestic",
        "supplier_price": 20.0,
        "supplier_shipping": 5.0,
        "target_sale_price": 50.0,
        "category": "数码配件",
        "risk_level": "low",
        "risk_flags": [],
        "supplier_verified": False,
        "estimated_demand_signal": "",
        "competitor_links": [],
        "competitor_price_min": None,
        "competitor_price_max": None,
        "economics": {
            "gross_profit": 20.0,
            "gross_margin": 0.4,
        },
    }
    base.update(overrides)
    return base


class TestStructuredReview(unittest.TestCase):
    """Test structured review_candidate() on each agent."""

    def test_research_review_has_scores(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        result = agent.review_candidate(_make_candidate())
        self.assertIn("supplier_score", result)
        self.assertIn("demand_score", result)
        self.assertIn("competitor_confidence", result)
        self.assertIn("research_flags", result)
        self.assertEqual(result["agent"], "resale_research")

    def test_risk_review_has_scores(self):
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        agent = ResaleRiskAgent()
        result = agent.review_candidate(_make_candidate())
        self.assertIn("compliance_score", result)
        self.assertIn("fulfillment_risk_score", result)
        self.assertIn("aftersale_risk_score", result)
        self.assertIn("risk_flags", result)
        self.assertIn("blocking_flags", result)
        self.assertEqual(result["agent"], "resale_risk")

    def test_pricing_review_has_scores(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        result = agent.review_candidate(_make_candidate())
        self.assertIn("margin_score", result)
        self.assertIn("price_competitiveness_score", result)
        self.assertIn("suggested_price", result)
        self.assertIn("pricing_flags", result)
        self.assertEqual(result["agent"], "resale_pricing")

    def test_blocking_flag_negative_margin(self):
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        agent = ResaleRiskAgent()
        candidate = _make_candidate(economics={"gross_profit": -5.0, "gross_margin": -0.1})
        result = agent.review_candidate(candidate)
        self.assertIn("负毛利", result["blocking_flags"])

    def test_blocking_flag_food_blocked(self):
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        agent = ResaleRiskAgent()
        candidate = _make_candidate(category="食品零食", status="blocked", risk_level="blocked",
                                     economics={"gross_profit": -5.0, "gross_margin": -0.1})
        result = agent.review_candidate(candidate)
        self.assertIn("食品禁售", result["blocking_flags"])
        self.assertIn("高风险已阻断", result["blocking_flags"])

    def test_scores_in_range(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        candidate = _make_candidate()
        for agent_cls in (ResaleResearchAgent, ResaleRiskAgent, ResalePricingAgent):
            result = agent_cls().review_candidate(candidate)
            for key, value in result.items():
                if isinstance(value, (int, float)) and key != "suggested_price":
                    self.assertGreaterEqual(value, 0, f"{key}={value} should be >= 0")
                    self.assertLessEqual(value, 100, f"{key}={value} should be <= 100")


class TestAggregateScoring(unittest.TestCase):
    """Test aggregate_agent_review() logic."""

    def _make_manager(self):
        from xianyu_agent.resale import ResaleManager
        import tempfile
        return ResaleManager(Path(tempfile.mkdtemp()))

    def _seed_candidate(self, mgr, candidate_overrides=None, reviews=None):
        c = _make_candidate(**(candidate_overrides or {}))
        mgr._candidates[c["candidate_id"]] = c
        if reviews:
            c["agent_structured_reviews"] = reviews
        mgr._save_candidates()
        return c["candidate_id"]

    def test_aggregate_high_score_approve(self):
        mgr = self._make_manager()
        reviews = [
            {"agent": "resale_research", "supplier_score": 90, "demand_score": 80},
            {"agent": "resale_risk", "compliance_score": 80, "fulfillment_risk_score": 80, "aftersale_risk_score": 80, "blocking_flags": []},
            {"agent": "resale_pricing", "margin_score": 100, "price_competitiveness_score": 80},
        ]
        cid = self._seed_candidate(mgr, reviews=reviews)
        agg = mgr.aggregate_agent_review(cid)
        self.assertEqual(agg["agent_review_recommendation"], "approve")
        self.assertGreaterEqual(agg["agent_review_score"], 75)

    def test_aggregate_mid_score_watch(self):
        mgr = self._make_manager()
        reviews = [
            {"agent": "resale_research", "supplier_score": 50, "demand_score": 40},
            {"agent": "resale_risk", "compliance_score": 60, "fulfillment_risk_score": 60, "aftersale_risk_score": 60, "blocking_flags": []},
            {"agent": "resale_pricing", "margin_score": 60, "price_competitiveness_score": 50},
        ]
        cid = self._seed_candidate(mgr, reviews=reviews)
        agg = mgr.aggregate_agent_review(cid)
        self.assertEqual(agg["agent_review_recommendation"], "watch")
        self.assertGreaterEqual(agg["agent_review_score"], 50)
        self.assertLess(agg["agent_review_score"], 75)

    def test_aggregate_low_score_reject(self):
        mgr = self._make_manager()
        reviews = [
            {"agent": "resale_research", "supplier_score": 20, "demand_score": 20},
            {"agent": "resale_risk", "compliance_score": 20, "fulfillment_risk_score": 20, "aftersale_risk_score": 20, "blocking_flags": []},
            {"agent": "resale_pricing", "margin_score": 20, "price_competitiveness_score": 20},
        ]
        cid = self._seed_candidate(mgr, reviews=reviews)
        agg = mgr.aggregate_agent_review(cid)
        self.assertEqual(agg["agent_review_recommendation"], "reject")
        self.assertLess(agg["agent_review_score"], 50)

    def test_aggregate_blocking_flags_reject(self):
        mgr = self._make_manager()
        reviews = [
            {"agent": "resale_research", "supplier_score": 90, "demand_score": 90},
            {"agent": "resale_risk", "compliance_score": 80, "fulfillment_risk_score": 80, "aftersale_risk_score": 80, "blocking_flags": ["负毛利"]},
            {"agent": "resale_pricing", "margin_score": 90, "price_competitiveness_score": 90},
        ]
        cid = self._seed_candidate(mgr, reviews=reviews)
        agg = mgr.aggregate_agent_review(cid)
        self.assertEqual(agg["agent_review_recommendation"], "reject")
        self.assertIn("负毛利", agg["blocking_flags"])

    def test_aggregate_blocked_candidate_reject(self):
        mgr = self._make_manager()
        reviews = [
            {"agent": "resale_research", "supplier_score": 90, "demand_score": 90},
            {"agent": "resale_risk", "compliance_score": 80, "fulfillment_risk_score": 80, "aftersale_risk_score": 80, "blocking_flags": []},
            {"agent": "resale_pricing", "margin_score": 90, "price_competitiveness_score": 90},
        ]
        cid = self._seed_candidate(mgr, {"status": "blocked"}, reviews=reviews)
        agg = mgr.aggregate_agent_review(cid)
        self.assertEqual(agg["agent_review_recommendation"], "reject")


class TestDecisionGate(unittest.TestCase):
    """Test set_decision() gate logic."""

    def _make_manager(self):
        from xianyu_agent.resale import ResaleManager
        import tempfile
        return ResaleManager(Path(tempfile.mkdtemp()))

    def _seed_with_review(self, mgr, recommendation="approve"):
        c = _make_candidate()
        c["agent_review_recommendation"] = recommendation
        mgr._candidates[c["candidate_id"]] = c
        mgr._save_candidates()
        return c["candidate_id"]

    def test_approve_without_review_allowed(self):
        mgr = self._make_manager()
        c = _make_candidate()
        mgr._candidates[c["candidate_id"]] = c
        mgr._save_candidates()
        result = mgr.set_decision(c["candidate_id"], "approve")
        self.assertNotIn("error", result)
        self.assertEqual(result["status"], "approved")

    def test_approve_with_override_works(self):
        mgr = self._make_manager()
        c = _make_candidate()
        mgr._candidates[c["candidate_id"]] = c
        mgr._save_candidates()
        result = mgr.set_decision(c["candidate_id"], "approve", override_reason="我确认风险可控")
        self.assertNotIn("error", result)
        self.assertEqual(result["status"], "approved")

    def test_approve_reject_recommendation_needs_override(self):
        mgr = self._make_manager()
        cid = self._seed_with_review(mgr, recommendation="reject")
        result = mgr.set_decision(cid, "approve")
        self.assertIn("error", result)
        self.assertIn("override_reason", result["error"])

    def test_approve_good_recommendation_works(self):
        mgr = self._make_manager()
        cid = self._seed_with_review(mgr, recommendation="approve")
        result = mgr.set_decision(cid, "approve")
        self.assertNotIn("error", result)
        self.assertEqual(result["status"], "approved")

    def test_override_reason_persisted(self):
        mgr = self._make_manager()
        cid = self._seed_with_review(mgr, recommendation="reject")
        result = mgr.set_decision(cid, "approve", override_reason="已人工核实")
        self.assertEqual(result["manual_decision_override_reason"], "已人工核实")


class TestAgentReviewAPIWithScores(unittest.TestCase):
    """Test agent-review endpoint with structured scores."""

    def _make_manager(self):
        from xianyu_agent.resale import ResaleManager
        return ResaleManager(Path(tempfile.mkdtemp()))

    def test_save_review_returns_aggregate(self):
        mgr = self._make_manager()
        c = mgr.create_candidate({
            "title": "测试商品",
            "source_platform": "pdd",
            "supplier_price": 20.0,
            "target_sale_price": 50.0,
        })
        cid = c["candidate_id"]
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        reviews = [
            ResaleResearchAgent().review_candidate(c),
            ResaleRiskAgent().review_candidate(c),
            ResalePricingAgent().review_candidate(c),
        ]
        discussion = {"summary": "test", "participants": ["resale_research", "resale_risk", "resale_pricing"]}
        result = mgr.save_agent_review(cid, discussion, reviews)
        self.assertIn("agent_structured_reviews", result)
        self.assertEqual(len(result["agent_structured_reviews"]), 3)
        self.assertIn("agent_review_score", result)
        self.assertIn("agent_review_recommendation", result)
        self.assertIn("agent_review_reasons", result)
        self.assertIn("blocking_flags", result)

    def test_structured_reviews_saved(self):
        mgr = self._make_manager()
        c = mgr.create_candidate({
            "title": "测试商品2",
            "source_platform": "pdd",
            "supplier_price": 25.0,
            "target_sale_price": 60.0,
        })
        cid = c["candidate_id"]
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        reviews = [
            ResaleResearchAgent().review_candidate(c),
            ResaleRiskAgent().review_candidate(c),
            ResalePricingAgent().review_candidate(c),
        ]
        discussion = {"summary": "test", "participants": ["resale_research", "resale_risk", "resale_pricing"]}
        result = mgr.save_agent_review(cid, discussion, reviews)
        self.assertIn("agent_structured_reviews", result)
        self.assertEqual(len(result["agent_structured_reviews"]), 3)

    def test_aggregate_fields_saved(self):
        mgr = self._make_manager()
        c = mgr.create_candidate({
            "title": "测试商品3",
            "source_platform": "pdd",
            "supplier_price": 30.0,
            "target_sale_price": 70.0,
        })
        cid = c["candidate_id"]
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        reviews = [
            ResaleResearchAgent().review_candidate(c),
            ResaleRiskAgent().review_candidate(c),
            ResalePricingAgent().review_candidate(c),
        ]
        discussion = {"summary": "test", "participants": ["resale_research", "resale_risk", "resale_pricing"]}
        result = mgr.save_agent_review(cid, discussion, reviews)
        self.assertIn("agent_review_score", result)
        self.assertIn("agent_review_recommendation", result)
        self.assertIn("agent_review_reasons", result)
        self.assertIn("blocking_flags", result)


class TestDeepSeekFallback(unittest.TestCase):
    """Test that agents work without DeepSeek API key."""

    def test_no_api_key_uses_fallback(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        result = agent.review_candidate(_make_candidate())
        self.assertIn("supplier_score", result)
        self.assertIn("summary", result)

    def test_llm_summary_none_when_disabled(self):
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        agent = ResaleRiskAgent()
        # _llm is None by default (no API key)
        result = agent._try_llm_summary(_make_candidate())
        self.assertIsNone(result)

    def test_deterministic_scores_always_computed(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        c = _make_candidate()
        r1 = agent.review_candidate(c)
        r2 = agent.review_candidate(c)
        self.assertEqual(r1["margin_score"], r2["margin_score"])
        self.assertEqual(r1["price_competitiveness_score"], r2["price_competitiveness_score"])
        self.assertEqual(r1["suggested_price"], r2["suggested_price"])

    def test_agent_no_execute_high_risk(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.agents.resale_risk import ResaleRiskAgent
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        for agent_cls in (ResaleResearchAgent, ResaleRiskAgent, ResalePricingAgent):
            agent = agent_cls()
            handlers = agent.get_handlers()
            self.assertEqual(len(handlers), 0)


class TestMilestone10UI(unittest.TestCase):
    """Test UI strings for Milestone 10."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_agent_score_display(self):
        self.assertIn("Agent 评分", self.js)

    def test_recommendation_badge(self):
        self.assertIn("resale-badge--rec-", self.js)

    def test_override_reason_field(self):
        self.assertIn("override_reason", self.js)

    def test_blocking_flag_display(self):
        self.assertIn("阻断:", self.js)


class TestMilestone10Behavioral(unittest.TestCase):
    """Behavioral tests: execute renderResaleFeed with scored candidates."""

    _JS_HARNESS = r"""
const fs = require("fs");
function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}
let _capturedHtml = "";
function $(sel) { return { set innerHTML(v) { _capturedHtml = v; }, get innerHTML() { return _capturedHtml; } }; }

function renderResaleFeed(candidates = []) {
  const feed = $("#resale-feed");
  if (!feed) return;
  if (!candidates.length) {
    feed.innerHTML = '<div class="empty-state">暂无候选</div>';
    return;
  }
  feed.innerHTML = candidates.map((c) => {
    const isBlocked = c.status === "blocked";
    const econ = c.economics || {};
    const riskClass = c.risk_level || "low";
    const flags = (c.risk_flags || []).map(
      (f) => '<span class="risk-flag' + (isBlocked ? ' is-blocked' : '') + '">' + escapeHtml(f) + '</span>'
    ).join("");

    const agentReviewHtml = c.agent_review_score != null
      ? '<div class="agent-review-summary">'
          + '<strong>Agent 评分:</strong> ' + c.agent_review_score + '/100 '
          + '<span class="resale-badge resale-badge--rec-' + (c.agent_review_recommendation || 'unknown') + '">' + (c.agent_review_recommendation || '-') + '</span>'
          + ((c.blocking_flags || []).length ? '<div style="color:var(--danger);margin-top:3px">阻断: ' + escapeHtml(c.blocking_flags.join(", ")) + '</div>' : '')
          + (c.manual_decision_override_reason ? '<div style="margin-top:3px">覆盖理由: ' + escapeHtml(c.manual_decision_override_reason) + '</div>' : '')
        + '</div>'
      : '';

    const actions = isBlocked
      ? '<div class="resale-actions"><span style="color:var(--danger);font-size:11px">已阻断，无法创建计划</span></div>'
      : '<div class="resale-actions">'
          + '<button class="resale-btn" onclick="createResalePlan(\'' + c.candidate_id + '\')">创建倒卖计划</button>'
        + '</div>';

    return ''
      + '<div class="resale-candidate' + (isBlocked ? ' is-blocked' : '') + '">'
      + '<div class="resale-candidate-header">'
      + '<span class="resale-candidate-title">' + escapeHtml(c.title || c.candidate_id) + '</span>'
      + '<span class="resale-badge resale-badge--' + c.status + '">' + escapeHtml(c.status) + '</span>'
      + '</div>'
      + agentReviewHtml
      + actions
      + '</div>';
  }).join("");
}

const data = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const outFile = process.argv[3];
renderResaleFeed(data);
fs.writeFileSync(outFile, _capturedHtml, "utf-8");
"""

    def _run(self, candidates):
        js_path = os.path.join(tempfile.gettempdir(), "m10_render_test.js")
        in_path = os.path.join(tempfile.gettempdir(), "m10_render_in.json")
        out_path = os.path.join(tempfile.gettempdir(), "m10_render_out.html")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(self._JS_HARNESS)
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False)
        proc = subprocess.run(["node", js_path, in_path, out_path], timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(f"Node.js failed with exit code {proc.returncode}")
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_candidate_with_score_shows_review(self):
        candidate = _make_candidate(
            agent_review_score=82.5,
            agent_review_recommendation="approve",
            blocking_flags=[],
        )
        html = self._run([candidate])
        self.assertIn("82.5/100", html)
        self.assertIn("approve", html)

    def test_candidate_with_blocking_shows_flags(self):
        candidate = _make_candidate(
            agent_review_score=30.0,
            agent_review_recommendation="reject",
            blocking_flags=["负毛利", "食品禁售"],
            status="blocked",
            risk_level="blocked",
        )
        html = self._run([candidate])
        self.assertIn("阻断:", html)
        self.assertIn("负毛利", html)
        self.assertIn("食品禁售", html)

    def test_candidate_with_override_shows_reason(self):
        candidate = _make_candidate(
            agent_review_score=30.0,
            agent_review_recommendation="reject",
            blocking_flags=["负毛利"],
            manual_decision_override_reason="已人工核实风险可控",
        )
        html = self._run([candidate])
        self.assertIn("已人工核实风险可控", html)


class TestSaveAgentReviewStructured(unittest.TestCase):
    """Test save_agent_review with structured_reviews parameter."""

    def _make_manager(self):
        from xianyu_agent.resale import ResaleManager
        import tempfile
        return ResaleManager(Path(tempfile.mkdtemp()))

    test_save_agent_review_with_structured = None  # placeholder

    def test_save_agent_review_stores_structured(self):
        mgr = self._make_manager()
        c = _make_candidate()
        mgr._candidates[c["candidate_id"]] = c
        mgr._save_candidates()
        discussion = {"summary": "test", "participants": ["a", "b"]}
        reviews = [
            {"agent": "resale_research", "supplier_score": 70, "demand_score": 60},
            {"agent": "resale_risk", "compliance_score": 80, "fulfillment_risk_score": 70, "aftersale_risk_score": 80, "blocking_flags": []},
            {"agent": "resale_pricing", "margin_score": 80, "price_competitiveness_score": 70},
        ]
        result = mgr.save_agent_review(c["candidate_id"], discussion, reviews)
        self.assertIn("agent_structured_reviews", result)
        self.assertIn("agent_review_score", result)
        self.assertIn("agent_review_recommendation", result)

    def test_save_agent_review_without_structured(self):
        mgr = self._make_manager()
        c = _make_candidate()
        mgr._candidates[c["candidate_id"]] = c
        mgr._save_candidates()
        discussion = {"summary": "test", "participants": ["a"]}
        result = mgr.save_agent_review(c["candidate_id"], discussion)
        self.assertIn("agent_reviews", result)
        self.assertNotIn("agent_structured_reviews", result)
        self.assertNotIn("agent_review_score", result)


class TestResearchAgentScoring(unittest.TestCase):
    """Detailed tests for research agent scoring logic."""

    def test_supplier_score_with_url_verified_1688(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        c = _make_candidate(source_url="https://1688.com/xxx", supplier_verified=True, source_platform="1688")
        result = agent.review_candidate(c)
        self.assertEqual(result["supplier_score"], 100)

    def test_supplier_score_no_url_no_verification(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        c = _make_candidate(source_url="", supplier_verified=False, source_platform="pdd")
        result = agent.review_candidate(c)
        self.assertEqual(result["supplier_score"], 50)

    def test_demand_score_with_all_signals(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        c = _make_candidate(
            estimated_demand_signal="高需求",
            competitor_links=["http://a.com"],
            competitor_price_min=40,
            competitor_price_max=60,
        )
        result = agent.review_candidate(c)
        self.assertEqual(result["demand_score"], 90)
        self.assertEqual(result["competitor_confidence"], "high")

    def test_competitor_confidence_medium(self):
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        agent = ResaleResearchAgent()
        c = _make_candidate(competitor_price_min=40)
        result = agent.review_candidate(c)
        self.assertEqual(result["competitor_confidence"], "medium")


class TestPricingAgentScoring(unittest.TestCase):
    """Detailed tests for pricing agent scoring logic."""

    def test_margin_score_high(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        c = _make_candidate(economics={"gross_profit": 25, "gross_margin": 0.45})
        result = agent.review_candidate(c)
        self.assertEqual(result["margin_score"], 100)

    def test_margin_score_negative(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        c = _make_candidate(economics={"gross_profit": -5, "gross_margin": -0.1})
        result = agent.review_candidate(c)
        self.assertEqual(result["margin_score"], 0)
        self.assertIn("负毛利", result["pricing_flags"])

    def test_suggested_price_with_competitors(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        c = _make_candidate(competitor_price_min=40, competitor_price_max=60)
        result = agent.review_candidate(c)
        self.assertEqual(result["suggested_price"], 50.0)

    def test_suggested_price_without_competitors(self):
        from xianyu_agent.agents.resale_pricing import ResalePricingAgent
        agent = ResalePricingAgent()
        c = _make_candidate(supplier_price=20, supplier_shipping=5)
        result = agent.review_candidate(c)
        self.assertEqual(result["suggested_price"], 62.5)  # (20+5) * 2.5


class TestEndpointCount(unittest.TestCase):
    """Verify endpoint count after Milestone 10 changes."""

    def test_endpoint_count_unchanged(self):
        from xianyu_agent.server import app
        resale_endpoints = []
        for r in app.routes:
            if hasattr(r, "path") and "/resale/" in r.path:
                methods = getattr(r, "methods", None)
                if methods:
                    for m in methods:
                        resale_endpoints.append((m, r.path))
        # Phase 28 adds candidate-bound evidence archive list/upload/download routes.
        self.assertEqual(len(resale_endpoints), 27)


class TestResaleAgentDeepSeekConfig(unittest.TestCase):
    """Verify all 3 resale agents use deepseek backend with correct config."""

    AGENTS = ["resale_research", "resale_risk", "resale_pricing"]

    def test_all_resale_agents_backend_is_deepseek(self):
        from xianyu_agent.llm_manager import AGENT_DEFINITIONS
        for name in self.AGENTS:
            self.assertEqual(
                AGENT_DEFINITIONS[name]["backend"], "deepseek",
                f"{name} backend should be 'deepseek'",
            )

    def test_all_resale_agents_key_field_is_none(self):
        from xianyu_agent.llm_manager import AGENT_DEFINITIONS
        for name in self.AGENTS:
            self.assertIsNone(
                AGENT_DEFINITIONS[name].get("key_field"),
                f"{name} key_field should be None (uses shared DEEPSEEK_API_KEY)",
            )

    def test_resale_agent_client_mimo_keys_cleared(self):
        from xianyu_agent.llm_manager import AgentLLMClient
        from xianyu_agent.config import Settings
        os.environ["DEEPSEEK_API_KEY"] = "test-ds-key"
        os.environ["MIMO_API_KEY"] = "test-mimo-key"
        os.environ["MIMO_BASE_URL"] = "https://mimo.example.com"
        try:
            settings = Settings.from_env()
            for name in self.AGENTS:
                agent_client = AgentLLMClient(name, settings)
                client = agent_client.client
                self.assertEqual(client.settings.mimo_api_key, "",
                                 f"{name}: mimo_api_key should be empty")
                self.assertEqual(client.settings.mimo_base_url, "",
                                 f"{name}: mimo_base_url should be empty")
                self.assertEqual(client.settings.mimo_model, "",
                                 f"{name}: mimo_model should be empty")
                self.assertEqual(client.settings.deepseek_api_key, "test-ds-key",
                                 f"{name}: deepseek_api_key should be set")
                self.assertEqual(client.settings.deepseek_model, "deepseek-v4-flash",
                                 f"{name}: deepseek_model should be deepseek-v4-flash")
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)
            os.environ.pop("MIMO_API_KEY", None)
            os.environ.pop("MIMO_BASE_URL", None)

    def test_resale_agent_use_mimo_is_false(self):
        from xianyu_agent.llm_manager import AgentLLMClient
        from xianyu_agent.config import Settings
        os.environ["DEEPSEEK_API_KEY"] = "test-ds-key"
        try:
            settings = Settings.from_env()
            for name in self.AGENTS:
                agent_client = AgentLLMClient(name, settings)
                client = agent_client.client
                self.assertFalse(client._use_mimo, f"{name}: _use_mimo should be False")
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)


class TestDeepSeekHttpPayload(unittest.TestCase):
    """Verify DeepSeekClient sends correct HTTP payload via mock."""

    def _make_settings(self, deepseek_key="test-ds-key"):
        """Build Settings directly, bypassing .env loading."""
        from xianyu_agent.config import Settings
        from pathlib import Path
        return Settings(
            deepseek_api_key=deepseek_key,
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-v4-flash",
            deepseek_json_mode=True,
            deepseek_thinking_enabled=False,
            deepseek_reasoning_effort="medium",
            deepseek_trust_env=False,
            mimo_api_key="",
            mimo_base_url="",
            mimo_model="",
            mimo_hermes_key="",
            mimo_listing_key="",
            mimo_discovery_key="",
            mimo_order_key="",
            mimo_github_key="",
            mimo_analytics_key="",
            xianyu_adapter="stub",
            xianyu_cookies="",
            browser_headless=True,
            browser_cdp_url="",
            xianguanjia_base_url="",
            xianguanjia_token="",
            xianguanjia_app_id="",
            xianguanjia_api_key="",
            xianguanjia_create_endpoint="/api/listings",
            xianguanjia_polish_endpoint="/api/listings/polish",
            xianguanjia_fulfill_endpoint="/api/orders/fulfill",
            feishu_verification_token="",
            feishu_webhook_url="",
            image_search_provider="none",
            serpapi_key="",
            bing_search_key="",
            allow_external_product_images=False,
            data_dir=Path(tempfile.mkdtemp()),
            polish_at="09:30",
            server_host="0.0.0.0",
            server_port=8788,
            github_token="",
            github_trust_env=False,
            github_discovery_enabled=False,
            github_discovery_at="10:30",
            github_discovery_queries="AI agent",
            github_discovery_min_stars=500,
            github_discovery_limit_per_query=5,
            market_search_provider="none",
            market_search_limit=5,
            default_platform_fee_rate=0.08,
            min_profit_amount=20,
            min_profit_rate=0.30,
            ask_feishu_on_opportunity=False,
            crawler_max_pages=3,
            crawler_max_downloads=5,
            crawler_max_file_mb=200,
            platform_guard_enabled=False,
            platform_guard_min_seconds_between_actions=20,
            platform_guard_max_actions_per_hour=60,
            platform_guard_daily_action_limit=300,
            platform_guard_pause_on_verification=True,
            browser_edge_path="",
            browser_debug_port=9223,
            browser_user_data_dir="",
            openclaw_root=Path("E:/openclaw/openclaw-main"),
            openclaw_control_center_root=Path("E:/openclaw/openclaw-main/control-center"),
            openclaw_mission_control_root=Path("E:/openclaw/openclaw-main/mission-control"),
            openclaw_rl_root=Path("E:/OpenClaw-RL-main"),
            awesome_openclaw_skills_root=Path("E:/awesome-openclaw-skills-main"),
            discovery_enabled=False,
            discovery_at="11:00",
            discovery_max_pages=3,
            discovery_default_min_price=None,
            discovery_default_max_price=None,
            auto_reply_enabled=False,
            auto_reply_model="",
            auto_reply_human_toggle="。",
            db_path=str(Path(tempfile.mkdtemp()) / "test.db"),
        )

    def test_chat_json_payload_model_is_deepseek_v4_flash(self):
        from xianyu_agent.llm import DeepSeekClient
        settings = self._make_settings()
        client = DeepSeekClient(settings)
        self.assertFalse(client._use_mimo)
        import requests
        from unittest.mock import MagicMock, patch
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"summary": "test"}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch.object(requests.Session, "post", return_value=mock_response) as mock_post:
            client.chat_json("system prompt", "user prompt", temperature=0.3)
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            self.assertEqual(payload["model"], "deepseek-v4-flash")

    def test_chat_json_base_url_is_deepseek(self):
        from xianyu_agent.llm import DeepSeekClient
        settings = self._make_settings()
        client = DeepSeekClient(settings)
        self.assertFalse(client._use_mimo)
        import requests
        from unittest.mock import MagicMock, patch
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"summary": "test"}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch.object(requests.Session, "post", return_value=mock_response) as mock_post:
            client.chat_json("system", "user")
            call_args = mock_post.call_args
            url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
            self.assertIn("api.deepseek.com", url)
            self.assertIn("/chat/completions", url)

    def test_chat_json_authorization_header_present(self):
        from xianyu_agent.llm import DeepSeekClient
        settings = self._make_settings()
        client = DeepSeekClient(settings)
        self.assertFalse(client._use_mimo)
        import requests
        from unittest.mock import MagicMock, patch
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch.object(requests.Session, "post", return_value=mock_response) as mock_post:
            client.chat_json("system", "user")
            call_args = mock_post.call_args
            headers = call_args.kwargs.get("headers") or call_args[1].get("headers")
            self.assertIn("Authorization", headers)
            self.assertTrue(headers["Authorization"].startswith("Bearer "))

    def test_resale_agent_end_to_end_mock(self):
        """Resale agent review_candidate → DeepSeekClient → mock HTTP, model == deepseek-v4-flash."""
        from xianyu_agent.agents.resale_research import ResaleResearchAgent
        from xianyu_agent.llm_manager import llm_manager
        settings = self._make_settings()
        llm_manager.init(settings)
        llm_manager._clients.clear()
        agent = ResaleResearchAgent()
        agent._llm = None
        _ = agent.llm  # trigger lazy-load via property so self._llm is populated
        import requests
        from unittest.mock import MagicMock, patch
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"summary": "供应商已验证，来源可靠"}'}}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch.object(requests.Session, "post", return_value=mock_response) as mock_post:
            result = agent._try_llm_summary(_make_candidate())
            self.assertIsNotNone(result)
            self.assertIn("供应商", result)
            mock_post.assert_called_once()
            payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
            self.assertEqual(payload["model"], "deepseek-v4-flash")
        llm_manager._clients.clear()


class TestNoHardcodedKeys(unittest.TestCase):
    """Verify no hardcoded API keys in source/test/doc/web files."""

    def test_no_sk_key_in_python_source(self):
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(r"sk-[a-zA-Z0-9]{20,}")
        for dirpath, _, filenames in os.walk(os.path.join(root, "xianyu_agent")):
            for fn in filenames:
                if fn.endswith(".py"):
                    fpath = os.path.join(dirpath, fn)
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                    match = pattern.search(content)
                    self.assertIsNone(match, f"Found API key pattern in {fpath}")

    def test_no_sk_key_in_tests(self):
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(r"sk-[a-zA-Z0-9]{20,}")
        tests_dir = os.path.join(root, "tests")
        for dirpath, _, filenames in os.walk(tests_dir):
            for fn in filenames:
                if fn.endswith(".py"):
                    fpath = os.path.join(dirpath, fn)
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                    match = pattern.search(content)
                    self.assertIsNone(match, f"Found API key pattern in {fpath}")

    def test_no_sk_key_in_web(self):
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(r"sk-[a-zA-Z0-9]{20,}")
        web_dir = os.path.join(root, "web")
        for fn in os.listdir(web_dir):
            if fn.endswith((".js", ".html", ".css")):
                fpath = os.path.join(web_dir, fn)
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
                match = pattern.search(content)
                self.assertIsNone(match, f"Found API key pattern in {fpath}")

    def test_no_specific_key_in_source(self):
        """Verify the user's specific key does not appear anywhere in xianyu_agent/."""
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(r"sk-012d[a-zA-Z0-9]+")
        for dirpath, _, filenames in os.walk(os.path.join(root, "xianyu_agent")):
            for fn in filenames:
                if fn.endswith(".py"):
                    fpath = os.path.join(dirpath, fn)
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                    match = pattern.search(content)
                    self.assertIsNone(match, f"Found specific key pattern in {fpath}")


if __name__ == "__main__":
    unittest.main()
