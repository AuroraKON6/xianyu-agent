from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xianyu_agent.adapters import XianGuanJiaAdapter, xianguanjia_health
from xianyu_agent.config import Settings
from xianyu_agent.models import ListingDraft


def _settings_from_env(env: dict[str, str]) -> Settings:
    with patch.dict(os.environ, env, clear=True):
        return Settings.from_env(Path(tempfile.mkdtemp()) / "missing.env")


class XianGuanJiaHealthTests(unittest.TestCase):
    def test_health_reports_missing_base_url(self):
        settings = _settings_from_env({"XIANYU_ADAPTER": "xianguanjia"})

        health = xianguanjia_health(settings)

        self.assertFalse(health["configured"])
        self.assertFalse(health["ready"])
        self.assertIn("XIANGUANJIA_BASE_URL", health["missing"])

    def test_health_reports_auth_modes_without_secrets(self):
        settings = _settings_from_env(
            {
                "XIANYU_ADAPTER": "xianguanjia",
                "XIANGUANJIA_BASE_URL": "https://xgj.example.com",
                "XIANGUANJIA_TOKEN": "token-should-not-leak",
                "XIANGUANJIA_APP_ID": "user-001",
                "XIANGUANJIA_API_KEY": "secret-should-not-leak",
            }
        )

        health = xianguanjia_health(settings)
        serialized = json.dumps(health, ensure_ascii=False)

        self.assertTrue(health["configured"])
        self.assertTrue(health["ready"])
        self.assertEqual(health["missing"], [])
        self.assertEqual(health["base_url"], "https://xgj.example.com")
        self.assertIn("bearer_token", health["auth_modes"])
        self.assertIn("legacy_sha1_headers", health["auth_modes"])
        self.assertNotIn("token-should-not-leak", serialized)
        self.assertNotIn("secret-should-not-leak", serialized)

    def test_app_key_secret_aliases_populate_legacy_credentials(self):
        settings = _settings_from_env(
            {
                "XIANGUANJIA_BASE_URL": "https://xgj.example.com",
                "XIANGUANJIA_APP_KEY": "alias-app-key",
                "XIANGUANJIA_APP_SECRET": "alias-app-secret",
            }
        )

        self.assertEqual(settings.xianguanjia_app_id, "alias-app-key")
        self.assertEqual(settings.xianguanjia_api_key, "alias-app-secret")


class XianGuanJiaAdapterTests(unittest.TestCase):
    def test_build_url_accepts_relative_and_absolute_endpoints(self):
        settings = _settings_from_env({"XIANGUANJIA_BASE_URL": "https://xgj.example.com/root"})
        adapter = XianGuanJiaAdapter(settings)

        self.assertEqual(adapter._build_url("/api/listings"), "https://xgj.example.com/root/api/listings")
        self.assertEqual(adapter._build_url("api/listings"), "https://xgj.example.com/root/api/listings")
        self.assertEqual(adapter._build_url("https://api.example.com/listings"), "https://api.example.com/listings")

    def test_build_headers_adds_bearer_and_legacy_sha1_signature(self):
        settings = _settings_from_env(
            {
                "XIANGUANJIA_BASE_URL": "https://xgj.example.com",
                "XIANGUANJIA_TOKEN": "bearer-token",
                "XIANGUANJIA_APP_ID": "user-001",
                "XIANGUANJIA_API_KEY": "legacy-secret",
            }
        )
        adapter = XianGuanJiaAdapter(settings)
        body = adapter._build_body({"price": 12, "name": "测试"})

        headers = adapter._build_headers(body, timestamp="1710000000000")

        expected = hashlib.sha1(f"1710000000000{body}legacy-secret".encode("utf-8")).hexdigest()
        self.assertEqual(headers["Authorization"], "Bearer bearer-token")
        self.assertEqual(headers["Timestamp"], "1710000000000")
        self.assertEqual(headers["UserId"], "user-001")
        self.assertEqual(headers["Sign"], expected)

    def test_preflight_delegates_to_health(self):
        settings = _settings_from_env(
            {
                "XIANGUANJIA_BASE_URL": "https://xgj.example.com",
                "XIANGUANJIA_TOKEN": "bearer-token",
            }
        )
        adapter = XianGuanJiaAdapter(settings)

        self.assertTrue(adapter.preflight()["ready"])

    def test_build_listing_payload_maps_listing_draft_without_raw_secrets(self):
        settings = _settings_from_env({"XIANGUANJIA_BASE_URL": "https://xgj.example.com"})
        adapter = XianGuanJiaAdapter(settings)
        draft = ListingDraft(
            title="商用不锈钢操作台",
            category="商用厨具",
            price=1280.0,
            stock=2,
            condition="new",
            location="杭州",
            description="规格清楚，物流发货",
            tags=["操作台", "不锈钢"],
            selected_images=["authorized-1.jpg"],
            delivery_type="shipping",
            delivery_payload="物流到付",
            source="resale:rc_1",
            raw_text="operator draft only",
        )

        payload = adapter._build_listing_payload(draft)
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["title"], "商用不锈钢操作台")
        self.assertEqual(payload["category"], "商用厨具")
        self.assertEqual(payload["price"], 1280.0)
        self.assertEqual(payload["stock"], 2)
        self.assertEqual(payload["images"], ["authorized-1.jpg"])
        self.assertEqual(payload["delivery"]["type"], "shipping")
        self.assertNotIn("cookie", serialized.lower())
        self.assertNotIn("token", serialized.lower())

    def test_publish_uses_payload_mapper_and_nested_response_id(self):
        settings = _settings_from_env(
            {
                "XIANGUANJIA_BASE_URL": "https://xgj.example.com",
                "XIANGUANJIA_CREATE_ENDPOINT": "/provider/create",
            }
        )
        adapter = XianGuanJiaAdapter(settings)
        calls = []
        adapter._post = lambda endpoint, payload: calls.append((endpoint, payload)) or {
            "ok": True,
            "data": {"item_id": "item_nested_1"},
            "message": "created",
        }
        draft = ListingDraft(title="测试商品", category="商用厨具", price=99.0, stock=1)

        result = adapter.publish(draft)

        self.assertEqual(calls[0][0], "/provider/create")
        self.assertEqual(calls[0][1]["title"], "测试商品")
        self.assertIn("delivery", calls[0][1])
        self.assertTrue(result.ok)
        self.assertEqual(result.external_id, "item_nested_1")


class XianGuanJiaCLITests(unittest.TestCase):
    def test_config_check_includes_xianguanjia_health(self):
        from xianyu_agent.cli import main

        settings = _settings_from_env(
            {
                "XIANYU_ADAPTER": "xianguanjia",
                "XIANGUANJIA_BASE_URL": "https://xgj.example.com",
                "XIANGUANJIA_TOKEN": "token-should-not-leak",
            }
        )

        with patch("xianyu_agent.cli.Settings.from_env", return_value=settings):
            with patch("builtins.print") as mock_print:
                rc = main(["config-check"])

        self.assertEqual(rc, 0)
        payload = json.loads(mock_print.call_args[0][0])
        self.assertTrue(payload["xianguanjia_configured"])
        self.assertTrue(payload["xianguanjia"]["ready"])
        self.assertIn("bearer_token", payload["xianguanjia"]["auth_modes"])
        self.assertNotIn("token-should-not-leak", mock_print.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
