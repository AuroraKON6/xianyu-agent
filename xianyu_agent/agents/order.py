"""Order Agent - Order management and fulfillment.

Connects to real order database and order status handler.
Uses MiMo V2.5 Pro for intelligent order handling.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)


class OrderAgent(BaseAgent):
    name = "order"
    description = "Order management, fulfillment checklists, and after-sales"

    # Pre-fulfillment checklist from docs/ops/xianyu/fulfillment-after-sales-playbook.md
    CHECKLIST_ITEMS = [
        "confirm_specification",   # Size, material, color, function, thickness, quantity
        "confirm_address",         # Address and phone for large/fragile items
        "confirm_supplier_stock",  # Supplier has the ordered spec in stock
        "confirm_shipping",        # Supplier shipping timeline and method
        "confirm_special_instructions",  # No PDD branding, no price cards, neutral packaging
    ]

    def list_orders(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List orders from database, optionally filtered by cookie_id and status."""
        p = params or {}
        cookie_id = p.get("cookie_id", "")
        status = p.get("status")
        if cookie_id:
            orders = self.workflow.db.get_orders_by_cookie(cookie_id, status=status)
        else:
            # Get all orders across accounts
            orders = []
            cookies = self.workflow.db.get_all_cookies()
            for cid in cookies:
                orders.extend(self.workflow.db.get_orders_by_cookie(cid, status=status))
        return {"ok": True, "count": len(orders), "orders": orders}

    def get_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get a specific order by ID."""
        order_id = params.get("order_id", "")
        order = self.workflow.db.get_order(order_id)
        if not order:
            return {"ok": False, "error": f"Order {order_id} not found"}
        return {"ok": True, "order": order}

    def update_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Update order status."""
        order_id = params.get("order_id", "")
        status = params.get("status", "")
        self.workflow.db.update_order_status(order_id, status)
        return {"ok": True, "order_id": order_id, "new_status": status}

    def check_fulfillment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run pre-fulfillment checklist before confirming an order.

        Params:
          order_id: order to check
          specification: confirmed spec (size, material, color, etc.)
          address: shipping address
          phone: contact phone
          supplier_confirmed: whether supplier has stock
          shipping_method: courier vs logistics
          special_instructions: e.g., "无痕发货"

        Returns checklist status and any missing items.
        """
        order_id = params.get("order_id", "")
        missing = []
        warnings = []

        # Check specification
        spec = params.get("specification", "")
        if not spec:
            missing.append("specification: 未确认产品规格（尺寸、材质、颜色、功能、厚度、数量）")

        # Check address
        address = params.get("address", "")
        if not address:
            missing.append("address: 未确认收货地址")

        # Check phone
        phone = params.get("phone", "")
        if not phone:
            missing.append("phone: 未确认收货电话")

        # Check supplier stock
        if not params.get("supplier_confirmed"):
            missing.append("supplier_confirmed: 未确认供应商库存")

        # Check shipping
        shipping = params.get("shipping_method", "")
        if not shipping:
            missing.append("shipping_method: 未确认发货方式")

        # Check special instructions
        instructions = params.get("special_instructions", "")
        if not instructions:
            warnings.append("special_instructions: 建议确认无痕发货（无拼多多信息、无好评卡、无短信）")

        passed = len(missing) == 0
        return {
            "ok": True,
            "order_id": order_id,
            "passed": passed,
            "missing": missing,
            "warnings": warnings,
            "checklist": {
                "specification": spec,
                "address": address[:6] + "***" if len(address) > 10 else address,
                "phone": phone[:3] + "***" + phone[-4:] if len(phone) > 7 else phone,
                "supplier_confirmed": params.get("supplier_confirmed", False),
                "shipping_method": shipping,
                "special_instructions": instructions,
            },
        }

    def classify_after_sale(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Classify an after-sales issue and suggest handling approach.

        Params:
          message: buyer's complaint or issue description
          has_photos: whether buyer provided damage photos

        Returns classification and suggested action from the fulfillment playbook.
        """
        message = params.get("message", "")
        has_photos = params.get("has_photos", False)

        # Classify issue type
        issue_type = "unknown"
        if any(kw in message for kw in ["破损", "磕碰", "损坏", "碎了", "damaged"]):
            issue_type = "shipping_damage"
        elif any(kw in message for kw in ["质量问题", "不能用", "坏了", "defect", "broken"]):
            issue_type = "quality_issue"
        elif any(kw in message for kw in ["买错了", "选错了", "不要了", "changed mind"]):
            issue_type = "buyer_mistake"
        elif any(kw in message for kw in ["不想要", "退货", "退款"]):
            issue_type = "non_quality_return"
        elif any(kw in message for kw in ["投诉", "差评", "举报"]):
            issue_type = "dispute"

        strategies = {
            "shipping_damage": {
                "action": "道歉+收集照片+转发供应商+协商赔偿",
                "escalate": False,
                "note": "小问题发红包解决，态度诚恳。大件破损找供应商处理。",
            },
            "quality_issue": {
                "action": "收集证据+转发供应商+退换货",
                "escalate": False,
                "note": "小件退货运费不高。大件找供应商协商上门处理。拼多多介入100%处理。",
            },
            "buyer_mistake": {
                "action": "确认买家自付运费退换",
                "escalate": False,
                "note": "闲鱼无运费险，非质量问题买家自付运费。大件建议买家在闲鱼转卖。",
            },
            "non_quality_return": {
                "action": "买家自付运费退货",
                "escalate": False,
                "note": "如有运费险可小赚差价。",
            },
            "dispute": {
                "action": "保持冷静，收集证据，平台介入",
                "escalate": True,
                "note": "纠纷回复需要审批。",
            },
            "unknown": {
                "action": "了解情况后再处理",
                "escalate": False,
                "note": "无法自动分类，需人工判断。",
            },
        }

        strategy = strategies.get(issue_type, strategies["unknown"])
        return {
            "ok": True,
            "message": message,
            "issue_type": issue_type,
            "has_photos": has_photos,
            "suggested_action": strategy["action"],
            "escalate": strategy["escalate"],
            "note": strategy["note"],
        }

    def generate_shipping_message(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a shipping notification message using LLM."""
        order_id = params.get("order_id", "")
        tracking = params.get("tracking_number", "")
        product = params.get("product_title", "")
        system = "你是闲鱼卖家，写一条发货通知消息给买家。简洁友好，包含快递信息。用中文，50字以内。"
        result = self.chat(system, f"订单: {order_id}\n快递单号: {tracking}\n商品: {product}")
        return {"ok": True, "message": result}

    def handle_dispute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a response for order disputes using LLM."""
        issue = params.get("issue", "")
        product = params.get("product_title", "")
        system = """你是闲鱼纠纷处理专家。根据买家问题生成专业的回复方案。
返回JSON: {"response":"回复内容","action":"建议处理方式","escalate":true/false}"""
        result = self.chat_json(system, f"买家问题: {issue}\n商品: {product}")
        return {"ok": True, "resolution": result} if result else {"ok": False, "error": "Failed"}

    def get_handlers(self):
        return {
            "list_orders": self.list_orders,
            "get_order": self.get_order,
            "update_status": self.update_status,
            "check_fulfillment": self.check_fulfillment,
            "classify_after_sale": self.classify_after_sale,
            "generate_shipping_message": self.generate_shipping_message,
            "handle_dispute": self.handle_dispute,
        }
