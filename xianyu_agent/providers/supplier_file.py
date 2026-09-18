"""Supplier-file provider adapter (WP04).

The first and only currently usable procurement source: offer
snapshots imported from operator-supplied quote files (WP03). This
adapter queries the local snapshot repository -- it performs no
network I/O -- and applies the freshness and revocation rules:

- Snapshots from revoked/failed import batches are excluded by
  default (they remain readable history in the repository).
- Expired snapshots are classified and excluded from the ``current``
  result set unless ``include_expired`` is requested; the exclusion is
  reported, never silent.
- Asking for specific SKUs reports ``missing_skus`` instead of
  pretending the data is complete.
- Every result carries per-record freshness so panels can separate
  current / expired / unknown data (WP04 acceptance).
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .base import ProviderAdapter
from .fetcher import FetchOutcome, FETCH_DEGRADED, FETCH_ERROR, FETCH_OK
from .freshness import FRESH_CURRENT, classify_snapshot


class SupplierFileProvider(ProviderAdapter):
    provider_id = "supplier_file"

    def __init__(self, repos, import_service=None) -> None:
        self._repos = repos
        self._import_service = import_service

    def capability_summary(self) -> Mapping[str, Any]:
        return {
            "provider_id": self.provider_id,
            "kind": "file",
            "network": False,
            "search_supported": False,
            "exact_sku_supported": True,
            "status": "CONFIGURED",
            "note": "queries imported WP03 snapshots; no online access",
        }

    def committed_snapshot_ids(self) -> set:
        """Snapshot ids belonging to committed (non-revoked) batches."""
        if self._import_service is None:
            return set()
        active: set = set()
        for batch in self._import_service.list_batches():
            if str(batch.get("status")) == "committed":
                active.update(batch.get("snapshot_ids") or [])
        return active

    def fetch_offers(
        self,
        *,
        keywords: Sequence[str] = (),
        sku: str = "",
        limit: int = 100,
        timeout_seconds: float = 10.0,
        include_expired: bool = False,
        requested_skus: Sequence[str] = (),
    ) -> Dict[str, Any]:
        records = self._repos.offers.list()
        records = [r for r in records if str(r.get("provider_id", "")).startswith("supplier_file")]
        if self._import_service is not None:
            committed = self.committed_snapshot_ids()
            records = [r for r in records if r.get("record_id") in committed]

        if sku:
            records = [r for r in records if str(r.get("source_item_id")) == sku]
        for keyword in keywords:
            records = [
                r
                for r in records
                if keyword.lower() in str((r.get("attributes") or {}).get("name") or "").lower()
                or keyword.lower() in str(r.get("source_item_id")).lower()
            ]

        # collapse to the latest snapshot per (provider, item, sku)
        latest: Dict[tuple, Dict[str, Any]] = {}
        for record in records:
            key = (record.get("provider_id"), record.get("source_item_id"), record.get("source_sku_id"))
            incumbent = latest.get(key)
            if incumbent is None or str(record.get("observed_at") or "") > str(incumbent.get("observed_at") or ""):
                latest[key] = record
        records = list(latest.values())

        classified: List[Dict[str, Any]] = []
        expired_excluded = 0
        for record in records:
            meta = classify_snapshot(record)
            if meta["freshness"] != FRESH_CURRENT and not include_expired:
                expired_excluded += 1
                continue
            entry = dict(record)
            entry["freshness_meta"] = meta
            classified.append(entry)

        missing_skus = [s for s in requested_skus if s and not any(r.get("source_item_id") == s for r in classified)]

        if classified:
            status = FETCH_OK
        elif missing_skus:
            status = FETCH_ERROR
        else:
            status = FETCH_DEGRADED
        outcome = FetchOutcome(provider_id=self.provider_id, status=status, records=classified[: max(0, limit)])
        result = outcome.to_dict()
        result["expired_excluded"] = expired_excluded
        result["missing_skus"] = missing_skus
        result["include_expired"] = include_expired
        return result
