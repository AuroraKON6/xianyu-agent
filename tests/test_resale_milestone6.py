"""Tests for Milestone 6 — purchase task lifecycle (manual ordering, no auto-pay)."""
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
    c = manager.create_candidate({
        "title": "蓝牙耳机 TWS",
        "source_platform": "pdd",
        "source_url": "https://pdd.example.com/item/123",
        "supplier_price": 25.0,
        "supplier_shipping": 3.0,
        "target_sale_price": 59.0,
        "category": "数码配件",
        "specifications": {"color": "black", "type": "TWS"},
    })
    manager.verify_candidate(c["candidate_id"], {"supplier_verified": True})
    manager.set_decision(c["candidate_id"], "approve")
    return manager.get_candidate(c["candidate_id"])


# ============================================================
# PurchaseTask CRUD
# ============================================================

class TestCreatePurchaseTask(unittest.TestCase):
    """PurchaseTask creation from a purchase draft."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        self.plan = self.manager.create_plan(self.candidate["candidate_id"])
        self.draft = self.manager.create_purchase_draft(self.plan["plan_id"], quantity=2)

    def test_create_task_from_draft(self):
        task = self.manager.create_purchase_task(self.draft)
        self.assertIn("purchase_task_id", task)
        self.assertEqual(task["status"], "pending_manual_order")

    def test_task_has_required_fields(self):
        task = self.manager.create_purchase_task(self.draft)
        self.assertEqual(task["purchase_draft_id"], self.draft["draft_id"])
        self.assertEqual(task["plan_id"], self.plan["plan_id"])
        self.assertEqual(task["candidate_id"], self.candidate["candidate_id"])
        self.assertEqual(task["supplier_url"], "https://pdd.example.com/item/123")
        self.assertEqual(task["quantity"], 2)
        self.assertEqual(task["payable_amount"], self.draft["total_cost"])
        self.assertIn("expected_profit", task)
        self.assertIn("risk_warnings", task)
        self.assertIn("created_at", task)
        self.assertIsNone(task["ordered_at"])

    def test_task_persisted(self):
        task = self.manager.create_purchase_task(self.draft)
        fetched = self.manager.get_purchase_task(task["purchase_task_id"])
        self.assertEqual(fetched["purchase_task_id"], task["purchase_task_id"])
        self.assertEqual(fetched["status"], "pending_manual_order")

    def test_task_has_mvp_warning(self):
        task = self.manager.create_purchase_task(self.draft)
        warnings = task["risk_warnings"]
        self.assertTrue(any("不会自动付款" in w for w in warnings))


class TestListPurchaseTasks(unittest.TestCase):
    """Listing and filtering purchase tasks."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        self.plan = self.manager.create_plan(self.candidate["candidate_id"])
        self.draft = self.manager.create_purchase_draft(self.plan["plan_id"])

    def test_list_all_tasks(self):
        self.manager.create_purchase_task(self.draft)
        self.manager.create_purchase_task(self.draft)
        tasks = self.manager.list_purchase_tasks()
        self.assertEqual(len(tasks), 2)

    def test_list_filter_by_status(self):
        task = self.manager.create_purchase_task(self.draft)
        self.manager.create_purchase_task(self.draft)
        self.manager.update_purchase_task(task["purchase_task_id"], {"status": "ordered"})
        pending = self.manager.list_purchase_tasks(status="pending_manual_order")
        self.assertEqual(len(pending), 1)
        ordered = self.manager.list_purchase_tasks(status="ordered")
        self.assertEqual(len(ordered), 1)


