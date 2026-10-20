"""Continuous shadow observation of real internal events (stage 2).

Every real internal proposal -- a reply draft produced from verified
facts, or an opportunity that just qualified -- is automatically run
through the deterministic authorization policy in SHADOW mode: the
verdict ("would the mandate layer allow this automatically?") is
recorded, nothing is executed. This accumulates honest shadow samples
from real events; with no mandates configured the records say "no
active mandate" and the gate counters stay truthful.

Idempotency: a persisted cursor tracks which draft ids and which
opportunity ids have already been observed; ticks reprocess nothing.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .budget_reservations import ShadowAuthorizationLog
from .commerce.repositories import LockedJsonList
from .operating_mandates import ActionRequest, OperatingMandateStore, evaluate_action, today_key
from .providers.freshness import FRESH_CURRENT, classify_snapshot
from .reply_drafts import DRAFT_COUNTER_OFFER, DRAFT_DECLINE, DRAFT_FACT_ANSWER

OPPORTUNITY_READY_FOR_TEST = "ready_for_test"


class ShadowObserver:
    def __init__(
        self,
        data_dir,
        *,
        draft_service,
        opportunities,
        mandates: OperatingMandateStore,
        shadow_log: ShadowAuthorizationLog,
        budget_store=None,
        offers=None,
        import_service=None,
    ) -> None:
        self._cursor = LockedJsonList(data_dir / "commerce" / "shadow_cursor.json")
        # counterfactual accumulation: shadow allowances only, never real money
        self._shadow_usage = LockedJsonList(data_dir / "commerce" / "shadow_usage.json")
        self._drafts = draft_service
        self._opportunities = opportunities
        self._mandates = mandates
        self._log = shadow_log
        self._budgets = budget_store
        self._offers = offers
        self._imports = import_service
        self._tick_lock = threading.Lock()

    def _load_processed(self) -> Dict[str, List[str]]:
        records = self._cursor.all()
        return {
            "draft_ids": sorted(str(r.get("id")) for r in records if r.get("kind") == "draft"),
            "opportunity_ids": sorted(str(r.get("id")) for r in records if r.get("kind") == "opportunity"),
        }

    def _mark_processed(self, kind: str, record_id: str) -> None:
        self._cursor.append({"kind": kind, "id": record_id})

    def _usage_projection(self, active_mandates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Real committed usage today PLUS shadow-allowed accumulation.

        Real usage comes from the budget reservation store; shadow usage
        is this observer's own counterfactual ledger (allowed actions
        only). Neither is ever fabricated as zero when unknown -- a
        missing real store simply contributes nothing extra.
        """
        day = today_key()
        usage: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        for mandate in active_mandates:
            mandate_id = str(mandate.get("record_id"))
            total = Decimal("0")
            if self._budgets is not None and str(mandate.get("account_id") or ""):
                real = self._budgets.usage(
                    account_id=str(mandate.get("account_id")), bucket="mandate_daily", day=day
                )
                total += Decimal(str(real.get("committed_total") or "0"))
            shadow_count = 0
            for entry in self._shadow_usage.find(
                lambda r, mid=mandate_id, d=day: str(r.get("mandate_id")) == mid and str(r.get("business_day")) == d
            ):
                amount = (entry.get("amount") or {}).get("amount")
                if amount is not None:
                    total += Decimal(str(amount))
                shadow_count += 1
            usage[mandate_id] = float(total)
            counts[mandate_id] = shadow_count
        return {"usage": usage, "counts": counts}

    def _record_shadow_allowance(self, mandate_id: str, *, amount, day: str, event_id: str) -> None:
        # idempotent by event id: a replay after a cursor-write crash must
        # not accrue the same allowance twice
        existing = self._shadow_usage.find(lambda r: str(r.get("event_id")) == event_id)
        if existing:
            return
        self._shadow_usage.append(
            {
                "mandate_id": mandate_id,
                "business_day": day,
                "amount": amount if amount is not None else None,
                "event_id": event_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _record(
        self,
        *,
        event_id: str,
        action_type: str,
        account_id: str,
        sku: str,
        summary: str,
        amount=None,
        listing_price=None,
        provenance=None,
    ) -> None:
        # REPLAY uses the saved verdict: if this event was already judged
        # (e.g. the cursor write failed after the log write), the stored
        # record is authoritative -- no re-evaluation, no re-accrual
        existing = self._log.find_by_event(event_id)
        if existing is not None:
            self._restore_allowance(existing)
            return
        active = self._mandates.list_active()
        projection = self._usage_projection(active)
        try:
            request = ActionRequest(
                action_type=action_type,
                account_id=account_id or "unknown-account",
                sku=sku,
                amount=amount,
                listing_price=listing_price,
            )
            decision = evaluate_action(
                request,
                active_mandates=active,
                budget_usage_today=projection["usage"],
                mandate_action_counts=projection["counts"],
            )
        except Exception as exc:  # noqa: BLE001 - policy errors become denied records
            decision = {"allowed": False, "reasons": [f"policy evaluation error: {exc}"], "matched_mandate": None}
        # the LOG write happens FIRST and is idempotent by event id; the
        # cursor only advances after the record is durably stored
        saved = self._log.record(decision=decision, action_summary=summary[:60],
                                 event_id=event_id, allowance_amount=amount, provenance=provenance)
        self._restore_allowance(saved)

    def _opportunity_provenance(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        moment = datetime.now(timezone.utc)
        supplier_ids = opportunity.get("offer_snapshot_ids") or []
        supplier_id = str(supplier_ids[-1]) if supplier_ids else ""
        market_id = str(opportunity.get("market_snapshot_id") or "")
        supplier = self._offers.get(supplier_id) if self._offers is not None else None
        market = self._offers.get(market_id) if self._offers is not None else None
        supplier_facts = classify_snapshot(supplier or {}, now=moment)
        market_facts = classify_snapshot(market or {}, now=moment)
        reasons = list(supplier_facts.get("reasons") or [])
        if supplier_facts["freshness"] != FRESH_CURRENT:
            reasons.append("supplier_source_not_current")
        if market_facts["freshness"] != FRESH_CURRENT:
            reasons.append("market_source_not_current")
        source_ref = str((supplier or {}).get("source_ref") or "")
        batch_id = source_ref.split(":", 1)[1] if source_ref.startswith("import_batch:") else ""
        batch = next((b for b in self._imports.list_batches()
                      if b.get("record_id") == batch_id), None) if self._imports is not None else None
        if not batch or batch.get("status") != "committed" or supplier_id not in batch.get("snapshot_ids", []):
            reasons.append("supplier_import_not_committed_or_bound")
        return {
            "eligible": not reasons,
            "source_kind": supplier_facts.get("source_kind", "unknown"),
            "supplier_snapshot_id": supplier_id, "market_snapshot_id": market_id,
            "import_batch_id": batch_id, "source_reference": supplier_facts.get("source_reference"),
            "assessed_at": moment.isoformat(), "reasons": reasons, "provider_verified": False,
        }

    def _restore_allowance(self, saved: Dict[str, Any]) -> None:
        if not saved.get("would_allow"):
            return
        event_id = str(saved.get("event_id") or "")
        mandate_id = str((saved.get("matched_mandate") or {}).get("mandate_id") or "")
        day = str(saved.get("business_day") or "")
        if not event_id or not mandate_id or not day:
            raise RuntimeError("shadow allowance evidence incomplete; manual reconciliation required")
        existing = self._shadow_usage.find(lambda r: str(r.get("event_id")) == event_id)
        if existing:
            expected = {"mandate_id": mandate_id, "business_day": day}
            if "allowance_amount" in saved:
                expected["amount"] = saved["allowance_amount"]
            if len(existing) != 1 or "amount" not in existing[0] or any(existing[0].get(k) != v for k, v in expected.items()):
                raise RuntimeError("shadow allowance projection conflicts with log; manual reconciliation required")
            return
        if "allowance_amount" not in saved:
            raise RuntimeError("shadow allowance evidence incomplete; manual reconciliation required")
        self._record_shadow_allowance(mandate_id, amount=saved["allowance_amount"],
                                      day=day, event_id=event_id)

    def run_tick(self) -> Dict[str, Any]:
        """Observe new events once; returns what was evaluated."""
        with self._tick_lock:
            # Recover from durable verdicts before new policy evaluations, even
            # if a source draft disappeared or an older cursor already advanced.
            for saved in self._log.list_events():
                self._restore_allowance(saved)
            processed = self._load_processed()
            seen_drafts = set(processed["draft_ids"])
            seen_opportunities = set(processed["opportunity_ids"])

            evaluated: Dict[str, Any] = {"reply_drafts": 0, "qualified_opportunities": 0}

            for draft in self._drafts.list_drafts():
                draft_id = str(draft.get("record_id"))
                if draft_id in seen_drafts:
                    continue
                draft_type = str(draft.get("draft_type") or "")
                if draft_type == DRAFT_FACT_ANSWER:
                    action_type = "faq_reply"
                    amount = None
                elif draft_type in (DRAFT_COUNTER_OFFER, DRAFT_DECLINE):
                    action_type = "price_adjust"
                    offer = draft.get("offer_amount")
                    amount = {"state": "known", "amount": str(offer), "currency": "CNY"} if offer else None
                else:
                    self._mark_processed("draft", draft_id)  # never a send candidate
                    continue
                # log-first: a failure here aborts the tick BEFORE the
                # cursor advances, so a retry reprocesses the event
                self._record(
                    event_id=f"draft:{draft_id}",
                    action_type=action_type,
                    account_id=str(draft.get("cookie_id") or ""),
                    sku=str(draft.get("item_id") or ""),
                    summary=f"draft:{draft_type} {draft.get('chat_id')}",
                    amount=amount,
                )
                self._mark_processed("draft", draft_id)
                evaluated["reply_drafts"] += 1

            for opportunity in self._opportunities.list(status=OPPORTUNITY_READY_FOR_TEST):
                opportunity_id = str(opportunity.get("record_id"))
                if opportunity_id in seen_opportunities:
                    continue
                self._record(
                    event_id=f"opportunity:{opportunity_id}",
                    action_type="listing_publish",
                    account_id=str(opportunity.get("account_id") or ""),
                    sku=str(opportunity.get("canonical_sku") or ""),
                    summary=f"qualified:{opportunity.get('canonical_sku')}",
                    provenance=self._opportunity_provenance(opportunity),
                )
                self._mark_processed("opportunity", opportunity_id)
                evaluated["qualified_opportunities"] += 1

            evaluated["mode"] = "shadow"
            return evaluated


def start_background_observer(observer: ShadowObserver, *, interval_seconds: float, enabled: bool) -> Optional[threading.Thread]:
    """Daemon tick loop; disabled flag returns None (no thread)."""
    if not enabled or interval_seconds <= 0:
        return None

    stop_event = threading.Event()

    def loop() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                observer.run_tick()
            except Exception:  # noqa: BLE001 - one bad tick never kills the loop
                continue

    thread = threading.Thread(target=loop, name="shadow-observer", daemon=True)
    thread.start()
    thread.stop_event = stop_event  # type: ignore[attr-defined]
    return thread


def stop_background_observer(thread: Optional[threading.Thread]) -> bool:
    """Signal a started observer thread to stop and wait briefly (R02)."""
    if thread is None:
        return False
    stop_event = getattr(thread, "stop_event", None)
    if stop_event is None:
        return False
    stop_event.set()
    thread.join(timeout=5.0)
    return not thread.is_alive()
