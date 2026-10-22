"""R13 acceptance tests: unified order/account attribution.

* composite identity — the same external order id on two accounts
  coexists in the SQLite store and in the runtime store, and a scoped
  status update never moves the other account's row;
* out-of-order events never regress the state machine and never crash
  the sync (the anomaly is recorded, the current status stands);
* a failed resource sync does NOT refresh its last-success timestamp;
* a partial sync (one ok, one failed) is explicitly marked.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path


class CompositeOrderIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r13-db-"))
        from xianyu_agent.db_manager import DBManager

        self.db = DBManager(str(self.tmp / "orders.db"))
        self.db.init_db()
        self.db.save_cookie("acct-a", "cookie-a")
        self.db.save_cookie("acct-b", "cookie-b")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_order_id_two_accounts_coexist(self):
        self.db.save_order("ORD-SHARED", "acct-a", status="paid", amount=300)
        self.db.save_order("ORD-SHARED", "acct-b", status="shipped", amount=250)
        a = self.db.get_order("ORD-SHARED", cookie_id="acct-a")
        b = self.db.get_order("ORD-SHARED", cookie_id="acct-b")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b, "cross-account same order id must coexist")
        self.assertEqual(a["status"], "paid")
        self.assertEqual(b["status"], "shipped")

    def test_scoped_status_update_does_not_move_other_account(self):
        self.db.save_order("ORD-X", "acct-a", status="paid")
        self.db.save_order("ORD-X", "acct-b", status="paid")
        self.db.update_order_status("ORD-X", "refund_requested", cookie_id="acct-b")
        self.assertEqual(self.db.get_order("ORD-X", cookie_id="acct-a")["status"], "paid")
        self.assertEqual(self.db.get_order("ORD-X", cookie_id="acct-b")["status"], "refund_requested")

    def test_legacy_single_pk_schema_migrates(self):
        """An orders table created under the old single-PK schema is
        rebuilt with the composite key, keeping the newest row per
        (account, order)."""
        import sqlite3

        self.db.close()
        legacy_path = self.tmp / "legacy.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute("""
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                cookie_id TEXT NOT NULL,
                item_id TEXT DEFAULT '',
                buyer_id TEXT DEFAULT '',
                status TEXT DEFAULT 'processing',
                amount REAL DEFAULT 0,
                delivery_payload TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO orders (order_id, cookie_id, status, updated_at)"
            " VALUES ('ORD-L', 'acct-a', 'paid', '2026-10-01 10:00:00')"
        )
        conn.commit()
        conn.close()

        from xianyu_agent.db_manager import DBManager

        db = DBManager(str(legacy_path))
        db.init_db()
        db.save_cookie("acct-a", "c-a")
        db.save_order("ORD-L", "acct-b", status="shipped")  # coexist after migration
        a = db.get_order("ORD-L", cookie_id="acct-a")
        b = db.get_order("ORD-L", cookie_id="acct-b")
        db.close()
        self.assertIsNotNone(a, "migrated legacy row survives")
        self.assertEqual(a["status"], "paid")
        self.assertIsNotNone(b)
        self.assertEqual(b["status"], "shipped")


class OutOfOrderEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r13-ooo-"))
        from xianyu_agent.order_runtime import OrderRuntimeStore

        self.store = OrderRuntimeStore(self.tmp / "runtime.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_out_of_order_event_does_not_regress_or_crash(self):
        self.store.upsert_order(order_id="ORD-1", cookie_id="acct-a", status="shipped",
                                item_id="SKU", amount=300, source="sync")
        # a stale sync batch replays the EARLIER 'paid' stage
        updated = self.store.upsert_order(order_id="ORD-1", cookie_id="acct-a",
                                          status="paid", item_id="SKU",
                                          amount=300, source="sync")
        self.assertEqual(updated["status"], "shipped",
                         "out-of-order event must not regress the state machine")
        history = updated.get("status_history") or []
        self.assertTrue(any(e.get("change") == "ignored_out_of_order" for e in history),
                        "the anomaly must be recorded: %r" % history)

    def test_forward_transition_still_applies(self):
        self.store.upsert_order(order_id="ORD-2", cookie_id="acct-a", status="paid")
        updated = self.store.upsert_order(order_id="ORD-2", cookie_id="acct-a", status="shipped")
        self.assertEqual(updated["status"], "shipped")

    def test_deliberate_invalid_operator_transition_still_raises(self):
        from xianyu_agent.order_runtime import OrderRuntimeStore

        store = OrderRuntimeStore(self.tmp / "op.json")
        store.upsert_order(order_id="ORD-3", cookie_id="acct-a", status="shipped")
        with self.assertRaises(ValueError):
            store.update_status("ORD-3", "paid")  # operator actions keep strict validation


class PartialSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r13-sync-"))
        from xianyu_agent.db_manager import DBManager

        self.db = DBManager(str(self.tmp / "sync.db"))
        self.db.init_db()
        self.db.save_cookie("acct-a", "cookie-a")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sync(self, orders_ok, listings_ok):
        from xianyu_agent.sync_persistence import sync_account_snapshot

        class Adapter:
            name = "stub"

            def sync_orders(self, cookie_id="", status=""):
                if orders_ok:
                    return {"ok": True, "orders": [
                        {"order_id": "ORD-S", "status": "paid", "amount": 300, "item_id": "SKU"}
                    ]}
                return {"ok": False, "error": "sync down"}

            def sync_my_listings(self, cookie_id=""):
                if listings_ok:
                    return {"ok": True, "items": [
                        {"item_id": "ITM-1", "title": "t", "price": 9}
                    ]}
                return {"ok": False, "error": "listings down"}

        from xianyu_agent.order_runtime import OrderRuntimeStore

        runtime = OrderRuntimeStore(self.tmp / "runtime.json")
        return sync_account_snapshot(
            db=self.db, adapter=Adapter(), cookie_id="acct-a",
            order_runtime_store=runtime,
        )

    def test_failed_resource_does_not_refresh_success_timestamp(self):
        first = self._sync(orders_ok=True, listings_ok=True)
        self.assertTrue(first["ok"])
        self.assertFalse(first["partial"])
        before = self.db.get_account_sync_state("acct-a")["listings"]["last_synced_epoch"]

        second = self._sync(orders_ok=True, listings_ok=False)
        self.assertFalse(second["ok"])
        self.assertTrue(second["partial"], "partial sync must be explicit")
        after = self.db.get_account_sync_state("acct-a")["listings"]["last_synced_epoch"]
        self.assertEqual(before, after,
                         "a failed listings sync must not refresh the last-success timestamp")
        # the healthy resource DID refresh
        orders_state = self.db.get_account_sync_state("acct-a")["orders"]
        self.assertGreater(orders_state["last_synced_epoch"], 0)

    def test_both_ok_is_not_partial(self):
        result = self._sync(orders_ok=True, listings_ok=True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["partial"])


if __name__ == "__main__":
    unittest.main()
