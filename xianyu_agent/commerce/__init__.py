"""Commerce domain package (WP01+).

Contracts and repositories for supplier offers, product matching,
opportunities, agent jobs, decision records and the later mandate /
budget / procurement / ledger shapes. Import from the submodules
directly; this package intentionally re-exports only the names business
services use most.
"""
from .contracts import (  # noqa: F401
    MATCH_COMPARABLE,
    MATCH_EXACT,
    MATCH_NOT_MATCH,
    MATCH_UNKNOWN,
    OPPORTUNITY_DISCOVERED,
    OPPORTUNITY_REJECTED,
    OPPORTUNITY_RESEARCHING,
    PRICE_STATE_KNOWN,
    PRICE_STATE_NOT_APPLICABLE,
    PRICE_STATE_UNKNOWN,
    ContractError,
    CurrencyMismatch,
    Money,
    build_agent_job,
    build_authorization_record,
    build_budget_reservation,
    build_decision_record,
    build_ledger_entry,
    build_offer_snapshot,
    build_operating_mandate,
    build_opportunity,
    build_procurement_order,
    build_product_match,
    business_day,
    money_from_mapping,
    utc_now,
)
from .repositories import (  # noqa: F401
    DecisionRecordRepository,
    DuplicateRecord,
    JsonCommerceRepositories,
    OfferSnapshotRepository,
    OpportunityRepository,
    ProductMatchRepository,
    RevisionConflict,
)
