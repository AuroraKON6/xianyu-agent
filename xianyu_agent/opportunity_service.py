"""Opportunity service (WP06): match -> economics -> status -> decision card.

The service stitches the thin through-flow together: a market-side
observation snapshot and a supplier offer snapshot connected by a
ProductMatch become an Opportunity; ``evaluate`` computes full
contribution economics from the snapshots plus caller-provided current
terms (platform fee rate/fixed come from current account+category
terms, not from snapshots), stores the result on the opportunity, and
writes a DecisionRecord card that WP09 renders.

Honesty rules:
- A NOT_MATCH pair never becomes an opportunity.
- COMPARABLE/UNKNOWN matches create research-only opportunities; they
  cannot reach ``ready_for_test`` (plan 8.2: comparable never shares
  procurement pricing).
- Qualification (``ready_for_test``) requires an EXACT match, a KNOWN
  conservative-scenario profit above zero and fresh snapshots
  (supplier quote <= 24h, market baseline <= 72h, matching the existing
  candidate-activation thresholds).
- Expired inputs never silently qualify; the evaluation result carries
  the staleness and the status does not advance.
- The decision card records what is unknown; the recommendation is a
  suggestion, never an authorization.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from .candidate_activation import MARKET_MAX_AGE_SECONDS, QUOTE_MAX_AGE_SECONDS
from .commerce.contracts import (
    MATCH_COMPARABLE,
    MATCH_EXACT,
    MATCH_NOT_MATCH,
    MATCH_UNKNOWN,
    PRICE_STATE_KNOWN,
    OPPORTUNITY_DISCOVERED,
    OPPORTUNITY_READY_FOR_TEST,
    OPPORTUNITY_RESEARCHING,
    Money,
    build_decision_record,
    build_opportunity,
    utc_now,
)
from .unit_economics import (
    ECONOMICS_RULE_VERSION,
    SCENARIO_CONSERVATIVE,
    compute_unit_economics,
)


class OpportunityError(ValueError):
    pass


class OpportunityService:
    def __init__(self, repos) -> None:
        self._repos = repos

    # ---- creation ----

    def create_from_match(
        self,
        *,
        market_snapshot_id: str,
        supplier_snapshot_id: str,
        match_id: str,
        title: str = "",
    ) -> Dict[str, Any]:
        market = self._repos.offers.get(market_snapshot_id)
        supplier = self._repos.offers.get(supplier_snapshot_id)
        match = self._repos.matches.get(match_id)
        if market is None or supplier is None:
            raise OpportunityError("both snapshots must exist")
        if match is None:
            raise OpportunityError(f"match not found: {match_id}")
        pair = {match.get("left_snapshot_id"), match.get("right_snapshot_id")}
        if pair != {market_snapshot_id, supplier_snapshot_id}:
            raise OpportunityError("match does not connect the two snapshots")
        grade = str(match.get("grade"))
        if grade == MATCH_NOT_MATCH:
            raise OpportunityError("a NOT_MATCH pair is not an opportunity")

        existing = self._repos.opportunities.find_by_natural_key(
            str(supplier.get("source_item_id") or ""), str(supplier.get("provider_id") or "")
        )
        if existing is not None:
            return existing

        record = build_opportunity(
            canonical_sku=str(supplier.get("source_item_id") or ""),
            source=str(supplier.get("provider_id") or ""),
            title=title or str((supplier.get("attributes") or {}).get("name") or ""),
            match_id=match_id,
            offer_snapshot_ids=[supplier_snapshot_id],
            market_snapshot_id=market_snapshot_id,
        )
        stored = self._repos.opportunities.create(record)
        return stored

    # ---- evaluation ----

    def evaluate(
        self,
        opportunity_id: str,
        *,
        platform_fee_rate: Optional[str] = None,
        platform_fee_fixed: Optional[str] = None,
        packaging: Optional[Mapping[str, Any]] = None,
        after_sales_reserve: Optional[Mapping[str, Any]] = None,
        attributable_costs: Optional[Mapping[str, Any]] = None,
        return_probability: Optional[str] = None,
        return_loss_amount: Optional[Mapping[str, Any]] = None,
        expected_return_loss: Optional[Mapping[str, Any]] = None,
        scenario_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
        target_margin: Optional[str] = None,
    ) -> Dict[str, Any]:
        opportunity = self._repos.opportunities.get(opportunity_id)
        if opportunity is None:
            raise OpportunityError(f"opportunity not found: {opportunity_id}")

        market = self._repos.offers.get(str(opportunity.get("market_snapshot_id") or ""))
        supplier_ids = list(opportunity.get("offer_snapshot_ids") or [])
        supplier = self._repos.offers.get(supplier_ids[-1]) if supplier_ids else None
        if market is None or supplier is None:
            raise OpportunityError("opportunity snapshots missing; re-import or re-observe")
        match = self._repos.matches.get(str(opportunity.get("match_id") or ""))

        now = utc_now()
        freshness = self._freshness(market, supplier, now)

        sale_price = market.get("eligible_price") or market.get("list_price")
        economics = compute_unit_economics(
            sale_price=sale_price,
            procurement_cost=supplier.get("eligible_price"),
            supplier_freight=supplier.get("shipping_quote"),
            packaging=packaging,
            platform_fee_rate=platform_fee_rate,
            platform_fee_fixed=platform_fee_fixed,
            return_probability=return_probability,
            return_loss_amount=return_loss_amount,
            expected_return_loss=expected_return_loss,
            after_sales_reserve=after_sales_reserve,
            attributable_costs=attributable_costs,
            moq=supplier.get("moq"),
            scenario_overrides=scenario_overrides,
            target_margin=target_margin,
        )
        economics["inputs"] = {
            "market_snapshot_id": market["record_id"],
            "market_observed_at": market.get("observed_at"),
            "supplier_snapshot_id": supplier["record_id"],
            "supplier_observed_at": supplier.get("observed_at"),
            "match_id": match["record_id"] if match else None,
            "match_grade": match.get("grade") if match else None,
            "platform_fee_source": "caller-provided current terms"
            if platform_fee_rate is not None or platform_fee_fixed is not None
            else "missing",
        }
        economics["freshness"] = freshness

        revision = int(opportunity.get("revision") or 1)
        if opportunity.get("status") == OPPORTUNITY_DISCOVERED:
            self._repos.opportunities.transition(
                opportunity_id,
                to_status=OPPORTUNITY_RESEARCHING,
                reason=f"economics computed ({ECONOMICS_RULE_VERSION})",
                evidence_refs=[market["record_id"], supplier["record_id"]],
                expected_revision=revision,
            )
            opportunity = self._repos.opportunities.get(opportunity_id)
            revision = int(opportunity.get("revision") or 1)
        self._repos.opportunities.attach(
            opportunity_id, field="economics", value=economics, expected_revision=revision
        )
        self._repos.opportunities.attach(
            opportunity_id,
            field="economics_ref",
            value=f"computed:{ECONOMICS_RULE_VERSION}:{now.isoformat()}",
        )

        qualification = self._qualify(opportunity, economics, match, freshness)
        decision = self._write_decision(opportunity, economics, match, qualification)
        self._repos.opportunities.attach(opportunity_id, field="decision_ids", value=decision["record_id"])

        current = self._repos.opportunities.get(opportunity_id)
        if (
            qualification["qualified"]
            and str(current.get("status")) == OPPORTUNITY_RESEARCHING
        ):
            profit = economics["scenarios"][SCENARIO_CONSERVATIVE]["contribution_profit"]
            self._repos.opportunities.transition(
                opportunity_id,
                to_status=OPPORTUNITY_READY_FOR_TEST,
                reason=(
                    f"deterministic gates passed: EXACT match, conservative profit "
                    f"{profit.get('amount')} {profit.get('currency') or ''}, fresh inputs"
                ),
                evidence_refs=[decision["record_id"]],
            )
            current = self._repos.opportunities.get(opportunity_id)
        return {"opportunity": current, "economics": economics, "decision": decision, "qualification": qualification}

    # ---- helpers ----

    def _freshness(self, market: Mapping[str, Any], supplier: Mapping[str, Any], now: Any) -> Dict[str, Any]:
        def age_seconds(observed_at: Any) -> Optional[float]:
            if not observed_at:
                return None
            from datetime import datetime

            try:
                moment = datetime.fromisoformat(str(observed_at))
            except ValueError:
                return None
            if moment.tzinfo is None:
                return None
            return (now - moment).total_seconds()

        market_age = age_seconds(market.get("observed_at"))
        supplier_age = age_seconds(supplier.get("observed_at"))
        market_fresh = market_age is not None and market_age <= MARKET_MAX_AGE_SECONDS
        supplier_fresh = supplier_age is not None and supplier_age <= QUOTE_MAX_AGE_SECONDS
        return {
            "market_age_seconds": market_age,
            "supplier_age_seconds": supplier_age,
            "market_fresh": market_fresh,
            "supplier_fresh": supplier_fresh,
            "all_fresh": market_fresh and supplier_fresh,
        }

    def _qualify(
        self,
        opportunity: Mapping[str, Any],
        economics: Mapping[str, Any],
        match: Optional[Mapping[str, Any]],
        freshness: Mapping[str, Any],
    ) -> Dict[str, Any]:
        conservative = economics["scenarios"][SCENARIO_CONSERVATIVE]
        profit = conservative.get("contribution_profit") or {}
        profit_state = str(profit.get("state"))
        grade = str(match.get("grade")) if match else "none"
        profit_ok = profit_state == PRICE_STATE_KNOWN and Decimal(str(profit.get("amount"))) > 0
        checks = {
            "exact_match": grade == MATCH_EXACT,
            "profit_known_positive": profit_ok,
            "inputs_fresh": bool(freshness.get("all_fresh")),
        }
        reasons: List[str] = []
        if not checks["exact_match"]:
            reasons.append(f"match grade {grade} is below EXACT; direct arbitrage not allowed")
        if not checks["profit_known_positive"]:
            if profit_state != PRICE_STATE_KNOWN:
                reasons.append("conservative profit unknown: " + "; ".join(conservative.get("missing") or []))
            else:
                reasons.append(f"conservative profit {profit.get('amount')} not positive")
        if not checks["inputs_fresh"]:
            reasons.append(
                "inputs stale (market fresh={market}, supplier fresh={supplier})".format(
                    market=freshness.get("market_fresh"), supplier=freshness.get("supplier_fresh")
                )
            )
        return {"qualified": not reasons, "reasons": reasons, "checks": checks}

    def _write_decision(
        self,
        opportunity: Mapping[str, Any],
        economics: Mapping[str, Any],
        match: Optional[Mapping[str, Any]],
        qualification: Mapping[str, Any],
    ) -> Dict[str, Any]:
        conservative = economics["scenarios"][SCENARIO_CONSERVATIVE]
        profit = conservative.get("contribution_profit") or {}
        unknowns = list(economics.get("evidence_gaps") or [])
        if qualification["qualified"]:
            summary = (
                "conservative contribution profit {amount} {currency}; EXACT match and fresh inputs; "
                "recommend moving to ready_for_test"
            ).format(amount=profit.get("amount"), currency=profit.get("currency") or "CNY")
            recommendation = "advance to ready_for_test (operator approval required for any listing action)"
        else:
            summary = "not qualified: " + "; ".join(qualification["reasons"])
            recommendation = "stay in research; close the listed gaps before testing"
        decision = build_decision_record(
            summary=summary,
            actor_role="pricing_agent",
            decision_type="opportunity_review",
            target_ref=str(opportunity.get("record_id")),
            opportunity_id=str(opportunity.get("record_id")),
            options=[
                {"option": "advance", "note": "requires EXACT match, positive conservative profit, fresh inputs"},
                {"option": "keep_researching", "note": "close evidence gaps"},
                {"option": "reject", "note": "no viable path to positive contribution profit"},
            ],
            recommendation=recommendation,
            evidence_refs=[
                {"ref_type": "market_snapshot", "ref_id": economics["inputs"]["market_snapshot_id"]},
                {"ref_type": "supplier_snapshot", "ref_id": economics["inputs"]["supplier_snapshot_id"]},
                {"ref_type": "product_match", "ref_id": economics["inputs"].get("match_id") or ""},
                {"ref_type": "economics", "ref_id": f"{ECONOMICS_RULE_VERSION}@{economics['inputs']['supplier_observed_at']}"},
            ],
            input_versions={
                "market_snapshot_revision": 1,
                "supplier_snapshot_revision": 1,
                "economics_rule": ECONOMICS_RULE_VERSION,
                "match_rule": (match or {}).get("rule_version"),
            },
            constraint_checks=[
                {"check": name, "state": "pass" if passed else "blocked"}
                for name, passed in qualification["checks"].items()
            ],
            unknowns=unknowns or ["none"],
            model_version="deterministic-v1",
            authorization_type="human",
            result=None,
        )
        return self._repos.decisions.append(decision)

    # ---- projections ----

    def list_opportunities(self, *, status: str = "") -> List[Dict[str, Any]]:
        return self._repos.opportunities.list(status=status)

    def funnel(self) -> Dict[str, Any]:
        """Raw discovered / matched / costed / qualified counters (plan 8.4)."""
        opportunities = self._repos.opportunities.list()
        costed = 0
        qualified = 0
        for record in opportunities:
            economics = record.get("economics") or {}
            conservative = (economics.get("scenarios") or {}).get(SCENARIO_CONSERVATIVE) or {}
            if str((conservative.get("contribution_profit") or {}).get("state")) == PRICE_STATE_KNOWN:
                costed += 1
                if Decimal(str(conservative["contribution_profit"].get("amount"))) > 0:
                    qualified += 1
        matched = sum(1 for r in opportunities if r.get("match_id"))
        return {
            "discovered_total": len(opportunities),
            "with_match": matched,
            "cost_complete": costed,
            "positive_conservative_profit": qualified,
            "note": "counts are facts about stored records, not business results",
        }

    def decision_card(self, opportunity_id: str) -> Dict[str, Any]:
        """Assemble the WP09 decision card projection."""
        opportunity = self._repos.opportunities.get(opportunity_id)
        if opportunity is None:
            raise OpportunityError(f"opportunity not found: {opportunity_id}")
        decision_ids = list(opportunity.get("decision_ids") or [])
        cards = [self._repos.decisions.get(d) for d in decision_ids]
        cards = [c for c in cards if c]
        economics = opportunity.get("economics") or {}
        return {
            "opportunity": opportunity,
            "decisions": cards,
            "economics": economics,
            "funnel": self.funnel(),
        }
