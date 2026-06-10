"""Tests for resale Milestone 4 — candidate → listing draft (no auto-publish)."""
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


def _make_ready_candidate(manager):
    """Create an approved + verified candidate (ready for listing)."""
    c = manager.create_candidate({
        "title": "蓝牙耳机 TWS",
        "source_platform": "pdd",
        "source_url": "https://pdd.example.com/item/123",
        "supplier_price": 25.0,
        "supplier_shipping": 3.0,
        "target_sale_price": 59.0,
        "category": "数码配件",
        "specifications": {"color": "black", "type": "TWS"},
        "description": "高品质蓝牙耳机",
    })
    manager.verify_candidate(c["candidate_id"], {"supplier_verified": True})
    manager.set_decision(c["candidate_id"], "approve")
    return manager.get_candidate(c["candidate_id"])


class TestCreateListingDraft(unittest.TestCase):
    """Test create_listing_draft method."""

    def setUp(self):
        self.manager = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)

    def test_ready_candidate_creates_draft(self):
        result = self.manager.create_listing_draft(self.candidate["candidate_id"])
        self.assertNotIn("error", result)
        self.assertEqual(result["title"], "蓝牙耳机 TWS")
        self.assertEqual(result["price"], 59.0)
        self.assertEqual(result["stock"], 1)
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["delivery_type"], "shipping")
        self.assertEqual(result["supplier_verified"], True)
        self.assertIn("resale:", result["source"])

    def test_draft_has_correct_candidate_id(self):
        result = self.manager.create_listing_draft(self.candidate["candidate_id"])
        self.assertEqual(result["candidate_id"], self.candidate["candidate_id"])

    def test_blocked_candidate_returns_error(self):
        blocked = self.manager.create_candidate({
            "title": "食品", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
            "category": "食品",
        })
        self.manager.verify_candidate(blocked["candidate_id"], {"supplier_verified": True})
        result = self.manager.create_listing_draft(blocked["candidate_id"])
        self.assertIn("error", result)
        self.assertIn("blocked", result["error"])

    def test_unverified_candidate_returns_error(self):
        unverified = self.manager.create_candidate({
            "title": "未验证商品", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        self.manager.set_decision(unverified["candidate_id"], "approve")
        result = self.manager.create_listing_draft(unverified["candidate_id"])
        self.assertIn("error", result)
        self.assertIn("not verified", result["error"])

    def test_watch_decision_returns_error(self):
        watched = self.manager.create_candidate({
            "title": "观察中", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        self.manager.verify_candidate(watched["candidate_id"], {"supplier_verified": True})
        self.manager.set_decision(watched["candidate_id"], "watch")
        result = self.manager.create_listing_draft(watched["candidate_id"])
        self.assertIn("error", result)
        self.assertIn("approve", result["error"])

    def test_reject_decision_returns_error(self):
        rejected = self.manager.create_candidate({
            "title": "已拒绝", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        self.manager.verify_candidate(rejected["candidate_id"], {"supplier_verified": True})
        self.manager.set_decision(rejected["candidate_id"], "reject")
        result = self.manager.create_listing_draft(rejected["candidate_id"])
        self.assertIn("error", result)
        self.assertIn("approve", result["error"])

    def test_nonexistent_candidate_returns_none(self):
        result = self.manager.create_listing_draft("rc_nonexistent")
        self.assertIsNone(result)


class TestListingDraftDescription(unittest.TestCase):
    """Verify the safe description includes required sections, excludes sensitive data."""

    def setUp(self):
        self.manager = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        self.draft = self.manager.create_listing_draft(self.candidate["candidate_id"])

    def test_description_contains_specifications(self):
        self.assertIn("color: black", self.draft["description"])
        self.assertIn("type: TWS", self.draft["description"])

    def test_description_contains_shipping_info(self):
        self.assertIn("发货", self.draft["description"])
        self.assertIn("48小时", self.draft["description"])

    def test_description_contains_after_sale_boundary(self):
        self.assertIn("售后", self.draft["description"])
        self.assertIn("不退换", self.draft["description"])

    def test_description_does_not_contain_supplier_url(self):
        self.assertNotIn("pdd.example.com", self.draft["description"])
        self.assertNotIn("source_url", self.draft["description"])

    def test_description_does_not_contain_profit_info(self):
        self.assertNotIn("gross_profit", self.draft["description"])
        self.assertNotIn("gross_margin", self.draft["description"])
        self.assertNotIn("毛利", self.draft["description"])
        self.assertNotIn("supplier_price", self.draft["description"])

    def test_description_does_not_contain_buyer_address(self):
        self.assertNotIn("buyer_address", self.draft["description"])


class TestListingDraftAPI(unittest.TestCase):
    """Test POST /resale/candidates/{id}/listing-draft endpoint."""

    def test_success_creates_draft_and_work_order(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        # Create, verify, approve
        resp = client.post("/resale/candidates", json={
            "title": "API测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        cid = resp.json()["candidate"]["candidate_id"]
        client.post(f"/resale/candidates/{cid}/verify", json={"supplier_verified": True})
        client.post(f"/resale/candidates/{cid}/decision", json={"decision": "approve"})
        # Create listing draft
        resp = client.post(f"/resale/candidates/{cid}/listing-draft")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("listing_draft", data)
        self.assertIn("work_order", data)
        self.assertTrue(data["approval_required"])

    def test_400_on_blocked_candidate(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        srv.resale_manager = ResaleManager(Path(tmpdir))
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates", json={
            "title": "食品", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0, "category": "食品",
        })
        cid = resp.json()["candidate"]["candidate_id"]
        resp = client.post(f"/resale/candidates/{cid}/listing-draft")
        self.assertEqual(resp.status_code, 400)

    def test_404_on_nonexistent(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        srv.resale_manager = ResaleManager(Path(tmpdir))
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates/rc_nonexistent/listing-draft")
        self.assertEqual(resp.status_code, 404)


class TestListingDraftApproval(unittest.TestCase):
    """Test that listing draft creates proper WorkOrder + approval."""

    def test_work_order_kind_is_resale_listing(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates", json={
            "title": "审批测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        cid = resp.json()["candidate"]["candidate_id"]
        client.post(f"/resale/candidates/{cid}/verify", json={"supplier_verified": True})
        client.post(f"/resale/candidates/{cid}/decision", json={"decision": "approve"})
        resp = client.post(f"/resale/candidates/{cid}/listing-draft")
        data = resp.json()
        wo = data["work_order"]
        self.assertEqual(wo["kind"], "resale_listing")

    def test_approval_action_is_publish_listing(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates", json={
            "title": "审批测试2", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        cid = resp.json()["candidate"]["candidate_id"]
        client.post(f"/resale/candidates/{cid}/verify", json={"supplier_verified": True})
        client.post(f"/resale/candidates/{cid}/decision", json={"decision": "approve"})
        resp = client.post(f"/resale/candidates/{cid}/listing-draft")
        data = resp.json()
        wo = data["work_order"]
        # Check approval exists with correct action
        approvals = wo.get("approvals", [])
        self.assertTrue(len(approvals) > 0)
        self.assertEqual(approvals[0]["action"], "publish_listing")
        self.assertEqual(approvals[0]["status"], "pending")

    def test_approval_snapshot_has_required_fields(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        resp = client.post("/resale/candidates", json={
            "title": "快照测试", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        cid = resp.json()["candidate"]["candidate_id"]
        client.post(f"/resale/candidates/{cid}/verify", json={"supplier_verified": True})
        client.post(f"/resale/candidates/{cid}/decision", json={"decision": "approve"})
        resp = client.post(f"/resale/candidates/{cid}/listing-draft")
        data = resp.json()
        wo = data["work_order"]
        snapshot = wo["approvals"][0]["draft_snapshot"]
        self.assertIn("title", snapshot)
        self.assertIn("price", snapshot)
        self.assertIn("stock", snapshot)
        self.assertIn("description", snapshot)
        self.assertIn("delivery_type", snapshot)
        self.assertIn("category", snapshot)
        self.assertIn("candidate_id", snapshot)
        self.assertIn("gross_margin", snapshot)
        self.assertIn("supplier_verified", snapshot)


class TestMilestone4UIStrings(unittest.TestCase):
    """Verify JS source contains Milestone 4 function names and strings."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_createListingDraft_exists(self):
        self.assertIn("createListingDraft", self.js)

    def test_resale_listing_draft_in_summarize(self):
        self.assertIn('"resale_listing_draft"', self.js)

    def test_listing_button_text(self):
        self.assertIn("生成上架草稿", self.js)

    def test_missing_reason_text(self):
        self.assertIn("上架条件不足", self.js)


# ============================================================
# Behavioral tests via Node.js
# ============================================================

_JS_LISTING_HARNESS = r"""
const fs = require("fs");
function escapeHtml(v){return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
function decisionLabel(d){return{watch:"观察中",approve:"已通过",reject:"已拒绝"}[d]||d}
let _h="";function $(s){return{set innerHTML(v){_h=v},get innerHTML(){return _h}}}
function renderResaleFeed(candidates){
  const feed=$("#resale-feed");
  if(!candidates.length){feed.innerHTML="<div>empty</div>";return}
  feed.innerHTML=candidates.map(c=>{
    const isBlocked=c.status==="blocked";
    const canListing=!isBlocked&&c.supplier_verified&&c.manual_decision==="approve";
    const missingReasons=[];
    if(isBlocked)missingReasons.push("已阻断");
    if(!c.supplier_verified)missingReasons.push("未验证供应商");
    if(c.manual_decision!=="approve")missingReasons.push(c.manual_decision?"决策: "+decisionLabel(c.manual_decision):"未决策");
    const actions=isBlocked
      ?'<div class="resale-actions"><span>已阻断</span></div>'
      :'<div class="resale-actions">'
        +'<button onclick="createResalePlan(\''+c.candidate_id+'\')">创建倒卖计划</button>'
        +(canListing
          ?'<button onclick="createListingDraft(\''+c.candidate_id+'\')">生成上架草稿</button>'
          :'<span class="resale-missing">上架条件不足: '+escapeHtml(missingReasons.join(", "))+'</span>')
        +'</div>';
    return '<div class="resale-candidate">'+actions+'</div>';
  }).join("");
}
const data=JSON.parse(fs.readFileSync(process.argv[2],"utf-8"));
renderResaleFeed(data);
fs.writeFileSync(process.argv[3],_h,"utf-8");
"""


class TestMilestone4Behavioral(unittest.TestCase):
    """Behavioral tests: verify listing button appears only for ready candidates."""

    READY_CANDIDATE = {
        "candidate_id": "rc_ready001",
        "title": "Ready Product",
        "status": "approved",
        "supplier_verified": True,
        "manual_decision": "approve",
        "source_platform": "pdd",
        "sales_platform": "xianyu",
        "market_scope": "domestic",
        "supplier_price": 10.0,
        "supplier_shipping": 2.0,
        "target_sale_price": 30.0,
        "economics": {"gross_profit": 15.0, "gross_margin": 0.5},
        "risk_level": "low",
        "risk_flags": [],
    }

    UNVERIFIED_CANDIDATE = {
        "candidate_id": "rc_unverified001",
        "title": "Unverified Product",
        "status": "evaluating",
        "supplier_verified": False,
        "manual_decision": "approve",
        "source_platform": "pdd",
        "sales_platform": "xianyu",
        "market_scope": "domestic",
        "supplier_price": 10.0,
        "supplier_shipping": 2.0,
        "target_sale_price": 30.0,
        "economics": {"gross_profit": 15.0, "gross_margin": 0.5},
        "risk_level": "low",
        "risk_flags": [],
    }

    WATCH_CANDIDATE = {
        "candidate_id": "rc_watch001",
        "title": "Watch Product",
        "status": "evaluating",
        "supplier_verified": True,
        "manual_decision": "watch",
        "source_platform": "pdd",
        "sales_platform": "xianyu",
        "market_scope": "domestic",
        "supplier_price": 10.0,
        "supplier_shipping": 2.0,
        "target_sale_price": 30.0,
        "economics": {"gross_profit": 15.0, "gross_margin": 0.5},
        "risk_level": "low",
        "risk_flags": [],
    }

    def _run(self, candidates):
        js_path = os.path.join(tempfile.gettempdir(), "listing_test.js")
        in_path = os.path.join(tempfile.gettempdir(), "listing_test_in.json")
        out_path = os.path.join(tempfile.gettempdir(), "listing_test_out.html")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_LISTING_HARNESS)
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False)
        subprocess.run(["node", js_path, in_path, out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_ready_candidate_shows_listing_button(self):
        html = self._run([self.READY_CANDIDATE])
        self.assertIn("生成上架草稿", html)
        self.assertIn("createListingDraft", html)
        self.assertNotIn("上架条件不足", html)

    def test_unverified_candidate_shows_missing_reason(self):
        html = self._run([self.UNVERIFIED_CANDIDATE])
        self.assertNotIn("生成上架草稿", html)
        self.assertNotIn("createListingDraft", html)
        self.assertIn("上架条件不足", html)
        self.assertIn("未验证供应商", html)

    def test_watch_candidate_shows_missing_reason(self):
        html = self._run([self.WATCH_CANDIDATE])
        self.assertNotIn("生成上架草稿", html)
        self.assertIn("上架条件不足", html)
        self.assertIn("观察中", html)

    def test_blocked_candidate_shows_blocked_reason(self):
        blocked = dict(self.READY_CANDIDATE, candidate_id="rc_blocked001", status="blocked", supplier_verified=False, manual_decision=None)
        html = self._run([blocked])
        self.assertNotIn("生成上架草稿", html)
        self.assertIn("已阻断", html)


class TestMilestone4RoutesExist(unittest.TestCase):
    """Test that the new listing-draft route is registered."""

    def test_route_exists(self):
        from xianyu_agent.server import app
        route_paths = set()
        for r in app.routes:
            if hasattr(r, "path"):
                route_paths.add(r.path)
        self.assertIn("/resale/candidates/{candidate_id}/listing-draft", route_paths)


if __name__ == "__main__":
    unittest.main()
