"""Browser instance pool manager using Playwright.

Manages browser instances per cookie_id for xianyu operations:
- Reuses browser instances to reduce startup overhead
- Supports lazy loading and auto-cleanup
- Idle timeout for unused browsers
- Docker-optimized browser args
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger

try:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
except ImportError:
    Browser = None
    BrowserContext = None
    Page = None
    Playwright = None
    async_playwright = None


class BrowserPool:
    """Browser instance pool with per-cookie_id management."""

    def __init__(self, max_size: int = 3, idle_timeout: int = 300):
        self.max_size = max_size
        self.idle_timeout = idle_timeout
        self.pool: Dict[str, Tuple[Any, Browser, BrowserContext, Page, float]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._pool_lock: Optional[asyncio.Lock] = None
        logger.info(f"Browser pool initialized, max_size={max_size}, idle_timeout={idle_timeout}s")

    def _get_cookie_lock(self, cookie_id: str) -> asyncio.Lock:
        lock = self._locks.get(cookie_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cookie_id] = lock
        return lock

    def _get_pool_lock(self) -> asyncio.Lock:
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        return self._pool_lock

    async def get_browser(
        self,
        cookie_id: str,
        cookie_string: str,
        headless: bool = True,
        create_new_page: bool = True,
    ) -> Optional[Tuple[Browser, BrowserContext, Page]]:
        """Get or create a browser instance for a cookie_id."""
        if async_playwright is None:
            logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return None

        async with self._get_cookie_lock(cookie_id):
            async with self._get_pool_lock():
                if cookie_id in self.pool:
                    pw, browser, context, page, _ = self.pool[cookie_id]
                    if browser.is_connected():
                        try:
                            pages = context.pages
                            if not pages:
                                raise Exception("No pages in context")
                            self.pool[cookie_id] = (pw, browser, context, pages[0], time.time())
                            if create_new_page:
                                new_page = await context.new_page()
                                logger.info(f"[{cookie_id}] Reusing browser, new page created")
                                return browser, context, new_page
                            logger.info(f"[{cookie_id}] Reusing browser instance")
                            return browser, context, pages[0]
                        except Exception as e:
                            logger.warning(f"[{cookie_id}] Browser instance unavailable: {e}, recreating")
                            await self._close_browser_unsafe(cookie_id)
                    else:
                        logger.warning(f"[{cookie_id}] Browser disconnected, recreating")
                        await self._close_browser_unsafe(cookie_id)

            await self._ensure_pool_size()
            logger.info(f"[{cookie_id}] Creating new browser instance")
            result = await self._create_browser(cookie_id, cookie_string, headless)

            if result:
                pw, browser, context, page = result
                async with self._get_pool_lock():
                    self.pool[cookie_id] = (pw, browser, context, page, time.time())
                logger.info(f"[{cookie_id}] Browser instance created")
                return browser, context, page

            logger.error(f"[{cookie_id}] Failed to create browser instance")
            return None

    async def _create_browser(
        self, cookie_id: str, cookie_string: str, headless: bool = True
    ) -> Optional[Tuple[Any, Browser, BrowserContext, Page]]:
        """Create a new Playwright browser instance."""
        try:
            pw = await async_playwright().start()

            browser_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=TranslateUI",
                "--disable-ipc-flooding-protection",
                "--disable-extensions",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--hide-scrollbars",
                "--mute-audio",
                "--no-default-browser-check",
                "--no-pings",
            ]

            if os.getenv("DOCKER_ENV"):
                browser_args.extend([
                    "--disable-background-networking",
                    "--disable-client-side-phishing-detection",
                    "--disable-hang-monitor",
                    "--disable-popup-blocking",
                    "--disable-prompt-on-repost",
                    "--metrics-recording-only",
                    "--safebrowsing-disable-auto-update",
                    "--enable-automation",
                    "--password-store=basic",
                    "--use-mock-keychain",
                    "--memory-pressure-off",
                    "--max_old_space_size=512",
                ])

            browser = await pw.chromium.launch(headless=headless, args=browser_args)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            )

            # Set cookies
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
                await context.add_cookies(cookies)
                logger.info(f"[{cookie_id}] Set {len(cookies)} cookies")

            page = await context.new_page()
            await asyncio.sleep(1)
            return pw, browser, context, page

        except Exception as e:
            logger.error(f"[{cookie_id}] Browser creation failed: {e}")
            return None

    async def _ensure_pool_size(self):
        """Ensure pool doesn't exceed max_size by removing oldest instance."""
        async with self._get_pool_lock():
            while len(self.pool) >= self.max_size:
                oldest_id = None
                oldest_time = float("inf")
                for cid, (_, _, _, _, last_used) in self.pool.items():
                    if last_used < oldest_time:
                        oldest_time = last_used
                        oldest_id = cid
                if oldest_id:
                    logger.info(f"Pool full, closing oldest instance: {oldest_id}")
                    await self._close_browser_unsafe(oldest_id)
                else:
                    break

    async def _close_browser_unsafe(self, cookie_id: str):
        """Close browser instance (internal, no lock)."""
        if cookie_id not in self.pool:
            return
        pw, browser, context, page, _ = self.pool[cookie_id]
        try:
            if page:
                await page.close()
            if context:
                await context.close()
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
            logger.info(f"[{cookie_id}] Browser instance closed")
        except Exception as e:
            logger.error(f"[{cookie_id}] Error closing browser: {e}")
        finally:
            del self.pool[cookie_id]

    async def close_browser(self, cookie_id: str):
        """Close a specific browser instance."""
        async with self._get_pool_lock():
            await self._close_browser_unsafe(cookie_id)

    async def cleanup_idle_browsers(self):
        """Close browsers that have been idle too long."""
        current_time = time.time()
        to_close = []
        async with self._get_pool_lock():
            for cid, (_, _, _, _, last_used) in self.pool.items():
                if current_time - last_used > self.idle_timeout:
                    to_close.append(cid)
        for cid in to_close:
            logger.info(f"[{cid}] Closing idle browser (idle {current_time - self.pool[cid][4]:.0f}s)")
            await self.close_browser(cid)

    async def close_all(self):
        """Close all browser instances."""
        async with self._get_pool_lock():
            ids = list(self.pool.keys())
        for cid in ids:
            await self.close_browser(cid)
        logger.info("All browser instances closed")

    def get_pool_status(self) -> Dict[str, Any]:
        """Get pool status info."""
        current_time = time.time()
        return {
            "total": len(self.pool),
            "max_size": self.max_size,
            "instances": [
                {
                    "cookie_id": cid,
                    "connected": browser.is_connected() if browser else False,
                    "idle_time": current_time - last_used,
                    "last_used": last_used,
                }
                for cid, (_, browser, _, _, last_used) in self.pool.items()
            ],
        }


# Global singleton
_global_browser_pool: Optional[BrowserPool] = None


def get_browser_pool(max_size: int = 3, idle_timeout: int = 300) -> BrowserPool:
    """Get or create the global browser pool instance."""
    global _global_browser_pool
    if _global_browser_pool is None:
        _global_browser_pool = BrowserPool(max_size=max_size, idle_timeout=idle_timeout)
    return _global_browser_pool
