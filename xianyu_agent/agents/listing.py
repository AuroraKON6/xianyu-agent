"""Listing Agent - Create and optimize product listings."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..action_scope import listing_draft_revision

from .base import BaseAgent

logger = logging.getLogger(__name__)


class ListingAgent(BaseAgent):
    name = "listing"
    description = "Create and polish product listings"

    def create_listing(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a local product draft; publishing is never an agent action."""
        text = params.get("text", "")
        images = params.get("images", [])
        result = self.workflow.create_listing(
            raw_text=text,
            source="agent",
            product_images=images,
            publish=False,
        )
        return {
            "ok": True,
            "draft": result.get("draft"),
            "publish_result": None,
            "approval_required": bool(params.get("publish", False)),
            "executed": False,
        }

    def create_collaborative_listing(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a listing after peer-agent discussion, checks, and approval planning."""
        text = params.get("text", "")
        category = params.get("category") or _guess_category(text)
        collaboration: Dict[str, Any] = {}
        work_order: Dict[str, Any] = {}
        action_plan: Dict[str, Any] = {}

        if self._work_order_manager is not None:
            work_order = self._work_order_manager.create_work_order(
                kind="listing",
                title=text[:120] or "协作上架",
                owner="listing",
                context=_work_order_context(text, category, params),
                risk_level=params.get("risk_level", "normal"),
            )

        discussion = self._run_listing_discussion(text, category, params)
        if discussion:
            collaboration["discussion"] = discussion
            if self._work_order_manager is not None and work_order:
                work_order = self._work_order_manager.attach_discussion(work_order["work_order_id"], discussion)

        market = self.ask_agent(
            "discovery",
            "analyze_market",
            {"category": category},
            reason="Gather market context before creating a listing",
        )
        collaboration["market"] = market

        cost = _optional_number(params.get("cost"))
        price = _optional_number(params.get("price"))
        if cost is not None and price is not None:
            pricing = self.ask_agent(
                "analytics",
                "calculate_profit",
                {
                    "cost": cost,
                    "price": price,
                    "platform_fee_rate": params.get("platform_fee_rate", 0.08),
                    "shipping_cost": params.get("shipping_cost", 0),
                },
                reason="Validate profit before creating a listing",
            )
            collaboration["pricing"] = pricing
        else:
            collaboration["pricing"] = {"ok": False, "error": "cost and price are required for pricing collaboration"}

        enriched_text = _with_collaboration_context(text, collaboration)
        publish_requested = bool(params.get("publish", True))
        publish_now = publish_requested
        if self._work_order_manager is not None and publish_requested:
            publish_now = False

        result = self.workflow.create_listing(
            raw_text=enriched_text,
            source="collaborative_agent",
            product_images=params.get("images", []),
            publish=publish_now,
        )

        if self._work_order_manager is not None and work_order:
            action_plan = self._work_order_manager.create_action_plan(
                work_order["work_order_id"],
                discussion=discussion or None,
                proposed_actions=_listing_plan_actions(
                    category=category,
                    params=params,
                    draft=result.get("draft") or {},
                    publish_requested=publish_requested,
                ),
            )
            work_order = self._work_order_manager.get_work_order(work_order["work_order_id"])

        response = {
            "ok": True,
            "draft": result.get("draft"),
            "publish_result": result.get("publish_result"),
            "collaboration": collaboration,
        }
        if work_order:
            response["work_order"] = work_order
        if action_plan:
            response["action_plan"] = action_plan
            response["approval_required"] = action_plan.get("status") == "needs_approval"
        return response

    def _run_listing_discussion(self, text: str, category: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run a short roundtable before creating a collaborative listing."""
        if self._registry is None or params.get("discuss", True) is False:
            return {}
        topic = f"协作上架：{text}"
        context = {
            "category": category,
            "cost": params.get("cost"),
            "price": params.get("price"),
            "stock": params.get("stock"),
        }
        return self._registry.discuss(
            topic=topic,
            participants=params.get("participants") or ["listing", "discovery", "analytics"],
            rounds=int(params.get("rounds", 1)),
            context=context,
        )

    def polish_all(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Refresh/polish all published listings."""
        result = self.workflow.polish_all()
        return {"ok": True, "count": result.get("count", 0), "results": result.get("results", [])}

    def fulfill_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fulfill a digital product order."""
        return self.workflow.fulfill_order(params)

    def list_published(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List all published listings."""
        records = self.workflow.storage.list_published()
        return {"ok": True, "count": len(records), "listings": records}

    def get_handlers(self):
        return {
            "create_listing": self.create_listing,
            "create_collaborative_listing": self.create_collaborative_listing,
            "polish_all": self.polish_all,
            "fulfill_order": self.fulfill_order,
            "list_published": self.list_published,
        }


def _guess_category(text: str) -> str:
    if "耳机" in text:
        return "数码配件"
    if "手机" in text or "iPhone" in text:
        return "手机数码"
    if "资料" in text or "课程" in text:
        return "虚拟资料"
    return "闲鱼商品"


def _optional_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _with_collaboration_context(text: str, collaboration: Dict[str, Any]) -> str:
    return (
        f"{text}\n\n"
        "协作上下文\n"
        f"- Agent 圆桌讨论摘要: {collaboration.get('discussion', {}).get('summary', '')}\n"
        f"- Discovery Agent 市场建议: {collaboration.get('market')}\n"
        f"- Analytics Agent 定价建议: {collaboration.get('pricing')}\n"
        "请结合这些 agent 建议生成更稳妥的标题、价格说明、描述和 FAQ。\n\n"
        "安全边界（必须遵守）\n"
        "- 不得复制第三方商品图片或卖家原文文案作为自有内容。\n"
        "- 不得做虚假承诺（如伪造销量、伪造好评、伪造品牌授权）。\n"
        "- 低价引流必须在描述中明确标价对应的规格、尺寸或型号，不得恶意误导。\n"
        "- 所有草稿仅作为审批候选，不得直接发布。发布仍必须走 WorkOrder + Approval + ExecutionJobQueue。\n"
        "- 图片必须为自有实拍、授权供应商素材或经过授权的可商用图库，不得抓取其他卖家的图片。"
    )


def _work_order_context(text: str, category: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "text": text,
        "category": category,
        "cost": params.get("cost"),
        "price": params.get("price"),
        "stock": params.get("stock"),
        "publish_requested": params.get("publish", True),
    }


def _listing_plan_actions(
    category: str,
    params: Dict[str, Any],
    draft: Dict[str, Any],
    publish_requested: bool,
) -> List[Dict[str, Any]]:
    actions = [
        {
            "agent": "discovery",
            "action": "analyze_market",
            "description": "分析同类商品市场、价格区间和卖点",
            "params": {"category": category},
        },
        {
            "agent": "analytics",
            "action": "calculate_profit",
            "description": "核算成本、平台费、运费和利润率",
            "params": {
                "cost": params.get("cost"),
                "price": params.get("price"),
                "platform_fee_rate": params.get("platform_fee_rate", 0.08),
                "shipping_cost": params.get("shipping_cost", 0),
            },
        },
        {
            "agent": "listing",
            "action": "create_listing",
            "description": "生成商品草稿并等待复核",
            "params": {"draft_id": draft.get("id", ""), "title": draft.get("title", "")},
        },
    ]
    if publish_requested:
        actions.append(
            {
                "agent": "listing",
                "action": "publish_listing",
                "description": "人工确认后发布商品到闲鱼",
                "params": {
                    "draft_id": draft.get("id", ""),
                    "title": draft.get("title", ""),
                    "expected_draft_revision": listing_draft_revision(draft),
                },
                "draft_snapshot": {
                    "title": draft.get("title", ""),
                    "price": draft.get("price"),
                    "stock": draft.get("stock"),
                    "description": draft.get("description", ""),
                    "delivery_type": draft.get("delivery_type", ""),
                    "category": draft.get("category", ""),
                },
            }
        )
    return actions
