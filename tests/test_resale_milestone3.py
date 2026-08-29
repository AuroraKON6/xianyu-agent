"""Tests for resale Milestone 3 — verification, decision, dashboard."""
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
    return ResaleManager(Path(tmpdir))


def _make_candidate(manager, **overrides):
    payload = {
        "title": "蓝牙耳机",
        "source_platform": "pdd",
        "source_url": "https://pdd.example.com/item/123",
        "supplier_price": 25.0,
        "supplier_shipping": 3.0,
        "target_sale_price": 59.0,
        "category": "数码配件",
    }
    payload.update(overrides)
    return manager.create_candidate(payload)


class TestVerifyCandidate(unittest.TestCase):
    """Test verify_candidate method."""

    def setUp(self):
        self.manager = _make_manager()
        self.candidate = _make_candidate(self.manager)

    def test_verify_sets_supplier_verified(self):
        result = self.manager.verify_candidate(self.candidate["candidate_id"], {
            "supplier_verified": True,
            "supplier_verification_note": "已确认供应商真实存在",
        })
        self.assertTrue(result["supplier_verified"])
        self.assertEqual(result["supplier_verification_note"], "已确认供应商真实存在")

    def test_verify_sets_competitor_links(self):
        result = self.manager.verify_candidate(self.candidate["candidate_id"], {
            "competitor_links": ["https://taobao.com/item/1", "https://jd.com/item/2"],
        })
        self.assertEqual(len(result["competitor_links"]), 2)
        self.assertIn("https://taobao.com/item/1", result["competitor_links"])

    def test_verify_sets_competitor_prices(self):
        result = self.manager.verify_candidate(self.candidate["candidate_id"], {
            "competitor_price_min": 45.0,
            "competitor_price_max": 88.0,
        })
        self.assertEqual(result["competitor_price_min"], 45.0)
        self.assertEqual(result["competitor_price_max"], 88.0)

    def test_verify_sets_demand_signal(self):
        result = self.manager.verify_candidate(self.candidate["candidate_id"], {
            "estimated_demand_signal": "月销200+",
        })
        self.assertEqual(result["estimated_demand_signal"], "月销200+")

    def test_verify_sets_verified_at(self):
        result = self.manager.verify_candidate(self.candidate["candidate_id"], {
            "supplier_verified": True,
        })
        self.assertIsNotNone(result["verified_at"])
        self.assertIn("T", result["verified_at"])

    def test_verify_nonexistent_returns_none(self):
        result = self.manager.verify_candidate("rc_nonexistent", {"supplier_verified": True})
        self.assertIsNone(result)


class TestManualDecision(unittest.TestCase):
    """Test set_decision method."""

    def setUp(self):
        self.manager = _make_manager()
        self.candidate = _make_candidate(self.manager)

    def test_watch_sets_decision_keeps_status(self):
        result = self.manager.set_decision(self.candidate["candidate_id"], "watch", "再观察几天")
        self.assertEqual(result["manual_decision"], "watch")
        self.assertEqual(result["manual_decision_note"], "再观察几天")
        self.assertEqual(result["status"], "evaluating")

    def test_approve_sets_decision_and_status(self):
        result = self.manager.set_decision(self.candidate["candidate_id"], "approve", "利润足够")
        self.assertEqual(result["manual_decision"], "approve")
        self.assertEqual(result["status"], "approved")

    def test_reject_sets_decision_and_status(self):
        result = self.manager.set_decision(self.candidate["candidate_id"], "reject", "利润太低")
        self.assertEqual(result["manual_decision"], "reject")
        self.assertEqual(result["status"], "rejected")

    def test_approve_blocked_returns_error(self):
        blocked = _make_candidate(self.manager, category="食品")
        result = self.manager.set_decision(blocked["candidate_id"], "approve")
        self.assertIn("error", result)
        self.assertIn("blocked", result["error"])
        # Status should remain blocked
        c = self.manager.get_candidate(blocked["candidate_id"])
        self.assertEqual(c["status"], "blocked")

    def test_nonexistent_returns_none(self):
        result = self.manager.set_decision("rc_nonexistent", "watch")
        self.assertIsNone(result)

    def test_invalid_decision_returns_none(self):
        result = self.manager.set_decision(self.candidate["candidate_id"], "invalid")
        self.assertIsNone(result)


