"""Multi-account cookie manager for xianyu operations.

Manages multiple xianyu accounts with:
- Per-account cookie lifecycle
- Enable/disable accounts
- Auto-confirm delivery settings
- Keyword management per account
- Async task management for WebSocket connections
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .db_manager import db_manager


class CookieManager:
    """Manages multiple xianyu account cookies and their associated tasks."""

    def __init__(self, loop: asyncio.AbstractEventLoop = None):
        self.loop = loop or asyncio.get_event_loop()
        self.cookies: Dict[str, str] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.keywords: Dict[str, List[Tuple[str, str]]] = {}
        self.cookie_status: Dict[str, bool] = {}
        self.auto_confirm_settings: Dict[str, bool] = {}
        self._task_locks: Dict[str, asyncio.Lock] = {}
        self._load_from_db()

    def _load_from_db(self):
        """Load all cookies, keywords, and status from database."""
        try:
            self.cookies = db_manager.get_all_cookies()
            self.keywords = db_manager.get_all_keywords()
            self.cookie_status = db_manager.get_all_cookie_status()
            self.auto_confirm_settings = {}
            for cookie_id in self.cookies:
                if cookie_id not in self.cookie_status:
                    self.cookie_status[cookie_id] = True
                self.auto_confirm_settings[cookie_id] = db_manager.get_auto_confirm(cookie_id)
            logger.info(
                f"Loaded {len(self.cookies)} cookies, {len(self.keywords)} keyword groups, "
                f"{len(self.cookie_status)} status records"
            )
        except Exception as e:
            logger.error(f"Failed to load data from database: {e}")

    def reload_from_db(self):
        """Reload all data from database."""
        logger.info("Reloading data from database...")
        self._load_from_db()
        return True

    async def _run_xianyu(self, cookie_id: str, cookie_value: str):
        """Start XianyuLive instance for a cookie."""
        try:
            from .xianyu_ws import XianyuWebSocket

            logger.info(f"[{cookie_id}] Starting xianyu WebSocket connection...")
            ws = XianyuWebSocket(cookie_value, cookie_id=cookie_id)
            await ws.connect()
        except asyncio.CancelledError:
            logger.info(f"[{cookie_id}] Task cancelled")
        except Exception as e:
            logger.error(f"[{cookie_id}] Task error: {e}")
            import traceback
            logger.error(traceback.format_exc())

    async def _add_cookie_async(self, cookie_id: str, cookie_value: str):
        """Async add cookie and start task."""
        if cookie_id not in self._task_locks:
            self._task_locks[cookie_id] = asyncio.Lock()

        async with self._task_locks[cookie_id]:
            if cookie_id in self.tasks:
                existing = self.tasks[cookie_id]
                if not existing.done():
                    logger.warning(f"[{cookie_id}] Task already running, stopping old task...")
                    existing.cancel()
                    try:
                        await asyncio.wait_for(existing, timeout=10.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                    self.tasks.pop(cookie_id, None)

            self.cookies[cookie_id] = cookie_value
            db_manager.save_cookie(cookie_id, cookie_value)

            task = self.loop.create_task(self._run_xianyu(cookie_id, cookie_value))
            self.tasks[cookie_id] = task
            logger.info(f"[{cookie_id}] Account task started")

    async def _remove_cookie_async(self, cookie_id: str):
        """Async remove cookie and stop task."""
        if cookie_id not in self._task_locks:
            self._task_locks[cookie_id] = asyncio.Lock()

        async with self._task_locks[cookie_id]:
            task = self.tasks.pop(cookie_id, None)
            if task:
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=10.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

            self.cookies.pop(cookie_id, None)
            self.keywords.pop(cookie_id, None)
            self._task_locks.pop(cookie_id, None)
            db_manager.delete_cookie(cookie_id)
            logger.info(f"[{cookie_id}] Account removed")

    def add_cookie(self, cookie_id: str, cookie_value: str, kw_list: Optional[List[Tuple[str, str]]] = None):
        """Add cookie and start task (thread-safe)."""
        if kw_list is not None:
            self.keywords[cookie_id] = kw_list
        else:
            self.keywords.setdefault(cookie_id, [])

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop and current_loop == self.loop:
            return self.loop.create_task(self._add_cookie_async(cookie_id, cookie_value))
        else:
            fut = asyncio.run_coroutine_threadsafe(self._add_cookie_async(cookie_id, cookie_value), self.loop)
            return fut.result()

    def remove_cookie(self, cookie_id: str):
        """Remove cookie and stop task (thread-safe)."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop and current_loop == self.loop:
            return self.loop.create_task(self._remove_cookie_async(cookie_id))
        else:
            fut = asyncio.run_coroutine_threadsafe(self._remove_cookie_async(cookie_id), self.loop)
            return fut.result()

    def update_cookie(self, cookie_id: str, new_value: str, save_to_db: bool = True):
        """Update cookie value and restart task."""
        async def _update():
            if cookie_id not in self._task_locks:
                self._task_locks[cookie_id] = asyncio.Lock()

            async with self._task_locks[cookie_id]:
                original_keywords = self.keywords.get(cookie_id, []).copy()
                original_status = self.cookie_status.get(cookie_id, True)

                task = self.tasks.pop(cookie_id, None)
                if task:
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=10.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass

                self.cookies[cookie_id] = new_value
                if save_to_db:
                    db_manager.update_cookie_value(cookie_id, new_value)

                self.keywords[cookie_id] = original_keywords
                self.cookie_status[cookie_id] = original_status

                task = self.loop.create_task(self._run_xianyu(cookie_id, new_value))
                self.tasks[cookie_id] = task
                logger.info(f"[{cookie_id}] Cookie updated and task restarted")

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop and current_loop == self.loop:
            return self.loop.create_task(_update())
        else:
            fut = asyncio.run_coroutine_threadsafe(_update(), self.loop)
            return fut.result()

    def update_keywords(self, cookie_id: str, kw_list: List[Tuple[str, str]]):
        """Update keywords for a cookie."""
        self.keywords[cookie_id] = kw_list
        db_manager.save_keywords(cookie_id, kw_list)
        logger.info(f"[{cookie_id}] Keywords updated: {len(kw_list)} rules")

    def list_cookies(self) -> List[str]:
        return list(self.cookies.keys())

    def get_keywords(self, cookie_id: str) -> List[Tuple[str, str]]:
        return self.keywords.get(cookie_id, [])

    def update_cookie_status(self, cookie_id: str, enabled: bool):
        """Enable or disable a cookie account."""
        if cookie_id not in self.cookies:
            raise ValueError(f"Cookie {cookie_id} not found")

        old_status = self.cookie_status.get(cookie_id, True)
        self.cookie_status[cookie_id] = enabled
        db_manager.save_cookie_status(cookie_id, enabled)
        logger.info(f"[{cookie_id}] Status: {'enabled' if enabled else 'disabled'}")

        if old_status != enabled:
            if enabled:
                self._start_cookie_task(cookie_id)
            else:
                self._stop_cookie_task(cookie_id)

    def get_cookie_status(self, cookie_id: str) -> bool:
        return self.cookie_status.get(cookie_id, True)

    def get_enabled_cookies(self) -> Dict[str, str]:
        return {
            cid: value
            for cid, value in self.cookies.items()
            if self.cookie_status.get(cid, True)
        }

    def _start_cookie_task(self, cookie_id: str):
        """Start task for a cookie."""
        if cookie_id in self.tasks:
            return

        cookie_value = self.cookies.get(cookie_id)
        if not cookie_value:
            return

        try:
            if self.loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(
                    self._add_cookie_async(cookie_id, cookie_value), self.loop
                )
                fut.result(timeout=5)
            else:
                task = self.loop.create_task(self._run_xianyu(cookie_id, cookie_value))
                self.tasks[cookie_id] = task
            logger.info(f"[{cookie_id}] Task started")
        except Exception as e:
            logger.error(f"[{cookie_id}] Failed to start task: {e}")

    def _stop_cookie_task(self, cookie_id: str):
        """Stop task for a cookie."""
        if cookie_id not in self.tasks:
            return

        async def _stop():
            task = self.tasks[cookie_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self.tasks[cookie_id]

        try:
            if self.loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(_stop(), self.loop)
                fut.result(timeout=10)
            else:
                task = self.tasks[cookie_id]
                if not task.done():
                    task.cancel()
                del self.tasks[cookie_id]
        except Exception as e:
            logger.error(f"[{cookie_id}] Failed to stop task: {e}")

    def update_auto_confirm_setting(self, cookie_id: str, auto_confirm: bool):
        """Update auto-confirm delivery setting."""
        self.auto_confirm_settings[cookie_id] = auto_confirm
        db_manager.set_auto_confirm(cookie_id, auto_confirm)
        logger.info(f"[{cookie_id}] Auto-confirm: {'enabled' if auto_confirm else 'disabled'}")

    def get_auto_confirm_setting(self, cookie_id: str) -> bool:
        return self.auto_confirm_settings.get(cookie_id, True)


# Global singleton (initialized in Start.py)
manager: Optional[CookieManager] = None
