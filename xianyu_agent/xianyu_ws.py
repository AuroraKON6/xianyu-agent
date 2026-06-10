"""Xianyu WebSocket IM connection layer.

Enhanced version with features from xianyu-super-butler:
- Connection state management with reconnection logic
- Auto-reply pause manager (pauses when seller sends manually)
- Message debouncing (waits for user to finish sending multiple messages)
- Message deduplication (prevents processing same message twice)
- Order status handler integration
- Multi-account support (cookie_id parameter)
- Exponential backoff reconnection
- Token refresh via API
- Item detail caching
- Background task management
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
from collections import defaultdict
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

try:
    from .xianyu_crypto import decrypt, generate_mid, generate_uuid, generate_device_id, generate_sign, trans_cookies
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


class ConnectionState(Enum):
    """WebSocket connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    CLOSED = "closed"


class AutoReplyPauseManager:
    """Manages auto-reply pause state per chat.

    When the seller sends a manual message, auto-reply is paused for that chat
    for a configurable duration.
    """

    def __init__(self):
        self.paused_chats: Dict[str, float] = {}  # {chat_id: pause_until_timestamp}

    def pause_chat(self, chat_id: str, cookie_id: str, pause_minutes: int = 10):
        """Pause auto-reply for a chat."""
        if pause_minutes == 0:
            return
        pause_until = time.time() + (pause_minutes * 60)
        self.paused_chats[chat_id] = pause_until
        end_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pause_until))
        logger.info(f"[{cookie_id}] Auto-reply paused for chat {chat_id} until {end_time}")

    def is_chat_paused(self, chat_id: str) -> bool:
        """Check if a chat is paused."""
        if chat_id not in self.paused_chats:
            return False
        if time.time() >= self.paused_chats[chat_id]:
            del self.paused_chats[chat_id]
            return False
        return True

    def cleanup_expired(self):
        """Remove expired pause records."""
        current_time = time.time()
        expired = [cid for cid, until in self.paused_chats.items() if current_time >= until]
        for cid in expired:
            del self.paused_chats[cid]


