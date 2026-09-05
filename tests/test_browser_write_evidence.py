from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import xianyu_agent.browser_adapter as browser_adapter_module
from xianyu_agent.browser_adapter import BrowserAdapter
from xianyu_agent.models import ListingDraft, Order


class BrowserWriteEvidenceArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_writes_redacted_manifest_and_integrity_bound_screenshot(self):
        from xianyu_agent.browser_write_evidence import capture_browser_write_evidence

        cookie_id = "seller-account-secret"
        target_ref = "order-target-secret"
        screenshot_bytes = b"fake-png-evidence"

        class FakePage:
            url = (
                "https://user:password@www.goofish.com/item/order-target-secret"
                "?token=query-secret#private"
            )

            async def title(self):
                return "Order order-target-secret for seller-account-secret"

            async def screenshot(self, path, full_page=True):
                self.full_page = full_page
                Path(path).write_bytes(screenshot_bytes)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            evidence = await capture_browser_write_evidence(
                FakePage(),
                data_dir=root,
                action="fulfill_order",
                cookie_id=cookie_id,
                stage="post_submit_exception",
                write_attempted=True,
                error=(
                    "cookie=raw-cookie seller-account-secret failed for "
                    "order-target-secret token=raw-token"
                ),
                blocker="session=raw-session order-target-secret",
                target_ref=target_ref,
            )

            screenshot = root / evidence["after_screenshot"]
            manifest = root / evidence["evidence_manifest"]
            serialized = manifest.read_text(encoding="utf-8")
            payload = json.loads(serialized)

            self.assertEqual(evidence["capture_status"], "complete")
            self.assertEqual(evidence["adapter_status"], "page_available")
            self.assertEqual(evidence["screenshot_bytes"], len(screenshot_bytes))
            self.assertEqual(
                evidence["screenshot_sha256"],
                hashlib.sha256(screenshot_bytes).hexdigest(),
            )
            self.assertEqual(screenshot.read_bytes(), screenshot_bytes)
            self.assertEqual(payload["screenshot"]["sha256"], evidence["screenshot_sha256"])
            self.assertNotIn("query-secret", serialized)
            self.assertNotIn("user:password", serialized)
            self.assertNotIn(cookie_id, serialized)
            self.assertNotIn(target_ref, serialized)
            self.assertNotIn("raw-cookie", serialized)
            self.assertNotIn("raw-token", serialized)
            self.assertNotIn("raw-session", serialized)
            self.assertNotIn(cookie_id, evidence["evidence_manifest"])
            self.assertNotIn(target_ref, evidence["evidence_manifest"])

    async def test_capture_keeps_metadata_manifest_when_screenshot_fails(self):
        from xianyu_agent.browser_write_evidence import capture_browser_write_evidence

        class BrokenScreenshotPage:
            url = "https://www.goofish.com/publish?authorization=hidden"

            async def title(self):
                return "Publish"

            async def screenshot(self, path, full_page=True):
                raise RuntimeError("token=screenshot-secret capture failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            evidence = await capture_browser_write_evidence(
                BrokenScreenshotPage(),
                data_dir=root,
                action="publish_listing",
                cookie_id="seller_1",
                stage="pre_submit",
                write_attempted=False,
                blocker="publish_button_not_found",
                target_ref="draft_1",
            )

            self.assertEqual(evidence["capture_status"], "metadata_only")
            self.assertEqual(evidence["after_screenshot"], "")
            self.assertTrue(evidence["evidence_manifest"])
            self.assertIn("token=[REDACTED]", evidence["capture_error"])
            self.assertNotIn("screenshot-secret", evidence["capture_error"])
            self.assertTrue((root / evidence["evidence_manifest"]).is_file())


class BrowserAdapterFailureEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_browser_still_creates_metadata_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = BrowserAdapter(
                SimpleNamespace(data_dir=Path(tmpdir), browser_cdp_url="", xianyu_cookies=""),
                storage=None,
                browser_pool=None,
            )

            async def no_session(cookie_id="default"):
                return None

            adapter._ensure_session = no_session
            result = await adapter.publish_listing(
                ListingDraft(id="draft-secret", title="Test"),
                cookie_id="seller-secret",
            )

            evidence = result.raw["evidence"]
            self.assertFalse(result.ok)
            self.assertEqual(evidence["detected_blocker"], "browser_unavailable")
            self.assertEqual(evidence["adapter_status"], "unavailable")
            self.assertEqual(evidence["capture_status"], "metadata_only")
            self.assertFalse(evidence["write_attempted"])
            self.assertTrue((Path(tmpdir) / evidence["evidence_manifest"]).is_file())

    async def test_fulfillment_disconnect_after_submit_requires_verification_with_evidence(self):
        account_id = "seller-account-secret"
        order_id = "order-target-secret"

        class FakeLocator:
            def __init__(self, kind):
                self.kind = kind

            @property
            def first(self):
                return self

            async def count(self):
                return 1

            async def click(self):
                if self.kind == "submit":
                    raise RuntimeError("session=browser-secret lost after submit")

            async def fill(self, value):
                self.value = value

        class FakePage:
            url = "https://www.goofish.com/bought?orderId=order-target-secret&token=hidden"

            async def goto(self, url, wait_until=None, timeout=None):
                self.url = url

            def locator(self, selector):
                if "确认发货" in selector or "提交" in selector:
                    return FakeLocator("submit")
                if "发货" in selector:
                    return FakeLocator("ship")
                return FakeLocator("tracking")

            async def title(self):
                return "Order order-target-secret"

            async def screenshot(self, path, full_page=True):
                Path(path).write_bytes(b"fulfillment-failure")

        async def fast_sleep(_seconds):
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            adapter = BrowserAdapter(
                SimpleNamespace(data_dir=root, browser_cdp_url="", xianyu_cookies=""),
                storage=None,
                browser_pool=None,
            )

            async def fake_session(cookie_id="default"):
                return FakePage()

            adapter._ensure_session = fake_session
            old_sleep = browser_adapter_module.asyncio.sleep
            browser_adapter_module.asyncio.sleep = fast_sleep
            try:
                result = await adapter.fulfill_order(
                    Order(
                        order_id=order_id,
                        listing_id="listing_1",
                        delivery_payload="SF123456",
                    ),
                    cookie_id=account_id,
                )
            finally:
                browser_adapter_module.asyncio.sleep = old_sleep

            evidence = result["evidence"]
            manifest_text = (root / evidence["evidence_manifest"]).read_text(encoding="utf-8")
            self.assertFalse(result["ok"])
            self.assertTrue(result["outcome_uncertain"])
            self.assertTrue(result["verification_required"])
            self.assertEqual(result["verification_reason"], "fulfillment_adapter_exception")
            self.assertEqual(evidence["action_stage"], "post_submit_exception")
            self.assertTrue(evidence["write_attempted"])
            self.assertEqual(evidence["capture_status"], "complete")
            self.assertNotIn("browser-secret", result["error"])
            self.assertNotIn(account_id, manifest_text)
            self.assertNotIn(order_id, manifest_text)
            self.assertNotIn("token=hidden", manifest_text)


