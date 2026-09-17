"""Provider capability registry (WP02).

One record per data source answering the minimal capability sheet from
docs/research/INTEGRATION_EVIDENCE_2026-09-18.md (section 5). The
registry is the single place that answers "which sources are actually
usable and under what terms".

Enforced honesty rule: a record can only be saved as ``VERIFIED`` when
it carries at least 20 real-sample observations with raw evidence
references and a checked_at date. Anything less stays UNVERIFIED or
CONFIGURED -- an implemented adapter, a fixture test or a 2021 PDF is
not current verified access.

The default seed set records the 2026-09-18 investigation outcome:
- supplier_file: CONFIGURED (channel implemented + tested; first real
  supplier file still pending from the operator)
- xianyu_observation (manual): CONFIGURED (manual observation channel
  works; online observation API not connected)
- pinduoduo_duoduojinbao: UNVERIFIED (2020/2021 official PDFs prove the
  mechanism existed; current account permission not applied for)
- alibaba_1688_open: UNVERIFIED (portal could not be read; no contract)
- xianyu_browser_observation: UNVERIFIED (code exists from earlier
  phases; no current login/run evidence in this baseline)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..commerce.repositories import LockedJsonList
from .base import (
    PROVIDER_STATUS_CONFIGURED,
    PROVIDER_STATUS_UNVERIFIED,
    PROVIDER_STATUS_VERIFIED,
    PROVIDER_STATUSES,
    ProviderError,
)

CAPABILITY_SCHEMA_VERSION = 1
VERIFIED_MIN_REAL_SAMPLES = 20
CAPABILITY_MAX_AGE_DAYS = 90

APPLICATION_STATUSES = ("none", "pending", "partial", "blocked", "rejected", "granted")

CAPABILITY_FIELDS = (
    "provider_id",
    "owner",
    "docs_url",
    "checked_at",
    "terms_version",
    "application_status",
    "account_scope",
    "required_permissions",
    "cost_basis",
    "quota",
    "retry_policy",
    "allowed_storage",
    "search_supported",
    "exact_sku_supported",
    "stock_supported",
    "freight_supported",
    "coupon_eligibility",
    "price_valid_until",
    "real_sample_count",
    "raw_evidence_refs",
    "observed_limitations",
    "status",
    "next_action",
    "accountable_owner",
)


def build_capability_record(*, provider_id: str, checked_at: datetime, **fields: Any) -> Dict[str, Any]:
    """Build and validate one capability record.

    ``unknown`` is an explicit, allowed value for every capability
    field except provider_id/checked_at. VERIFIED is refused without
    the real-sample evidence rule.
    """
    if not str(provider_id).strip():
        raise ProviderError("capability record requires provider_id")
    if not hasattr(checked_at, "astimezone") or checked_at.tzinfo is None:
        raise ProviderError("checked_at must be a timezone-aware datetime")
    checked_at = checked_at.astimezone(timezone.utc)
    status = str(fields.get("status") or PROVIDER_STATUS_UNVERIFIED)
    if status not in PROVIDER_STATUSES:
        raise ProviderError(f"invalid provider status {status!r}")
    application_status = str(fields.get("application_status") or "none")
    if application_status not in APPLICATION_STATUSES:
        raise ProviderError(f"invalid application_status {application_status!r}")

    record: Dict[str, Any] = {
        "record_type": "provider_capability",
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "provider_id": str(provider_id),
        "checked_at": checked_at.isoformat(),
    }
    for field_name in CAPABILITY_FIELDS:
        if field_name in ("provider_id", "checked_at"):
            continue
        record[field_name] = fields.get(field_name, "unknown")

    real_samples = record.get("real_sample_count")
    evidence_refs = record.get("raw_evidence_refs") or []
    if status == PROVIDER_STATUS_VERIFIED:
        try:
            sample_count = int(real_samples)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ProviderError("VERIFIED requires real_sample_count as an integer >= 20")
        if sample_count < VERIFIED_MIN_REAL_SAMPLES:
            raise ProviderError(
                f"VERIFIED requires >= {VERIFIED_MIN_REAL_SAMPLES} real samples; got {sample_count}. "
                "Keep the source UNVERIFIED/CONFIGURED until real requests exist."
            )
        if not evidence_refs or not isinstance(evidence_refs, (list, tuple)):
            raise ProviderError("VERIFIED requires raw_evidence_refs pointing at real observations")
        if isinstance(record.get("cost_basis"), str) and record["cost_basis"] == "unknown":
            raise ProviderError("VERIFIED requires a cost_basis with its evidence date")
    return record


def is_capability_check_stale(record: Mapping[str, Any], *, now: Optional[datetime] = None) -> bool:
    """A VERIFIED capability older than the window must be re-checked."""
    if str(record.get("status")) != PROVIDER_STATUS_VERIFIED:
        return False
    moment = now or datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(str(record.get("checked_at")))
    except (TypeError, ValueError):
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (moment - checked) > timedelta(days=CAPABILITY_MAX_AGE_DAYS)


def default_capability_seed(*, checked_at: datetime) -> List[Dict[str, Any]]:
    """Seed records reflecting the 2026-09-18 investigation (see module docstring)."""
    return [
        build_capability_record(
            provider_id="supplier_file",
            checked_at=checked_at,
            owner="operator-supplied supplier quotes",
            docs_url="local: docs/ops (import runbook)",
            application_status="granted",
            account_scope="per supplier file batch",
            cost_basis="free (operator-owned documents)",
            search_supported=False,
            exact_sku_supported=True,
            stock_supported="per-file column",
            freight_supported="per-file column",
            coupon_eligibility=False,
            price_valid_until="per-file, default 24h reuse of QUOTE_MAX_AGE_SECONDS",
            real_sample_count=0,
            raw_evidence_refs=["docs/acceptance/BASELINE_2026-09-18.md", "tests/test_supplier_import.py"],
            observed_limitations=[
                "channel implemented and unit-tested against synthetic fixtures",
                "first real supplier quote file still pending from operator",
            ],
            status=PROVIDER_STATUS_CONFIGURED,
            next_action="operator imports first real supplier quote file",
            accountable_owner="operator",
        ),
        build_capability_record(
            provider_id="xianyu_observation",
            checked_at=checked_at,
            owner="operator manual observation",
            docs_url="local: /commerce/market-observations",
            application_status="granted",
            account_scope="single operator account scope",
            cost_basis="free (manual work)",
            search_supported=False,
            exact_sku_supported="manual entry",
            stock_supported=False,
            freight_supported=False,
            coupon_eligibility=False,
            price_valid_until="72h market-baseline freshness",
            real_sample_count=0,
            raw_evidence_refs=["tests/test_commerce_api.py"],
            observed_limitations=[
                "manual observation channel only; not a platform API",
                "display prices are not transaction facts",
            ],
            status=PROVIDER_STATUS_CONFIGURED,
            next_action="operator records real market observations per candidate SKU",
            accountable_owner="operator",
        ),
        build_capability_record(
            provider_id="pinduoduo_duoduojinbao",
            checked_at=checked_at,
            owner="PDD open platform (Duoduo Jinbao)",
            docs_url="https://open.pinduoduo.com/ (2021 PDF: pdd.ddk.goods.search)",
            application_status="none",
            account_scope="none",
            cost_basis="historical PDF claims free API; 2026 terms not read",
            search_supported="unknown",
            exact_sku_supported="unknown",
            stock_supported="unknown",
            freight_supported="unknown",
            coupon_eligibility="unknown",
            price_valid_until="unknown",
            real_sample_count=0,
            raw_evidence_refs=[
                "docs/research/INTEGRATION_EVIDENCE_2026-09-18.md (2021/2020 official PDFs)",
            ],
            observed_limitations=[
                "alliance promotion price is not automatically a procurement price",
                "application/quota/fee terms must be re-read before any adapter",
            ],
            status=PROVIDER_STATUS_UNVERIFIED,
            next_action="operator applies for a developer account; then re-read current terms",
            accountable_owner="operator",
        ),
        build_capability_record(
            provider_id="alibaba_1688_open",
            checked_at=checked_at,
            owner="1688 open platform",
            docs_url="https://open.1688.com/ (unreadable in the 2026-09-18 check)",
            application_status="none",
            account_scope="none",
            cost_basis="unknown",
            search_supported="unknown",
            exact_sku_supported="unknown",
            stock_supported="unknown",
            freight_supported="unknown",
            coupon_eligibility="unknown",
            price_valid_until="unknown",
            real_sample_count=0,
            raw_evidence_refs=["docs/research/INTEGRATION_EVIDENCE_2026-09-18.md"],
            observed_limitations=["portal could not be read; no API contract on file"],
            status=PROVIDER_STATUS_UNVERIFIED,
            next_action="operator with an account reads current API/fee contract",
            accountable_owner="operator",
        ),
        build_capability_record(
            provider_id="xianyu_browser_observation",
            checked_at=checked_at,
            owner="local browser capture (product_discovery)",
            docs_url="local: xianyu_agent/product_discovery.py",
            application_status="none",
            account_scope="requires logged-in seller browser profile",
            cost_basis="free (own browsing)",
            search_supported="partial (browser search capture)",
            exact_sku_supported=False,
            stock_supported=False,
            freight_supported=False,
            coupon_eligibility=False,
            price_valid_until="72h market-baseline freshness",
            real_sample_count=0,
            raw_evidence_refs=["xianyu_agent/product_discovery.py"],
            observed_limitations=[
                "implementation exists from earlier phases",
                "no current login or capture run in the 2026-09-18 baseline",
            ],
            status=PROVIDER_STATUS_UNVERIFIED,
            next_action="re-run a real capture after account login and record samples",
            accountable_owner="operator",
        ),
    ]


class ProviderCapabilityRegistry:
    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(data_dir / "commerce" / "provider_capabilities.json")

    def seed_defaults(self, *, checked_at: Optional[datetime] = None, force: bool = False) -> List[Dict[str, Any]]:
        moment = checked_at or datetime.now(timezone.utc)
        existing = {str(r.get("provider_id")) for r in self._store.all()}
        created: List[Dict[str, Any]] = []
        for record in default_capability_seed(checked_at=moment):
            if record["provider_id"] in existing and not force:
                continue
            self._store.append(dict(record))
            created.append(record)
        return created

    def upsert(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        provider_id = str(record.get("provider_id") or "")
        if not provider_id:
            raise ProviderError("capability record requires provider_id")

        def mutate(items: LockedJsonList) -> None:
            for index, existing in enumerate(items._items):
                if str(existing.get("provider_id")) == provider_id:
                    items._items[index] = dict(record)
                    return
            items._items.append(dict(record))

        with self._store as items:
            mutate(items)
        return dict(record)

    def get(self, provider_id: str) -> Optional[Dict[str, Any]]:
        found = self._store.find(lambda r: str(r.get("provider_id")) == str(provider_id))
        return found[0] if found else None

    def list(self) -> List[Dict[str, Any]]:
        return self._store.all()

    def usable_sources(self, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """VERIFIED (non-stale) + CONFIGURED sources usable for building."""
        usable: List[Dict[str, Any]] = []
        for record in self.list():
            status = str(record.get("status"))
            if status in (PROVIDER_STATUS_VERIFIED, PROVIDER_STATUS_CONFIGURED):
                if status == PROVIDER_STATUS_VERIFIED and is_capability_check_stale(record, now=now):
                    continue
                usable.append(record)
        return usable
