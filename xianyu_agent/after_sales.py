"""After-sales handling for physical product dropshipping.

Implements article's after-sales strategies:
- Minor damage: small red envelope (5-20 CNY) + sincere apology
- Non-quality return: buyer pays shipping, we earn shipping insurance
- Quality return: negotiate dispatch technician for large items
- Wrong purchase: suggest reselling on Xianyu
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class IssueType(Enum):
    DAMAGE_MINOR = "damage_minor"          # 小破损
    DAMAGE_MAJOR = "damage_major"          # 大破损
    RETURN_NON_QUALITY = "return_non_quality"  # 非质量问题退货
    RETURN_QUALITY = "return_quality"      # 质量问题退货
    WRONG_PURCHASE = "wrong_purchase"      # 买错了
    MISSING_PARTS = "missing_parts"        # 缺件
    DELAYED_SHIPPING = "delayed_shipping"  # 发货延迟
    OTHER = "other"


class ProductSize(Enum):
    SMALL = "small"      # 小件 (< 20kg)
    MEDIUM = "medium"    # 中件 (20-50kg)
    LARGE = "large"      # 大件 (> 50kg)


# Response templates (from article methodology)
RESPONSE_TEMPLATES = {
    "damage_minor": {
        "apology": "非常抱歉亲，运输中造成了小破损，我们一定会妥善处理。",
        "solution": "这边给您补偿{amount}元红包作为歉意，您看可以吗？",
        "follow_up": "如果影响使用，我们马上安排补发或者退换，绝不推脱。",
    },
    "damage_major": {
        "apology": "非常抱歉亲，这次运输造成了较大破损，是我们的责任。",
        "solution": "我们马上联系商家安排{action}，产生的费用全部由我们承担。",
        "follow_up": "请拍几张照片给我们，我们好跟物流和商家交涉。",
    },
    "return_non_quality": {
        "explanation": "亲，闲鱼平台没有运费险，非质量问题退货需要您承担退货运费哦。",
        "suggestion": "如果您确实不想要了，建议您在闲鱼上转卖，这样可以减少损失。",
        "alternatives": "或者我们可以帮您问问有没有其他买家需要，尽量帮您减少损失。",
    },
    "return_quality": {
        "small_item": "质量问题我们全权负责，退货运费我们承担，您直接申请退货即可。",
        "large_item_dispatch": "大件商品退货运费较高，我们先安排师傅上门检查维修，如果修不好再退货。",
        "large_item_return": "如果确实需要退货，我们联系商家安排物流上门取件，运费由商家承担。",
    },
    "wrong_purchase": {
        "explanation": "亲，非质量问题退货需要您承担运费哦。",
        "suggestion_small": "建议您挂在闲鱼上卖掉，小件运费不高，应该能卖掉的。",
        "suggestion_large": "大件退货运费要几百块，建议您挂在闲鱼上处理，能省一点是一点。",
        "help": "我们可以帮您看看有没有合适的买家，尽量帮您减少损失。",
    },
    "missing_parts": {
        "response": "非常抱歉，可能仓库漏发了配件。我们马上联系补发，预计2-3天内发出。",
        "compensation": "给您补偿{amount}元作为等待的歉意，请见谅。",
    },
    "delayed_shipping": {
        "apology": "非常抱歉，发货比预期晚了。",
        "explanation": "可能是厂家那边排单较多，我们已经催促了。",
        "compensation": "给您补偿{amount}元红包，到货后确认收货再联系领取。",
    },
}


@dataclass
class AfterSalesCase:
    """An after-sales case record."""
    id: str
    order_id: str
    buyer_id: str
    issue_type: str
    product_title: str
    product_price: float
    product_size: str
    buyer_message: str
    handler_response: str = ""
    resolution: str = ""
    compensation_amount: float = 0.0
    status: str = "open"  # open, handling, resolved, escalated
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AfterSalesHandler:
    """Handle after-sales issues for physical product dropshipping."""

    def __init__(self, settings: Any, storage: Any, llm: Any) -> None:
        self.settings = settings
        self.storage = storage
        self.llm = llm
        self._cases: Dict[str, AfterSalesCase] = {}
        self._load_cases()

    def _data_dir(self) -> Path:
        path = self.storage.data_dir / "after_sales"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load_cases(self) -> None:
        cases_file = self._data_dir() / "cases.json"
        if cases_file.exists():
            try:
                data = json.loads(cases_file.read_text(encoding="utf-8"))
                for item in data:
                    self._cases[item["id"]] = AfterSalesCase(**item)
            except Exception as exc:
                logger.warning("Failed to load after-sales cases: %s", exc)

    def _save_cases(self) -> None:
        cases_file = self._data_dir() / "cases.json"
        cases_file.write_text(
            json.dumps([c.to_dict() for c in self._cases.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def create_case(
        self,
        order_id: str,
        buyer_id: str,
        issue_type: str,
        product_title: str,
        product_price: float,
        buyer_message: str,
        product_size: str = "small",
    ) -> Dict[str, Any]:
        """Create a new after-sales case."""
        case = AfterSalesCase(
            id=uuid4().hex[:12],
            order_id=order_id,
            buyer_id=buyer_id,
            issue_type=issue_type,
            product_title=product_title,
            product_price=product_price,
            product_size=product_size,
            buyer_message=buyer_message,
            status="open",
            created_at=__import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            updated_at=__import__("time").strftime("Y-%m-%d %H:%M:%S"),
        )
        self._cases[case.id] = case
        self._save_cases()

        # Auto-generate initial response
        response = self.generate_response(case)
        case.handler_response = response
        case.status = "handling"
        self._save_cases()

        return case.to_dict()

    def classify_issue(self, buyer_message: str, product_title: str = "") -> str:
        """Classify buyer's issue from message."""
        msg = buyer_message.lower()

        # Damage detection
        damage_keywords = ["破损", "坏了", "裂", "碎", "凹陷", "变形", "磕碰", "划痕"]
        if any(kw in msg for kw in damage_keywords):
            # Determine severity
            major_damage = ["严重", "大", "完全", "不能用", "报废", "烂"]
            if any(kw in msg for kw in major_damage):
                return IssueType.DAMAGE_MAJOR.value
            return IssueType.DAMAGE_MINOR.value

        # Return requests
        return_keywords = ["退货", "不要", "退款", "退掉", "退钱", "不想要"]
        if any(kw in msg for kw in return_keywords):
            quality_keywords = ["质量", "问题", "坏的", "瑕疵", " defective", "不行"]
            if any(kw in msg for kw in quality_keywords):
                return IssueType.RETURN_QUALITY.value
            return IssueType.RETURN_NON_QUALITY.value

        # Wrong purchase
        wrong_keywords = ["买错", "选错", "尺寸不对", "大小不对", "颜色不对", "拍错"]
        if any(kw in msg for kw in wrong_keywords):
            return IssueType.WRONG_PURCHASE.value

        # Missing parts
        missing_keywords = ["缺", "少", "没有", "漏发", "配件", "零件"]
        if any(kw in msg for kw in missing_keywords):
            return IssueType.MISSING_PARTS.value

        # Delayed shipping
        delay_keywords = ["还没发", "怎么还没", "多久发货", "催", "快点"]
        if any(kw in msg for kw in delay_keywords):
            return IssueType.DELAYED_SHIPPING.value

        return IssueType.OTHER.value

    def generate_response(self, case: AfterSalesCase) -> str:
        """Generate handler response for a case."""
        issue_type = case.issue_type
        product_size = case.product_size

        if issue_type == IssueType.DAMAGE_MINOR.value:
            return self._handle_damage_minor(case)
        elif issue_type == IssueType.DAMAGE_MAJOR.value:
            return self._handle_damage_major(case)
        elif issue_type == IssueType.RETURN_NON_QUALITY.value:
            return self._handle_return_non_quality(case)
        elif issue_type == IssueType.RETURN_QUALITY.value:
            return self._handle_return_quality(case, product_size)
        elif issue_type == IssueType.WRONG_PURCHASE.value:
            return self._handle_wrong_purchase(case, product_size)
        elif issue_type == IssueType.MISSING_PARTS.value:
            return self._handle_missing_parts(case)
        elif issue_type == IssueType.DELAYED_SHIPPING.value:
            return self._handle_delayed_shipping(case)
        else:
            return "亲，您好！我已经记录了您的问题，会尽快为您处理，请稍等。"

    def _handle_damage_minor(self, case: AfterSalesCase) -> str:
        """Handle minor damage: small red envelope."""
        # Determine compensation based on product price
        if case.product_price < 50:
            amount = 5
        elif case.product_price < 200:
            amount = 10
        else:
            amount = 20

        case.compensation_amount = amount
        self._save_cases()

        templates = RESPONSE_TEMPLATES["damage_minor"]
        return (
            f"{templates['apology']}\n"
            f"{templates['solution'].format(amount=amount)}\n"
            f"{templates['follow_up']}"
        )

    def _handle_damage_major(self, case: AfterSalesCase) -> str:
        """Handle major damage: dispatch technician or full return."""
        action = "退换货"
        if case.product_size == "large":
            action = "师傅上门检查，如需退换我们承担全部费用"

        templates = RESPONSE_TEMPLATES["damage_major"]
        return (
            f"{templates['apology']}\n"
            f"{templates['solution'].format(action=action)}\n"
            f"{templates['follow_up']}"
        )

    def _handle_return_non_quality(self, case: AfterSalesCase) -> str:
        """Handle non-quality return."""
        templates = RESPONSE_TEMPLATES["return_non_quality"]
        return (
            f"{templates['explanation']}\n"
            f"{templates['suggestion']}\n"
            f"{templates['alternatives']}"
        )

    def _handle_return_quality(self, case: AfterSalesCase, product_size: str) -> str:
        """Handle quality issue return."""
        templates = RESPONSE_TEMPLATES["return_quality"]

        if product_size == "small":
            return templates["small_item"]
        elif product_size == "large":
            return (
                f"{templates['large_item_dispatch']}\n"
                f"如果师傅确认无法维修：{templates['large_item_return']}"
            )
        else:
            return templates["small_item"]

    def _handle_wrong_purchase(self, case: AfterSalesCase, product_size: str) -> str:
        """Handle wrong purchase."""
        templates = RESPONSE_TEMPLATES["wrong_purchase"]

        if product_size == "small":
            suggestion = templates["suggestion_small"]
        else:
            suggestion = templates["suggestion_large"]

        return (
            f"{templates['explanation']}\n"
            f"{suggestion}\n"
            f"{templates['help']}"
        )

    def _handle_missing_parts(self, case: AfterSalesCase) -> str:
        """Handle missing parts."""
        amount = 5 if case.product_price < 100 else 10
        case.compensation_amount = amount
        self._save_cases()

        templates = RESPONSE_TEMPLATES["missing_parts"]
        return (
            f"{templates['response']}\n"
            f"{templates['compensation'].format(amount=amount)}"
        )

    def _handle_delayed_shipping(self, case: AfterSalesCase) -> str:
        """Handle delayed shipping."""
        amount = 3 if case.product_price < 100 else 5
        case.compensation_amount = amount
        self._save_cases()

        templates = RESPONSE_TEMPLATES["delayed_shipping"]
        return (
            f"{templates['apology']}{templates['explanation']}\n"
            f"{templates['compensation'].format(amount=amount)}"
        )

    def resolve_case(self, case_id: str, resolution: str) -> Optional[Dict[str, Any]]:
        """Mark a case as resolved."""
        case = self._cases.get(case_id)
        if not case:
            return None

        case.status = "resolved"
        case.resolution = resolution
        case.updated_at = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
        self._save_cases()

        return case.to_dict()

    def escalate_case(self, case_id: str, reason: str) -> Optional[Dict[str, Any]]:
        """Escalate a case to human handler."""
        case = self._cases.get(case_id)
        if not case:
            return None

        case.status = "escalated"
        case.resolution = f"Escalated: {reason}"
        case.updated_at = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
        self._save_cases()

        return case.to_dict()

    def list_cases(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all cases, optionally filtered by status."""
        cases = list(self._cases.values())
        if status:
            cases = [c for c in cases if c.status == status]
        return [c.to_dict() for c in cases]

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        case = self._cases.get(case_id)
        return case.to_dict() if case else None

    def get_stats(self) -> Dict[str, Any]:
        """Get after-sales statistics."""
        total = len(self._cases)
        by_status = {}
        by_type = {}
        total_compensation = 0.0

        for case in self._cases.values():
            by_status[case.status] = by_status.get(case.status, 0) + 1
            by_type[case.issue_type] = by_type.get(case.issue_type, 0) + 1
            total_compensation += case.compensation_amount

        return {
            "total_cases": total,
            "by_status": by_status,
            "by_type": by_type,
            "total_compensation": round(total_compensation, 2),
            "resolution_rate": (
                round(by_status.get("resolved", 0) / total * 100, 2) if total > 0 else 0
            ),
        }


# ====== Integration helpers ======

def looks_like_aftersales_request(text: str) -> bool:
    """Check if text is an after-sales related request."""
    patterns = [
        r"售后",
        r"退货",
        r"破损",
        r"退款",
        r"质量问题",
        r"买错",
        r"缺件",
        r"赔偿",
        r"补偿",
        r"处理",
    ]
    return any(re.search(p, text) for p in patterns)
