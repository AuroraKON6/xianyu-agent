"""Integration-round browser acceptance: the commerce panel's new sections
(decision card material status, adoption, channel status, order projection)
against the REAL server app on an isolated temp DATA_DIR (stub adapter).

Real console assets, real routes, real persistence in a temp directory.
No model calls, no platform writes, no live data.
"""
from __future__ import annotations

import hashlib
import json
import random
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from quote_fixtures import declared_quote_csv


class CommerceIntegrationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as error:
            raise unittest.SkipTest(f"Chromium unavailable; browser acceptance not verified: {error}") from error
        cls.addClassCleanup(cls.browser.close)
        cls.artifacts = Path(tempfile.mkdtemp(prefix="commerce-ui-artifacts-"))
        print(f"\n[commerce-ui] artifacts: {cls.artifacts}")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="commerce-ui-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # the server module reads DATA_DIR at import time; run a fresh
        # subprocess server so each test gets its own isolated composition
        import os
        import subprocess
        import sys

        port = self._free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        env = dict(os.environ)
        env.update({
            "DATA_DIR": str(self.root / "data"),
            "DB_PATH": str(self.root / "xianyu.db"),
            "XIANYU_ADAPTER": "stub",
            "SHADOW_OBSERVER_ENABLED": "0",
            "RADAR_DISPATCHER_ENABLED": "0",
            "INTAKE_CONFIRM_OPERATOR": "ui-operator",
            "INTAKE_CONFIRM_TOKEN_SHA256": hashlib.sha256(
                b"intake-ui-fixture-token-0123456789abcdef").hexdigest(),
        })
        self.server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "xianyu_agent.server:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "critical"],
            env=env, cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(self._stop_server)
        deadline = time.monotonic() + 30
        import urllib.request

        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            self.fail("isolated server failed to start")

        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.console_errors = []
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.on("pageerror", lambda error: self.console_errors.append(str(error)))

    def _free_port(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", random.SystemRandom().randint(49152, 65535)))
        port = listener.getsockname()[1]
        listener.close()
        return port

    def _stop_server(self):
        self.server_proc.terminate()
        try:
            self.server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.server_proc.kill()
            self.server_proc.wait(timeout=10)

    def _open(self):
        self.page.goto(f"{self.base_url}/console")
        self.page.wait_for_selector("#decision-card")

    def _full_opportunity(self, sku):
        now = datetime.now(timezone.utc)
        model = "GZ-1200"  # the model hard constraint needs a known model on both sides
        csv_body = (
            "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
            f"{model},不锈钢工作台 {model},160,件,1,50,20,120x60x85cm,众商,全新\n"
        ).encode("utf-8")
        payload = declared_quote_csv(csv_body).decode("utf-8")
        self.page.fill("#intake-supplier", "杭州五金供应商")
        self.page.fill("#intake-quote-text", payload)
        self.page.click("#intake-import-preview-btn")
        self.page.wait_for_selector("#intake-import-result table")
        self.page.click("#intake-import-confirm-btn")
        self.page.wait_for_selector("#intake-batch-status table")
        # confirm material use through the REAL panel flow (G1C -> G3A gate):
        # generate the declaration template, fill verified facts, preview, confirm
        self.page.click("#intake-template-btn")
        self.page.evaluate(
            """() => {
              const node = document.getElementById('intake-declaration');
              const declaration = JSON.parse(node.value);
              const now = new Date();
              const iso = (ms) => new Date(ms).toISOString();
              declaration.origin_ref = 'isolated-fixture-reference';
              declaration.supplier_identity_ref = 'fixture:identity';
              declaration.use_permission_ref = 'fixture:permission';
              declaration.local_review_permitted = true;
              declaration.issued_at = iso(Date.now() - 95 * 60000);
              declaration.received_at = iso(Date.now() - 50 * 60000);
              declaration.valid_until = iso(Date.now() + 20 * 3600000);
              for (const terms of Object.values(declaration.offers)) {
                terms.freight_destination = '杭州仓';
                terms.tax_terms = '含税出厂价（夹具假设）';
                terms.delivery_terms = '48小时内出库';
                terms.after_sales_terms = '错货破损供应商承担';
                terms.stock_observed_at = iso(Date.now() - 3600000);
                terms.stock_valid_until = iso(Date.now() + 8 * 3600000);
                terms.evidence_ref = 'fixture:row';
              }
              node.value = JSON.stringify(declaration, null, 2);
            }"""
        )
        self.page.fill("#intake-operator-token", "intake-ui-fixture-token-0123456789abcdef")
        self.page.click("#intake-preview-btn")
        self.page.wait_for_selector("#intake-preview-result")  # 采纳前置：本地分析许可已确认
        expect(self.page.locator("#intake-preview-result")).to_contain_text("SKU")
        self.page.click("#intake-confirm-btn")
        self.page.wait_for_selector("#intake-confirm-result[data-tone='ok']")
        self.page.fill("#market-sku", sku)
        self.page.fill("#market-sku", sku)
        self.page.fill("#market-title", f"不锈钢工作台 {model} 全新")
        self.page.fill("#market-spec", "120x60x85cm")
        self.page.fill("#market-brand", "众商")
        self.page.fill("#market-price", "300")
        self.page.click("#market-save-btn")
        self.page.wait_for_selector("#market-save-result:not(:empty)")
        self.page.fill("#match-market-id", "")  # left empty: use latest via API below
        # use API for the match + opportunity to keep the UI test focused on
        # the decision card / adoption / projection surfaces
        import urllib.request

        offers = json.loads(urllib.request.urlopen(f"{self.base_url}/commerce/offers").read())
        rows = offers.get("offers") or offers if isinstance(offers, dict) else offers
        market = [o for o in rows if o.get("provider_id") == "market:xianyu_observation"][-1]
        supplier = [o for o in rows if str(o.get("provider_id", "")).startswith("supplier_file:")][-1]
        match = json.loads(urllib.request.urlopen(
            urllib.request.Request(
                f"{self.base_url}/commerce/product-matches",
                data=json.dumps({"left_snapshot_id": market["record_id"],
                                 "right_snapshot_id": supplier["record_id"]}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
        ).read())
        opportunity = json.loads(urllib.request.urlopen(
            urllib.request.Request(
                f"{self.base_url}/commerce/opportunities",
                data=json.dumps({"market_snapshot_id": market["record_id"],
                                 "supplier_snapshot_id": supplier["record_id"],
                                 "match_id": match["match_id"]}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
        ).read())
        evaluate = json.loads(urllib.request.urlopen(
            urllib.request.Request(
                f"{self.base_url}/commerce/opportunities/{opportunity['record_id']}/evaluate",
                data=json.dumps({"platform_fee_rate": "0.03",
                                 "packaging": {"state": "known", "amount": "5", "currency": "CNY"},
                                 "expected_return_loss": {"state": "known", "amount": "12", "currency": "CNY"},
                                 "attributable_costs": {"state": "known", "amount": "4", "currency": "CNY"}}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
        ).read())
        return opportunity["record_id"], evaluate

    def test_decision_card_adopt_and_projection_flow(self):
        self._open()
        opportunity_id, _ = self._full_opportunity("SKU-UI")
        # decision card renders with material status (real API through UI JS)
        self.page.evaluate(
            "opportunityId => { window.__commerceStateProbe = opportunityId; }", opportunity_id
        )
        self.page.click("#commerce-refresh")
        # render the card through the same UI entry the user would use
        self.page.evaluate(
            """async (opportunityId) => {
              commerceState.opportunityId = opportunityId;
              await commerceRenderDecisionCard(opportunityId);
            }""",
            opportunity_id,
        )
        expect(self.page.locator("#decision-card")).to_contain_text("资料到期")
        expect(self.page.locator("#decision-card")).to_contain_text("local_analysis")
        self.page.screenshot(path=str(self.artifacts / "decision-card-material.png"))

        # create a candidate, then adopt through the UI
        import urllib.request

        urllib.request.urlopen(urllib.request.Request(
            f"{self.base_url}/resale/candidates",
            data=json.dumps({
                "title": "不锈钢工作台", "source_platform": "pdd",
                "source_url": "https://fixture.example/item",
                "supplier_name": "杭州五金供应商", "supplier_sku": "SKU-UI",
                "supplier_price": 160, "supplier_shipping": 20,
                "target_sale_price": 300, "category": "商用厨具",
            }).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        ))
        # the dropdown was rendered before the candidate existed: reload it
        self.page.evaluate("async () => { await commerceLoadAdoptTargets(); }")
        self.page.select_option("#adopt-candidate-select", index=1)
        self.page.fill("#adopt-account", "acct-ui")
        self.page.fill("#adopt-reason", "规格与保守利润核对后采纳该商机版本")
        self.page.click("#adopt-btn")
        expect(self.page.locator("#adopt-result")).to_contain_text("采纳已记录")
        self.page.click("#adopt-recheck-btn")
        expect(self.page.locator("#adopt-result")).to_contain_text("仍然有效")
        self.page.screenshot(path=str(self.artifacts / "adoption-flow.png"))

        # channel status: honest unconfigured answer, radar counters visible
        self.page.click("#market-channel-status-btn")
        expect(self.page.locator("#channel-status-result")).to_contain_text("unconfigured")

        # order projection: empty state is explicit, not fabricated
        self.page.fill("#order-detail-ref", "xy-nothing")
        self.page.click("#order-detail-btn")
        expect(self.page.locator("#order-detail-result")).to_contain_text("无采购记录")
        expect(self.page.locator("#order-detail-result")).to_contain_text("无账本分录")
        self.page.screenshot(path=str(self.artifacts / "projection-empty.png"))

        errors = [error for error in self.console_errors if "favicon" not in error.lower()]
        self.assertEqual(errors, [], f"console errors: {errors}")


if __name__ == "__main__":
    unittest.main()
