"""Customer Service Agent - Auto-reply to Xianyu buyers.

Delegates to AutoReplyEngine for full workflow:
keyword rules -> intent classification -> expert routing -> safety filter -> DB persistence.

Uses DeepSeek V4 Flash as LLM backend.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)


class CustomerServiceAgent(BaseAgent):
    name = "customer_service"
    description = "Auto-reply to Xianyu buyers with conversion-safe patterns (DeepSeek V4 Flash)"

    # Intent classification from docs/ops/xianyu/conversion-reply-playbook.md
    INTENT_PATTERNS = {
        "price_inquiry": ["多少钱", "价格", "怎么卖", "售价", "how much"],
        "spec_inquiry": ["尺寸", "规格", "材质", "颜色", "大小", "功能", "size", "material"],
        "delivery": ["发货", "快递", "几天到", "运费", "shipping", "delivery"],
        "quality": ["质量", "耐用", "材质", "好不好", "quality", "durable"],
        "comparison": ["淘宝", "拼多多", "别家", "其他", "更便宜", "taobao", "pdd"],
        "after_sales": ["退换", "退货", "售后", "保修", "return", "warranty"],
        "complaint": ["坏了", "破损", "不对", "差评", "投诉", "damaged"],
    }

    def generate_reply(
        self,
        buyer_id: str,
        message: str,
        product_id: str = "",
        product_title: str = "",
        product_description: str = "",
        product_price: str = "",
        cookie_id: str = "",
    ) -> Optional[str]:
        """Generate a reply using the full AutoReplyEngine pipeline.

        Flow: should_reply gate -> keyword rules -> intent classification ->
              expert routing (DeepSeek) -> safety filter -> DB persistence
        """
        return self.workflow.generate_auto_reply(
            buyer_id=buyer_id,
            message=message,
            product_id=product_id,
            product_title=product_title,
            product_description=product_description,
            product_price=product_price,
        )

    def classify_intent(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Classify buyer message intent using pattern matching and LLM.

        Returns intent type and suggested reply strategy from the conversion playbook.
        """
        message = params.get("message", "")
        if not message:
            return {"ok": False, "error": "message is required"}

        # Pattern-based classification
        detected_intents = []
        for intent, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in message.lower():
                    detected_intents.append(intent)
                    break

        primary_intent = detected_intents[0] if detected_intents else "general"

        # Strategy suggestion from playbook
        strategy = self._get_reply_strategy(primary_intent, message)

        return {
            "ok": True,
            "message": message,
            "detected_intents": detected_intents,
            "primary_intent": primary_intent,
            "reply_strategy": strategy,
        }

    def _get_reply_strategy(self, intent: str, message: str) -> Dict[str, str]:
        """Get reply strategy from the conversion playbook."""
        strategies = {
            "price_inquiry": {
                "approach": "报价同时说明规格，不要只说价格",
                "example": "在的亲！标价是XX款，XX元。您需要什么规格呢？我帮您推荐最合适的。",
            },
            "spec_inquiry": {
                "approach": "根据买家用途推荐最合适的规格",
                "example": "您主要是用来做什么呢？我帮您看看哪个规格最合适，能省一点是一点。",
            },
            "delivery": {
                "approach": "给出真实到货时间，匹配发货地",
                "example": "我们XX发货，大部分地区3-5天到货。大件走物流，会提前跟您约时间。",
            },
            "quality": {
                "approach": "提供具体质量信号（材质等级、承重、保修）",
                "example": "我们是工厂直供，[具体材质]，承重XX斤，质量有保障。售后直接找我就行。",
            },
            "comparison": {
                "approach": "不打价格战，强调服务和品质差异",
                "example": "哪里都有高价和低价的，我们主营工厂直供，用料足，售后有保障，随时找我。",
            },
            "after_sales": {
                "approach": "明确退换政策，主动提供解决方案",
                "example": "非质量问题退换运费自理，质量问题我们承担。您先拍个照片给我看看？",
            },
            "complaint": {
                "approach": "道歉+收集证据+提供解决方案，必要时升级到审批",
                "example": "非常抱歉给您带来不便！您方便拍几张照片发给我吗？我马上帮您处理。",
            },
            "general": {
                "approach": "主动提供产品基本信息，不要只回答字面问题",
                "example": "在的亲！这款产品[主要卖点]，需要了解什么随时问我。",
            },
        }
        return strategies.get(intent, strategies["general"])

    def get_rules(self) -> List[Dict[str, Any]]:
        """List all auto-reply rules."""
        return self.workflow.list_reply_rules()

    def add_rule(self, keyword: str, reply_text: str, product_id: str = "", priority: int = 0) -> Dict[str, Any]:
        """Add a keyword-based reply rule."""
        return self.workflow.add_reply_rule({
            "keyword": keyword,
            "reply_text": reply_text,
            "product_id": product_id,
            "priority": priority,
        })

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a reply rule."""
        return self.workflow.remove_reply_rule(rule_id)

    def get_conversation_history(self, cookie_id: str, chat_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get conversation history from database."""
        return self.workflow.db.get_conversation_history(cookie_id, chat_id, limit)

    def get_handlers(self):
        return {
            "generate_reply": self.generate_reply,
            "classify_intent": self.classify_intent,
            "get_rules": self.get_rules,
            "add_rule": self.add_rule,
            "remove_rule": self.remove_rule,
            "get_conversation_history": self.get_conversation_history,
        }
