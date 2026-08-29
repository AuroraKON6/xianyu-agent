from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class DiscoveryInboxTests(unittest.TestCase):
    def test_inbox_adds_lists_marks_and_sanitizes_raw_events(self):
        from xianyu_agent.discovery_inbox import DiscoveryInboxStore

        store = DiscoveryInboxStore(Path(tempfile.mkdtemp()) / "discovery_inbox.json")
        event = store.add_event(
            source="manual",
            raw_payload={"title": "stainless work table", "buyer_id": "buyer-secret"},
            title="stainless work table",
            summary="Commercial kitchen lead",
            category_hint="commercial kitchen",
            supplier_hint="pdd",
            price_hint=120.0,
        )

        self.assertEqual(event["status"], "unread")
        self.assertEqual(event["title"], "stainless work table")
        self.assertNotIn("buyer-secret", json.dumps(event, ensure_ascii=False))

        listed = store.list_events(status="unread")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["event_id"], event["event_id"])

        marked = store.mark_read(event["event_id"])
        self.assertEqual(marked["status"], "read")

        rejected = store.reject_event(event["event_id"], reason="not enough margin")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["rejection_reason"], "not enough margin")

    def test_inbox_promotes_event_to_resale_candidate_and_persists_link(self):
        from xianyu_agent.discovery_inbox import DiscoveryInboxStore
        from xianyu_agent.resale import ResaleManager

        data_dir = Path(tempfile.mkdtemp())
        store = DiscoveryInboxStore(data_dir / "discovery_inbox.json")
        resale = ResaleManager(data_dir / "resale")
        event = store.add_event(
            source="pdd_scan",
            raw_payload={"url": "https://pdd.example/item/1"},
            title="commercial stainless prep table",
            summary="Cold-category kitchen equipment lead",
            category_hint="commercial kitchen",
            supplier_hint="pdd",
            price_hint=180.0,
        )

        result = store.promote_to_candidate(
            event["event_id"],
            resale_manager=resale,
            overrides={
                "supplier_price": 180.0,
                "supplier_shipping": 40.0,
                "target_sale_price": 420.0,
            },
        )

        self.assertTrue(result["ok"])
        candidate = result["candidate"]
        self.assertTrue(candidate["candidate_id"].startswith("rc_"))
        self.assertEqual(candidate["title"], "commercial stainless prep table")
        self.assertEqual(candidate["source_platform"], "pdd")
        updated = store.get_event(event["event_id"])
        self.assertEqual(updated["status"], "promoted")
        self.assertEqual(updated["promoted_candidate_id"], candidate["candidate_id"])

    def test_discovery_inbox_routes_cover_add_list_mark_read_and_promote(self):
        from xianyu_agent import server
        from xianyu_agent.discovery_inbox import DiscoveryInboxStore
        from xianyu_agent.resale import ResaleManager

        data_dir = Path(tempfile.mkdtemp())
        original_inbox = server.discovery_inbox_store
        original_resale = server.resale_manager
        server.discovery_inbox_store = DiscoveryInboxStore(data_dir / "discovery_inbox.json")
        server.resale_manager = ResaleManager(data_dir / "resale")
        try:
            client = TestClient(server.app)
            create_response = client.post(
                "/discovery/inbox",
                json={
                    "source": "manual",
                    "raw_payload": {"title": "bench grinder", "secret": "do-not-return"},
                    "title": "bench grinder",
                    "summary": "Workshop equipment lead",
                    "category_hint": "machinery",
                    "supplier_hint": "pdd",
                    "price_hint": 90,
                },
            )
            self.assertEqual(create_response.status_code, 200, create_response.text)
            event = create_response.json()["event"]
            self.assertEqual(event["status"], "unread")
            self.assertNotIn("do-not-return", create_response.text)

            list_response = client.get("/discovery/inbox?status=unread")
            self.assertEqual(list_response.status_code, 200, list_response.text)
            self.assertEqual(list_response.json()["events"][0]["event_id"], event["event_id"])

            mark_response = client.post(f"/discovery/inbox/{event['event_id']}/mark-read")
            self.assertEqual(mark_response.status_code, 200, mark_response.text)
            self.assertEqual(mark_response.json()["event"]["status"], "read")

            promote_response = client.post(
                f"/discovery/inbox/{event['event_id']}/promote-candidate",
                json={"supplier_price": 90, "supplier_shipping": 15, "target_sale_price": 220},
            )
            self.assertEqual(promote_response.status_code, 200, promote_response.text)
            payload = promote_response.json()
            self.assertTrue(payload["candidate"]["candidate_id"].startswith("rc_"))
            self.assertEqual(payload["event"]["status"], "promoted")
        finally:
            server.discovery_inbox_store = original_inbox
            server.resale_manager = original_resale


if __name__ == "__main__":
    unittest.main()
