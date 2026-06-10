"""Tests for Milestone 5 — end-to-end resale listing draft → publish_listing execution."""
import asyncio
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


# ============================================================
# Dual-write: data/drafts/{draft_id}.json
# ============================================================

class TestListingDraftDualWrite(unittest.TestCase):
    """Resale listing draft must also be written to data/drafts/{draft_id}.json."""

    def test_draft_file_created_in_drafts_dir(self):
        manager, data_dir = _make_manager()
        candidate = _make_ready_candidate(manager)
        draft = manager.create_listing_draft(candidate["candidate_id"])
        draft_path = data_dir / "drafts" / f"{draft['draft_id']}.json"
        self.assertTrue(draft_path.exists(), f"Expected {draft_path} to exist")

    def test_draft_file_is_valid_json(self):
        manager, data_dir = _make_manager()
        candidate = _make_ready_candidate(manager)
        draft = manager.create_listing_draft(candidate["candidate_id"])
        draft_path = data_dir / "drafts" / f"{draft['draft_id']}.json"
        data = json.loads(draft_path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_draft_file_has_required_fields_for_publish(self):
        manager, data_dir = _make_manager()
        candidate = _make_ready_candidate(manager)
        draft = manager.create_listing_draft(candidate["candidate_id"])
        draft_path = data_dir / "drafts" / f"{draft['draft_id']}.json"
        data = json.loads(draft_path.read_text(encoding="utf-8"))
        # Fields handle_publish_listing reads
        self.assertIn("title", data)
        self.assertIn("category", data)
        self.assertIn("price", data)
        self.assertIn("stock", data)
        self.assertIn("description", data)
        self.assertIn("delivery_type", data)
        self.assertIn("source", data)
        self.assertEqual(data["id"], draft["draft_id"])

    def test_draft_file_title_matches(self):
        manager, data_dir = _make_manager()
        candidate = _make_ready_candidate(manager)
        draft = manager.create_listing_draft(candidate["candidate_id"])
        draft_path = data_dir / "drafts" / f"{draft['draft_id']}.json"
        data = json.loads(draft_path.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "蓝牙耳机 TWS")
        self.assertEqual(data["price"], 59.0)


class TestCandidateHasListingDraftId(unittest.TestCase):
    """After creating a listing draft, candidate should have listing_draft_id."""

    def test_candidate_gets_listing_draft_id(self):
        manager, _ = _make_manager()
        candidate = _make_ready_candidate(manager)
        draft = manager.create_listing_draft(candidate["candidate_id"])
        updated = manager.get_candidate(candidate["candidate_id"])
        self.assertEqual(updated["listing_draft_id"], draft["draft_id"])

    def test_candidate_initially_has_no_listing_draft_id(self):
        manager, _ = _make_manager()
        candidate = _make_ready_candidate(manager)
        self.assertIsNone(candidate.get("listing_draft_id"))


# ============================================================
# Approval → Execution flow
# ============================================================

class TestApprovalExecutionGate(unittest.TestCase):
    """Unapproved approvals cannot create execution jobs; approved can."""

    def setUp(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        self.tmpdir = tempfile.mkdtemp()
        self.wom = WorkOrderManager()
        self.wom.configure(Path(self.tmpdir) / "work_orders.json")
        self.eq = ExecutionJobQueue(Path(self.tmpdir) / "jobs.json")

    def _create_approved_approval(self):
        wo = self.wom.create_work_order(kind="test", title="test", owner="test")
        self.wom.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "test", "action": "publish_listing",
            "description": "test", "params": {"draft_id": "ld_test"},
            "requires_approval": True,
        }])
        wo = self.wom.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]
        self.wom.decide_approval(approval_id, "approved", "ok")
        return approval_id

    def test_unapproved_approval_rejects_job_creation(self):
        wo = self.wom.create_work_order(kind="test", title="test", owner="test")
        self.wom.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "test", "action": "publish_listing",
            "description": "test", "params": {"draft_id": "ld_test"},
            "requires_approval": True,
        }])
        wo = self.wom.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]
        with self.assertRaises(ValueError):
            self.eq.create_job(approval_id, self.wom)

    def test_approved_approval_creates_job(self):
        approval_id = self._create_approved_approval()
        job = self.eq.create_job(approval_id, self.wom)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["action"], "publish_listing")


