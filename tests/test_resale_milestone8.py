"""Tests for Milestone 8 — create-order from task + cross-object chain detail."""
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
        "specifications": {"color": "black"},
    })
    manager.verify_candidate(c["candidate_id"], {"supplier_verified": True})
    manager.set_decision(c["candidate_id"], "approve")
    return manager.get_candidate(c["candidate_id"])


def _make_ordered_task(manager, candidate):
    plan = manager.create_plan(candidate["candidate_id"])
    draft = manager.create_purchase_draft(plan["plan_id"], quantity=2)
    task = manager.create_purchase_task(draft)
    manager.update_purchase_task(task["purchase_task_id"], {
        "status": "ordered",
        "supplier_order_id": "SO_EXIST",
        "tracking_number": "SF_EXIST",
    })
    return manager.get_purchase_task(task["purchase_task_id"])


# ============================================================
# Create order from task
# ============================================================

class TestCreateOrderFromOrderedTask(unittest.TestCase):
    """Ordered task → default status=purchased."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        self.task = _make_ordered_task(self.manager, self.candidate)

    def test_ordered_task_creates_order_with_purchased_status(self):
        order = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )
        self.assertNotIn("error", order)
        self.assertEqual(order["status"], "purchased")

    def test_auto_fills_from_task(self):
        order = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )
        self.assertEqual(order["purchase_task_id"], self.task["purchase_task_id"])
        self.assertEqual(order["purchase_draft_id"], self.task["purchase_draft_id"])
        self.assertEqual(order["plan_id"], self.task["plan_id"])
        self.assertEqual(order["candidate_id"], self.task["candidate_id"])
        self.assertEqual(order["supplier_order_id"], "SO_EXIST")
        self.assertEqual(order["supplier_tracking_number"], "SF_EXIST")
        self.assertEqual(order["supplier_cost"], self.task["payable_amount"])

    def test_auto_fills_listing_draft_id(self):
        listing = self.manager.create_listing_draft(self.candidate["candidate_id"])
        order = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )
        self.assertEqual(order["listing_draft_id"], listing["draft_id"])


class TestCreateOrderFromPendingTask(unittest.TestCase):
    """Pending task → default status=pending_purchase."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        plan = self.manager.create_plan(self.candidate["candidate_id"])
        draft = self.manager.create_purchase_draft(plan["plan_id"])
        self.task = self.manager.create_purchase_task(draft)

    def test_pending_task_creates_order_with_pending_status(self):
        order = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )
        self.assertEqual(order["status"], "pending_purchase")


class TestCreateOrderProfitCalc(unittest.TestCase):
    """Profit calculation from auto-filled supplier_cost."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        self.task = _make_ordered_task(self.manager, self.candidate)

    def test_supplier_cost_from_payable_amount(self):
        order = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 100.0, "platform_fee": 8.0, "shipping_cost": 5.0},
        )
        # supplier_cost = payable_amount from task
        expected_profit = 100.0 - order["supplier_cost"] - 5.0 - 8.0
        self.assertAlmostEqual(order["actual_profit"], round(expected_profit, 2), places=2)

    def test_actual_margin_computed(self):
        order = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 100.0, "platform_fee": 8.0},
        )
        if order["sale_amount"] > 0:
            expected = round(order["actual_profit"] / order["sale_amount"], 4)
            self.assertAlmostEqual(order["actual_margin"], expected, places=3)


class TestDuplicateOrderPrevention(unittest.TestCase):
    """Duplicate active order blocked by default."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        self.task = _make_ordered_task(self.manager, self.candidate)

    def test_duplicate_blocked_by_default(self):
        self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )
        result = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 60.0, "platform_fee": 5.0},
        )
        self.assertIn("error", result)
        self.assertIn("already exists", result["error"])

    def test_allow_duplicate_permits_second_order(self):
        self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )
        result = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 60.0, "platform_fee": 5.0},
            allow_duplicate=True,
        )
        self.assertNotIn("error", result)
        self.assertEqual(result["sale_amount"], 60.0)

    def test_cancelled_order_allows_new(self):
        o1 = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )
        self.manager.update_resale_order(o1["resale_order_id"], {"status": "cancelled"})
        result = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 60.0, "platform_fee": 5.0},
        )
        self.assertNotIn("error", result)

    def test_nonexistent_task_returns_none(self):
        result = self.manager.create_order_from_task("pt_nonexistent", overrides={"sale_amount": 10})
        self.assertIsNone(result)


# ============================================================
# Order detail (chain aggregation)
# ============================================================

