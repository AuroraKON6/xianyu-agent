"""Resale pricing agent — pricing strategy, margin optimization, competitor pricing."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)


class ResalePricingAgent(BaseAgent):
    name = "resale_pricing"
    description = "定价、毛利、竞品价格区间、价格建议"

    def contribute_to_discussion(self, topic: str, transcript: list, context: Optional[Dict[str, Any]] = None) -> str:
        candidate = (context or {}).get("candidate", {})
        title = candidate.get("title", "未知商品")
        supplier_price = candidate.get("supplier_price", 0)
        supplier_shipping = candidate.get("supplier_shipping", 0)
        target_price = candidate.get("target_sale_price", 0)
        economics = candidate.get("economics", {})
        gross_profit = economics.get("gross_profit")
        gross_margin = economics.get("gross_margin")
        comp_min = candidate.get("competitor_price_min")
        comp_max = candidate.get("competitor_price_max")

        total_cost = supplier_price + supplier_shipping
        parts = [f"resale_pricing: 针对「{title}」定价分析:"]
        parts.append(f"供应价 ¥{supplier_price} + 运费 ¥{supplier_shipping} = 成本 ¥{total_cost}。")
        parts.append(f"目标售价 ¥{target_price}。")
        if gross_profit is not None:
            if gross_margin is not None:
                parts.append(f"预估毛利 ¥{gross_profit:.1f}, 毛利率 {gross_margin:.1%}。")
            else:
                parts.append(f"预估毛利 ¥{gross_profit:.1f}。")
        if comp_min and comp_max:
            parts.append(f"竞品价格区间 ¥{comp_min}-¥{comp_max}。")
            if target_price < comp_min:
                parts.append("目标价低于竞品下限, 有价格优势但需确认是否有利润。")
            elif target_price > comp_max:
                parts.append("目标价高于竞品上限, 需要差异化卖点支撑。")
        else:
            parts.append("缺少竞品价格数据, 建议补充。")
        if gross_margin is not None and gross_margin >= 0.3:
            parts.append("毛利率健康, 定价合理。")
        elif gross_margin is not None and gross_margin >= 0.15:
            parts.append("毛利率一般, 建议优化成本或调整售价。")
        return " ".join(parts)

    def review_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Return structured pricing scores. Deterministic rule-based with optional LLM summary."""
        llm_summary = self._try_llm_summary(candidate)
        scores = self._compute_scores(candidate)
        if llm_summary:
            scores["summary"] = llm_summary
        return scores

    def _compute_scores(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        supplier_price = candidate.get("supplier_price", 0)
        supplier_shipping = candidate.get("supplier_shipping", 0)
        target_price = candidate.get("target_sale_price", 0)
        economics = candidate.get("economics", {})
        gross_margin = economics.get("gross_margin")
        comp_min = candidate.get("competitor_price_min")
        comp_max = candidate.get("competitor_price_max")

        total_cost = supplier_price + supplier_shipping

        # margin_score
        if gross_margin is not None:
            if gross_margin >= 0.4:
                margin_score = 100
            elif gross_margin >= 0.3:
                margin_score = 80
            elif gross_margin >= 0.2:
                margin_score = 60
            elif gross_margin >= 0.1:
                margin_score = 40
            elif gross_margin >= 0:
                margin_score = 20
            else:
                margin_score = 0
        else:
            margin_score = 0

        # price_competitiveness_score
        pricing_flags = []
        if comp_min is not None and comp_max is not None:
            if comp_min <= target_price <= comp_max:
                price_competitiveness_score = 80
            elif target_price < comp_min:
                price_competitiveness_score = 70
                pricing_flags.append("低于竞品")
            else:  # target_price > comp_max
                price_competitiveness_score = 40
                pricing_flags.append("高于竞品")
        else:
            price_competitiveness_score = 50
            pricing_flags.append("缺少竞品数据")

        # suggested_price
        if comp_min is not None and comp_max is not None:
            suggested_price = round((comp_min + comp_max) / 2, 2)
        else:
            suggested_price = round(total_cost * 2.5, 2) if total_cost > 0 else 0

        # pricing_flags
        if gross_margin is not None and gross_margin < 0:
            pricing_flags.append("负毛利")
        elif gross_margin is not None and gross_margin < 0.1:
            pricing_flags.append("低毛利")

        # summary
        parts = [f"margin_score={margin_score}", f"competitiveness={price_competitiveness_score}"]
        if suggested_price > 0:
            parts.append(f"建议价 ¥{suggested_price}")
        summary = "resale_pricing: " + ", ".join(parts)

        return {
            "agent": "resale_pricing",
            "margin_score": margin_score,
            "price_competitiveness_score": price_competitiveness_score,
            "suggested_price": suggested_price,
            "pricing_flags": pricing_flags,
            "summary": summary,
        }

    def _try_llm_summary(self, candidate: Dict[str, Any]) -> Optional[str]:
        if not self._llm or not self._llm.enabled:
            return None
        try:
            result = self.chat_json(
                system="你是 resale_pricing agent。根据候选商品信息输出一段简短的定价分析摘要(50字以内)。返回JSON: {\"summary\": \"...\"}",
                user=json.dumps(candidate, ensure_ascii=False),
                temperature=0.3,
                max_tokens=200,
            )
            if isinstance(result, dict) and result.get("summary"):
                return str(result["summary"])
        except Exception as exc:
            logger.debug("resale_pricing LLM summary failed: %s", exc)
        return None

    def get_handlers(self) -> Dict[str, callable]:
        return {}
