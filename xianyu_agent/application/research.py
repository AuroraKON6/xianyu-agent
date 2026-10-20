"""Read-only research projections (taskbook R02, feeds R07 export).

``ResearchService`` exposes strictly read-only views over the JSON
commerce repositories (offers / matches / opportunities / decisions).
It must never mutate anything: GET routes (R04) and the candidate
exporter (R07) both build on it, so a write here would silently turn a
query into a business action.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from ..commerce import JsonCommerceRepositories


class ResearchService:
    def __init__(
        self,
        repositories: JsonCommerceRepositories,
        *,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._repositories = repositories
        self._clock = clock

    @property
    def repositories(self) -> JsonCommerceRepositories:
        """Read-only access for exporters that freeze input versions."""
        return self._repositories

    # ---- offers (quote snapshots) ---------------------------------------

    def list_offer_snapshots(
        self, *, provider_id: str = "", source_item_id: str = ""
    ) -> List[Dict[str, Any]]:
        return list(
            self._repositories.offers.list(
                provider_id=provider_id, source_item_id=source_item_id
            )
        )

    def get_offer_snapshot(self, record_id: str) -> Optional[Dict[str, Any]]:
        return self._repositories.offers.get(record_id)

    # ---- opportunities (candidates) -------------------------------------

    def list_opportunities(self, *, status: str = "") -> List[Dict[str, Any]]:
        return list(self._repositories.opportunities.list(status=status))

    def get_opportunity(self, record_id: str) -> Optional[Dict[str, Any]]:
        return self._repositories.opportunities.get(record_id)

    def latest_match_for_opportunity(
        self, opportunity: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        left = str(opportunity.get("left_snapshot_id") or opportunity.get("supplier_snapshot_id") or "")
        right = str(opportunity.get("right_snapshot_id") or opportunity.get("market_snapshot_id") or "")
        if not left or not right:
            return None
        return self._repositories.matches.latest_for_pair(left, right)

    # ---- decisions (audit trail) ----------------------------------------

    def list_decisions(self, *, opportunity_id: str = "", actor_role: str = "") -> List[Dict[str, Any]]:
        return list(
            self._repositories.decisions.list(opportunity_id=opportunity_id, actor_role=actor_role)
        )

    # ---- composite read view --------------------------------------------

    def candidate_view(self, opportunity_id: str) -> Optional[Dict[str, Any]]:
        """Opportunity + its snapshots + latest match + decision count."""
        opportunity = self.get_opportunity(opportunity_id)
        if opportunity is None:
            return None
        left = self.get_offer_snapshot(str(opportunity.get("left_snapshot_id") or ""))
        right = self.get_offer_snapshot(str(opportunity.get("right_snapshot_id") or ""))
        decisions = self.list_decisions(opportunity_id=opportunity_id)
        return {
            "opportunity": opportunity,
            "supplier_snapshot": left,
            "market_snapshot": right,
            "latest_match": self.latest_match_for_opportunity(opportunity),
            "decision_count": len(decisions),
        }
