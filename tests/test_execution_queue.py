import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class ExecutionJobQueueTests(unittest.TestCase):
    def test_create_job_from_approved_approval(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")

            job = eq.create_job(approval_id, wom)

            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["approval_id"], approval_id)
            self.assertEqual(job["action"], "publish_listing")
            self.assertEqual(job["attempts"], 0)
            self.assertEqual(job["max_attempts"], 3)
            self.assertIsNotNone(job["job_id"])
            self.assertIsNotNone(job["created_at"])

    def test_pending_approval_cannot_create_job(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]

            with self.assertRaises(ValueError) as ctx:
                eq.create_job(approval_id, wom)
            self.assertIn("approved", str(ctx.exception).lower())

    def test_rejected_approval_cannot_create_job(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="rejected", note="不行")

            with self.assertRaises(ValueError) as ctx:
                eq.create_job(approval_id, wom)
            self.assertIn("approved", str(ctx.exception).lower())

    def test_no_duplicate_active_jobs(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")

            eq.create_job(approval_id, wom)

            with self.assertRaises(ValueError) as ctx:
                eq.create_job(approval_id, wom)
            self.assertIn("active", str(ctx.exception).lower())

    def test_run_next_dispatches_and_completes(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            executor = ActionExecutor()
            executor.register_handler("publish_listing", lambda a, ctx: {"ok": True, "external_id": "ext_123"})

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            eq.create_job(approval_id, wom)

            results = eq.run_next(executor, wom)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["ok"])
            self.assertEqual(results[0]["status"], "completed")

            job = eq.get_job(results[0]["job_id"])
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["attempts"], 1)
            self.assertIsNotNone(job["finished_at"])

    def test_handler_failure_marks_failed(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            executor = ActionExecutor()
            executor.register_handler("publish_listing", lambda a, ctx: {"ok": False, "error": "browser crashed"})

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            eq.create_job(approval_id, wom)

            results = eq.run_next(executor, wom)

            self.assertEqual(len(results), 1)
            job = eq.get_job(results[0]["job_id"])
            self.assertEqual(job["status"], "failed")
            self.assertIn("browser crashed", job.get("error", ""))

    def test_failed_job_can_retry(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            call_count = {"n": 0}

            def flaky_handler(approval, ctx):
                call_count["n"] += 1
                if call_count["n"] < 2:
                    return {"ok": False, "error": "transient error"}
                return {"ok": True, "external_id": "ext_retry"}

            executor = ActionExecutor()
            executor.register_handler("publish_listing", flaky_handler)

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            job = eq.create_job(approval_id, wom)
            job_id = job["job_id"]

            # First run fails
            eq.run_next(executor, wom)
            job = eq.get_job(job_id)
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["attempts"], 1)

            # Retry
            eq.retry_job(job_id)
            job = eq.get_job(job_id)
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["attempts"], 1)  # attempts preserved

            # Second run succeeds
            eq.run_next(executor, wom)
            job = eq.get_job(job_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["attempts"], 2)

    def test_completed_approval_cannot_retry(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.action_executor import ActionExecutor

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            executor = ActionExecutor()
            executor.register_handler("publish_listing", lambda a, ctx: {"ok": True, "external_id": "ext_ok"})

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            job = eq.create_job(approval_id, wom)
            job_id = job["job_id"]

            eq.run_next(executor, wom)

            with self.assertRaises(ValueError) as ctx:
                eq.retry_job(job_id)
            self.assertIn("completed", str(ctx.exception).lower())

    def test_cancel_queued_job(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            job = eq.create_job(approval_id, wom)
            job_id = job["job_id"]

            eq.cancel_job(job_id)
            job = eq.get_job(job_id)
            self.assertEqual(job["status"], "cancelled")

    def test_list_jobs(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            eq.create_job(approval_id, wom)

            jobs = eq.list_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "queued")

    def test_jobs_persist_to_disk(self):
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.work_orders import WorkOrderManager

        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wom = WorkOrderManager(tmp / "work_orders.json")
            eq = ExecutionJobQueue(tmp / "execution_jobs.json")

            order = wom.create_work_order("listing", "测试上架", "listing")
            wom.create_action_plan(
                order["work_order_id"],
                proposed_actions=[
                    {"agent": "listing", "action": "publish_listing", "description": "发布商品", "params": {"draft_id": "d1"}},
                ],
            )
            approval_id = wom.get_work_order(order["work_order_id"])["approvals"][0]["approval_id"]
            wom.decide_approval(approval_id, decision="approved", note="确认")
            eq.create_job(approval_id, wom)

            # Reload from disk
            eq2 = ExecutionJobQueue(tmp / "execution_jobs.json")
            jobs = eq2.list_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "queued")


class ExecutionQueueRouteTests(unittest.TestCase):
    def test_execution_queue_routes_registered(self):
        from xianyu_agent.server import app

        route_paths = [route.path for route in app.routes if hasattr(route, "path")]
        self.assertIn("/execution-jobs", route_paths)
        self.assertIn("/execution-jobs/{job_id}", route_paths)
        self.assertIn("/execution-jobs/run-next", route_paths)
        self.assertIn("/execution-jobs/run-pending", route_paths)
        self.assertIn("/execution-jobs/{job_id}/retry", route_paths)
        self.assertIn("/execution-jobs/{job_id}/cancel", route_paths)
        self.assertIn("/execution-jobs/{job_id}/verification", route_paths)

    def test_execute_approval_creates_job_not_direct_execution(self):
        """POST /approvals/{id}/execute should create a job, not execute directly."""
        from xianyu_agent.server import app
        import inspect

        execute_handler = None
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/approvals/{approval_id}/execute":
                if hasattr(route, "endpoint"):
                    execute_handler = route.endpoint
                    break

        self.assertIsNotNone(execute_handler)
        source = inspect.getsource(execute_handler)
        # Should create a job, not call execute_approval_async directly
        self.assertNotIn("execute_approval_async", source)
        self.assertIn("execution_queue", source)


class ExecutionQueueJSTests(unittest.TestCase):
    def test_js_contains_execution_job_ui(self):
        js_path = Path(__file__).resolve().parent.parent / "web" / "app.js"
        source = js_path.read_text(encoding="utf-8")
        self.assertIn("execution-jobs", source)
        self.assertIn("enqueueApproval", source)
        self.assertIn("retryJob", source)
        self.assertIn("cancelJob", source)
        self.assertIn("runNextJobs", source)
        self.assertIn("resolveJobVerification", source)
        self.assertIn("verification_required", source)


if __name__ == "__main__":
    unittest.main()
