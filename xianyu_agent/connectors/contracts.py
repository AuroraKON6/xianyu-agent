"""Connector contracts (taskbook R19).

A connector is the ONLY sanctioned way external systems are reached.
Every connector declares, before it is registered:

* capabilities — named tools with a pinned schema version; a schema
  change stops every tool that approved the old version until the new
  one is explicitly accepted;
* permissions — which principal scopes may call each tool;
* quota — the per-source budget key (enforced by the R08 ledger);
* timeout and bounded reads — no response may exceed ``max_response_bytes``;
* outbound policy — an allowlist of hosts, a redirect policy with
  PER-HOP re-validation, and schemes limited to http/https;
* upstream_family — which external family (xianyu / 1688 / pdd / ...)
  this connector speaks, so sources and quotas can be grouped;
* probe — a zero-cost capability check that makes NO transport calls
  unless real fetching is explicitly authorized (a probe result of
  ``zero_calls`` is never "verified fetch").
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Mapping, Optional
from urllib.parse import urlsplit


class ConnectorContractError(ValueError):
    """Raised for contract violations (bad URL, schema drift, ...)."""

    def __init__(self, message: str, *, code: str = "connector_contract") -> None:
        super().__init__(message)
        self.code = code


ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 3
DEFAULT_MAX_RESPONSE_BYTES = 1 << 20  # 1 MiB


# ---------------------------------------------------------------- outbound


def _host_candidates(hostname: str):
    yield hostname
    try:
        for info in socket.getaddrinfo(hostname, None):
            yield info[4][0]
    except Exception:
        # DNS may be unavailable (offline isolation guard blocks it too);
        # literal-IP checks below still run and the connect-time boundary
        # remains the final enforcement point
        pass


def assert_public_http_url(url: str, *, allowed_hosts: FrozenSet[str] = frozenset(),
                           allowed_private_hosts: FrozenSet[str] = frozenset()) -> None:
    """SSRF guard: only http(s), no private/loopback/link-local targets,
    and (when the connector declares one) only allowlisted hosts."""
    parts = urlsplit(str(url or ""))
    if parts.scheme not in ALLOWED_SCHEMES:
        raise ConnectorContractError(
            f"scheme {parts.scheme!r} is not allowed (http/https only)",
            code="scheme_forbidden",
        )
    hostname = (parts.hostname or "").strip().lower()
    if not hostname:
        raise ConnectorContractError("URL has no hostname", code="malformed_url")
    if allowed_hosts and hostname not in {h.lower() for h in allowed_hosts}:
        raise ConnectorContractError(
            f"host {hostname!r} is not in the connector's outbound allowlist",
            code="host_forbidden",
        )
    for candidate in _host_candidates(hostname):
        if candidate.lower() in allowed_private_hosts:
            continue
        try:
            address = ipaddress.ip_address(candidate.split("%")[0])
        except ValueError:
            continue
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            raise ConnectorContractError(
                f"target {candidate} resolves to a non-public address; "
                "internal networks are unreachable from connectors",
                code="private_target_forbidden",
            )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema_version: int
    schema_digest: str
    required_scopes: FrozenSet[str] = field(default_factory=frozenset)
    quota_key: str = ""
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    connector_version: int
    upstream_family: str
    allowed_hosts: FrozenSet[str] = field(default_factory=frozenset)
    allow_private_hosts: FrozenSet[str] = field(default_factory=frozenset)
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_redirects: int = MAX_REDIRECTS
    tools: Mapping[str, ToolSpec] = field(default_factory=dict)
    probe_makes_transport_calls: bool = False  # probes are free by default

    def tool(self, name: str) -> ToolSpec:
        try:
            return self.tools[name]
        except KeyError:
            raise ConnectorContractError(
                f"tool {name!r} is not part of connector {self.name!r}",
                code="unknown_tool",
            ) from None
