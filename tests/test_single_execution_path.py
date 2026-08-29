from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class SingleExecutionPathTests(unittest.TestCase):
    def _stores(self):
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.work_orders import WorkOrderManager

        temp_dir = Path(tempfile.mkdtemp())
        return (
            DBManager(str(temp_dir / "accounts.db")),
            WorkOrderManager(temp_dir / "work_orders.json"),
        )

    def test_legacy_listing_route_creates_approval_without_publishing(self):
        from xianyu_agent import server

        db, work_orders = self._stores()
        draft = {
            "id": "draft_legacy",
            "title": "Commercial work table",
            "price": 699,
            "stock": 1,
            "description": "Draft only",
            "selected_images": ["owned.jpg"],
        }
        with patch.object(server, "db_manager", db), patch.object(
            server, "work_order_manager", work_orders
        ), patch.object(server.workflow, "create_listing", return_value={"draft": draft, "publish_result": None}) as create:
            response = TestClient(server.app).post(
                "/listings",
                json={"text": "Commercial work table", "publish": True},
            )

        self.assertEqual(response.status_code, 200, response.text)
        create.assert_called_once_with(
            "Commercial work table",
            source="api",
            product_images=[],
            publish=False,
        )
        data = response.json()
        self.assertTrue(data["approval_required"])
        self.assertFalse(data["executed"])
        self.assertEqual(data["work_order"]["approvals"][0]["action"], "publish_listing")

    def test_legacy_paid_order_route_creates_fulfillment_approval(self):
        from xianyu_agent import server

        db, work_orders = self._stores()
        with patch.object(server, "db_manager", db), patch.object(server, "work_order_manager", work_orders):
            response = TestClient(server.app).post(
                "/orders/paid",
                json={"order_id": "order_1", "delivery_type": "physical", "delivery_payload": "tracking pending"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["approval_required"])
        self.assertEqual(data["work_order"]["approvals"][0]["action"], "fulfill_order")

    def test_browser_write_compatibility_routes_create_approvals(self):
        from xianyu_agent import server

        db, work_orders = self._stores()
        with patch.object(server, "db_manager", db), patch.object(server, "work_order_manager", work_orders):
            client = TestClient(server.app)
            message = client.post(
                "/browser/send-message",
                json={"buyer_id": "buyer_1", "message": "The item is available."},
            )
            deletion = client.delete("/browser/item/item_1")

        self.assertEqual(message.status_code, 200, message.text)
        self.assertEqual(deletion.status_code, 200, deletion.text)
        self.assertEqual(message.json()["work_order"]["approvals"][0]["action"], "send_message")
        self.assertEqual(deletion.json()["work_order"]["approvals"][0]["action"], "delete_item")

    def test_server_has_no_direct_platform_mutation_calls(self):
        from xianyu_agent import server

        source = inspect.getsource(server)
        for forbidden in (
            "adapter.publish_listing(",
            "adapter.send_message(",
            "adapter.delete_item(",
            "workflow.fulfill_order(",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
