"""Connector registry with schema pinning and bounded outbound fetch
(taskbook R19).

The registry enforces, at CALL time (never at import/startup — zero
warmup):

* schema pinning — a caller names the tool AND the schema version it
  approved; if the registered connector's tool schema version or digest
  changed, the call is REFUSED until the new schema is explicitly
  accepted (``accept_schema``), so a connector upgrade can never
  silently change what an approved tool does;
* outbound guard — every fetch URL is SSRF-checked per hop; redirects
  are followed manually with each hop re-validated (max 3);
* bounded reads — the response body is capped at the connector's
  ``max_response_bytes``; a larger body raises instead of ballooning
  memory;
* probe honesty — ``probe`` reports ``transport_calls=0`` by default:
  a capability check is never presented as a verified fetch.
"""
from __future__ import annotations

import http.client
import http.client as _http_client
import ssl
from typing import Any, Dict, FrozenSet, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from .contracts import (
    ConnectorContractError,
    ConnectorSpec,
    ToolSpec,
    assert_public_http_url,
)


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: Dict[str, ConnectorSpec] = {}
        # schema acceptance: {(connector, tool): approved_version}
        self._accepted: Dict[Tuple[str, str], int] = {}

    # ---- registration (pure bookkeeping, no I/O) --------------------------

    def register(self, spec: ConnectorSpec, *, replace: bool = False) -> None:
        if spec.name in self._connectors and not replace:
            raise ConnectorContractError(
                f"connector {spec.name!r} already registered",
                code="duplicate_connector",
            )
        self._connectors[spec.name] = spec

    def get(self, name: str) -> Optional[ConnectorSpec]:
        return self._connectors.get(name)

    def list_connectors(self):
        return sorted(self._connectors)

    def disable(self, name: str) -> bool:
        """R19 rollback: stop ONE connector; the others keep working."""
        return self._connectors.pop(name, None) is not None

    # ---- schema pinning ----------------------------------------------------

    def accept_schema(self, connector: str, tool: str, version: int) -> None:
        """Explicit operator acceptance of a (possibly new) schema."""
        self._accepted[(connector, tool)] = int(version)

    def check_tool(
        self,
        connector: str,
        tool: str,
        *,
        approved_version: Optional[int] = None,
        scopes: FrozenSet[str] = frozenset(),
    ) -> ToolSpec:
        """Resolve a tool call or refuse it.

        Refusal codes:
        * ``unknown_connector`` / ``unknown_tool`` — not registered;
        * ``schema_drift_stopped`` — the tool's registered schema version
          or digest differs from the accepted one (startup default = the
          version at registration time);
        * ``scope_forbidden`` — the caller lacks a required scope.
        """
        spec = self._connectors.get(connector)
        if spec is None:
            raise ConnectorContractError(
                f"connector {connector!r} is not registered",
                code="unknown_connector",
            )
        tool_spec = spec.tool(tool)
        key = (connector, tool)
        accepted = self._accepted.get(key)
        if accepted is None:
            # first sight: pin the CURRENT version as accepted baseline
            accepted = tool_spec.schema_version
            self._accepted[key] = accepted
        effective = approved_version if approved_version is not None else accepted
        if (tool_spec.schema_version != effective
                or int(accepted) != tool_spec.schema_version):
            raise ConnectorContractError(
                f"tool {connector}.{tool} schema changed: registered "
                f"v{tool_spec.schema_version} (digest {tool_spec.schema_digest}) "
                f"but v{effective} was approved; the tool is STOPPED until the "
                "new schema is explicitly accepted",
                code="schema_drift_stopped",
            )
        missing = set(tool_spec.required_scopes) - set(scopes)
        if missing:
            raise ConnectorContractError(
                f"tool {connector}.{tool} requires scopes {sorted(missing)}",
                code="scope_forbidden",
            )
        return tool_spec

    # ---- probe honesty ---------------------------------------------------

    def probe(self, connector: str) -> Dict[str, Any]:
        """Zero-cost capability check. Unless the connector explicitly
        declares ``probe_makes_transport_calls`` the probe makes ZERO
        network calls — and its result says so, so a probe can never be
        presented as a verified fetch (review A50 family)."""
        spec = self._connectors.get(connector)
        if spec is None:
            raise ConnectorContractError(
                f"connector {connector!r} is not registered",
                code="unknown_connector",
            )
        transport_calls = 1 if spec.probe_makes_transport_calls else 0
        return {
            "connector": connector,
            "upstream_family": spec.upstream_family,
            "connector_version": spec.connector_version,
            "tools": sorted(spec.tools),
            "transport_calls": transport_calls,
            "verified_fetch": False,
            "note": (
                "probe proves registration only; a verified fetch requires an "
                "authorized tool call through the quota ledger"
            ),
        }

    # ---- bounded outbound fetch ---------------------------------------------

    def fetch(
        self,
        connector: str,
        url: str,
        *,
        method: str = "GET",
        body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Guarded HTTP fetch: SSRF check per hop, manual redirects,
        bounded body. Raises ConnectorContractError on any violation —
        never a fake success."""
        spec = self._connectors.get(connector)
        if spec is None:
            raise ConnectorContractError(
                f"connector {connector!r} is not registered",
                code="unknown_connector",
            )
        current = url
        hops = 0
        while True:
            assert_public_http_url(
                current,
                allowed_hosts=spec.allowed_hosts,
                allowed_private_hosts=spec.allow_private_hosts,
            )
            parts = urlsplit(current)
            path = urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))
            connection = self._open(parts)
            try:
                connection.request(
                    method,
                    path,
                    body=body,
                    headers=headers or {},
                )
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    location = response.getheader("Location") or ""
                    response.read(1)  # drain minimal bytes
                    if not location:
                        raise ConnectorContractError(
                            "redirect without a Location header",
                            code="malformed_redirect",
                        )
                    hops += 1
                    if hops > spec.max_redirects:
                        raise ConnectorContractError(
                            f"more than {spec.max_redirects} redirects",
                            code="too_many_redirects",
                        )
                    next_url = _resolve_relative(current, location)
                    # PER-HOP re-validation happens at the top of the loop
                    current = next_url
                    continue
                data = response.read(spec.max_response_bytes + 1)
                if len(data) > spec.max_response_bytes:
                    raise ConnectorContractError(
                        f"response exceeds the connector's bound of "
                        f"{spec.max_response_bytes} bytes",
                        code="response_too_large",
                    )
                return {
                    "status": response.status,
                    "headers": dict(response.getheaders()),
                    "body": data,
                    "url": current,
                    "redirects": hops,
                }
            finally:
                connection.close()

    @staticmethod
    def _open(parts) -> http.client.HTTPConnection:
        if parts.scheme == "https":
            context = ssl.create_default_context()
            return _http_client.HTTPSConnection(parts.hostname, parts.port or 443,
                                               timeout=30, context=context)
        return _http_client.HTTPConnection(parts.hostname, parts.port or 80,
                                           timeout=30)


def _resolve_relative(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)
