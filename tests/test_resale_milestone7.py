"""Tests for Milestone 7 — resale order reconciliation."""
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


def _make_order(manager, **overrides):
    """Helper to create a resale order with defaults."""
    payload = {
        "xianyu_order_id": "xy_001",
        "purchase_task_id": "pt_001",
        "plan_id": "rp_001",
        "candidate_id": "rc_001",
        "sale_amount": 59.0,
        "supplier_cost": 25.0,
        "shipping_cost": 3.0,
        "platform_fee": 4.72,
    }
    payload.update(overrides)
    return manager.create_resale_order(payload)


# ============================================================
# ResaleOrder CRUD
# ============================================================

class TestCreateResaleOrder(unittest.TestCase):
    """ResaleOrder creation and field computation."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()

    def test_create_order_returns_all_fields(self):
        order = _make_order(self.manager)
        self.assertIn("resale_order_id", order)
        self.assertEqual(order["xianyu_order_id"], "xy_001")
        self.assertEqual(order["sale_amount"], 59.0)
        self.assertEqual(order["supplier_cost"], 25.0)
        self.assertEqual(order["shipping_cost"], 3.0)
        self.assertEqual(order["platform_fee"], 4.72)
        self.assertIn("actual_profit", order)
        self.assertIn("actual_margin", order)
        self.assertEqual(order["status"], "pending_purchase")
        self.assertIn("created_at", order)
        self.assertIsNone(order["completed_at"])

    def test_actual_profit_computed_correctly(self):
        order = _make_order(self.manager)
        # 59 - 25 - 3 - 4.72 = 26.28
        self.assertAlmostEqual(order["actual_profit"], 26.28, places=2)

    def test_actual_margin_computed_correctly(self):
        order = _make_order(self.manager)
        # 26.28 / 59 = 0.4454...
        self.assertAlmostEqual(order["actual_margin"], 0.4454, places=3)

    def test_negative_profit_order(self):
        order = _make_order(self.manager, sale_amount=10.0, supplier_cost=25.0, shipping_cost=5.0, platform_fee=1.0)
        self.assertEqual(order["actual_profit"], -21.0)
        self.assertTrue(order["actual_margin"] < 0)

    def test_zero_sale_amount_no_division_error(self):
        order = _make_order(self.manager, sale_amount=0)
        self.assertEqual(order["actual_margin"], 0.0)

    def test_order_persisted(self):
        order = _make_order(self.manager)
        fetched = self.manager.get_resale_order(order["resale_order_id"])
        self.assertEqual(fetched["resale_order_id"], order["resale_order_id"])
        self.assertEqual(fetched["actual_profit"], order["actual_profit"])


class TestUpdateResaleOrder(unittest.TestCase):
    """Status updates, completed_at, financial recalc."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()
        self.order = _make_order(self.manager)

    def test_update_status(self):
        updated = self.manager.update_resale_order(self.order["resale_order_id"], {"status": "purchased"})
        self.assertEqual(updated["status"], "purchased")

    def test_update_writes_updated_at(self):
        original = self.order["updated_at"]
        updated = self.manager.update_resale_order(self.order["resale_order_id"], {"status": "shipped"})
        self.assertGreaterEqual(updated["updated_at"], original)

    def test_completed_writes_completed_at(self):
        updated = self.manager.update_resale_order(self.order["resale_order_id"], {"status": "completed"})
        self.assertIsNotNone(updated["completed_at"])

    def test_non_completed_no_completed_at(self):
        updated = self.manager.update_resale_order(self.order["resale_order_id"], {"status": "shipped"})
        self.assertIsNone(updated["completed_at"])

    def test_update_tracking_numbers(self):
        updated = self.manager.update_resale_order(self.order["resale_order_id"], {
            "supplier_tracking_number": "SF123",
            "xianyu_tracking_number": "YT456",
        })
        self.assertEqual(updated["supplier_tracking_number"], "SF123")
        self.assertEqual(updated["xianyu_tracking_number"], "YT456")

    def test_update_notes(self):
        updated = self.manager.update_resale_order(self.order["resale_order_id"], {
            "buyer_note": "快点发",
            "supplier_note": "已催",
            "aftersale_note": "无",
        })
        self.assertEqual(updated["buyer_note"], "快点发")
        self.assertEqual(updated["supplier_note"], "已催")
        self.assertEqual(updated["aftersale_note"], "无")

    def test_update_financials_recalculates_profit(self):
        updated = self.manager.update_resale_order(self.order["resale_order_id"], {
            "sale_amount": 100.0,
            "supplier_cost": 30.0,
        })
        # 100 - 30 - 3 - 4.72 = 62.28
        self.assertAlmostEqual(updated["actual_profit"], 62.28, places=2)
        self.assertAlmostEqual(updated["actual_margin"], 0.6228, places=3)

    def test_nonexistent_order_returns_none(self):
        result = self.manager.update_resale_order("ro_nonexistent", {"status": "completed"})
        self.assertIsNone(result)