class TestOrderDetail(unittest.TestCase):
    """get_order_detail returns full chain."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.candidate = _make_ready_candidate(self.manager)
        self.plan = self.manager.create_plan(self.candidate["candidate_id"])
        self.draft = self.manager.create_purchase_draft(self.plan["plan_id"], quantity=2)
        self.task = self.manager.create_purchase_task(self.draft)
        self.listing = self.manager.create_listing_draft(self.candidate["candidate_id"])
        self.manager.update_purchase_task(self.task["purchase_task_id"], {
            "status": "ordered",
            "supplier_order_id": "SO_1",
            "tracking_number": "SF_1",
        })
        self.task = self.manager.get_purchase_task(self.task["purchase_task_id"])
        self.order = self.manager.create_order_from_task(
            self.task["purchase_task_id"],
            overrides={"sale_amount": 59.0, "platform_fee": 4.72},
        )

    def test_detail_has_all_objects(self):
        detail = self.manager.get_order_detail(self.order["resale_order_id"])
        self.assertIsNotNone(detail["order"])
        self.assertIsNotNone(detail["purchase_task"])
        self.assertIsNotNone(detail["purchase_draft"])
        self.assertIsNotNone(detail["plan"])
        self.assertIsNotNone(detail["candidate"])
        self.assertIsNotNone(detail["listing_draft"])

    def test_detail_order_matches(self):
        detail = self.manager.get_order_detail(self.order["resale_order_id"])
        self.assertEqual(detail["order"]["resale_order_id"], self.order["resale_order_id"])

    def test_detail_candidate_title(self):
        detail = self.manager.get_order_detail(self.order["resale_order_id"])
        self.assertEqual(detail["candidate"]["title"], "蓝牙耳机 TWS")

    def test_detail_listing_draft_id(self):
        detail = self.manager.get_order_detail(self.order["resale_order_id"])
        self.assertEqual(detail["listing_draft"]["draft_id"], self.listing["draft_id"])


class TestOrderDetailMissingObjects(unittest.TestCase):
    """Missing objects return None, not 500."""

    def test_no_task_returns_null(self):
        manager, _ = _make_manager()
        order = manager.create_resale_order({
            "xianyu_order_id": "xy_min",
            "sale_amount": 10.0,
            "supplier_cost": 5.0,
        })
        detail = manager.get_order_detail(order["resale_order_id"])
        self.assertIsNone(detail["purchase_task"])
        self.assertIsNone(detail["candidate"])
        self.assertIsNone(detail["plan"])
        self.assertIsNone(detail["purchase_draft"])
        self.assertIsNone(detail["listing_draft"])

    def test_nonexistent_order_returns_none(self):
        manager, _ = _make_manager()
        self.assertIsNone(manager.get_order_detail("ro_nonexistent"))


# ============================================================
# API
# ============================================================

class TestCreateOrderFromTaskAPI(unittest.TestCase):
    """Test POST /resale/purchase-tasks/{id}/create-order."""

    def _setup(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        return mgr, client

    def test_create_order_success(self):
        mgr, client = self._setup()
        c = _make_ready_candidate(mgr)
        task = _make_ordered_task(mgr, c)
        resp = client.post(f"/resale/purchase-tasks/{task['purchase_task_id']}/create-order", json={
            "sale_amount": 59.0, "platform_fee": 4.72, "xianyu_order_id": "xy_api",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["resale_order"]["status"], "purchased")

    def test_create_order_404(self):
        _, client = self._setup()
        resp = client.post("/resale/purchase-tasks/pt_nonexistent/create-order", json={"sale_amount": 10})
        self.assertEqual(resp.status_code, 404)

    def test_create_order_409_on_duplicate(self):
        mgr, client = self._setup()
        c = _make_ready_candidate(mgr)
        task = _make_ordered_task(mgr, c)
        client.post(f"/resale/purchase-tasks/{task['purchase_task_id']}/create-order", json={"sale_amount": 59})
        resp = client.post(f"/resale/purchase-tasks/{task['purchase_task_id']}/create-order", json={"sale_amount": 60})
        self.assertEqual(resp.status_code, 409)


class TestOrderDetailAPI(unittest.TestCase):
    """Test GET /resale/orders/{id}/detail."""

    def _setup(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        return mgr, client

    def test_detail_endpoint(self):
        mgr, client = self._setup()
        c = _make_ready_candidate(mgr)
        task = _make_ordered_task(mgr, c)
        order = mgr.create_order_from_task(task["purchase_task_id"], overrides={"sale_amount": 59, "platform_fee": 4})
        resp = client.get(f"/resale/orders/{order['resale_order_id']}/detail")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("order", data)
        self.assertIn("candidate", data)
        self.assertEqual(data["candidate"]["title"], "蓝牙耳机 TWS")

    def test_detail_404(self):
        _, client = self._setup()
        resp = client.get("/resale/orders/ro_nonexistent/detail")
        self.assertEqual(resp.status_code, 404)


# ============================================================
# UI strings
# ============================================================

class TestMilestone8UIStrings(unittest.TestCase):
    """Verify JS contains Milestone 8 strings."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_create_order_from_task_function(self):
        self.assertIn("createOrderFromTask", self.js)

    def test_view_order_detail_function(self):
        self.assertIn("viewOrderDetail", self.js)

    def test_create_order_button_text(self):
        self.assertIn("创建订单对账", self.js)

    def test_view_chain_button_text(self):
        self.assertIn("查看链路详情", self.js)

    def test_no_auto_sync_text(self):
        self.assertNotIn("自动同步", self.js)


