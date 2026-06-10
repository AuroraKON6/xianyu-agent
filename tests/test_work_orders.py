import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class WorkOrderManagerTests(unittest.TestCase):
    def test_work_order_persists_task_plan_and_approval(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")

            order = manager.create_work_order(
                kind="listing",
                title="协作上架蓝牙耳机",
                owner="listing",
                context={"price": 98, "cost": 70},
            )
            plan = manager.create_action_plan(
                order["work_order_id"],
                discussion={"summary": "市场建议强调成色，利润可以接受。"},
                proposed_actions=[
                    {"agent": "discovery", "action": "analyze_market", "description": "确认市场价格"},
                    {"agent": "analytics", "action": "calculate_profit", "description": "核算利润"},
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品"},
                ],
            )

            self.assertEqual(order["status"], "open")
            self.assertEqual(plan["work_order_id"], order["work_order_id"])
            self.assertEqual(plan["actions"][2]["requires_approval"], True)
            self.assertEqual(plan["status"], "needs_approval")

            latest = manager.get_work_order(order["work_order_id"])
            self.assertEqual(latest["status"], "needs_approval")
            self.assertEqual(len(latest["approvals"]), 1)
            self.assertEqual(latest["approvals"][0]["status"], "pending")

            reloaded = WorkOrderManager(Path(tmp) / "work_orders.json")
            restored = reloaded.get_work_order(order["work_order_id"])
            self.assertEqual(restored["action_plan"]["summary"], plan["summary"])
            self.assertEqual(restored["approvals"][0]["action"], "publish_listing")

    def test_approval_decision_updates_work_order_status(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = manager.create_work_order("listing", "协作上架", "listing")
            manager.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品"},
                ],
            )
            approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]

            approval = manager.decide_approval(approval_id, decision="approved", note="人工确认可以发布")

            self.assertEqual(approval["status"], "approved")
            updated = manager.get_work_order(order["work_order_id"])
            self.assertEqual(updated["status"], "approved")

    def test_work_order_routes_are_registered(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}

        self.assertIn(("/work-orders", ("GET",)), routes)
        self.assertIn(("/work-orders", ("POST",)), routes)
        self.assertIn(("/work-orders/{work_order_id}", ("GET",)), routes)
        self.assertIn(("/work-orders/{work_order_id}/plan", ("POST",)), routes)
        self.assertIn(("/approvals/{approval_id}/decision", ("POST",)), routes)


    # --- Execution tests ---

    def _make_approved_work_order(self, tmp: str, draft_id: str = "test_draft_001"):
        """Helper: create a work order with an approved publish_listing approval."""
        from xianyu_agent.work_orders import WorkOrderManager

        manager = WorkOrderManager(Path(tmp) / "work_orders.json")
        order = manager.create_work_order("listing", "协作上架", "listing", context={"draft_id": draft_id})
        manager.create_action_plan(
            order["work_order_id"],
            proposed_actions=[
                {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": draft_id}},
            ],
        )
        approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
        manager.decide_approval(approval_id, decision="approved", note="确认发布")
        return manager, order["work_order_id"], approval_id

    def test_approved_approval_can_be_executed(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager, wo_id, approval_id = self._make_approved_work_order(tmp)

            def fake_publish(draft_data):
                return {"ok": True, "external_id": "ext_123", "adapter": "fake"}

            result = manager.execute_approval(approval_id, publish_fn=fake_publish)

            self.assertTrue(result["ok"])
            self.assertEqual(result["execution_status"], "completed")
            self.assertIn("external_id", result.get("execution_result", {}))

            approval = None
            for a in manager.get_work_order(wo_id)["approvals"]:
                if a["approval_id"] == approval_id:
                    approval = a
            self.assertIsNotNone(approval)
            self.assertEqual(approval["execution_status"], "completed")
            self.assertEqual(approval["execution_result"]["external_id"], "ext_123")

            wo = manager.get_work_order(wo_id)
            self.assertEqual(wo["status"], "completed")

    def test_pending_approval_cannot_be_executed(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = manager.create_work_order("listing", "协作上架", "listing")
            manager.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "x"}},
                ],
            )
            approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]

            with self.assertRaises(ValueError):
                manager.execute_approval(approval_id, publish_fn=lambda d: {"ok": True})

    def test_rejected_approval_cannot_be_executed(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = manager.create_work_order("listing", "协作上架", "listing")
            manager.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "x"}},
                ],
            )
            approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            manager.decide_approval(approval_id, decision="rejected", note="不要发布")

            with self.assertRaises(ValueError):
                manager.execute_approval(approval_id, publish_fn=lambda d: {"ok": True})

    def test_missing_draft_id_returns_failed_and_records_event(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager, wo_id, approval_id = self._make_approved_work_order(tmp, draft_id="nonexistent_draft")

            def publish_fn(draft_data):
                raise FileNotFoundError("Draft not found")

            result = manager.execute_approval(approval_id, publish_fn=publish_fn)

            self.assertFalse(result["ok"])
            self.assertEqual(result["execution_status"], "failed")
            self.assertIn("Draft not found", result.get("error", ""))

            wo = manager.get_work_order(wo_id)
            self.assertEqual(wo["status"], "failed")

            event_types = [e["type"] for e in wo["events"]]
            self.assertIn("execution_failed", event_types)

    def test_publish_success_updates_work_order_to_completed(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager, wo_id, approval_id = self._make_approved_work_order(tmp)

            def fake_publish(draft_data):
                return {"ok": True, "external_id": "item_abc", "adapter": "browser"}

            manager.execute_approval(approval_id, publish_fn=fake_publish)

            wo = manager.get_work_order(wo_id)
            self.assertEqual(wo["status"], "completed")

            event_types = [e["type"] for e in wo["events"]]
            self.assertIn("execution_completed", event_types)

    def test_execute_approval_route_is_registered(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}
        self.assertIn(("/approvals/{approval_id}/execute", ("POST",)), routes)

    # --- Idempotency and snapshot tests ---

    def test_completed_approval_cannot_be_executed_again(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager, wo_id, approval_id = self._make_approved_work_order(tmp)
            manager.execute_approval(approval_id, publish_fn=lambda d: {"ok": True, "external_id": "x"})

            with self.assertRaises(ValueError) as ctx:
                manager.execute_approval(approval_id, publish_fn=lambda d: {"ok": True, "external_id": "y"})
            self.assertIn("completed", str(ctx.exception))

    def test_running_approval_cannot_be_executed_again(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager, wo_id, approval_id = self._make_approved_work_order(tmp)

            call_count = {"n": 0}
            def slow_publish(d):
                call_count["n"] += 1
                if call_count["n"] > 1:
                    raise RuntimeError("publish_fn called twice!")
                return {"ok": True, "external_id": "ext"}

            # Simulate a stuck running state by manually setting it
            for wo in manager._work_orders.values():
                for a in wo.get("approvals", []):
                    if a.get("approval_id") == approval_id:
                        a["execution_status"] = "running"

            with self.assertRaises(ValueError) as ctx:
                manager.execute_approval(approval_id, publish_fn=slow_publish)
            self.assertIn("running", str(ctx.exception))

    def test_execute_writes_running_event_before_execution(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager, wo_id, approval_id = self._make_approved_work_order(tmp)

            events_seen = []
            original_publish = None

            def spy_publish(d):
                # Capture events at the moment publish_fn is called
                wo = manager._work_orders[wo_id]
                events_seen.extend([e["type"] for e in wo["events"]])
                return {"ok": True, "external_id": "ext"}

            manager.execute_approval(approval_id, publish_fn=spy_publish)

            self.assertIn("execution_started", events_seen)

    def test_approval_saves_draft_snapshot(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            manager = WorkOrderManager(Path(tmp) / "work_orders.json")
            order = manager.create_work_order("listing", "协作上架", "listing")
            draft_snapshot = {
                "title": "蓝牙耳机",
                "price": 98.0,
                "stock": 3,
                "description": "成色很新",
                "delivery_type": "physical",
            }
            manager.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品",
                     "params": {"draft_id": "d1"}, "draft_snapshot": draft_snapshot},
                ],
            )

            wo = manager.get_work_order(order["work_order_id"])
            approval = wo["approvals"][0]
            snapshot = approval.get("draft_snapshot", {})
            self.assertEqual(snapshot["title"], "蓝牙耳机")
            self.assertEqual(snapshot["price"], 98.0)
            self.assertEqual(snapshot["stock"], 3)

            # Verify persistence
            reloaded = WorkOrderManager(Path(tmp) / "work_orders.json")
            restored = reloaded.get_work_order(order["work_order_id"])
            self.assertEqual(restored["approvals"][0]["draft_snapshot"]["title"], "蓝牙耳机")

    def test_approval_decision_route_is_registered(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}
        self.assertIn(("/approvals/{approval_id}/decision", ("POST",)), routes)


if __name__ == "__main__":
    unittest.main()
