"""Provider adapter base contract (WP02/WP04).

Every external data source is an adapter behind this contract. The
business pipeline consumes normalized provider ids and OfferSnapshot
records; platform naming differences (``pdd`` vs ``pinduoduo`` etc.)
are normalized at this boundary and nowhere else (WP04 rule).

Statuses follow master plan 4.3:
``UNVERIFIED / CONFIGURED / VERIFIED / DEGRADED / DISABLED``.
``VERIFIED`` is only assigned through ProviderCapabilityRegistry with
real-sample evidence; no adapter may self-declare it.

This module currently defines the contract and normalization only --
no online adapter ships in this round. Sources without credentials
stay UNVERIFIED; their adapter protocol can still be tested against
fixtures (WP02 acceptance).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Mapping, Optional, Sequence

PROVIDER_STATUS_UNVERIFIED = "UNVERIFIED"
PROVIDER_STATUS_CONFIGURED = "CONFIGURED"
PROVIDER_STATUS_VERIFIED = "VERIFIED"
PROVIDER_STATUS_DEGRADED = "DEGRADED"
PROVIDER_STATUS_DISABLED = "DISABLED"
PROVIDER_STATUSES = (
    PROVIDER_STATUS_UNVERIFIED,
    PROVIDER_STATUS_CONFIGURED,
    PROVIDER_STATUS_VERIFIED,
    PROVIDER_STATUS_DEGRADED,
    PROVIDER_STATUS_DISABLED,
)

# Canonical provider ids used across the commerce domain. Historical
# spellings map here at the boundary; downstream code sees only the
# canonical id.
PROVIDER_ALIASES: Dict[str, str] = {
    "pdd": "pinduoduo_duoduojinbao",
    "pinduoduo": "pinduoduo_duoduojinbao",
    "duoduojinbao": "pinduoduo_duoduojinbao",
    "pdd.ddk": "pinduoduo_duoduojinbao",
    "1688": "alibaba_1688_open",
    "alibaba": "alibaba_1688_open",
    "taobao": "taobao_alliance",
    "douyin": "douyin_jingxuan",
    "xianyu": "xianyu_observation",
    "goofish": "xianyu_observation",
    "闲鱼": "xianyu_observation",
    "supplier file": "supplier_file",
    "supplier_file": "supplier_file",
    "supplier csv": "supplier_file",
}


class ProviderError(RuntimeError):
    pass


def normalize_provider_id(raw: str) -> str:
    """Normalize any historical provider spelling to its canonical id."""
    text = re.sub(r"\s+", "_", str(raw or "").strip().lower())
    if not text:
        raise ProviderError("provider id must not be empty")
    if text.startswith("supplier_file:"):
        return text  # file batches keep their supplier-specific id
    if text.startswith("market:"):
        return text
    return PROVIDER_ALIASES.get(text, text)


class ProviderAdapter(ABC):
    """Contract for future online adapters (WP04).

    Adapters fetch and normalize; they never judge opportunities, never
    write business facts directly and never mark themselves VERIFIED.
    Read-only retries are allowed; unknown results surface as errors,
    not as empty data.
    """

    provider_id: str = ""

    @abstractmethod
    def capability_summary(self) -> Mapping[str, Any]:
        """Return the adapter's current capability fields for the registry."""

    @abstractmethod
    def fetch_offers(
        self,
        *,
        keywords: Sequence[str] = (),
        sku: str = "",
        limit: int = 20,
        timeout_seconds: float = 10.0,
    ) -> List[Dict[str, Any]]:
        """Fetch offers and return raw normalized records (not snapshots)."""