class EvidenceProjectionTests(unittest.TestCase):
    def test_nested_publish_raw_evidence_reaches_verification_job(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = WorkOrderManager(root / "work_orders.json")
            queue = ExecutionJobQueue(root / "execution_jobs.json")
            work_order = manager.create_work_order(
                "listing",
                "Publish approved draft",
                "listing",
            )
            manager.create_action_plan(
                work_order["work_order_id"],
                proposed_actions=[{
                    "agent": "listing",
                    "action": "publish_listing",
                    "description": "Publish listing",
                    "params": {"draft_id": "draft_1"},
                }],
            )
            stored = manager.get_work_order(work_order["work_order_id"])
            approval = stored["approvals"][0]
            manager.decide_approval(approval["approval_id"], "approved", "reviewed")
            job = queue.create_job(approval["approval_id"], manager)
            executor = ActionExecutor()
            executor.register_handler(
                "publish_listing",
                lambda approval, context: {
                    "ok": False,
                    "error": "publish result not confirmed",
                    "publish_result": {
                        "ok": False,
                        "raw": {
                            "outcome_uncertain": True,
                            "verification_required": True,
                            "verification_reason": "publish_result_not_confirmed",
                            "evidence": {
                                "evidence_id": "bwe_publish",
                                "evidence_manifest": "browser_write_evidence/a/publish.json",
                                "after_screenshot": "browser_write_evidence/a/publish.png",
                                "action_stage": "post_submit",
                                "write_attempted": True,
                                "capture_status": "complete",
                                "detected_blocker": "publish_result_not_confirmed",
                            },
                        },
                    },
                },
            )

            queue.run_next(executor, manager, worker_id="publish-worker")
            stored_job = queue.get_job(job["job_id"])

            self.assertEqual(stored_job["status"], "verification_required")
            self.assertEqual(
                stored_job["verification"]["reason"],
                "publish_result_not_confirmed",
            )
            self.assertEqual(stored_job["evidence"]["evidence_id"], "bwe_publish")
            self.assertEqual(stored_job["evidence"]["action_stage"], "post_submit")
            self.assertTrue(stored_job["evidence"]["write_attempted"])

    def test_nested_send_evidence_reaches_job_and_reply_outbox(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.reply_outbox import build_reply_metadata, list_reply_outbox
        from xianyu_agent.work_orders import WorkOrderManager

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = WorkOrderManager(root / "work_orders.json")
            queue = ExecutionJobQueue(root / "execution_jobs.json")
            message = "Approved reply"
            metadata = build_reply_metadata(
                action="send_message",
                cookie_id="seller_1",
                buyer_id="buyer_1",
                message=message,
                chat_id="chat_1",
                item_id="item_1",
                source="xianyu_im",
                source_event_key="event_1",
                source_message_id="message_1",
            )
            work_order = manager.create_work_order(
                "customer_message",
                "Reply proposal",
                "customer_service",
                context={"buyer_id": "buyer_1", "cookie_id": "seller_1"},
            )
            manager.create_action_plan(
                work_order["work_order_id"],
                proposed_actions=[{
                    "agent": "customer_service",
                    "action": "send_message",
                    "description": "Send approved reply",
                    "params": {
                        "buyer_id": "buyer_1",
                        "cookie_id": "seller_1",
                        "chat_id": "chat_1",
                        "item_id": "item_1",
                        "message": message,
                        **metadata,
                    },
                    "draft_snapshot": {"reply_summary": message, **metadata},
                }],
            )
            stored = manager.get_work_order(work_order["work_order_id"])
            approval = stored["approvals"][0]
            manager.decide_approval(approval["approval_id"], "approved", "reviewed")
            job = queue.create_job(approval["approval_id"], manager)

            class UncertainAdapter:
                async def send_message(self, buyer_id, message, cookie_id="default"):
                    return {
                        "ok": False,
                        "error": "connection lost after Enter",
                        "outcome_uncertain": True,
                        "verification_required": True,
                        "verification_reason": "message_send_adapter_exception",
                        "evidence": {
                            "evidence_id": "bwe_test",
                            "evidence_manifest": "browser_write_evidence/a/manifest.json",
                            "after_screenshot": "browser_write_evidence/a/screenshot.png",
                            "screenshot_sha256": "a" * 64,
                            "screenshot_bytes": 42,
                            "action_stage": "post_submit_exception",
                            "write_attempted": True,
                            "capture_status": "complete",
                            "detected_blocker": "message_send_adapter_exception",
                            "operator_next_action": "verify_platform_before_retry",
                        },
                    }

            class FakeWorkflow:
                adapter = UncertainAdapter()

            queue.run_next(
                ActionExecutor(),
                manager,
                {"workflow": FakeWorkflow()},
                worker_id="evidence-worker",
            )

            stored_job = queue.get_job(job["job_id"])
            outbox = list_reply_outbox(
                work_order_manager=manager,
                execution_queue=queue,
            )[0]
            self.assertEqual(stored_job["status"], "verification_required")
            self.assertEqual(stored_job["evidence"]["evidence_id"], "bwe_test")
            self.assertTrue(stored_job["evidence"]["write_attempted"])
            self.assertEqual(outbox["status"], "verification_required")
            self.assertEqual(outbox["execution_evidence"]["evidence_id"], "bwe_test")
            self.assertEqual(
                outbox["execution_evidence"]["evidence_manifest"],
                "browser_write_evidence/a/manifest.json",
            )
            with self.assertRaises(ValueError):
                queue.retry_job(job["job_id"])

    def test_dashboard_counts_failed_and_verification_evidence_artifacts(self):
        from xianyu_agent.opc_dashboard import OPCDashboard

        class FakeExecutionQueue:
            def list_jobs(self, limit=500):
                return [
                    {
                        "job_id": "job_verify",
                        "status": "verification_required",
                        "action": "send_message",
                        "evidence": {
                            "detected_blocker": "message_send_adapter_exception",
                            "evidence_manifest": "browser_write_evidence/a/one.json",
                        },
                        "operator_next_action": "verify_platform_before_retry",
                    },
                    {
                        "job_id": "job_failed",
                        "status": "failed",
                        "action": "publish_listing",
                        "evidence": {
                            "detected_blocker": "login_required",
                            "after_screenshot": "browser_write_evidence/a/two.png",
                        },
                        "operator_next_action": "restore_login_then_retry",
                    },
                ]

        result = OPCDashboard(execution_queue=FakeExecutionQueue()).get_opc_daily()
        summary = result["execution_evidence"]

        self.assertEqual(summary["artifact_count"], 2)
        self.assertEqual(summary["verification_with_evidence"], 1)
        self.assertEqual(summary["failed_with_blocker"], 1)
        self.assertEqual(
            {item["status"] for item in summary["recent_blockers"]},
            {"failed", "verification_required"},
        )


if __name__ == "__main__":
    unittest.main()
