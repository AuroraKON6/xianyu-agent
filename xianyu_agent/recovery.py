"""Crash and consistency recovery (drill round).

The alpha stores are separate JSON files with no cross-file
transaction: a crash between the inventory reservation and the
procurement append (or between a cancel transition and the lock
release) can leave a reservation that no live procurement owns. This
module reconciles those leftovers after a restart:

- ``reconcile_orphan_reservations`` releases every inventory lock keyed
  ``proc:{id}`` whose procurement order is missing (crash before the
  append) or cancelled (crash before the release). Locks of live
  orders are untouched; non-procurement locks are never touched.
- The recovery is idempotent and reports every action it took; with
  ``dry_run`` it only reports.

Agent-job lease recovery already lives in
``AgentJobStore.recover_expired_leases`` (analysis requeue, write jobs
to unknown); the restart drills exercise it end to end.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .inventory import InventoryAllocationService
from .procurement import ProcurementError, ProcurementService

RESERVATION_KEY_PREFIX = "proc:"


def reconcile_orphan_reservations(
    procurement: ProcurementService,
    inventory: InventoryAllocationService,
    *,
    dry_run: bool = False,
    operator: str = "recovery",
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "scanned": 0,
        "released": [],
        "kept_live": 0,
        "skipped_non_procurement": 0,
        "dry_run": dry_run,
    }
    # The check (procurement exists?) and the release must run inside the
    # SAME exclusion domain as procurement creation/cancel: a creation
    # parked between its inventory lock and its store append holds this
    # lock, so the reconciler waits instead of releasing the in-flight
    # reservation.
    with procurement.mutation_guard():
        for lock in inventory.list_locks():
            key = str(lock.get("order_id") or "")
            if not key.startswith(RESERVATION_KEY_PREFIX):
                report["skipped_non_procurement"] += 1
                continue
            report["scanned"] += 1
            procurement_id = key[len(RESERVATION_KEY_PREFIX):]
            try:
                order = procurement.get(procurement_id)
            except ProcurementError:
                order = None
            if order is not None and str(order.get("status")) != "cancelled":
                report["kept_live"] += 1
                continue
            cause = "procurement missing (crash before store)" if order is None else "procurement cancelled (crash before release)"
            if dry_run:
                report["released"].append({"order_id": key, "quantity": lock.get("quantity"), "cause": cause, "action": "would_release"})
                continue
            inventory.release_order(
                account_id=str(lock.get("account_id") or ""),
                sku=str(lock.get("sku") or ""),
                order_id=key,
                reason=f"recovery: {cause}",
            )
            report["released"].append({"order_id": key, "quantity": lock.get("quantity"), "cause": cause, "action": "released"})
    return report
