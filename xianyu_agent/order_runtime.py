from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional


ORDER_STATUSES = {
    "pending_payment",
    "paid",
    "pending_shipment",
    "shipped",
    "delivered",
    "refund_requested",
    "disputed",
    "closed",
}

VALID_TRANSITIONS = {
    "pending_payment": {"paid", "closed"},
    "paid": {"pending_shipment", "shipped", "refund_requested", "disputed", "closed"},
    "pending_shipment": {"shipped", "refund_requested", "disputed", "closed"},
    "shipped": {"delivered", "refund_requested", "disputed", "closed"},
    "delivered": {"refund_requested", "disputed", "closed"},
    "refund_requested": {"disputed", "closed"},
    "disputed": {"closed"},
    "closed": set(),
}

FULFILLMENT_STATUSES = {"paid", "pending_shipment"}
AFTER_SALES_STATUSES = {"refund_requested", "disputed"}
SYNTHETIC_SOURCE_PREFIXES = ("fake", "stub", "mock", "test", "demo")


def is_synthetic_order_source(source: Any) -> bool:
    normalized = str(source or "").strip().lower()
    return bool(normalized) and normalized.startswith(SYNTHETIC_SOURCE_PREFIXES)


def _default_lock() -> Dict[str, Any]:
    return {
        "locked": False,
        "owner": "",
        "reason": "",
        "locked_at": 0.0,
    }


def _operator_next_action(status: str, pending_blocker: str = "") -> str:
    if pending_blocker:
        return "resolve_order_blocker"
    return {
        "pending_payment": "wait_for_payment",
        "paid": "verify_order_and_prepare_fulfillment",
        "pending_shipment": "verify_order_and_prepare_fulfillment",
        "shipped": "monitor_delivery_confirmation",
        "delivered": "close_or_request_feedback",
        "refund_requested": "review_after_sales_evidence",
        "disputed": "escalate_dispute_review",
        "closed": "archive_order_and_review_profit",
    }.get(status, "review_order")


