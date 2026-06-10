import asyncio
import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class EcommerceChannelIntegrationTests(unittest.TestCase):
    def test_ecommerce_listing_draft_route_is_registered(self):
        from xianyu_agent.server import app

        paths = [route.path for route in app.routes if hasattr(route, "path")]

        self.assertIn("/integrations/ecommerce/listing-drafts", paths)

    def test_ecommerce_listing_draft_endpoint_creates_work_order_and_approval(self):
        from xianyu_agent import server
        from xianyu_agent.work_orders import WorkOrderManager

        original_manager = server.work_order_manager
        with TemporaryDirectory() as tmp:
            server.work_order_manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            try:
                req = server.EcommerceListingDraftRequest(
                    candidate_id="CAND-001",
                    title="USB desk fan",
                    description="Small quiet USB fan",
                    price_cny=19.9,
                    stock=3,
                    source_platform="1688",
                    supplier_name="Supplier A",
                    source_url="https://example.test/item",
                    image_url="https://example.test/item.jpg",
                    tags=["summer", "office"],
                )

                result = asyncio.run(server.create_ecommerce_listing_draft(req))
            finally:
                server.work_order_manager = original_manager

        self.assertTrue(result["ok"])
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["candidate_id"], "CAND-001")
        work_order = result["work_order"]
        self.assertEqual(work_order["kind"], "ecommerce_candidate")
        self.assertEqual(work_order["status"], "needs_approval")
        self.assertEqual(work_order["approvals"][0]["status"], "pending")
        self.assertEqual(work_order["approvals"][0]["action"], "publish_listing")
        self.assertEqual(work_order["context"]["source_project"], "ecommerce-openclaw-system")

    def test_ecommerce_listing_draft_endpoint_does_not_execute_channel_actions(self):
        from xianyu_agent.server import app

        handler = None
        for route in app.routes:
            if getattr(route, "path", "") == "/integrations/ecommerce/listing-drafts":
                handler = route.endpoint
                break

        self.assertIsNotNone(handler)
        source = inspect.getsource(handler)

        self.assertIn("ecommerce_candidate", source)
        self.assertIn("publish_listing", source)
        self.assertIn("approval_required", source)
        self.assertNotIn("publish_listing(", source)
        self.assertNotIn("execution_queue", source)
        self.assertNotIn("run_next", source)
        self.assertNotIn("action_executor", source)

    def test_ecommerce_listing_draft_uses_safe_snapshot_fields(self):
        from xianyu_agent.server import app

        handler = None
        for route in app.routes:
            if getattr(route, "path", "") == "/integrations/ecommerce/listing-drafts":
                handler = route.endpoint
                break

        self.assertIsNotNone(handler)
        source = inspect.getsource(handler)

        self.assertIn("candidate_id", source)
        self.assertIn("source_project", source)
        self.assertIn("draft_snapshot", source)
        self.assertNotIn("api_key", source.lower())
        self.assertNotIn("cookie", source.lower().replace("cookie_id", ""))


if __name__ == "__main__":
    unittest.main()
