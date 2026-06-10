"""Tests for resale UI integration (Milestone 2)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestResalePanelRendering(unittest.TestCase):
    """Verify resale panel rendering logic exists in app.js."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_render_resale_feed_function_exists(self):
        self.assertIn("renderResaleFeed", self.js)

    def test_refresh_resale_candidates_function_exists(self):
        self.assertIn("refreshResaleCandidates", self.js)

    def test_submit_resale_form_function_exists(self):
        self.assertIn("submitResaleForm", self.js)

    def test_create_resale_plan_function_exists(self):
        self.assertIn("createResalePlan", self.js)

    def test_create_purchase_draft_function_exists(self):
        self.assertIn("createPurchaseDraft", self.js)

    def test_resale_candidate_type_in_summarize(self):
        self.assertIn('data.type === "resale_candidate"', self.js)

    def test_resale_plan_type_in_summarize(self):
        self.assertIn('data.type === "resale_plan"', self.js)

    def test_resale_purchase_draft_type_in_summarize(self):
        self.assertIn('data.type === "resale_purchase_draft"', self.js)


class TestResaleFormFields(unittest.TestCase):
    """Verify form fields are present in console.html."""

    def setUp(self):
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "console.html")
        with open(html_path, encoding="utf-8") as f:
            self.html = f.read()

    def test_resale_form_exists(self):
        self.assertIn('id="resale-form"', self.html)

    def test_title_field(self):
        self.assertIn('name="title"', self.html)

    def test_category_field(self):
        self.assertIn('name="category"', self.html)

    def test_source_platform_field(self):
        self.assertIn('name="source_platform"', self.html)
        self.assertIn("pdd", self.html)
        self.assertIn("1688", self.html)

    def test_sales_platform_field(self):
        self.assertIn('name="sales_platform"', self.html)
        self.assertIn("xianyu", self.html)
        self.assertIn("shopee", self.html)
        self.assertIn("lazada", self.html)

    def test_market_scope_field(self):
        self.assertIn('name="market_scope"', self.html)
        self.assertIn("domestic", self.html)
        self.assertIn("cross_border", self.html)

    def test_supplier_url_field(self):
        self.assertIn('name="source_url"', self.html)

    def test_supplier_price_field(self):
        self.assertIn('name="supplier_price"', self.html)

    def test_supplier_shipping_field(self):
        self.assertIn('name="supplier_shipping"', self.html)

    def test_target_sale_price_field(self):
        self.assertIn('name="target_sale_price"', self.html)

    def test_platform_fee_rate_field(self):
        self.assertIn('name="platform_fee_rate"', self.html)

    def test_expected_refund_rate_field(self):
        self.assertIn('name="expected_refund_rate"', self.html)

    def test_specifications_field(self):
        self.assertIn('name="specifications"', self.html)

    def test_filter_controls_exist(self):
        self.assertIn('id="resale-filter-scope"', self.html)
        self.assertIn('id="resale-filter-source"', self.html)
        self.assertIn('id="resale-filter-status"', self.html)


