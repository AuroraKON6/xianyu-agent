from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List


def build_account_profiles(db) -> List[Dict[str, Any]]:
    """Build operator-facing account profiles without exposing credentials."""
    cookies = db.get_all_cookies()
    statuses = db.get_all_cookie_status()
    profiles: List[Dict[str, Any]] = []

    for cookie_id in sorted(cookies):
        details = db.get_cookie_details(cookie_id) or {}
        keywords = db.get_keywords(cookie_id)
        ai_settings = db.get_ai_reply_settings(cookie_id) or {}
        orders = db.get_orders_by_cookie(cookie_id)
        items = db.get_items_by_cookie(cookie_id)
        sync_freshness = {}
        if hasattr(db, "get_account_sync_state"):
            sync_freshness = db.get_account_sync_state(cookie_id)
        orders_by_status = dict(Counter(str(order.get("status") or "unknown") for order in orders))

        enabled = bool(statuses.get(cookie_id, True))
        auto_confirm = bool(details.get("auto_confirm", 1))
        ai_enabled = bool(ai_settings.get("ai_enabled", False))
        custom_prompts = ai_settings.get("custom_prompts") or {}
        keyword_count = len(keywords)

        capabilities = {
            "seller_auto_reply": enabled and (ai_enabled or keyword_count > 0),
            "keyword_reply": keyword_count > 0,
            "ai_reply": enabled and ai_enabled,
            "order_fulfillment_tracking": len(orders) > 0 or auto_confirm,
            "listing_inventory_tracking": len(items) > 0,
        }

        profile = {
            "cookie_id": cookie_id,
            "enabled": enabled,
            "remark": details.get("remark", ""),
            "auto_confirm": auto_confirm,
            "pause_duration": int(details.get("pause_duration") or 10),
            "role_modes": {
                "seller": True,
                "buyer": False,
            },
            "capabilities": capabilities,
            "counts": {
                "keyword_rules": keyword_count,
                "items": len(items),
                "orders_total": len(orders),
                "orders_by_status": orders_by_status,
            },
            "sync_freshness": sync_freshness,
            "ai": {
                "enabled": ai_enabled,
                "model_name": ai_settings.get("model_name", ""),
                "custom_prompt_keys": sorted(str(key) for key in custom_prompts.keys()),
                "max_discount_percent": int(ai_settings.get("max_discount_percent") or 0),
                "max_discount_amount": int(ai_settings.get("max_discount_amount") or 0),
                "max_bargain_rounds": int(ai_settings.get("max_bargain_rounds") or 0),
            },
        }
        profile["recommended_next_actions"] = _recommended_next_actions(profile)
        profiles.append(profile)

    return profiles


def _recommended_next_actions(profile: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    counts = profile.get("counts", {})
    capabilities = profile.get("capabilities", {})
    ai = profile.get("ai", {})

    if not profile.get("enabled"):
        actions.append("Enable the account before running live monitoring.")
    if not capabilities.get("seller_auto_reply"):
        actions.append("Add keyword rules or enable AI reply for this account.")
    if counts.get("items", 0) == 0:
        actions.append("Sync or create listings so the account has inventory context.")
    if ai.get("enabled") and not ai.get("custom_prompt_keys"):
        actions.append("Add account-specific prompts for price, tech, and default reply styles.")
    if counts.get("orders_total", 0) == 0:
        actions.append("Run a small validation cycle before scaling this account.")
    if not actions:
        actions.append("Account profile is ready for monitored seller-mode operation.")
    return actions
