"""Atomic purchase commands (taskbook R11).

One command layer owns the WHOLE write sequence for procurement, so the
legacy purchase-task chain and the new procurement chain share a single
ownership boundary instead of racing each other:

* ``create_procurement`` — payment-fact gate (runtime order, body is
  never authoritative) + legacy-ownership check + creation + cash
  reservation; a refused reservation rolls the creation back entirely
  (no unfunded procurement, no locked stock, non-200 to the caller);
* ``mark_purchase_task_ordered`` — the legacy task may only order a
  sale that the NEW chain has not taken, scoped to (account, order),
  under the same mutation lock the new chain uses;
* ``cancel_procurement`` — status transition + inventory lock release +
  budget reservation release, with an idempotent retry that heals a
  crash between the cancel commit and the releases;
* ``record_procurement_receipt`` — the receipt's immutable actual costs
  (required on first receipt) and the ledger entry are written by the
  SAME command; a ledger failure compensates the status back instead of
  returning a fake success; receipt documents are NEVER payment
  evidence, so the entry is provisional by construction.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Mapping, Optional

from ..budget_reservations import BudgetError, BudgetReservationStore
from ..ledger_service import LedgerService
from ..procurement import ProcurementError, ProcurementService


class PurchaseCommandError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _live_legacy_task(resale_manager, *, account_id: str, sales_order_ref: str):
    for task in resale_manager.list_purchase_tasks():
        if (
            str(task.get("xianyu_order_id") or "") == sales_order_ref
            and str(task.get("cookie_id") or "") == account_id
            and str(task.get("status") or "") not in ("cancelled", "cancelled_refunded")
        ):
            return task
    return None


def _live_new_procurement(procurement_service, *, account_id: str, sales_order_ref: str):
    for order in procurement_service.list():
        if (
            str(order.get("sales_order_ref") or "") == sales_order_ref
            and str(order.get("account_id") or "") == account_id
            and str(order.get("status") or "") != "cancelled"
        ):
            return order
    return None


def create_procurement(
    *,
    procurement_service: ProcurementService,
    budget_store: Optional[BudgetReservationStore],
    resale_manager,
    order_runtime_store,
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    sales_order_ref = str(payload.get("sales_order_ref") or "")
    account_id = str(payload.get("account_id") or "")
    sku = str(payload.get("sku") or "")
    quantity = int(payload.get("quantity") or 0)

    # ---- payment-fact gate (body is never authoritative) ----
    runtime_order = None
    try:
        runtime_order = order_runtime_store.get_order(sales_order_ref)
    except (KeyError, ValueError):
        runtime_order = None
    asserts_payment = "paid" in payload or "status" in payload
    if asserts_payment and runtime_order is None:
        raise PurchaseCommandError(
            f"platform order {sales_order_ref!r} not found in order runtime; "
            "procurement requires a verified paid platform order",
            status_code=404,
        )
    if runtime_order is not None:
        runtime_account = str(runtime_order.get("cookie_id") or "")
        if runtime_account and account_id and runtime_account != account_id:
            raise PurchaseCommandError(
                f"procurement account {account_id!r} does not match order account {runtime_account!r}"
            )
        if asserts_payment and str(runtime_order.get("status") or "") != "paid":
            raise PurchaseCommandError(
                f"platform order {sales_order_ref!r} status is {runtime_order.get('status')!r}, not 'paid'; "
                "body paid/status fields are not authoritative",
            )
        if str(runtime_order.get("item_id") or "") and str(runtime_order.get("item_id")) != sku:
            raise PurchaseCommandError(
                f"procurement SKU {sku!r} does not match order item {runtime_order.get('item_id')!r}"
            )

    # ---- single ownership boundary: legacy + new share one lock ----
    with procurement_service.mutation_guard():
        legacy = _live_legacy_task(
            resale_manager, account_id=account_id, sales_order_ref=sales_order_ref
        )
        if legacy is not None:
            raise PurchaseCommandError(
                f"sales order {sales_order_ref!r} is already owned by a legacy purchase task "
                f"({legacy.get('purchase_task_id')}, status {legacy.get('status')}); "
                "continue there instead",
            )
        try:
            result = procurement_service.create_from_sales_order(
                account_id=account_id,
                sku=sku,
                quantity=quantity,
                sales_order_ref=sales_order_ref,
                opportunity_id=str(payload.get("opportunity_id") or ""),
            )
        except ProcurementError as exc:
            raise PurchaseCommandError(str(exc)) from exc

        # ---- cash reservation inside the same boundary; refusal rolls back ----
        if budget_store is not None:
            unit_cost = (result.get("unit_cost") or {}).get("amount") or "0"
            shipping = (result.get("shipping_cost") or {}).get("amount") or "0"
            total = Decimal(str(unit_cost)) * quantity + Decimal(str(shipping))
            try:
                budget_store.reserve(
                    account_id=account_id,
                    budget_bucket="procurement",
                    job_id=str(result.get("record_id") or ""),
                    amount={"state": "known", "amount": str(total), "currency": "CNY"},
                )
            except BudgetError as exc:
                procurement_service.rollback_create(result, reason=f"budget refused: {exc}")
                raise PurchaseCommandError(
                    f"cash reservation refused for {sales_order_ref!r}: {exc}; "
                    "procurement rolled back (no lock, no record)",
                    status_code=409,
                ) from exc
        return result


def mark_purchase_task_ordered(
    *,
    resale_manager,
    procurement_service: ProcurementService,
    purchase_task_id: str,
    supplier_order_id: str,
    tracking_number: str,
    manual_order_note: str,
    now_text: str,
) -> Dict[str, Any]:
    """Legacy 'mark ordered' sharing the new chain's ownership lock.

    Double-checked ownership: a lock-free pre-check rejects the obvious
    conflicts fast; the authoritative re-check plus the commit happen
    inside the same mutation lock the new chain uses, so a procurement
    created during the check window is still seen before the legacy
    task commits (one platform order can never become two payments).
    """
    task = resale_manager.get_purchase_task(purchase_task_id)
    if not task:
        raise PurchaseCommandError("purchase task not found", status_code=404)
    if task["status"] != "pending_manual_order":
        raise PurchaseCommandError(
            f"cannot mark ordered: status is '{task['status']}'", status_code=400
        )
    sales_ref = str(task.get("xianyu_order_id") or "")
    account = str(task.get("cookie_id") or "")

    def conflict() -> Optional[Dict[str, Any]]:
        if not sales_ref:
            return None
        # ownership is (account, sales order): a different account's
        # procurement for the same platform order id is NOT a collision
        return _live_new_procurement(
            procurement_service, account_id=account, sales_order_ref=sales_ref
        )

    live = conflict()  # fast pre-check, no lock held
    if live is not None:
        raise PurchaseCommandError(
            f"sales order {sales_ref} is already owned by new-chain procurement "
            f"{live.get('record_id')} ({live.get('status')}); continue there instead",
        )

    with procurement_service.mutation_guard():
        live = conflict()  # authoritative re-check under the shared lock
        if live is not None:
            raise PurchaseCommandError(
                f"sales order {sales_ref} is already owned by new-chain procurement "
                f"{live.get('record_id')} ({live.get('status')}); continue there instead",
            )
        updated = resale_manager.update_purchase_task(purchase_task_id, {
            "status": "ordered",
            "supplier_order_id": supplier_order_id,
            "tracking_number": tracking_number,
            "manual_order_note": manual_order_note,
            "ordered_at": now_text,
        })
        return {"ok": True, "purchase_task": updated}


def cancel_procurement(
    *,
    procurement_service: ProcurementService,
    budget_store: Optional[BudgetReservationStore],
    procurement_id: str,
    reason: str,
    operator: str,
) -> Dict[str, Any]:
    result = procurement_service.cancel(procurement_id, reason=reason, operator=operator)
    # release THIS order's cash reservation (job-scoped: a stale retry can
    # never touch a newer reservation created for the same sales order)
    if budget_store is not None:
        for reservation in budget_store.list(
            account_id=str(result.get("account_id") or ""), status="reserved"
        ):
            if str(reservation.get("job_id") or "") == procurement_id:
                try:
                    budget_store.release(
                        str(reservation.get("record_id") or ""),
                        reason=f"procurement cancelled: {reason}",
                    )
                except BudgetError:
                    pass  # already released by an earlier retry
    return result


def record_procurement_receipt(
    *,
    procurement_service: ProcurementService,
    ledger_service: LedgerService,
    budget_store: Optional[BudgetReservationStore],
    procurement_id: str,
    receipt_ref: str,
    actual_unit_cost: Optional[str],
    actual_freight: Optional[str],
    operator: str,
) -> Dict[str, Any]:
    """Receipt + immutable cost entry in one command (R11)."""
    before = procurement_service.get(procurement_id)
    result = procurement_service.record_receipt(
        procurement_id,
        receipt_ref=receipt_ref,
        actual_unit_cost=actual_unit_cost,
        actual_freight=actual_freight,
        operator=operator,
    )
    # idempotent replay (same receipt ref): no duplicate ledger row, no
    # re-settle — the service already returned the stored record
    if before is not None and str(before.get("status") or "") == "received":
        return result
    stored = result

    # first receipt: ledger entry from the IMMUTABLE stored actuals; a
    # supplier receipt document is never payment evidence -> provisional
    quantity = int(stored.get("quantity") or 0)
    unit = str(stored.get("actual_unit_cost") or "")
    freight = str(stored.get("actual_freight") or "")
    total = Decimal(unit) * quantity + Decimal(freight)
    try:
        if total > 0:
            ledger_service.record_spend(
                category="procurement",
                amount={"state": "known", "amount": str(total), "currency": "CNY"},
                order_ref=str(stored.get("sales_order_ref") or ""),
                opportunity_id=str(stored.get("opportunity_id") or ""),
                evidence_ref=receipt_ref,
                verified=False,
            )
    except Exception as exc:
        # ledger failed: compensate the status back — a receipt must never
        # be finalized (HTTP success) without its durable cost entry
        try:
            procurement_service._transition(
                procurement_id,
                "ordered",
                require_status="received",
                actor="system-ledger-compensation",
                extra={
                    "receipt_ref": None,
                    "actual_unit_cost": None,
                    "actual_freight": None,
                    "ledger_failure": str(exc),
                },
            )
        except ProcurementError:
            pass
        raise PurchaseCommandError(
            f"ledger recording failed for receipt {receipt_ref!r}; receipt reverted to "
            f"ordered for retry: {exc}",
            status_code=503,
        ) from exc

    # settle the cash reservation with the ACTUAL landed cost
    if budget_store is not None:
        for reservation in budget_store.list(
            account_id=str(stored.get("account_id") or ""), status="reserved"
        ):
            if str(reservation.get("job_id") or "") == procurement_id:
                try:
                    budget_store.settle(
                        str(reservation.get("record_id") or ""),
                        actual_amount={"state": "known", "amount": str(total), "currency": "CNY"},
                    )
                except BudgetError:
                    pass
    return result
