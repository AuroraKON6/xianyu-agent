"""After-Sales Agent - Handle buyer complaints, returns, and refunds.

Handles 6 issue types from the article:
1. damage_minor - 小破损 → small red envelope (5-20 CNY)
2. damage_major - 大破损 → dispatch technician / full return
3. return_non_quality - 非质量退货 → buyer pays shipping, we earn insurance
4. return_quality - 质量退货 → full responsibility, large items dispatch technician
5. wrong_purchase - 买错了 → suggest reselling on Xianyu
6. missing_parts - 缺件 → compensate + resend
7. delayed_shipping - 发货延迟 → compensate + apologize

Strategy from the article:
- 小问题发红包，态度诚恳
- 大件退货建议闲鱼转卖
- 质量问题全权负责
- 比态度，不比推卸

Can collaborate with sourcing agent for supplier refund handling.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)


class AfterSalesAgent(BaseAgent):
    name = "after_sales"
    description = "售后工单处理：破损/退货/买错分类、自动回复生成、统计"

    def create_case(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """创建售后工单并自动生成回复。

        输入: buyer_message(买家反馈), product_title(产品名),
              product_price(产品价), product_size(small/medium/large)

        系统自动: 分类问题 → 计算补偿 → 生成回复话术
        """
        result = self.workflow.create_aftersales_case(params)
        return {"ok": True, "type": "after_sales_case", "result": result}

    def list_cases(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """列出售后工单。可选 status 筛选。"""
        status = (params or {}).get("status")
        cases = self.workflow.list_aftersales_cases(status)
        return {"ok": True, "type": "after_sales_list", "count": len(cases), "result": cases}

    def get_stats(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """获取售后统计数据：总工单数、各状态分布、总赔偿金额、解决率。"""
        stats = self.workflow.get_aftersales_stats()
        return {"ok": True, "type": "after_sales_stats", "result": stats}

    def classify_issue(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """只分类买家反馈的问题类型（不创建工单）。

        输入: buyer_message(买家消息)

        返回: 问题类型(damage_minor/damage_major/return_non_quality等)
        """
        result = self.workflow.after_sales.classify_issue(
            buyer_message=params.get("buyer_message", ""),
            product_title=params.get("product_title", ""),
        )
        return {"ok": True, "type": "issue_classification", "result": result}

    def contribute_to_discussion(self, topic: str, transcript: list, context: Optional[Dict[str, Any]] = None) -> str:
        ctx = context or {}
        title = ctx.get("title", "未知商品")
        price = ctx.get("product_price", ctx.get("price", 0))

        parts = [f"after_sales: 针对「{title}」售后预案:"]
        if price > 500:
            parts.append("大件商品, 售后重点是: 破损派师傅上门 → 质量问题全额退货 → 买家买错建议闲鱼转卖。")
        else:
            parts.append("小件商品, 售后重点是: 小破损发红包(5-10元) → 质量问题全额退款 → 非质退运费自理。")
        parts.append("所有售后首要原则: 态度诚恳, 比态度不比推卸。")
        return " ".join(parts)

    def get_handlers(self) -> Dict[str, callable]:
        return {
            "create_case": self.create_case,
            "list_cases": self.list_cases,
            "get_stats": self.get_stats,
            "classify_issue": self.classify_issue,
        }
