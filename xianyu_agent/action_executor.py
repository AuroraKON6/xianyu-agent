from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

# Type for a handler: (approval, context) -> dict or coroutine
ActionHandler = Callable[[Dict[str, Any], Dict[str, Any]], Any]


class ActionExecutor:
    """Dispatches approved actions to registered handlers.

    Responsibilities:
    - register_handler(action_name, handler)
    - execute(approval, context)  — sync, for simple handlers
    - execute_async(approval, context) — async, for handlers that need await
    - Does NOT manage WorkOrder state; that stays in WorkOrderManager.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, ActionHandler] = {}
        self._register_stubs()

    def register_handler(self, action: str, handler: ActionHandler) -> None:
        self._handlers[action] = handler

    def execute(self, approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a sync handler for the given approval."""
        action = approval.get("action", "")
        handler = self._handlers.get(action)
        if handler is None:
            return {
                "ok": False,
                "action": action,
                "error": f"Action handler not registered: {action}",
            }
        try:
            result = handler(approval, context)
            # If handler returns a coroutine, run it
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                result = asyncio.run(result)
            return result
        except Exception as exc:
            logger.exception("Action handler '%s' raised", action)
            return {
                "ok": False,
                "action": action,
                "error": str(exc),
            }

    async def execute_async(self, approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a handler that may be async."""
        action = approval.get("action", "")
        handler = self._handlers.get(action)
        if handler is None:
            return {
                "ok": False,
                "action": action,
                "error": f"Action handler not registered: {action}",
            }
        try:
            result = handler(approval, context)
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                result = await result
            return result
        except Exception as exc:
            logger.exception("Action handler '%s' raised", action)
            return {
                "ok": False,
                "action": action,
                "error": str(exc),
            }

    def _register_stubs(self) -> None:
        """Register real handlers and stubs for future actions."""
        self.register_handler("publish_listing", handle_publish_listing)
        self.register_handler("edit_listing", handle_edit_listing)
        self.register_handler("fulfill_order", handle_fulfill_order)
        self.register_handler("send_message", handle_send_message)
        self.register_handler("sensitive_reply", handle_sensitive_reply)
        self.register_handler("delete_item", handle_delete_item)
        self.register_handler("refund_order", handle_refund_order)
        self.register_handler("prepare_purchase_draft", handle_prepare_purchase_draft)
        self.register_handler("place_supplier_order", handle_place_supplier_order)


async def handle_publish_listing(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Read draft from disk, call browser adapter, return structured result."""
    action = "publish_listing"
    payload = approval.get("payload", {})
    draft_id = payload.get("draft_id", "")
    cookie_id = str(
        payload.get("cookie_id")
        or (approval.get("draft_snapshot") or {}).get("cookie_id")
        or "default"
    ).strip()

    if not draft_id:
        return {"ok": False, "action": action, "error": "draft_id missing from approval payload"}

    data_dir: Path = context.get("data_dir") or Path("data")
    draft_path = data_dir / "drafts" / f"{draft_id}.json"

    if not draft_path.exists():
        return {"ok": False, "action": action, "draft_id": draft_id, "error": f"Draft not found: {draft_id}"}

    draft_data = json.loads(draft_path.read_text(encoding="utf-8"))

    # Backfill draft_snapshot if missing
    if not approval.get("draft_snapshot"):
        approval["draft_snapshot"] = {
            "title": draft_data.get("title", ""),
            "price": draft_data.get("price"),
            "stock": draft_data.get("stock"),
            "description": draft_data.get("description", ""),
            "delivery_type": draft_data.get("delivery_type", ""),
            "category": draft_data.get("category", ""),
        }

    workflow = context.get("workflow")
    if workflow is None:
        return {"ok": False, "action": action, "draft_id": draft_id, "error": "workflow not available in context"}

    adapter = getattr(workflow, "adapter", None)
    if adapter is None:
        return {"ok": False, "action": action, "draft_id": draft_id, "error": "adapter not available"}

    from .models import ListingDraft

    draft = ListingDraft(
        title=draft_data.get("title", ""),
        category=draft_data.get("category", ""),
        price=draft_data.get("price"),
        stock=draft_data.get("stock"),
        condition=draft_data.get("condition", "new"),
        location=draft_data.get("location", ""),
        description=draft_data.get("description", ""),
        tags=draft_data.get("tags", []),
        selected_images=draft_data.get("selected_images", []),
        delivery_type=draft_data.get("delivery_type", "manual"),
        delivery_payload=draft_data.get("delivery_payload", ""),
        source=draft_data.get("source", "execute-approved"),
        raw_text=draft_data.get("raw_text", ""),
    )

    result = await adapter.publish_listing(draft, cookie_id=cookie_id)
    if result.ok:
        workflow.storage.save_publish_result(result, draft)

    return {
        "ok": result.ok,
        "action": action,
        "draft_id": draft_id,
        "cookie_id": cookie_id,
        "publish_result": {
            "ok": result.ok,
            "external_id": result.external_id,
            "adapter": result.adapter,
            "message": result.message,
        },
    }


async def handle_edit_listing(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Submit one approved title/price edit; activation needs later evidence."""
    action = "edit_listing"
    payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
    item_id = str(payload.get("item_id") or "").strip()
    cookie_id = str(payload.get("cookie_id") or "").strip()
    variable = str(payload.get("variable") or "").strip().lower()
    after_value = payload.get("after_value")
    if not item_id or not cookie_id:
        return {"ok": False, "action": action, "error": "item_id and cookie_id are required"}
    if variable not in {"title", "price"}:
        return {
            "ok": False,
            "action": action,
            "item_id": item_id,
            "variable": variable,
            "manual_required": True,
            "error": f"{variable or 'unknown'} edit requires operator confirmation",
            "operator_next_action": "complete_manual_listing_edit",
        }

    trial_id = str(payload.get("commercial_trial_id") or "").strip()
    experiment_id = str(payload.get("experiment_id") or "").strip()
    if trial_id or experiment_id:
        store = context.get("commercial_trial_store")
        if store is None or not trial_id or not experiment_id:
            return {
                "ok": False,
                "action": action,
                "error": "commercial experiment state is unavailable",
                "operator_next_action": "inspect_experiment_linkage",
            }
        trial = store.get_trial(trial_id)
        experiment = next(
            (
                item
                for item in (trial or {}).get("experiments") or []
                if str(item.get("experiment_id") or "") == experiment_id
            ),
            None,
        )
        if (
            not trial
            or str(trial.get("lifecycle") or "") != "active"
            or not experiment
            or str(experiment.get("status") or "") != "proposed"
        ):
            return {
                "ok": False,
                "action": action,
                "item_id": item_id,
                "error": "commercial experiment is no longer executable",
                "operator_next_action": "review_cancelled_or_inactive_experiment",
            }

    if variable == "title":
        clean_value = str(after_value or "").strip()
        if not clean_value or len(clean_value) > 80:
            return {"ok": False, "action": action, "error": "title must contain 1 to 80 characters"}
    else:
        try:
            clean_value = round(float(after_value), 2)
        except (TypeError, ValueError):
            clean_value = float("nan")
        if not math.isfinite(clean_value) or not 0 < clean_value <= 1_000_000:
            return {"ok": False, "action": action, "error": "price must be a positive finite number"}

    workflow = context.get("workflow")
    adapter = getattr(workflow, "adapter", None) if workflow is not None else None
    if adapter is None:
        return {"ok": False, "action": action, "error": "adapter not available"}
    if hasattr(adapter, "polish_listing"):
        result = adapter.polish_listing(item_id, {variable: clean_value}, cookie_id=cookie_id)
    else:
        result = adapter.polish(item_id, {variable: clean_value})
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        result = await result
    result = dict(result or {})
    submitted = bool(result.get("ok"))
    return {
        "ok": submitted,
        "action": action,
        "item_id": item_id,
        "cookie_id": cookie_id,
        "variable": variable,
        "submitted": submitted,
        "confirmed": False,
        "adapter_result": result,
        "error": None if submitted else str(result.get("error") or result.get("message") or "listing edit failed"),
        "operator_next_action": "sync_listing_and_activate_experiment" if submitted else "inspect_listing_edit_failure",
        "evidence": {
            "page_url": str(result.get("page_url") or ""),
            "detected_blocker": str(result.get("blocker") or ""),
            "operator_next_action": "sync_listing_and_activate_experiment" if submitted else "inspect_listing_edit_failure",
        },
    }


async def handle_fulfill_order(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Fulfill an order by calling workflow.fulfill_order."""
    action = "fulfill_order"
    payload = approval.get("payload", {})
    order_id = payload.get("order_id", "")
    cookie_id = str(payload.get("cookie_id") or "default")

    if not order_id:
        return {"ok": False, "action": action, "error": "order_id missing from approval payload"}

    workflow = context.get("workflow")
    if workflow is None:
        return {"ok": False, "action": action, "order_id": order_id, "error": "workflow not available in context"}

    try:
        result = workflow.fulfill_order(payload)
    except Exception as exc:
        logger.exception("fulfill_order handler raised for order '%s'", order_id)
        return {"ok": False, "action": action, "order_id": order_id, "error": str(exc)}

    return {
        "ok": result.get("ok", False),
        "action": action,
        "order_id": order_id,
        "cookie_id": cookie_id,
        "fulfillment_result": result,
        "error": None if result.get("ok") else result.get("message", "fulfill_order failed"),
    }


async def handle_send_message(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send a message to a buyer via browser adapter."""
    return await _send_message_action(approval, context, action="send_message")


async def handle_sensitive_reply(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send an approved dispute/customer-sensitive reply via the normal message adapter."""
    return await _send_message_action(approval, context, action="sensitive_reply")


async def _send_message_action(
    approval: Dict[str, Any],
    context: Dict[str, Any],
    action: str,
) -> Dict[str, Any]:
    payload = approval.get("payload", {})
    buyer_id = payload.get("buyer_id", "")
    message = payload.get("message", "")
    cookie_id = payload.get("cookie_id", "default")
    dispute_id = payload.get("dispute_id", "")

    if not buyer_id:
        return {"ok": False, "action": action, "error": "buyer_id missing from approval payload"}

    workflow = context.get("workflow")
    if workflow is None:
        return {"ok": False, "action": action, "buyer_id": buyer_id, "error": "workflow not available in context"}

    adapter = getattr(workflow, "adapter", None)
    if adapter is None:
        return {"ok": False, "action": action, "buyer_id": buyer_id, "error": "adapter not available"}

    try:
        result = await adapter.send_message(buyer_id=buyer_id, message=message, cookie_id=cookie_id)
    except Exception as exc:
        logger.exception("%s handler raised for buyer '%s'", action, buyer_id)
        return {"ok": False, "action": action, "buyer_id": buyer_id, "error": str(exc)}

    response = {
        "ok": result.get("ok", False),
        "action": action,
        "buyer_id": buyer_id,
        "send_result": result,
        "error": None if result.get("ok") else result.get("error", f"{action} failed"),
    }
    if dispute_id:
        response["dispute_id"] = dispute_id
    return response


async def handle_delete_item(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Delete an approved listing via the workflow adapter."""
    action = "delete_item"
    payload = approval.get("payload", {})
    item_id = payload.get("item_id", "")
    cookie_id = payload.get("cookie_id", "default")

    if not item_id:
        return {"ok": False, "action": action, "error": "item_id missing from approval payload"}

    workflow = context.get("workflow")
    if workflow is None:
        return {"ok": False, "action": action, "item_id": item_id, "error": "workflow not available in context"}

    adapter = getattr(workflow, "adapter", None)
    if adapter is None:
        return {"ok": False, "action": action, "item_id": item_id, "error": "adapter not available"}

    delete_fn = getattr(adapter, "delete_item", None)
    if delete_fn is None:
        return {
            "ok": False,
            "action": action,
            "item_id": item_id,
            "error": "adapter does not support delete_item",
        }

    try:
        result = delete_fn(item_id, cookie_id=cookie_id)
        result = await _maybe_await(result)
    except Exception as exc:
        logger.exception("delete_item handler raised for item '%s'", item_id)
        return {"ok": False, "action": action, "item_id": item_id, "error": str(exc)}

    _append_workflow_event(workflow, "listing.deleted", {"item_id": item_id, "result": result})
    return {
        "ok": result.get("ok", False),
        "action": action,
        "item_id": item_id,
        "delete_result": result,
        "error": None if result.get("ok") else result.get("error") or result.get("message", "delete_item failed"),
    }


async def handle_refund_order(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Submit an approved refund through workflow or adapter support."""
    action = "refund_order"
    payload = approval.get("payload", {})
    order_id = payload.get("order_id", "")

    if not order_id:
        return {"ok": False, "action": action, "error": "order_id missing from approval payload"}

    workflow = context.get("workflow")
    if workflow is None:
        return {"ok": False, "action": action, "order_id": order_id, "error": "workflow not available in context"}

    refund_fn = getattr(workflow, "refund_order", None)
    if refund_fn is None:
        adapter = getattr(workflow, "adapter", None)
        refund_fn = getattr(adapter, "refund_order", None) if adapter is not None else None
    if refund_fn is None:
        return {
            "ok": False,
            "action": action,
            "order_id": order_id,
            "error": "workflow adapter does not support refund_order",
        }

    try:
        result = refund_fn(payload)
        result = await _maybe_await(result)
    except TypeError:
        try:
            result = refund_fn(
                order_id=order_id,
                amount_cny=payload.get("amount_cny"),
                reason=payload.get("reason", ""),
                cookie_id=payload.get("cookie_id", "default"),
            )
            result = await _maybe_await(result)
        except Exception as exc:
            logger.exception("refund_order handler raised for order '%s'", order_id)
            return {"ok": False, "action": action, "order_id": order_id, "error": str(exc)}
    except Exception as exc:
        logger.exception("refund_order handler raised for order '%s'", order_id)
        return {"ok": False, "action": action, "order_id": order_id, "error": str(exc)}

    return {
        "ok": result.get("ok", False),
        "action": action,
        "order_id": order_id,
        "refund_result": result,
        "error": None if result.get("ok") else result.get("error") or result.get("message", "refund_order failed"),
    }


async def handle_prepare_purchase_draft(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a purchase draft and create a manual purchase task. Does NOT auto-pay or auto-submit."""
    action = "prepare_purchase_draft"
    payload = approval.get("payload", {})
    plan_id = payload.get("plan_id", "")
    draft_id = payload.get("draft_id", "")

    if not plan_id:
        return {"ok": False, "action": action, "error": "plan_id missing from approval payload"}

    data_dir: Path = context.get("data_dir") or Path("data")

    try:
        from .resale import ResaleManager
        manager = ResaleManager(data_dir)
    except Exception as exc:
        return {"ok": False, "action": action, "error": f"Failed to init ResaleManager: {exc}"}

    if draft_id:
        draft = manager.get_purchase_draft(draft_id)
    else:
        plan = manager.get_plan(plan_id)
        if not plan:
            return {"ok": False, "action": action, "error": f"Plan not found: {plan_id}"}
        draft = manager.create_purchase_draft(
            plan_id=plan_id,
            quantity=int(payload.get("quantity", 1)),
            buyer_address_summary=payload.get("buyer_address_summary", ""),
            buyer_address_full=payload.get("buyer_address_full", ""),
        )

    if not draft:
        return {"ok": False, "action": action, "error": "Failed to create purchase draft"}

    # Create a manual purchase task — never auto-order
    task = manager.create_purchase_task(draft)

    return {
        "ok": True,
        "action": action,
        "draft_id": draft.get("draft_id"),
        "purchase_task_id": task.get("purchase_task_id"),
        "purchase_task": task,
        "message": "已创建人工采购任务，请手动在供应商平台下单。不会自动付款或提交订单。",
    }


async def handle_place_supplier_order(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Blocked in MVP — never auto-submit supplier orders."""
    return {
        "ok": False,
        "action": "place_supplier_order",
        "error": "MVP 阶段不支持自动提交供应商订单。请手动在供应商平台下单。",
    }


def _make_stub(action: str) -> ActionHandler:
    def _stub(approval: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ok": False,
            "action": action,
            "error": f"Action handler not implemented: {action}",
        }
    return _stub


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value) or asyncio.isfuture(value):
        return await value
    return value


def _before_platform_action(workflow: Any, action: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    guard = getattr(workflow, "platform_guard", None)
    before_action = getattr(guard, "before_action", None)
    if before_action is None:
        return {}
    return before_action(action, metadata)


def _append_workflow_event(workflow: Any, event_type: str, payload: Dict[str, Any]) -> None:
    storage = getattr(workflow, "storage", None)
    append_event = getattr(storage, "append_event", None)
    if append_event is None:
        return
    append_event(event_type, payload)


action_executor = ActionExecutor()