# ============================================================
# Behavioral
# ============================================================

_JS_M8_HARNESS = r"""
const fs = require("fs");
function escapeHtml(v){return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
function orderStatusLabel(s){return{pending_purchase:"待采购",purchased:"已采购",shipped:"已发货",completed:"已完成",cancelled:"已取消",refund_risk:"退款风险",refunded:"已退款"}[s]||s}
function taskStatusLabel(s){return{pending_manual_order:"待人工下单",ordered:"已下单",cancelled:"已取消",failed:"失败"}[s]||s}
let _h="";function $(s){return{set innerHTML(v){_h=v},get innerHTML(){return _h}}}

function renderPurchaseTasks(tasks){
  const feed=$("#purchase-task-feed");
  if(!feed)return;
  if(!tasks.length){feed.innerHTML='<div>暂无采购任务</div>';return}
  feed.innerHTML=tasks.map(t=>{
    const isPending=t.status==="pending_manual_order";
    const isOrdered=t.status==="ordered";
    const actions=isPending
      ?'<div class="purchase-task-actions">'
        +'<button onclick="markPurchaseTaskOrdered(\''+t.purchase_task_id+'\')">标记已下单</button>'
        +'<button onclick="createOrderFromTask(\''+t.purchase_task_id+'\')">创建订单对账</button>'
        +'<button onclick="cancelPurchaseTask(\''+t.purchase_task_id+'\')">取消任务</button>'
        +'</div>'
      :isOrdered
      ?'<div class="purchase-task-actions">'
        +'<button onclick="createOrderFromTask(\''+t.purchase_task_id+'\')">创建订单对账</button>'
        +'</div>'
      :"";
    return '<div class="purchase-task-item">'+actions+'</div>';
  }).join("");
}

function renderOrders(orders){
  const feed=$("#order-feed");
  if(!feed)return;
  if(!orders.length){feed.innerHTML='<div>暂无订单</div>';return}
  feed.innerHTML=orders.map(o=>{
    return '<div class="order-item">'
      +'<div class="order-actions">'
      +'<button onclick="viewOrderDetail(\''+o.resale_order_id+'\')">查看链路详情</button>'
      +'</div>'
      +'</div>';
  }).join("");
}

const args=process.argv.slice(2);
const mode=args[0];
const data=JSON.parse(fs.readFileSync(args[1],"utf-8"));
if(mode==="tasks")renderPurchaseTasks(data);
else if(mode==="orders")renderOrders(data);
fs.writeFileSync(args[2],_h,"utf-8");
"""


class TestMilestone8Behavioral(unittest.TestCase):
    """Behavioral tests for Milestone 8 UI."""

    def _run(self, mode, data):
        js_path = os.path.join(tempfile.gettempdir(), "m8_test.js")
        in_path = os.path.join(tempfile.gettempdir(), "m8_in.json")
        out_path = os.path.join(tempfile.gettempdir(), "m8_out.html")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_M8_HARNESS)
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        subprocess.run(["node", js_path, mode, in_path, out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_pending_task_shows_create_order_button(self):
        html = self._run("tasks", [{"purchase_task_id": "pt_1", "status": "pending_manual_order"}])
        self.assertIn("创建订单对账", html)
        self.assertIn("createOrderFromTask", html)

    def test_ordered_task_shows_create_order_button(self):
        html = self._run("tasks", [{"purchase_task_id": "pt_2", "status": "ordered"}])
        self.assertIn("创建订单对账", html)

    def test_cancelled_task_no_create_order_button(self):
        html = self._run("tasks", [{"purchase_task_id": "pt_3", "status": "cancelled"}])
        self.assertNotIn("创建订单对账", html)

    def test_order_shows_view_detail_button(self):
        html = self._run("orders", [{"resale_order_id": "ro_1", "status": "purchased"}])
        self.assertIn("查看链路详情", html)
        self.assertIn("viewOrderDetail", html)

    def test_no_auto_sync_text(self):
        html = self._run("tasks", [{"purchase_task_id": "pt_1", "status": "pending_manual_order"}])
        self.assertNotIn("自动同步", html)


# ============================================================
# Routes exist
# ============================================================

class TestMilestone8RoutesExist(unittest.TestCase):
    """Verify Milestone 8 routes are registered."""

    def test_create_order_from_task_route(self):
        from xianyu_agent.server import app
        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        self.assertIn("/resale/purchase-tasks/{purchase_task_id}/create-order", route_paths)

    def test_order_detail_route(self):
        from xianyu_agent.server import app
        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        self.assertIn("/resale/orders/{resale_order_id}/detail", route_paths)


if __name__ == "__main__":
    unittest.main()