class TestRunNextWithFakeWorkflow(unittest.TestCase):
    """run_next with a fake workflow/adapter successfully executes publish_listing."""

    def setUp(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.action_executor import ActionExecutor
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmpdir)
        self.wom = WorkOrderManager()
        self.wom.configure(self.data_dir / "work_orders.json")
        self.eq = ExecutionJobQueue(self.data_dir / "jobs.json")
        self.executor = ActionExecutor()

    def _setup_draft_and_approval(self):
        """Create a draft file and an approved approval pointing to it."""
        # Create draft file
        drafts_dir = self.data_dir / "drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        draft_id = "ld_test123"
        draft_data = {
            "id": draft_id,
            "title": "测试商品",
            "category": "数码",
            "price": 49.0,
            "stock": 1,
            "description": "测试描述",
            "delivery_type": "shipping",
            "source": "resale:test",
        }
        (drafts_dir / f"{draft_id}.json").write_text(
            json.dumps(draft_data, ensure_ascii=False), encoding="utf-8"
        )

        # Create work order + approval
        wo = self.wom.create_work_order(kind="resale_listing", title="test", owner="resale")
        self.wom.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "resale", "action": "publish_listing",
            "description": "上架", "params": {"draft_id": draft_id},
            "requires_approval": True,
        }])
        wo = self.wom.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]
        self.wom.decide_approval(approval_id, "approved", "ok")
        return approval_id

    def _make_fake_workflow(self):
        """Create a fake workflow with adapter that returns ok=True."""

        class FakeResult:
            ok = True
            external_id = "ext_123"
            adapter = "fake"
            message = "published"

        class FakeAdapter:
            async def publish_listing(self, draft, cookie_id="default"):
                return FakeResult()

        class FakeStorage:
            def save_publish_result(self, result, draft):
                pass

        class FakeWorkflow:
            adapter = FakeAdapter()
            storage = FakeStorage()

        return FakeWorkflow()

    def test_run_next_completes_publish_listing(self):
        approval_id = self._setup_draft_and_approval()
        self.eq.create_job(approval_id, self.wom)

        workflow = self._make_fake_workflow()
        results = self.eq.run_next(
            self.executor, self.wom,
            {"data_dir": self.data_dir, "workflow": workflow},
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["status"], "completed")

    def test_run_next_job_result_has_publish_info(self):
        approval_id = self._setup_draft_and_approval()
        self.eq.create_job(approval_id, self.wom)

        workflow = self._make_fake_workflow()
        results = self.eq.run_next(
            self.executor, self.wom,
            {"data_dir": self.data_dir, "workflow": workflow},
        )
        job = self.eq.get_job(results[0]["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertTrue(job["result"]["ok"])
        self.assertEqual(job["result"]["draft_id"], "ld_test123")


class TestMissingDraftFails(unittest.TestCase):
    """If the draft file is missing, publish_listing handler returns error."""

    def test_missing_draft_returns_error(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.action_executor import ActionExecutor

        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        wom = WorkOrderManager()
        wom.configure(data_dir / "work_orders.json")
        eq = ExecutionJobQueue(data_dir / "jobs.json")
        executor = ActionExecutor()

        # Create approval pointing to nonexistent draft
        wo = wom.create_work_order(kind="resale_listing", title="test", owner="resale")
        wom.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "resale", "action": "publish_listing",
            "description": "上架", "params": {"draft_id": "ld_nonexistent"},
            "requires_approval": True,
        }])
        wo = wom.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]
        wom.decide_approval(approval_id, "approved", "ok")
        eq.create_job(approval_id, wom)

        # No drafts directory → draft not found
        results = eq.run_next(executor, wom, {"data_dir": data_dir})
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["status"], "failed")

    def test_missing_draft_error_message(self):
        from xianyu_agent.action_executor import ActionExecutor

        tmpdir = tempfile.mkdtemp()
        executor = ActionExecutor()
        approval = {"action": "publish_listing", "payload": {"draft_id": "ld_noexist"}}
        result = asyncio.run(executor.execute_async(approval, {"data_dir": Path(tmpdir)}))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())


# ============================================================
# End-to-end: full pipeline
# ============================================================

