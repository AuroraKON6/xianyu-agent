import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


def _create_reply_approval(manager, metadata, *, message="Approved reply", action="send_message"):
    order = manager.create_work_order(
        kind="customer_message",
        title="Reply proposal",
        owner="customer_service",
        context={
            "buyer_id": "buyer_1",
            "cookie_id": "seller_1",
            "chat_id": "chat_1",
            "item_id": "item_1",
            **metadata,
        },
    )
    manager.create_action_plan(
        order["work_order_id"],
        proposed_actions=[{
            "agent": "customer_service",
            "action": action,
            "description": "Send approved reply",
            "params": {
                "buyer_id": "buyer_1",
                "message": message,
                "cookie_id": "seller_1",
                "chat_id": "chat_1",
                "item_id": "item_1",
                **metadata,
            },
            "draft_snapshot": {
                "buyer_id": "buyer_1",
                "cookie_id": "seller_1",
                "chat_id": "chat_1",
                "item_id": "item_1",
                "reply_summary": message,
                "risk_flags": [],
                **metadata,
            },
        }],
    )
    stored = manager.get_work_order(order["work_order_id"])
    return stored, stored["approvals"][0]


class ReplyMetadataTests(unittest.TestCase):
    def test_inbound_metadata_is_deterministic_and_bound_to_content(self):
        from xianyu_agent.reply_outbox import build_reply_metadata

        base = build_reply_metadata(
            action="send_message",
            cookie_id="seller_1",
            buyer_id="buyer_1",
            message="Available today",
            chat_id="chat_1",
            item_id="item_1",
            source="xianyu_im",
            source_event_key="event_1",
            source_message_id="message_1",
            reply_revision=1,
        )
        replay = build_reply_metadata(
            action="send_message",
            cookie_id="seller_1",
            buyer_id="buyer_1",
            message="Available today",
            chat_id="chat_1",
            item_id="item_1",
            source="xianyu_im",
            source_event_key="event_1",
            source_message_id="message_1",
            reply_revision=1,
        )
        changed = build_reply_metadata(
            action="send_message",
            cookie_id="seller_1",
            buyer_id="buyer_1",
            message="Available tomorrow",
            chat_id="chat_1",
            item_id="item_1",
            source="xianyu_im",
            source_event_key="event_1",
            source_message_id="message_1",
            reply_revision=1,
        )

        self.assertEqual(base, replay)
        self.assertNotEqual(base["reply_idempotency_key"], changed["reply_idempotency_key"])
        self.assertNotEqual(base["reply_action_digest"], changed["reply_action_digest"])


