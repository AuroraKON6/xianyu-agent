"""Procurement service (WP13 minimal).

Turns a confirmed sales order into a procurement draft -- after
re-verifying supply (fresh supplier snapshot at a known price) and
allocation -- and walks it through explicit human steps only:

    draft -> awaiting_approval -> approved -> ordered (supplier order id
    recorded) -> received (receipt + actual costs)

There is NO automated ordering or payment here: the first phase keeps
placing and paying orders a human decision (WP13 backlog), and a
supplier order id / receipt must come from real documents, never from
this module's imagination. Actual costs land in the append-only ledger
as provisional entries; corrections happen by reversal, never edits.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from .commerce.contracts import (
    PRICE_STATE_KNOWN,
    build_procurement_order,
    utc_now,
)
from .commerce.repositories import LockedJsonList
from .providers.freshness import FRESH_CURRENT, classify_snapshot


class ProcurementError(RuntimeError):
    pass


class ProcurementService:
    def __init__(self, data_dir, repos, inventory) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "procurement_orders.json")
        self._repos = repos
        self._inventory = inventory

    # ---- creation with re-verification ----

    def create_from_sales_order(
        self,
        *,
        account_id: str,
        sku: str,
        quantity: int,
        sales_order_ref: str,
        opportunity_id: str = "",
    ) -> Dict[str, Any]:
        if not sales_order_ref.strip():
            raise ProcurementError("sales_order_ref required")
        if quantity <= 0:
            raise ProcurementError("quantity must be positive")

        # select the freshest supplier_file snapshot for this SKU
        candidates = [
            record
            for record in self._repos.offers.list(source_item_id=sku)
            if str(record.get("provider_id", "")).startswith("supplier_file")
        ]
        if not candidates:
            raise ProcurementError(f"no supplier offer for sku {sku}; procurement refused")
        latest = max(candidates, key=lambda r: str(r.get("observed_at") or ""))
        freshness = classify_snapshot(latest)
        if freshness["freshness"] != FRESH_CURRENT:
            raise ProcurementError(
                f"supplier quote for {sku} is {freshness['freshness']}; refresh before procuring"
            )
        price = latest.get("eligible_price") or {}
        freight = latest.get("shipping_quote") or {}
        if price.get("state") != PRICE_STATE_KNOWN:
            raise ProcurementError(f"supplier price unknown for {sku}; procurement refused")
        allocation = self._inventory.allocation(account_id=account_id, sku=sku)
        if allocation["available"] < quantity:
            raise ProcurementError(
                f"allocation {allocation['available']} < requested {quantity} for {account_id}/{sku}"
            )

        order = build_procurement_order(
            supplier_ref=str(latest.get("seller_ref") or latest.get("provider_id")),
            canonical_sku=sku,
            quantity=quantity,
            unit_cost=price,
            shipping_cost=freight,
            sales_order_ref=sales_order_ref,
            opportunity_id=opportunity_id,
            status="awaiting_approval",
        )
        order["supplier_snapshot_id"] = latest["record_id"]
        order["supplier_price_at_creation"] = price.get("amount")
        return self._store.append(order)

    # ---- human steps ----

    def approve(self, procurement_id: str, *, operator: str) -> Dict[str, Any]:
        return self._transition(procurement_id, "approved", require_status="awaiting_approval", actor=operator)

    def record_supplier_order(self, procurement_id: str, *, supplier_order_id: str, operator: str) -> Dict[str, Any]:
        if not supplier_order_id.strip():
            raise ProcurementError("a real supplier order id is required")
        return self._transition(
            procurement_id, "ordered", require_status="approved", actor=operator,
            extra={"supplier_order_id": supplier_order_id},
        )

    def record_receipt(
        self,
        procurement_id: str,
        *,
        receipt_ref: str,
        actual_unit_cost: Optional[str] = None,
        actual_freight: Optional[str] = None,
        operator: str = "",
    ) -> Dict[str, Any]:
        if not receipt_ref.strip():
            raise ProcurementError("receipt evidence reference required")
        return self._transition(
            procurement_id, "received", require_status="ordered", actor=operator,
            extra={
                "receipt_ref": receipt_ref,
                "actual_unit_cost": actual_unit_cost,
                "actual_freight": actual_freight,
            },
        )

    def cancel(self, procurement_id: str, *, reason: str, operator: str) -> Dict[str, Any]:
        found = self._get(procurement_id)
        if str(found.get("status")) in ("ordered", "received"):
            raise ProcurementError("cannot cancel after supplier order; use the after-sales flow")
        return self._transition(procurement_id, "cancelled", require_status=None, actor=operator, extra={"cancel_reason": reason})

    def _transition(
        self,
        procurement_id: str,
        to_status: str,
        *,
        require_status: Optional[str],
        actor: str,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        def mutate(order: Dict[str, Any]) -> None:
            if require_status and str(order.get("status")) != require_status:
                raise ProcurementError(
                    f"procurement {procurement_id} in status {order.get('status')}, expected {require_status}"
                )
            order["status"] = to_status
            order["revision"] = int(order.get("revision") or 1) + 1
            order.setdefault("history", []).append(
                {"at": utc_now().isoformat(), "to": to_status, "actor": actor}
            )
            for key, value in (extra or {}).items():
                order[key] = value

        try:
            return self._store.update(procurement_id, mutate)
        except KeyError as exc:
            raise ProcurementError(f"procurement not found: {procurement_id}") from exc

    def _get(self, procurement_id: str) -> Dict[str, Any]:
        found = self._store.find(lambda o: str(o.get("record_id")) == procurement_id)
        if not found:
            raise ProcurementError(f"procurement not found: {procurement_id}")
        return found[0]

    def get(self, procurement_id: str) -> Dict[str, Any]:
        return self._get(procurement_id)

    def list(self, *, opportunity_id: str = "", status: str = "") -> List[Dict[str, Any]]:
        def predicate(order: Mapping[str, Any]) -> bool:
            if opportunity_id and str(order.get("opportunity_id")) != opportunity_id:
                return False
            if status and str(order.get("status")) != status:
                return False
            return True

        return self._store.find(predicate)