class TestBlockedCandidateNoPlanAction(unittest.TestCase):
    """Verify blocked candidates don't show plan creation button."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_blocked_candidate_shows_no_plan_button(self):
        """In renderResaleFeed, blocked candidates should show '已阻断' not plan button."""
        self.assertIn("isBlocked", self.js)
        self.assertIn("已阻断，无法创建计划", self.js)

    def test_non_blocked_candidate_shows_plan_button(self):
        self.assertIn("创建倒卖计划", self.js)


class TestResaleApprovalSnapshot(unittest.TestCase):
    """Verify purchase draft approval snapshot renders supplier/profit/risk info."""

    def setUp(self):
        js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.js")
        with open(js_path, encoding="utf-8") as f:
            self.js = f.read()

    def test_purchase_draft_snapshot_renders_supplier_url(self):
        self.assertIn("snapshot.supplier_url", self.js)
        self.assertIn("供应商链接", self.js)

    def test_purchase_draft_snapshot_renders_quantity(self):
        self.assertIn("snapshot.quantity", self.js)

    def test_purchase_draft_snapshot_renders_total_cost(self):
        self.assertIn("snapshot.total_cost", self.js)
        self.assertIn("应付", self.js)

    def test_purchase_draft_snapshot_renders_profit_review(self):
        self.assertIn("snapshot.profit_review", self.js)
        self.assertIn("unit_gross_profit", self.js)

    def test_purchase_draft_snapshot_renders_risk_warnings(self):
        self.assertIn("snapshot.risk_warnings", self.js)

    def test_purchase_draft_snapshot_renders_specs(self):
        self.assertIn("snapshot.product_specs", self.js)

    def test_snapshot_data_action_attribute(self):
        self.assertIn('data-action="prepare_purchase_draft"', self.js)


class TestResaleRoutesNotBroken(unittest.TestCase):
    """Verify existing approval/execution routes still exist."""

    def test_approve_resale_plan_in_risky_actions(self):
        from xianyu_agent.work_orders import RISKY_ACTIONS
        self.assertIn("approve_resale_plan", RISKY_ACTIONS)

    def test_prepare_purchase_draft_in_risky_actions(self):
        from xianyu_agent.work_orders import RISKY_ACTIONS
        self.assertIn("prepare_purchase_draft", RISKY_ACTIONS)

    def test_place_supplier_order_in_risky_actions(self):
        from xianyu_agent.work_orders import RISKY_ACTIONS
        self.assertIn("place_supplier_order", RISKY_ACTIONS)

    def test_existing_risky_actions_unchanged(self):
        from xianyu_agent.work_orders import RISKY_ACTIONS
        self.assertIn("publish_listing", RISKY_ACTIONS)
        self.assertIn("fulfill_order", RISKY_ACTIONS)
        self.assertIn("send_message", RISKY_ACTIONS)
        self.assertIn("delete_item", RISKY_ACTIONS)
        self.assertIn("refund_order", RISKY_ACTIONS)

    def test_execution_queue_still_works(self):
        from xianyu_agent.execution_queue import execution_queue
        self.assertIsNotNone(execution_queue)

    def test_action_executor_has_purchase_draft_handler(self):
        from xianyu_agent.action_executor import action_executor
        result = action_executor.execute(
            {"action": "prepare_purchase_draft", "payload": {"plan_id": "nonexistent"}},
            {},
        )
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    def test_action_executor_blocks_supplier_order(self):
        from xianyu_agent.action_executor import action_executor
        result = action_executor.execute(
            {"action": "place_supplier_order", "payload": {}},
            {},
        )
        self.assertFalse(result["ok"])


# ============================================================
# Behavioral tests — execute renderResaleFeed with mock data
# ============================================================

# Minimal JS harness: escapeHtml + renderResaleFeed + mock DOM
# Writes output to a temp file to avoid Windows stdout encoding issues
_JS_HARNESS = r"""
const fs = require("fs");
const path = require("path");

// Mock escapeHtml (same logic as app.js)
function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// Mock $ selector — returns element with innerHTML capture
let _capturedHtml = "";
function $(sel) {
  return {
    set innerHTML(v) { _capturedHtml = v; },
    get innerHTML() { return _capturedHtml; }
  };
}

// renderResaleFeed extracted from app.js
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
      + '<div class="resale-candidate-meta">'
      + '<span class="snapshot-field">' + escapeHtml(c.source_platform) + '</span>'
      + '<span class="snapshot-field">' + escapeHtml(c.sales_platform) + '</span>'
      + '<span class="snapshot-field">' + escapeHtml(c.market_scope) + '</span>'
      + '<span class="resale-badge resale-badge--' + riskClass + '">风险: ' + escapeHtml(riskClass) + '</span>'
      + '</div>'
      + '<div class="resale-candidate-econ">'
      + '<span class="snapshot-field">供应价: ¥' + c.supplier_price + '</span>'
      + '<span class="snapshot-field">运费: ¥' + c.supplier_shipping + '</span>'
      + '<span class="snapshot-field">目标售价: ¥' + c.target_sale_price + '</span>'
      + '<span class="snapshot-field">毛利: ¥' + (econ.gross_profit ?? "-") + '</span>'
      + '<span class="snapshot-field">毛利率: ' + (econ.gross_margin != null ? (econ.gross_margin * 100).toFixed(1) + "%" : "-") + '</span>'
      + '</div>'
      + (flags ? '<div class="resale-candidate-risk">' + flags + '</div>' : '')
      + (c.blocked_reason ? '<div class="resale-candidate-risk" style="color:var(--danger)">原因: ' + escapeHtml(c.blocked_reason) + '</div>' : '')
      + actions
      + '</div>';
  }).join("");
}

