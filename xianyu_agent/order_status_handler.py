"""Order status handler with state machine for xianyu orders.

Manages order lifecycle:
- processing -> pending_ship -> shipped -> completed/cancelled/refunding
- Supports refund cancellation rollback
- Order ID extraction from complex message structures
- Pending update queue for orders not yet in DB
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional

from loguru import logger


# Order status handler configuration
ORDER_STATUS_HANDLER_CONFIG = {
    "use_pending_queue": True,
    "strict_validation": True,
    "log_level": "info",
    "max_pending_age_hours": 24,
    "enable_status_logging": True,
}


class OrderStatusHandler:
    """Order status state machine for xianyu orders."""

    # Valid state transitions
    VALID_TRANSITIONS = {
        "processing": ["pending_ship", "shipped", "completed", "cancelled"],
        "pending_ship": ["shipped", "completed", "cancelled", "refunding"],
        "shipped": ["completed", "cancelled", "refunding"],
        "completed": ["cancelled", "refunding"],
        "refunding": ["completed", "cancelled", "refund_cancelled"],
        "refund_cancelled": [],  # Temporary state, rolls back immediately
        "cancelled": [],  # Terminal state
    }

    STATUS_LABELS = {
        "processing": "处理中",
        "pending_ship": "待发货",
        "shipped": "已发货",
        "completed": "已完成",
        "refunding": "退款中",
        "refund_cancelled": "退款撤销",
        "cancelled": "已关闭",
    }

    def __init__(self):
        self.config = ORDER_STATUS_HANDLER_CONFIG
        self.pending_updates: Dict[str, List[Dict[str, Any]]] = {}
        self._order_status_history: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = threading.RLock()
        logger.info(f"Order status handler initialized, config: {self.config}")

    def extract_order_id(self, message: dict) -> Optional[str]:
        """Extract order ID from various message formats."""
        try:
            order_id = None

            # Method 1: From button targetUrl
            message_1 = message.get("1", {})
            if isinstance(message_1, dict):
                message_1_6 = message_1.get("6", {})
                if isinstance(message_1_6, dict):
                    content_json_str = message_1_6.get("3", {}).get("5", "") if isinstance(message_1_6.get("3", {}), dict) else ""
                    if content_json_str:
                        try:
                            content_data = json.loads(content_json_str)
                            # From button targetUrl
                            target_url = content_data.get("dxCard", {}).get("item", {}).get("main", {}).get("exContent", {}).get("button", {}).get("targetUrl", "")
                            if target_url:
                                match = re.search(r"orderId=(\d+)", target_url)
                                if match:
                                    order_id = match.group(1)
                                    logger.info(f"Extracted order ID from button: {order_id}")
                            # From main targetUrl
                            if not order_id:
                                main_url = content_data.get("dxCard", {}).get("item", {}).get("main", {}).get("targetUrl", "")
                                if main_url:
                                    match = re.search(r"order_detail\?id=(\d+)", main_url)
                                    if match:
                                        order_id = match.group(1)
                                        logger.info(f"Extracted order ID from main URL: {order_id}")
                            # From dynamicOperation
                            if not order_id:
                                dyn_url = content_data.get("dynamicOperation", {}).get("changeContent", {}).get("dxCard", {}).get("item", {}).get("main", {}).get("exContent", {}).get("button", {}).get("targetUrl", "")
                                if dyn_url:
                                    match = re.search(r"order_detail\?id=(\d+)", dyn_url)
                                    if match:
                                        order_id = match.group(1)
                                        logger.info(f"Extracted order ID from dynamicOperation: {order_id}")
                        except Exception as e:
                            logger.error(f"Failed to parse content JSON: {e}")

            # Method 2: Search entire message string
            if not order_id:
                message_str = str(message)
                patterns = [
                    r"orderId[=:](\d{10,})",
                    r"order_detail\?id=(\d{10,})",
                    r'"id"\s*:\s*"?(\d{10,})"?',
                    r"bizOrderId[=:](\d{10,})",
                ]
                for pattern in patterns:
                    match = re.search(pattern, message_str)
                    if match:
                        order_id = match.group(1)
                        logger.info(f"Extracted order ID from pattern {pattern}: {order_id}")
                        break

            return order_id

        except Exception as e:
            logger.error(f"Failed to extract order ID: {e}")
            return None

    def get_status_label(self, status: str) -> str:
        """Get Chinese label for status."""
        return self.STATUS_LABELS.get(status, status)

    def is_valid_transition(self, current_status: str, new_status: str) -> bool:
        """Check if a status transition is valid."""
        if not self.config.get("strict_validation", True):
            return True
        valid_targets = self.VALID_TRANSITIONS.get(current_status, [])
        return new_status in valid_targets

    def _record_status_history(self, order_id: str, old_status: str, new_status: str):
        """Record status change history for rollback support."""
        if order_id not in self._order_status_history:
            self._order_status_history[order_id] = []
        self._order_status_history[order_id].append({
            "old_status": old_status,
            "new_status": new_status,
            "timestamp": time.time(),
        })

    def get_previous_status(self, order_id: str) -> Optional[str]:
        """Get the previous status for rollback (refund cancellation)."""
        history = self._order_status_history.get(order_id, [])
        if len(history) >= 2:
            return history[-2]["new_status"]
        return None

    def handle_order_status_update(
        self,
        order_id: str,
        new_status: str,
        cookie_id: str = "",
        context: str = "",
    ) -> bool:
        """Update order status with validation."""
        with self._lock:
            try:
                # Get current status from DB
                current_status = "processing"
                try:
                    from .db_manager import db_manager
                    order = db_manager.get_order(order_id)
                    if order:
                        current_status = order.get("status", "processing")
                except ImportError:
                    pass

                # Handle refund cancellation rollback
                if new_status == "refund_cancelled":
                    previous = self.get_previous_status(order_id)
                    if previous:
                        new_status = previous
                        logger.info(f"[{cookie_id}] Refund cancelled, rolling back to: {new_status}")

                # Validate transition
                if not self.is_valid_transition(current_status, new_status):
                    logger.warning(
                        f"[{cookie_id}] Invalid status transition: {current_status} -> {new_status} for order {order_id}"
                    )
                    return False

                # Record history
                self._record_status_history(order_id, current_status, new_status)

                # Update in DB
                try:
                    from .db_manager import db_manager
                    if order:
                        db_manager.update_order_status(order_id, new_status)
                    else:
                        db_manager.save_order(order_id, cookie_id, status=new_status)
                except ImportError:
                    pass

                logger.info(
                    f"[{cookie_id}] Order {order_id}: {self.get_status_label(current_status)} -> {self.get_status_label(new_status)}"
                    + (f" ({context})" if context else "")
                )
                return True

            except Exception as e:
                logger.error(f"[{cookie_id}] Failed to update order status: {e}")
                return False

    def handle_auto_delivery_order_status(self, order_id: str, cookie_id: str = "", context: str = "") -> bool:
        """Handle order status update after auto-delivery."""
        return self.handle_order_status_update(order_id, "shipped", cookie_id, context)

    def handle_system_message(self, cookie_id: str, message: dict) -> Optional[str]:
        """Process system messages about order status changes.

        Returns the detected status change type, or None.
        """
        try:
            message_str = str(message).lower()

            # Detect status from message content
            if "付款" in message_str or "paid" in message_str:
                return self._handle_payment_message(cookie_id, message)
            elif "发货" in message_str or "shipped" in message_str:
                return self._handle_shipping_message(cookie_id, message)
            elif "收货" in message_str or "received" in message_str or "确认收货" in message_str:
                return self._handle_received_message(cookie_id, message)
            elif "退款" in message_str or "refund" in message_str:
                return self._handle_refund_message(cookie_id, message)
            elif "关闭" in message_str or "closed" in message_str or "交易关闭" in message_str:
                return self._handle_cancelled_message(cookie_id, message)

            return None

        except Exception as e:
            logger.error(f"[{cookie_id}] Failed to handle system message: {e}")
            return None

    def _handle_payment_message(self, cookie_id: str, message: dict) -> str:
        """Handle payment notification."""
        order_id = self.extract_order_id(message)
        if order_id:
            self.handle_order_status_update(order_id, "pending_ship", cookie_id, "买家已付款")
        return "payment"

    def _handle_shipping_message(self, cookie_id: str, message: dict) -> str:
        """Handle shipping notification."""
        order_id = self.extract_order_id(message)
        if order_id:
            self.handle_order_status_update(order_id, "shipped", cookie_id, "已发货")
        return "shipping"

    def _handle_received_message(self, cookie_id: str, message: dict) -> str:
        """Handle received notification."""
        order_id = self.extract_order_id(message)
        if order_id:
            self.handle_order_status_update(order_id, "completed", cookie_id, "买家已确认收货")
        return "received"

    def _handle_refund_message(self, cookie_id: str, message: dict) -> str:
        """Handle refund notification."""
        order_id = self.extract_order_id(message)
        if order_id:
            self.handle_order_status_update(order_id, "refunding", cookie_id, "买家申请退款")
        return "refund"

    def _handle_cancelled_message(self, cookie_id: str, message: dict) -> str:
        """Handle order cancellation."""
        order_id = self.extract_order_id(message)
        if order_id:
            self.handle_order_status_update(order_id, "cancelled", cookie_id, "交易关闭")
        return "cancelled"

    def get_pending_updates(self, cookie_id: str = None) -> Dict[str, List[Dict[str, Any]]]:
        """Get pending updates, optionally filtered by cookie_id."""
        if cookie_id:
            return {k: v for k, v in self.pending_updates.items() if any(u.get("cookie_id") == cookie_id for u in v)}
        return self.pending_updates

    def cleanup_stale_updates(self):
        """Remove stale pending updates."""
        max_age = self.config.get("max_pending_age_hours", 24) * 3600
        current_time = time.time()
        with self._lock:
            stale_keys = []
            for order_id, updates in self.pending_updates.items():
                valid_updates = [u for u in updates if current_time - u.get("timestamp", 0) < max_age]
                if valid_updates:
                    self.pending_updates[order_id] = valid_updates
                else:
                    stale_keys.append(order_id)
            for key in stale_keys:
                del self.pending_updates[key]
            if stale_keys:
                logger.info(f"Cleaned up {len(stale_keys)} stale pending updates")


# Global singleton
order_status_handler = OrderStatusHandler()
