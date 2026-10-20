from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List

from .models import now_iso
from .storage import JsonStorage


DEFAULT_MEMORY: Dict[str, Any] = {
    "agent_name": "闲鱼Agent",
    "owner": "用户",
    "primary_goal": "根据用户描述自动生成合规闲鱼商品草稿，辅助上架、比价、授权校验、自动发货和每日擦亮。",
    "integrations": {
        "llm": "DeepSeek V4",
        "agent_container": "OpenClaw",
        "approval_channel": "Feishu",
        "publishing_adapter": "stub until XianGuanJia credentials are configured",
    },
    "listing_style": [
        "标题短、清楚、少夸张，尽量 30 个中文字符以内。",
        "描述按手机端阅读排版，先讲适用人群、内容/规格、交付方式，再写售后边界。",
        "FAQ 要提前回答发货、格式、适用范围、是否实物、是否支持退款等问题，减少客服压力。",
        "默认不要使用 emoji 或花哨符号，除非用户明确要求。",
        "不编造品牌授权、官方背书、证书、稀缺性、疗效、收益承诺或平台无法验证的承诺。",
    ],
    "commerce_rules": [],
    "approval_rules": [],
    "user_preferences": [
        "希望桌面有程序入口。",
        "希望 agent 保留记忆。",
        "希望尽量减少人工客服。",
    ],
    "notes": [],
    "updated_at": "",
}


class AgentMemory:
    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage

    def load(self) -> Dict[str, Any]:
        if not self.storage.memory_profile_path.exists():
            memory = deepcopy(DEFAULT_MEMORY)
            memory["updated_at"] = now_iso()
            self.save(memory)
            return memory
        data = json.loads(self.storage.memory_profile_path.read_text(encoding="utf-8"))
        return merge_defaults(data)

    def peek(self) -> Dict[str, Any]:
        """R04 read-only variant of load(): same view, never persists.

        Query surfaces (health, dashboards) must not create the profile
        file as a side effect of being read; materializing defaults on
        disk belongs to explicit writes (remember/save).
        """
        if not self.storage.memory_profile_path.exists():
            memory = deepcopy(DEFAULT_MEMORY)
            memory["updated_at"] = ""
            return memory
        data = json.loads(self.storage.memory_profile_path.read_text(encoding="utf-8"))
        return merge_defaults(data)

    def save(self, memory: Dict[str, Any]) -> Dict[str, Any]:
        memory["updated_at"] = now_iso()
        self.storage.memory_profile_path.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return memory

    def remember(self, text: str, source: str = "manual") -> Dict[str, Any]:
        clean = text.strip()
        if not clean:
            return self.load()
        memory = self.load()
        note = {"text": clean, "source": source, "created_at": now_iso()}
        notes: List[Dict[str, Any]] = list(memory.get("notes") or [])
        notes.append(note)
        memory["notes"] = notes[-100:]
        self.save(memory)
        with self.storage.memory_events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "remember", **note}, ensure_ascii=False) + "\n")
        return memory

    def context_for_prompt(self) -> str:
        memory = self.load()
        compact = {
            "agent_name": memory.get("agent_name"),
            "primary_goal": memory.get("primary_goal"),
            "integrations": memory.get("integrations"),
            "listing_style": memory.get("listing_style"),
            "commerce_rules": memory.get("commerce_rules"),
            "approval_rules": memory.get("approval_rules"),
            "user_preferences": memory.get("user_preferences"),
            "recent_notes": list(memory.get("notes") or [])[-20:],
        }
        return json.dumps(compact, ensure_ascii=False, indent=2)


def merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(DEFAULT_MEMORY)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    if not merged.get("updated_at"):
        merged["updated_at"] = now_iso()
    return merged


def looks_like_memory_request(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.startswith(("记忆:", "记忆：", "remember:", "偏好:", "偏好："))


def memory_text_from_request(text: str) -> str:
    for marker in ("记忆：", "记忆:", "remember:", "偏好：", "偏好:"):
        if text.lower().startswith(marker.lower()):
            return text[len(marker) :].strip()
    return text.strip()