class TestEndToEndResalePublish(unittest.TestCase):
    """Full e2e: candidate → listing draft → approval → enqueue → run → ok."""

    def test_full_pipeline(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.server import app
        import xianyu_agent.server as srv
        from fastapi.testclient import TestClient

        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        mgr = __import__("xianyu_agent.resale", fromlist=["ResaleManager"]).ResaleManager(data_dir)
        srv.resale_manager = mgr

        wom = WorkOrderManager()
        wom.configure(data_dir / "work_orders.json")
        srv.work_order_manager = wom

        eq = ExecutionJobQueue(data_dir / "jobs.json")
        srv.execution_queue = eq

        client = TestClient(app)

        # 1. Create candidate
        resp = client.post("/resale/candidates", json={
            "title": "E2E测试商品", "source_platform": "pdd",
            "supplier_price": 10.0, "target_sale_price": 30.0,
        })
        self.assertEqual(resp.status_code, 200)
        cid = resp.json()["candidate"]["candidate_id"]

        # 2. Verify + approve
        client.post(f"/resale/candidates/{cid}/verify", json={"supplier_verified": True})
        client.post(f"/resale/candidates/{cid}/decision", json={"decision": "approve"})

        # 3. Create listing draft
        resp = client.post(f"/resale/candidates/{cid}/listing-draft")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        draft_id = data["listing_draft"]["draft_id"]
        wo_id = data["work_order"]["work_order_id"]

        # 4. Verify draft file exists in data/drafts/
        draft_path = data_dir / "drafts" / f"{draft_id}.json"
        self.assertTrue(draft_path.exists())

        # 5. Get approval_id
        wo = wom.get_work_order(wo_id)
        approval_id = wo["approvals"][0]["approval_id"]
        self.assertEqual(wo["approvals"][0]["status"], "pending")

        # 6. Approve the approval
        resp = client.post(f"/approvals/{approval_id}/decision", json={
            "decision": "approved", "reason": "ok",
        })
        self.assertEqual(resp.status_code, 200)

        # 7. Enqueue execution job
        resp = client.post(f"/approvals/{approval_id}/execute")
        self.assertEqual(resp.status_code, 200)

        # 8. Run with fake workflow
        class FakeResult:
            ok = True
            external_id = "ext_e2e"
            adapter = "fake"
            message = "ok"

        class FakeAdapter:
            async def publish_listing(self, draft, cookie_id="default"):
                return FakeResult()

        class FakeStorage:
            def save_publish_result(self, result, draft):
                pass

        class FakeWorkflow:
            adapter = FakeAdapter()
            storage = FakeStorage()

        # Call run_next directly with context (same as endpoint does internally)
        results = eq.run_next(
            srv.action_executor, wom,
            {"data_dir": data_dir, "workflow": FakeWorkflow()},
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["status"], "completed")


# ============================================================
# UI strings
# ============================================================

class TestMilestone5UIStrings(unittest.TestCase):
    """Verify JS source contains Milestone 5 strings."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_listing_draft_id_in_summarize(self):
        self.assertIn("draft_id", self.js)

    def test_draft_status_html_exists(self):
        self.assertIn("resale-draft-status", self.js)

    def test_listing_draft_id_field_in_render(self):
        self.assertIn("listing_draft_id", self.js)

    def test_draft_badge_text(self):
        self.assertIn("草稿:", self.js)

    def test_no_auto_publish_text(self):
        self.assertIn("需审批后发布", self.js)


# ============================================================
# Behavioral: draft status on card
# ============================================================

_JS_DRAFT_HARNESS = r"""
const fs = require("fs");
function escapeHtml(v){return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
function decisionLabel(d){return{watch:"观察中",approve:"已通过",reject:"已拒绝"}[d]||d}
let _h="";function $(s){return{set innerHTML(v){_h=v},get innerHTML(){return _h}}}
function renderResaleFeed(candidates){
  const feed=$("#resale-feed");
  if(!candidates.length){feed.innerHTML="<div>empty</div>";return}
  feed.innerHTML=candidates.map(c=>{
    const isBlocked=c.status==="blocked";
    const canListing=!isBlocked&&c.supplier_verified&&c.manual_decision==="approve"&&!c.listing_draft_id;
    const missingReasons=[];
    if(isBlocked)missingReasons.push("已阻断");
    if(!c.supplier_verified)missingReasons.push("未验证供应商");
    if(c.manual_decision!=="approve")missingReasons.push(c.manual_decision?"决策: "+decisionLabel(c.manual_decision):"未决策");
    if(c.listing_draft_id)missingReasons.push("已生成草稿");
    const draftStatusHtml=c.listing_draft_id
      ?'<div class="resale-draft-status"><span class="resale-badge resale-badge--draft">草稿: '+escapeHtml(c.listing_draft_id)+'</span><span class="snapshot-field">需审批后发布</span></div>'
      :"";
    const actions=isBlocked
      ?'<div class="resale-actions"><span>已阻断</span></div>'
      :'<div class="resale-actions">'
        +'<button onclick="createResalePlan(\''+c.candidate_id+'\')">创建倒卖计划</button>'
        +(canListing
          ?'<button onclick="createListingDraft(\''+c.candidate_id+'\')">生成上架草稿</button>'
          :'<span class="resale-missing">上架条件不足: '+escapeHtml(missingReasons.join(", "))+'</span>')
        +'</div>';
    return '<div class="resale-candidate">'+draftStatusHtml+actions+'</div>';
  }).join("");
}
const data=JSON.parse(fs.readFileSync(process.argv[2],"utf-8"));
renderResaleFeed(data);
fs.writeFileSync(process.argv[3],_h,"utf-8");
"""


class TestMilestone5Behavioral(unittest.TestCase):
    """Behavioral tests: draft status display on resale cards."""

    CANDIDATE_WITH_DRAFT = {
        "candidate_id": "rc_with_draft",
        "title": "Draft Product",
        "status": "approved",
        "supplier_verified": True,
        "manual_decision": "approve",
        "listing_draft_id": "ld_abc123",
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

    CANDIDATE_WITHOUT_DRAFT = {
        "candidate_id": "rc_no_draft",
        "title": "No Draft Product",
        "status": "approved",
        "supplier_verified": True,
        "manual_decision": "approve",
        "listing_draft_id": None,
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
        js_path = os.path.join(tempfile.gettempdir(), "draft_status_test.js")
        in_path = os.path.join(tempfile.gettempdir(), "draft_status_in.json")
        out_path = os.path.join(tempfile.gettempdir(), "draft_status_out.html")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_DRAFT_HARNESS)
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False)
        subprocess.run(["node", js_path, in_path, out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_candidate_with_draft_shows_draft_id(self):
        html = self._run([self.CANDIDATE_WITH_DRAFT])
        self.assertIn("草稿: ld_abc123", html)
        self.assertIn("需审批后发布", html)

    def test_candidate_with_draft_no_listing_button(self):
        html = self._run([self.CANDIDATE_WITH_DRAFT])
        self.assertNotIn("生成上架草稿", html)
        self.assertNotIn("createListingDraft", html)

    def test_candidate_with_draft_shows_missing_reason(self):
        html = self._run([self.CANDIDATE_WITH_DRAFT])
        self.assertIn("已生成草稿", html)

    def test_candidate_without_draft_shows_listing_button(self):
        html = self._run([self.CANDIDATE_WITHOUT_DRAFT])
        self.assertIn("生成上架草稿", html)
        self.assertIn("createListingDraft", html)

    def test_candidate_without_draft_no_draft_status(self):
        html = self._run([self.CANDIDATE_WITHOUT_DRAFT])
        self.assertNotIn("resale-draft-status", html)

    def test_mixed_candidates_render_independently(self):
        html = self._run([self.CANDIDATE_WITH_DRAFT, self.CANDIDATE_WITHOUT_DRAFT])
        self.assertIn("草稿: ld_abc123", html)
        self.assertIn("生成上架草稿", html)


# ============================================================
# publish_listing handler reads from correct path
# ============================================================

class TestPublishHandlerReadsDraft(unittest.TestCase):
    """Verify handle_publish_listing can read the dual-written draft."""

    def test_handler_reads_draft_from_data_drafts(self):
        from xianyu_agent.action_executor import handle_publish_listing

        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        drafts_dir = data_dir / "drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)

        draft_id = "ld_readable"
        draft_data = {
            "id": draft_id,
            "title": "可读商品",
            "category": "数码",
            "price": 39.0,
            "stock": 1,
            "description": "测试",
            "delivery_type": "shipping",
            "source": "resale:test",
        }
        (drafts_dir / f"{draft_id}.json").write_text(
            json.dumps(draft_data, ensure_ascii=False), encoding="utf-8"
        )

        approval = {"action": "publish_listing", "payload": {"draft_id": draft_id}}

        class FakeResult:
            ok = True
            external_id = "ext_read"
            adapter = "fake"
            message = "ok"

        class FakeAdapter:
            async def publish_listing(self, draft, cookie_id="default"):
                return FakeResult()

        class FakeStorage:
            def save_publish_result(self, result, draft):
                pass

        class FakeWorkflow:
            adapter = FakeAdapter()
            storage = FakeStorage()

        result = asyncio.run(handle_publish_listing(approval, {
            "data_dir": data_dir,
            "workflow": FakeWorkflow(),
        }))
        self.assertTrue(result["ok"])
        self.assertEqual(result["draft_id"], draft_id)


if __name__ == "__main__":
    unittest.main()
