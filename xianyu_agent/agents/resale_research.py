"""Resale research agent — supplier info, competitor analysis, demand signals."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)


class ResaleResearchAgent(BaseAgent):
    name = "resale_research"
    description = "供应商信息、竞品分析、市场需求信号"

    def contribute_to_discussion(self, topic: str, transcript: list, context: Optional[Dict[str, Any]] = None) -> str:
        candidate = (context or {}).get("candidate", {})
        title = candidate.get("title", "未知商品")
        platform = candidate.get("source_platform", "未知平台")
        url = candidate.get("source_url", "")
        verified = candidate.get("supplier_verified", False)

        parts = [f"resale_research: 针对「{title}」供应商调研:"]
        if url:
            parts.append(f"来源平台 {platform}, 链接已提供。")
        else:
            parts.append(f"来源平台 {platform}, 缺少供应商链接, 建议补充。")
        if verified:
            parts.append("供应商已验证, 可信度较高。")
        else:
            parts.append("供应商未验证, 建议先完成验证再推进。")
        demand = candidate.get("estimated_demand_signal")
        if demand:
            parts.append(f"需求信号: {demand}。")
        else:
            parts.append("缺少市场需求数据, 建议补充竞品链接和销量参考。")
        return " ".join(parts)

    def review_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Return structured research scores. Deterministic rule-based with optional LLM summary."""
        llm_summary = self._try_llm_summary(candidate)
        scores = self._compute_scores(candidate)
        if llm_summary:
            scores["summary"] = llm_summary
        return scores

    def _compute_scores(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        url = candidate.get("source_url", "")
        verified = candidate.get("supplier_verified", False)
        platform = candidate.get("source_platform", "")
        demand = candidate.get("estimated_demand_signal", "")
        comp_min = candidate.get("competitor_price_min")
        comp_max = candidate.get("competitor_price_max")
        comp_links = candidate.get("competitor_links", [])

        # supplier_score: start 50, +20 url, +20 verified, +10 platform 1688
        supplier_score = 50
        if url:
            supplier_score += 20
        if verified:
            supplier_score += 20
        if platform == "1688":
            supplier_score += 10
        supplier_score = min(supplier_score, 100)

        # demand_score: start 40, +20 demand signal, +20 comp links, +10 comp price range
        demand_score = 40
        if demand:
            demand_score += 20
        if comp_links:
            demand_score += 20
        if comp_min is not None and comp_max is not None:
            demand_score += 10
        demand_score = min(demand_score, 100)

        # competitor_confidence
        if comp_min is not None and comp_max is not None:
            competitor_confidence = "high"
        elif comp_min is not None or comp_max is not None:
            competitor_confidence = "medium"
        else:
            competitor_confidence = "low"

        # research_flags
        research_flags = []
        if not url:
            research_flags.append("缺少供应商链接")
        if not verified:
            research_flags.append("供应商未验证")
        if not demand:
            research_flags.append("缺少市场数据")

        # summary
        parts = []
        if url:
            parts.append("供应商链接已提供")
        else:
            parts.append("缺少供应商链接")
        if verified:
            parts.append("已验证")
        else:
            parts.append("未验证")
        if demand:
            parts.append(f"需求信号: {demand}")
        summary = "resale_research: " + ", ".join(parts)

        return {
            "agent": "resale_research",
            "supplier_score": supplier_score,
            "demand_score": demand_score,
            "competitor_confidence": competitor_confidence,
            "research_flags": research_flags,
            "summary": summary,
        }

    def _try_llm_summary(self, candidate: Dict[str, Any]) -> Optional[str]:
        if not self._llm or not self._llm.enabled:
            return None
        try:
            result = self.chat_json(
                system="你是 resale_research agent。根据候选商品信息输出一段简短的供应商调研摘要(50字以内)。返回JSON: {\"summary\": \"...\"}",
                user=json.dumps(candidate, ensure_ascii=False),
                temperature=0.3,
                max_tokens=200,
            )
            if isinstance(result, dict) and result.get("summary"):
                return str(result["summary"])
        except Exception as exc:
            logger.debug("resale_research LLM summary failed: %s", exc)
        return None

    def get_handlers(self) -> Dict[str, callable]:
        return {}
