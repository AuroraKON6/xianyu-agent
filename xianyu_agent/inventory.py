"""Sellable inventory allocation (WP12).

Supplier stock is NOT sellable stock. This service converts the latest
supplier offer snapshot (stock_quantity + observed_at) into a
conservative sellable allocation per account + SKU, with order locks:

- Unknown or expired supplier stock (older than the quote TTL) yields
  allocation 0: the SKU is paused for selling rather than oversold
  ("宁可低库存不超卖").
- A safety margin is subtracted from known stock.
- Order locks decrement the allocation atomically and idempotently per
  order; releasing a cancelled order returns the quantity.
- Every state change records why, so panels can explain why a SKU is
  not sellable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from .commerce.repositories import LockedJsonList
from .commerce.contracts import new_id, utc_now
from .providers.freshness import FRESH_CURRENT, classify_snapshot

DEFAULT_SAFETY_MARGIN = 1


class InventoryError(RuntimeError):
    pass


class InventoryAllocationService:
    def __init__(self, data_dir, *, safety_margin: int = DEFAULT_SAFETY_MARGIN) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "inventory.json")
        self._safety_margin = max(0, int(safety_margin))

    @staticmethod
    def _stock_freshness(record: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
        facts = record.get("supplier_time_provenance")
        if not isinstance(facts, Mapping):
            return {"freshness": "unknown", "reasons": ["legacy_inventory_provenance_missing"]}
        return classify_snapshot(facts, now=now)

    @classmethod
    def _stock_is_current(cls, record: Mapping[str, Any], now: datetime) -> bool:
        return cls._stock_freshness(record, now)["freshness"] == FRESH_CURRENT

    # ---- supplier stock registration ----

    def register_supplier_snapshot(self, *, account_id: str, sku: str, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
        """Refresh supplier stock for one account+SKU from a snapshot."""
        if not account_id.strip() or not sku.strip():
            raise InventoryError("account_id and sku are required")
        if str(snapshot.get("record_type")) != "offer_snapshot":
            raise InventoryError("supplier stock requires an offer snapshot")

        freshness = classify_snapshot(snapshot)
        stock_quantity = snapshot.get("stock_quantity")
        stock_state = str(snapshot.get("stock_state") or "unknown")
        known = stock_quantity is not None and stock_state != "unknown" and freshness["freshness"] == "current"
        sellable = max(0, int(stock_quantity) - self._safety_margin) if known else 0
        reason = (
            "supplier stock known and fresh"
            if known
            else f"stock not sellable (stock_quantity={stock_quantity}, stock_state={stock_state}, freshness={freshness['freshness']})"
        )

        def mutate(items: LockedJsonList) -> Dict[str, Any]:
            record = self._find(items, account_id, sku)
            if record is None:
                record = {
                    "record_type": "inventory_allocation",
                    "record_id": new_id("inv"),
                    "account_id": account_id,
                    "sku": sku,
                    "supplier_snapshot_id": None,
                    "supplier_stock": None,
                    "sellable_base": 0,
                    "locks": {},
                    "history": [],
                    "created_at": utc_now().isoformat(),
                }
                items._items.append(record)
            record["supplier_snapshot_id"] = snapshot.get("record_id")
            record["supplier_stock"] = stock_quantity if known else None
            record["supplier_observed_at"] = snapshot.get("observed_at")
            record["supplier_time_provenance"] = deepcopy({key: snapshot.get(key) for key in (
                "provider_id", "observed_at", "fetched_at", "expires_at", "attributes")})
            record["freshness"] = freshness
            record["sellable_base"] = sellable
            record["updated_at"] = utc_now().isoformat()
            record["history"].append(
                {
                    "at": utc_now().isoformat(),
                    "change": f"supplier stock -> sellable_base={sellable}",
                    "reason": reason,
                    "snapshot_id": snapshot.get("record_id"),
                }
            )
            return dict(record)

        with self._store as items:
            return mutate(items)

    # ---- allocation reads ----

    def _find(self, items: LockedJsonList, account_id: str, sku: str) -> Optional[Dict[str, Any]]:
        for record in items._items:
            if str(record.get("account_id")) == account_id and str(record.get("sku")) == sku:
                return record
        return None

    def allocation(self, *, account_id: str, sku: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        moment = now or utc_now()
        found = self._store.find(
            lambda r: str(r.get("account_id")) == account_id and str(r.get("sku")) == sku
        )
        if not found:
            return {
                "account_id": account_id,
                "sku": sku,
                "sellable": 0,
                "locked": 0,
                "available": 0,
                "sellable_reason": "no supplier stock registered; paused",
            }
        record = found[0]
        locked = sum(int(q) for q in (record.get("locks") or {}).values())
        freshness = self._stock_freshness(record, moment)
        current = freshness["freshness"] == FRESH_CURRENT
        base = int(record.get("sellable_base") or 0) if current else 0
        available = max(0, base - locked) if current else 0
        reason = (
            "ok"
            if current and record.get("supplier_stock") is not None
            else (
                "supplier stock expired or source unknown; refresh the snapshot before selling"
                if not current
                else "supplier stock unknown; paused"
            )
        )
        return {
            "account_id": account_id,
            "sku": sku,
            "supplier_snapshot_id": record.get("supplier_snapshot_id"),
            "supplier_stock": record.get("supplier_stock"),
            "supplier_observed_at": record.get("supplier_observed_at"),
            "freshness": freshness,
            "stock_current_at_read": current,
            "sellable": base,
            "locked": locked,
            "available": available,
            "sellable_reason": reason,
        }

    # ---- order locks ----

    def lock_for_order(
        self,
        *,
        account_id: str,
        sku: str,
        order_id: str,
        quantity: int,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if quantity <= 0:
            raise InventoryError("lock quantity must be positive")
        if not order_id.strip():
            raise InventoryError("order_id required")
        moment = now or utc_now()

        def mutate(items: LockedJsonList) -> Dict[str, Any]:
            record = self._find(items, account_id, sku)
            if record is None:
                raise InventoryError(f"no inventory registered for {account_id}/{sku}; refusing to oversell")
            # re-validate freshness at lock time: a snapshot that was
            # current at registration may have expired since
            if not self._stock_is_current(record, moment):
                raise InventoryError(
                    f"supplier stock for {account_id}/{sku} expired or source unknown "
                    f"(observed {record.get('supplier_observed_at')}); refresh before locking"
                )
            locks = record.setdefault("locks", {})
            if order_id in locks:
                return dict(record)  # idempotent re-lock
            locked = sum(int(q) for q in locks.values())
            available = max(0, int(record.get("sellable_base") or 0) - locked)
            if quantity > available:
                raise InventoryError(
                    f"insufficient allocation for {account_id}/{sku}: need {quantity}, available {available}"
                )
            locks[order_id] = quantity
            record["history"].append(
                {"at": utc_now().isoformat(), "change": f"lock {quantity} for {order_id}", "reason": "order confirmed"}
            )
            return dict(record)

        with self._store as items:
            return mutate(items)

    def release_order(self, *, account_id: str, sku: str, order_id: str, reason: str) -> Dict[str, Any]:
        if not reason.strip():
            raise InventoryError("release requires a reason")

        def mutate(items: LockedJsonList) -> Dict[str, Any]:
            record = self._find(items, account_id, sku)
            if record is None or order_id not in (record.get("locks") or {}):
                raise InventoryError(f"no lock for order {order_id}")
            quantity = record["locks"].pop(order_id)
            record["history"].append(
                {"at": utc_now().isoformat(), "change": f"release {quantity} from {order_id}", "reason": reason}
            )
            return dict(record)

        with self._store as items:
            return mutate(items)

    def list_locks(self, *, account_id: str = "") -> List[Dict[str, Any]]:
        """Read-only projection of all order locks (recovery/ops use)."""
        locks: List[Dict[str, Any]] = []
        for record in self._store.find(lambda r: (not account_id) or str(r.get("account_id")) == account_id):
            for order_id, quantity in (record.get("locks") or {}).items():
                locks.append(
                    {
                        "account_id": record.get("account_id"),
                        "sku": record.get("sku"),
                        "order_id": order_id,
                        "quantity": int(quantity),
                    }
                )
        return locks

    def list_allocations(self, *, account_id: str = "") -> List[Dict[str, Any]]:
        records = self._store.find(lambda r: (not account_id) or str(r.get("account_id")) == account_id)
        return [
            {
                "account_id": r.get("account_id"),
                "sku": r.get("sku"),
                "sellable": r.get("sellable_base"),
                "supplier_observed_at": r.get("supplier_observed_at"),
                "freshness": (r.get("freshness") or {}).get("freshness"),
            }
            for r in records
        ]
