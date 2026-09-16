import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


class FakeTraceWorkflow:
    def __init__(self, reply="The item is available. I can ship tonight."):
        self.reply = reply

    def generate_auto_reply(self, **kwargs):
        return self.reply

    def handle_feishu_text(self, text):
        return {"type": "legacy_text", "text": text}


class ReplyTraceRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.chat_events import ChatEventStore
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.price_commitments import (
            PriceCommitmentStore,
            ProductFactsStore,
        )
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
        server.chat_event_store = ChatEventStore(Path(self.tmp.name) / "chat_events.json")
        server.price_commitment_store = PriceCommitmentStore(
            Path(self.tmp.name) / "price_commitments.json"
        )
        server.product_facts_store = ProductFactsStore(
            Path(self.tmp.name) / "product_facts.json"
        )
        server.workflow = FakeTraceWorkflow()
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

    def test_trace_assembles_sorted_timeline_and_current_state(self):
        facts = self.client.put(
            "/product-facts",
            json={"cookie_id": "acc-1", "item_id": "item_1", "list_price": 100.0},
        )
        self.assertEqual(facts.status_code, 200)
        inbound = self._inbound("50出吗", "msg-1")
        self.assertEqual(inbound.status_code, 200)

        trace = self.client.get(
            "/reply-trace", params={"cookie_id": "acc-1", "chat_id": "chat_1"}
        )
        self.assertEqual(trace.status_code, 200, trace.text)
        payload = trace.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["chain"]["ok"])
        kinds = [entry["kind"] for entry in payload["timeline"]]
        self.assertIn("facts.revision", kinds)
        self.assertIn("chat.inbound", kinds)
        self.assertIn("price.buyer", kinds)
        timestamps = [entry["ts"] for entry in payload["timeline"]]
        self.assertEqual(timestamps, sorted(timestamps))
        state = payload["current_state"]
        self.assertEqual(
            state["price_records"][0]["buyer_highest_offer"]["amount"], 50.0
        )
        self.assertEqual(state["product_facts"][0]["list_price"], 100.0)

    def test_trace_requires_scope_parameters(self):
        missing = self.client.get("/reply-trace")
        self.assertEqual(missing.status_code, 400)
        partial = self.client.get("/reply-trace", params={"cookie_id": "acc-1"})
        self.assertEqual(partial.status_code, 400)

    def test_trace_for_unknown_chat_is_empty_but_valid(self):
        payload = self.client.get(
            "/reply-trace", params={"cookie_id": "acc-9", "chat_id": "chat-x"}
        ).json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["timeline"], [])
        self.assertEqual(payload["current_state"]["price_records"], [])


if __name__ == "__main__":
    unittest.main()
