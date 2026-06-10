"""Auto-confirm delivery module for xianyu orders.

Handles automatic order confirmation with:
- Token refresh from cookies
- Retry logic for failed confirmations
- Cookie update tracking
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from loguru import logger

try:
    import aiohttp
except ImportError:
    aiohttp = None

from .config import Settings


def _trans_cookies(cookies_str: str) -> Dict[str, str]:
    """Parse cookie string into dict."""
    result = {}
    for pair in cookies_str.split("; "):
        if "=" in pair:
            name, value = pair.split("=", 1)
            result[name.strip()] = value.strip()
    return result


def _generate_sign(t: str, token: str, data: str) -> str:
    """Generate sign for xianyu API request."""
    import hashlib
    raw = f"{token}&{t}&34839810&{data}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class SecureConfirm:
    """Auto-confirm delivery for xianyu orders."""

    def __init__(self, session: Any, cookies_str: str, cookie_id: str, main_instance: Any = None):
        self.session = session
        self.cookies_str = cookies_str
        self.cookie_id = cookie_id
        self.main_instance = main_instance
        self.cookies = _trans_cookies(cookies_str) if cookies_str else {}
        self.current_token = None
        self.last_token_refresh_time = 0
        self.token_refresh_interval = 3600

    async def auto_confirm(self, order_id: str, item_id: str = None, retry_count: int = 0) -> Dict[str, Any]:
        """Auto-confirm delivery for an order."""
        if retry_count >= 4:
            logger.error(f"[{self.cookie_id}] Auto-confirm failed after {retry_count} retries")
            return {"error": "Max retries exceeded"}

        if item_id:
            self.current_token = item_id

        if not self.session:
            return {"error": "Session not created"}

        params = {
            "jsv": "2.7.2",
            "appKey": "34839810",
            "t": str(int(time.time()) * 1000),
            "sign": "",
            "v": "1.0",
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": "mtop.taobao.idle.logistic.consign.dummy",
            "sessionOption": "AutoLoginOnly",
        }

        data_val = f'{{"orderId":"{order_id}", "tradeText":"","picList":[],"newUnconsign":true}}'
        data = {"data": data_val}

        # Get _m_h5_tk token from cookies
        cookies = _trans_cookies(self.cookies_str)
        m_h5_tk = cookies.get("_m_h5_tk", "")
        token = m_h5_tk.split("_")[0] if m_h5_tk else ""

        if token:
            logger.info(f"[{self.cookie_id}] Using _m_h5_tk token: {token}")
        else:
            logger.warning(f"[{self.cookie_id}] No _m_h5_tk token in cookies")

        sign = _generate_sign(params["t"], token, data_val)
        params["sign"] = sign

        try:
            logger.info(f"[{self.cookie_id}] Auto-confirming delivery, order: {order_id}")
            async with self.session.post(
                "https://h5api.m.goofish.com/h5/mtop.taobao.idle.logistic.consign.dummy/1.0/",
                params=params,
                data=data,
            ) as response:
                res_json = await response.json()

                # Update cookies from response
                if "set-cookie" in response.headers:
                    new_cookies = {}
                    for cookie in response.headers.getall("set-cookie", []):
                        if "=" in cookie:
                            name, value = cookie.split(";")[0].split("=", 1)
                            new_cookies[name.strip()] = value.strip()
                    if new_cookies:
                        self.cookies.update(new_cookies)
                        self.cookies_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

                logger.info(f"[{self.cookie_id}] Auto-confirm response: {res_json}")

                if res_json.get("ret") and res_json["ret"][0] == "SUCCESS::调用成功":
                    logger.info(f"[{self.cookie_id}] Auto-confirm success, order: {order_id}")
                    return {"success": True, "order_id": order_id}
                else:
                    error_msg = res_json.get("ret", ["Unknown error"])[0] if res_json.get("ret") else "Unknown error"
                    logger.warning(f"[{self.cookie_id}] Auto-confirm failed: {error_msg}")
                    return await self.auto_confirm(order_id, item_id, retry_count + 1)

        except Exception as e:
            logger.error(f"[{self.cookie_id}] Auto-confirm error: {e}")
            await asyncio.sleep(0.5)
            if retry_count < 2:
                logger.info(f"[{self.cookie_id}] Retrying after network error...")
                return await self.auto_confirm(order_id, item_id, retry_count + 1)
            return {"error": f"Network error: {e}", "order_id": order_id}
