from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, Iterable, Mapping, Optional


def sync_account_snapshot(
    *,
    db: Any,
    adapter: Any,
    cookie_id: str,
    status: str = "",
    order_runtime_store: Any = None,
) -> Dict[str, Any]:
    """Sync orders and listings through the active adapter, then persist a safe local snapshot."""
    adapter_name = str(getattr(adapter, "name", "unknown") or "unknown")
    orders_result = _call_sync(adapter.sync_orders, cookie_id=cookie_id, status=status)
    listings_result = _call_sync(adapter.sync_my_listings, cookie_id=cookie_id)

    orders = _records_from_result(orders_result, "orders")
    listings = _records_from_result(listings_result, "items", "listings")

    persisted_orders = _persist_orders(db, cookie_id, orders)
    runtime_orders = _persist_order_runtime(order_runtime_store, cookie_id, orders, adapter_name)
    persisted_listings = _persist_listings(db, cookie_id, listings)

    orders_ok = bool(orders_result.get("ok"))
    listings_ok = bool(listings_result.get("ok"))
    # R13: sync failure must never refresh the last-success timestamp —
    # only the resource that actually succeeded writes its sync state
    if hasattr(db, "save_account_sync_state") and orders_ok:
        db.save_account_sync_state(
            cookie_id,
            "orders",
            adapter=str(orders_result.get("adapter") or adapter_name),
            last_count=len(orders),
        )
    if hasattr(db, "save_account_sync_state") and listings_ok:
        db.save_account_sync_state(
            cookie_id,
            "listings",
            adapter=str(listings_result.get("adapter") or adapter_name),
            last_count=len(listings),
        )

    sync_freshness = {}
    if hasattr(db, "get_account_sync_state"):
        sync_freshness = db.get_account_sync_state(cookie_id)

    both_ok = orders_ok and listings_ok
    return {
        "ok": both_ok,
        # R13: a partial sync (one resource ok, the other failed) is
        # explicitly marked so consumers never mistake it for complete
        "partial": orders_ok != listings_ok,
        "adapter": adapter_name,
        "cookie_id": cookie_id,
        "status": status,
        "orders": {
            "ok": orders_ok,
            "fetched": len(orders),
            "persisted": persisted_orders,
            "runtime_updated": runtime_orders,
            "error": _public_error(orders_result),
        },
        "listings": {
            "ok": listings_ok,
            "fetched": len(listings),
            "persisted": persisted_listings,
            "error": _public_error(listings_result),
        },
        "sync_freshness": sync_freshness,
    }


def _call_sync(method: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        result = method(**kwargs)
    except TypeError:
        result = method()
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        return {"ok": False, "error": str(exc)}
    return dict(result or {}) if isinstance(result, Mapping) else {"ok": False, "error": "invalid sync result"}


def _records_from_result(result: Mapping[str, Any], *keys: str) -> list[Dict[str, Any]]:
    for key in keys:
        records = result.get(key)
        if isinstance(records, list):
            return [dict(record) for record in records if isinstance(record, Mapping)]
    data = result.get("data")
    if isinstance(data, list):
        return [dict(record) for record in data if isinstance(record, Mapping)]
    return []


def _persist_orders(db: Any, cookie_id: str, orders: Iterable[Mapping[str, Any]]) -> int:
    persisted = 0
    for order in orders:
        order_id = _first_text(order, "order_id", "orderId", "order_no", "orderNo", "id")
        if not order_id:
            continue
        db.save_order(
            order_id,
            cookie_id,
            item_id=_first_text(order, "item_id", "itemId", "item_no", "itemNo", "listing_id", "listingId"),
            buyer_id=_first_text(order, "buyer_id", "buyerId", "buyer_user_id", "buyerUserId"),
            status=_first_text(order, "status", "order_status", "orderStatus") or "processing",
            amount=_number(order.get("amount", order.get("price", order.get("paid_amount", 0)))),
            delivery_payload=_compact_json(
                order.get("delivery_payload", order.get("deliveryPayload", order.get("delivery", "")))
            ),
        )
        persisted += 1
    return persisted


def _persist_order_runtime(
    order_runtime_store: Any,
    cookie_id: str,
    orders: Iterable[Mapping[str, Any]],
    adapter_name: str,
) -> int:
    if order_runtime_store is None:
        return 0
    updated = 0
    for order in orders:
        order_id = _first_text(order, "order_id", "orderId", "order_no", "orderNo", "id")
        if not order_id:
            continue
        order_runtime_store.upsert_order(
            order_id=order_id,
            cookie_id=cookie_id,
            item_id=_first_text(order, "item_id", "itemId", "item_no", "itemNo", "listing_id", "listingId"),
            buyer_id=_first_text(order, "buyer_id", "buyerId", "buyer_user_id", "buyerUserId"),
            status=_order_runtime_status(_first_text(order, "status", "order_status", "orderStatus")),
            amount=_number(order.get("amount", order.get("price", order.get("paid_amount", 0)))),
            source=f"{adapter_name}_sync",
        )
        updated += 1
    return updated


def _persist_listings(db: Any, cookie_id: str, listings: Iterable[Mapping[str, Any]]) -> int:
    persisted = 0
    observed_at = time.time()
    for item in listings:
        item_id = _first_text(item, "item_id", "itemId", "listing_id", "listingId", "id")
        if not item_id:
            item_id = _first_text(item, "product_id", "productId")
        if not item_id:
            continue
        browse_keys = ("browse_count", "browseCount", "view_count", "viewCount")
        want_keys = ("want_count", "wantCount", "collect_count", "collectCount", "favoriteCount")
        counters_declared = bool(item.get("metrics_available")) or (
            any(key in item for key in browse_keys) and any(key in item for key in want_keys)
        )
        browse_count = _counter_or_none(_first_value(item, *browse_keys))
        want_count = _counter_or_none(_first_value(item, *want_keys))
        metrics_available = counters_declared and browse_count is not None and want_count is not None
        db.save_item(
            item_id,
            cookie_id,
            title=_first_text(item, "title", "name", "subject"),
            price=_number(item.get("price", item.get("amount", 0))),
            description=_first_text(item, "description", "desc", "detail"),
            status=_first_text(item, "status", "item_status", "itemStatus") or "active",
            metrics_available=metrics_available,
            browse_count=browse_count if metrics_available else 0,
            want_count=want_count if metrics_available else 0,
            metrics_observed_at=observed_at if metrics_available else 0,
        )
        persisted += 1
    return persisted


def _first_text(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _first_value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record.get(key) is not None:
            return record.get(key)
    return 0


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _counter_or_none(value: Any) -> Optional[int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def _compact_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _order_runtime_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    aliases = {
        "": "pending_payment",
        "processing": "pending_payment",
        "created": "pending_payment",
        "unpaid": "pending_payment",
        "pending_pay": "pending_payment",
        "paid": "paid",
        "pending_ship": "pending_shipment",
        "pending_shipment": "pending_shipment",
        "shipped": "shipped",
        "completed": "delivered",
        "delivered": "delivered",
        "received": "delivered",
        "refund": "refund_requested",
        "refunding": "refund_requested",
        "refund_requested": "refund_requested",
        "dispute": "disputed",
        "disputed": "disputed",
        "cancelled": "closed",
        "canceled": "closed",
        "closed": "closed",
    }
    return aliases.get(normalized, "pending_payment")


def _public_error(result: Mapping[str, Any]) -> str:
    error = result.get("error")
    return str(error)[:200] if error else ""
