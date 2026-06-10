"""Agent scheduler - automated tasks for each agent.

Runs alongside the server, triggering agents at scheduled times.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict

from .config import Settings
from .scheduler import seconds_until_next

logger = logging.getLogger(__name__)


def _notify_user(agents: Dict, message: str) -> bool:
    """Send a user notification through the shared workflow notifier."""
    for agent in agents.values():
        try:
            workflow = agent.workflow
            return bool(workflow.notifier.send_text(message))
        except RuntimeError:
            continue
        except Exception as e:
            logger.error("User notification failed: %s", e)
            return False
    return False


async def listing_polish_loop(agents: Dict, settings: Settings) -> None:
    """Daily listing polish via ListingAgent."""
    while True:
        await asyncio.sleep(seconds_until_next(settings.polish_at))
        try:
            agent = agents.get("listing")
            if agent:
                result = agent.polish_all()
                logger.info("Listing polish: %s", result)
        except Exception as e:
            logger.error("Listing polish failed: %s", e)


async def discovery_loop(agents: Dict, settings: Settings) -> None:
    """Daily product discovery via DiscoveryAgent."""
    while True:
        await asyncio.sleep(seconds_until_next(settings.discovery_at))
        try:
            agent = agents.get("discovery")
            if agent:
                tasks = agent.list_tasks()
                logger.info("Discovery tasks: %s", tasks)
        except Exception as e:
            logger.error("Discovery loop failed: %s", e)


async def github_scan_loop(agents: Dict, settings: Settings) -> None:
    """Daily GitHub opportunity scan via GitHubAgent."""
    if not settings.github_discovery_enabled:
        return
    while True:
        await asyncio.sleep(seconds_until_next(settings.github_discovery_at))
        try:
            agent = agents.get("github")
            if agent:
                result = agent.find_opportunities()
                logger.info("GitHub scan: %s", result)
        except Exception as e:
            logger.error("GitHub scan failed: %s", e)


async def analytics_daily_loop(agents: Dict, settings: Settings) -> None:
    """Daily analytics report via AnalyticsAgent."""
    while True:
        await asyncio.sleep(seconds_until_next("23:00"))
        try:
            agent = agents.get("analytics")
            if agent:
                report = agent.generate_report()
                logger.info("Daily report generated")
                if report.get("report"):
                    _notify_user(agents, f"📊 每日运营报告\n{report['report'][:500]}")
        except Exception as e:
            logger.error("Analytics daily failed: %s", e)


async def order_check_loop(agents: Dict) -> None:
    """Hourly order status check via OrderAgent."""
    while True:
        await asyncio.sleep(3600)  # Every hour
        try:
            agent = agents.get("order")
            if agent:
                result = agent.list_orders({"status": "pending_ship"})
                count = result.get("count", 0)
                if count > 0:
                    _notify_user(agents, f"⚠️ 有 {count} 个待发货订单")
        except Exception as e:
            logger.error("Order check failed: %s", e)


async def start_agent_scheduler(agents: Dict, settings: Settings) -> None:
    """Start all agent scheduler loops."""
    loops = [
        listing_polish_loop(agents, settings),
        discovery_loop(agents, settings),
        github_scan_loop(agents, settings),
        analytics_daily_loop(agents, settings),
        order_check_loop(agents),
    ]
    logger.info("Agent scheduler started with %d loops", len(loops))
    await asyncio.gather(*loops)
