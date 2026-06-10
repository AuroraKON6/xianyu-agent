"""Base agent class with workflow integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..llm_manager import llm_manager

if TYPE_CHECKING:
    from ..workflow import CommerceWorkflow

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for all agents.

    All agents share a reference to the CommerceWorkflow instance,
    which provides access to real systems (adapter, db, notifier, etc).
    """

    name: str = "base"
    description: str = ""
    _workflow: Optional["CommerceWorkflow"] = None
    _registry: Optional[Any] = None
    _work_order_manager: Optional[Any] = None

    def __init__(self):
        self._llm = None

    @classmethod
    def set_workflow(cls, workflow: "CommerceWorkflow") -> None:
        """Set the shared workflow instance for all agents."""
        cls._workflow = workflow
        logger.info("Workflow injected into BaseAgent")

    @classmethod
    def set_registry(cls, registry: Optional[Any]) -> None:
        """Set the shared agent registry for peer-to-peer collaboration."""
        cls._registry = registry
        if registry is not None:
            logger.info("Agent registry injected into BaseAgent")

    @classmethod
    def set_work_order_manager(cls, manager: Optional[Any]) -> None:
        """Set the shared work-order manager for realistic agent workflows."""
        cls._work_order_manager = manager
        if manager is not None:
            logger.info("Work order manager injected into BaseAgent")

    @property
    def workflow(self) -> "CommerceWorkflow":
        """Access the workflow (real systems)."""
        if self._workflow is None:
            raise RuntimeError("Workflow not set. Call BaseAgent.set_workflow(workflow) first.")
        return self._workflow

    @property
    def llm(self):
        """Get this agent's LLM client."""
        if self._llm is None:
            self._llm = llm_manager.get_client(self.name)
        return self._llm

    def chat(self, system: str, user: str, **kwargs) -> str:
        """Plain text chat using agent's LLM."""
        return self.llm.chat(system, user, **kwargs)

    def chat_json(self, system: str, user: str, **kwargs) -> Optional[Dict[str, Any]]:
        """JSON response using agent's LLM."""
        return self.llm.chat_json(system, user, **kwargs)

    def ask_agent(self, agent: str, action: str, params: Optional[Dict[str, Any]] = None, reason: str = "") -> Dict[str, Any]:
        """Ask another registered agent to perform an action."""
        if self._registry is None:
            return {"ok": False, "error": "Agent registry not set"}
        task = self._registry.send_message(self.name, agent, action, params or {}, reason=reason)
        if task.status == "completed":
            return task.result if isinstance(task.result, dict) else {"ok": True, "result": task.result}
        return {"ok": False, "error": task.error or f"Task {task.task_id} {task.status}"}

    def contribute_to_discussion(self, topic: str, transcript: list, context: Optional[Dict[str, Any]] = None) -> str:
        """Contribute one turn to a multi-agent discussion."""
        system = (
            f"你是 {self.name} agent。根据你的职责参与多 agent 讨论。"
            "请先回应已有观点，再给出你负责领域的具体建议。"
            "回复中文，控制在 120 字以内。"
        )
        recent = transcript[-8:]
        user = (
            f"讨论主题: {topic}\n"
            f"上下文: {context or {}}\n"
            f"已有发言: {recent}\n"
            "请给出你的下一轮发言。"
        )
        try:
            response = self.chat(system, user, temperature=0.3, max_tokens=300)
        except Exception as exc:
            logger.warning("%s discussion LLM failed: %s", self.name, exc)
            response = ""
        response = (response or "").strip()
        if response:
            return response
        return _discussion_fallback(self.name, topic, context or {})

    def get_handlers(self) -> Dict[str, callable]:
        """Return {action_name: handler_function} mapping. Override in subclass."""
        return {}


def _discussion_fallback(agent_name: str, topic: str, context: Dict[str, Any]) -> str:
    role_lines = {
        "listing": f"listing: 我建议围绕「{topic}」先明确标题、成色、库存、图片和交付方式，再生成低客服负担的上架文案。",
        "discovery": f"discovery: 我建议先看同类商品市场价格、需求热度和卖点差异，再决定「{topic}」的搜索关键词和定价区间。",
        "analytics": f"analytics: 我建议核算成本、平台费、运费和目标利润，确认「{topic}」的利润率达标后再发布。",
        "order": f"order: 我建议提前定义发货、售后和异常订单处理话术，避免「{topic}」发布后履约混乱。",
        "customer_service": f"customer_service: 我建议把买家高频问题写进 FAQ，并准备价格、物流、成色相关回复。",
        "github": f"github: 我建议检查是否有可复用的自动化流程或开源工具，但注意许可和二次售卖边界。",
        "resale_research": f"resale_research: 针对「{topic}」, 我建议核实供应商资质、来源平台可靠性和市场需求信号。",
        "resale_risk": f"resale_risk: 针对「{topic}」, 需检查禁售清单、侵权风险、售后成本和物流可行性。",
        "resale_pricing": f"resale_pricing: 针对「{topic}」, 建议核算全成本(采购+运费+平台费), 确保毛利率达标。",
    }
    return role_lines.get(agent_name, f"{agent_name}: 我会根据当前上下文补充本 agent 负责领域的建议。")
