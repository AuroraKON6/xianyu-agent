from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _make_approval(
    root: Path,
    *,
    action: str = "refund_order",
    payload=None,
    snapshot=None,
):
    from xianyu_agent.execution_queue import ExecutionJobQueue
    from xianyu_agent.work_orders import WorkOrderManager

    payload = dict(payload or {
        "order_id": "order_1",
        "buyer_id": "buyer_1",
        "cookie_id": "seller_1",
        "amount_cny": 29.9,
        "evidence_revision": 3,
    })
    snapshot = dict(snapshot or {
        "order_id": payload.get("order_id", ""),
        "buyer_id": payload.get("buyer_id", ""),
        "cookie_id": payload.get("cookie_id", "default"),
        "amount_cny": payload.get("amount_cny"),
        "evidence_revision": payload.get("evidence_revision"),
    })
    manager = WorkOrderManager(root / "work_orders.json")
    queue = ExecutionJobQueue(root / "execution_jobs.json")
    work_order = manager.create_work_order(
        "scope_test",
        "Scoped external action",
        "operations",
        context={
            "cookie_id": payload.get("cookie_id", "default"),
            "order_id": payload.get("order_id", ""),
        },
        risk_level="high",
    )
    manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[{
            "agent": "operations",
            "action": action,
            "description": "Execute scoped action",
            "params": payload,
            "draft_snapshot": snapshot,
        }],
    )
    stored = manager.get_work_order(work_order["work_order_id"])
    approval = stored["approvals"][0]
    return manager, queue, work_order["work_order_id"], approval["approval_id"]


def _internal_approval(manager, work_order_id, approval_id):
    approvals = manager._work_orders[work_order_id]["approvals"]
    return next(item for item in approvals if item["approval_id"] == approval_id)


