import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from xianyu_agent.browser_adapter import BrowserAdapter
import xianyu_agent.browser_adapter as browser_adapter_module
from xianyu_agent.models import ListingDraft, Order, PublishResult


class BrowserAdapterContractTests(unittest.TestCase):
    def make_adapter(self):
        settings = SimpleNamespace(data_dir=Path("data"), browser_cdp_url="")
        return BrowserAdapter(settings, storage=None, browser_pool=None)

    def test_publish_delegates_to_async_publish_listing(self):
        adapter = self.make_adapter()
        draft = ListingDraft(title="测试商品")

        async def fake_publish_listing(received_draft, cookie_id="default"):
            self.assertIs(received_draft, draft)
            self.assertEqual(cookie_id, "default")
            return PublishResult(ok=True, adapter="browser", listing_id=draft.id, external_id="item-1")

        adapter.publish_listing = fake_publish_listing

        result = adapter.publish(draft)

        self.assertTrue(result.ok)
        self.assertEqual(result.external_id, "item-1")

    def test_polish_and_fulfill_delegate_to_async_methods(self):
        adapter = self.make_adapter()
        order = Order(order_id="order-1", listing_id="item-1", raw={"cookie_id": "seller_b"})

        async def fake_polish_listing(external_id, record, cookie_id="default"):
            return {"ok": True, "external_id": external_id, "record": record}

        async def fake_fulfill_order(received_order, cookie_id="default"):
            self.assertIs(received_order, order)
            self.assertEqual(cookie_id, "seller_b")
            return {"ok": True, "order_id": received_order.order_id}

        adapter.polish_listing = fake_polish_listing
        adapter.fulfill_order = fake_fulfill_order

        self.assertEqual(adapter.polish("item-1", {"title": "旧标题"})["external_id"], "item-1")
        self.assertEqual(adapter.fulfill(order)["order_id"], "order-1")


class BrowserAdapterCdpCookieTests(unittest.IsolatedAsyncioTestCase):
    async def test_cdp_session_applies_saved_cookies_before_returning_page(self):
        settings = SimpleNamespace(data_dir=Path("data"), browser_cdp_url="http://127.0.0.1:9222", xianyu_cookies="")
        adapter = BrowserAdapter(settings, storage=None, browser_pool=None)
        adapter._load_cookies = lambda cookie_id="default": "unb=123; cookie2=abc"

        added_cookies = []

        class FakePage:
            def is_closed(self):
                return False

        class FakeContext:
            def __init__(self):
                self.pages = []

            async def new_page(self):
                page = FakePage()
                self.pages.append(page)
                return page

            async def add_cookies(self, cookies):
                added_cookies.extend(cookies)

        class FakeBrowser:
            def __init__(self):
                self.contexts = [FakeContext()]

        class FakeChromium:
            async def connect_over_cdp(self, url):
                self.url = url
                return FakeBrowser()

        class FakePlaywright:
            def __init__(self):
                self.chromium = FakeChromium()

        class FakeAsyncPlaywrightFactory:
            async def start(self):
                return FakePlaywright()

        old_module = sys.modules.get("playwright.async_api")
        fake_module = types.SimpleNamespace(async_playwright=lambda: FakeAsyncPlaywrightFactory())
        sys.modules["playwright.async_api"] = fake_module
        try:
            page = await adapter._ensure_session("default")
        finally:
            if old_module is None:
                sys.modules.pop("playwright.async_api", None)
            else:
                sys.modules["playwright.async_api"] = old_module

        self.assertIsNotNone(page)
        self.assertEqual([cookie["name"] for cookie in added_cookies], ["unb", "cookie2"])


class BrowserAdapterStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_login_status_uses_temporary_page_without_moving_active_page(self):
        settings = SimpleNamespace(data_dir=Path("data"), browser_cdp_url="", xianyu_cookies="")
        adapter = BrowserAdapter(settings, storage=None, browser_pool=None)

        class FakePage:
            def __init__(self, url):
                self.url = url
                self.closed = False

            async def goto(self, url, wait_until=None, timeout=None):
                self.url = url

            async def close(self):
                self.closed = True

        active_page = FakePage("https://www.goofish.com/publish")
        temp_page = FakePage("about:blank")

        class FakeContext:
            async def new_page(self):
                return temp_page

        async def fake_ensure_session(cookie_id="default"):
            adapter._persistent_context = FakeContext()
            return active_page

        async def fake_intercept(page, url, api_pattern, timeout=15):
            self.assertIs(page, temp_page)
            await page.goto(url)
            return {"ret": ["SUCCESS::OK"]}

        adapter._ensure_session = fake_ensure_session
        adapter._intercept = fake_intercept

        result = await adapter.check_login_status()

        self.assertTrue(result["logged_in"])
        self.assertEqual(active_page.url, "https://www.goofish.com/publish")
        self.assertTrue(temp_page.closed)


class BrowserPublishConfirmTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_current_publish_detects_item_id_from_current_page(self):
        settings = SimpleNamespace(data_dir=Path("data"), browser_cdp_url="", xianyu_cookies="")
        adapter = BrowserAdapter(settings, storage=None, browser_pool=None)

        class FakePage:
            url = "https://www.goofish.com/item?id=1050442563213&spm=a21ybx.publish"

        async def fake_ensure_session(cookie_id="default"):
            return FakePage()

        adapter._ensure_session = fake_ensure_session

        result = await adapter.confirm_current_publish()

        self.assertTrue(result["ok"])
        self.assertEqual(result["external_id"], "1050442563213")

    async def test_confirm_current_publish_reports_manual_verification_prompt(self):
        settings = SimpleNamespace(data_dir=Path("data"), browser_cdp_url="", xianyu_cookies="")
        adapter = BrowserAdapter(settings, storage=None, browser_pool=None)

        class FakeLocator:
            @property
            def first(self):
                return self

            async def inner_text(self, timeout=None):
                return "\u5b8c\u6210\u8ba4\u8bc1\u624d\u53ef\u7ee7\u7eed\u53d1\u5e03\n\u8bf7\u4f7f\u7528\u624b\u673a\u626b\u7801\u8fdb\u884c\u8ba4\u8bc1"

        class FakePage:
            url = "https://www.goofish.com/publish"

            def locator(self, selector):
                return FakeLocator()

        async def fake_ensure_session(cookie_id="default"):
            return FakePage()

        adapter._ensure_session = fake_ensure_session

        result = await adapter.confirm_current_publish()

        self.assertFalse(result["ok"])
        self.assertEqual(result["blockers"][0]["code"], "manual_verification_required")
        self.assertIn("verification", result["message"].lower())


class BrowserPublishResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_listing_fails_when_submit_does_not_leave_publish_page(self):
        settings = SimpleNamespace(data_dir=Path("data"), browser_cdp_url="", xianyu_cookies="")
        adapter = BrowserAdapter(settings, storage=None, browser_pool=None)
        draft = ListingDraft(title="test", price=999999)

        class FakeLocator:
            def __init__(self, count=1):
                self._count = count

            @property
            def first(self):
                return self

            async def count(self):
                return self._count

            async def click(self):
                return None

            async def fill(self, value):
                return None

            async def set_input_files(self, value):
                return None

        class FakeKeyboard:
            def __init__(self):
                self.pressed = []

            async def press(self, key):
                self.pressed.append(key)

            async def type(self, text, delay=0):
                return None

        class FakePage:
            url = "https://www.goofish.com/publish"
            wait_until = None

            def __init__(self):
                self.keyboard = FakeKeyboard()

            async def goto(self, url, wait_until=None, timeout=None):
                self.url = url
                self.wait_until = wait_until

            def locator(self, selector):
                return FakeLocator()

        fake_page = FakePage()

        async def fake_ensure_session(cookie_id="default"):
            return fake_page

        adapter._ensure_session = fake_ensure_session

        old_sleep = browser_adapter_module.asyncio.sleep

        async def fast_sleep(_seconds):
            return None

        browser_adapter_module.asyncio.sleep = fast_sleep
        try:
            result = await adapter.publish_listing(draft)
        finally:
            browser_adapter_module.asyncio.sleep = old_sleep

        self.assertFalse(result.ok)
        self.assertIn("not confirmed", result.message)
        self.assertEqual(fake_page.wait_until, "domcontentloaded")
        self.assertEqual(fake_page.keyboard.pressed[:2], ["Control+A", "Backspace"])


class BrowserPublishRouteTests(unittest.TestCase):
    def test_browser_publish_route_is_registered(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}

        self.assertIn(("/browser/publish", ("POST",)), routes)

    def test_browser_publish_confirm_route_is_registered(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}

        self.assertIn(("/browser/publish/confirm", ("GET",)), routes)


if __name__ == "__main__":
    unittest.main()
