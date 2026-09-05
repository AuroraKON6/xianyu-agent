import json
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from xianyu_agent.chat_events import ChatEventStore


def _inbound_envelope(envelope_id, *, message="hello", cookie_id="acc-1", chat_id="chat-9"):
    return {
        "envelope_id": envelope_id,
        "dedupe_key": f"message_id:ws:{cookie_id}:{envelope_id}",
        "source": "ws",
        "source_message_id": envelope_id,
        "cookie_id": cookie_id,
        "buyer_id": "buyer-1",
        "buyer_name": "Buyer One",
        "chat_id": chat_id,
        "item_id": "item-7",
        "message_text": message,
        "message_type": "text",
        "direction": "inbound",
        "occurred_at": 1000.0,
    }


class ChatEventStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = ChatEventStore(self.root / "chat_events.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_appends_are_sequenced_and_hash_chained(self):
        first = self.store.append_inbound(
            _inbound_envelope("env-a"), intake_id="intake-1"
        )
        second = self.store.append_outbound(
            execution_job_id="job-1",
            cookie_id="acc-1",
            chat_id="chat-9",
            buyer_id="buyer-1",
            item_id="item-7",
            message_text="fixed reply",
            source_message_id="env-a",
            send_outcome="sent",
        )
        self.assertEqual(first["status"], "appended")
        self.assertEqual(second["status"], "appended")
        events = self.store.list_events()
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual([event["direction"] for event in events], ["inbound", "outbound"])
        self.assertEqual(events[0]["intake_id"], "intake-1")
        self.assertEqual(events[1]["execution_job_id"], "job-1")
        self.assertEqual(events[1]["source_message_id"], "env-a")
        chain = self.store.verify_chain()
        self.assertTrue(chain["ok"], chain["errors"])
        self.assertEqual(chain["event_count"], 2)
        self.assertEqual(events[1]["previous_event_hash"], events[0]["event_hash"])

    def test_inbound_reappend_is_idempotent_and_conflict_is_rejected(self):
        envelope = _inbound_envelope("env-a")
        first = self.store.append_inbound(envelope)
        again = self.store.append_inbound(envelope)
        self.assertEqual(first["status"], "appended")
        self.assertEqual(again["status"], "already_recorded")
        self.assertEqual(again["event"]["event_id"], first["event"]["event_id"])
        self.assertEqual(len(self.store.list_events()), 1)

        conflicting = _inbound_envelope("env-a", message="changed text")
        with self.assertRaises(ValueError):
            self.store.append_inbound(conflicting)

    def test_inbound_event_requires_envelope_id(self):
        with self.assertRaises(ValueError):
            self.store.append_inbound({"message_text": "no id"})

    def test_outbound_reappend_is_idempotent_and_outcome_conflict_rejected(self):
        kwargs = dict(
            execution_job_id="job-1",
            cookie_id="acc-1",
            buyer_id="buyer-1",
            message_text="fixed reply",
        )
        first = self.store.append_outbound(send_outcome="sent", **kwargs)
        again = self.store.append_outbound(send_outcome="sent", **kwargs)
        self.assertEqual(first["status"], "appended")
        self.assertEqual(again["status"], "already_recorded")
        self.assertEqual(len(self.store.list_events()), 1)

        with self.assertRaises(ValueError):
            self.store.append_outbound(send_outcome="verification_required", **kwargs)
        with self.assertRaises(ValueError):
            self.store.append_outbound(send_outcome="bogus", **kwargs)
        with self.assertRaises(ValueError):
            self.store.append_outbound(send_outcome="sent", execution_job_id="")

    def test_filters_and_backward_pagination(self):
        for index, cookie in enumerate(["acc-1", "acc-2", "acc-1", "acc-1"], start=1):
            self.store.append_inbound(
                _inbound_envelope(f"env-{index}", cookie_id=cookie, chat_id=f"chat-{index}")
            )
        page = self.store.list_events(limit=2)
        self.assertEqual([event["sequence"] for event in page], [3, 4])
        older = self.store.list_events(limit=2, before_sequence=3)
        self.assertEqual([event["sequence"] for event in older], [1, 2])
        by_account = self.store.list_events(cookie_id="acc-2")
        self.assertEqual([event["sequence"] for event in by_account], [2])
        by_chat = self.store.list_events(chat_id="chat-1")
        self.assertEqual([event["sequence"] for event in by_chat], [1])
        inbound = self.store.list_events(direction="inbound", limit=10)
        self.assertEqual(len(inbound), 4)

    def test_replay_returns_verified_suffix(self):
        for index in range(1, 5):
            self.store.append_inbound(_inbound_envelope(f"env-{index}"))
        suffix = self.store.replay(from_sequence=3)
        self.assertEqual([event["sequence"] for event in suffix], [3, 4])

    def test_replay_refuses_tampered_stream(self):
        self.store.append_inbound(_inbound_envelope("env-a"))
        self.store.append_inbound(_inbound_envelope("env-b"))
        path = self.store.storage_path
        records = json.loads(path.read_text(encoding="utf-8"))
        records[0]["message_text"] = "tampered"
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        reloaded = ChatEventStore(path)
        with self.assertRaises(ValueError):
            reloaded.replay()
        chain = reloaded.verify_chain()
        self.assertFalse(chain["ok"])

    def test_state_survives_reopen_without_duplicates(self):
        self.store.append_inbound(_inbound_envelope("env-a"))
        self.store.append_outbound(execution_job_id="job-1", cookie_id="acc-1")
        reopened = ChatEventStore(self.store.storage_path)
        result = reopened.append_inbound(_inbound_envelope("env-a"))
        self.assertEqual(result["status"], "already_recorded")
        self.assertEqual(len(reopened.list_events()), 2)
        self.assertTrue(reopened.verify_chain()["ok"])


class FakeReplyWorkflow:
    def __init__(self, reply="The item is available. I can ship tonight."):
        self.reply = reply

    def generate_auto_reply(self, **kwargs):
        return self.reply

    def handle_feishu_text(self, text):
        return {"type": "legacy_text", "text": text}


class ChatEventsRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_ledger = server.message_intake_ledger
        self.original_events = server.chat_event_store
        self.original_workflow = server.workflow
        self.tmp = TemporaryDirectory()
        server.work_order_manager = WorkOrderManager(Path(self.tmp.name) / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(
            Path(self.tmp.name) / "message_intake.json"
        )
        server.chat_event_store = ChatEventStore(Path(self.tmp.name) / "chat_events.json")
        server.workflow = FakeReplyWorkflow()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.message_intake_ledger = self.original_ledger
        self.server.chat_event_store = self.original_events
        self.server.workflow = self.original_workflow
        self.tmp.cleanup()

    def _post_inbound(self, message_id):
        return self.client.post(
            "/workflows/messages/inbound",
            json={
                "source": "xianyu_im",
                "message_id": message_id,
                "buyer_id": "buyer_123",
                "buyer_name": "Ada",
                "message": "Is this still available?",
                "chat_id": "chat_123",
                "item_id": "item_9",
                "cookie_id": "acc-1",
            },
        )

    def test_inbound_message_appends_one_chat_event_and_replay_stays_idempotent(self):
        response = self._post_inbound("msg-1")
        self.assertEqual(response.status_code, 200, response.text)
        feed = self.client.get("/chat-events", params={"cookie_id": "acc-1"})
        self.assertEqual(feed.status_code, 200)
        payload = feed.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["events"]), 1)
        event = payload["events"][0]
        self.assertEqual(event["direction"], "inbound")
        self.assertEqual(event["source_message_id"], "msg-1")
        self.assertEqual(event["buyer_id"], "buyer_123")
        self.assertTrue(event["intake_id"])

        replay = self._post_inbound("msg-1")
        self.assertEqual(replay.status_code, 200)
        after = self.client.get("/chat-events").json()
        self.assertEqual(len(after["events"]), 1)
        self.assertEqual(after["chain"]["event_count"], 1)
        self.assertTrue(after["chain"]["ok"])

    def test_metrics_reports_counts_and_chain(self):
        self._post_inbound("msg-1")
        metrics = self.client.get("/chat-events/metrics").json()
        self.assertEqual(metrics["event_count"], 1)
        self.assertEqual(metrics["by_direction"], {"inbound": 1})
        self.assertTrue(metrics["chain"]["ok"])


if __name__ == "__main__":
    unittest.main()