class ActionScopeEnvelopeTests(unittest.TestCase):
    def test_new_approval_has_persisted_account_target_version_and_digest_binding(self):
        from xianyu_agent.work_orders import WorkOrderManager

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager, _queue, work_order_id, approval_id = _make_approval(root)
            approval = manager.get_work_order(work_order_id)["approvals"][0]
            scope = approval["scope_envelope"]

            self.assertEqual(scope["schema_version"], 1)
            self.assertEqual(scope["approval_id"], approval_id)
            self.assertEqual(scope["action"], "refund_order")
            self.assertEqual(scope["cookie_id"], "seller_1")
            self.assertEqual(scope["target_refs"]["order_id"], "order_1")
            self.assertEqual(scope["target_refs"]["buyer_id"], "buyer_1")
            self.assertEqual(scope["ttl_seconds"], 3600)
            self.assertEqual(len(scope["payload_sha256"]), 64)
            self.assertEqual(len(scope["draft_snapshot_sha256"]), 64)
            self.assertEqual(len(scope["expected_revision"]), 64)
            self.assertEqual(len(scope["action_digest"]), 64)
            self.assertTrue(scope["correlation_id"].startswith("corr_"))
            self.assertTrue(scope["idempotency_key"].startswith("action:"))
            self.assertEqual(approval["approved_action_digest"], "")

            reloaded = WorkOrderManager(root / "work_orders.json")
            restored = reloaded.get_work_order(work_order_id)["approvals"][0]
            self.assertEqual(restored["scope_envelope"], scope)

    def test_approval_signature_and_scope_are_copied_to_execution_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, queue, work_order_id, approval_id = _make_approval(Path(tmpdir))
            approved = manager.decide_approval(approval_id, "approved", "reviewed")
            job = queue.create_job(approval_id, manager)

            scope = approved["scope_envelope"]
            self.assertEqual(approved["approved_action_digest"], scope["action_digest"])
            self.assertEqual(job["idempotency_key"], scope["idempotency_key"])
            self.assertEqual(job["scope_envelope"], scope)
            self.assertEqual(
                manager.validate_approval_scope(approval_id)["action_digest"],
                scope["action_digest"],
            )
            event = manager.get_work_order(work_order_id)["events"][-1]
            self.assertEqual(event["type"], "approval_approved")
            self.assertEqual(event["action_digest"], scope["action_digest"])

    def test_identical_dangerous_actions_share_a_deterministic_idempotency_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager, queue, first_work_order_id, first_approval_id = _make_approval(root)
            _manager, _queue, second_work_order_id, second_approval_id = _make_approval(root)
            # _make_approval reloads the same file, so continue with the newest manager.
            manager = _manager
            queue = _queue
            first = manager.get_work_order(first_work_order_id)["approvals"][0]
            second = manager.get_work_order(second_work_order_id)["approvals"][0]

            self.assertNotEqual(
                first["scope_envelope"]["correlation_id"],
                second["scope_envelope"]["correlation_id"],
            )
            self.assertEqual(
                first["scope_envelope"]["idempotency_key"],
                second["scope_envelope"]["idempotency_key"],
            )
            manager.decide_approval(first_approval_id, "approved", "reviewed")
            manager.decide_approval(second_approval_id, "approved", "reviewed")
            queue.create_job(first_approval_id, manager)
            with self.assertRaisesRegex(ValueError, "idempotency key"):
                queue.create_job(second_approval_id, manager)

    def test_conflicting_account_sources_are_rejected_at_plan_creation(self):
        from xianyu_agent.action_scope import ActionScopeValidationError
        from xianyu_agent.work_orders import WorkOrderManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkOrderManager(Path(tmpdir) / "work_orders.json")
            work_order = manager.create_work_order(
                "listing",
                "Conflicting account",
                "listing",
                context={"cookie_id": "seller_context"},
            )

            with self.assertRaises(ActionScopeValidationError) as captured:
                manager.create_action_plan(
                    work_order["work_order_id"],
                    proposed_actions=[{
                        "agent": "listing",
                        "action": "publish_listing",
                        "description": "publish",
                        "params": {"draft_id": "draft_1", "cookie_id": "seller_payload"},
                    }],
                )

            self.assertEqual(captured.exception.code, "account_scope_conflict")

    def test_payload_change_before_decision_cannot_be_silently_approved(self):
        from xianyu_agent.action_scope import ActionScopeValidationError

        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _queue, work_order_id, approval_id = _make_approval(Path(tmpdir))
            approval = _internal_approval(manager, work_order_id, approval_id)
            approval["payload"]["amount_cny"] = 299.9

            with self.assertRaises(ActionScopeValidationError) as captured:
                manager.decide_approval(approval_id, "approved", "reviewed")

            self.assertEqual(captured.exception.code, "action_scope_mismatch")
            stored = manager.get_work_order(work_order_id)["approvals"][0]
            self.assertEqual(stored["status"], "pending")
            self.assertEqual(stored["approved_action_digest"], "")

    def test_change_after_approval_is_blocked_before_job_creation(self):
        from xianyu_agent.action_scope import ActionScopeValidationError

        with tempfile.TemporaryDirectory() as tmpdir:
            manager, queue, work_order_id, approval_id = _make_approval(Path(tmpdir))
            manager.decide_approval(approval_id, "approved", "reviewed")
            approval = _internal_approval(manager, work_order_id, approval_id)
            approval["payload"]["cookie_id"] = "different_seller"

            with self.assertRaises(ActionScopeValidationError) as captured:
                queue.create_job(approval_id, manager)

            self.assertEqual(captured.exception.code, "action_scope_mismatch")
            self.assertEqual(queue.list_jobs(), [])

    def test_change_after_enqueue_fails_before_handler_is_called(self):
        from xianyu_agent.action_executor import ActionExecutor

        with tempfile.TemporaryDirectory() as tmpdir:
            manager, queue, work_order_id, approval_id = _make_approval(Path(tmpdir))
            manager.decide_approval(approval_id, "approved", "reviewed")
            job = queue.create_job(approval_id, manager)
            approval = _internal_approval(manager, work_order_id, approval_id)
            approval["draft_snapshot"]["amount_cny"] = 1.0
            calls = []
            executor = ActionExecutor()
            executor.register_handler(
                "refund_order",
                lambda approval, context: calls.append(approval_id) or {"ok": True},
            )

            result = queue.run_next(executor, manager, worker_id="scope-worker")

            stored_job = queue.get_job(job["job_id"])
            stored_approval = manager.get_work_order(work_order_id)["approvals"][0]
            self.assertEqual(result[0]["status"], "failed")
            self.assertEqual(calls, [])
            self.assertEqual(stored_job["status"], "failed")
            self.assertEqual(stored_job["evidence"]["detected_blocker"], "action_scope_mismatch")
            self.assertFalse(stored_job["evidence"]["write_attempted"])
            self.assertEqual(stored_approval["execution_status"], "failed")

    def test_expired_scope_cannot_be_approved(self):
        from xianyu_agent.action_scope import ActionScopeValidationError

        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _queue, work_order_id, approval_id = _make_approval(
                Path(tmpdir),
                action="send_message",
                payload={
                    "buyer_id": "buyer_1",
                    "cookie_id": "seller_1",
                    "message": "Current reply",
                },
                snapshot={
                    "buyer_id": "buyer_1",
                    "cookie_id": "seller_1",
                    "message_summary": "Current reply",
                },
            )
            approval = manager.get_work_order(work_order_id)["approvals"][0]
            future = approval["scope_envelope"]["valid_until"] + 1

            with patch("xianyu_agent.work_orders.time.time", return_value=future):
                with self.assertRaises(ActionScopeValidationError) as captured:
                    manager.decide_approval(approval_id, "approved", "too late")

            self.assertEqual(captured.exception.code, "action_scope_expired")

    def test_expired_scope_cannot_be_retried(self):
        from xianyu_agent.action_executor import ActionExecutor

        with tempfile.TemporaryDirectory() as tmpdir:
            manager, queue, _work_order_id, approval_id = _make_approval(Path(tmpdir))
            manager.decide_approval(approval_id, "approved", "reviewed")
            job = queue.create_job(approval_id, manager)
            executor = ActionExecutor()
            executor.register_handler("refund_order", lambda approval, context: {"ok": False})
            queue.run_next(executor, manager, worker_id="scope-worker")
            valid_until = job["scope_envelope"]["valid_until"]

            with patch("xianyu_agent.execution_queue.time.time", return_value=valid_until + 1):
                with self.assertRaisesRegex(ValueError, "scope expired"):
                    queue.retry_job(job["job_id"])

    def test_manual_completion_allows_expiry_but_not_scope_tampering(self):
        from xianyu_agent.action_scope import ActionScopeValidationError

        with tempfile.TemporaryDirectory() as tmpdir:
            manager, _queue, _work_order_id, approval_id = _make_approval(
                Path(tmpdir),
                action="send_message",
                payload={
                    "buyer_id": "buyer_1",
                    "cookie_id": "seller_1",
                    "message": "Current reply",
                },
                snapshot={
                    "buyer_id": "buyer_1",
                    "cookie_id": "seller_1",
                    "message_summary": "Current reply",
                },
            )
            approved = manager.decide_approval(approval_id, "approved", "reviewed")
            future = approved["scope_envelope"]["valid_until"] + 1

            with patch("xianyu_agent.work_orders.time.time", return_value=future):
                completed = manager.complete_approval_manually(
                    approval_id,
                    {"ok": True, "external_id": "message_1"},
                )

            self.assertEqual(completed["execution_status"], "completed")

            second_manager, _queue, second_work_order_id, second_approval_id = _make_approval(
                Path(tmpdir) / "second"
            )
            second_manager.decide_approval(second_approval_id, "approved", "reviewed")
            second = _internal_approval(second_manager, second_work_order_id, second_approval_id)
            second["payload"]["amount_cny"] = 1.0

            with self.assertRaises(ActionScopeValidationError) as captured:
                second_manager.complete_approval_manually(
                    second_approval_id,
                    {"ok": True, "external_id": "refund_1"},
                )

            self.assertEqual(captured.exception.code, "action_scope_mismatch")
            self.assertIsNone(second["execution_status"])

    def test_verification_resolution_does_not_commit_after_scope_tampering(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.action_scope import ActionScopeValidationError

        with tempfile.TemporaryDirectory() as tmpdir:
            manager, queue, work_order_id, approval_id = _make_approval(Path(tmpdir))
            manager.decide_approval(approval_id, "approved", "reviewed")
            job = queue.create_job(approval_id, manager)
            executor = ActionExecutor()
            executor.register_handler(
                "refund_order",
                lambda approval, context: {
                    "ok": False,
                    "verification_required": True,
                    "outcome_uncertain": True,
                },
            )
            queue.run_next(executor, manager, worker_id="scope-worker")
            approval = _internal_approval(manager, work_order_id, approval_id)
            approval["payload"]["amount_cny"] = 1.0

            with self.assertRaises(ActionScopeValidationError) as captured:
                queue.resolve_verification(
                    job["job_id"],
                    resolution="confirmed_completed",
                    note="checked platform result",
                    evidence={"external_id": "refund_1"},
                    work_order_manager=manager,
                )

            self.assertEqual(captured.exception.code, "action_scope_mismatch")
            self.assertEqual(queue.get_job(job["job_id"])["status"], "verification_required")


class ListingDraftRevisionTests(unittest.TestCase):
    def test_image_content_change_changes_listing_revision(self):
        from xianyu_agent.action_scope import listing_draft_revision

        with tempfile.TemporaryDirectory() as tmpdir:
            image = Path(tmpdir) / "image.png"
            image.write_bytes(b"version-one")
            draft = {"title": "Test", "price": 10, "selected_images": [str(image)]}
            first = listing_draft_revision(draft)
            image.write_bytes(b"version-two")

            self.assertNotEqual(first, listing_draft_revision(draft))

    def test_changed_persisted_draft_is_blocked_before_browser_adapter(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.action_scope import listing_draft_revision

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            drafts = root / "drafts"
            drafts.mkdir()
            draft_id = "draft_1"
            draft = {
                "id": draft_id,
                "title": "Approved title",
                "price": 99,
                "description": "Approved description",
                "selected_images": [],
            }
            draft_path = drafts / f"{draft_id}.json"
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            manager, queue, work_order_id, approval_id = _make_approval(
                root,
                action="publish_listing",
                payload={
                    "draft_id": draft_id,
                    "cookie_id": "seller_1",
                    "expected_draft_revision": listing_draft_revision(draft),
                },
                snapshot={
                    "title": draft["title"],
                    "price": draft["price"],
                    "cookie_id": "seller_1",
                },
            )
            manager.decide_approval(approval_id, "approved", "reviewed")
            job = queue.create_job(approval_id, manager)
            draft["title"] = "Changed after approval"
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            adapter_calls = []

            class FakeAdapter:
                async def publish_listing(self, draft, cookie_id="default"):
                    adapter_calls.append(draft.title)
                    return SimpleNamespace(to_dict=lambda: {"ok": True})

            class FakeWorkflow:
                adapter = FakeAdapter()

            result = queue.run_next(
                ActionExecutor(),
                manager,
                {"workflow": FakeWorkflow(), "data_dir": root},
                worker_id="scope-worker",
            )

            stored_job = queue.get_job(job["job_id"])
            approval = manager.get_work_order(work_order_id)["approvals"][0]
            self.assertEqual(result[0]["status"], "failed")
            self.assertEqual(adapter_calls, [])
            self.assertEqual(stored_job["evidence"]["detected_blocker"], "draft_revision_mismatch")
            self.assertFalse(stored_job["evidence"]["write_attempted"])
            self.assertEqual(approval["execution_result"]["error"], "Listing draft changed after approval")

    def test_missing_expected_revision_is_blocked_before_browser_adapter(self):
        import asyncio

        from xianyu_agent.action_executor import handle_publish_listing

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            drafts = root / "drafts"
            drafts.mkdir()
            draft_id = "legacy_draft"
            (drafts / f"{draft_id}.json").write_text(
                json.dumps({"id": draft_id, "title": "Legacy", "price": 9.9}),
                encoding="utf-8",
            )
            adapter_calls = []

            class FakeAdapter:
                async def publish_listing(self, draft, cookie_id="default"):
                    adapter_calls.append(draft.title)
                    return SimpleNamespace(ok=True)

            result = asyncio.run(
                handle_publish_listing(
                    {"action": "publish_listing", "payload": {"draft_id": draft_id}},
                    {"data_dir": root, "workflow": SimpleNamespace(adapter=FakeAdapter())},
                )
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["scope_validation"]["code"], "draft_revision_missing")
            self.assertFalse(result["evidence"]["write_attempted"])
            self.assertEqual(adapter_calls, [])


class ActionScopeRouteTests(unittest.TestCase):
    def test_mutated_approval_returns_http_409(self):
        from fastapi.testclient import TestClient
        from xianyu_agent import server
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        original_manager = server.work_order_manager
        original_queue = server.execution_queue
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            try:
                server.work_order_manager = WorkOrderManager(root / "work_orders.json")
                server.execution_queue = ExecutionJobQueue(root / "execution_jobs.json")
                manager, _queue, work_order_id, approval_id = _make_approval(root)
                server.work_order_manager = manager
                approval = _internal_approval(manager, work_order_id, approval_id)
                approval["payload"]["amount_cny"] = 999.0

                response = TestClient(server.app).post(
                    f"/approvals/{approval_id}/decision",
                    json={"decision": "approved", "note": "reviewed"},
                )

                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["error"], "action_scope_mismatch")
            finally:
                server.work_order_manager = original_manager
                server.execution_queue = original_queue

    def test_mutation_after_approval_returns_http_409_before_enqueue(self):
        from fastapi.testclient import TestClient
        from xianyu_agent import server
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        original_manager = server.work_order_manager
        original_queue = server.execution_queue
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            try:
                manager, queue, work_order_id, approval_id = _make_approval(root)
                server.work_order_manager = manager
                server.execution_queue = queue
                manager.decide_approval(approval_id, "approved", "reviewed")
                approval = _internal_approval(manager, work_order_id, approval_id)
                approval["payload"]["order_id"] = "different_order"

                response = TestClient(server.app).post(f"/approvals/{approval_id}/execute")

                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["error"], "action_scope_mismatch")
                self.assertEqual(queue.list_jobs(), [])
            finally:
                server.work_order_manager = original_manager
                server.execution_queue = original_queue

    def test_mutated_scope_returns_http_409_before_verification_resolution(self):
        from fastapi.testclient import TestClient

        from xianyu_agent import server
        from xianyu_agent.action_executor import ActionExecutor

        original_manager = server.work_order_manager
        original_queue = server.execution_queue
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                manager, queue, work_order_id, approval_id = _make_approval(Path(tmpdir))
                server.work_order_manager = manager
                server.execution_queue = queue
                manager.decide_approval(approval_id, "approved", "reviewed")
                job = queue.create_job(approval_id, manager)
                executor = ActionExecutor()
                executor.register_handler(
                    "refund_order",
                    lambda approval, context: {
                        "ok": False,
                        "verification_required": True,
                        "outcome_uncertain": True,
                    },
                )
                queue.run_next(executor, manager, worker_id="scope-worker")
                approval = _internal_approval(manager, work_order_id, approval_id)
                approval["payload"]["order_id"] = "different_order"

                response = TestClient(server.app).post(
                    f"/execution-jobs/{job['job_id']}/verification",
                    json={
                        "resolution": "confirmed_completed",
                        "note": "checked platform result",
                        "external_id": "refund_1",
                    },
                )

                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["error"], "action_scope_mismatch")
                self.assertEqual(queue.get_job(job["job_id"])["status"], "verification_required")
            finally:
                server.work_order_manager = original_manager
                server.execution_queue = original_queue


if __name__ == "__main__":
    unittest.main()