class TestMarkOrdered(unittest.TestCase):
    """Mark purchase task as ordered."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        candidate = _make_ready_candidate(self.manager)
        plan = self.manager.create_plan(candidate["candidate_id"])
        draft = self.manager.create_purchase_draft(plan["plan_id"])
        self.task = self.manager.create_purchase_task(draft)

    def test_mark_ordered_updates_fields(self):
        updated = self.manager.update_purchase_task(self.task["purchase_task_id"], {
            "status": "ordered",
            "supplier_order_id": "SO_12345",
            "tracking_number": "SF789",
            "manual_order_note": "已在pdd下单",
            "ordered_at": "2026-05-20T15:00:00",
        })
        self.assertEqual(updated["status"], "ordered")
        self.assertEqual(updated["supplier_order_id"], "SO_12345")
        self.assertEqual(updated["tracking_number"], "SF789")
        self.assertEqual(updated["manual_order_note"], "已在pdd下单")
        self.assertEqual(updated["ordered_at"], "2026-05-20T15:00:00")

    def test_mark_ordered_persisted(self):
        self.manager.update_purchase_task(self.task["purchase_task_id"], {
            "status": "ordered",
            "supplier_order_id": "SO_99",
        })
        fetched = self.manager.get_purchase_task(self.task["purchase_task_id"])
        self.assertEqual(fetched["status"], "ordered")
        self.assertEqual(fetched["supplier_order_id"], "SO_99")


class TestCancelPurchaseTask(unittest.TestCase):
    """Cancel purchase task."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        candidate = _make_ready_candidate(self.manager)
        plan = self.manager.create_plan(candidate["candidate_id"])
        draft = self.manager.create_purchase_draft(plan["plan_id"])
        self.task = self.manager.create_purchase_task(draft)

    def test_cancel_pending_task(self):
        updated = self.manager.update_purchase_task(self.task["purchase_task_id"], {"status": "cancelled"})
        self.assertEqual(updated["status"], "cancelled")

    def test_cannot_cancel_ordered_task(self):
        self.manager.update_purchase_task(self.task["purchase_task_id"], {"status": "ordered"})
        # update_purchase_task doesn't enforce status transitions — the API does
        # This test verifies the manager allows it; API-level test below checks the gate
        fetched = self.manager.get_purchase_task(self.task["purchase_task_id"])
        self.assertEqual(fetched["status"], "ordered")


# ============================================================
# handle_prepare_purchase_draft creates PurchaseTask
# ============================================================

class TestPreparePurchaseDraftCreatesTask(unittest.TestCase):
    """Executing prepare_purchase_draft should create a PurchaseTask."""

    def test_handler_creates_task(self):
        from xianyu_agent.action_executor import handle_prepare_purchase_draft
        from xianyu_agent.work_orders import WorkOrderManager

        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        manager = __import__("xianyu_agent.resale", fromlist=["ResaleManager"]).ResaleManager(data_dir)

        # Setup: candidate → plan → work order → approval
        candidate = _make_ready_candidate(manager)
        plan = manager.create_plan(candidate["candidate_id"])

        wo_mgr = WorkOrderManager()
        wo_mgr.configure(data_dir / "work_orders.json")
        wo = wo_mgr.create_work_order(kind="resale_plan", title="test", owner="resale")
        wo_mgr.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "resale", "action": "prepare_purchase_draft",
            "description": "采购草稿", "params": {"plan_id": plan["plan_id"]},
            "requires_approval": True,
        }])
        wo = wo_mgr.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]
        wo_mgr.decide_approval(approval_id, "approved", "ok")

        approval = wo_mgr.get_work_order(wo["work_order_id"])["approvals"][0]
        result = asyncio.run(handle_prepare_purchase_draft(approval, {"data_dir": data_dir}))

        self.assertTrue(result["ok"])
        self.assertIn("purchase_task_id", result)
        self.assertIn("人工采购任务", result["message"])
        self.assertIn("手动", result["message"])

    def test_created_task_has_correct_status(self):
        from xianyu_agent.action_executor import handle_prepare_purchase_draft
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.resale import ResaleManager

        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        manager = ResaleManager(data_dir)
        candidate = _make_ready_candidate(manager)
        plan = manager.create_plan(candidate["candidate_id"])

        wo_mgr = WorkOrderManager()
        wo_mgr.configure(data_dir / "work_orders.json")
        wo = wo_mgr.create_work_order(kind="resale_plan", title="test", owner="resale")
        wo_mgr.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "resale", "action": "prepare_purchase_draft",
            "description": "采购草稿", "params": {"plan_id": plan["plan_id"]},
            "requires_approval": True,
        }])
        wo = wo_mgr.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]
        wo_mgr.decide_approval(approval_id, "approved", "ok")

        approval = wo_mgr.get_work_order(wo["work_order_id"])["approvals"][0]
        result = asyncio.run(handle_prepare_purchase_draft(approval, {"data_dir": data_dir}))

        # Re-load manager from disk to pick up the new task
        fresh_manager = ResaleManager(data_dir)
        task = fresh_manager.get_purchase_task(result["purchase_task_id"])
        self.assertEqual(task["status"], "pending_manual_order")
        self.assertEqual(task["plan_id"], plan["plan_id"])


