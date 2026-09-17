"""External data source adapters and capability registry (WP02)."""
from .base import (  # noqa: F401
    PROVIDER_STATUSES,
    PROVIDER_STATUS_CONFIGURED,
    PROVIDER_STATUS_DISABLED,
    PROVIDER_STATUS_DEGRADED,
    PROVIDER_STATUS_UNVERIFIED,
    PROVIDER_STATUS_VERIFIED,
    ProviderAdapter,
    ProviderError,
    normalize_provider_id,
)
from .capabilities import (  # noqa: F401
    ProviderCapabilityRegistry,
    build_capability_record,
    default_capability_seed,
    is_capability_check_stale,
)
