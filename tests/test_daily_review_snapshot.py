from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from xianyu_agent.opc_dashboard import OPCDashboard


class DailyReviewSnapshotTests(unittest.TestCase):
    def test_daily_review_includes_account_queues_and_sync_freshness(self):
        dashboard = OPCDashboard()
        account_queues = {
            "accounts": [
                {
                    "cookie_id": "seller_daily",
                    "remark": "daily account",
                    "queues": {"pending_approvals": 2, "items": 3, "orders_total": 1},
                    "sync_freshness": {
                        "orders": {"fresh": True, "last_count": 1, "adapter": "fake"},
                        "listings": {"fresh": False, "last_count": 3, "adapter": "fake"},
                    },
                }
            ],
            "unassigned": {"queues": {"pending_approvals": 1}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            result = dashboard.generate_daily_review(Path(tmpdir), account_queues=account_queues)

            self.assertTrue(result["ok"])
            self.assertIn("daily_review_", Path(result["file"]).name)
            content = Path(result["file"]).read_text(encoding="utf-8")
            self.assertIn("OPC Daily Review", content)
            self.assertIn("Account Queues", content)
            self.assertIn("seller_daily", content)
            self.assertIn("sync_freshness", content)
            self.assertIn("listings", content)
            self.assertIn("stale", content)

    def test_daily_review_route_writes_combined_snapshot(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DBManager(str(Path(tmpdir) / "daily.db"))
            db.save_cookie("seller_route", "raw-cookie-secret", "route account")
            db.save_item("item_route", "seller_route", title="table")
            db.save_account_sync_state("seller_route", "orders", adapter="fake", last_count=1)

            original_db = server.db_manager
            original_settings = server.settings
            server.db_manager = db
            server.settings = replace(server.settings, data_dir=Path(tmpdir))
            try:
                response = TestClient(server.app).post("/dashboard/daily-review")
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                content = Path(data["file"]).read_text(encoding="utf-8")
            finally:
                server.db_manager = original_db
                server.settings = original_settings
                db.close()

        self.assertTrue(data["ok"])
        self.assertIn("daily_review_", Path(data["file"]).name)
        self.assertIn("seller_route", content)
        self.assertIn("sync_freshness", content)
        self.assertNotIn("raw-cookie-secret", content)


if __name__ == "__main__":
    unittest.main()
