"""R19 acceptance tests: connector contracts, registry, source registry.

Taskbook matrix:
* schema change STOPS affected tools until explicitly accepted;
* malicious URLs (private/loopback targets, non-http schemes) refused;
* redirects re-validated PER HOP (a redirect to an internal address is
  blocked even when the first hop was public);
* oversized responses raise instead of ballooning;
* fake success impossible: probes report transport_calls=0 and
  verified_fetch=false; guarded fetch raises on every violation;
* zero startup warmup: constructing, registering and checking tools
  make no network calls;
* per-connector rollback: disabling one connector stops only its
  sources; an unregistered connector is never auto-substituted.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from xianyu_agent.connectors.contracts import (
    ConnectorContractError,
    ConnectorSpec,
    ToolSpec,
    assert_public_http_url,
)
from xianyu_agent.connectors.registry import ConnectorRegistry
from xianyu_agent.source_registry import SourceRegistry


def spec_v1(**overrides) -> ConnectorSpec:
    tools = {
        "fetch_page": ToolSpec(
            name="fetch_page", schema_version=1, schema_digest="tool:fetch:v1",
            required_scopes=frozenset({"commerce:read"}),
            quota_key="market:xianyu_channel", timeout_seconds=10.0,
        )
    }
    tools.update(overrides.pop("tools_extra", {}))
    params = dict(
        name="xianyu-market-read",
        connector_version=1,
        upstream_family="xianyu",
        allowed_hosts=frozenset({"h5api.m.goofish.com"}),
        max_response_bytes=1024,
        tools=tools,
    )
    params.update(overrides)
    return ConnectorSpec(**params)


class SchemaPinningTests(unittest.TestCase):
    def test_schema_drift_stops_the_tool(self):
        registry = ConnectorRegistry()
        registry.register(spec_v1())
        # first call pins v1 as the accepted baseline
        registry.check_tool("xianyu-market-read", "fetch_page",
                            scopes=frozenset({"commerce:read"}))
        # connector upgrade: same tool, schema v2 + new digest
        upgraded = spec_v1(
            connector_version=2,
            tools_extra={"fetch_page": ToolSpec(
                name="fetch_page", schema_version=2, schema_digest="tool:fetch:v2",
                required_scopes=frozenset({"commerce:read"}),
                quota_key="market:xianyu_channel",
            )},
        )
        registry.register(upgraded, replace=True)
        with self.assertRaises(ConnectorContractError) as ctx:
            registry.check_tool("xianyu-market-read", "fetch_page",
                                scopes=frozenset({"commerce:read"}))
        self.assertEqual(ctx.exception.code, "schema_drift_stopped")
        # other connectors are unaffected
        other = spec_v1(name="other-connector", upstream_family="1688")
        registry.register(other)
        registry.check_tool("other-connector", "fetch_page",
                            scopes=frozenset({"commerce:read"}))
        # explicit acceptance un-stops the tool
        registry.accept_schema("xianyu-market-read", "fetch_page", 2)
        registry.check_tool("xianyu-market-read", "fetch_page",
                            scopes=frozenset({"commerce:read"}))

    def test_scope_required(self):
        registry = ConnectorRegistry()
        registry.register(spec_v1())
        with self.assertRaises(ConnectorContractError) as ctx:
            registry.check_tool("xianyu-market-read", "fetch_page",
                                scopes=frozenset({"research:read"}))
        self.assertEqual(ctx.exception.code, "scope_forbidden")


class OutboundGuardTests(unittest.TestCase):
    def test_private_targets_refused(self):
        for url in (
            "http://127.0.0.1/api",
            "http://localhost/api",
            "http://10.0.0.5/api",
            "http://192.168.1.10/api",
            "http://169.254.169.254/latest/meta-data",  # cloud metadata
            "http://[::1]/api",
            "file:///etc/passwd",
            "ftp://example.com/file",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ConnectorContractError):
                    assert_public_http_url(url)

    def test_host_allowlist_enforced(self):
        with self.assertRaises(ConnectorContractError) as ctx:
            assert_public_http_url(
                "https://evil.example.com/api",
                allowed_hosts=frozenset({"h5api.m.goofish.com"}),
            )
        self.assertEqual(ctx.exception.code, "host_forbidden")
        # allowlisted host passes the host check (public DNS assumed)
        assert_public_http_url(
            "https://h5api.m.goofish.com/api",
            allowed_hosts=frozenset({"h5api.m.goofish.com"}),
        )


class GuardedFetchTests(unittest.TestCase):
    def setUp(self):
        self.registry = ConnectorRegistry()
        self.registry.register(spec_v1())

    def _fake_response(self, connection_mock, status=200, body=b"x" * 10,
                       location=None):
        response = connection_mock.getresponse.return_value
        response.status = status
        response.getheader.return_value = location
        response.read.side_effect = lambda n=-1: body[:n] if n > 0 else body
        return response

    def test_oversized_response_raises(self):
        import xianyu_agent.connectors.registry as reg

        with patch.object(reg.http.client, "HTTPSConnection") as conn_cls:
            self._fake_response(conn_cls.return_value, body=b"x" * 4096)
            with self.assertRaises(ConnectorContractError) as ctx:
                self.registry.fetch("xianyu-market-read",
                                    "https://h5api.m.goofish.com/api")
            self.assertEqual(ctx.exception.code, "response_too_large")

    def test_redirect_to_internal_blocked_per_hop(self):
        import xianyu_agent.connectors.registry as reg

        # no host allowlist on this connector: the SSRF private-target
        # check is what must fire on the redirect hop
        self.registry.register(spec_v1(allowed_hosts=frozenset()), replace=True)
        with patch.object(reg.http.client, "HTTPSConnection") as conn_cls:
            connection = conn_cls.return_value
            # first hop: public host redirecting to loopback
            self._fake_response(connection, status=302, location="http://127.0.0.1/steal",
                                body=b"")
            with patch.object(reg.ConnectorRegistry, "_open", return_value=connection):
                with self.assertRaises(ConnectorContractError) as ctx:
                    self.registry.fetch("xianyu-market-read",
                                        "https://example.com/api")
                self.assertEqual(ctx.exception.code, "private_target_forbidden")
            # per-hop validation ran BEFORE the second request was sent
            self.assertEqual(connection.request.call_count, 1)

    def test_redirect_chain_within_limit_and_allowlist(self):
        import xianyu_agent.connectors.registry as reg

        with patch.object(reg.http.client, "HTTPSConnection") as conn_cls:
            connection = conn_cls.return_value
            redirected = self._fake_response(connection, status=302,
                                             location="https://h5api.m.goofish.com/page2",
                                             body=b"")
            from unittest.mock import MagicMock

            final = MagicMock()
            final.status = 200
            final.getheader.return_value = None
            final.read = lambda n=-1: b"payload"
            final.getheaders.return_value = [("Content-Type", "text/plain")]
            connection.getresponse.side_effect = [redirected, final]
            result = self.registry.fetch(
                "xianyu-market-read", "https://h5api.m.goofish.com/api"
            )
            self.assertEqual(result["redirects"], 1)
            self.assertEqual(result["status"], 200)

    def test_too_many_redirects(self):
        import xianyu_agent.connectors.registry as reg

        with patch.object(reg.http.client, "HTTPSConnection") as conn_cls:
            connection = conn_cls.return_value
            self._fake_response(connection, status=302,
                                location="https://h5api.m.goofish.com/loop",
                                body=b"")
            with patch.object(reg.ConnectorRegistry, "_open", return_value=connection):
                with self.assertRaises(ConnectorContractError) as ctx:
                    self.registry.fetch("xianyu-market-read",
                                        "https://h5api.m.goofish.com/api")
                self.assertEqual(ctx.exception.code, "too_many_redirects")

    def test_fetch_refuses_non_registered_connector(self):
        with self.assertRaises(ConnectorContractError) as ctx:
            self.registry.fetch("not-registered", "https://example.com/")
        self.assertEqual(ctx.exception.code, "unknown_connector")


class ProbeHonestyTests(unittest.TestCase):
    def test_probe_makes_zero_transport_calls_and_says_so(self):
        registry = ConnectorRegistry()
        registry.register(spec_v1())
        result = registry.probe("xianyu-market-read")
        self.assertEqual(result["transport_calls"], 0)
        self.assertFalse(result["verified_fetch"])
        self.assertIn("verified fetch", result["note"])  # probe != verified fetch

    def test_probe_declares_transport_when_it_does_call(self):
        registry = ConnectorRegistry()
        registry.register(spec_v1(probe_makes_transport_calls=True))
        result = registry.probe("xianyu-market-read")
        self.assertEqual(result["transport_calls"], 1)

    def test_zero_startup_warmup(self):
        """Construct + register + check tools: no socket may be touched."""
        import socket as socket_module

        registry = ConnectorRegistry()
        with patch.object(
            socket_module.socket, "connect",
            side_effect=AssertionError("startup warmup attempted a connection"),
        ):
            registry.register(spec_v1())
            registry.check_tool("xianyu-market-read", "fetch_page",
                                scopes=frozenset({"commerce:read"}))
            registry.probe("xianyu-market-read")
        self.assertEqual(registry.list_connectors(), ["xianyu-market-read"])


class SourceRegistryTests(unittest.TestCase):
    def _registries(self):
        connectors = ConnectorRegistry()
        connectors.register(spec_v1())
        sources = SourceRegistry()
        sources.register(source="market:xianyu_channel",
                         connector="xianyu-market-read",
                         quota_bucket="market:xianyu_channel", enabled=True)
        sources.register(source="supplier:file-import",
                         connector="supplier-file", enabled=True)
        return connectors, sources

    def test_disabling_one_connector_stops_only_its_sources(self):
        connectors, sources = self._registries()
        # register the file connector too so that source can resolve
        connectors.register(spec_v1(name="supplier-file",
                                    upstream_family="local-files"))
        connectors.disable("xianyu-market-read")
        with self.assertRaises(ConnectorContractError) as ctx:
            sources.connector_for("market:xianyu_channel",
                                  connector_registry=connectors)
        self.assertEqual(ctx.exception.code, "connector_unavailable")
        # the other source keeps working
        resolved = sources.connector_for("supplier:file-import",
                                        connector_registry=connectors)
        self.assertEqual(resolved["connector"], "supplier-file")

    def test_disabled_source_refused(self):
        connectors, sources = self._registries()
        sources.register(source="market:paused", connector="xianyu-market-read",
                         enabled=False)
        with self.assertRaises(ConnectorContractError) as ctx:
            sources.connector_for("market:paused", connector_registry=connectors)
        self.assertEqual(ctx.exception.code, "source_disabled")

    def test_unknown_source_refused_no_substitution(self):
        connectors, sources = self._registries()
        with self.assertRaises(ConnectorContractError) as ctx:
            sources.connector_for("market:never-registered",
                                  connector_registry=connectors)
        self.assertEqual(ctx.exception.code, "unknown_source")

    def test_sources_start_disabled_by_default(self):
        sources = SourceRegistry()
        record = sources.register(source="x:new", connector="c")
        self.assertFalse(record["enabled"])

    def test_example_config_parses(self):
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "config" / "source-registry.example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(all(s["enabled"] is False or s["source"] == "supplier:file-import"
                            for s in data["sources"]))


if __name__ == "__main__":
    unittest.main()