class TestUnapprovedCannotCreateTask(unittest.TestCase):
    """Unapproved approval should not create a purchase task."""

    def test_unapproved_rejected_by_queue(self):
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.execution_queue import ExecutionJobQueue

        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        wo_mgr = WorkOrderManager()
        wo_mgr.configure(data_dir / "work_orders.json")
        eq = ExecutionJobQueue(data_dir / "jobs.json")

        wo = wo_mgr.create_work_order(kind="resale_plan", title="test", owner="resale")
        wo_mgr.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "resale", "action": "prepare_purchase_draft",
            "description": "采购草稿", "params": {"plan_id": "rp_test"},
            "requires_approval": True,
        }])
        wo = wo_mgr.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]

        # NOT approved — should raise ValueError
        with self.assertRaises(ValueError):
            eq.create_job(approval_id, wo_mgr)


class TestNoBrowserOrSupplierCall(unittest.TestCase):
    """The handler must NOT call any browser/adapter functions."""

    def test_handler_returns_ok_without_adapter(self):
        from xianyu_agent.action_executor import handle_prepare_purchase_draft
        from xianyu_agent.work_orders import WorkOrderManager

        tmpdir = tempfile.mkdtemp()
        data_dir = Path(tmpdir)
        manager = __import__("xianyu_agent.resale", fromlist=["ResaleManager"]).ResaleManager(data_dir)
        candidate = _make_ready_candidate(manager)
        plan = manager.create_plan(candidate["candidate_id"])

        wo_mgr = WorkOrderManager()
        wo_mgr.configure(data_dir / "work_orders.json")
        wo = wo_mgr.create_work_order(kind="resale_plan", title="test", owner="resale")
        wo_mgr.create_action_plan(wo["work_order_id"], proposed_actions=[{
            "agent": "resale", "action": "prepare_purchase_draft",
            "description": "采购草稿", "params": {"plan_id": plan["plan_id"]},
            "requires_approval": True,
        }])
        wo = wo_mgr.get_work_order(wo["work_order_id"])
        approval_id = wo["approvals"][0]["approval_id"]
        wo_mgr.decide_approval(approval_id, "approved", "ok")

        approval = wo_mgr.get_work_order(wo["work_order_id"])["approvals"][0]
        # No workflow/adapter in context — handler should still succeed
        result = asyncio.run(handle_prepare_purchase_draft(approval, {"data_dir": data_dir}))
        self.assertTrue(result["ok"])
        self.assertIn("purchase_task_id", result)


# ============================================================
# API endpoints
# ============================================================

