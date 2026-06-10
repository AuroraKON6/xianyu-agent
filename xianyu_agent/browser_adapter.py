"""Playwright-based Xianyu adapter - intercepts internal API responses.

Uses the same technique as product_discovery.py:
Navigate to Xianyu pages → intercept h5api.m.goofish.com responses →
get structured JSON data instead of scraping fragile HTML.

This is the PRIMARY interface for all Xianyu operations.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus

from .models import ListingDraft, Order, PublishResult

logger = logging.getLogger(__name__)

# ============================================================
# Xianyu internal API patterns (for response interception)
# ============================================================
API = {
    # 搜索 (verified)
    "search":        "mtop.taobao.idlemtopsearch.pc.search",
    # 我的商品 (verified - on /personal page)
    "my_items":      "mtop.idle.web.xyh.item.list",
    "user_page_head":"mtop.idle.web.user.page.head",
    # 订单 (verified - on /bought page)
    "order_list":    "mtop.idle.web.trade.bought.list",
    # 消息 (verified - on /im page)
    "msg_session":   "mtop.taobao.idlemessage.pc.session.sync",
    "msg_loginuser": "mtop.taobao.idlemessage.pc.loginuser.get",
    "msg_userquery": "mtop.taobao.idlemessage.pc.user.query",
    # 未验证 (need to find correct patterns)
    "item_detail":   "mtop.taobao.idle.pc.detail",
    "item_publish":  "mtop.taobao.idle.pc.publish",
    "msg_list":      "mtop.taobao.idlemessage.pc.message.list",
    "msg_send":      "mtop.taobao.idlemessage.pc.message.send",
}

# Page URLs (verified working paths on goofish.com)
URL = {
    "home":      "https://www.goofish.com",
    "login":     "https://www.goofish.com/login",
    "publish":   "https://www.goofish.com/publish",
    "orders":    "https://www.goofish.com/bought",
    "messages":  "https://www.goofish.com/im",
    "my":        "https://www.goofish.com/personal",
    "item":      "https://www.goofish.com/item?id={id}",
    "search":    "https://www.goofish.com/search?q={q}",
}

PUBLISH_BLOCKERS: List = []


class BrowserAdapter:
    """Playwright adapter that intercepts Xianyu internal API responses.

    Instead of scraping HTML (fragile), we intercept the structured JSON
    that Xianyu's frontend receives from its own API.
    """

    name = "browser"

    def __init__(self, settings: Any, storage: Any, browser_pool: Any = None) -> None:
        self.settings = settings
        self.storage = storage
        self._pool = browser_pool
        self._cookie_file = Path(settings.data_dir) / "browser_cookies.json"
        self._login_state_file = Path(settings.data_dir) / "browser_login.json"
        # Persistent session
        self._persistent_pw = None
        self._persistent_browser = None
        self._persistent_context = None
        self._persistent_page = None
        # Sync ListingAdapter contract methods run browser coroutines on one
        # dedicated loop so Playwright state is not split across transient loops.
        self._sync_loop: Optional[asyncio.AbstractEventLoop] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._sync_lock = threading.Lock()

    # ============================================================
    # ListingAdapter sync contract
    # ============================================================

    def _ensure_sync_loop(self) -> asyncio.AbstractEventLoop:
        with self._sync_lock:
            if self._sync_loop and self._sync_loop.is_running():
                return self._sync_loop

            ready = threading.Event()

            def run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._sync_loop = loop
                ready.set()
                loop.run_forever()

            self._sync_thread = threading.Thread(
                target=run_loop,
                name="xianyu-browser-adapter-loop",
                daemon=True,
            )
            self._sync_thread.start()
            ready.wait(timeout=10)
            if not self._sync_loop:
                raise RuntimeError("Browser adapter event loop did not start")
            return self._sync_loop

    def _run_sync(self, async_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self._sync_thread and threading.current_thread() is self._sync_thread:
            raise RuntimeError("Cannot call BrowserAdapter sync wrapper from its own event loop thread")
        loop = self._ensure_sync_loop()
        future = asyncio.run_coroutine_threadsafe(async_fn(*args, **kwargs), loop)
        return future.result()

    def publish(self, draft: ListingDraft) -> PublishResult:
        return self._run_sync(self.publish_listing, draft)

    def polish(self, external_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        return self._run_sync(self.polish_listing, external_id, record)

    def fulfill(self, order: Order) -> Dict[str, Any]:
        return self._run_sync(self.fulfill_order, order)

    # ============================================================
    # Browser lifecycle
    # ============================================================

    async def _ensure_session(self, cookie_id: str = "default") -> Optional[Any]:
        """Get or create a persistent browser session.

        Two modes:
        1. CDP mode (recommended): Connect to a running Chrome/Edge via CDP.
           No automation detection. User logs in manually.
           Set BROWSER_CDP_URL=http://127.0.0.1:9222 in .env
        2. Launch mode: Launch Playwright Chromium with stealth patches.
           May still trigger captcha on sensitive pages.
        """
        if self._persistent_page and not self._persistent_page.is_closed():
            return self._persistent_page

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed")
            return None

        self._persistent_pw = await async_playwright().start()

        # Mode 1: CDP connection to existing browser
        cdp_url = getattr(self.settings, 'browser_cdp_url', '') or ""
        if cdp_url:
            try:
                self._persistent_browser = await self._persistent_pw.chromium.connect_over_cdp(cdp_url)
                # Use the first page or create a new one
                contexts = self._persistent_browser.contexts
                if contexts:
                    self._persistent_context = contexts[0]
                    pages = self._persistent_context.pages
                    if pages:
                        self._persistent_page = pages[0]
                    else:
                        self._persistent_page = await self._persistent_context.new_page()
                else:
                    self._persistent_context = await self._persistent_browser.new_context()
                    self._persistent_page = await self._persistent_context.new_page()
                cookies_str = self._load_cookies(cookie_id)
                if cookies_str:
                    await self._set_cookies(cookies_str)
                logger.info(f"Connected to browser via CDP: {cdp_url}")
                return self._persistent_page
            except Exception as e:
                logger.warning(f"CDP connection failed ({cdp_url}): {e}, falling back to launch mode")

        # Mode 2: Launch Playwright with stealth patches
        self._persistent_browser = await self._persistent_pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        self._persistent_context = await self._persistent_browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        )

        # Apply stealth patches to hide automation fingerprint
        try:
            from playwright_stealth import stealth_async
            self._persistent_page = await self._persistent_context.new_page()
            await stealth_async(self._persistent_page)
            logger.info("Stealth patches applied")
        except ImportError:
            self._persistent_page = await self._persistent_context.new_page()
            logger.warning("playwright-stealth not installed, using plain Playwright")
        except Exception as e:
            self._persistent_page = await self._persistent_context.new_page()
            logger.warning(f"Stealth patch failed: {e}")

        # Load cookies
        cookies_str = self._load_cookies(cookie_id)
        if cookies_str:
            await self._set_cookies(cookies_str)

        logger.info("Browser session created (launch mode)")
        return self._persistent_page

    async def _set_cookies(self, cookie_string: str) -> None:
        """Parse cookie string and set in context."""
        cookies = []
        for pair in cookie_string.split("; "):
            if "=" in pair:
                name, value = pair.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".goofish.com",
                    "path": "/",
                })
        if cookies:
            await self._persistent_context.add_cookies(cookies)
            logger.info(f"Set {len(cookies)} cookies")

    async def close(self) -> None:
        """Close persistent session."""
        try:
            if self._persistent_page:
                await self._persistent_page.close()
            if self._persistent_context:
                await self._persistent_context.close()
            if self._persistent_browser:
                await self._persistent_browser.close()
            if self._persistent_pw:
                await self._persistent_pw.stop()
        except Exception:
            pass
        self._persistent_pw = None
        self._persistent_browser = None
        self._persistent_context = None
        self._persistent_page = None

    def _load_cookies(self, cookie_id: str = "default") -> str:
        """Load cookies from settings or file."""
        if getattr(self.settings, 'xianyu_cookies', ''):
            return self.settings.xianyu_cookies
        if self._cookie_file.exists():
            try:
                data = json.loads(self._cookie_file.read_text(encoding="utf-8"))
                return data.get(cookie_id, "")
            except Exception:
                pass
        return ""

    def _save_cookies(self, cookie_id: str, cookie_string: str) -> None:
        """Save cookies to file."""
        data = {}
        if self._cookie_file.exists():
            try:
                data = json.loads(self._cookie_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        data[cookie_id] = cookie_string
        self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self._cookie_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ============================================================
    # API interception helper
    # ============================================================

    async def _intercept(self, page, url: str, api_pattern: str,
                        timeout: int = 15) -> Optional[Dict[str, Any]]:
        """Navigate to URL and intercept the matching API response."""
        captured = {}

        async def on_response(response):
            try:
                if api_pattern in response.url:
                    body = await response.json()
                    captured["data"] = body
            except Exception:
                pass

        page.on("response", on_response)
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            # Wait for API response
            for _ in range(timeout * 2):
                if "data" in captured:
                    break
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Navigation to {url} failed: {e}")
        finally:
            page.remove_listener("response", on_response)

        return captured.get("data")

    async def _intercept_multi(self, page, url: str, api_patterns: List[str],
                               timeout: int = 15) -> Dict[str, Any]:
        """Navigate and intercept multiple API patterns."""
        captured = {}

        async def on_response(response):
            try:
                for name, pattern in api_patterns.items():
                    if pattern in response.url:
                        body = await response.json()
                        captured[name] = body
            except Exception:
                pass

        page.on("response", on_response)
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Navigation failed: {e}")
        finally:
            page.remove_listener("response", on_response)

        return captured

    # ============================================================
    # Login
    # ============================================================

    async def login_with_qrcode(self, cookie_id: str = "default") -> Dict[str, Any]:
        """Open visible browser for QR code login."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {"ok": False, "error": "playwright not installed"}

        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        await page.goto(URL["login"], wait_until="networkidle", timeout=30000)
        logger.info("Scan QR code to login...")

        try:
            await page.wait_for_url("**/goofish.com/**", timeout=120000)
            cookies = await context.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if "goofish" in c.get("domain", ""))
            self._save_cookies(cookie_id, cookie_str)

            self._login_state_file.parent.mkdir(parents=True, exist_ok=True)
            self._login_state_file.write_text(json.dumps({
                "cookie_id": cookie_id, "logged_in": True,
                "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }), encoding="utf-8")

            await browser.close()
            await pw.stop()
            # Reset persistent session to use new cookies
            await self.close()
            return {"ok": True, "cookie_id": cookie_id, "message": "Login successful"}
        except Exception as e:
            await browser.close()
            await pw.stop()
            return {"ok": False, "error": f"Login timeout: {e}"}

    async def login_with_cookies(self, cookie_string: str, cookie_id: str = "default") -> Dict[str, Any]:
        """Login using cookie string."""
        self._save_cookies(cookie_id, cookie_string)
        await self.close()  # Reset session
        return {"ok": True, "cookie_id": cookie_id, "message": "Cookies saved, session reset"}

    async def check_login_status(self, cookie_id: str = "default") -> Dict[str, Any]:
        """Check login status by testing search API (user page doesn't trigger mtop calls)."""
        active_page = await self._ensure_session(cookie_id)
        if not active_page:
            return {"ok": False, "logged_in": False, "error": "No browser"}

        page = active_page
        close_after = False
        if self._persistent_context:
            try:
                page = await self._persistent_context.new_page()
                close_after = True
            except Exception:
                page = active_page

        try:
            # Use search API as login check - it returns results only when logged in.
            result = await self._intercept(page, f"{URL['home']}/search?q=test", API["search"], timeout=15)
            if result and result.get("ret", [""])[0].startswith("SUCCESS"):
                return {"ok": True, "logged_in": True}
            return {"ok": True, "logged_in": False}
        finally:
            if close_after:
                try:
                    await page.close()
                except Exception:
                    pass

    def get_login_state(self, cookie_id: str = "default") -> Dict[str, Any]:
        if self._login_state_file.exists():
            try:
                return json.loads(self._login_state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"cookie_id": cookie_id, "logged_in": False}

    # ============================================================
    # Search products (same as product_discovery.py)
    # ============================================================

    async def search_products(self, keyword: str, max_pages: int = 1,
                             min_price: float = 0, max_price: float = 0,
                             cookie_id: str = "default") -> List[Dict[str, Any]]:
        """Search Xianyu, intercept API for structured data."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return []

        all_items = []
        seen_ids = set()

        for page_num in range(1, max_pages + 1):
            url = f"https://www.goofish.com/search?q={quote_plus(keyword)}"
            if min_price > 0:
                url += f"&price_min={int(min_price * 100)}"
            if max_price > 0:
                url += f"&price_max={int(max_price * 100)}"
            if page_num > 1:
                url += f"&page={page_num}"

            result = await self._intercept(page, url, API["search"], timeout=20)
            if result:
                items = result.get("data", {}).get("resultList", [])
                for item in items:
                    product = self._parse_search_item(item)
                    pid = product.get("product_id", "")
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        all_items.append(product)

            await asyncio.sleep(2)

        return all_items

    def _parse_search_item(self, item: Dict) -> Dict[str, Any]:
        """Parse search result item from API response.

        Actual structure: item.data.item.main.{exContent, clickParam.args}
        - exContent: title, area, picUrl, userNickName, itemId, userAvatarUrl
        - clickParam.args: id, price, displayPrice, seller_id
        """
        import re as _re
        main = item.get("data", {}).get("item", {}).get("main", {})
        ex = main.get("exContent", {})
        args = main.get("clickParam", {}).get("args", {})

        title = str(ex.get("title") or "")
        title = _re.sub(r"<[^>]+>", "", title).strip()

        # price: exContent.price is a list, prefer displayPrice from args
        price = str(args.get("displayPrice") or args.get("price") or "")

        return {
            "product_id": str(ex.get("itemId") or args.get("id") or args.get("item_id") or ""),
            "title": title,
            "price": price,
            "location": str(ex.get("area") or ""),
            "seller_name": str(ex.get("userNickName") or ""),
            "seller_id": str(args.get("seller_id") or ""),
            "image_url": str(ex.get("picUrl") or ""),
            "seller_avatar": str(ex.get("userAvatarUrl") or ""),
            "want_count": 0,
            "browse_count": 0,
            "tags": [],
        }

    def _parse_product(self, data: Dict) -> Dict[str, Any]:
        """Parse product data from flat API response (used by my_listings, item_detail)."""
        import re as _re
        title = str(data.get("title") or data.get("name") or "")
        title = _re.sub(r"<[^>]+>", "", title).strip()
        return {
            "product_id": str(data.get("id") or data.get("itemId") or ""),
            "title": title,
            "price": str(data.get("price") or data.get("soldPrice") or ""),
            "location": str(data.get("area") or data.get("city") or ""),
            "seller_name": str(data.get("sellerNick") or data.get("userName") or ""),
            "seller_id": str(data.get("sellerId") or data.get("userId") or ""),
            "image_url": str(data.get("picUrl") or data.get("imageUrl") or ""),
            "want_count": data.get("wantCount", 0),
            "browse_count": data.get("browseCount", 0),
            "tags": [str(t) for t in (data.get("tags") or data.get("tagList") or [])[:10]],
        }

    # ============================================================
    # My listings
    # ============================================================

    async def get_my_listings(self, cookie_id: str = "default") -> List[Dict[str, Any]]:
        """Get my published listings via API interception.

        API: mtop.idle.web.xyh.item.list on /personal page
        Response: data.itemTopicList[] with item data
        """
        page = await self._ensure_session(cookie_id)
        if not page:
            return []

        result = await self._intercept(page, URL["my"], API["my_items"])
        if result:
            items = result.get("data", {}).get("itemTopicList", [])
            return [self._parse_my_item(item) for item in items]
        return []

    def _parse_my_item(self, item: Dict) -> Dict[str, Any]:
        """Parse my item from xyh.item.list API response."""
        import re as _re
        # Structure varies; try common field names
        title = str(item.get("title") or item.get("name") or "")
        title = _re.sub(r"<[^>]+>", "", title).strip()
        return {
            "product_id": str(item.get("itemId") or item.get("id") or ""),
            "title": title,
            "price": str(item.get("price") or item.get("soldPrice") or ""),
            "location": str(item.get("area") or item.get("city") or ""),
            "image_url": str(item.get("picUrl") or item.get("imageUrl") or item.get("mainPic") or ""),
            "status": str(item.get("status") or item.get("itemStatus") or ""),
            "raw": item,
        }

    # ============================================================
    # Publish listing
    # ============================================================

    def _extract_item_id_from_url(self, url: str) -> str:
        m = re.search(r'(?:[?&](?:id|itemId)=|/item/)([A-Za-z0-9_-]+)', url or "")
        return m.group(1) if m else ""

    async def _get_body_text(self, page: Any) -> str:
        try:
            return await page.locator("body").first.inner_text(timeout=3000)
        except Exception:
            return ""

    async def _collect_publish_blockers(self, page: Any) -> List[Dict[str, str]]:
        text = await self._get_body_text(page)
        blockers = []
        for code, pattern in PUBLISH_BLOCKERS:
            if pattern in text and not any(item["code"] == code for item in blockers):
                blockers.append({"code": code, "text": pattern})
        return blockers

    def _format_publish_blockers(self, blockers: List[Dict[str, str]]) -> str:
        codes = {item.get("code") for item in blockers}
        if "manual_verification_required" in codes:
            return "Manual verification required; complete it in the browser or phone, then call /browser/publish/confirm"
        if "unsupported_web_category" in codes:
            return "Selected category is not supported by the web publisher; use the app or change category"
        if "missing_condition" in codes:
            return "Publish form requires condition selection"
        if "missing_image" in codes:
            return "Publish form requires a cover image"
        return "Publish not confirmed; still on publish page"

    async def _publish_result_from_current_page(self, page: Any, draft: ListingDraft) -> Optional[PublishResult]:
        current_url = getattr(page, "url", "") or ""
        current_url_lower = current_url.lower()
        if "login" in current_url_lower:
            return PublishResult(ok=False, adapter=self.name, listing_id=draft.id, message="Not logged in")

        external_id = self._extract_item_id_from_url(current_url)
        if external_id:
            return PublishResult(
                ok=True,
                adapter=self.name,
                listing_id=draft.id,
                external_id=external_id,
                message=f"Published: {draft.title}",
                raw={"current_url": current_url},
            )

        blockers = await self._collect_publish_blockers(page)
        if blockers:
            return PublishResult(
                ok=False,
                adapter=self.name,
                listing_id=draft.id,
                message=self._format_publish_blockers(blockers),
                raw={"current_url": current_url, "blockers": blockers},
            )
        return None

    async def confirm_current_publish(self, cookie_id: str = "default") -> Dict[str, Any]:
        """Confirm whether the current browser page is a published item page."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return {"ok": False, "error": "No browser"}

        current_url = getattr(page, "url", "") or ""
        external_id = self._extract_item_id_from_url(current_url)
        if external_id:
            return {
                "ok": True,
                "external_id": external_id,
                "url": current_url,
                "message": f"Published item detected: {external_id}",
            }

        blockers = await self._collect_publish_blockers(page)
        return {
            "ok": False,
            "url": current_url,
            "blockers": blockers,
            "message": self._format_publish_blockers(blockers) if blockers else "No published item detected on current page",
        }

    async def publish_listing(self, draft: ListingDraft, cookie_id: str = "default") -> PublishResult:
        """Publish listing by filling the browser form.

        Form structure (verified):
        - <INPUT> type=file - image upload
        - <DIV> contenteditable - description (title + description combined)
        - <INPUT> placeholder=0.00 - price
        - <INPUT> placeholder=搜索地点 - location
        - Radio buttons for shipping options
        """
        page = await self._ensure_session(cookie_id)
        if not page:
            return PublishResult(ok=False, adapter=self.name, listing_id=draft.id, message="No browser")

        try:
            await page.goto(URL["publish"], wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            if "login" in page.url.lower():
                return PublishResult(ok=False, adapter=self.name, listing_id=draft.id, message="Not logged in")

            # Fill description (contenteditable div - title + description combined)
            desc_text = draft.title
            if draft.description or draft.raw_text:
                desc_text += "\n" + (draft.description or draft.raw_text)
            desc_el = page.locator('div[contenteditable="true"]').first
            if await desc_el.count() > 0:
                await desc_el.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(desc_text[:1500], delay=10)
                await asyncio.sleep(1)

            # Fill price (first input with placeholder=0.00)
            price_el = page.locator('input[placeholder="0.00"]').first
            if await price_el.count() > 0 and draft.price:
                await price_el.click()
                await price_el.fill(str(draft.price))
                await asyncio.sleep(0.5)

            # Fill location
            if draft.location:
                loc_el = page.locator('input[placeholder="搜索地点"]').first
                if await loc_el.count() > 0:
                    await loc_el.click()
                    await loc_el.fill(draft.location)
                    await asyncio.sleep(2)
                    # Select first suggestion
                    suggestion = page.locator("[class*='suggestion'] li, [class*='option']").first
                    if await suggestion.count() > 0:
                        await suggestion.click()
                        await asyncio.sleep(0.5)

            # Upload images
            if draft.selected_images:
                file_el = page.locator('input[type="file"]').first
                if await file_el.count() > 0:
                    for img in draft.selected_images[:9]:
                        if Path(img).exists():
                            await file_el.set_input_files(img)
                            await asyncio.sleep(1)

            pre_submit_result = await self._publish_result_from_current_page(page, draft)
            if pre_submit_result and not pre_submit_result.ok:
                codes = {item.get("code") for item in pre_submit_result.raw.get("blockers", [])}
                if "unsupported_web_category" in codes:
                    return pre_submit_result

            # Submit
            btn = page.locator('button:has-text("发布"), [class*="submit"]').first
            if await btn.count() > 0:
                await btn.click()
                timeout_seconds = int(getattr(self.settings, "browser_publish_confirm_timeout", 12) or 12)
                for _ in range(max(1, timeout_seconds)):
                    await asyncio.sleep(1)
                    result = await self._publish_result_from_current_page(page, draft)
                    if result:
                        return result

                current_url = getattr(page, "url", "") or ""
                if "/publish" in current_url.lower():
                    blockers = await self._collect_publish_blockers(page)
                    return PublishResult(
                        ok=False,
                        adapter=self.name,
                        listing_id=draft.id,
                        message=self._format_publish_blockers(blockers),
                        raw={"current_url": current_url, "blockers": blockers},
                    )

                return PublishResult(
                    ok=False,
                    adapter=self.name,
                    listing_id=draft.id,
                    message="Publish not confirmed; item id was not detected",
                    raw={"current_url": current_url},
                )

            return PublishResult(ok=False, adapter=self.name, listing_id=draft.id, message="Publish failed")
        except Exception as e:
            return PublishResult(ok=False, adapter=self.name, listing_id=draft.id, message=str(e))

    # ============================================================
    # Polish listing
    # ============================================================

    async def polish_listing(self, external_id: str, record: Dict[str, Any],
                            cookie_id: str = "default") -> Dict[str, Any]:
        """Edit an existing listing."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return {"ok": False, "error": "No browser"}

        try:
            url = URL["item"].format(id=external_id)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            edit_btn = page.locator('button:has-text("编辑"), a:has-text("编辑")').first
            if await edit_btn.count() > 0:
                await edit_btn.click()
                await asyncio.sleep(2)

                if "title" in record:
                    el = page.locator('input[placeholder*="标题"]').first
                    if await el.count() > 0:
                        await el.fill(record["title"])
                if "price" in record:
                    el = page.locator('input[placeholder*="价格"]').first
                    if await el.count() > 0:
                        await el.fill(str(record["price"]))

                save = page.locator('button:has-text("保存"), button:has-text("确认")').first
                if await save.count() > 0:
                    await save.click()
                    await asyncio.sleep(2)
                    return {"ok": True, "external_id": external_id}

            return {"ok": False, "message": "Edit button not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ============================================================
    # Orders
    # ============================================================

    async def get_orders(self, cookie_id: str = "default", status: str = "") -> List[Dict[str, Any]]:
        """Get orders via API interception.

        API: mtop.idle.web.trade.bought.list on /bought page
        Response: data.items[] with order data
        """
        page = await self._ensure_session(cookie_id)
        if not page:
            return []

        url = URL["orders"]
        if status:
            url += f"?status={status}"

        result = await self._intercept(page, url, API["order_list"])
        if result:
            orders = result.get("data", {}).get("items", [])
            return [self._parse_order(o) for o in orders]
        return []

    async def get_order_detail(self, order_id: str, cookie_id: str = "default") -> Optional[Dict[str, Any]]:
        """Get order detail via API interception."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return None

        url = f"{URL['orders']}?orderId={order_id}"
        result = await self._intercept(page, url, API["order_detail"])
        if result:
            return result.get("data", {})
        return None

    def _parse_order(self, data: Dict) -> Dict[str, Any]:
        """Parse order from trade.bought.list API response.

        Structure: {commonData: {orderId, itemId, tradeStatusEnum, seller},
                    content: {data: {detailInfo: {auctionTitle, auctionPic}, priceInfo: {price}}},
                    head: {data: {createTime, statusViewMsg, userInfo: {userNick, userId}}}}
        """
        common = data.get("commonData", {})
        content = data.get("content", {}).get("data", {})
        detail = content.get("detailInfo", {})
        price_info = content.get("priceInfo", {})
        head_data = data.get("head", {}).get("data", {})
        user = head_data.get("userInfo", {})

        return {
            "order_id": str(common.get("orderId") or ""),
            "item_id": str(common.get("itemId") or ""),
            "title": str(detail.get("auctionTitle") or ""),
            "price": str(price_info.get("price") or ""),
            "status": str(common.get("tradeStatusEnum") or ""),
            "status_msg": str(head_data.get("statusViewMsg") or ""),
            "buyer_name": str(user.get("userNick") or ""),
            "buyer_id": str(user.get("userId") or ""),
            "created_at": str(head_data.get("createTime") or ""),
            "image_url": str(detail.get("auctionPic") or ""),
            "is_seller": common.get("seller") == "true",
            "raw": data,
        }

    async def fulfill_order(self, order: Order, cookie_id: str = "default") -> Dict[str, Any]:
        """Ship order by filling tracking info in browser."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return {"ok": False, "error": "No browser"}

        try:
            url = f"{URL['orders']}?orderId={order.order_id}"
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            ship_btn = page.locator('button:has-text("发货")').first
            if await ship_btn.count() > 0:
                await ship_btn.click()
                await asyncio.sleep(2)

                if order.delivery_payload:
                    tracking = page.locator('input[placeholder*="快递"], input[placeholder*="单号"]').first
                    if await tracking.count() > 0:
                        await tracking.fill(order.delivery_payload)

                submit = page.locator('button:has-text("确认发货"), button:has-text("提交")').first
                if await submit.count() > 0:
                    await submit.click()
                    await asyncio.sleep(2)
                    return {"ok": True, "order_id": order.order_id}

            return {"ok": False, "message": "Ship button not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ============================================================
    # Messages
    # ============================================================

    async def get_conversations(self, cookie_id: str = "default") -> List[Dict[str, Any]]:
        """Get message conversation list via API interception.

        Actual API: mtop.taobao.idlemessage.pc.session.sync
        Response: data.sessions[] = {session: {sessionId, userInfo: {nick, userId, logo}}, message: {summary: {summary, ts, unread}}}
        """
        page = await self._ensure_session(cookie_id)
        if not page:
            return []

        result = await self._intercept(page, URL["messages"], API["msg_session"])
        if result:
            sessions = result.get("data", {}).get("sessions", [])
            out = []
            for s in sessions:
                sess = s.get("session", {})
                user = sess.get("userInfo", {})
                msg = s.get("message", {}).get("summary", {})
                out.append({
                    "session_id": str(sess.get("sessionId") or ""),
                    "buyer_id": str(user.get("userId") or ""),
                    "buyer_name": str(user.get("nick") or ""),
                    "buyer_avatar": str(user.get("logo") or ""),
                    "last_message": str(msg.get("summary") or ""),
                    "last_time": str(msg.get("ts") or ""),
                    "unread": msg.get("unread", 0),
                })
            return out
        return []

    async def get_messages(self, buyer_id: str = "", cookie_id: str = "default") -> List[Dict[str, Any]]:
        """Get messages with a specific buyer via API interception."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return []

        url = URL["messages"]
        if buyer_id:
            url += f"?buyer={buyer_id}"

        result = await self._intercept(page, url, API["msg_list"])
        if result:
            msgs = result.get("data", {}).get("messageList", [])
            return [{
                "message_id": str(m.get("messageId") or ""),
                "sender": str(m.get("senderNick") or m.get("senderId") or ""),
                "content": str(m.get("content") or m.get("text") or ""),
                "time": str(m.get("createTime") or m.get("time") or ""),
                "is_self": m.get("isSelf", False),
                "msg_type": str(m.get("msgType") or "text"),
            } for m in msgs]
        return []

    async def send_message(self, buyer_id: str, message: str,
                          cookie_id: str = "default") -> Dict[str, Any]:
        """Send message to buyer via browser."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return {"ok": False, "error": "No browser"}

        try:
            url = f"{URL['messages']}?buyer={buyer_id}"
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Type message
            msg_input = page.locator('textarea[placeholder*="输入"], div[contenteditable="true"]').first
            if await msg_input.count() > 0:
                await msg_input.fill(message)
                await asyncio.sleep(0.5)
                await msg_input.press("Enter")
                await asyncio.sleep(1)
                return {"ok": True, "buyer_id": buyer_id, "message": message}

            return {"ok": False, "error": "Input not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ============================================================
    # Item detail
    # ============================================================

    async def get_item_detail(self, item_id: str, cookie_id: str = "default") -> Optional[Dict[str, Any]]:
        """Get item detail via API interception."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return None

        url = URL["item"].format(id=item_id)
        result = await self._intercept(page, url, API["item_detail"])
        if result:
            return result.get("data", {})
        return None

    # ============================================================
    # Delete item
    # ============================================================

    async def delete_item(self, item_id: str, cookie_id: str = "default") -> Dict[str, Any]:
        """Delete a listing via browser."""
        page = await self._ensure_session(cookie_id)
        if not page:
            return {"ok": False, "error": "No browser"}

        try:
            url = URL["item"].format(id=item_id)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            del_btn = page.locator('button:has-text("删除"), a:has-text("删除")').first
            if await del_btn.count() > 0:
                await del_btn.click()
                await asyncio.sleep(1)

                confirm = page.locator('button:has-text("确认"), button:has-text("确定")').first
                if await confirm.count() > 0:
                    await confirm.click()
                    await asyncio.sleep(2)
                    return {"ok": True, "item_id": item_id}

            return {"ok": False, "message": "Delete button not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