class OrderRuntimeStore:
    """JSON-backed operating state for synced Xianyu orders.

    DB orders keep the compact account/order snapshot. This store keeps the
    operator-facing runtime layer: status history, blockers, locks, and the
    next thing a one-person operator should do.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = RLock()
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._load()

    def upsert_order(
        self,
        *,
        order_id: str,
        cookie_id: str = "",
        status: str = "pending_payment",
        item_id: str = "",
        buyer_id: str = "",
        amount: float | int | str = 0,
        source: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        timestamp: float | None = None,
    ) -> Dict[str, Any]:
        clean_id = self._clean_order_id(order_id)
        clean_status = self._clean_status(status)
        now = self._timestamp(timestamp)
        clean_cookie = str(cookie_id or "").strip()
        with self._lock:
            # R11: runtime identity is (account, order). The same external
            # order id arriving under a DIFFERENT cookie is a separate row
            # (cross-account collision), never an overwrite of the first
            # account's facts. Same-cookie upserts keep updating in place.
            existing = self._orders.get(clean_id)
            if (
                existing is not None
                and clean_cookie
                and str(existing.get("cookie_id") or "")
                and clean_cookie != str(existing.get("cookie_id"))
            ):
                composite_key = f"{clean_cookie}\x1f{clean_id}"
                existing = self._orders.get(composite_key)
                if existing is None:
                    order = self._default_order(clean_id, now)
                    order["cookie_id"] = clean_cookie
                    order["runtime_key"] = composite_key
                    self._orders[composite_key] = order
                    self._record_history(order, "", clean_status, "created", source, now)
                else:
                    order = existing
                    old_status = str(order.get("status") or "pending_payment")
                    clean_status = self._synced_status(order, old_status, clean_status, source, now)
            elif existing is None:
                order = self._default_order(clean_id, now)
                self._orders[clean_id] = order
                self._record_history(order, "", clean_status, "created", source, now)
            else:
                order = existing
                old_status = str(order.get("status") or "pending_payment")
                clean_status = self._synced_status(order, old_status, clean_status, source, now)

            order.update(
                {
                    "cookie_id": str(cookie_id or order.get("cookie_id") or "").strip(),
                    "item_id": str(item_id or order.get("item_id") or "").strip(),
                    "buyer_id": str(buyer_id or order.get("buyer_id") or "").strip(),
                    "amount": _to_float(amount if amount not in (None, "") else order.get("amount", 0)),
                    "status": clean_status,
                    "source": str(source or order.get("source") or "").strip(),
                    "metadata": _copy(dict(metadata or order.get("metadata") or {})),
                    "last_status_at": now,
                    "updated_at": now,
                }
            )
            order["operator_next_action"] = _operator_next_action(
                clean_status,
                str(order.get("pending_blocker") or ""),
            )
            self._persist()
            return _copy(order)

    def _synced_status(
        self,
        order: Dict[str, Any],
        old_status: str,
        new_status: str,
        source: str,
        now: float,
    ) -> str:
        """R13: apply a synced transition or IGNORE an out-of-order replay.

        A stale sync batch delivering an earlier lifecycle stage (e.g.
        'paid' after the order is already 'shipped') must neither regress
        the state machine nor crash the whole sync — the anomaly is
        recorded in the status history and the current status stands.
        """
        if old_status == new_status:
            return new_status
        try:
            self._validate_transition(old_status, new_status)
        except ValueError:
            history = order.setdefault("status_history", [])
            if not isinstance(history, list):
                history = []
                order["status_history"] = history
            history.append({
                "at": now,
                "from": old_status,
                "to": new_status,
                "change": "ignored_out_of_order",
                "context": str(source or ""),
                "note": "out-of-order event kept the current status",
            })
            return old_status
        self._record_history(order, old_status, new_status, "synced", source, now)
        return new_status

    def update_status(
        self,
        order_id: str,
        status: str,
        *,
        context: str = "",
        operator_action: str = "",
        blocker: str = "",
        timestamp: float | None = None,
    ) -> Dict[str, Any]:
        clean_id = self._clean_order_id(order_id)
        clean_status = self._clean_status(status)
        now = self._timestamp(timestamp)
        with self._lock:
            order = self._require(clean_id)
            old_status = str(order.get("status") or "pending_payment")
            if old_status != clean_status:
                self._validate_transition(old_status, clean_status)
                self._record_history(order, old_status, clean_status, context, operator_action, now)
            order["status"] = clean_status
            order["last_status_at"] = now
            order["last_operator_action"] = str(operator_action or "").strip()
            order["pending_blocker"] = str(blocker or "").strip()
            order["operator_next_action"] = _operator_next_action(clean_status, order["pending_blocker"])
            order["updated_at"] = now
            self._persist()
            return _copy(order)

    def acquire_fulfillment_lock(self, order_id: str, *, owner: str, reason: str = "") -> Dict[str, Any]:
        clean_owner = str(owner or "").strip()
        if not clean_owner:
            raise ValueError("owner is required")
        with self._lock:
            order = self._require(self._clean_order_id(order_id))
            lock = order.get("fulfillment_lock") if isinstance(order.get("fulfillment_lock"), Mapping) else {}
            if lock.get("locked") and str(lock.get("owner") or "") != clean_owner:
                raise ValueError(f"order is already locked by {lock.get('owner')}")
            order["fulfillment_lock"] = {
                "locked": True,
                "owner": clean_owner,
                "reason": str(reason or "").strip(),
                "locked_at": time.time(),
            }
            order["last_operator_action"] = "acquire_fulfillment_lock"
            order["updated_at"] = time.time()
            self._persist()
            return _copy(order)

    def release_fulfillment_lock(self, order_id: str, *, owner: str = "") -> Dict[str, Any]:
        with self._lock:
            order = self._require(self._clean_order_id(order_id))
            lock = order.get("fulfillment_lock") if isinstance(order.get("fulfillment_lock"), Mapping) else {}
            clean_owner = str(owner or "").strip()
            if clean_owner and lock.get("locked") and str(lock.get("owner") or "") != clean_owner:
                raise ValueError(f"order is locked by {lock.get('owner')}")
            order["fulfillment_lock"] = _default_lock()
            order["last_operator_action"] = "release_fulfillment_lock"
            order["updated_at"] = time.time()
            self._persist()
            return _copy(order)

    def get_order(self, order_id: str) -> Dict[str, Any]:
        with self._lock:
            return _copy(self._require(self._clean_order_id(order_id)))

    def list_orders(
        self,
        *,
        status: Optional[str] = None,
        cookie_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            orders = list(self._orders.values())
            if status:
                orders = [order for order in orders if order.get("status") == status]
            if cookie_id:
                orders = [order for order in orders if order.get("cookie_id") == cookie_id]
            orders.sort(key=lambda order: float(order.get("updated_at") or 0), reverse=True)
            return _copy(orders[: max(0, int(limit or 100))])

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            orders = list(self._orders.values())
            synthetic_orders = [
                order for order in orders if is_synthetic_order_source(order.get("source"))
            ]
            operational_orders = [
                order for order in orders if not is_synthetic_order_source(order.get("source"))
            ]
            by_status = dict(Counter(str(order.get("status") or "unknown") for order in operational_orders))
            observed_by_status = dict(Counter(str(order.get("status") or "unknown") for order in orders))
            needs_fulfillment = [
                order for order in operational_orders if str(order.get("status") or "") in FULFILLMENT_STATUSES
            ]
            after_sales = [
                order for order in operational_orders if str(order.get("status") or "") in AFTER_SALES_STATUSES
            ]
            blocked = [
                order for order in operational_orders if str(order.get("pending_blocker") or "").strip()
            ]
            locked = [
                order for order in operational_orders
                if isinstance(order.get("fulfillment_lock"), Mapping)
                and bool(order["fulfillment_lock"].get("locked"))
            ]
            action_orders = blocked or needs_fulfillment or after_sales
            action_orders = sorted(action_orders, key=lambda order: float(order.get("updated_at") or 0), reverse=True)
            return {
                "total_orders": len(orders),
                "operational_orders": len(operational_orders),
                "synthetic_orders": len(synthetic_orders),
                "needs_fulfillment": len(needs_fulfillment),
                "after_sales": len(after_sales),
                "blocked": len(blocked),
                "locked": len(locked),
                "by_status": by_status,
                "observed_by_status": observed_by_status,
                "next_actions": [
                    {
                        "order_id": str(order.get("order_id") or ""),
                        "status": str(order.get("status") or ""),
                        "operator_next_action": str(order.get("operator_next_action") or ""),
                        "pending_blocker": str(order.get("pending_blocker") or ""),
                    }
                    for order in action_orders[:5]
                ],
            }

    def _default_order(self, order_id: str, now: float) -> Dict[str, Any]:
        return {
            "order_id": order_id,
            "cookie_id": "",
            "item_id": "",
            "buyer_id": "",
            "amount": 0.0,
            "status": "pending_payment",
            "last_status_at": now,
            "last_operator_action": "",
            "operator_next_action": "wait_for_payment",
            "pending_blocker": "",
            "fulfillment_lock": _default_lock(),
            "status_history": [],
            "source": "",
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }

    def _require(self, order_id: str) -> Dict[str, Any]:
        order = self._orders.get(order_id)
        if not order:
            raise KeyError(f"order runtime not found: {order_id}")
        return order

    def _record_history(
        self,
        order: Dict[str, Any],
        old_status: str,
        new_status: str,
        context: str,
        operator_action: str,
        timestamp: float,
    ) -> None:
        history = order.setdefault("status_history", [])
        history.append(
            {
                "old_status": old_status,
                "new_status": new_status,
                "context": str(context or "").strip(),
                "operator_action": str(operator_action or "").strip(),
                "created_at": timestamp,
            }
        )

    def _validate_transition(self, old_status: str, new_status: str) -> None:
        if old_status not in ORDER_STATUSES or new_status not in ORDER_STATUSES:
            raise ValueError(f"unsupported order transition: {old_status} -> {new_status}")
        if new_status not in VALID_TRANSITIONS.get(old_status, set()):
            raise ValueError(f"invalid order transition: {old_status} -> {new_status}")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_orders = data.get("orders") if isinstance(data, Mapping) else None
        if not isinstance(raw_orders, list):
            return
        for item in raw_orders:
            if not isinstance(item, Mapping):
                continue
            order_id = str(item.get("order_id") or "").strip()
            if not order_id:
                continue
            now = self._timestamp(None)
            order = self._default_order(order_id, now)
            order.update(dict(item))
            if order.get("status") not in ORDER_STATUSES:
                order["status"] = "pending_payment"
            if not isinstance(order.get("fulfillment_lock"), Mapping):
                order["fulfillment_lock"] = _default_lock()
            if not isinstance(order.get("status_history"), list):
                order["status_history"] = []
            # R11: cross-account rows persist under their composite runtime
            # key so a reload keeps both accounts' orders distinct
            self._orders[str(order.get("runtime_key") or order_id)] = order

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        orders = sorted(self._orders.values(), key=lambda order: float(order.get("created_at") or 0))
        tmp_path.write_text(
            json.dumps({"orders": orders}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    @staticmethod
    def _clean_order_id(order_id: str) -> str:
        clean = str(order_id or "").strip()
        if not clean:
            raise ValueError("order_id is required")
        return clean

    @staticmethod
    def _clean_status(status: str) -> str:
        clean = str(status or "pending_payment").strip()
        if clean not in ORDER_STATUSES:
            raise ValueError(f"unsupported order status: {clean}")
        return clean

    @staticmethod
    def _timestamp(timestamp: float | None) -> float:
        return time.time() if timestamp is None else float(timestamp)


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