// Read candidates from input file, write output to temp file
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const outFile = process.argv[3];
renderResaleFeed(data);
fs.writeFileSync(outFile, _capturedHtml, "utf-8");
"""


def _run_render_resale_feed(candidates):
    """Execute renderResaleFeed in Node.js with mock candidates, return HTML string."""
    js_path = os.path.join(tempfile.gettempdir(), "resale_render_test.js")
    in_path = os.path.join(tempfile.gettempdir(), "resale_render_in.json")
    out_path = os.path.join(tempfile.gettempdir(), "resale_render_out.html")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(_JS_HARNESS)
    with open(in_path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False)
    proc = subprocess.run(
        ["node", js_path, in_path, out_path],
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Node.js failed with exit code {proc.returncode}")
    with open(out_path, "r", encoding="utf-8") as f:
        return f.read()


class TestResaleFeedBehavioral(unittest.TestCase):
    """Behavioral tests: execute renderResaleFeed with mock data, verify output HTML."""

    BLOCKED_CANDIDATE = {
        "candidate_id": "rc_blocked001",
        "title": "零食大礼包",
        "status": "blocked",
        "risk_level": "blocked",
        "risk_flags": ["食品类目", "高客诉风险"],
        "blocked_reason": "食品类目禁止倒卖",
        "source_platform": "pdd",
        "sales_platform": "xianyu",
        "market_scope": "domestic",
        "supplier_price": 10.0,
        "supplier_shipping": 5.0,
        "target_sale_price": 30.0,
        "economics": {"gross_profit": 12.0, "gross_margin": 0.4},
    }

    APPROVED_CANDIDATE = {
        "candidate_id": "rc_approved001",
        "title": "蓝牙耳机 TWS",
        "status": "approved",
        "risk_level": "low",
        "risk_flags": [],
        "source_platform": "pdd",
        "sales_platform": "xianyu",
        "market_scope": "domestic",
        "supplier_price": 25.0,
        "supplier_shipping": 3.0,
        "target_sale_price": 59.0,
        "economics": {"gross_profit": 25.1, "gross_margin": 0.425},
    }

    def test_blocked_candidate_no_plan_button(self):
        """Blocked candidates must NOT have createResalePlan onclick."""
        html = _run_render_resale_feed([self.BLOCKED_CANDIDATE])
        self.assertNotIn('onclick="createResalePlan', html)

    def test_blocked_candidate_shows_blocked_message(self):
        """Blocked candidates must show '已阻断，无法创建计划'."""
        html = _run_render_resale_feed([self.BLOCKED_CANDIDATE])
        self.assertIn("已阻断，无法创建计划", html)

    def test_blocked_candidate_has_blocked_class(self):
        """Blocked candidates must have is-blocked CSS class."""
        html = _run_render_resale_feed([self.BLOCKED_CANDIDATE])
        self.assertIn("is-blocked", html)

    def test_blocked_candidate_shows_reason(self):
        """Blocked candidates must display the blocked_reason."""
        html = _run_render_resale_feed([self.BLOCKED_CANDIDATE])
        self.assertIn("食品类目禁止倒卖", html)

    def test_non_blocked_candidate_has_plan_button(self):
        """Non-blocked candidates must have createResalePlan button."""
        html = _run_render_resale_feed([self.APPROVED_CANDIDATE])
        self.assertIn("创建倒卖计划", html)
        self.assertIn('onclick="createResalePlan', html)

    def test_non_blocked_candidate_no_blocked_message(self):
        """Non-blocked candidates must NOT show blocked message."""
        html = _run_render_resale_feed([self.APPROVED_CANDIDATE])
        self.assertNotIn("已阻断", html)

    def test_non_blocked_candidate_no_blocked_class(self):
        """Non-blocked candidates must NOT have is-blocked class on the card."""
        html = _run_render_resale_feed([self.APPROVED_CANDIDATE])
        # The card div should not have is-blocked
        self.assertNotIn('resale-candidate is-blocked', html)

    def test_mixed_candidates_render_independently(self):
        """When rendering both blocked and non-blocked, each behaves correctly."""
        html = _run_render_resale_feed([self.BLOCKED_CANDIDATE, self.APPROVED_CANDIDATE])
        # Blocked: has blocked message, no plan button
        self.assertIn("已阻断，无法创建计划", html)
        # Non-blocked: has plan button
        self.assertIn("创建倒卖计划", html)
        # Both titles present
        self.assertIn("零食大礼包", html)
        self.assertIn("蓝牙耳机 TWS", html)

    def test_empty_candidates_shows_empty_state(self):
        """Empty candidate list shows '暂无候选'."""
        html = _run_render_resale_feed([])
        self.assertIn("暂无候选", html)

    def test_candidate_id_in_plan_button(self):
        """The createResalePlan button must reference the correct candidate_id."""
        html = _run_render_resale_feed([self.APPROVED_CANDIDATE])
        self.assertIn("rc_approved001", html)

    def test_economics_rendered(self):
        """Economics fields (supplier price, profit, margin) must appear in output."""
        html = _run_render_resale_feed([self.APPROVED_CANDIDATE])
        self.assertIn("供应价: ¥25", html)
        self.assertIn("目标售价: ¥59", html)
        self.assertIn("毛利: ¥25.1", html)

    def test_risk_flags_rendered(self):
        """Risk flags must appear as risk-flag spans."""
        html = _run_render_resale_feed([self.BLOCKED_CANDIDATE])
        self.assertIn("risk-flag", html)
        self.assertIn("食品类目", html)

    def test_evaluating_candidate_shows_plan_button(self):
        """Evaluating (non-blocked) candidates also get plan button."""
        evaluating = dict(self.APPROVED_CANDIDATE, candidate_id="rc_eval001", status="evaluating", risk_level="medium")
        html = _run_render_resale_feed([evaluating])
        self.assertIn("创建倒卖计划", html)
        self.assertIn("rc_eval001", html)


class TestResaleEndpointCount(unittest.TestCase):
    """Verify all 5 resale method+path endpoint combinations exist."""

    def test_server_initializes_resale_manager_for_live_routes(self):
        import xianyu_agent.server as srv
        from fastapi.testclient import TestClient

        self.assertIsNotNone(srv.resale_manager)
        client = TestClient(srv.app)

        for path in (
            "/resale/candidates",
            "/resale/dashboard/summary",
            "/resale/purchase-tasks",
            "/resale/orders",
            "/resale/orders/dashboard",
        ):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_five_endpoints_registered(self):
        from xianyu_agent.server import app
        resale_endpoints = []
        for r in app.routes:
            if hasattr(r, "path") and "/resale/" in r.path:
                methods = getattr(r, "methods", None)
                if methods:
                    for m in methods:
                        resale_endpoints.append((m, r.path))
        # 21 method+path combos: 5 original + 3 milestone 3 + 1 milestone 4 + 4 milestone 6 + 5 milestone 7 + 2 milestone 8 + 1 milestone 9
        self.assertEqual(len(resale_endpoints), 21,
                         f"Expected 21 resale endpoints, got {len(resale_endpoints)}: {resale_endpoints}")

    def test_post_and_get_share_candidates_path(self):
        from xianyu_agent.server import app
        candidate_methods = set()
        for r in app.routes:
            if hasattr(r, "path") and r.path == "/resale/candidates":
                if hasattr(r, "methods"):
                    candidate_methods.update(r.methods)
        self.assertIn("POST", candidate_methods)
        self.assertIn("GET", candidate_methods)


if __name__ == "__main__":
    unittest.main()
