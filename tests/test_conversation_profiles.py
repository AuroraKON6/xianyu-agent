from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent.db_manager import DBManager


class ConversationProfileTests(unittest.TestCase):
    def _db(self) -> DBManager:
        return DBManager(str(Path(tempfile.mkdtemp()) / "conversation_profiles.db"))

    def test_summarize_conversation_counts_intents_and_redacts_contact(self):
        from xianyu_agent.conversation_profiles import summarize_conversation

        history = [
            {
                "role": "buyer",
                "content": "最低多少钱，电话 13800138000",
                "intent": "price",
                "bargain_count": 1,
                "buyer_id": "buyer-should-not-leak",
                "chat_id": "chat-should-not-leak",
            },
            {
                "role": "assistant",
                "content": "价格已经很实在，可以当天发。",
                "intent": "price",
                "bargain_count": 1,
            },
            {
                "role": "buyer",
                "content": "能再便宜点吗",
                "intent": "bargain",
                "bargain_count": 2,
            },
        ]

        summary = summarize_conversation(history)

        self.assertEqual(summary["turns"], 3)
        self.assertEqual(summary["last_intent"], "bargain")
        self.assertEqual(summary["max_bargain_count"], 2)
        self.assertEqual(summary["intent_counts"], {"price": 2, "bargain": 1})
        self.assertEqual(len(summary["recent_messages"]), 3)
        self.assertIn("off_platform_contact", summary["risk_flags"])
        self.assertEqual(summary["next_operator_action"], "review_risk_before_reply")

        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("buyer-should-not-leak", serialized)
        self.assertNotIn("chat-should-not-leak", serialized)
        self.assertNotIn("13800138000", serialized)

    def test_summarize_empty_conversation(self):
        from xianyu_agent.conversation_profiles import summarize_conversation

        summary = summarize_conversation([])

        self.assertEqual(summary["turns"], 0)
        self.assertEqual(summary["last_intent"], "")
        self.assertEqual(summary["max_bargain_count"], 0)
        self.assertEqual(summary["recent_messages"], [])
        self.assertEqual(summary["next_operator_action"], "wait_for_buyer_message")

    def test_conversation_summary_route_reads_history_without_exposing_ids(self):
        from xianyu_agent import server

        db = self._db()
        db.save_cookie("seller_chat", "raw-cookie-secret", "chat account")
        db.save_conversation(
            "seller_chat",
            "chat_route_secret",
            "buyer_route_secret",
            "item_1",
            "buyer",
            "还在吗，QQ 123456",
            intent="default",
            bargain_count=0,
        )
        db.save_conversation(
            "seller_chat",
            "chat_route_secret",
            "buyer_route_secret",
            "item_1",
            "assistant",
            "在的，可以发货。",
            intent="default",
            bargain_count=0,
        )

        with patch.object(server, "db_manager", db):
            client = TestClient(server.app)
            response = client.get("/accounts/seller_chat/conversation-summary?chat_id=chat_route_secret")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["cookie_id"], "seller_chat")
        self.assertEqual(data["turns"], 2)
        self.assertEqual(data["last_intent"], "default")

        serialized = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("raw-cookie-secret", serialized)
        self.assertNotIn("chat_route_secret", serialized)
        self.assertNotIn("buyer_route_secret", serialized)
        self.assertNotIn("123456", serialized)


if __name__ == "__main__":
    unittest.main()
