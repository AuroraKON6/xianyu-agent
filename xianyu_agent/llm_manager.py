"""Multi-agent LLM manager.

Each agent gets an independent LLM client:
- Customer Service Agent -> DeepSeek V4 Flash (cloud, fast)
- Specialist commerce agents -> MiMo V2.5 Pro (cloud, powerful, independent API keys)

Usage:
    from .llm_manager import llm_manager
    client = llm_manager.get_client("customer_service")
    reply = client.chat(system_prompt, user_message)
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Dict, Optional

from .config import Settings
from .llm import DeepSeekClient

logger = logging.getLogger(__name__)

# Agent definitions with their model backend and API key
AGENT_DEFINITIONS = {
    "customer_service": {
        "description": "Auto-reply to Xianyu buyers",
        "backend": "deepseek",
        "key_field": None,
    },
    "listing": {
        "description": "Create and polish product listings",
        "backend": "mimo",
        "key_field": "mimo_listing_key",
    },
    "discovery": {
        "description": "Market research and product discovery",
        "backend": "mimo",
        "key_field": "mimo_discovery_key",
    },
    "order": {
        "description": "Order status, shipping, confirmations",
        "backend": "mimo",
        "key_field": "mimo_order_key",
    },
    "github": {
        "description": "GitHub project discovery and analysis",
        "backend": "mimo",
        "key_field": "mimo_github_key",
    },
    "analytics": {
        "description": "Sales analysis, profit calculation, trends",
        "backend": "mimo",
        "key_field": "mimo_analytics_key",
    },
    "resale_research": {
        "description": "Supplier info, competitor analysis, demand signals",
        "backend": "deepseek",
        "key_field": None,
    },
    "resale_risk": {
        "description": "Prohibited items, IP, after-sales, logistics, margin risk",
        "backend": "deepseek",
        "key_field": None,
    },
    "resale_pricing": {
        "description": "Pricing strategy, margin optimization, competitor pricing",
        "backend": "deepseek",
        "key_field": None,
    },
}


class AgentLLMClient:
    """LLM client configured for a specific agent."""

    def __init__(self, agent_name: str, settings: Settings) -> None:
        self.agent_name = agent_name
        self.definition = AGENT_DEFINITIONS[agent_name]
        self._settings = settings
        self._client: Optional[DeepSeekClient] = None

    @property
    def client(self) -> DeepSeekClient:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def _create_client(self) -> DeepSeekClient:
        """Create a DeepSeekClient configured for this agent's backend."""
        s = self._settings
        backend = self.definition["backend"]
        key_field = self.definition.get("key_field")

        if backend == "deepseek":
            # DeepSeek: use deepseek API key, force deepseek-v4-flash, clear MiMo
            agent_settings = replace(
                s,
                deepseek_model="deepseek-v4-flash",
                mimo_api_key="",
                mimo_base_url="",
                mimo_model="",
                mimo_hermes_key="",
                mimo_listing_key="",
                mimo_discovery_key="",
                mimo_order_key="",
                mimo_github_key="",
                mimo_analytics_key="",
            )
        else:
            # MiMo: use agent-specific API key
            agent_key = getattr(s, key_field, "") if key_field else s.mimo_api_key
            agent_settings = replace(
                s,
                deepseek_api_key="",
                mimo_api_key=agent_key,
                mimo_base_url=s.mimo_base_url,
                mimo_model=s.mimo_model,
            )

        return DeepSeekClient(agent_settings)

    def chat(self, system: str, user: str, temperature: float = 0.4, max_tokens: int = 500) -> str:
        """Plain text chat."""
        return self.client.chat(system, user, temperature, max_tokens)

    def chat_json(self, system: str, user: str, temperature: float = 0.3, **kwargs):
        """JSON response."""
        return self.client.chat_json(system, user, temperature, **kwargs)


class LLManager:
    """Manages LLM clients for all agents."""

    def __init__(self) -> None:
        self._settings: Optional[Settings] = None
        self._clients: Dict[str, AgentLLMClient] = {}

    def init(self, settings: Settings) -> None:
        self._settings = settings
        self._clients.clear()
        logger.info("LLMManager initialized with %d agent definitions", len(AGENT_DEFINITIONS))

    def get_client(self, agent_name: str) -> AgentLLMClient:
        if agent_name not in AGENT_DEFINITIONS:
            raise ValueError(f"Unknown agent: {agent_name}. Available: {list(AGENT_DEFINITIONS.keys())}")
        if agent_name not in self._clients:
            if self._settings is None:
                raise RuntimeError("LLManager not initialized. Call init(settings) first.")
            self._clients[agent_name] = AgentLLMClient(agent_name, self._settings)
        return self._clients[agent_name]

    def list_agents(self):
        """List all registered agents and their status."""
        result = []
        for name, defn in AGENT_DEFINITIONS.items():
            if defn["backend"] == "deepseek":
                model = "deepseek-v4-flash"
            elif self._settings:
                model = self._settings.mimo_model
            else:
                model = "unknown"
            entry = {
                "name": name,
                "description": defn["description"],
                "backend": defn["backend"],
                "model": model,
            }
            if name in self._clients:
                entry["status"] = "active"
            else:
                entry["status"] = "idle"
            result.append(entry)
        return result


# Global singleton
llm_manager = LLManager()
