"""Strategy Agent - Generate physical product listing strategies.

Handles:
- Low-price bait strategy (引流价 = 实际售价 50%)
- Publish city selection (按产品类别/发货地)
- Standard vs non-standard product differentiation
- First image strategy (场景图/堆积图/文字标签)
- Competitive analysis
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)


class StrategyAgent(BaseAgent):
    name = "strategy"
    description = "上架策略生成：低价引流、发布地址、标品/非标区分、首图策略"

    def create_strategy(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """生成实物产品上架策略。

        输入: title(标题), category(类别), actual_price(实际售价),
              cost_price(成本), profit_margin(利润率),
              shipping_origin(发货地, 可选), is_standard(是否标品, 可选)

        输出: 定价策略、发布城市、首图类型、描述重点、标签建议
        """
        result = self.workflow.create_listing_strategy(params)
        return {"ok": True, "type": "listing_strategy", "result": result}

    def create_full_strategy(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """完整的协作上架策略计划。

        先通过 sourcing agent 评估货源 → 再生成上架策略 → 返回完整的操作指南。
        """
        title = params.get("title", "")
        category = params.get("category", "")
        cost = params.get("cost_price", params.get("cost", 0))
        price = params.get("resale_price", params.get("price", 0))

        # Step 1: Ask sourcing agent to evaluate
        sourcing_result = {}
        if cost and price:
            sourcing_result = self.ask_agent(
                "sourcing",
                "evaluate_sourcing",
                {
                    "title": title,
                    "category": category,
                    "cost_price": cost,
                    "resale_price": price,
                    "shipping_cost": params.get("shipping_cost", 0),
                    "shipping_origin": params.get("shipping_origin", ""),
                },
                reason="先评估货源利润再生成上架策略",
            )

        # Step 2: Generate listing strategy
        strategy = self.workflow.create_listing_strategy({
            "title": title,
            "category": category,
            "actual_price": price,
            "cost_price": cost,
            "profit_margin": params.get("profit_margin", 0.35),
            "shipping_origin": params.get("shipping_origin", ""),
            "is_standard": params.get("is_standard"),
        })

        return {
            "ok": True,
            "type": "full_strategy",
            "sourcing_evaluation": sourcing_result,
            "listing_strategy": strategy.to_dict() if hasattr(strategy, "to_dict") else strategy,
        }

    def contribute_to_discussion(self, topic: str, transcript: list, context: Optional[Dict[str, Any]] = None) -> str:
        ctx = context or {}
        title = ctx.get("title", "未知商品")
        category = ctx.get("category", "")

        parts = [f"strategy: 针对「{title}」上架策略建议:"]
        if category:
            import re
            from ..physical_sourcing import PhysicalSourcingManager

            # Quick city suggestion
            cities_map = {
                "餐饮": "成都/重庆/广州",
                "厨具": "广州/成都/武汉",
                "机械": "河北/山东/浙江",
                "家具": "佛山/东莞/赣州",
                "电子": "深圳/东莞/苏州",
            }
            city = cities_map.get(category, "一线城市")
            parts.append(f"推荐发布城市: {city}。")
        parts.append("非标品(非品牌产品)应强调材质、尺寸、功能、售后, 用场景图做首图。")
        parts.append("标品应强调价格优势和正品保证。")
        return " ".join(parts)

    def get_handlers(self) -> Dict[str, callable]:
        return {
            "create_strategy": self.create_strategy,
            "create_full_strategy": self.create_full_strategy,
        }
