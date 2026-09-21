"""WP11 LLM polish tests: mock client, guards are the acceptance surface."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xianyu_agent.price_commitments import PriceCommitmentStore, ProductFactsStore
from xianyu_agent.reply_drafts import DraftReplyService


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error

    def chat_json(self, system, user, temperature=0.3, max_tokens=500):
        if self.error:
            raise self.error
        return {"reply": self.reply}


class PolishGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.facts = ProductFactsStore(root / "facts.json")
        self.commitments = PriceCommitmentStore(root / "commitments.json")
        self.service = DraftReplyService(root, self.facts, self.commitments)
        self.facts.upsert(
            cookie_id="acc-1", item_id="item-1", list_price=299.0, stock_available=3, ships_in_days=2
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _draft(self):
        return self.service.draft_reply(
            cookie_id="acc-1", chat_id="chat-1", item_id="item-1", buyer_text="200卖吗", floor=180.0
        )

    def test_clean_polish_accepted_with_same_amounts(self):
        draft = self._draft()
        polished = self.service.polish_draft(
            draft, client=FakeClient(reply="可以的呢，200 元给您，现在有货，2 天内发货～"
            )
        )
        # note: original body contains 有货/发货 promises from verified facts
        self.assertTrue(polished["polish_accepted"])
        self.assertNotEqual(polished["record_id"], draft["record_id"])
        self.assertEqual(polished["polish_of"], draft["record_id"])

    def test_invented_amount_rejected_keeps_deterministic_body(self):
        draft = self._draft()
        polished = self.service.polish_draft(draft, client=FakeClient(reply="好的，190 元成交！"))
        self.assertFalse(polished["polish_accepted"])
        self.assertEqual(polished["body"], draft["body"])
        self.assertTrue(any("absent from the deterministic draft" in n for n in polished["polish_guard_notes"]))

    def test_polish_cannot_replace_offer_with_the_internal_floor(self):
        """P2 regression: quoting 200 but polishing to the floor 180 is a
        price change, not a rewording -- even though 180 is internally
        known (floor). The buyer-facing price must stay 200."""
        draft = self._draft()  # floor=180, offer=200 accepted at 200
        self.assertIn("200", draft["body"])
        polished = self.service.polish_draft(draft, client=FakeClient(reply="可以，180 元给您！"))
        self.assertFalse(polished["polish_accepted"])
        self.assertEqual(polished["body"], draft["body"])
        self.assertIn("200", polished["body"])

    def test_added_promise_rejected(self):
        draft = self.service.draft_reply(
            cookie_id="acc-1", chat_id="chat-2", item_id="item-1", buyer_text="还有货吗", floor=None
        )
        # deterministic stock answer says 当前可售 3 件 (no arrival promise)
        polished = self.service.polish_draft(
            draft, client=FakeClient(reply="有货的，保证 24 小时内到货哦")
        )
        self.assertFalse(polished["polish_accepted"])
        # rejected either by the arrival-guarantee claim pattern or the
        # added-promise guard; both keep the deterministic body
        self.assertTrue(polished["polish_guard_notes"])
        self.assertEqual(polished["body"], draft["body"])

    def test_unverified_claim_pattern_rejected(self):
        draft = self._draft()
        polished = self.service.polish_draft(
            draft, client=FakeClient(reply="可以，我们在其他平台也有店铺，好评返现5元")
        )
        self.assertFalse(polished["polish_accepted"])

    def test_llm_failure_falls_back_to_deterministic(self):
        draft = self._draft()
        polished = self.service.polish_draft(draft, client=FakeClient(error=RuntimeError("timeout")))
        self.assertFalse(polished["polish_accepted"])
        self.assertIn("llm error", polished["polish_failure"])
        self.assertEqual(polished["body"], draft["body"])

    def test_escalate_drafts_never_polished(self):
        draft = self.service.draft_reply(
            cookie_id="acc-1", chat_id="chat-3", item_id="item-1", buyer_text="我不要了，退款吧"
        )
        polished = self.service.polish_draft(draft, client=FakeClient(reply="随便说点什么"))
        self.assertEqual(polished["record_id"], draft["record_id"])  # returned unchanged


if __name__ == "__main__":
    unittest.main()
