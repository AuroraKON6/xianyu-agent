import json
import random
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import uvicorn
from playwright.sync_api import expect, sync_playwright

from pi_usage_ui_fixture import TOKEN, WEB_ROOT, build_fixture
from xianyu_agent.pi_jobs import ModelUsageLedger


class UsageReconciliationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as error:
            raise unittest.SkipTest(f"Chromium unavailable; browser acceptance not verified: {error}") from error
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pi-usage-ui-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        app, self.runner, self.job = build_fixture(self.root)
        # Some hosts allocate low ephemeral ports (e.g. 6665), which
        # Chromium refuses. Keep ownership of a high-port socket throughout.
        for _ in range(64):
            listener = socket.socket()
            try:
                listener.bind(("127.0.0.1", random.SystemRandom().randint(49152, 65535)))
                break
            except OSError:
                listener.close()
        else:
            self.fail("no loopback high port available for isolated browser acceptance")
        self.addCleanup(listener.close)
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        server_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        server_thread.start()

        def shutdown():
            server.should_exit = True
            server_thread.join(timeout=10)
            self.assertFalse(server_thread.is_alive(), "isolated UI server failed to stop")

        self.addCleanup(shutdown)
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(server.started, "isolated UI server failed to start")
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.context.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(self.base_url + "/") else route.abort())
        self.page = self.context.new_page()
        self.page.set_default_timeout(6000)
        self.requests = []
        self.errors = []
        self.page.on("request", lambda request: self.requests.append(request))
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.base_url + "/console")

    def node(self, suffix):
        return self.page.locator("#pi-usage-" + suffix)

    def unlock(self, token=TOKEN):
        self.node("token").fill(token)
        self.node("unlock").click()
        if token == TOKEN:
            expect(self.node("refresh")).to_be_enabled()

    def prepare(self, resolution="settle", reference="fixture-provider-report-001"):
        self.unlock()
        self.page.locator("[data-usage-select]").click()
        self.node("resolution").select_option(resolution)
        if resolution == "settle":
            self.node("tokens").fill("15")
        self.node("reference").fill(reference)
        self.node("reason").fill("人工核对隔离测试用量记录，与原任务尝试一一对应。")
        self.node("statement").fill("仅为离线浏览器验收示例，已核对该任务尝试和原业务日，无真实供应商调用。")

    def preview(self):
        self.node("preview-btn").click()
        expect(self.node("preview")).to_be_visible()

    def confirm(self):
        self.node("confirm").check()
        self.node("commit").click()

    def ledger_entry(self):
        return ModelUsageLedger(self.root)._store.all()[0]

    def submissions(self):
        return [request for request in self.requests if request.url.endswith("/pi-usage/reconcile")]

    def assert_safe(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(self.page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length"), 0)
        self.assertEqual(self.context.cookies(), [])
        for request in self.requests:
            self.assertNotIn(TOKEN, request.url)
            self.assertNotIn(TOKEN, request.post_data or "")
            if request.method == "POST":
                self.assertIn(request.url.rsplit("/", 1)[-1], ("preview", "reconcile"))
            if request.headers.get("authorization"):
                self.assertTrue(request.url.startswith(self.base_url + "/commerce/pi-usage/"))
        self.assertNotIn(TOKEN, self.runner.model_usage._store.path.read_text(encoding="utf-8"))
        self.assertEqual(self.runner._jobs.get(self.job["record_id"])["status"], "failed")
        self.assertEqual(self.runner._decisions.list(), [])

    def test_settle_preview_is_read_only_then_audited_commit(self):
        expect(self.node("tokens")).to_be_disabled()
        self.assertEqual(len(self.requests), 4)
        self.prepare()
        original = self.runner.model_usage._store.path.read_bytes()
        self.preview()
        self.assertEqual(self.runner.model_usage._store.path.read_bytes(), original)
        expect(self.node("commit")).to_be_disabled()
        expect(self.node("proof")).to_contain_text("ui-fixture-operator")
        expect(self.node("diff")).to_contain_text("未知（阻断）")
        expect(self.node("diff")).to_contain_text("total_tokens：15")
        self.confirm()
        expect(self.node("receipt-panel")).to_be_visible()
        receipt = json.loads(self.node("receipt").inner_text())["receipt"]
        self.assertEqual(receipt, self.ledger_entry()["reconciliation"])
        self.assertEqual(receipt["business_day"], "2026-09-22")
        expect(self.node("commit")).to_be_disabled()
        self.node("refresh").click()
        expect(self.node("count")).to_contain_text("0 条")
        self.assert_safe()

    def test_shadow_desk_distinguishes_total_from_source_eligible_counts(self):
        self.page.route("**/console/app.js", lambda route: route.fulfill(path=str(WEB_ROOT / "app.js")))
        self.page.route("**/commerce/agent-jobs/stats", lambda route: route.fulfill(
            json={"by_status": {"succeeded": 2}}))
        self.page.route("**/commerce/shadow-observer/status", lambda route: route.fulfill(json={
            "background_thread": False, "metrics": {"total": 105, "allowed": 100,
                "real_source_samples": 0, "real_source_days": 0, "excluded_source_samples": 105,
                "readiness_note": "Fixture: sample gate is NOT met; no action authorized."}}))
        self.page.reload()
        for width in (1440, 390):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.page.locator("#pi-status-btn").click()
            result = self.page.locator("#pi-task-result")
            expect(result).to_contain_text("影子记录：105 条")
            expect(result).to_contain_text("来源声明合格：0 条 / 0 天")
            expect(result).to_contain_text("未计入：105 条")
            expect(result).to_contain_text("自动执行未授权")
            self.assertTrue(result.evaluate("node => node.scrollWidth <= node.clientWidth + 1"))
        self.assert_safe()

    def test_editing_preview_revokes_confirmation_and_requires_new_digest(self):
        self.prepare()
        self.preview()
        first = json.loads(self.node("proof").inner_text())
        self.node("confirm").check()
        self.node("tokens").fill("27")
        expect(self.node("preview")).to_be_hidden()
        expect(self.node("confirm")).not_to_be_checked()
        expect(self.node("commit")).to_be_disabled()
        self.preview()
        second = json.loads(self.node("proof").inner_text())
        self.assertNotEqual(first["approval_digest"], second["approval_digest"])
        self.confirm()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertEqual(self.ledger_entry()["total_tokens"], 27)
        self.assert_safe()

    def test_release_requires_explicit_no_dispatch_evidence(self):
        self.prepare(resolution="release")
        expect(self.node("tokens")).to_have_value("0")
        self.assertTrue(self.node("tokens").evaluate("node => node.readOnly"))
        self.preview()
        expect(self.node("proof")).to_contain_text("dispatch_not_sent")
        self.confirm()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertEqual(self.ledger_entry()["status"], "released")
        self.assert_safe()

    def test_lost_response_reauth_retries_identical_body_and_original_receipt(self):
        self.prepare()
        self.preview()

        def lose_response(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.abort("failed")

        self.page.route("**/pi-usage/reconcile", lose_response, times=1)
        self.confirm()
        expect(self.node("status")).to_contain_text("结果不明")
        expect(self.node("tokens")).to_be_disabled()
        expect(self.node("refresh")).to_be_disabled()
        saved = self.runner.model_usage._store.path.read_bytes()
        first_body = self.submissions()[0].post_data
        self.node("lock").click()
        expect(self.node("token")).to_have_value("")
        expect(self.node("status")).to_contain_text("原请求仅保留")
        self.node("token").fill(TOKEN)
        self.node("unlock").click()
        expect(self.node("commit")).to_be_enabled()
        self.node("commit").click()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertTrue(json.loads(self.node("receipt").inner_text())["idempotent"])
        self.assertEqual(self.submissions()[1].post_data, first_body)
        self.assertEqual(self.runner.model_usage._store.path.read_bytes(), saved)
        self.assert_safe()

    def test_storage_failure_freezes_original_request_until_recovery(self):
        self.prepare()
        self.preview()
        with patch.object(self.runner.model_usage._store, "_persist", side_effect=OSError("fixture disk full")):
            self.confirm()
            expect(self.node("status")).to_contain_text("结果不明")
        self.assertIsNone(self.ledger_entry()["total_tokens"])
        self.node("commit").click()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertEqual(self.submissions()[0].post_data, self.submissions()[1].post_data)
        self.assertEqual(self.ledger_entry()["total_tokens"], 15)
        self.assert_safe()

    def test_lock_during_pending_request_clears_credential_and_preserves_retry(self):
        self.prepare()
        self.preview()
        held = {}
        self.page.route("**/pi-usage/reconcile", lambda route: held.update(route=route), times=1)
        self.confirm()
        expect(self.node("status")).to_contain_text("提交中")
        self.node("lock").click()
        expect(self.node("token")).to_have_value("")
        expect(self.node("status")).to_contain_text("原请求仅保留")
        expect(self.node("commit")).to_be_disabled()
        self.assertIn("route", held)
        held["route"].abort("failed")
        self.assertIsNone(self.ledger_entry()["total_tokens"])
        self.node("token").fill(TOKEN)
        self.node("unlock").click()
        expect(self.node("commit")).to_be_enabled()
        self.node("commit").click()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertEqual(self.submissions()[0].post_data, self.submissions()[1].post_data)
        self.assert_safe()

    def test_incomplete_success_response_cannot_be_treated_as_receipt(self):
        self.prepare()
        self.preview()
        self.page.route("**/pi-usage/reconcile", lambda route: route.fulfill(status=200,
                        content_type="application/json", body='{"receipt":{}}'), times=1)
        self.confirm()
        expect(self.node("status")).to_contain_text("结果不明")
        expect(self.node("receipt-panel")).to_be_hidden()
        self.assertIsNone(self.ledger_entry()["total_tokens"])
        self.node("commit").click()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertEqual(self.submissions()[0].post_data, self.submissions()[1].post_data)
        self.assert_safe()

    def test_invalid_tokens_and_contradicted_release_never_commit(self):
        self.prepare()
        self.node("tokens").fill("1.5")
        self.node("preview-btn").click()
        self.assertFalse(any(request.url.endswith("/pi-usage/preview") for request in self.requests))
        with self.runner.model_usage._store as store:
            store._items[0]["model"] = "fixture-known-model"
        self.node("resolution").select_option("release")
        self.node("preview-btn").click()
        expect(self.node("status")).to_contain_text("冲突")
        expect(self.node("commit")).to_be_disabled()
        self.assertEqual(self.submissions(), [])
        self.assertIsNone(self.ledger_entry()["total_tokens"])
        self.assert_safe()

    def test_stale_confirmation_is_rejected_by_real_api(self):
        self.prepare()
        self.preview()
        with self.runner._jobs._store as store:
            store._items[0]["error"] = "fixture state changed after preview"
        self.confirm()
        expect(self.node("status")).to_contain_text("冲突")
        expect(self.node("preview")).to_be_hidden()
        expect(self.node("commit")).to_be_disabled()
        self.assertIsNone(self.ledger_entry()["total_tokens"])
        self.preview()
        self.confirm()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assert_safe()

    def test_double_click_sends_one_submission(self):
        self.prepare()
        self.preview()
        self.node("confirm").check()
        self.node("commit").evaluate("node => { node.click(); node.click(); }")
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertEqual(len(self.submissions()), 1)
        self.assert_safe()

    def test_xss_is_literal_and_legacy_unknown_cannot_be_selected(self):
        with self.runner.model_usage._store as store:
            store._items.append({"job_id": "<img src=x onerror=window.uiXss=1>",
                                 "business_day": "2020-01-01", "total_tokens": None, "status": "unknown"})
        self.prepare(reference="<img src=x onerror=window.uiXss=1>")
        expect(self.node("count")).to_contain_text("2 条")
        self.assertEqual(self.page.locator("[data-usage-select]").count(), 1)
        self.preview()
        self.confirm()
        expect(self.node("receipt-panel")).to_be_visible()
        self.assertEqual(self.page.locator("#pi-usage-panel img").count(), 0)
        self.assertIsNone(self.page.evaluate("window.uiXss"))
        self.assertTrue(any(entry.get("total_tokens") is None for entry in ModelUsageLedger(self.root)._store.all()))
        self.assert_safe()

    def test_wrong_credential_and_disabled_service_do_not_unlock(self):
        self.node("token").fill("incorrect-fixture-credential-123456789")
        self.node("unlock").click()
        expect(self.node("status")).to_contain_text("凭证无效")
        expect(self.node("tokens")).to_be_disabled()
        self.page.route("**/pi-usage/unresolved", lambda route: route.fulfill(status=503,
                        content_type="application/json", body='{"detail":"disabled"}'))
        self.node("token").fill(TOKEN)
        self.node("unlock").click()
        expect(self.node("status")).to_contain_text("未启用")
        expect(self.node("refresh")).to_be_disabled()
        expect(self.node("token")).to_have_value("")
        self.assertEqual(self.submissions(), [])
        self.assert_safe()

    def test_reload_pagehide_and_explicit_lock_clear_credentials(self):
        self.prepare()
        self.preview()
        self.assert_safe()
        self.node("lock").click()
        expect(self.node("proof")).to_have_text("")
        expect(self.node("tokens")).to_be_disabled()
        self.unlock()
        self.page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        expect(self.node("token")).to_have_value("")
        expect(self.node("refresh")).to_be_disabled()
        self.unlock()
        self.page.reload()
        expect(self.node("token")).to_have_value("")
        expect(self.node("refresh")).to_be_disabled()
        self.assert_safe()

    def test_desktop_and_mobile_panel_have_readable_width_without_overflow(self):
        self.prepare()
        self.preview()
        self.assertGreater(self.page.locator("#pi-usage-panel").bounding_box()["width"], 600)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator("#pi-usage-panel").scroll_into_view_if_needed()
        dimensions = self.page.locator("#pi-usage-panel").evaluate(
            "node => ({width: node.clientWidth, scroll: node.scrollWidth})")
        self.assertLess(dimensions["width"], 390)
        self.assertLessEqual(dimensions["scroll"], dimensions["width"] + 1)
        self.assert_safe()


if __name__ == "__main__":
    unittest.main()
