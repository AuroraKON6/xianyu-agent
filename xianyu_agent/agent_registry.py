"""Agent registry and task routing.

Central hub for all agents. Routes tasks from OpenClaw to the right agent.
Each agent is an independent module with its own MiMo instance.

Architecture:
    User ↔ OpenClaw ↔ Agent Registry
                         ├── Customer Service (DeepSeek)
                         ├── Listing (MiMo)
                         ├── Discovery (MiMo)
                         ├── Order (MiMo)
                         ├── GitHub (MiMo)
                         └── Analytics (MiMo)
"""
from __future__ import annotations

import logging
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentTask:
    """A task dispatched to an agent."""
    task_id: str
    agent: str
    action: str
    params: Dict[str, Any]
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending, running, completed, failed
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class AgentMessage:
    """A peer-to-peer message from one agent to another."""
    message_id: str
    from_agent: str
    to_agent: str
    action: str
    params: Dict[str, Any]
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    task_id: str = ""
    status: str = "pending"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class AgentDiscussion:
    """A round-robin discussion among agents."""
    discussion_id: str
    topic: str
    participants: List[str]
    rounds: int
    context: Dict[str, Any]
    created_at: float = field(default_factory=time.time)
    transcript: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


class AgentRegistry:
    """Central registry for all agents. Handles task routing from OpenClaw."""

    def __init__(self, history_path: Optional[Path] = None):
        self._agents: Dict[str, Any] = {}  # agent_name -> agent module
        self._task_handlers: Dict[str, Dict[str, Callable]] = {}  # agent -> {action -> handler}
        self._task_history: List[AgentTask] = []
        self._message_history: List[AgentMessage] = []
        self._discussion_history: List[AgentDiscussion] = []
        self._openclaw_callback: Optional[Callable] = None
        self._history_path: Optional[Path] = None
        if history_path is not None:
            self.configure_persistence(history_path)

    def configure_persistence(self, history_path: Path) -> None:
        """Persist collaboration messages and discussions to a JSONL file."""
        self._history_path = Path(history_path)
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_history()

    def register_agent(self, name: str, agent_module: Any) -> None:
        """Register an agent module."""
        self._agents[name] = agent_module
        logger.info("Registered agent: %s", name)

    def register_handler(self, agent_name: str, action: str, handler: Callable) -> None:
        """Register a task handler for an agent action."""
        if agent_name not in self._task_handlers:
            self._task_handlers[agent_name] = {}
        self._task_handlers[agent_name][action] = handler

    def set_openclaw_callback(self, callback: Callable) -> None:
        """Set callback for OpenClaw-facing notifications."""
        self._openclaw_callback = callback

    def dispatch(self, agent: str, action: str, params: Dict[str, Any] = None) -> AgentTask:
        """Dispatch a task to an agent. Returns task object."""
        task_id = f"{agent}_{action}_{int(time.time() * 1000)}"
        task = AgentTask(
            task_id=task_id,
            agent=agent,
            action=action,
            params=params or {},
        )
        self._task_history.append(task)

        # Find handler
        handler = self._task_handlers.get(agent, {}).get(action)
        if handler:
            task.status = "running"
            try:
                import inspect
                sig = inspect.signature(handler)
                params = params or {}
                # If handler accepts **kwargs or a single dict param, pass dict directly
                # Otherwise, unpack dict as keyword arguments
                has_var_keyword = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                positional_params = [
                    p for p in sig.parameters.values()
                    if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
                ]
                if len(positional_params) == 0:
                    # No params besides self - call without args
                    result = handler()
                elif len(positional_params) == 1 and positional_params[0].annotation in (dict, Dict, Dict[str, Any], inspect.Parameter.empty):
                    result = handler(params)
                elif has_var_keyword or len(positional_params) > 1:
                    result = handler(**params)
                else:
                    result = handler(params)
                task.result = result
                task.status = "completed"
            except Exception as e:
                task.error = str(e)
                task.status = "failed"
                logger.error("Task %s failed: %s", task_id, e)
        else:
            task.error = f"No handler for {agent}/{action}"
            task.status = "failed"

        return task

    def send_message(
        self,
        from_agent: str,
        to_agent: str,
        action: str,
        params: Dict[str, Any] = None,
        reason: str = "",
    ) -> AgentTask:
        """Send a peer message and dispatch it as an agent task."""
        message = AgentMessage(
            message_id=f"msg_{from_agent}_{to_agent}_{int(time.time() * 1000)}",
            from_agent=from_agent,
            to_agent=to_agent,
            action=action,
            params=params or {},
            reason=reason,
        )
        task = self.dispatch(to_agent, action, params or {})
        message.task_id = task.task_id
        message.status = task.status
        message.result = task.result
        message.error = task.error
        self._message_history.append(message)
        self._persist_event("message", self._message_to_dict(message))
        return task

    def discuss(
        self,
        topic: str,
        participants: List[str],
        rounds: int = 2,
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Run a round-robin discussion among registered agents."""
        safe_rounds = max(1, min(int(rounds or 1), 5))
        discussion = AgentDiscussion(
            discussion_id=f"discussion_{int(time.time() * 1000)}",
            topic=topic,
            participants=list(participants or []),
            rounds=safe_rounds,
            context=context or {},
        )

        for round_index in range(safe_rounds):
            for agent_name in discussion.participants:
                agent = self._agents.get(agent_name)
                if agent is None:
                    turn = {
                        "round": round_index + 1,
                        "agent": agent_name,
                        "status": "failed",
                        "content": f"Agent {agent_name} is not registered",
                        "created_at": time.time(),
                    }
                    discussion.transcript.append(turn)
                    continue

                try:
                    content = agent.contribute_to_discussion(
                        discussion.topic,
                        list(discussion.transcript),
                        context=discussion.context,
                    )
                    status = "completed"
                except Exception as exc:
                    content = str(exc)
                    status = "failed"
                    logger.error("Discussion turn failed for %s: %s", agent_name, exc)

                discussion.transcript.append(
                    {
                        "round": round_index + 1,
                        "agent": agent_name,
                        "status": status,
                        "content": content,
                        "created_at": time.time(),
                    }
                )

        discussion.summary = self._summarize_discussion(discussion)
        self._discussion_history.append(discussion)
        self._persist_event("discussion", self._discussion_to_dict(discussion))
        return self._discussion_to_dict(discussion)

    def _summarize_discussion(self, discussion: AgentDiscussion) -> str:
        completed = [t for t in discussion.transcript if t.get("status") == "completed"]
        if not completed:
            return "No agent completed a discussion turn."
        last_points = []
        for turn in completed[-len(discussion.participants or completed):]:
            content = str(turn.get("content", "")).strip()
            if len(content) > 160:
                content = content[:157] + "..."
            last_points.append(f"{turn.get('agent')}: {content}")
        return "\n".join(last_points)

    def _discussion_to_dict(self, discussion: AgentDiscussion) -> Dict[str, Any]:
        return {
            "discussion_id": discussion.discussion_id,
            "topic": discussion.topic,
            "participants": discussion.participants,
            "rounds": discussion.rounds,
            "context": discussion.context,
            "created_at": discussion.created_at,
            "transcript": discussion.transcript,
            "summary": discussion.summary,
        }

    def _message_to_dict(self, message: AgentMessage) -> Dict[str, Any]:
        return {
            "message_id": message.message_id,
            "from_agent": message.from_agent,
            "to_agent": message.to_agent,
            "action": message.action,
            "params": message.params,
            "reason": message.reason,
            "created_at": message.created_at,
            "task_id": message.task_id,
            "status": message.status,
            "result": message.result,
            "error": message.error,
        }

    def _persist_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if self._history_path is None:
            return
        record = {
            "kind": kind,
            "created_at": time.time(),
            "payload": payload,
        }
        try:
            with self._history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            logger.warning("Failed to persist agent collaboration event: %s", exc)

    def _load_history(self) -> None:
        if self._history_path is None or not self._history_path.exists():
            return
        message_ids = {m.message_id for m in self._message_history}
        discussion_ids = {d.discussion_id for d in self._discussion_history}
        try:
            lines = self._history_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning("Failed to load agent collaboration history: %s", exc)
            return

        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed agent collaboration history line")
                continue
            payload = record.get("payload") or {}
            if record.get("kind") == "message":
                message = self._message_from_dict(payload)
                if message.message_id and message.message_id not in message_ids:
                    self._message_history.append(message)
                    message_ids.add(message.message_id)
            elif record.get("kind") == "discussion":
                discussion = self._discussion_from_dict(payload)
                if discussion.discussion_id and discussion.discussion_id not in discussion_ids:
                    self._discussion_history.append(discussion)
                    discussion_ids.add(discussion.discussion_id)

    def _message_from_dict(self, payload: Dict[str, Any]) -> AgentMessage:
        return AgentMessage(
            message_id=str(payload.get("message_id") or ""),
            from_agent=str(payload.get("from_agent") or ""),
            to_agent=str(payload.get("to_agent") or ""),
            action=str(payload.get("action") or ""),
            params=dict(payload.get("params") or {}),
            reason=str(payload.get("reason") or ""),
            created_at=float(payload.get("created_at") or time.time()),
            task_id=str(payload.get("task_id") or ""),
            status=str(payload.get("status") or "completed"),
            result=payload.get("result"),
            error=payload.get("error"),
        )

    def _discussion_from_dict(self, payload: Dict[str, Any]) -> AgentDiscussion:
        return AgentDiscussion(
            discussion_id=str(payload.get("discussion_id") or ""),
            topic=str(payload.get("topic") or ""),
            participants=list(payload.get("participants") or []),
            rounds=int(payload.get("rounds") or 1),
            context=dict(payload.get("context") or {}),
            created_at=float(payload.get("created_at") or time.time()),
            transcript=list(payload.get("transcript") or []),
            summary=str(payload.get("summary") or ""),
        )

    def notify_openclaw(self, message: str, data: Dict[str, Any] = None) -> bool:
        """Send a notification to the OpenClaw-facing callback."""
        if self._openclaw_callback:
            try:
                self._openclaw_callback(message, data)
                return True
            except Exception as e:
                logger.error("Failed to notify OpenClaw: %s", e)
        return False

    def get_status(self) -> Dict[str, Any]:
        """Get registry status."""
        return {
            "registered_agents": list(self._agents.keys()),
            "handlers": {
                agent: list(actions.keys())
                for agent, actions in self._task_handlers.items()
            },
            "total_tasks": len(self._task_history),
            "collaboration_history_path": str(self._history_path) if self._history_path else "",
            "recent_tasks": [
                {
                    "task_id": t.task_id,
                    "agent": t.agent,
                    "action": t.action,
                    "status": t.status,
                }
                for t in self._task_history[-10:]
            ],
        }

    def get_task_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent task history."""
        return [
            {
                "task_id": t.task_id,
                "agent": t.agent,
                "action": t.action,
                "status": t.status,
                "created_at": t.created_at,
                "result": t.result,
                "error": t.error,
            }
            for t in self._task_history[-limit:]
        ]

    def get_message_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent agent-to-agent messages."""
        return [self._message_to_dict(m) for m in self._message_history[-limit:]]

    def get_discussion_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent multi-agent discussions."""
        return [self._discussion_to_dict(d) for d in self._discussion_history[-limit:]]


# Global singleton
registry = AgentRegistry()
