"""Source registry (taskbook R19): which sources exist, which connector
serves them, and whether they are enabled.

A *source* is a business data origin (e.g. ``market:xianyu_channel``);
a *connector* is the sanctioned transport for it. Disabling a connector
stops its sources; other sources continue untouched (per-connector
rollback), and an unregistered connector is never silently substituted.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .connectors.contracts import ConnectorContractError


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        *,
        source: str,
        connector: str,
        quota_bucket: str = "",
        enabled: bool = False,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Register a source. Sources start DISABLED; enabling is an
        explicit operator decision (no source is live by default)."""
        if not source.strip() or not connector.strip():
            raise ConnectorContractError("source and connector are required")
        record = {
            "source": source,
            "connector": connector,
            "quota_bucket": quota_bucket or source,
            "enabled": bool(enabled),
            "notes": notes,
        }
        self._sources[source] = record
        return dict(record)

    def get(self, source: str) -> Optional[Dict[str, Any]]:
        record = self._sources.get(source)
        return dict(record) if record else None

    def set_enabled(self, source: str, enabled: bool) -> Dict[str, Any]:
        record = self._sources.get(source)
        if record is None:
            raise ConnectorContractError(f"source not registered: {source}")
        record["enabled"] = bool(enabled)
        return dict(record)

    def list_sources(self, *, connector: str = "", enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        records = []
        for record in self._sources.values():
            if connector and record["connector"] != connector:
                continue
            if enabled is not None and record["enabled"] != enabled:
                continue
            records.append(dict(record))
        return sorted(records, key=lambda r: r["source"])

    def connector_for(self, source: str, *, connector_registry) -> Dict[str, Any]:
        """Resolve a source to its live connector or refuse the call.

        Refusal codes: ``unknown_source`` / ``source_disabled`` /
        ``connector_unavailable`` (connector disabled or replaced — an
        unregistered connector is NEVER auto-substituted)."""
        record = self._sources.get(source)
        if record is None:
            raise ConnectorContractError(
                f"source not registered: {source}", code="unknown_source"
            )
        if not record["enabled"]:
            raise ConnectorContractError(
                f"source {source!r} is disabled", code="source_disabled"
            )
        spec = connector_registry.get(record["connector"])
        if spec is None:
            raise ConnectorContractError(
                f"source {source!r} maps to connector {record['connector']!r} "
                "which is not registered; refusing to substitute an "
                "unvetted connector",
                code="connector_unavailable",
            )
        return {
            "source": source,
            "connector": record["connector"],
            "connector_version": spec.connector_version,
            "upstream_family": spec.upstream_family,
            "quota_bucket": record["quota_bucket"],
        }