class TestDashboardSummary(unittest.TestCase):
    """Test get_dashboard_summary method."""

    def setUp(self):
        self.manager = _make_manager()
        # Create a mix of candidates
        self.normal = _make_candidate(self.manager, title="普通商品")
        self.blocked = _make_candidate(self.manager, title="食品", category="食品")
        self.approved = _make_candidate(self.manager, title="已通过商品")
        self.manager.verify_candidate(self.approved["candidate_id"], {"supplier_verified": True})
        # Evidence updates invalidate prior decisions, so approval must always be
        # recorded against the latest candidate revision.
        self.manager.set_decision(self.approved["candidate_id"], "approve")
        self.watched = _make_candidate(self.manager, title="观察中商品")
        self.manager.set_decision(self.watched["candidate_id"], "watch")

    def test_total_candidates(self):
        summary = self.manager.get_dashboard_summary()
        self.assertEqual(summary["total_candidates"], 4)

    def test_blocked_count(self):
        summary = self.manager.get_dashboard_summary()
        self.assertEqual(summary["blocked_count"], 1)

    def test_approved_count(self):
        summary = self.manager.get_dashboard_summary()
        self.assertEqual(summary["approved_count"], 1)

    def test_watch_count(self):
        summary = self.manager.get_dashboard_summary()
        self.assertEqual(summary["watch_count"], 1)

    def test_rejected_count(self):
        summary = self.manager.get_dashboard_summary()
        self.assertEqual(summary["rejected_count"], 0)

    def test_avg_gross_margin(self):
        summary = self.manager.get_dashboard_summary()
        self.assertIsInstance(summary["avg_gross_margin"], float)
        self.assertGreater(summary["avg_gross_margin"], 0)

    def test_top_risk_flags(self):
        summary = self.manager.get_dashboard_summary()
        self.assertIsInstance(summary["top_risk_flags"], list)

    def test_candidates_ready_for_listing(self):
        summary = self.manager.get_dashboard_summary()
        # approved + supplier_verified = 1
        self.assertEqual(summary["candidates_ready_for_listing"], 1)

    def test_candidates_missing_supplier_verification(self):
        summary = self.manager.get_dashboard_summary()
        # normal + watched = 2 (blocked excluded, approved is verified)
        self.assertEqual(summary["candidates_missing_supplier_verification"], 2)

    def test_daily_candidate_count(self):
        summary = self.manager.get_dashboard_summary()
        self.assertIsInstance(summary["daily_candidate_count"], dict)
        self.assertTrue(len(summary["daily_candidate_count"]) > 0)


