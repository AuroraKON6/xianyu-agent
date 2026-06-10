"""Resale risk agent — prohibited items, IP, after-sales, logistics, margin risk."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)


class ResaleRiskAgent(BaseAgent):
    name = "resale_risk"
    description = "禁售、侵权、售后、物流、负毛利风险"

    def contribute_to_discussion(self, topic: str, transcript: list, context: Optional[Dict[str, Any]] = None) -> str:
        candidate = (context or {}).get("candidate", {})
        title = candidate.get("title", "未知商品")
        risk_flags = candidate.get("risk_flags", [])
        risk_level = candidate.get("risk_level", "unknown")
        economics = candidate.get("economics", {})
        gross_margin = economics.get("gross_margin")
        category = candidate.get("category", "")

        parts = [f"resale_risk: 针对「{title}」风险评估:"]
        if risk_flags:
            parts.append(f"已有风险标记 {len(risk_flags)} 项: {'、'.join(risk_flags[:3])}。")
        else:
            parts.append("当前无风险标记, 但需人工复核禁售/侵权清单。")
        if risk_level == "blocked":
            parts.append("风险等级已标记为 blocked, 强烈建议不推进。")
        elif risk_level == "high":
            parts.append("风险等级 high, 需额外审核。")
        else:
            parts.append(f"风险等级 {risk_level}, 可接受。")
        if gross_margin is not None and gross_margin < 0:
            parts.append("⚠️ 负毛利, 禁止推进。")
        elif gross_margin is not None and gross_margin < 0.1:
            parts.append(f"毛利率 {gross_margin:.1%} 偏低, 存在亏损风险。")
        if "食品" in category or "零食" in category:
            parts.append("食品类目需额外注意保质期和食品安全合规。")
        return " ".join(parts)

    def review_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Return structured risk scores. Deterministic rule-based with optional LLM summary."""
        llm_summary = self._try_llm_summary(candidate)
        scores = self._compute_scores(candidate)
        if llm_summary:
            scores["summary"] = llm_summary
        return scores

    def _compute_scores(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        risk_flags = candidate.get("risk_flags", [])
        risk_level = candidate.get("risk_level", "unknown")
        economics = candidate.get("economics", {})
        gross_margin = economics.get("gross_margin")
        category = candidate.get("category", "")
        market_scope = candidate.get("market_scope", "domestic")
        status = candidate.get("status", "")

        # compliance_score: start 80
        compliance_score = 80
        for kw in ("食品", "药品", "医疗", "母婴"):
            if kw in category:
                compliance_score -= 30
                break
        if risk_level == "high":
            compliance_score -= 20
        if risk_level == "blocked":
            compliance_score -= 50
        compliance_score = max(compliance_score, 0)

        # fulfillment_risk_score: start 80
        fulfillment_risk_score = 80
        fulfillment_risk_score -= min(len(risk_flags) * 10, 40)
        if market_scope == "cross_border":
            fulfillment_risk_score -= 20
        fulfillment_risk_score = max(fulfillment_risk_score, 0)

        # aftersale_risk_score: start 80
        aftersale_risk_score = 80
        if "食品" in category or "零食" in category:
            aftersale_risk_score -= 20
        if any(kw in category.lower() for kw in ("电子", "数码", "electronics")):
            aftersale_risk_score -= 10
        if gross_margin is not None and gross_margin < 0.1:
            aftersale_risk_score -= 20
        aftersale_risk_score = max(aftersale_risk_score, 0)

        # blocking_flags
        blocking_flags: List[str] = []
        if gross_margin is not None and gross_margin < 0:
            blocking_flags.append("负毛利")
        food_keywords = ("食品", "零食")
        if any(kw in category for kw in food_keywords) and status == "blocked":
            blocking_flags.append("食品禁售")
        if risk_level == "blocked":
            blocking_flags.append("高风险已阻断")

        # risk_flags: copy existing + any new
        all_risk_flags = list(risk_flags)
        if gross_margin is not None and 0 <= gross_margin < 0.1:
            if "低毛利" not in all_risk_flags:
                all_risk_flags.append("低毛利")

        # summary
        parts = [f"compliance={compliance_score}", f"fulfillment={fulfillment_risk_score}", f"aftersale={aftersale_risk_score}"]
        if blocking_flags:
            parts.append(f"阻断: {', '.join(blocking_flags)}")
        summary = "resale_risk: " + ", ".join(parts)

        return {
            "agent": "resale_risk",
            "compliance_score": compliance_score,
            "fulfillment_risk_score": fulfillment_risk_score,
            "aftersale_risk_score": aftersale_risk_score,
            "risk_flags": all_risk_flags,
            "blocking_flags": blocking_flags,
            "summary": summary,
        }

    def _try_llm_summary(self, candidate: Dict[str, Any]) -> Optional[str]:
        if not self._llm or not self._llm.enabled:
            return None
        try:
            result = self.chat_json(
                system="你是 resale_risk agent。根据候选商品信息输出一段简短的风险评估摘要(50字以内)。返回JSON: {\"summary\": \"...\"}",
                user=json.dumps(candidate, ensure_ascii=False),
                temperature=0.3,
                max_tokens=200,
            )
            if isinstance(result, dict) and result.get("summary"):
                return str(result["summary"])
        except Exception as exc:
            logger.debug("resale_risk LLM summary failed: %s", exc)
        return None

    def get_handlers(self) -> Dict[str, callable]:
        return {}
