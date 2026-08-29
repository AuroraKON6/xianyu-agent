from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent.db_manager import DBManager


class AccountPromptProfileTests(unittest.TestCase):
    def _db(self) -> DBManager:
        return DBManager(str(Path(tempfile.mkdtemp()) / "prompt_profiles.db"))

    def test_build_prompt_profile_reports_missing_unknown_and_lengths_without_secrets(self):
        from xianyu_agent.account_prompt_profiles import build_prompt_profile

        profile = build_prompt_profile(
            {
                "ai_enabled": True,
                "model_name": "deepseek-v4-pro",
                "api_key": "sk-secret-should-not-leak",
                "base_url": "https://api.example.com",
                "max_discount_percent": 8,
                "max_discount_amount": 50,
                "max_bargain_rounds": 2,
                "custom_prompts": {
                    "price": "firm but polite",
                    "tech": "specific details",
                    "surprise": "unknown prompt",
                },
            }
        )

        self.assertTrue(profile["enabled"])
        self.assertFalse(profile["ready"])
        self.assertEqual(profile["model_name"], "deepseek-v4-pro")
        self.assertEqual(profile["configured_keys"], ["price", "tech"])
        self.assertEqual(profile["unknown_keys"], ["surprise"])
        self.assertIn("classify", profile["missing_required_keys"])
        self.assertIn("default", profile["missing_required_keys"])
        self.assertEqual(profile["prompt_lengths"]["price"], len("firm but polite"))
        self.assertEqual(profile["limits"]["max_bargain_rounds"], 2)

        serialized = json.dumps(profile, ensure_ascii=False)
        self.assertNotIn("sk-secret-should-not-leak", serialized)
        self.assertNotIn("firm but polite", serialized)
        self.assertNotIn("unknown prompt", serialized)

    def test_build_prompt_profile_ready_when_required_keys_present(self):
        from xianyu_agent.account_prompt_profiles import build_prompt_profile

        profile = build_prompt_profile(
            {
                "ai_enabled": True,
                "custom_prompts": {
                    "classify": "classify",
                    "price": "price",
                    "tech": "tech",
                    "default": "default",
                    "after_sales": "after sales",
                },
            }
        )

        self.assertTrue(profile["ready"])
        self.assertEqual(profile["missing_required_keys"], [])
        self.assertIn("Prompt profile is ready for monitored AI reply drafts.", profile["recommended_next_actions"])

    def test_account_prompt_profile_route_uses_account_settings(self):
        from xianyu_agent import server

        db = self._db()
        db.save_cookie("seller_prompt", "raw-cookie-secret", "prompt account")
        db.save_ai_reply_settings(
            "seller_prompt",
            {
                "ai_enabled": True,
                "api_key": "sk-route-secret",
                "custom_prompts": {"price": "route price prompt"},
            },
        )

        with patch.object(server, "db_manager", db):
            client = TestClient(server.app)
            response = client.get("/accounts/seller_prompt/prompt-profile")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["cookie_id"], "seller_prompt")
        self.assertEqual(data["configured_keys"], ["price"])
        self.assertIn("classify", data["missing_required_keys"])
        self.assertNotIn("raw-cookie-secret", str(data))
        self.assertNotIn("sk-route-secret", str(data))
        self.assertNotIn("route price prompt", str(data))

    def test_account_prompt_bootstrap_route_populates_required_prompts_without_echoing_bodies(self):
        from xianyu_agent import server
        from xianyu_agent.account_prompt_profiles import REQUIRED_PROMPT_KEYS

        db = self._db()
        db.save_cookie("seller_bootstrap", "raw-cookie-secret", "bootstrap account")
        db.save_ai_reply_settings(
            "seller_bootstrap",
            {
                "ai_enabled": False,
                "api_key": "sk-bootstrap-secret",
                "custom_prompts": {"price": "existing price prompt"},
            },
        )

        with patch.object(server, "db_manager", db):
            client = TestClient(server.app)
            response = client.post("/accounts/seller_bootstrap/prompt-profile/bootstrap")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["cookie_id"], "seller_bootstrap")
        self.assertTrue(data["ready"])
        self.assertEqual(data["missing_required_keys"], [])
        self.assertEqual(data["configured_required_keys"], sorted(REQUIRED_PROMPT_KEYS))
        self.assertNotIn("raw-cookie-secret", response.text)
        self.assertNotIn("sk-bootstrap-secret", response.text)
        self.assertNotIn("existing price prompt", response.text)

        settings = db.get_ai_reply_settings("seller_bootstrap")
        self.assertTrue(settings["ai_enabled"])
        for key in REQUIRED_PROMPT_KEYS:
            self.assertIn(key, settings["custom_prompts"])
        self.assertEqual(settings["custom_prompts"]["price"], "existing price prompt")


if __name__ == "__main__":
    unittest.main()
