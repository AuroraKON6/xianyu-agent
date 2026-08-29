from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent.adapters import StubAdapter, XianGuanJiaAdapter
from xianyu_agent.config import Settings
from xianyu_agent.storage import JsonStorage


def _settings_from_env(env: dict[str, str]) -> Settings:
    with patch.dict(os.environ, env, clear=True):
        return Settings.from_env(Path(tempfile.mkdtemp()) / "missing.env")


class AdapterSyncContractTests(unittest.TestCase):
    def test_stub_adapter_exposes_safe_sync_methods(self):
        storage = JsonStorage(Path(tempfile.mkdtemp()))
        adapter = StubAdapter(storage)

        orders = adapter.sync_orders(cookie_id="seller_a")
        listings = adapter.sync_my_listings(cookie_id="seller_a")

        self.assertEqual(orders["adapter"], "stub")
        self.assertEqual(listings["adapter"], "stub")
        self.assertFalse(orders["ok"])
        self.assertFalse(listings["ok"])
        self.assertEqual(orders["orders"], [])
        self.assertEqual(listings["items"], [])
        self.assertIn("not implemented", orders["error"])
        self.assertIn("not implemented", listings["error"])

    def test_xianguanjia_adapter_exposes_sync_endpoint_calls_without_secrets(self):
        settings = _settings_from_env(
            {
                "XIANGUANJIA_BASE_URL": "https://xgj.example.com",
                "XIANGUANJIA_TOKEN": "token-should-not-leak",
            }
        )
        adapter = XianGuanJiaAdapter(settings)
        calls = []

        def fake_get(endpoint, params=None):
            calls.append({"endpoint": endpoint, "params": params})
            if endpoint == "/api/orders":
                return {"orders": [{"order_id": "o1"}]}
            return {"items": [{"item_id": "i1"}]}

        adapter._get = fake_get

        orders = adapter.sync_orders(cookie_id="seller_a", status="pending")
        listings = adapter.sync_my_listings(cookie_id="seller_a")

        self.assertEqual(calls[0]["endpoint"], "/api/orders")
        self.assertEqual(calls[0]["params"], {"cookie_id": "seller_a", "status": "pending"})
        self.assertEqual(calls[1]["endpoint"], "/api/listings")
        self.assertEqual(calls[1]["params"], {"cookie_id": "seller_a"})
        self.assertTrue(orders["ok"])
        self.assertTrue(listings["ok"])
        self.assertEqual(orders["orders"], [{"order_id": "o1"}])
        self.assertEqual(listings["items"], [{"item_id": "i1"}])
        self.assertNotIn("token-should-not-leak", json.dumps({"orders": orders, "listings": listings}))

    def test_browser_adapter_sync_methods_delegate_to_async_fetchers(self):
        from xianyu_agent.browser_adapter import BrowserAdapter

        settings = _settings_from_env({"XIANYU_ADAPTER": "browser"})
        adapter = BrowserAdapter(settings, JsonStorage(Path(tempfile.mkdtemp())))
        calls = []

        async def fake_get_orders(cookie_id="default", status=""):
            calls.append(("orders", cookie_id, status))
            return [{"order_id": "o1"}]

        async def fake_get_my_listings(cookie_id="default"):
            calls.append(("listings", cookie_id))
            return [{"item_id": "i1"}]

        adapter.get_orders = fake_get_orders
        adapter.get_my_listings = fake_get_my_listings
        adapter._run_sync = lambda fn, *args, **kwargs: __import__("asyncio").run(fn(*args, **kwargs))

        orders = adapter.sync_orders(cookie_id="seller_a", status="shipped")
        listings = adapter.sync_my_listings(cookie_id="seller_a")

        self.assertEqual(calls, [("orders", "seller_a", "shipped"), ("listings", "seller_a")])
        self.assertEqual(orders["orders"], [{"order_id": "o1"}])
        self.assertEqual(listings["items"], [{"item_id": "i1"}])


class AdapterSyncRouteTests(unittest.TestCase):
    def test_adapter_sync_preview_route_uses_workflow_adapter(self):
        from xianyu_agent import server

        class FakeAdapter:
            name = "fake"

            def sync_orders(self, cookie_id="default", status=""):
                return {"ok": True, "adapter": "fake", "cookie_id": cookie_id, "status": status, "orders": [{"id": "o1"}]}

            def sync_my_listings(self, cookie_id="default"):
                return {"ok": True, "adapter": "fake", "cookie_id": cookie_id, "items": [{"id": "i1"}]}

        original_adapter = server.workflow.adapter
        server.workflow.adapter = FakeAdapter()
        try:
            client = TestClient(server.app)
            response = client.get("/adapter/sync-preview?cookie_id=seller_a&status=paid")
        finally:
            server.workflow.adapter = original_adapter

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["orders"]["orders"], [{"id": "o1"}])
        self.assertEqual(data["listings"]["items"], [{"id": "i1"}])
        self.assertEqual(data["orders"]["cookie_id"], "seller_a")
        self.assertEqual(data["orders"]["status"], "paid")

    def test_opc_dashboard_adapter_health_reports_sync_capabilities(self):
        from xianyu_agent import server

        class FakeAdapter:
            name = "fake"

            def sync_orders(self, cookie_id="default", status=""):
                return {"ok": True, "orders": []}

            def sync_my_listings(self, cookie_id="default"):
                return {"ok": True, "items": []}

        original_adapter = server.workflow.adapter
        server.workflow.adapter = FakeAdapter()
        try:
            client = TestClient(server.app)
            response = client.get("/dashboard/opc-daily")
        finally:
            server.workflow.adapter = original_adapter

        self.assertEqual(response.status_code, 200)
        health = response.json()["adapter_health"]
        self.assertEqual(health["adapter"], "fake")
        self.assertTrue(health["sync_capabilities"]["orders"])
        self.assertTrue(health["sync_capabilities"]["listings"])


if __name__ == "__main__":
    unittest.main()