class ReplyOutboxLifecycleTests(unittest.TestCase):
    def _metadata(self, message="Approved reply", key=""):
        from xianyu_agent.reply_outbox import build_reply_metadata

        return build_reply_metadata(
            action="send_message",
            cookie_id="seller_1",
            buyer_id="buyer_1",
            message=message,
            chat_id="chat_1",
            item_id="item_1",
            source="xianyu_im",
            source_event_key="event_1",
            source_message_id="message_1",
            idempotency_key=key,
        )

    def test_projection_tracks_pending_ready_queued_and_sent_with_sanitized_receipt(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.reply_outbox import list_reply_outbox
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = WorkOrderManager(root / "work_orders.json")
            queue = ExecutionJobQueue(root / "execution_jobs.json")
            metadata = self._metadata()
            _, approval = _create_reply_approval(manager, metadata)

            items = list_reply_outbox(work_order_manager=manager, execution_queue=queue)
            self.assertEqual(items[0]["status"], "pending_approval")

            manager.decide_approval(approval["approval_id"], "approved", "operator reviewed")
            items = list_reply_outbox(work_order_manager=manager, execution_queue=queue)
            self.assertEqual(items[0]["status"], "ready_to_enqueue")

            job = queue.create_job(approval["approval_id"], manager)
            items = list_reply_outbox(work_order_manager=manager, execution_queue=queue)
            self.assertEqual(items[0]["status"], "queued")

            class FakeAdapter:
                async def send_message(self, buyer_id, message, cookie_id="default"):
                    return {
                        "ok": True,
                        "message_id": "external_message_1",
                        "adapter": "fake-browser",
                        "page_url": "https://example.invalid/chat/1",
                        "message": "adapter raw echo must stay hidden",
                    }

            class FakeWorkflow:
                adapter = FakeAdapter()

            result = queue.run_next(
                ActionExecutor(),
                manager,
                {"workflow": FakeWorkflow()},
                worker_id="reply_worker",
            )
            self.assertEqual(result[0]["status"], "completed")
            item = list_reply_outbox(work_order_manager=manager, execution_queue=queue)[0]
            self.assertEqual(item["status"], "sent")
            self.assertEqual(item["execution_job_id"], job["job_id"])
            self.assertEqual(item["receipt"]["external_message_id"], "external_message_1")
            self.assertNotIn("message", item["receipt"])
            self.assertNotIn("adapter raw echo must stay hidden", json.dumps(item["receipt"]))

    def test_uncertain_send_requires_verification_and_cannot_be_retried(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.reply_outbox import list_reply_outbox
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = WorkOrderManager(root / "work_orders.json")
            queue = ExecutionJobQueue(root / "execution_jobs.json")
            _, approval = _create_reply_approval(manager, self._metadata())
            manager.decide_approval(approval["approval_id"], "approved", "operator reviewed")
            job = queue.create_job(approval["approval_id"], manager)

            class UncertainAdapter:
                async def send_message(self, buyer_id, message, cookie_id="default"):
                    return {
                        "ok": False,
                        "error": "connection lost after Enter",
                        "outcome_uncertain": True,
                        "verification_required": True,
                        "verification_reason": "message_send_adapter_exception",
                    }

            class FakeWorkflow:
                adapter = UncertainAdapter()

            result = queue.run_next(
                ActionExecutor(),
                manager,
                {"workflow": FakeWorkflow()},
                worker_id="reply_worker",
            )
            self.assertEqual(result[0]["status"], "verification_required")
            item = list_reply_outbox(work_order_manager=manager, execution_queue=queue)[0]
            self.assertEqual(item["status"], "verification_required")
            self.assertEqual(item["verification"]["reason"], "message_send_adapter_exception")
            with self.assertRaises(ValueError):
                queue.retry_job(job["job_id"])

    def test_completed_idempotency_key_cannot_be_enqueued_from_another_approval(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = WorkOrderManager(root / "work_orders.json")
            queue = ExecutionJobQueue(root / "execution_jobs.json")
            metadata = self._metadata(key="reply:test:one-send")
            _, first = _create_reply_approval(manager, metadata)
            manager.decide_approval(first["approval_id"], "approved", "operator reviewed")
            queue.create_job(first["approval_id"], manager)

            executor = ActionExecutor()
            executor.register_handler("send_message", lambda approval, context: {"ok": True})
            queue.run_next(executor, manager, worker_id="reply_worker")

            _, second = _create_reply_approval(manager, metadata)
            manager.decide_approval(second["approval_id"], "approved", "operator reviewed")
            with self.assertRaisesRegex(ValueError, "idempotency key"):
                queue.create_job(second["approval_id"], manager)


class ReplyOutboxRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        self.server = server
        self.original_manager = server.work_order_manager
        self.original_queue = server.execution_queue
        self.original_ledger = server.message_intake_ledger
        self.original_workflow = server.workflow
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        server.work_order_manager = WorkOrderManager(root / "work_orders.json")
        server.execution_queue = ExecutionJobQueue(root / "execution_jobs.json")
        server.message_intake_ledger = MessageIntakeLedger(root / "message_intake.json")

        class FakeWorkflow:
            def generate_auto_reply(self, **kwargs):
                return "Draft reply"

        server.workflow = FakeWorkflow()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.server.work_order_manager = self.original_manager
        self.server.execution_queue = self.original_queue
        self.server.message_intake_ledger = self.original_ledger
        self.server.workflow = self.original_workflow
        self.tmp.cleanup()

    def test_manual_idempotency_replay_returns_existing_and_conflict_returns_409(self):
        payload = {
            "message": "First immutable reply",
            "idempotency_key": "reply:operator:request-1",
            "chat_id": "chat_1",
            "item_id": "item_1",
        }
        first = self.client.post("/workflows/messages/buyer_1/send", json=payload)
        replay = self.client.post("/workflows/messages/buyer_1/send", json=payload)
        conflict = self.client.post(
            "/workflows/messages/buyer_1/send",
            json={**payload, "message": "Different reply under the same key"},
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["duplicate"])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(
            replay.json()["work_order"]["work_order_id"],
            first.json()["work_order"]["work_order_id"],
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(self.server.work_order_manager.list_work_orders()), 1)

    def test_inbound_context_cannot_override_reply_binding_and_duplicate_projects_same_outbox(self):
        payload = {
            "source": "xianyu_im",
            "message_id": "platform_message_1",
            "buyer_id": "buyer_real",
            "message": "Still available?",
            "chat_id": "chat_real",
            "item_id": "item_real",
            "context": {
                "buyer_id": "buyer_wrong",
                "chat_id": "chat_wrong",
                "item_id": "item_wrong",
                "incoming_message": "wrong message",
                "reply_candidate": "wrong reply",
            },
        }
        first = self.client.post("/workflows/messages/inbound", json=payload)
        replay = self.client.post("/workflows/messages/inbound", json=payload)

        self.assertEqual(first.status_code, 200, first.text)
        context = first.json()["work_order"]["context"]
        self.assertEqual(context["buyer_id"], "buyer_real")
        self.assertEqual(context["chat_id"], "chat_real")
        self.assertEqual(context["item_id"], "item_real")
        self.assertEqual(context["incoming_message"], "Still available?")
        self.assertEqual(context["reply_candidate"], "Draft reply")
        self.assertEqual(first.json()["reply_outbox"]["status"], "pending_approval")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(
            replay.json()["reply_outbox"]["outbox_id"],
            first.json()["reply_outbox"]["outbox_id"],
        )

    def test_reply_outbox_route_is_registered_and_returns_summary(self):
        paths = [route.path for route in self.server.app.routes if hasattr(route, "path")]
        self.assertIn("/reply-outbox", paths)

        self.client.post(
            "/workflows/messages/buyer_1/send",
            json={"message": "Pending reply", "idempotency_key": "reply:operator:request-2"},
        )
        response = self.client.get("/reply-outbox")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["source_of_truth"], "WorkOrder + Approval + ExecutionJobQueue")
        self.assertEqual(data["summary"]["counts"]["pending_approval"], 1)
        self.assertEqual(len(data["items"]), 1)


class ReplyOutboxConsoleContractTests(unittest.TestCase):
    def test_console_exposes_reply_outbox_states_and_operator_actions(self):
        console_html = Path("web/console.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        style_css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn('id="reply-outbox-filter-status"', console_html)
        self.assertIn('id="reply-outbox-feed"', console_html)
        self.assertIn("renderReplyOutbox", app_js)
        self.assertIn("/reply-outbox?limit=100", app_js)
        self.assertIn("verification_required", app_js)
        self.assertIn("decideApproval", app_js)
        self.assertIn("enqueueApproval", app_js)
        self.assertIn("resolveJobVerification", app_js)
        self.assertIn(".reply-outbox-feed", style_css)
        self.assertIn(".reply-outbox-binding", style_css)


if __name__ == "__main__":
    unittest.main()
