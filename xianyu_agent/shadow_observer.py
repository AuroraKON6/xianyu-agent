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
from typing import Any, Dict, List, Optional

from .budget_reservations import ShadowAuthorizationLog
from .commerce.repositories import LockedJsonList
from .operating_mandates import ActionRequest, OperatingMandateStore, evaluate_action
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
    ) -> None:
        self._cursor = LockedJsonList(data_dir / "commerce" / "shadow_cursor.json")
        self._drafts = draft_service
        self._opportunities = opportunities
        self._mandates = mandates
        self._log = shadow_log
        self._tick_lock = threading.Lock()

    def _load_processed(self) -> Dict[str, List[str]]:
        records = self._cursor.all()
        return {
            "draft_ids": sorted(str(r.get("id")) for r in records if r.get("kind") == "draft"),
            "opportunity_ids": sorted(str(r.get("id")) for r in records if r.get("kind") == "opportunity"),
        }

    def _mark_processed(self, kind: str, record_id: str) -> None:
        self._cursor.append({"kind": kind, "id": record_id})

    def _record(self, *, action_type: str, account_id: str, sku: str, summary: str, amount=None, listing_price=None) -> None:
        try:
            request = ActionRequest(
                action_type=action_type,
                account_id=account_id or "unknown-account",
                sku=sku,
                amount=amount,
                listing_price=listing_price,
            )
            decision = evaluate_action(request, active_mandates=self._mandates.list_active())
        except Exception as exc:  # noqa: BLE001 - policy errors become denied records
            decision = {"allowed": False, "reasons": [f"policy evaluation error: {exc}"], "matched_mandate": None}
        self._log.record(decision=decision, action_summary=summary[:60])

    def run_tick(self) -> Dict[str, Any]:
        """Observe new events once; returns what was evaluated."""
        with self._tick_lock:
            processed = self._load_processed()
            seen_drafts = set(processed["draft_ids"])
            seen_opportunities = set(processed["opportunity_ids"])

            evaluated: Dict[str, Any] = {"reply_drafts": 0, "qualified_opportunities": 0}

            for draft in self._drafts.list_drafts():
                draft_id = str(draft.get("record_id"))
                if draft_id in seen_drafts:
                    continue
                self._mark_processed("draft", draft_id)
                draft_type = str(draft.get("draft_type") or "")
                if draft_type == DRAFT_FACT_ANSWER:
                    action_type = "faq_reply"
                    amount = None
                elif draft_type in (DRAFT_COUNTER_OFFER, DRAFT_DECLINE):
                    action_type = "price_adjust"
                    offer = draft.get("offer_amount")
                    amount = {"state": "known", "amount": str(offer), "currency": "CNY"} if offer else None
                else:
                    continue  # escalate/takeover drafts are never auto-send candidates
                self._record(
                    action_type=action_type,
                    account_id=str(draft.get("cookie_id") or ""),
                    sku=str(draft.get("item_id") or ""),
                    summary=f"draft:{draft_type} {draft.get('chat_id')}",
                    amount=amount,
                )
                evaluated["reply_drafts"] += 1

            for opportunity in self._opportunities.list(status=OPPORTUNITY_READY_FOR_TEST):
                opportunity_id = str(opportunity.get("record_id"))
                if opportunity_id in seen_opportunities:
                    continue
                self._mark_processed("opportunity", opportunity_id)
                self._record(
                    action_type="listing_publish",
                    account_id=str(opportunity.get("account_id") or ""),
                    sku=str(opportunity.get("canonical_sku") or ""),
                    summary=f"qualified:{opportunity.get('canonical_sku')}",
                )
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
