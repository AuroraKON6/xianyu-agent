from __future__ import annotations

from typing import Any, Dict, List


REQUIRED_PROMPT_KEYS = ("classify", "price", "tech", "default", "after_sales")
OPTIONAL_PROMPT_KEYS = ("bargain", "price_compare", "proactive", "no_reply")
SUPPORTED_PROMPT_KEYS = REQUIRED_PROMPT_KEYS + OPTIONAL_PROMPT_KEYS

DEFAULT_PROMPT_TEMPLATES = {
    "classify": (
        "Classify the buyer intent for a Xianyu seller conversation. Return only the intent label, "
        "risk flags, and whether operator approval is needed."
    ),
    "price": (
        "Reply as a concise seller. Keep the price position clear, explain value, and do not exceed "
        "the configured bargain limits."
    ),
    "tech": (
        "Answer product specification questions using only known listing and inventory facts. Ask the "
        "operator for missing or uncertain details."
    ),
    "default": (
        "Draft a friendly Xianyu seller reply that moves the buyer to the next concrete step while "
        "keeping promises limited to verified listing facts."
    ),
    "after_sales": (
        "Handle after-sales messages calmly. Identify the issue type, request evidence when needed, "
        "and escalate refund, dispute, or compensation decisions for approval."
    ),
}


def build_prompt_profile(ai_settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return an operator-facing AI prompt configuration profile.

    The profile intentionally exposes prompt keys and lengths only, not prompt
    bodies, API keys, base URLs, cookies, or any other credential-like fields.
    """
    settings = dict(ai_settings or {})
    prompts = settings.get("custom_prompts") or {}
    if not isinstance(prompts, dict):
        prompts = {}

    prompt_keys = {str(key) for key in prompts.keys()}
    supported = set(SUPPORTED_PROMPT_KEYS)
    required = set(REQUIRED_PROMPT_KEYS)
    enabled = bool(settings.get("ai_enabled", False))

    configured_keys = sorted(prompt_keys & supported)
    missing_required_keys = sorted(required - prompt_keys)
    unknown_keys = sorted(prompt_keys - supported)
    ready = enabled and not missing_required_keys

    profile = {
        "enabled": enabled,
        "ready": ready,
        "model_name": str(settings.get("model_name") or ""),
        "required_keys": list(REQUIRED_PROMPT_KEYS),
        "optional_keys": list(OPTIONAL_PROMPT_KEYS),
        "supported_keys": list(SUPPORTED_PROMPT_KEYS),
        "configured_keys": configured_keys,
        "configured_required_keys": sorted(prompt_keys & required),
        "missing_required_keys": missing_required_keys,
        "unknown_keys": unknown_keys,
        "prompt_lengths": {
            key: len(str(prompts.get(key) or ""))
            for key in configured_keys
        },
        "limits": {
            "max_discount_percent": int(settings.get("max_discount_percent") or 0),
            "max_discount_amount": int(settings.get("max_discount_amount") or 0),
            "max_bargain_rounds": int(settings.get("max_bargain_rounds") or 0),
        },
    }
    profile["recommended_next_actions"] = _recommended_next_actions(profile)
    return profile


def build_bootstrap_ai_reply_settings(ai_settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return AI settings with required prompt keys populated.

    Existing secrets, limits, model settings, and custom prompt bodies are
    preserved. Missing required prompt bodies are filled with conservative
    defaults so the operator can edit them later from the normal settings path.
    """
    settings = dict(ai_settings or {})
    prompts = settings.get("custom_prompts") or {}
    if not isinstance(prompts, dict):
        prompts = {}
    prompts = {str(key): str(value) for key, value in prompts.items()}
    for key, body in DEFAULT_PROMPT_TEMPLATES.items():
        prompts.setdefault(key, body)

    return {
        "ai_enabled": True,
        "model_name": str(settings.get("model_name") or "deepseek-v4-flash"),
        "api_key": str(settings.get("api_key") or ""),
        "base_url": str(settings.get("base_url") or "https://api.deepseek.com"),
        "max_discount_percent": int(settings.get("max_discount_percent") or 10),
        "max_discount_amount": int(settings.get("max_discount_amount") or 100),
        "max_bargain_rounds": int(settings.get("max_bargain_rounds") or 3),
        "custom_prompts": prompts,
    }


def _recommended_next_actions(profile: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    if not profile.get("enabled"):
        actions.append("Enable AI reply for this account before relying on custom prompts.")
    missing = profile.get("missing_required_keys") or []
    if missing:
        actions.append("Configure required prompt keys: " + ", ".join(missing) + ".")
    unknown = profile.get("unknown_keys") or []
    if unknown:
        actions.append("Remove or rename unsupported prompt keys: " + ", ".join(unknown) + ".")
    if not actions:
        actions.append("Prompt profile is ready for monitored AI reply drafts.")
    return actions