class XianyuWebSocket:
    """Xianyu IM WebSocket client with multi-account support.

    Connects to the Xianyu IM service and processes chat messages.
    Integrates with AutoReplyEngine for automatic responses.
    """

    BASE_URL = "wss://wss-goofish.dingtalk.com/"

    # Class-level instance management
    _instances: Dict[str, "XianyuWebSocket"] = {}
    _instances_lock = None

    def __init__(
        self,
        cookies_str: str,
        cookie_id: str = "default",
        settings: Any = None,
        auto_reply_engine: Any = None,
        notifier: Any = None,
        order_handler: Any = None,
    ) -> None:
        self.cookies_str = cookies_str
        self.cookie_id = cookie_id
        self.settings = settings
        self.auto_reply = auto_reply_engine
        self.notifier = notifier
        self.order_handler = order_handler

        # Parse cookies
        if HAS_CRYPTO:
            self.cookies = trans_cookies(cookies_str)
            self.myid = self.cookies.get("unb", "")
            self.device_id = generate_device_id(self.myid)
        else:
            self.cookies = {}
            self.myid = ""
            self.device_id = ""

        # Connection state
        self.ws: Any = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.connection_failures = 0
        self.max_connection_failures = 5
        self.last_successful_connection = 0.0

        # Token
        self.current_token: Optional[str] = None
        self.last_token_refresh_time = 0.0
        self.token_refresh_interval = 3600

        # Heartbeat
        self.heartbeat_interval = 15
        self.heartbeat_timeout = 5
        self.last_heartbeat_time = 0.0
        self.last_heartbeat_response = 0.0
        self.heartbeat_task: Optional[asyncio.Task] = None

        # Auto-reply pause manager
        self.pause_manager = AutoReplyPauseManager()

        # Message deduplication
        self.processed_message_ids: Dict[str, float] = {}
        self.message_expire_time = 3600  # 1 hour

        # Message debouncing
        self.message_debounce_tasks: Dict[str, asyncio.Task] = {}
        self.message_debounce_delay = 1.0  # seconds

        # Manual mode (human takeover)
        self.manual_mode_conversations: Set[str] = set()
        self.manual_mode_timeout = 3600
        self.manual_mode_timestamps: Dict[str, float] = {}

        # Item detail cache
        self._item_detail_cache: Dict[str, Dict[str, Any]] = {}
        self._item_detail_cache_max_size = 1000
        self._item_detail_cache_ttl = 86400  # 24 hours

        # Background tasks
        self.background_tasks: Set[asyncio.Task] = set()
        self._running = False

        # Callbacks
        self._on_message_callback: Optional[Callable] = None

    def on_message(self, callback: Callable) -> None:
        """Register a callback for incoming messages."""
        self._on_message_callback = callback

    def _set_connection_state(self, new_state: ConnectionState, reason: str = ""):
        """Update connection state with logging."""
        if self.connection_state != new_state:
            old_state = self.connection_state
            self.connection_state = new_state
            state_msg = f"[{self.cookie_id}] Connection: {old_state.value} -> {new_state.value}"
            if reason:
                state_msg += f" ({reason})"
            if new_state == ConnectionState.FAILED:
                logger.error(state_msg)
            elif new_state == ConnectionState.RECONNECTING:
                logger.warning(state_msg)
            elif new_state == ConnectionState.CONNECTED:
                logger.info(state_msg)
            else:
                logger.info(state_msg)

    def _calculate_retry_delay(self, error_msg: str) -> int:
        """Calculate retry delay based on error type and failure count."""
        if "no close frame" in error_msg:
            return min(3 * self.connection_failures, 15)
        elif "Connection refused" in error_msg or "timeout" in error_msg.lower():
            return min(10 * self.connection_failures, 60)
        else:
            return min(5 * self.connection_failures, 30)

    def _create_tracked_task(self, coro: Any) -> asyncio.Task:
        """Create and track a background task."""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def start(self) -> None:
        """Start the WebSocket connection loop."""
        if not HAS_WEBSOCKETS:
            logger.error("websockets not installed. Run: pip install websockets")
            return
        if not HAS_CRYPTO:
            logger.error("xianyu_crypto not available")
            return
        if not self.cookies_str:
            logger.error("No cookies set")
            return

        self._running = True
        self._set_connection_state(ConnectionState.CONNECTING)

        # Register instance
        XianyuWebSocket._instances[self.cookie_id] = self

        while self._running:
            try:
                self.connection_restart_flag = False
                headers = {
                    "Cookie": self.cookies_str,
                    "Host": "wss-goofish.dingtalk.com",
                    "Connection": "Upgrade",
                    "Pragma": "no-cache",
                    "Cache-Control": "no-cache",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                    "Origin": "https://www.goofish.com",
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                }

                async with websockets.connect(self.BASE_URL, extra_headers=headers) as ws:
                    self.ws = ws
                    await self._init_connection(ws)
                    self.last_heartbeat_time = time.time()
                    self.last_heartbeat_response = time.time()
                    self._set_connection_state(ConnectionState.CONNECTED)
                    self.connection_failures = 0
                    self.last_successful_connection = time.time()

                    # Start background tasks
                    self.heartbeat_task = self._create_tracked_task(self._heartbeat_loop(ws))
                    token_task = self._create_tracked_task(self._token_refresh_loop())
                    cleanup_task = self._create_tracked_task(self._cleanup_loop())

                    try:
                        async for message in ws:
                            if self.connection_restart_flag:
                                break
                            try:
                                message_data = json.loads(message)
                                if await self._handle_heartbeat_response(message_data):
                                    continue
                                await self._send_ack(ws, message_data)
                                await self._handle_message(message_data, ws)
                            except json.JSONDecodeError:
                                logger.error(f"[{self.cookie_id}] Message parse failed")
                            except Exception as exc:
                                logger.error(f"[{self.cookie_id}] Message handling error: {exc}")
                    finally:
                        if self.heartbeat_task:
                            self.heartbeat_task.cancel()
                        token_task.cancel()
                        cleanup_task.cancel()

            except Exception as exc:
                self.connection_failures += 1
                error_msg = str(exc)
                self._set_connection_state(ConnectionState.RECONNECTING, error_msg)

            if self._running:
                if self.connection_restart_flag:
                    logger.info(f"[{self.cookie_id}] Restarting connection immediately...")
                else:
                    delay = self._calculate_retry_delay(error_msg if 'error_msg' in dir() else "")
                    logger.info(f"[{self.cookie_id}] Reconnecting in {delay}s...")
                    await asyncio.sleep(delay)

    async def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False
        self._set_connection_state(ConnectionState.CLOSED)

        # Cancel all background tasks
        for task in self.background_tasks:
            if not task.done():
                task.cancel()

        if self.ws:
            await self.ws.close()

        # Unregister instance
        if self.cookie_id in XianyuWebSocket._instances:
            del XianyuWebSocket._instances[self.cookie_id]

    async def send_message(self, ws: Any, cid: str, toid: str, text: str) -> None:
        """Send a chat message."""
        text_payload = {
            "contentType": 1,
            "text": {"text": text}
        }
        text_b64 = base64.b64encode(json.dumps(text_payload).encode("utf-8")).decode("utf-8")
        msg = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {"mid": generate_mid()},
            "body": [
                {
                    "uuid": generate_uuid(),
                    "cid": f"{cid}@goofish",
                    "conversationType": 1,
                    "content": {
                        "contentType": 101,
                        "custom": {"type": 1, "data": text_b64}
                    },
                    "redPointPolicy": 0,
                    "extension": {"extJson": "{}"},
                    "ctx": {"appVersion": "1.0", "platform": "web"},
                    "mtags": {},
                    "msgReadStatusSetting": 1
                },
                {
                    "actualReceivers": [f"{toid}@goofish", f"{self.myid}@goofish"]
                }
            ]
        }
        await ws.send(json.dumps(msg))

    async def _init_connection(self, ws: Any) -> None:
        """Initialize the WebSocket connection with registration."""
        if not self.current_token or (time.time() - self.last_token_refresh_time) >= self.token_refresh_interval:
            await self._refresh_token()

        if not self.current_token:
            raise Exception("Token acquisition failed")

        msg = {
            "lwp": "/reg",
            "headers": {
                "cache-header": "app-key token ua wv",
                "app-key": "444e9908a51d1cb236a27862abc769c9",
                "token": self.current_token,
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 DingTalk(2.1.5) OS(Windows/10) Browser(Chrome/133.0.0.0) DingWeb/2.1.5 IMPaaS DingWeb/2.1.5",
                "dt": "j",
                "wv": "im:3,au:3,sy:6",
                "sync": "0,0;0;0;",
                "did": self.device_id,
                "mid": generate_mid()
            }
        }
        await ws.send(json.dumps(msg))
        await asyncio.sleep(1)

        sync_msg = {
            "lwp": "/r/SyncStatus/ackDiff",
            "headers": {"mid": "5701741704675979 0"},
            "body": [{
                "pipeline": "sync",
                "tooLong2Tag": "PNM,1",
                "channel": "sync",
                "topic": "sync",
                "highPts": 0,
                "pts": int(time.time() * 1000) * 1000,
                "seq": 0,
                "timestamp": int(time.time() * 1000)
            }]
        }
        await ws.send(json.dumps(sync_msg))
        logger.info(f"[{self.cookie_id}] Connection registered")

    async def _refresh_token(self) -> Optional[str]:
        """Refresh the access token via Xianyu API."""
        try:
            import aiohttp
            cookies = trans_cookies(self.cookies_str) if HAS_CRYPTO else {}
            token = cookies.get("_m_h5_tk", "").split("_")[0] if cookies.get("_m_h5_tk") else ""
            if not token:
                logger.warning(f"[{self.cookie_id}] No _m_h5_tk token in cookies")
                return None

            t = str(int(time.time()) * 1000)
            data_val = "{}"
            sign = generate_sign(t, token, data_val)

            params = {
                "jsv": "2.7.2",
                "appKey": "34839810",
                "t": t,
                "sign": sign,
                "v": "1.0",
                "type": "originaljson",
                "accountSite": "xianyu",
                "dataType": "json",
                "timeout": "20000",
                "api": "mtop.taobao.idle.home.page",
                "data": data_val,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://h5api.m.goofish.com/h5/mtop.taobao.idle.home.page/1.0/",
                    params=params,
                ) as response:
                    res_json = await response.json()
                    if res_json.get("ret") and "SUCCESS" in res_json["ret"][0]:
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
                        logger.info(f"[{self.cookie_id}] Token refreshed successfully")
                        self.last_token_refresh_time = time.time()
                        return token
                    else:
                        logger.warning(f"[{self.cookie_id}] Token refresh failed: {res_json}")
                        return None
        except Exception as exc:
            logger.error(f"[{self.cookie_id}] Token refresh error: {exc}")
            return None

    async def _heartbeat_loop(self, ws: Any) -> None:
        """Send heartbeats periodically."""
        while True:
            try:
                if time.time() - self.last_heartbeat_time >= self.heartbeat_interval:
                    mid = generate_mid()
                    await ws.send(json.dumps({"lwp": "/!", "headers": {"mid": mid}}))
                    self.last_heartbeat_time = time.time()

                if (time.time() - self.last_heartbeat_response) > (self.heartbeat_interval + self.heartbeat_timeout):
                    logger.warning(f"[{self.cookie_id}] Heartbeat timeout")
                    break

                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[{self.cookie_id}] Heartbeat error: {exc}")
                break

    async def _token_refresh_loop(self) -> None:
        """Periodically refresh the token."""
        while True:
            try:
                if time.time() - self.last_token_refresh_time >= self.token_refresh_interval:
                    new_token = await self._refresh_token()
                    if new_token:
                        self.connection_restart_flag = True
                        if self.ws:
                            await self.ws.close()
                        break
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[{self.cookie_id}] Token refresh loop error: {exc}")
                await asyncio.sleep(60)

    async def _cleanup_loop(self) -> None:
        """Periodically clean up caches and expired records."""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                self._cleanup_processed_messages()
                self.pause_manager.cleanup_expired()
                self._cleanup_item_cache()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[{self.cookie_id}] Cleanup error: {exc}")

    def _cleanup_processed_messages(self):
        """Remove expired processed message IDs."""
        current_time = time.time()
        expired = [mid for mid, ts in self.processed_message_ids.items() if current_time - ts > self.message_expire_time]
        for mid in expired:
            del self.processed_message_ids[mid]

    def _cleanup_item_cache(self):
        """Remove expired item detail cache entries."""
        current_time = time.time()
        expired = [iid for iid, data in self._item_detail_cache.items() if current_time - data.get("timestamp", 0) > self._item_detail_cache_ttl]
        for iid in expired:
            del self._item_detail_cache[iid]
        # Limit cache size
        if len(self._item_detail_cache) > self._item_detail_cache_max_size:
            sorted_items = sorted(self._item_detail_cache.items(), key=lambda x: x[1].get("timestamp", 0))
            for iid, _ in sorted_items[:len(sorted_items) - self._item_detail_cache_max_size]:
                del self._item_detail_cache[iid]

    async def _handle_heartbeat_response(self, data: Dict) -> bool:
        """Check if message is a heartbeat response."""
        if (isinstance(data, dict) and "headers" in data and "mid" in data["headers"]
                and "code" in data and data["code"] == 200):
            self.last_heartbeat_response = time.time()
            return True
        return False

    async def _send_ack(self, ws: Any, message: Dict) -> None:
        """Send ACK for a received message."""
        try:
            if "headers" in message and "mid" in message["headers"]:
                ack = {
                    "code": 200,
                    "headers": {
                        "mid": message["headers"]["mid"],
                        "sid": message["headers"].get("sid", "")
                    }
                }
                for key in ["app-key", "ua", "dt"]:
                    if key in message["headers"]:
                        ack["headers"][key] = message["headers"][key]
                await ws.send(json.dumps(ack))
        except Exception:
            pass

    async def _handle_message(self, data: Dict, ws: Any) -> None:
        """Handle an incoming message."""
        if not self._is_sync_package(data):
            return

        sync_data = data["body"]["syncPushPackage"]["data"][0]
        if "data" not in sync_data:
            return

        # Decrypt
        try:
            raw = sync_data["data"]
            try:
                decoded = base64.b64decode(raw).decode("utf-8")
                parsed = json.loads(decoded)
                return  # Unencrypted, skip
            except Exception:
                decrypted = decrypt(raw)
                message = json.loads(decrypted)
        except Exception as exc:
            logger.error(f"[{self.cookie_id}] Decrypt failed: {exc}")
            return

        # Check for order/system messages
        try:
            red = message.get("3", {}).get("redReminder", "")
            if red in ("等待买家付款", "交易关闭", "等待卖家发货"):
                user_id = message.get("1", "").split("@")[0]
                logger.info(f"[{self.cookie_id}] Order status: {red} (user: {user_id})")
                # Notify order handler
                if self.order_handler:
                    self.order_handler.handle_system_message(self.cookie_id, message)
                return
        except Exception:
            pass

        # Check typing status
        if self._is_typing_status(message):
            return

        # Check chat message
        if not self._is_chat_message(message):
            return

        # Extract message info
        try:
            create_time = int(message["1"]["5"])
            send_user_name = message["1"]["10"]["reminderTitle"]
            send_user_id = message["1"]["10"]["senderUserId"]
            send_message = message["1"]["10"]["reminderContent"]
            url_info = message["1"]["10"]["reminderUrl"]
            item_id = url_info.split("itemId=")[1].split("&")[0] if "itemId=" in url_info else ""
            chat_id = message["1"]["2"].split("@")[0]
        except (KeyError, IndexError) as exc:
            logger.warning(f"[{self.cookie_id}] Failed to extract message fields: {exc}")
            return

        # Filter expired messages
        if (time.time() * 1000 - create_time) > 300000:  # 5 minutes
            return

        # Message deduplication
        message_id = f"{chat_id}:{send_user_id}:{create_time}:{send_message[:50]}"
        if message_id in self.processed_message_ids:
            return
        self.processed_message_ids[message_id] = time.time()

        # Seller's own message - check for toggle command
        if send_user_id == self.myid:
            toggle_keyword = getattr(self.settings, 'auto_reply_human_toggle', '。') if self.settings else '。'
            if send_message.strip() == toggle_keyword:
                self._toggle_manual_mode(chat_id)
            else:
                # Manual message sent - pause auto-reply
                pause_minutes = 10
                if self.settings:
                    try:
                        from .db_manager import db_manager
                        pause_minutes = db_manager.get_pause_duration(self.cookie_id)
                    except Exception:
                        pass
                self.pause_manager.pause_chat(chat_id, self.cookie_id, pause_minutes)
            return

        # Skip if manual mode
        if self._is_manual_mode(chat_id):
            return

        # Skip if chat is paused
        if self.pause_manager.is_chat_paused(chat_id):
            return

        # Skip system messages
        if self._is_bracket_system_message(send_message):
            return

        # Debounce message processing
        await self._debounce_message(chat_id, send_user_id, send_user_name, send_message, item_id, ws)

    async def _debounce_message(
        self, chat_id: str, buyer_id: str, buyer_name: str, message: str, item_id: str, ws: Any
    ):
        """Debounce message processing to handle rapid successive messages."""
        # Cancel existing debounce task for this chat
        if chat_id in self.message_debounce_tasks:
            self.message_debounce_tasks[chat_id].cancel()

        async def _process_after_delay():
            await asyncio.sleep(self.message_debounce_delay)
            await self._process_chat_message(chat_id, buyer_id, buyer_name, message, item_id, ws)

        self.message_debounce_tasks[chat_id] = self._create_tracked_task(_process_after_delay())

    async def _process_chat_message(
        self, chat_id: str, buyer_id: str, buyer_name: str, message: str, item_id: str, ws: Any
    ):
        """Process a chat message and generate auto-reply."""
        # Generate auto-reply
        reply = None
        if self.auto_reply:
            product_title = ""
            product_price = ""
            product_description = ""

            # Try to get product details from cache
            if item_id and item_id in self._item_detail_cache:
                cached = self._item_detail_cache[item_id]
                product_title = cached.get("title", "")
                product_price = str(cached.get("price", ""))
                product_description = cached.get("description", "")

            reply = self.auto_reply.generate_reply(
                buyer_id=buyer_id,
                message=message,
                product_id=item_id,
                product_title=product_title,
                product_description=product_description,
                product_price=product_price,
                cookie_id=self.cookie_id,
            )

        if reply and bool(getattr(self.settings, "auto_reply_direct_send_enabled", False)):
            await self.send_message(ws, chat_id, buyer_id, reply)
            logger.info(f"[{self.cookie_id}] Auto-reply to {buyer_name}: {reply[:50]}")
        elif reply:
            logger.info(f"[{self.cookie_id}] Draft reply generated for {buyer_name}: {reply[:50]}")

        # Callback
        if self._on_message_callback:
            self._on_message_callback({
                "cookie_id": self.cookie_id,
                "buyer_id": buyer_id,
                "buyer_name": buyer_name,
                "message": message,
                "item_id": item_id,
                "chat_id": chat_id,
                "reply": reply,
            })

    def _is_sync_package(self, data: Dict) -> bool:
        try:
            return (isinstance(data, dict) and "body" in data
                    and "syncPushPackage" in data["body"]
                    and "data" in data["body"]["syncPushPackage"]
                    and len(data["body"]["syncPushPackage"]["data"]) > 0)
        except Exception:
            return False

    def _is_chat_message(self, message: Dict) -> bool:
        try:
            return (isinstance(message, dict) and "1" in message
                    and isinstance(message["1"], dict) and "10" in message["1"]
                    and isinstance(message["1"]["10"], dict)
                    and "reminderContent" in message["1"]["10"])
        except Exception:
            return False

    def _is_typing_status(self, message: Dict) -> bool:
        try:
            return (isinstance(message, dict) and "1" in message
                    and isinstance(message["1"], list) and len(message["1"]) > 0
                    and isinstance(message["1"][0], dict) and "1" in message["1"][0]
                    and isinstance(message["1"][0]["1"], str)
                    and "@goofish" in message["1"][0]["1"])
        except Exception:
            return False

    def _is_bracket_system_message(self, message: str) -> bool:
        try:
            if not message or not isinstance(message, str):
                return False
            clean = message.strip()
            return clean.startswith("[") and clean.endswith("]")
        except Exception:
            return False

    def _is_manual_mode(self, chat_id: str) -> bool:
        if chat_id not in self.manual_mode_conversations:
            return False
        if time.time() - self.manual_mode_timestamps.get(chat_id, 0) > self.manual_mode_timeout:
            self._exit_manual_mode(chat_id)
            return False
        return True

    def _toggle_manual_mode(self, chat_id: str) -> None:
        if chat_id in self.manual_mode_conversations:
            self._exit_manual_mode(chat_id)
            logger.info(f"[{self.cookie_id}] AI reply ON for chat {chat_id}")
        else:
            self.manual_mode_conversations.add(chat_id)
            self.manual_mode_timestamps[chat_id] = time.time()
            logger.info(f"[{self.cookie_id}] Manual mode ON for chat {chat_id}")

    def _exit_manual_mode(self, chat_id: str) -> None:
        self.manual_mode_conversations.discard(chat_id)
        self.manual_mode_timestamps.pop(chat_id, None)

    @classmethod
    def get_instance(cls, cookie_id: str) -> Optional["XianyuWebSocket"]:
        """Get instance by cookie_id."""
        return cls._instances.get(cookie_id)

    @classmethod
    def get_all_instances(cls) -> Dict[str, "XianyuWebSocket"]:
        """Get all active instances."""
        return dict(cls._instances)
