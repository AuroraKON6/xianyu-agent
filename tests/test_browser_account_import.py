from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent.account_runtime import AccountRuntimeStore
from xianyu_agent.browser_adapter import BrowserAdapter
from xianyu_agent.db_manager import DBManager


class _FakeResponse:
    async def json(self):
        return {"content": {"success": True}}


class _FakeRequestContext:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse()


class _FakeContext:
    def __init__(self, cookies):
        self.cookie_values = list(cookies)
        self.request = _FakeRequestContext()

    async def cookies(self):
        return list(self.cookie_values)


class _FakePage:
    def is_closed(self):
        return False


def _cookies(account_id="2218280680651", cookie2="session-secret"):
    return [
        {"name": "unb", "value": account_id, "domain": ".goofish.com"},
        {"name": "cookie2", "value": cookie2, "domain": ".taobao.com"},
        {"name": "_m_h5_tk", "value": "token-secret_123", "domain": ".goofish.com"},
        {"name": "tracknick", "value": "seller%20name", "domain": ".taobao.com"},
        {"name": "unrelated", "value": "ignore-me", "domain": ".example.com"},
    ]


class BrowserAccountImportAdapterTests(unittest.IsolatedAsyncioTestCase):
    def make_adapter(self):
        root = Path(tempfile.mkdtemp())
        settings = SimpleNamespace(data_dir=root, browser_cdp_url="", xianyu_cookies="legacy-secret")
        return BrowserAdapter(settings, storage=None, browser_pool=None)

    async def _attach_context(self, adapter, cookies):
        context = _FakeContext(cookies)

        async def ensure_session(cookie_id="default", *, require_cdp=False):
            adapter._persistent_context = context
            adapter._persistent_cookie_id = cookie_id
            adapter._persistent_is_cdp = require_cdp
            return _FakePage()

        adapter._ensure_session = ensure_session
        return context

    def test_account_database_precedes_file_and_global_cookie_fallbacks(self):
        adapter = self.make_adapter()
        adapter._save_cookies("seller_file", "unb=file")
        adapter.set_cookie_provider(lambda cookie_id: "unb=database" if cookie_id == "seller_db" else None)

        self.assertEqual(adapter._load_cookies("seller_db"), "unb=database")
        self.assertEqual(adapter._load_cookies("seller_file"), "unb=file")
        self.assertEqual(adapter._load_cookies("missing"), "")
        self.assertEqual(adapter._load_cookies("default"), "legacy-secret")

    async def test_preview_verifies_login_and_never_returns_cookie_values(self):
        adapter = self.make_adapter()
        context = await self._attach_context(adapter, _cookies())

        preview = await adapter.preview_current_account_import(now=1000)

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["account_id"], "2218280680651")
        self.assertEqual(preview["display_name"], "seller name")
        self.assertEqual(preview["expires_at"], 1300)
        self.assertEqual(preview["probe"], "passport_has_login")
        self.assertEqual(len(context.request.calls), 1)
        serialized = str(preview)
        self.assertNotIn("session-secret", serialized)
        self.assertNotIn("token-secret", serialized)
        self.assertNotIn("cookie_string", preview)

    async def test_confirm_rechecks_same_session_and_returns_secret_only_internally(self):
        adapter = self.make_adapter()
        await self._attach_context(adapter, _cookies())
        preview = await adapter.preview_current_account_import(now=1000)

        confirmed = await adapter.confirm_current_account_import(preview["preview_token"], now=1100)

        self.assertTrue(confirmed["ok"])
        self.assertEqual(confirmed["account_id"], preview["account_id"])
        self.assertIn("cookie2=session-secret", confirmed["cookie_string"])

    async def test_confirm_blocks_expired_or_changed_browser_session(self):
        adapter = self.make_adapter()
        context = await self._attach_context(adapter, _cookies())
        expired = await adapter.preview_current_account_import(now=1000)
        expired_result = await adapter.confirm_current_account_import(expired["preview_token"], now=1301)
        self.assertEqual(expired_result["error_code"], "account_import_preview_expired")

        preview = await adapter.preview_current_account_import(now=2000)
        context.cookie_values = _cookies(account_id="9988776655443")
        changed = await adapter.confirm_current_account_import(preview["preview_token"], now=2001)
        self.assertEqual(changed["error_code"], "browser_account_changed")

    async def test_preview_blocks_incomplete_login_cookie_set_before_probe(self):
        adapter = self.make_adapter()
        context = await self._attach_context(
            adapter,
            [{"name": "unb", "value": "2218280680651", "domain": ".goofish.com"}],
        )

        result = await adapter.preview_current_account_import(now=1000)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "browser_login_cookies_missing")
        self.assertEqual(context.request.calls, [])

    async def test_preview_requires_dedicated_cdp_and_rejects_untrusted_cookie_domains(self):
        adapter = self.make_adapter()
        required = []

        async def unavailable(cookie_id="default", *, require_cdp=False):
            required.append((cookie_id, require_cdp))
            return None

        adapter._ensure_session = unavailable
        unavailable_result = await adapter.preview_current_account_import(now=1000)
        self.assertEqual(unavailable_result["error_code"], "browser_cdp_unavailable")
        self.assertEqual(required, [("__account_import__", True)])

        await self._attach_context(
            adapter,
            [
                {"name": "unb", "value": "2218280680651", "domain": ".evilgoofish.com"},
                {"name": "cookie2", "value": "secret", "domain": ".taobao.com.evil.test"},
                {"name": "_m_h5_tk", "value": "token", "domain": ""},
            ],
        )
        untrusted_result = await adapter.preview_current_account_import(now=1000)
        self.assertEqual(untrusted_result["error_code"], "browser_login_cookies_missing")


class BrowserAccountImportRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server

        self.server = server
        self.temp = tempfile.TemporaryDirectory()
        self.db = DBManager(str(Path(self.temp.name) / "accounts.db"))
        self.runtime = AccountRuntimeStore(Path(self.temp.name) / "runtime.json")
        settings = SimpleNamespace(data_dir=Path(self.temp.name), browser_cdp_url="", xianyu_cookies="")
        self.adapter = BrowserAdapter(settings, storage=None, browser_pool=None)
        self.adapter_closed = 0

        async def close():
            self.adapter_closed += 1

        self.adapter.close = close
        self.patches = [
            patch.object(server, "db_manager", self.db),
            patch.object(server, "account_runtime_store", self.runtime),
            patch.object(server.workflow, "adapter", self.adapter),
        ]
        for item in self.patches:
            item.start()
        server.browser_account_import_commits.clear()
        server.browser_account_import_inflight.clear()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.server.browser_account_import_commits.clear()
        self.server.browser_account_import_inflight.clear()
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.db.close()
        self.temp.cleanup()

    def test_preview_is_sanitized_and_marks_create_or_refresh(self):
        async def preview():
            return {
                "ok": True,
                "preview_token": "abcdefgh-preview-token",
                "account_id": "2218280680651",
                "display_name": "seller name",
                "cookie_count": 9,
                "required_cookie_names": ["_m_h5_tk", "cookie2", "unb"],
                "session_ref": "abc123def456",
                "probe": "passport_has_login",
                "observed_at": 1000,
                "expires_at": 1300,
                "confirmation_phrase": "BIND ABCDEFGH",
            }

        self.adapter.preview_current_account_import = preview
        response = self.client.post("/accounts/browser-import/preview")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["operation"], "create")
        self.assertFalse(body["existing_account"])
        self.assertNotIn("cookie_string", body)
        self.assertNotIn("session-secret", response.text)
        self.assertNotIn("token-secret", response.text)

    def test_open_login_route_returns_no_page_or_credential_content(self):
        with patch.object(
            self.server.workflow,
            "browser_open_account_login",
            return_value={
                "ok": True,
                "url": "https://www.goofish.com/",
                "debug_url": "http://127.0.0.1:9223",
                "browser_started": True,
                "tab_id": "tab-1",
            },
        ):
            response = self.client.post("/accounts/browser-import/open")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tab_id"], "tab-1")
        self.assertNotIn("cookie", response.text.lower())
        self.assertNotIn("websocket", response.text.lower())
        self.assertNotIn("page_text", response.text.lower())

    def test_commit_requires_confirmation_and_never_returns_cookie(self):
        calls = []

        async def confirm(token):
            calls.append(token)
            return {
                "ok": True,
                "account_id": "2218280680651",
                "display_name": "seller name",
                "cookie_string": "unb=2218280680651; cookie2=route-secret; _m_h5_tk=token-secret",
                "cookie_count": 3,
                "session_ref": "sessionref12",
                "probe": "passport_has_login",
                "observed_at": 1000,
            }

        self.adapter.confirm_current_account_import = confirm
        wrong = self.client.post(
            "/accounts/browser-import/commit",
            json={"preview_token": "abcdefgh-preview-token", "confirmation": "BIND WRONG"},
        )
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(calls, [])

        payload = {
            "preview_token": "abcdefgh-preview-token",
            "confirmation": "BIND ABCDEFGH",
            "remark": "main kitchen account",
        }
        response = self.client.post("/accounts/browser-import/commit", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["operation"], "create")
        self.assertEqual(body["cookie_id"], "2218280680651")
        self.assertNotIn("route-secret", response.text)
        self.assertNotIn("token-secret", response.text)
        self.assertEqual(self.db.get_cookie("2218280680651").split("; ")[1], "cookie2=route-secret")
        self.assertTrue(self.runtime.get_public_state("2218280680651")["cookie_fresh"])
        self.assertEqual(self.adapter_closed, 1)

        repeated = self.client.post("/accounts/browser-import/commit", json=payload)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json(), body)
        self.assertEqual(calls, ["abcdefgh-preview-token"])

    def test_refresh_preserves_existing_account_controls(self):
        self.db.save_cookie("2218280680651", "old-cookie", "existing remark")
        self.db.save_cookie_status("2218280680651", False)
        self.db.set_auto_confirm("2218280680651", False)

        async def confirm(_token):
            return {
                "ok": True,
                "account_id": "2218280680651",
                "display_name": "seller name",
                "cookie_string": "unb=2218280680651; cookie2=new-secret; _m_h5_tk=token-secret",
                "cookie_count": 3,
                "session_ref": "sessionref12",
                "probe": "passport_has_login",
                "observed_at": 1000,
            }

        self.adapter.confirm_current_account_import = confirm
        response = self.client.post(
            "/accounts/browser-import/commit",
            json={
                "preview_token": "refresh1-preview-token",
                "confirmation": "BIND REFRESH1",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["operation"], "refresh")
        self.assertFalse(response.json()["enabled"])
        self.assertEqual((self.db.get_cookie_details("2218280680651") or {})["remark"], "existing remark")
        self.assertFalse(self.db.get_auto_confirm("2218280680651"))
        self.assertFalse(self.db.get_all_cookie_status()["2218280680651"])

    def test_commit_rejects_a_duplicate_request_while_first_commit_is_running(self):
        token = "inflight-preview-token"
        self.server.browser_account_import_inflight.add(token)
        response = self.client.post(
            "/accounts/browser-import/commit",
            json={"preview_token": token, "confirmation": "BIND INFLIGHT"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error"], "account_import_commit_in_progress")


if __name__ == "__main__":
    unittest.main()