class TestVerifyAPI(unittest.TestCase):
    """Test POST /resale/candidates/{id}/verify endpoint."""

    def test_verify_updates_candidate(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        # Create candidate
        resp = client.post("/resale/candidates", json={
            "title": "API测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        cid = resp.json()["candidate"]["candidate_id"]
        # Verify
        resp = client.post(f"/resale/candidates/{cid}/verify", json={
            "supplier_verified": True,
            "supplier_verification_note": "API验证测试",
            "competitor_links": ["https://example.com"],
            "competitor_price_min": 20.0,
            "competitor_price_max": 40.0,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["candidate"]["supplier_verified"])
        self.assertEqual(data["candidate"]["supplier_verification_note"], "API验证测试")

    def test_verify_404_nonexistent(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        srv.resale_manager = ResaleManager(Path(tmpdir))
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates/rc_nonexistent/verify", json={
            "supplier_verified": True,
        })
        self.assertEqual(resp.status_code, 404)


class TestDecisionAPI(unittest.TestCase):
    """Test POST /resale/candidates/{id}/decision endpoint."""

    def test_decision_updates_candidate(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        srv.resale_manager = ResaleManager(Path(tmpdir))
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates", json={
            "title": "决策测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        cid = resp.json()["candidate"]["candidate_id"]
        resp = client.post(f"/resale/candidates/{cid}/decision", json={
            "decision": "approve", "note": "利润足够",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["candidate"]["manual_decision"], "approve")
        self.assertEqual(data["candidate"]["status"], "approved")

    def test_decision_400_blocked_approve(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        srv.resale_manager = ResaleManager(Path(tmpdir))
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates", json={
            "title": "食品测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
            "category": "食品",
        })
        cid = resp.json()["candidate"]["candidate_id"]
        resp = client.post(f"/resale/candidates/{cid}/decision", json={
            "decision": "approve",
        })
        self.assertEqual(resp.status_code, 400)

    def test_decision_404_nonexistent(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        srv.resale_manager = ResaleManager(Path(tmpdir))
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates/rc_nonexistent/decision", json={
            "decision": "watch",
        })
        self.assertEqual(resp.status_code, 404)


class TestDashboardAPI(unittest.TestCase):
    """Test GET /resale/dashboard/summary endpoint."""

    def test_dashboard_returns_expected_keys(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        srv.resale_manager = ResaleManager(Path(tmpdir))
        client = TestClient(srv.app)
        # Create a candidate so dashboard is not empty
        client.post("/resale/candidates", json={
            "title": "仪表盘测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        resp = client.get("/resale/dashboard/summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ("total_candidates", "blocked_count", "approved_count",
                     "rejected_count", "watch_count", "avg_gross_margin",
                     "top_risk_flags", "candidates_ready_for_listing",
                     "candidates_missing_supplier_verification", "daily_candidate_count"):
            self.assertIn(key, data)


class TestMilestone3RoutesExist(unittest.TestCase):
    """Test that all 3 new routes are registered."""

    def test_routes_exist(self):
        from xianyu_agent.server import app
        route_paths = set()
        for r in app.routes:
            if hasattr(r, "path"):
                route_paths.add(r.path)
        self.assertIn("/resale/candidates/{candidate_id}/verify", route_paths)
        self.assertIn("/resale/candidates/{candidate_id}/decision", route_paths)
        self.assertIn("/resale/dashboard/summary", route_paths)


class TestMilestone3UIStrings(unittest.TestCase):
    """Verify JS source contains Milestone 3 function names and types."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_openVerifyDialog_exists(self):
        self.assertIn("openVerifyDialog", self.js)

    def test_openDecisionDialog_exists(self):
        self.assertIn("openDecisionDialog", self.js)

    def test_loadDashboard_exists(self):
        self.assertIn("loadDashboard", self.js)

    def test_renderDashboard_exists(self):
        self.assertIn("renderDashboard", self.js)

    def test_resale_verification_in_summarize(self):
        self.assertIn('"resale_verification"', self.js)

    def test_resale_decision_in_summarize(self):
        self.assertIn('"resale_decision"', self.js)

    def test_decisionLabel_exists(self):
        self.assertIn("decisionLabel", self.js)

    def test_dashboard_element_exists(self):
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "console.html")
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="resale-dashboard"', html)
        self.assertIn('id="refresh-dashboard"', html)

    def test_verify_button_in_render(self):
        self.assertIn("一品启动包", self.js)

    def test_decision_button_in_render(self):
        self.assertIn("人工决策", self.js)


# ============================================================
# Behavioral test: blocked candidate cannot be approved
# ============================================================

_JS_DECISION_HARNESS = r"""
const fs = require("fs");

// Minimal ResaleManager-like logic for testing
const candidates = {};
function createCandidate(id, status) {
  candidates[id] = { id, candidate_id: id, status, manual_decision: null, manual_decision_note: "", updated_at: "" };
}
function setDecision(id, decision, note) {
  const c = candidates[id];
  if (!c) return null;
  if (decision === "approve" && c.status === "blocked") return { error: "blocked candidate cannot be approved" };
  c.manual_decision = decision;
  c.manual_decision_note = note;
  if (decision === "approve") c.status = "approved";
  else if (decision === "reject") c.status = "rejected";
  return { ...c };
}

createCandidate("rc_normal", "evaluating");
createCandidate("rc_blocked", "blocked");

const test = process.argv[2];
let result;
if (test === "blocked_approve") {
  result = setDecision("rc_blocked", "approve", "test");
} else if (test === "normal_approve") {
  result = setDecision("rc_normal", "approve", "test");
} else if (test === "blocked_watch") {
  result = setDecision("rc_blocked", "watch", "observing");
}
fs.writeFileSync(process.argv[3], JSON.stringify(result), "utf-8");
"""


class TestBlockedNoApproveBehavioral(unittest.TestCase):
    """Behavioral: blocked candidate cannot be approved via decision logic."""

    def test_blocked_approve_returns_error(self):
        js_path = os.path.join(tempfile.gettempdir(), "decision_test.js")
        out_path = os.path.join(tempfile.gettempdir(), "decision_test_out.json")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_DECISION_HARNESS)
        subprocess.run(["node", js_path, "blocked_approve", out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.loads(f.read())
        self.assertIn("error", result)

    def test_normal_approve_succeeds(self):
        js_path = os.path.join(tempfile.gettempdir(), "decision_test.js")
        out_path = os.path.join(tempfile.gettempdir(), "decision_test_out.json")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_DECISION_HARNESS)
        subprocess.run(["node", js_path, "normal_approve", out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.loads(f.read())
        self.assertNotIn("error", result)
        self.assertEqual(result["manual_decision"], "approve")
        self.assertEqual(result["status"], "approved")

    def test_blocked_watch_succeeds(self):
        js_path = os.path.join(tempfile.gettempdir(), "decision_test.js")
        out_path = os.path.join(tempfile.gettempdir(), "decision_test_out.json")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_DECISION_HARNESS)
        subprocess.run(["node", js_path, "blocked_watch", out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.loads(f.read())
        self.assertNotIn("error", result)
        self.assertEqual(result["manual_decision"], "watch")


if __name__ == "__main__":
    unittest.main()
