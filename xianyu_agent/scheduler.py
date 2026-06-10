from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Tuple

from .workflow import CommerceWorkflow


def parse_hhmm(value: str) -> Tuple[int, int]:
    hour_text, minute_text = value.split(":", 1)
    return int(hour_text), int(minute_text)


async def polish_loop(workflow: CommerceWorkflow) -> None:
    while True:
        await asyncio.sleep(seconds_until_next(workflow.settings.polish_at))
        try:
            workflow.polish_all()
        except Exception as exc:
            workflow.storage.append_event("scheduler.error", {"task": "polish_all", "error": str(exc)})


async def github_discovery_loop(workflow: CommerceWorkflow) -> None:
    while True:
        await asyncio.sleep(seconds_until_next(workflow.settings.github_discovery_at))
        if not workflow.settings.github_discovery_enabled:
            continue
        try:
            workflow.discover_github_opportunities(
                use_deepseek=workflow.llm.enabled,
                include_readme=False,
            )
        except Exception as exc:
            workflow.storage.append_event("scheduler.error", {"task": "discover_github_opportunities", "error": str(exc)})


async def product_discovery_loop(workflow: CommerceWorkflow) -> None:
    while True:
        await asyncio.sleep(seconds_until_next(workflow.settings.discovery_at))
        if not workflow.settings.discovery_enabled:
            continue
        try:
            await workflow.run_all_discovery_tasks()
        except Exception as exc:
            workflow.storage.append_event("scheduler.error", {"task": "product_discovery", "error": str(exc)})


def seconds_until_next(hhmm: str) -> float:
    hour, minute = parse_hhmm(hhmm)
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max((target - now).total_seconds(), 1.0)