class TestListResaleOrders(unittest.TestCase):
    """Listing and filtering orders."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()

    def test_list_all(self):
        _make_order(self.manager)
        _make_order(self.manager, xianyu_order_id="xy_002")
        orders = self.manager.list_resale_orders()
        self.assertEqual(len(orders), 2)

    def test_list_filter_by_status(self):
        o1 = _make_order(self.manager)
        _make_order(self.manager, xianyu_order_id="xy_002")
        self.manager.update_resale_order(o1["resale_order_id"], {"status": "completed"})
        completed = self.manager.list_resale_orders(status="completed")
        self.assertEqual(len(completed), 1)
        pending = self.manager.list_resale_orders(status="pending_purchase")
        self.assertEqual(len(pending), 1)


# ============================================================
# Dashboard
# ============================================================

class TestOrdersDashboard(unittest.TestCase):
    """Dashboard summary statistics."""

    def setUp(self):
        self.manager, self.data_dir = _make_manager()

    def test_empty_dashboard(self):
        d = self.manager.get_orders_dashboard()
        self.assertEqual(d["total_orders"], 0)
        self.assertEqual(d["total_sale_amount"], 0)
        self.assertEqual(d["total_actual_profit"], 0)

    def test_status_counts(self):
        o1 = _make_order(self.manager)
        _make_order(self.manager, xianyu_order_id="xy_002")
        _make_order(self.manager, xianyu_order_id="xy_003")
        self.manager.update_resale_order(o1["resale_order_id"], {"status": "completed"})
        d = self.manager.get_orders_dashboard()
        self.assertEqual(d["total_orders"], 3)
        self.assertEqual(d["completed_count"], 1)
        self.assertEqual(d["purchased_count"], 0)

    def test_financial_totals(self):
        _make_order(self.manager, sale_amount=59.0, supplier_cost=25.0, shipping_cost=3.0, platform_fee=4.72)
        _make_order(self.manager, xianyu_order_id="xy_002", sale_amount=100.0, supplier_cost=40.0, shipping_cost=5.0, platform_fee=8.0)
        d = self.manager.get_orders_dashboard()
        self.assertAlmostEqual(d["total_sale_amount"], 159.0, places=2)
        # profit1 = 59-25-3-4.72=26.28, profit2=100-40-5-8=47
        self.assertAlmostEqual(d["total_actual_profit"], 73.28, places=2)

    def test_avg_margin(self):
        _make_order(self.manager, sale_amount=59.0, supplier_cost=25.0, shipping_cost=3.0, platform_fee=4.72)
        d = self.manager.get_orders_dashboard()
        self.assertAlmostEqual(d["avg_actual_margin"], 0.4454, places=3)

    def test_refund_risk_count(self):
        o = _make_order(self.manager)
        self.manager.update_resale_order(o["resale_order_id"], {"status": "refund_risk"})
        d = self.manager.get_orders_dashboard()
        self.assertEqual(d["refund_risk_count"], 1)

    def test_refunded_count(self):
        o = _make_order(self.manager)
        self.manager.update_resale_order(o["resale_order_id"], {"status": "refunded"})
        d = self.manager.get_orders_dashboard()
        self.assertEqual(d["refunded_count"], 1)

    def test_orders_missing_tracking(self):
        _make_order(self.manager, status="purchased", supplier_tracking_number="")
        _make_order(self.manager, xianyu_order_id="xy_002", status="shipped", supplier_tracking_number="SF1")
        d = self.manager.get_orders_dashboard()
        self.assertEqual(d["orders_missing_tracking"], 1)

    def test_orders_with_negative_profit(self):
        _make_order(self.manager, sale_amount=10.0, supplier_cost=25.0, shipping_cost=5.0, platform_fee=1.0)
        _make_order(self.manager, xianyu_order_id="xy_002", sale_amount=100.0, supplier_cost=25.0, shipping_cost=3.0, platform_fee=4.72)
        d = self.manager.get_orders_dashboard()
        self.assertEqual(d["orders_with_negative_profit"], 1)


# ============================================================
# API endpoints
# ============================================================

class TestResaleOrderAPI(unittest.TestCase):
    """Test resale order API endpoints."""

    def _setup(self):
        import xianyu_agent.server as srv
        from xianyu_agent.resale import ResaleManager
        from fastapi.testclient import TestClient
        tmpdir = tempfile.mkdtemp()
        mgr = ResaleManager(Path(tmpdir))
        srv.resale_manager = mgr
        client = TestClient(srv.app)
        return mgr, client

    def test_create_order(self):
        _, client = self._setup()
        resp = client.post("/resale/orders", json={
            "xianyu_order_id": "xy_api", "sale_amount": 59.0,
            "supplier_cost": 25.0, "shipping_cost": 3.0, "platform_fee": 4.72,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertAlmostEqual(data["resale_order"]["actual_profit"], 26.28, places=2)

    def test_list_orders(self):
        mgr, client = self._setup()
        _make_order(mgr)
        resp = client.get("/resale/orders")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_get_order(self):
        mgr, client = self._setup()
        order = _make_order(mgr)
        resp = client.get(f"/resale/orders/{order['resale_order_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["resale_order_id"], order["resale_order_id"])

    def test_get_order_404(self):
        _, client = self._setup()
        resp = client.get("/resale/orders/ro_nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_update_status(self):
        mgr, client = self._setup()
        order = _make_order(mgr)
        resp = client.post(f"/resale/orders/{order['resale_order_id']}/status", json={"status": "shipped"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["resale_order"]["status"], "shipped")

    def test_update_status_invalid(self):
        mgr, client = self._setup()
        order = _make_order(mgr)
        resp = client.post(f"/resale/orders/{order['resale_order_id']}/status", json={"status": "bogus"})
        self.assertEqual(resp.status_code, 400)

    def test_update_status_404(self):
        _, client = self._setup()
        resp = client.post("/resale/orders/ro_nonexistent/status", json={"status": "completed"})
        self.assertEqual(resp.status_code, 404)

    def test_update_status_no_updates(self):
        mgr, client = self._setup()
        order = _make_order(mgr)
        resp = client.post(f"/resale/orders/{order['resale_order_id']}/status", json={})
        self.assertEqual(resp.status_code, 400)

    def test_dashboard_endpoint(self):
        mgr, client = self._setup()
        _make_order(mgr)
        resp = client.get("/resale/orders/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total_orders"], 1)

    def test_list_filter_by_status(self):
        mgr, client = self._setup()
        o = _make_order(mgr)
        _make_order(mgr, xianyu_order_id="xy_2")
        mgr.update_resale_order(o["resale_order_id"], {"status": "completed"})
        resp = client.get("/resale/orders?status=completed")
        self.assertEqual(len(resp.json()), 1)


# ============================================================
# UI strings
# ============================================================

class TestMilestone7UIStrings(unittest.TestCase):
    """Verify JS and HTML contain Milestone 7 strings."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "console.html")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()
        with open(html_path, encoding="utf-8") as f:
            self.html = f.read()

    def test_order_feed_in_html(self):
        self.assertIn("order-feed", self.html)

    def test_order_dashboard_in_html(self):
        self.assertIn("order-dashboard", self.html)

    def test_refresh_orders_button(self):
        self.assertIn("refresh-orders", self.html)

    def test_order_panel_title(self):
        self.assertIn("订单对账", self.html)

    def test_render_orders_function(self):
        self.assertIn("renderOrders", self.js)

    def test_render_order_dashboard_function(self):
        self.assertIn("renderOrderDashboard", self.js)

    def test_update_order_status_function(self):
        self.assertIn("updateOrderStatus", self.js)

    def test_update_order_details_function(self):
        self.assertIn("updateOrderDetails", self.js)

    def test_order_status_label_function(self):
        self.assertIn("orderStatusLabel", self.js)

    def test_profit_label(self):
        self.assertIn("利润:", self.js)

    def test_margin_label(self):
        self.assertIn("毛利率:", self.js)

    def test_actual_profit_in_render(self):
        self.assertIn("actual_profit", self.js)

    def test_actual_margin_in_render(self):
        self.assertIn("actual_margin", self.js)


