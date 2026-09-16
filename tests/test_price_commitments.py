import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from xianyu_agent.price_commitments import (
    PriceCommitmentStore,
    ProductFactsStore,
    extract_price_mentions,
    plausible_against_list_price,
)


def _amounts(text):
    return sorted(mention["amount"] for mention in extract_price_mentions(text))


class PriceExtractionTests(unittest.TestCase):
    def test_extracts_explicit_cny_patterns(self):
        self.assertEqual(_amounts("￥50"), [50.0])
        self.assertEqual(_amounts("¥ 66.5 包邮"), [66.5])
        self.assertEqual(_amounts("最低55元可以吗"), [55.0])
        self.assertEqual(_amounts("60块钱卖不卖"), [60.0])
        self.assertEqual(_amounts("我出45"), [45.0])
        self.assertEqual(_amounts("50出吗"), [50.0])
        self.assertEqual(_amounts("出50"), [50.0])
        self.assertEqual(_amounts("200块或者￥180"), [180.0, 200.0])

    def test_ignores_non_price_text(self):
        self.assertEqual(_amounts("还在吗，多少钱？"), [])
        self.assertEqual(_amounts("可以便宜点吗"), [])
        self.assertEqual(_amounts("发货要几天"), [])

    def test_drops_implausible_amounts(self):
        self.assertEqual(_amounts("￥99999999"), [])
        self.assertEqual(_amounts("0.001元"), [])

    def test_plausibility_band_against_list_price(self):
        self.assertTrue(plausible_against_list_price(90, 100))
        self.assertTrue(plausible_against_list_price(100, 100))
        self.assertFalse(plausible_against_list_price(5, 100))
        self.assertFalse(plausible_against_list_price(150, 100))
        self.assertTrue(plausible_against_list_price(50, None))


class PriceCommitmentStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = PriceCommitmentStore(self.root / "price_commitments.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _buyer(self, amount, source, **kwargs):
        return self.store.record_buyer_offer(
            cookie_id="acc-1",
            chat_id="chat-9",
            item_id="item-7",
            amount=amount,
            source=source,
            **kwargs,
        )

    def _seller(self, amount, source, **kwargs):
        return self.store.record_seller_commit(
            cookie_id="acc-1",
            chat_id="chat-9",
            item_id="item-7",
            amount=amount,
            source=source,
            **kwargs,
        )

    def test_buyer_offer_only_moves_up(self):
        first = self._buyer(50, "msg-1")
        second = self._buyer(60, "msg-2")
        lower = self._buyer(45, "msg-3")
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "applied")
        self.assertEqual(lower["status"], "superseded")
        record = self.store.get_record(cookie_id="acc-1", chat_id="chat-9", item_id="item-7")
        self.assertEqual(record["buyer_highest_offer"]["amount"], 60.0)
        self.assertEqual(len(record["history"]), 3)

    def test_seller_floor_only_moves_down(self):
        first = self._seller(55, "job-1")
        higher = self._seller(60, "job-2")
        lower = self._seller(50, "job-3")
        self.assertEqual(first["status"], "applied")
        self.assertEqual(higher["status"], "superseded")
        self.assertEqual(lower["status"], "applied")
        record = self.store.get_record(cookie_id="acc-1", chat_id="chat-9", item_id="item-7")
        self.assertEqual(record["seller_lowest_committed"]["amount"], 50.0)

    def test_same_source_is_idempotent(self):
        self._buyer(50, "msg-1")
        again = self._buyer(99, "msg-1")
        self.assertEqual(again["status"], "already_recorded")
        record = self.store.get_record(cookie_id="acc-1", chat_id="chat-9", item_id="item-7")
        self.assertEqual(record["buyer_highest_offer"]["amount"], 50.0)
        self.assertEqual(len(record["history"]), 1)

    def test_implausible_offer_against_list_price_is_skipped(self):
        result = self._buyer(5, "msg-1", list_price=100)
        self.assertEqual(result["status"], "skipped_implausible")
        self.assertIsNone(
            self.store.get_record(cookie_id="acc-1", chat_id="chat-9", item_id="item-7")
        )
        accepted = self._buyer(90, "msg-2", list_price=100)
        self.assertEqual(accepted["status"], "applied")

    def test_records_are_isolated_per_chat_and_persist(self):
        self._buyer(50, "msg-1")
        other = self.store.record_buyer_offer(
            cookie_id="acc-1",
            chat_id="chat-other",
            item_id="item-7",
            amount=30,
            source="msg-9",
        )
        self.assertEqual(other["status"], "applied")
        reopened = PriceCommitmentStore(self.store.storage_path)
        first = reopened.get_record(cookie_id="acc-1", chat_id="chat-9", item_id="item-7")
        second = reopened.get_record(cookie_id="acc-1", chat_id="chat-other", item_id="item-7")
        self.assertEqual(first["buyer_highest_offer"]["amount"], 50.0)
        self.assertEqual(second["buyer_highest_offer"]["amount"], 30.0)
        self.assertEqual(reopened.metrics()["record_count"], 2)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            self._buyer(-5, "msg-x")
        with self.assertRaises(ValueError):
            self.store.record_buyer_offer(
                cookie_id="", chat_id="c", item_id="i", amount=5, source="s"
            )


class ProductFactsStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = ProductFactsStore(self.root / "product_facts.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_upsert_keeps_revision_history(self):
        first = self.store.upsert(
            cookie_id="acc-1", item_id="item-7", list_price=100.0, stock_available=3
        )
        self.assertEqual(first["list_price"], 100.0)
        self.assertEqual(len(first["revisions"]), 1)
        second = self.store.upsert(cookie_id="acc-1", item_id="item-7", list_price=95.0)
        self.assertEqual(second["list_price"], 95.0)
        self.assertEqual(len(second["revisions"]), 2)
        self.assertEqual(second["revisions"][1]["changed"], {"list_price": 95.0})
        unchanged = self.store.upsert(cookie_id="acc-1", item_id="item-7", list_price=95.0)
        self.assertEqual(len(unchanged["revisions"]), 2)

    def test_get_and_filter(self):
        self.store.upsert(cookie_id="acc-1", item_id="item-7", list_price=100.0)
        self.store.upsert(cookie_id="acc-2", item_id="item-8", list_price=200.0)
        self.assertIsNone(self.store.get(cookie_id="acc-1", item_id="item-8"))
        self.assertEqual(
            self.store.get(cookie_id="acc-2", item_id="item-8")["list_price"], 200.0
        )
        self.assertEqual(len(self.store.list_facts(cookie_id="acc-1")), 1)

    def test_requires_scope(self):
        with self.assertRaises(ValueError):
            self.store.upsert(cookie_id="", item_id="item-7")


class PriceCommitmentRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_ledger = server.message_intake_ledger
        self.original_events = server.chat_event_store
        self.original_prices = server.price_commitment_store
        self.original_facts = server.product_facts_store
        self.original_workflow = server.workflow
        self.tmp = TemporaryDirectory()
        server.work_order_manager = WorkOrderManager(Path(self.tmp.name) / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(
            Path(self.tmp.name) / "message_intake.json"
        )
        server.chat_event_store = __import__(
            "xianyu_agent.chat_events", fromlist=["ChatEventStore"]
        ).ChatEventStore(Path(self.tmp.name) / "chat_events.json")
        server.price_commitment_store = PriceCommitmentStore(
            Path(self.tmp.name) / "price_commitments.json"
        )
        server.product_facts_store = ProductFactsStore(
            Path(self.tmp.name) / "product_facts.json"
        )
        server.workflow = FakePricingWorkflow()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.message_intake_ledger = self.original_ledger
        self.server.chat_event_store = self.original_events
        self.server.price_commitment_store = self.original_prices
        self.server.product_facts_store = self.original_facts
        self.server.workflow = self.original_workflow
        self.tmp.cleanup()

    def _inbound(self, message, message_id):
        return self.client.post(
            "/workflows/messages/inbound",
            json={
                "source": "xianyu_im",
                "message_id": message_id,
                "buyer_id": "buyer_1",
                "message": message,
                "chat_id": "chat_1",
                "item_id": "item_1",
                "cookie_id": "acc-1",
            },
        )

    def test_inbound_offer_is_recorded_and_surfaces_in_outbox_context(self):
        response = self._inbound("50出吗", "msg-1")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            payload["buyer_price_offers"],
            [{"amount": 50.0, "pattern": "offer_shorthand_suffix", "status": "applied"}],
        )
        records = self.client.get(
            "/price-commitments", params={"cookie_id": "acc-1", "chat_id": "chat_1"}
        ).json()["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["buyer_highest_offer"]["amount"], 50.0)
        self.assertIsNone(records[0]["seller_lowest_committed"])
        outbox = payload.get("reply_outbox") or {}
        context = outbox.get("price_context") or {}
        self.assertEqual(context["buyer_highest_offer"]["amount"], 50.0)

    def test_offer_below_known_list_price_is_skipped(self):
        put = self.client.put(
            "/product-facts",
            json={"cookie_id": "acc-1", "item_id": "item_1", "list_price": 100.0},
        )
        self.assertEqual(put.status_code, 200)
        response = self._inbound("5元卖吗", "msg-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["buyer_price_offers"][0]["status"], "skipped_implausible"
        )
        records = self.client.get("/price-commitments").json()["records"]
        self.assertEqual(records, [])
        facts = self.client.get("/product-facts", params={"cookie_id": "acc-1"}).json()["facts"]
        self.assertEqual(facts[0]["list_price"], 100.0)
        self.assertEqual(len(facts[0]["revisions"]), 1)

    def test_inbound_without_price_mentions_records_nothing(self):
        response = self._inbound("还在吗", "msg-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["buyer_price_offers"], [])
        self.assertEqual(self.client.get("/price-commitments").json()["records"], [])


class FakePricingWorkflow:
    def __init__(self, reply="The item is available. I can ship tonight."):
        self.reply = reply

    def generate_auto_reply(self, **kwargs):
        return self.reply

    def handle_feishu_text(self, text):
        return {"type": "legacy_text", "text": text}


if __name__ == "__main__":
    unittest.main()
