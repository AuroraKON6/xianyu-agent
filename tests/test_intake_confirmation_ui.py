"""G1C browser acceptance for the intake material loop (A30-A33).

Real console markup/styles and the real intake-confirmation module against
real isolated routes (fixture server on a high loopback port). No live
data, no model calls, no platform actions. Screenshots land in a printed
temp directory and are referenced by the acceptance report.
"""
from __future__ import annotations

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

from intake_ui_fixture import TOKEN, build_fixture

QUOTE_TEXT_TEMPLATE = (
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色,"
    "source_kind,source_reference,source_observed_at,valid_until\n"
    "{rows}"
)


def _quote_rows(now: datetime, skus=(("SKU-UI-A", "150"), ("SKU-UI-B", "30"))):
    observed = (now - timedelta(minutes=30)).isoformat()
    valid_until = (now + timedelta(hours=24)).isoformat()
    rows = []
    for sku, price in skus:
        rows.append(
            f"{sku},不锈钢工作台,{price},件,1,50,包邮,120x60x85cm,众商,全新,"
            f"supplier_original,ui-fixture-quote,{observed},{valid_until}"
        )
    return "\n".join(rows)


class IntakePanelBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as error:
            raise unittest.SkipTest(f"Chromium unavailable; browser acceptance not verified: {error}") from error
        cls.addClassCleanup(cls.browser.close)
        cls.artifacts = Path(tempfile.mkdtemp(prefix="intake-ui-artifacts-"))
        print(f"\n[intake-ui] artifacts: {cls.artifacts}")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="intake-ui-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app, self.imports = build_fixture(self.root)
        for _ in range(64):
            listener = socket.socket()
            try:
                listener.bind(("127.0.0.1", random.SystemRandom().randint(49152, 65535)))
                break
            except OSError:
                listener.close()
        else:
            self.fail("no loopback high port available")
        self.addCleanup(listener.close)
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(self.app, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()

        def shutdown():
            server.should_exit = True
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "isolated UI server failed to stop")

        self.addCleanup(shutdown)
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(server.started, "isolated UI server failed to start")
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.console_errors = []
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.on("pageerror", lambda error: self.console_errors.append(str(error)))

    # ------------------------------------------------------------------ flow

    def _open(self):
        self.page.goto(f"{self.base_url}/console")
        self.page.wait_for_selector("#intake-panel")

    def _import_quote(self, skus=(("SKU-UI-A", "150"), ("SKU-UI-B", "30"))):
        now = datetime.now(timezone.utc)
        self.page.fill("#intake-supplier", "杭州五金供应商")
        self.page.fill("#intake-quote-text", QUOTE_TEXT_TEMPLATE.format(rows=_quote_rows(now, skus)))
        self.page.click("#intake-import-preview-btn")
        self.page.wait_for_selector("#intake-import-result table")
        self.page.click("#intake-import-confirm-btn")
        self.page.wait_for_selector("#intake-batch-status table")

    def _fill_declaration(self, valid_hours=20):
        self.page.click("#intake-template-btn")
        textarea = self.page.locator("#intake-declaration")
        raw = textarea.input_value()
        declaration = json.loads(raw)
        now = datetime.now(timezone.utc)
        declaration.update({
            "origin_ref": "ui-fixture-quote",
            "supplier_identity_ref": "fixture:identity",
            "use_permission_ref": "fixture:permission",
            "local_review_permitted": True,
            "external_llm_permitted": True,
            "issued_at": (now - timedelta(minutes=95)).isoformat(),
            "received_at": (now - timedelta(minutes=50)).isoformat(),
            "valid_until": (now + timedelta(hours=valid_hours)).isoformat(),
        })
        for terms in declaration["offers"].values():
            terms.update({
                "freight_destination": "杭州仓半径50公里",
                "tax_terms": "含税出厂价（夹具假设）",
                "delivery_terms": "48小时内出库",
                "after_sales_terms": "错货破损供应商承担往返运费",
                "stock_observed_at": (now - timedelta(hours=1)).isoformat(),
                "stock_valid_until": (now + timedelta(hours=8)).isoformat(),
                "evidence_ref": "fixture:row",
            })
        textarea.fill(json.dumps(declaration, ensure_ascii=False, indent=2))
        return declaration

    def _token(self):
        self.page.fill("#intake-operator-token", TOKEN)

    def _screenshot(self, name):
        self.page.screenshot(path=str(self.artifacts / f"{name}.png"), full_page=False)

    # ------------------------------------------------------------------- A30

    def test_a30_full_loop_without_copying_ids_and_reload(self):
        self._open()
        self._import_quote()
        # batch status shows SKUs by name, not raw ids
        expect(self.page.locator("#intake-batch-status")).to_contain_text("SKU-UI-A")
        expect(self.page.locator("#intake-batch-status")).to_contain_text("SKU-UI-B")
        self._fill_declaration()
        self._token()
        self.page.click("#intake-preview-btn")
        self.page.wait_for_selector("#intake-preview-result")
        expect(self.page.locator("#intake-preview-result")).to_contain_text("SKU-UI-A")
        expect(self.page.locator("#intake-preview-result")).to_contain_text("SKU-UI-B")
        expect(self.page.locator("#intake-preview-result")).to_contain_text("有效期不超过原报价截止")
        self._screenshot("a30-preview")
        self.page.click("#intake-confirm-btn")
        self.page.wait_for_selector("#intake-confirm-result[data-tone='ok']")
        expect(self.page.locator("#intake-confirm-result")).to_contain_text("用途确认已保存")
        self.page.click("#intake-eligibility-btn")
        self.page.wait_for_selector("#intake-eligibility-result table")
        expect(self.page.locator("#intake-eligibility-result")).to_contain_text("local_analysis")
        rows = self.page.locator("#intake-eligibility-result tbody tr")
        self.assertEqual(rows.count(), 2)
        for index in range(rows.count()):
            self.assertIn("✔ 可用", rows.nth(index).inner_text())
        self._screenshot("a30-eligibility")

        # reload: hash restores the batch view and the saved confirmation is
        # reachable again (draft is intentionally cleared after a successful
        # confirm, so it is NOT restored)
        self.page.reload()
        self.page.wait_for_selector("#intake-batch-status table")
        expect(self.page.locator("#intake-batch-status")).to_contain_text("SKU-UI-A")
        expect(self.page.locator("#intake-batch-status")).to_contain_text("local_analysis")
        self.assertEqual(self.page.locator("#intake-declaration").input_value(), "")
        self._screenshot("a30-after-reload")

    # ------------------------------------------------------------------- A31

    def test_a31_double_click_guard_and_partial_exclusion(self):
        self._open()
        # SKU-UI-B gets a conflicting unit in the declaration -> excluded
        self._import_quote()
        self._fill_declaration()
        raw = json.loads(self.page.locator("#intake-declaration").input_value())
        second_snapshot = list(raw["offers"].keys())[1]
        raw["offers"][second_snapshot]["quantity_unit"] = "箱"
        self.page.locator("#intake-declaration").fill(json.dumps(raw, ensure_ascii=False, indent=2))
        self._token()
        # double click fires twice; the busy guard must collapse them
        self.page.locator("#intake-preview-btn").dblclick()
        self.page.wait_for_selector("#intake-preview-result")
        expect(self.page.locator("#intake-preview-result")).to_contain_text("SKU-UI-A")
        expect(self.page.locator("#intake-preview-result")).to_contain_text("排除")
        # confirm is available; after confirming once it is disabled (no duplicate confirm)
        self.page.click("#intake-confirm-btn")
        self.page.wait_for_selector("#intake-confirm-result[data-tone='ok']")
        self.assertFalse(self.page.locator("#intake-confirm-btn").is_enabled())
        # the excluded SKU must not gain eligibility
        self.page.click("#intake-eligibility-btn")
        self.page.wait_for_selector("#intake-eligibility-result table")
        body_text = self.page.locator("#intake-eligibility-result").inner_text()
        self.assertIn("SKU-UI-A", body_text)
        self.assertIn("SKU-UI-B", body_text)
        self.assertIn("no_active_confirmation", body_text)
        self._screenshot("a31-partial")

    # ------------------------------------------------------------------- A32

    def test_a32_network_failure_preserves_draft_and_empty_state(self):
        self._open()
        # empty state before any import
        expect(self.page.locator("#intake-batch-status")).to_contain_text("暂无导入批次")
        # a failing list request surfaces an error without clearing the draft
        self.page.fill("#intake-declaration", '{"draft": "保留的声明草稿"}')
        build_fixture.fail_next_list = True
        self.page.reload()
        self.page.wait_for_selector("#intake-panel")
        self.page.wait_for_selector("#intake-confirm-result[data-tone='error']")
        draft = self.page.locator("#intake-declaration").input_value()
        self.assertEqual(draft, '{"draft": "保留的声明草稿"}')

        # aborted preview request keeps the filled text (A32)
        self._import_quote()
        self._fill_declaration()
        self.page.route("**/confirmation/preview", lambda route: route.abort())
        self._token()
        self.page.click("#intake-preview-btn")
        self.page.wait_for_selector("#intake-confirm-result[data-tone='error']")
        self.assertTrue(self.page.locator("#intake-declaration").input_value().strip())
        self.page.unroute("**/confirmation/preview")
        self._screenshot("a32-network-error")

    # ------------------------------------------------------------------- A33

    def test_a33_viewports_no_overflow_and_console_clean(self):
        for width, height, label in ((1440, 900, "desktop"), (390, 844, "mobile")):
            self.page.set_viewport_size({"width": width, "height": height})
            self._open()
            self._import_quote()
            self._fill_declaration()
            overflow = self.page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            self.assertLessEqual(overflow, 1, f"unexpected horizontal overflow at {label}")
            panel_overflow = self.page.evaluate(
                "() => { const p = document.querySelector('#intake-panel'); return p.scrollWidth - p.clientWidth; }"
            )
            self.assertLessEqual(panel_overflow, 1, f"panel horizontal overflow at {label}")
            confirm_text = self.page.locator("#intake-confirm-btn").inner_text()
            self.assertTrue(confirm_text.strip())
            self._screenshot(f"a33-{label}")
        errors = [error for error in self.console_errors if "favicon" not in error.lower()]
        self.assertEqual(errors, [], f"console errors: {errors}")


if __name__ == "__main__":
    unittest.main()