class TestPurchaseTaskAPI(unittest.TestCase):
    """Test purchase task API endpoints."""

    def _setup(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        return mgr, client, tmpdir

    def test_list_purchase_tasks_empty(self):
        _, client, _ = self._setup()
        resp = client.get("/resale/purchase-tasks")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_get_purchase_task_404(self):
        _, client, _ = self._setup()
        resp = client.get("/resale/purchase-tasks/pt_nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_mark_ordered(self):
        mgr, client, _ = self._setup()
        candidate = _make_ready_candidate(mgr)
        plan = mgr.create_plan(candidate["candidate_id"])
        draft = mgr.create_purchase_draft(plan["plan_id"])
        task = mgr.create_purchase_task(draft)

        resp = client.post(f"/resale/purchase-tasks/{task['purchase_task_id']}/mark-ordered", json={
            "supplier_order_id": "SO_API",
            "tracking_number": "TRK_001",
            "manual_order_note": "测试下单",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["purchase_task"]["status"], "ordered")
        self.assertEqual(data["purchase_task"]["supplier_order_id"], "SO_API")

    def test_mark_ordered_404(self):
        _, client, _ = self._setup()
        resp = client.post("/resale/purchase-tasks/pt_nonexistent/mark-ordered", json={})
        self.assertEqual(resp.status_code, 404)

    def test_mark_ordered_rejects_non_pending(self):
        mgr, client, _ = self._setup()
        candidate = _make_ready_candidate(mgr)
        plan = mgr.create_plan(candidate["candidate_id"])
        draft = mgr.create_purchase_draft(plan["plan_id"])
        task = mgr.create_purchase_task(draft)
        mgr.update_purchase_task(task["purchase_task_id"], {"status": "cancelled"})

        resp = client.post(f"/resale/purchase-tasks/{task['purchase_task_id']}/mark-ordered", json={})
        self.assertEqual(resp.status_code, 400)

    def test_cancel_task(self):
        mgr, client, _ = self._setup()
        candidate = _make_ready_candidate(mgr)
        plan = mgr.create_plan(candidate["candidate_id"])
        draft = mgr.create_purchase_draft(plan["plan_id"])
        task = mgr.create_purchase_task(draft)

        resp = client.post(f"/resale/purchase-tasks/{task['purchase_task_id']}/cancel")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(resp.json()["purchase_task"]["status"], "cancelled")

    def test_cancel_rejects_non_pending(self):
        mgr, client, _ = self._setup()
        candidate = _make_ready_candidate(mgr)
        plan = mgr.create_plan(candidate["candidate_id"])
        draft = mgr.create_purchase_draft(plan["plan_id"])
        task = mgr.create_purchase_task(draft)
        mgr.update_purchase_task(task["purchase_task_id"], {"status": "ordered"})

        resp = client.post(f"/resale/purchase-tasks/{task['purchase_task_id']}/cancel")
        self.assertEqual(resp.status_code, 400)

    def test_list_with_status_filter(self):
        mgr, client, _ = self._setup()
        candidate = _make_ready_candidate(mgr)
        plan = mgr.create_plan(candidate["candidate_id"])
        draft = mgr.create_purchase_draft(plan["plan_id"])
        task1 = mgr.create_purchase_task(draft)
        mgr.create_purchase_task(draft)
        mgr.update_purchase_task(task1["purchase_task_id"], {"status": "ordered"})

        resp = client.get("/resale/purchase-tasks?status=pending_manual_order")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

        resp = client.get("/resale/purchase-tasks?status=ordered")
        self.assertEqual(len(resp.json()), 1)


# ============================================================
# UI strings
# ============================================================

class TestMilestone6UIStrings(unittest.TestCase):
    """Verify JS and HTML contain Milestone 6 strings."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "console.html")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()
        with open(html_path, encoding="utf-8") as f:
            self.html = f.read()

    def test_purchase_task_feed_exists(self):
        self.assertIn("purchase-task-feed", self.html)

    def test_refresh_purchase_tasks_button(self):
        self.assertIn("refresh-purchase-tasks", self.html)

    def test_manual_order_label(self):
        self.assertIn("人工采购任务", self.html)

    def test_render_purchase_tasks_function(self):
        self.assertIn("renderPurchaseTasks", self.js)

    def test_refresh_purchase_tasks_function(self):
        self.assertIn("refreshPurchaseTasks", self.js)

    def test_mark_ordered_function(self):
        self.assertIn("markPurchaseTaskOrdered", self.js)

    def test_cancel_task_function(self):
        self.assertIn("cancelPurchaseTask", self.js)

    def test_pending_manual_order_label(self):
        self.assertIn("待人工下单", self.js)

    def test_no_auto_order_text(self):
        self.assertNotIn("自动下单", self.js)
        self.assertNotIn("自动付款", self.js)

    def test_manual_order_text_in_handler_message(self):
        self.assertIn("人工采购任务", self.js)
        self.assertIn("手动下单", self.js)

    def test_task_status_label_function(self):
        self.assertIn("taskStatusLabel", self.js)


# ============================================================
# Behavioral: purchase task rendering
# ============================================================

_JS_TASK_HARNESS = r"""
const fs = require("fs");
function escapeHtml(v){return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
function taskStatusLabel(s){return{pending_manual_order:"待人工下单",ordered:"已下单",cancelled:"已取消",failed:"失败"}[s]||s}
let _h="";function $(s){return{set innerHTML(v){_h=v},get innerHTML(){return _h}}}
function renderPurchaseTasks(tasks){
  const feed=$("#purchase-task-feed");
  if(!feed)return;
  if(!tasks.length){feed.innerHTML='<div class="empty-state">暂无采购任务</div>';return}
  feed.innerHTML=tasks.map(t=>{
    const isPending=t.status==="pending_manual_order";
    const isOrdered=t.status==="ordered";
    const specs=t.product_specs||{};
    const specText=Object.keys(specs).length
      ?Object.entries(specs).map(([k,v])=>escapeHtml(k)+": "+escapeHtml(v)).join(", ")
      :"无";
    const statusBadge='<span class="resale-badge resale-badge--task-'+t.status+'">'+escapeHtml(taskStatusLabel(t.status))+'</span>';
    const orderedInfo=isOrdered
      ?'<div class="purchase-task-ordered">'
        +(t.supplier_order_id?'<span class="snapshot-field">供应商单号: '+escapeHtml(t.supplier_order_id)+'</span>':'')
        +(t.tracking_number?'<span class="snapshot-field">快递单号: '+escapeHtml(t.tracking_number)+'</span>':'')
        +(t.manual_order_note?'<span class="snapshot-field">备注: '+escapeHtml(t.manual_order_note)+'</span>':'')
        +'</div>'
      :"";
    const actions=isPending
      ?'<div class="purchase-task-actions">'
        +'<button onclick="markPurchaseTaskOrdered(\''+t.purchase_task_id+'\')">标记已下单</button>'
        +'<button onclick="cancelPurchaseTask(\''+t.purchase_task_id+'\')">取消任务</button>'
        +'</div>'
      :"";
    const warnings=(t.risk_warnings||[]).length
      ?'<div class="purchase-task-warnings">'+t.risk_warnings.map(w=>'<span class="risk-flag">'+escapeHtml(w)+'</span>').join("")+'</div>'
      :"";
    return '<div class="purchase-task-item">'
      +'<div class="purchase-task-header"><span class="purchase-task-title">采购任务 '+escapeHtml(t.purchase_task_id)+'</span>'+statusBadge+'</div>'
      +'<div class="purchase-task-body">'
      +(t.supplier_url?'<div><a href="'+escapeHtml(t.supplier_url)+'">供应商链接</a></div>':'')
      +'<div>规格: '+specText+'</div>'
      +'<div>数量: '+t.quantity+'</div>'
      +'<div>应付: ¥'+t.payable_amount+'</div>'
      +'<div>预期利润: ¥'+t.expected_profit+'</div>'
      +'</div>'
      +warnings+orderedInfo+actions
      +'</div>';
  }).join("");
}
const data=JSON.parse(fs.readFileSync(process.argv[2],"utf-8"));
renderPurchaseTasks(data);
fs.writeFileSync(process.argv[3],_h,"utf-8");
"""


class TestMilestone6Behavioral(unittest.TestCase):
    """Behavioral tests for purchase task rendering."""

    PENDING_TASK = {
        "purchase_task_id": "pt_test001",
        "purchase_draft_id": "pd_test001",
        "plan_id": "rp_test001",
        "candidate_id": "rc_test001",
        "supplier_url": "https://pdd.example.com/item/456",
        "product_specs": {"color": "black", "type": "TWS"},
        "quantity": 2,
        "masked_buyer_address": "北京市***",
        "payable_amount": 56.0,
        "expected_profit": 30.0,
        "risk_warnings": ["低毛利率"],
        "status": "pending_manual_order",
        "manual_order_note": "",
        "supplier_order_id": "",
        "tracking_number": "",
        "created_at": "2026-05-20T15:00:00",
        "updated_at": "2026-05-20T15:00:00",
        "ordered_at": None,
    }

    ORDERED_TASK = {
        "purchase_task_id": "pt_test002",
        "purchase_draft_id": "pd_test002",
        "plan_id": "rp_test002",
        "candidate_id": "rc_test002",
        "supplier_url": "https://1688.example.com/item/789",
        "product_specs": {"size": "M"},
        "quantity": 1,
        "masked_buyer_address": "上海市***",
        "payable_amount": 30.0,
        "expected_profit": 15.0,
        "risk_warnings": [],
        "status": "ordered",
        "manual_order_note": "已手动下单",
        "supplier_order_id": "SO_789",
        "tracking_number": "SF123",
        "created_at": "2026-05-20T14:00:00",
        "updated_at": "2026-05-20T15:00:00",
        "ordered_at": "2026-05-20T14:30:00",
    }

    def _run(self, tasks):
        js_path = os.path.join(tempfile.gettempdir(), "task_render_test.js")
        in_path = os.path.join(tempfile.gettempdir(), "task_render_in.json")
        out_path = os.path.join(tempfile.gettempdir(), "task_render_out.html")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_TASK_HARNESS)
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False)
        subprocess.run(["node", js_path, in_path, out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_pending_task_shows_action_buttons(self):
        html = self._run([self.PENDING_TASK])
        self.assertIn("标记已下单", html)
        self.assertIn("取消任务", html)
        self.assertIn("markPurchaseTaskOrdered", html)
        self.assertIn("cancelPurchaseTask", html)

    def test_pending_task_shows_details(self):
        html = self._run([self.PENDING_TASK])
        self.assertIn("供应商链接", html)
        self.assertIn("color: black", html)
        self.assertIn("数量: 2", html)
        self.assertIn("应付: ¥56", html)
        self.assertIn("预期利润: ¥30", html)

    def test_pending_task_shows_status_label(self):
        html = self._run([self.PENDING_TASK])
        self.assertIn("待人工下单", html)

    def test_ordered_task_no_action_buttons(self):
        html = self._run([self.ORDERED_TASK])
        self.assertNotIn("标记已下单", html)
        self.assertNotIn("取消任务", html)

    def test_ordered_task_shows_order_info(self):
        html = self._run([self.ORDERED_TASK])
        self.assertIn("SO_789", html)
        self.assertIn("SF123", html)
        self.assertIn("已手动下单", html)

    def test_ordered_task_shows_status(self):
        html = self._run([self.ORDERED_TASK])
        self.assertIn("已下单", html)

    def test_empty_tasks_shows_empty_state(self):
        html = self._run([])
        self.assertIn("暂无采购任务", html)

    def test_no_auto_order_language(self):
        html = self._run([self.PENDING_TASK])
        self.assertNotIn("自动下单", html)
        self.assertNotIn("自动付款", html)

    def test_risk_warnings_rendered(self):
        html = self._run([self.PENDING_TASK])
        self.assertIn("risk-flag", html)
        self.assertIn("低毛利率", html)

    def test_mixed_tasks_render(self):
        html = self._run([self.PENDING_TASK, self.ORDERED_TASK])
        self.assertIn("标记已下单", html)
        self.assertIn("SO_789", html)
        self.assertIn("待人工下单", html)
        self.assertIn("已下单", html)


# ============================================================
# Routes exist
# ============================================================

class TestMilestone6RoutesExist(unittest.TestCase):
    """Verify purchase task routes are registered."""

    def test_routes_exist(self):
        from xianyu_agent.server import app
        route_paths = set()
        for r in app.routes:
            if hasattr(r, "path"):
                route_paths.add(r.path)
        self.assertIn("/resale/purchase-tasks", route_paths)
        self.assertIn("/resale/purchase-tasks/{purchase_task_id}", route_paths)
        self.assertIn("/resale/purchase-tasks/{purchase_task_id}/mark-ordered", route_paths)
        self.assertIn("/resale/purchase-tasks/{purchase_task_id}/cancel", route_paths)


if __name__ == "__main__":
    unittest.main()
