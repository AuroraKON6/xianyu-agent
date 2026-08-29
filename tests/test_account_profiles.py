from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent.db_manager import DBManager


class AccountProfileTests(unittest.TestCase):
    def _db(self) -> DBManager:
        return DBManager(str(Path(tempfile.mkdtemp()) / "profiles.db"))

    def test_build_profiles_summarizes_account_capabilities_without_secrets(self):
        from xianyu_agent.account_profiles import build_account_profiles

        db = self._db()
        db.save_cookie("seller_a", "raw-cookie-should-not-leak", "main kitchen account")
        db.save_cookie_status("seller_a", True)
        db.save_keywords("seller_a", [("尺寸", "支持定制"), ("发货", "当天发")])
        db.save_ai_reply_settings(
            "seller_a",
            {
                "ai_enabled": True,
                "model_name": "deepseek-v4-flash",
                "api_key": "sk-should-not-leak",
                "base_url": "https://api.deepseek.com",
                "max_discount_percent": 8,
                "max_discount_amount": 50,
                "max_bargain_rounds": 2,
                "custom_prompts": {"price": "firm", "tech": "specific"},
            },
        )
        db.save_order("order_1", "seller_a", status="paid", amount=188)
        db.save_order("order_2", "seller_a", status="shipped", amount=288)
        db.save_item("item_1", "seller_a", title="商用操作台", status="active")

        profiles = build_account_profiles(db)

        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile["cookie_id"], "seller_a")
        self.assertEqual(profile["remark"], "main kitchen account")
        self.assertTrue(profile["enabled"])
        self.assertEqual(profile["counts"]["keyword_rules"], 2)
        self.assertEqual(profile["counts"]["items"], 1)
        self.assertEqual(profile["counts"]["orders_total"], 2)
        self.assertEqual(profile["counts"]["orders_by_status"], {"paid": 1, "shipped": 1})
        self.assertTrue(profile["ai"]["enabled"])
        self.assertEqual(profile["ai"]["model_name"], "deepseek-v4-flash")
        self.assertEqual(profile["ai"]["custom_prompt_keys"], ["price", "tech"])
        self.assertTrue(profile["capabilities"]["seller_auto_reply"])
        self.assertTrue(profile["capabilities"]["keyword_reply"])
        self.assertTrue(profile["capabilities"]["order_fulfillment_tracking"])

        serialized = str(profile)
        self.assertNotIn("raw-cookie-should-not-leak", serialized)
        self.assertNotIn("sk-should-not-leak", serialized)

    def test_build_profiles_recommends_next_actions_for_empty_disabled_account(self):
        from xianyu_agent.account_profiles import build_account_profiles

        db = self._db()
        db.save_cookie("seller_b", "cookie", "backup")
        db.save_cookie_status("seller_b", False)

        profile = build_account_profiles(db)[0]

        self.assertFalse(profile["enabled"])
        self.assertFalse(profile["capabilities"]["seller_auto_reply"])
        self.assertIn("Enable the account before running live monitoring.", profile["recommended_next_actions"])
        self.assertIn("Add keyword rules or enable AI reply for this account.", profile["recommended_next_actions"])

    def test_accounts_profiles_route_returns_profile_list(self):
        from xianyu_agent import server

        db = self._db()
        db.save_cookie("seller_route", "route-raw-cookie-secret", "route account")
        db.save_keywords("seller_route", [("还在吗", "在的")])

        with patch.object(server, "db_manager", db):
            client = TestClient(server.app)
            response = client.get("/accounts/profiles")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data[0]["cookie_id"], "seller_route")
        self.assertEqual(data[0]["counts"]["keyword_rules"], 1)
        self.assertNotIn("route-raw-cookie-secret", str(data[0]))

    def test_add_account_route_trims_enables_and_never_echoes_cookie(self):
        from xianyu_agent import server

        db = self._db()

        with patch.object(server, "db_manager", db):
            client = TestClient(server.app)
            response = client.post(
                "/accounts",
                json={
                    "cookie_id": "  seller_main  ",
                    "cookie_value": "  raw-cookie-secret  ",
                    "remark": "  main account  ",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["cookie_id"], "seller_main")
        self.assertTrue(data["enabled"])
        self.assertNotIn("raw-cookie-secret", response.text)
        self.assertEqual(db.get_cookie("seller_main"), "raw-cookie-secret")
        self.assertEqual((db.get_cookie_details("seller_main") or {}).get("remark"), "main account")
        self.assertTrue(db.get_all_cookie_status()["seller_main"])

    def test_add_account_route_rejects_blank_cookie_id_or_value(self):
        from xianyu_agent import server

        db = self._db()

        with patch.object(server, "db_manager", db):
            client = TestClient(server.app)
            blank_id = client.post("/accounts", json={"cookie_id": " ", "cookie_value": "cookie"})
            blank_cookie = client.post("/accounts", json={"cookie_id": "seller", "cookie_value": " "})

        self.assertEqual(blank_id.status_code, 400, blank_id.text)
        self.assertEqual(blank_cookie.status_code, 400, blank_cookie.text)
        self.assertEqual(db.get_all_cookies(), {})


if __name__ == "__main__":
    unittest.main()