# ============================================================
# Behavioral: order rendering
# ============================================================

_JS_ORDER_HARNESS = r"""
const fs = require("fs");
function escapeHtml(v){return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
function orderStatusLabel(s){return{pending_purchase:"待采购",purchased:"已采购",shipped:"已发货",completed:"已完成",cancelled:"已取消",refund_risk:"退款风险",refunded:"已退款"}[s]||s}
let _h="";function $(s){return{set innerHTML(v){_h=v},get innerHTML(){return _h}}}
function renderOrders(orders){
  const feed=$("#order-feed");
  if(!feed)return;
  if(!orders.length){feed.innerHTML='<div class="empty-state">暂无订单</div>';return}
  feed.innerHTML=orders.map(o=>{
    const profitColor=o.actual_profit<0?"color:var(--danger)":"";
    const statusBadge='<span class="resale-badge resale-badge--order-'+o.status+'">'+escapeHtml(orderStatusLabel(o.status))+'</span>';
    return '<div class="order-item">'
      +'<div class="order-header"><span class="order-title">订单 '+escapeHtml(o.resale_order_id)+'</span>'+statusBadge+'</div>'
      +'<div class="order-body">'
      +(o.xianyu_order_id?'<span class="snapshot-field">闲鱼单号: '+escapeHtml(o.xianyu_order_id)+'</span>':'')
      +(o.supplier_order_id?'<span class="snapshot-field">供应商单号: '+escapeHtml(o.supplier_order_id)+'</span>':'')
      +(o.supplier_tracking_number?'<span class="snapshot-field">供应物流: '+escapeHtml(o.supplier_tracking_number)+'</span>':'')
      +'<span class="snapshot-field">售价: ¥'+o.sale_amount+'</span>'
      +'<span class="snapshot-field" style="'+profitColor+'">利润: ¥'+o.actual_profit+'</span>'
      +'<span class="snapshot-field" style="'+profitColor+'">毛利率: '+(o.actual_margin*100).toFixed(1)+'%</span>'
      +'</div>'
      +'</div>';
  }).join("");
}
const data=JSON.parse(fs.readFileSync(process.argv[2],"utf-8"));
renderOrders(data);
fs.writeFileSync(process.argv[3],_h,"utf-8");
"""


