from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import requests

from .config import Settings


class DeepSeekClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.deepseek_api_key) or bool(self.settings.mimo_api_key)

    @property
    def _use_mimo(self) -> bool:
        """Check if we should use MiMo/Anthropic API for agent tasks."""
        return bool(self.settings.mimo_api_key) and bool(self.settings.mimo_base_url)

    @property
    def _effective_base_url(self) -> str:
        if self._use_mimo:
            return self.settings.mimo_base_url
        return self.settings.deepseek_base_url

    @property
    def _effective_model(self) -> str:
        if self._use_mimo:
            return self.settings.mimo_model
        return self.settings.deepseek_model

    @property
    def _effective_api_key(self) -> str:
        if self._use_mimo:
            return self.settings.mimo_api_key
        return self.settings.deepseek_api_key

    def chat_json(self, system: str, user: str, temperature: float = 0.3, max_tokens: int = 500) -> Optional[Dict[str, Any]]:
        """JSON response - prefers MiMo (agent tasks), falls back to DeepSeek."""
        if not self.enabled:
            return None

        # MiMo first for structured agent tasks (more capable)
        if self._use_mimo:
            return self._chat_anthropic_json(system, user, temperature)

        url = f"{self._effective_base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self._effective_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.settings.deepseek_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.settings.deepseek_thinking_enabled:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = self.settings.deepseek_reasoning_effort
        headers = {
            "Authorization": f"Bearer {self._effective_api_key}",
            "Content-Type": "application/json",
        }
        session = requests.Session()
        session.trust_env = self.settings.deepseek_trust_env
        response = session.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        return _parse_json_object(content)

    def chat(self, system: str, user: str, temperature: float = 0.4, max_tokens: int = 500) -> str:
        """Plain text chat - prefers MiMo, falls back to DeepSeek."""
        if not self.enabled:
            return ""

        # MiMo fallback
        if self._use_mimo:
            return self._chat_anthropic(system, user, temperature, max_tokens)

        url = f"{self._effective_base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self._effective_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._effective_api_key}",
            "Content-Type": "application/json",
        }
        session = requests.Session()
        session.trust_env = self.settings.deepseek_trust_env
        response = session.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"]

    def _chat_anthropic_json(self, system: str, user: str, temperature: float = 0.3) -> Optional[Dict[str, Any]]:
        """Anthropic API format for JSON responses (MiMo)."""
        url = f"{self._effective_base_url}/v1/messages"
        payload: Dict[str, Any] = {
            "model": self._effective_model,
            "max_tokens": 4096,
            "temperature": temperature,
            "system": system,
            "messages": [
                {"role": "user", "content": user + "\n\nPlease respond with valid JSON only."},
            ],
        }
        headers = {
            "x-api-key": self._effective_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        session = requests.Session()
        session.trust_env = self.settings.deepseek_trust_env
        response = session.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        body = response.json()
        # MiMo may return thinking blocks before text blocks
        text_content = ""
        for block in body.get("content", []):
            if block.get("type") == "text":
                text_content = block.get("text", "")
                break
        if not text_content:
            text_content = body["content"][0].get("text", "")
        return _parse_json_object(text_content)

    def _chat_anthropic(self, system: str, user: str, temperature: float = 0.4, max_tokens: int = 500) -> str:
        """Anthropic API format for plain text responses (MiMo)."""
        url = f"{self._effective_base_url}/v1/messages"
        payload: Dict[str, Any] = {
            "model": self._effective_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "x-api-key": self._effective_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        session = requests.Session()
        session.trust_env = self.settings.deepseek_trust_env
        response = session.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        body = response.json()
        # MiMo may return thinking blocks before text blocks
        for block in body.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
        return body["content"][0].get("text", "")


def _parse_json_object(content: str) -> Dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S)
    if fenced:
        return json.loads(fenced.group(1))

    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        return json.loads(content[start : end + 1])
    raise ValueError("LLM response did not contain a JSON object")


def heuristic_listing(raw_text: str) -> Dict[str, Any]:
    title = raw_text.strip().replace("上架：", "").replace("上架:", "")
    title = title.split("，")[0].split(",")[0][:30] or "待上架商品"
    price = _extract_number(raw_text, [r"售价[:：]?\s*([0-9]+(?:\.[0-9]+)?)", r"价格[:：]?\s*([0-9]+(?:\.[0-9]+)?)"])
    stock_value = _extract_number(raw_text, [r"库存[:：]?\s*([0-9]+)"])
    delivery_payload = _extract_after(raw_text, ["自动发货内容：", "自动发货内容:", "发货内容：", "发货内容:"])
    delivery_type = "digital" if delivery_payload else "manual"
    return {
        "title": title,
        "category": "",
        "price": price,
        "stock": int(stock_value) if stock_value is not None else None,
        "condition": "new",
        "description": raw_text.strip(),
        "tags": ["闲鱼", "现货"],
        "image_queries": [title, f"{title} 产品图"],
        "delivery_type": delivery_type,
        "delivery_payload": delivery_payload,
        "faqs": [
            {"q": "什么时候发货？", "a": "拍下付款后系统会自动处理。"},
            {"q": "可以议价吗？", "a": "页面价格为当前优惠价。"},
        ],
    }


def _extract_number(text: str, patterns: List[str]) -> Optional[float]:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def _extract_after(text: str, markers: List[str]) -> str:
    for marker in markers:
        if marker in text:
            return text.split(marker, 1)[1].strip()
    return ""
