"""OpenClaw direct message router.

OpenClaw is the user-facing orchestrator. It routes commands directly into the
registered commerce agents and falls back to CommerceWorkflow for legacy natural
language flows such as memory, URL crawling, and opportunity evaluation.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .agent_registry import AgentRegistry, AgentTask
from .arbitrage import looks_like_opportunity_request
from .memory import looks_like_memory_request
from .url_crawler import looks_like_url_crawl_request


class OpenClawRouter:
    """Route OpenClaw chat messages to the local agent registry."""

    def __init__(self, registry: AgentRegistry, workflow: Any) -> None:
        self.registry = registry
        self.workflow = workflow

    def process_message(
        self,
        user_message: str,
        context: Optional[Dict[str, Any]] = None,
        publish: bool = True,
    ) -> Dict[str, Any]:
        clean = user_message.strip()
        if not clean:
            return {"type": "empty", "entrypoint": "openclaw", "result": {"message": "text is required"}}

        if any(word in clean for word in ("状态", "status", "概览", "总览")):
            return {
                "type": "openclaw_status",
                "entrypoint": "openclaw",
                "ok": True,
                "result": self.registry.get_status(),
            }

        if clean.startswith(("讨论:", "讨论：", "圆桌:", "圆桌：", "开会:", "开会：")):
            topic = _after_separator(clean) or clean
            participants = (context or {}).get("participants") or ["listing", "discovery", "analytics"]
            rounds = (context or {}).get("rounds", 2)
            discussion = self.registry.discuss(topic=topic, participants=participants, rounds=rounds, context=context or {})
            return {
                "type": "agent_discussion",
                "entrypoint": "openclaw",
                "ok": True,
                "result": discussion,
            }

        if any(word in clean for word in ("上架", "发布", "listing", "创建商品")):
            text = _strip_leading_command(clean, ("上架", "发布", "listing", "创建商品"))
            collaborative = any(word in clean for word in ("协作", "团队", "多agent", "多 Agent", "多Agent"))
            if collaborative:
                text = _strip_leading_command(text, ("协作", "团队", "多agent", "多 Agent", "多Agent"))
                text = _strip_leading_command(text, ("上架", "发布", "listing", "创建商品"))
            if not text:
                return {
                    "type": "listing",
                    "entrypoint": "openclaw",
                    "ok": False,
                    "result": {"message": "请提供商品描述，例如：上架 iPhone 15 Pro，售价8999元"},
                }
            params = {
                "text": text,
                "images": (context or {}).get("images", []),
                "publish": publish,
            }
            cost = _number_after(clean, ("成本", "cost"))
            price = _number_after(clean, ("售价", "价格", "price"))
            if cost is not None:
                params["cost"] = cost
            if price is not None:
                params["price"] = price
            task = self.registry.dispatch("listing", "create_collaborative_listing" if collaborative else "create_listing", params)
            return self._task_response("listing", task)

        if any(word in clean for word in ("搜货", "选品", "找货", "discover", "搜索商品")):
            keyword = _strip_leading_command(clean, ("搜货", "选品", "找货", "discover", "搜索商品"))
            if not keyword:
                return {
                    "type": "xianyu_discovery",
                    "entrypoint": "openclaw",
                    "ok": False,
                    "result": {"message": "请提供搜索关键词，例如：搜货 iPhone 15"},
                }
            task = self.registry.dispatch("discovery", "search_products", {"keyword": keyword})
            return self._task_response("xianyu_discovery", task)

        # Strategy-driven discovery: 按原材料法选品：不锈钢
        if any(word in clean for word in ("原材料", "raw_material", "按原材料")):
            material = _strip_leading_command(clean, ("按原材料法选品", "原材料选品", "原材料", "raw_material"))
            params = {"mode": "raw_material"}
            if material:
                params["material"] = material
            task = self.registry.dispatch("discovery", "strategy_discovery", params)
            return self._task_response("strategy_discovery", task)

        # High-ticket scan: 高客单价扫描
        if any(word in clean for word in ("高客单价", "high_ticket", "高价产品")):
            task = self.registry.dispatch("discovery", "strategy_discovery", {"mode": "high_ticket"})
            return self._task_response("strategy_discovery", task)

        # Batch-demand scan: 批量需求扫描
        if any(word in clean for word in ("批量需求", "batch_demand", "批量单")):
            task = self.registry.dispatch("discovery", "strategy_discovery", {"mode": "batch_demand"})
            return self._task_response("strategy_discovery", task)

        # Same-product multi-link scan: 同商品多链接测试
        if any(word in clean for word in ("多链接", "同商品", "竞品分析", "multi_link", "差异化分析")):
            keyword = _strip_leading_command(clean, ("多链接测试", "同商品测试", "竞品分析", "差异化分析", "多链接", "同商品"))
            params = {"mode": "same_product_multi_link"}
            if keyword:
                params["keyword"] = keyword
            task = self.registry.dispatch("discovery", "strategy_discovery", params)
            return self._task_response("strategy_discovery", task)

        # Resale candidate scoring: 分析这个商品能不能做闲鱼测试
        if any(word in clean for word in ("分析商品", "能不能做", "闲鱼测试", "score candidate")):
            match = re.search(r"(rc_[a-f0-9]+)", clean)
            if match:
                cid = match.group(1)
                return {
                    "type": "candidate_scoring_hint",
                    "entrypoint": "openclaw",
                    "ok": True,
                    "candidate_id": cid,
                    "message": f"请调用 POST /resale/candidates/{cid}/score 触发策略评分",
                }
            return {
                "type": "candidate_scoring_hint",
                "entrypoint": "openclaw",
                "ok": True,
                "message": "请提供 candidate_id, 例如: 分析商品 rc_xxxxxxxx",
            }

        # Weekly review: 做本周闲鱼复盘
        if any(word in clean for word in ("复盘", "周报", "weekly review", "dashboard")):
            task = self.registry.dispatch("analytics", "get_dashboard_data", {})
            return self._task_response("weekly_review", task)

        lower = clean.lower()
        if lower.startswith(("github:", "github：")) or any(word in lower for word in ("github", "repo")):
            query = _after_separator(clean) or clean
            task = self.registry.dispatch("github", "find_opportunities", {"queries": [query]})
            return self._task_response("github_discovery", task)

        if any(word in clean for word in ("订单", "order", "发货", "快递")):
            task = self.registry.dispatch("order", "list_orders", {})
            return self._task_response("orders", task)

        # Resale agent review — needs candidate_id (must be before analytics since "倒卖分析" contains "分析")
        if any(word in clean for word in ("倒卖分析", "resale review", "候选评审", "倒卖评审")):
            match = re.search(r"(rc_[a-f0-9]+)", clean)
            if match:
                cid = match.group(1)
                return {
                    "type": "resale_review_hint",
                    "entrypoint": "openclaw",
                    "ok": True,
                    "candidate_id": cid,
                    "message": f"请调用 POST /resale/candidates/{cid}/agent-review 触发 Agent 评审",
                }
            return {
                "type": "resale_review_hint",
                "entrypoint": "openclaw",
                "ok": True,
                "candidate_id": None,
                "message": "请提供 candidate_id, 例如: 倒卖分析 rc_xxxxxxxx",
            }

        if any(word in clean for word in ("分析", "报表", "利润", "analytics", "数据")):
            task = self.registry.dispatch("analytics", "get_dashboard_data", {})
            return self._task_response("analytics", task)

        if any(word in clean for word in ("客服", "回复", "auto-reply", "自动回复")):
            task = self.registry.dispatch("customer_service", "get_rules", {})
            return self._task_response("customer_service", task)

        if _should_use_legacy_workflow(clean):
            result = self.workflow.handle_agent_message(clean, publish=publish)
            if isinstance(result, dict):
                result.setdefault("entrypoint", "openclaw")
            return result

        return {
            "type": "openclaw_help",
            "entrypoint": "openclaw",
            "ok": True,
            "result": {
                "message": "我可以处理上架、搜货选品、策略选品、订单、客服、GitHub机会、利润/报表、比价、URL评估和记忆。请用明确指令开头。",
                "examples": [
                    "上架：测试商品，库存1，售价9.9",
                    "搜货：iPhone 15",
                    "按原材料法选品：不锈钢",
                    "高客单价扫描",
                    "批量需求扫描",
                    "多链接测试：不锈钢操作台",
                    "分析商品 rc_xxxxxxxx",
                    "做本周闲鱼复盘",
                    "比价：AI资料包，源售价99，计划卖39，已有供应商转售授权",
                    "github: ai agent workflow automation",
                    "记忆：以后标题更短一点",
                ],
            },
        }

    def _task_response(self, result_type: str, task: AgentTask) -> Dict[str, Any]:
        ok = task.status == "completed"
        if ok and isinstance(task.result, dict) and task.result.get("ok") is False:
            ok = False
        return {
            "type": result_type,
            "entrypoint": "openclaw",
            "ok": ok,
            "task": {
                "task_id": task.task_id,
                "agent": task.agent,
                "action": task.action,
                "status": task.status,
            },
            "result": task.result,
            "error": task.error,
        }


def _strip_leading_command(text: str, commands: tuple[str, ...]) -> str:
    pattern = r"^\s*(?:" + "|".join(re.escape(command) for command in commands) + r")\s*[：:]?\s*"
    return re.sub(pattern, "", text, count=1, flags=re.IGNORECASE).strip()


def _after_separator(text: str) -> str:
    if ":" in text:
        return text.split(":", 1)[1].strip()
    if "：" in text:
        return text.split("：", 1)[1].strip()
    return ""


def _number_after(text: str, labels: tuple[str, ...]) -> Optional[float]:
    for label in labels:
        match = re.search(re.escape(label) + r"\s*[：:]?\s*(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _should_use_legacy_workflow(text: str) -> bool:
    return (
        looks_like_memory_request(text)
        or looks_like_opportunity_request(text)
        or looks_like_url_crawl_request(text)
    )
