"""WP11 reply draft service tests: guards are the acceptance surface."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xianyu_agent.price_commitments import PriceCommitmentStore, ProductFactsStore
from xianyu_agent.reply_drafts import (
    DRAFT_COUNTER_OFFER,
    DRAFT_DECLINE,
    DRAFT_ESCALATE,
    DRAFT_FACT_ANSWER,
    DRAFT_TAKEOVER_HOLD,
    DraftReplyService,
    analyze_money_mentions,
    classify_intent,
    evaluate_reply_scenarios,
)

FIXTURE = Path(__file__).parent / "fixtures" / "reply_scenarios.jsonl"


class MoneyGuardTests(unittest.TestCase):
    def test_negation_excludes_only_the_negated_amount(self):
        money = analyze_money_mentions("不要300，我最多出210")
        self.assertEqual(money["operative_offer"], 210.0)
        verdicts = {m["amount"]: m["verdict"] for m in money["mentions"]}
        self.assertEqual(verdicts[300.0], "negated")
        self.assertEqual(verdicts[210.0], "offer")

    def test_negation_after_amount_does_not_kill_it(self):
        money = analyze_money_mentions("我是说商品195，不是运费")
        self.assertEqual(money["operative_offer"], 195.0)

    def test_freight_is_not_price(self):
        money = analyze_money_mentions("邮费我出10块，东西270卖吗")
        verdicts = {m["amount"]: m["verdict"] for m in money["mentions"]}
        self.assertEqual(verdicts[10.0], "freight")
        self.assertEqual(money["operative_offer"], 270.0)

    def test_freight_included_stays_an_offer(self):
        money = analyze_money_mentions("290含运费出吗")
        self.assertEqual(money["operative_offer"], 290.0)

    def test_third_party_reference_is_not_an_offer(self):
        money = analyze_money_mentions("别人家同款才卖240，你280太贵了")
        self.assertIsNone(money["operative_offer"])
        self.assertTrue(all(m["verdict"] in ("reference",) for m in money["mentions"]))

    def test_multi_item_total_not_a_unit_offer(self):
        money = analyze_money_mentions("两个60卖吗")
        self.assertIsNone(money["operative_offer"])
        self.assertEqual(money["mentions"][0]["verdict"], "multi_item_total")

    def test_stale_quote_not_an_offer(self):
        money = analyze_money_mentions("上次你说280，现在还算数吗")
        self.assertIsNone(money["operative_offer"])
        self.assertEqual(money["mentions"][0]["verdict"], "stale")

    def test_model_number_not_an_amount(self):
        money = analyze_money_mentions("型号是GZ-1200吗")
        self.assertIsNone(money["operative_offer"])
        self.assertEqual(money["mentions"][0]["verdict"], "model_number")

    def test_last_offer_wins(self):
        money = analyze_money_mentions("200可以吗？再加点，230行吗")
        self.assertEqual(money["operative_offer"], 230.0)


class DraftServiceGuardTests(unittest.TestCase):
    """Zero-tolerance guard behavior of the draft service."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.facts = ProductFactsStore(root / "facts.json")
        self.commitments = PriceCommitmentStore(root / "commitments.json")
        self.service = DraftReplyService(root, self.facts, self.commitments)

    def tearDown(self):
        self.tmp.cleanup()

    def _draft(self, text, **kwargs):
        params = dict(cookie_id="acc-1", chat_id="chat-1", item_id="item-1", buyer_text=text, floor=180.0)
        params.update(kwargs)
        return self.service.draft_reply(**params)

    def test_offer_below_floor_declines_without_promise(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0, stock_available=3, ships_in_days=2)
        draft = self._draft("150卖吗")
        self.assertEqual(draft["draft_type"], DRAFT_DECLINE)
        self.assertIn("低于", draft["body"])
        record = self.commitments.get_record(cookie_id="acc-1", chat_id="chat-1", item_id="item-1")
        self.assertEqual(record["buyer_highest_offer"]["amount"], 150.0)
        self.assertIsNone(record.get("seller_lowest_committed"))

    def test_offer_above_floor_accepts_as_draft_only(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0, stock_available=3, ships_in_days=2)
        draft = self._draft("200卖吗")
        self.assertEqual(draft["draft_type"], DRAFT_COUNTER_OFFER)
        self.assertEqual(draft["status"], "draft_needs_approval")
        self.assertIn("send_path", draft)  # drafts only; no send path exists
        # seller commitment NOT written (only platform-confirmed sends may)
        record = self.commitments.get_record(cookie_id="acc-1", chat_id="chat-1", item_id="item-1")
        self.assertIsNone(record.get("seller_lowest_committed"))

    def test_no_floor_available_escalates_instead_of_guessing(self):
        # no facts, no commitments, no explicit floor
        draft = self.service.draft_reply(
            cookie_id="acc-1", chat_id="chat-1", item_id="item-1", buyer_text="200卖吗", floor=None
        )
        self.assertEqual(draft["draft_type"], DRAFT_ESCALATE)

    def test_unknown_stock_never_promised(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0)  # no stock
        draft = self._draft("200卖吗")
        self.assertEqual(draft["draft_type"], DRAFT_COUNTER_OFFER)
        self.assertIn("确认一下库存", draft["body"])
        self.assertNotIn("天内发货", draft["body"])

    def test_stock_question_with_unknown_stock_escalates(self):
        draft = self._draft("还有货吗")
        self.assertEqual(draft["draft_type"], DRAFT_ESCALATE)
        self.assertTrue(any("stock unknown" in note for note in draft["notes"]))

    def test_stock_question_with_facts_answers_with_revision_evidence(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", stock_available=5, list_price=299.0)
        draft = self._draft("还有货吗")
        self.assertEqual(draft["draft_type"], DRAFT_FACT_ANSWER)
        self.assertIn("5 件", draft["body"])
        self.assertTrue(any(ref["ref_type"] == "product_facts_revision" for ref in draft["evidence_refs"]))

    def test_shipping_unknown_escalates_without_arrival_promise(self):
        draft = self._draft("几天能到")
        self.assertEqual(draft["draft_type"], DRAFT_ESCALATE)
        self.assertTrue(any("no arrival promise" in note for note in draft["notes"]))

    def test_refund_request_always_escalates(self):
        draft = self._draft("我不要了，退款吧")
        self.assertEqual(draft["draft_type"], DRAFT_ESCALATE)
        self.assertTrue(any("human decisions" in note for note in draft["notes"]))

    def test_freight_amount_never_recorded_as_offer(self):
        draft = self._draft("运费15元是吗")
        # shipping question path; no buyer offer recorded at all
        record = self.commitments.get_record(cookie_id="acc-1", chat_id="chat-1", item_id="item-1")
        self.assertIsNone(record)

    def test_manual_takeover_blocks_drafts(self):
        draft = self._draft(
            "200卖吗",
            manual_takeover_until=1000.0,
            latest_inbound_at=2000.0,  # newer message after takeover
        )
        self.assertEqual(draft["draft_type"], DRAFT_TAKEOVER_HOLD)
        self.assertEqual(draft["body"], "")
        # no commitment written for held messages
        self.assertIsNone(self.commitments.get_record(cookie_id="acc-1", chat_id="chat-1", item_id="item-1"))

    def test_floor_respects_previous_seller_commitment(self):
        # seller previously committed (platform-confirmed) to 250; the
        # effective floor must not drop below it even if the caller
        # passes a lower explicit floor
        self.commitments.record_seller_commit(
            cookie_id="acc-1", chat_id="chat-1", item_id="item-1", amount=250.0, source="platform_receipt"
        )
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0)
        accepted = self._draft("260卖吗", floor=180.0)
        self.assertGreaterEqual(accepted["effective_floor"], 250.0)
        self.assertEqual(accepted["draft_type"], DRAFT_COUNTER_OFFER)
        below = self._draft("240卖吗")
        self.assertEqual(below["draft_type"], DRAFT_DECLINE)

    def test_fact_update_changes_draft_and_cites_new_revision(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", stock_available=5, list_price=299.0)
        first = self._draft("还有货吗")
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", stock_available=0)
        second = self._draft("还有货吗")
        self.assertIn("5 件", first["body"])
        self.assertIn("缺货", second["body"])
        self.assertNotEqual(
            first["evidence_refs"][0]["ref_id"], second["evidence_refs"][0]["ref_id"]
        )

    def test_duplicate_message_produces_second_draft_not_a_send(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0, stock_available=2, ships_in_days=1)
        first = self._draft("200卖吗")
        second = self._draft("200卖吗")
        self.assertNotEqual(first["record_id"], second["record_id"])
        # both are drafts; neither is a send or a commitment
        self.assertEqual(second["status"], "draft_needs_approval")


class ScenarioEvaluationTests(unittest.TestCase):
    def test_seed_set_meets_200_gate_with_clean_guards(self):
        scenarios = [
            json.loads(line)
            for line in FIXTURE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertGreaterEqual(len(scenarios), 200)
        report = evaluate_reply_scenarios(scenarios)
        self.assertGreaterEqual(report["total"], 200)
        self.assertIn("met", report["sample_size_note"])
        # guard-critical categories must be perfect
        for category in ("negation", "freight", "reference_price", "multi_item", "stale_quote"):
            bucket = report["by_category"].get(category, {})
            self.assertEqual(bucket.get("offer_ok"), bucket.get("total"), f"category {category} has offer mistakes")
            self.assertEqual(bucket.get("intent_ok"), bucket.get("total"), f"category {category} has intent mistakes")
        self.assertGreaterEqual(report["intent_accuracy"], 0.98)
        self.assertGreaterEqual(report["offer_accuracy"], 0.98)


if __name__ == "__main__":
    unittest.main()