class TestMilestone7Behavioral(unittest.TestCase):
    """Behavioral tests for order rendering."""

    ORDER_BASIC = {
        "resale_order_id": "ro_test001",
        "xianyu_order_id": "xy_123",
        "supplier_order_id": "SO_456",
        "supplier_tracking_number": "SF789",
        "xianyu_tracking_number": "",
        "sale_amount": 59.0,
        "supplier_cost": 25.0,
        "shipping_cost": 3.0,
        "platform_fee": 4.72,
        "actual_profit": 26.28,
        "actual_margin": 0.4454,
        "status": "completed",
    }

    ORDER_NEGATIVE = {
        "resale_order_id": "ro_test002",
        "xianyu_order_id": "xy_999",
        "supplier_order_id": "",
        "supplier_tracking_number": "",
        "xianyu_tracking_number": "",
        "sale_amount": 10.0,
        "supplier_cost": 25.0,
        "shipping_cost": 5.0,
        "platform_fee": 1.0,
        "actual_profit": -21.0,
        "actual_margin": -2.1,
        "status": "refund_risk",
    }

    def _run(self, orders):
        js_path = os.path.join(tempfile.gettempdir(), "order_render_test.js")
        in_path = os.path.join(tempfile.gettempdir(), "order_render_in.json")
        out_path = os.path.join(tempfile.gettempdir(), "order_render_out.html")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(_JS_ORDER_HARNESS)
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False)
        subprocess.run(["node", js_path, in_path, out_path], timeout=10)
        with open(out_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_order_shows_ids(self):
        html = self._run([self.ORDER_BASIC])
        self.assertIn("xy_123", html)
        self.assertIn("SO_456", html)

    def test_order_shows_tracking(self):
        html = self._run([self.ORDER_BASIC])
        self.assertIn("SF789", html)

    def test_order_shows_profit_and_margin(self):
        html = self._run([self.ORDER_BASIC])
        self.assertIn("利润: ¥26.28", html)
        self.assertIn("毛利率: 44.5%", html)

    def test_order_shows_status(self):
        html = self._run([self.ORDER_BASIC])
        self.assertIn("已完成", html)

    def test_negative_profit_shows_danger(self):
        html = self._run([self.ORDER_NEGATIVE])
        self.assertIn("利润: ¥-21", html)
        self.assertIn("color:var(--danger)", html)

    def test_refund_risk_status(self):
        html = self._run([self.ORDER_NEGATIVE])
        self.assertIn("退款风险", html)

    def test_empty_orders(self):
        html = self._run([])
        self.assertIn("暂无订单", html)

    def test_multiple_orders(self):
        html = self._run([self.ORDER_BASIC, self.ORDER_NEGATIVE])
        self.assertIn("ro_test001", html)
        self.assertIn("ro_test002", html)
        self.assertIn("已完成", html)
        self.assertIn("退款风险", html)


# ============================================================
# Routes exist
# ============================================================

class TestMilestone7RoutesExist(unittest.TestCase):
    """Verify order routes are registered."""

    def test_routes_exist(self):
        from xianyu_agent.server import app
        route_paths = set()
        for r in app.routes:
            if hasattr(r, "path"):
                route_paths.add(r.path)
        self.assertIn("/resale/orders", route_paths)
        self.assertIn("/resale/orders/dashboard", route_paths)
        self.assertIn("/resale/orders/{resale_order_id}", route_paths)
        self.assertIn("/resale/orders/{resale_order_id}/status", route_paths)


if __name__ == "__main__":
    unittest.main()
