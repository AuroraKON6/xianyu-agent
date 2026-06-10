"""Ruflo multi-agent orchestration integration.

Provides MCP tool interface for Ruflo to orchestrate the xianyu agent
alongside the OpenClaw direct router.

Key capabilities exposed to Ruflo:
- agent_spawn: Spawn xianyu agent tasks
- memory_store/memory_search: Persistent memory via database
- hooks_route: Event routing between agents
- workflow_engine: Commerce workflow execution
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from .db_manager import db_manager

if TYPE_CHECKING:
    from .workflow import CommerceWorkflow


class RufloIntegration:
    """Ruflo MCP tool interface for xianyu agent."""

    def __init__(self, workflow: "CommerceWorkflow" = None):
        self.workflow = workflow
        self._memory_store: Dict[str, Dict[str, Any]] = {}
        self._hooks: Dict[str, List[callable]] = {}
        self._agents: Dict[str, Any] = {}

    def set_workflow(self, workflow: "CommerceWorkflow"):
        """Set the commerce workflow instance."""
        self.workflow = workflow

    # ---- Memory Store/Recall ----

    def memory_store(self, key: str, value: Any, namespace: str = "default", ttl: int = 0) -> Dict[str, Any]:
        """Store a value in persistent memory."""
        entry = {
            "key": key,
            "value": value,
            "namespace": namespace,
            "stored_at": time.time(),
            "ttl": ttl,
            "expires_at": time.time() + ttl if ttl > 0 else 0,
        }
        self._memory_store[f"{namespace}:{key}"] = entry

        # Also persist to database
        try:
            db_manager.save_conversation(
                cookie_id="ruflo",
                chat_id=f"memory:{namespace}",
                user_id="system",
                item_id=key,
                role="system",
                content=json.dumps(value, ensure_ascii=False),
                intent="memory_store",
            )
        except Exception as e:
            logger.warning(f"Failed to persist memory to DB: {e}")

        logger.info(f"Memory stored: {namespace}:{key}")
        return {"ok": True, "key": key, "namespace": namespace}

    def memory_search(self, query: str, namespace: str = "default", limit: int = 10) -> List[Dict[str, Any]]:
        """Search memory by query."""
        results = []
        current_time = time.time()

        for store_key, entry in self._memory_store.items():
            # Filter by namespace
            if not store_key.startswith(f"{namespace}:"):
                continue
            # Check TTL
            if entry.get("expires_at", 0) > 0 and current_time > entry["expires_at"]:
                continue
            # Search in key and value
            value_str = json.dumps(entry["value"], ensure_ascii=False) if not isinstance(entry["value"], str) else entry["value"]
            if query.lower() in entry["key"].lower() or query.lower() in value_str.lower():
                results.append(entry)

        return results[:limit]

    def memory_get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Get a specific memory entry."""
        entry = self._memory_store.get(f"{namespace}:{key}")
        if not entry:
            return None
        # Check TTL
        if entry.get("expires_at", 0) > 0 and time.time() > entry["expires_at"]:
            del self._memory_store[f"{namespace}:{key}"]
            return None
        return entry["value"]

    # ---- Hooks/Event Routing ----

    def hooks_register(self, event: str, handler: callable):
        """Register a hook handler for an event."""
        if event not in self._hooks:
            self._hooks[event] = []
        self._hooks[event].append(handler)
        logger.info(f"Hook registered for event: {event}")

    async def hooks_route(self, event: str, data: Dict[str, Any]) -> List[Any]:
        """Route an event to all registered handlers."""
        results = []
        handlers = self._hooks.get(event, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(data)
                else:
                    result = handler(data)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook handler error for {event}: {e}")
                results.append({"error": str(e)})
        return results

    # ---- Agent Spawn ----

    async def agent_spawn(self, task: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Spawn an agent task."""
        if not self.workflow:
            return {"error": "Workflow not initialized"}

        params = params or {}
        logger.info(f"Agent spawn: {task}")

        try:
            if task == "create_listing":
                text = params.get("text", "")
                result = self.workflow.create_listing(text, source="ruflo")
                return {"ok": True, "task": task, "result": result}

            elif task == "polish_all":
                result = self.workflow.polish_all()
                return {"ok": True, "task": task, "result": result}

            elif task == "auto_reply":
                buyer_id = params.get("buyer_id", "")
                message = params.get("message", "")
                product_id = params.get("product_id", "")
                reply = self.workflow.generate_auto_reply(
                    buyer_id=buyer_id, message=message, product_id=product_id
                )
                return {"ok": True, "task": task, "reply": reply}

            elif task == "github_discovery":
                queries = params.get("queries", [])
                result = self.workflow.discover_github_opportunities(queries=queries)
                return {"ok": True, "task": task, "result": result}

            elif task == "product_discovery":
                keyword = params.get("keyword", "")
                result = await self.workflow.run_discovery(keyword=keyword)
                return {"ok": True, "task": task, "result": result}

            elif task == "order_status":
                order_id = params.get("order_id", "")
                from .order_status_handler import order_status_handler
                order = db_manager.get_order(order_id)
                return {"ok": True, "task": task, "order": order}

            elif task == "account_status":
                cookies = db_manager.get_all_cookies()
                statuses = db_manager.get_all_cookie_status()
                return {
                    "ok": True,
                    "task": task,
                    "accounts": len(cookies),
                    "enabled": sum(1 for v in statuses.values() if v),
                }

            else:
                return {"error": f"Unknown task: {task}"}

        except Exception as e:
            logger.error(f"Agent spawn error: {e}")
            return {"error": str(e)}

    # ---- Swarm Coordination ----

    def swarm_status(self) -> Dict[str, Any]:
        """Get current swarm/agent status."""
        return {
            "agent": "xianyu",
            "status": "ready",
            "memory_entries": len(self._memory_store),
            "hooks_registered": len(self._hooks),
            "workflow_ready": self.workflow is not None,
            "db_path": db_manager.db_path,
            "accounts": len(db_manager.get_all_cookies()),
        }

    def swarm_capabilities(self) -> List[str]:
        """List capabilities this agent provides to the swarm."""
        return [
            "create_listing",
            "polish_all",
            "auto_reply",
            "github_discovery",
            "product_discovery",
            "order_status",
            "account_status",
            "memory_store",
            "memory_search",
        ]


# Global singleton
ruflo = RufloIntegration()
