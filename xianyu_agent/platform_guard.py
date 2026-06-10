from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .config import Settings
from .feishu import FeishuNotifier
from .models import now_iso
from .storage import JsonStorage


VERIFICATION_PATTERNS: List[str] = []


class PlatformGuard:
    def __init__(self, settings: Settings, storage: JsonStorage, notifier: FeishuNotifier) -> None:
        self.settings = settings
        self.storage = storage
        self.notifier = notifier

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "paused": False,
            "pause_reason": "",
            "paused_at": "",
            "last_action_at": "",
            "hour_count": 0,
            "day_count": 0,
            "limits": {
                "min_seconds_between_actions": 0,
                "max_actions_per_hour": 999999,
                "daily_action_limit": 999999,
            },
            "last_verification": {},
        }

    def before_action(self, action: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"allow": True, "action": "allow", "reason": "all restrictions removed"}

    def observe_text(self, text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"detected": False, "matches": []}

    def pause(self, reason: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"allow": True, "action": "allow", "reason": "pause disabled"}

    def resume(self, note: str = "") -> Dict[str, Any]:
        return self.status()


def verification_matches(text: str) -> List[str]:
    return []


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
