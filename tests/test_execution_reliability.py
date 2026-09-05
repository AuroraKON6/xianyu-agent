import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _approved_job(root: Path, action: str = "publish_listing"):
    from xianyu_agent.execution_queue import ExecutionJobQueue
    from xianyu_agent.work_orders import WorkOrderManager

    manager = WorkOrderManager(root / "work_orders.json")
    queue = ExecutionJobQueue(root / "execution_jobs.json")
    order = manager.create_work_order("listing", "reliable execution", "listing")
    manager.create_action_plan(
        order["work_order_id"],
        proposed_actions=[{
            "agent": "listing",
            "action": action,
            "description": "execute once",
            "params": {"draft_id": "draft_1"},
        }],
    )
    approval_id = manager.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
    manager.decide_approval(approval_id, "approved", "approved for reliability test")
    job = queue.create_job(approval_id, manager)
    return manager, queue, order["work_order_id"], approval_id, job


class ExecutionReliabilityTests(unittest.TestCase):
    def test_two_queue_instances_claim_one_job_only_once(self):
        from xianyu_agent.action_executor import ActionExecutor
        from xianyu_agent.execution_queue import ExecutionJobQueue

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager, queue_a, _, _, job = _approved_job(root)
            queue_b = ExecutionJobQueue(root / "execution_jobs.json")
            started = threading.Event()
            release = threading.Event()
            calls = []

            def handler(approval, context):
                calls.append(context["execution_claim"]["fencing_token"])
                started.set()
                release.wait(timeout=5)
                return {"ok": True, "external_id": "item_once"}

            executor = ActionExecutor()
            executor.register_handler("publish_listing", handler)
            first_results = []
            thread = threading.Thread(
                target=lambda: first_results.extend(
                    queue_a.run_next(executor, manager, worker_id="worker_a", lease_seconds=30)
                )
            )
            thread.start()
            self.assertTrue(started.wait(timeout=5))

            second_results = queue_b.run_next(
                executor,
                manager,
                worker_id="worker_b",
                lease_seconds=30,
            )
            release.set()
            thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(second_results, [])
            self.assertEqual(calls, [1])
            self.assertEqual(first_results[0]["status"], "completed")
            self.assertEqual(queue_b.get_job(job["job_id"])["status"], "completed")

    def test_expired_lease_requires_verification_and_never_reexecutes(self):
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager, queue, work_order_id, _, job = _approved_job(root)
            claimed, _ = queue._claim_ready_jobs("crashed_worker", 5, max_jobs=1)
            self.assertEqual(len(claimed), 1)
            calls = []
            executor = ActionExecutor()
            executor.register_handler("publish_listing", lambda approval, context: calls.append(1) or {"ok": True})

            with patch("xianyu_agent.execution_queue.time.time", return_value=claimed[0]["lease_expires_at"] + 1):
                results = queue.run_next(executor, manager, worker_id="recovery_worker")

            self.assertEqual(results, [])
            self.assertEqual(calls, [])
            stored = queue.get_job(job["job_id"])
            self.assertEqual(stored["status"], "verification_required")
            self.assertEqual(stored["verification"]["reason"], "execution_lease_expired")
            approval = manager.get_work_order(work_order_id)["approvals"][0]
            self.assertEqual(approval["execution_status"], "verification_required")
            with self.assertRaises(ValueError):
                queue.retry_job(job["job_id"])

    def test_uncertain_handler_result_requires_human_closure(self):
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager, queue, work_order_id, _, job = _approved_job(root)
            executor = ActionExecutor()
            executor.register_handler(
                "publish_listing",
                lambda approval, context: {
                    "ok": False,
                    "error": "connection lost after submit",
                    "outcome_uncertain": True,
                    "verification_required": True,
                    "verification_reason": "post_submit_disconnect",
                },
            )

            result = queue.run_next(executor, manager, worker_id="worker_a")

            self.assertEqual(result[0]["status"], "verification_required")
            stored = queue.get_job(job["job_id"])
            self.assertEqual(stored["verification"]["reason"], "post_submit_disconnect")
            approval = manager.get_work_order(work_order_id)["approvals"][0]
            self.assertEqual(approval["execution_status"], "verification_required")

    def test_confirmed_completion_fences_stale_worker_and_completes_approval(self):
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager, queue, work_order_id, _, job = _approved_job(root)
            claimed, _ = queue._claim_ready_jobs("crashed_worker", 5, max_jobs=1)
            with patch("xianyu_agent.execution_queue.time.time", return_value=claimed[0]["lease_expires_at"] + 1):
                queue.run_next(ActionExecutor(), manager, worker_id="recovery_worker")

            resolved = queue.resolve_verification(
                job["job_id"],
                resolution="confirmed_completed",
                note="checked the seller item page",
                evidence={"external_id": "item_confirmed", "checked_by": "operator_a"},
                work_order_manager=manager,
            )
            stale = queue._finalize_claim(
                claimed[0],
                {"ok": False, "error": "late worker result"},
                manager,
                verification_reason="",
            )

            self.assertEqual(resolved["status"], "completed")
            self.assertGreater(resolved["fencing_token"], claimed[0]["fencing_token"])
            self.assertTrue(stale["stale_claim"])
            approval = manager.get_work_order(work_order_id)["approvals"][0]
            self.assertEqual(approval["execution_status"], "completed")
            self.assertTrue(approval["execution_result"]["manual_confirmation"])
            self.assertEqual(queue.get_job(job["job_id"])["status"], "completed")

    def test_confirmed_non_execution_allows_one_explicit_retry(self):
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager, queue, _, _, job = _approved_job(root)
            claimed, _ = queue._claim_ready_jobs("crashed_worker", 5, max_jobs=1)
            with patch("xianyu_agent.execution_queue.time.time", return_value=claimed[0]["lease_expires_at"] + 1):
                queue.run_next(ActionExecutor(), manager, worker_id="recovery_worker")

            resolved = queue.resolve_verification(
                job["job_id"],
                resolution="confirmed_not_executed",
                note="seller center contains no new item",
                evidence={"checked_by": "operator_a"},
                work_order_manager=manager,
            )
            self.assertEqual(resolved["status"], "failed")
            queued = queue.retry_job(job["job_id"])
            self.assertEqual(queued["status"], "queued")

            executor = ActionExecutor()
            executor.register_handler("publish_listing", lambda approval, context: {"ok": True, "external_id": "item_retry"})
            result = queue.run_next(executor, manager, worker_id="retry_worker")

            self.assertEqual(result[0]["status"], "completed")
            self.assertEqual(queue.get_job(job["job_id"])["attempts"], 2)

    def test_confirmed_completion_requires_platform_evidence(self):
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager, queue, _, _, job = _approved_job(root)
            executor = ActionExecutor()
            executor.register_handler(
                "publish_listing",
                lambda approval, context: {"ok": False, "verification_required": True},
            )
            queue.run_next(executor, manager)

            with self.assertRaises(ValueError):
                queue.resolve_verification(
                    job["job_id"],
                    resolution="confirmed_completed",
                    note="checked",
                    evidence={},
                    work_order_manager=manager,
                )


if __name__ == "__main__":
    unittest.main()
