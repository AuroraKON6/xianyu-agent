from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class OrderRuntimeStoreTests(unittest.TestCase):
    def test_order_runtime_records_status_history_and_next_operator_action(self):
        from xianyu_agent.order_runtime import OrderRuntimeStore

        with tempfile.TemporaryDirectory() as tmp:
            store = OrderRuntimeStore(Path(tmp) / "order_runtime.json")

            created = store.upsert_order(
                order_id="order_1",
                cookie_id="seller_a",
                status="paid",
                item_id="item_1",
                amount=188.0,
                source="adapter_sync",
            )

            self.assertEqual(created["status"], "paid")
            self.assertEqual(created["cookie_id"], "seller_a")
            self.assertEqual(created["status_history"][0]["new_status"], "paid")
            self.assertEqual(created["operator_next_action"], "verify_order_and_prepare_fulfillment")
            self.assertEqual(created["pending_blocker"], "")

            updated = store.update_status(
                "order_1",
                "pending_shipment",
                context="buyer paid, waiting for supplier order",
                operator_action="place_supplier_order",
                blocker="supplier_quote_missing",
            )

            self.assertEqual(updated["status"], "pending_shipment")
            self.assertEqual(updated["pending_blocker"], "supplier_quote_missing")
            self.assertEqual(updated["last_operator_action"], "place_supplier_order")
            self.assertEqual(updated["status_history"][-1]["old_status"], "paid")
            self.assertEqual(updated["status_history"][-1]["new_status"], "pending_shipment")

            reloaded = OrderRuntimeStore(Path(tmp) / "order_runtime.json")
            restored = reloaded.get_order("order_1")
            self.assertEqual(restored["status"], "pending_shipment")
            self.assertEqual(len(restored["status_history"]), 2)

    def test_fulfillment_lock_prevents_double_processing(self):
        from xianyu_agent.order_runtime import OrderRuntimeStore

        with tempfile.TemporaryDirectory() as tmp:
            store = OrderRuntimeStore(Path(tmp) / "order_runtime.json")
            store.upsert_order(order_id="order_2", cookie_id="seller_a", status="paid")

            locked = store.acquire_fulfillment_lock("order_2", owner="agent_a", reason="placing supplier order")
            self.assertTrue(locked["fulfillment_lock"]["locked"])
            self.assertEqual(locked["fulfillment_lock"]["owner"], "agent_a")

            with self.assertRaises(ValueError):
                store.acquire_fulfillment_lock("order_2", owner="agent_b", reason="duplicate attempt")

            released = store.release_fulfillment_lock("order_2", owner="agent_a")
            self.assertFalse(released["fulfillment_lock"]["locked"])
            self.assertEqual(released["fulfillment_lock"]["owner"], "")

    def test_sync_account_snapshot_updates_order_runtime_store(self):
        from xianyu_agent.order_runtime import OrderRuntimeStore
        from xianyu_agent.sync_persistence import sync_account_snapshot

        class FakeDB:
            def __init__(self):
                self.saved_orders = []
                self.sync_state = {}

            def save_order(self, order_id, cookie_id, **kwargs):
                self.saved_orders.append({"order_id": order_id, "cookie_id": cookie_id, **kwargs})

            def save_item(self, item_id, cookie_id, **kwargs):
                pass

            def save_account_sync_state(self, cookie_id, resource, **kwargs):
                self.sync_state[resource] = kwargs

            def get_account_sync_state(self, cookie_id):
                return self.sync_state

        class FakeAdapter:
            name = "fake"

            def sync_orders(self, cookie_id="default", status=""):
                return {
                    "ok": True,
                    "adapter": "fake",
                    "orders": [
                        {
                            "order_id": "order_sync_1",
                            "item_id": "item_sync_1",
                            "status": "paid",
                            "amount": 288,
                        }
                    ],
                }

            def sync_my_listings(self, cookie_id="default"):
                return {"ok": True, "adapter": "fake", "items": []}

        with tempfile.TemporaryDirectory() as tmp:
            runtime = OrderRuntimeStore(Path(tmp) / "order_runtime.json")
            result = sync_account_snapshot(
                db=FakeDB(),
                adapter=FakeAdapter(),
                cookie_id="seller_sync",
                order_runtime_store=runtime,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["orders"]["runtime_updated"], 1)
            order = runtime.get_order("order_sync_1")
            self.assertEqual(order["status"], "paid")
            self.assertEqual(order["operator_next_action"], "verify_order_and_prepare_fulfillment")

    def test_summary_keeps_synthetic_orders_for_audit_but_excludes_them_from_operations(self):
        from xianyu_agent.order_runtime import OrderRuntimeStore

        with tempfile.TemporaryDirectory() as tmp:
            store = OrderRuntimeStore(Path(tmp) / "order_runtime.json")
            store.upsert_order(
                order_id="real_paid",
                cookie_id="seller_a",
                item_id="item_real",
                status="paid",
                source="browser_sync",
            )
            store.upsert_order(
                order_id="fake_paid",
                cookie_id="seller_a",
                item_id="item_fake",
                status="paid",
                source="fake_sync_sync",
            )

            summary = store.summary()
            self.assertEqual(summary["total_orders"], 2)
            self.assertEqual(summary["operational_orders"], 1)
            self.assertEqual(summary["synthetic_orders"], 1)
            self.assertEqual(summary["needs_fulfillment"], 1)
            self.assertEqual(summary["by_status"], {"paid": 1})
            self.assertEqual(summary["observed_by_status"], {"paid": 2})
            self.assertEqual([item["order_id"] for item in summary["next_actions"]], ["real_paid"])


class OrderRuntimeRouteTests(unittest.TestCase):
    def test_order_runtime_routes_are_registered(self):
        from xianyu_agent.server import app

        route_paths = [route.path for route in app.routes if hasattr(route, "path")]
        self.assertIn("/orders/runtime", route_paths)
        self.assertIn("/orders/runtime/{order_id}/status", route_paths)
        self.assertIn("/orders/runtime/{order_id}/fulfillment-lock", route_paths)


if __name__ == "__main__":
    unittest.main()
