from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class FakeWorkflow:
    def __init__(self, reply: str = "Yes, this is available."):
        self.reply = reply

    def generate_auto_reply(self, **kwargs):
        return self.reply


class AccountAttributionTests(unittest.TestCase):
    def test_work_order_approvals_inherit_cookie_id_from_context(self):
        from xianyu_agent.work_orders import WorkOrderManager

        manager = WorkOrderManager(Path(tempfile.mkdtemp()) / "work_orders.json")
        work_order = manager.create_work_order(
            kind="listing",
            title="publish candidate",
            owner="listing",
            context={"cookie_id": "seller_a", "candidate_id": "cand_1"},
        )

        manager.create_action_plan(
            work_order["work_order_id"],
            proposed_actions=[
                {
                    "agent": "listing",
                    "action": "publish_listing",
                    "description": "publish",
                    "params": {"candidate_id": "cand_1"},
                    "draft_snapshot": {"title": "candidate title"},
                }
            ],
        )

        stored = manager.get_work_order(work_order["work_order_id"])
        approval = stored["approvals"][0]
        self.assertEqual(approval["payload"]["cookie_id"], "seller_a")
        self.assertEqual(approval["draft_snapshot"]["cookie_id"], "seller_a")

    def test_work_order_route_defaults_to_single_configured_account(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.work_orders import WorkOrderManager

        db = DBManager(str(Path(tempfile.mkdtemp()) / "accounts.db"))
        db.save_cookie("seller_auto", "raw-cookie-secret", "only account")
        original_db = server.db_manager
        original_manager = server.work_order_manager
        server.db_manager = db
        server.work_order_manager = WorkOrderManager(Path(tempfile.mkdtemp()) / "work_orders.json")
        try:
            client = TestClient(server.app)
            response = client.post(
                "/work-orders",
                json={"kind": "listing", "title": "candidate", "owner": "listing", "context": {}},
            )
        finally:
            server.db_manager = original_db
            server.work_order_manager = original_manager

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["context"]["cookie_id"], "seller_auto")
        self.assertNotIn("raw-cookie-secret", response.text)

    def test_ecommerce_listing_draft_defaults_to_single_configured_account(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.work_orders import WorkOrderManager

        db = DBManager(str(Path(tempfile.mkdtemp()) / "accounts.db"))
        db.save_cookie("seller_auto", "raw-cookie-secret", "only account")
        original_db = server.db_manager
        original_manager = server.work_order_manager
        server.db_manager = db
        server.work_order_manager = WorkOrderManager(Path(tempfile.mkdtemp()) / "work_orders.json")
        try:
            req = server.EcommerceListingDraftRequest(
                candidate_id="CAND-ATTR-1",
                title="Commercial kitchen table",
                description="Stainless work table",
                price_cny=799,
                stock=1,
            )
            result = asyncio.run(server.create_ecommerce_listing_draft(req))
        finally:
            server.db_manager = original_db
            server.work_order_manager = original_manager

        work_order = result["work_order"]
        approval = work_order["approvals"][0]
        self.assertEqual(work_order["context"]["cookie_id"], "seller_auto")
        self.assertEqual(approval["payload"]["cookie_id"], "seller_auto")
        self.assertEqual(approval["draft_snapshot"]["cookie_id"], "seller_auto")
        self.assertNotIn("raw-cookie-secret", str(result))

    def test_inbound_message_defaults_to_single_configured_account(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        tmp = Path(tempfile.mkdtemp())
        db = DBManager(str(tmp / "accounts.db"))
        db.save_cookie("seller_auto", "raw-cookie-secret", "only account")
        original_db = server.db_manager
        original_manager = server.work_order_manager
        original_ledger = server.message_intake_ledger
        original_workflow = server.workflow
        server.db_manager = db
        server.work_order_manager = WorkOrderManager(tmp / "work_orders.json")
        server.message_intake_ledger = MessageIntakeLedger(tmp / "message_intake.json")
        server.workflow = FakeWorkflow()
        try:
            client = TestClient(server.app)
            response = client.post(
                "/workflows/messages/inbound",
                json={
                    "buyer_id": "buyer_attr",
                    "message": "Still available?",
                    "chat_id": "chat_attr",
                    "item_id": "item_attr",
                },
            )
        finally:
            server.db_manager = original_db
            server.work_order_manager = original_manager
            server.message_intake_ledger = original_ledger
            server.workflow = original_workflow

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        work_order = data["work_order"]
        approval = work_order["approvals"][0]
        self.assertEqual(work_order["context"]["cookie_id"], "seller_auto")
        self.assertEqual(approval["payload"]["cookie_id"], "seller_auto")
        self.assertEqual(approval["draft_snapshot"]["cookie_id"], "seller_auto")
        self.assertEqual(data["intake_record"]["cookie_id"], "seller_auto")
        self.assertNotIn("raw-cookie-secret", response.text)

    def test_repair_account_attribution_dry_run_then_apply(self):
        from xianyu_agent.account_attribution import repair_work_order_account_attribution
        from xianyu_agent.work_orders import WorkOrderManager

        manager = WorkOrderManager(Path(tempfile.mkdtemp()) / "work_orders.json")
        work_order = manager.create_work_order(
            kind="listing",
            title="legacy unassigned",
            owner="listing",
            context={"candidate_id": "legacy_1"},
        )
        manager.create_action_plan(
            work_order["work_order_id"],
            proposed_actions=[
                {
                    "agent": "listing",
                    "action": "publish_listing",
                    "description": "publish",
                    "params": {"candidate_id": "legacy_1"},
                    "draft_snapshot": {"title": "legacy title"},
                }
            ],
        )

        preview = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_auto"],
            dry_run=True,
        )
        stored_after_preview = manager.get_work_order(work_order["work_order_id"])
        self.assertEqual(preview["target_cookie_id"], "seller_auto")
        self.assertEqual(preview["planned_updates"], 1)
        self.assertTrue(preview["preview_token"])
        self.assertEqual(preview["items"][0]["work_order_id"], work_order["work_order_id"])
        self.assertNotIn("cookie_id", stored_after_preview["context"])

        implicit_apply = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_auto"],
            dry_run=False,
        )
        self.assertEqual(implicit_apply["skipped_reason"], "work_order_ids_required")

        applied = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_auto"],
            dry_run=False,
            work_order_ids=[work_order["work_order_id"]],
            preview_token=preview["preview_token"],
            confirmation="CLAIM 1",
        )
        stored_after_apply = manager.get_work_order(work_order["work_order_id"])
        approval = stored_after_apply["approvals"][0]
        self.assertEqual(applied["updated"], 1)
        self.assertEqual(stored_after_apply["context"]["cookie_id"], "seller_auto")
        self.assertEqual(approval["payload"]["cookie_id"], "seller_auto")
        self.assertEqual(approval["draft_snapshot"]["cookie_id"], "seller_auto")

    def test_repair_account_attribution_skips_ambiguous_accounts_without_target(self):
        from xianyu_agent.account_attribution import repair_work_order_account_attribution
        from xianyu_agent.work_orders import WorkOrderManager

        manager = WorkOrderManager(Path(tempfile.mkdtemp()) / "work_orders.json")
        work_order = manager.create_work_order(
            kind="listing",
            title="legacy unassigned",
            owner="listing",
            context={"candidate_id": "legacy_2"},
        )

        result = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_a", "seller_b"],
            dry_run=False,
        )
        stored = manager.get_work_order(work_order["work_order_id"])
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped_reason"], "target_account_required")
        self.assertNotIn("cookie_id", stored["context"])

    def test_account_attribution_repair_route_uses_configured_accounts(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.work_orders import WorkOrderManager

        tmp = Path(tempfile.mkdtemp())
        db = DBManager(str(tmp / "accounts.db"))
        db.save_cookie("seller_auto", "raw-cookie-secret", "only account")
        manager = WorkOrderManager(tmp / "work_orders.json")
        manager.create_work_order(kind="listing", title="legacy", owner="listing", context={})
        original_db = server.db_manager
        original_manager = server.work_order_manager
        server.db_manager = db
        server.work_order_manager = manager
        try:
            client = TestClient(server.app)
            preview = client.post("/dashboard/account-queues/repair-attribution", json={"dry_run": True})
            preview_data = preview.json()
            implicit = client.post("/dashboard/account-queues/repair-attribution", json={"dry_run": False})
            applied = client.post(
                "/dashboard/account-queues/repair-attribution",
                json={
                    "dry_run": False,
                    "mode": "claim",
                    "work_order_ids": [preview_data["items"][0]["work_order_id"]],
                    "preview_token": preview_data["preview_token"],
                    "confirmation": "CLAIM 1",
                },
            )
        finally:
            server.db_manager = original_db
            server.work_order_manager = original_manager

        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["planned_updates"], 1)
        self.assertEqual(implicit.status_code, 409, implicit.text)
        self.assertEqual(implicit.json()["detail"]["reason"], "work_order_ids_required")
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["updated"], 1)
        self.assertNotIn("raw-cookie-secret", applied.text)

    def test_quarantine_unassigned_work_cancels_pending_execution_and_reply(self):
        from xianyu_agent.account_attribution import repair_work_order_account_attribution
        from xianyu_agent.execution_queue import ExecutionJobQueue
        from xianyu_agent.message_intake import MessageIntakeLedger
        from xianyu_agent.work_orders import WorkOrderManager

        tmp = Path(tempfile.mkdtemp())
        manager = WorkOrderManager(tmp / "work_orders.json")
        queue = ExecutionJobQueue(tmp / "execution_jobs.json")
        intake = MessageIntakeLedger(tmp / "message_intake.json")
        work_order = manager.create_work_order(
            kind="customer_message",
            title="legacy unassigned buyer reply",
            owner="customer_service",
            context={},
        )
        manager.create_action_plan(
            work_order["work_order_id"],
            proposed_actions=[{
                "agent": "customer_service",
                "action": "send_message",
                "description": "reply",
                "params": {},
                "draft_snapshot": {"message_summary": "legacy reply"},
            }],
        )
        approval_id = manager.get_work_order(work_order["work_order_id"])["approvals"][0]["approval_id"]
        manager.decide_approval(approval_id, "approved", "legacy approval")
        job = queue.create_job(approval_id, manager)
        record = intake.record_created(
            event_key="legacy-event",
            source="browser",
            buyer_id="buyer-secret",
            chat_id="chat-secret",
            item_id="item-1",
            incoming_message_summary="private incoming text",
            reply_summary="legacy reply",
            risk_flags=[],
            work_order_id=work_order["work_order_id"],
            approval_id=approval_id,
        )

        preview = repair_work_order_account_attribution(
            work_order_manager=manager,
            execution_queue=queue,
            message_intake_ledger=intake,
            account_ids=["seller_live"],
            dry_run=True,
        )
        self.assertNotIn("buyer-secret", str(preview))
        self.assertNotIn("private incoming text", str(preview))

        result = repair_work_order_account_attribution(
            work_order_manager=manager,
            execution_queue=queue,
            message_intake_ledger=intake,
            account_ids=["seller_live"],
            dry_run=False,
            mode="quarantine",
            work_order_ids=[work_order["work_order_id"]],
            preview_token=preview["preview_token"],
            confirmation="QUARANTINE 1",
        )

        stored = manager.get_work_order(work_order["work_order_id"])
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(stored["status"], "quarantined")
        self.assertEqual(stored["context"]["account_attribution_state"], "quarantined")
        self.assertEqual(stored["approvals"][0]["status"], "rejected")
        self.assertEqual(queue.get_job(job["job_id"])["status"], "cancelled")
        self.assertEqual(intake.get_record(record["intake_id"])["status"], "quarantined")

    def test_repair_rejects_stale_preview_token(self):
        from xianyu_agent.account_attribution import repair_work_order_account_attribution
        from xianyu_agent.work_orders import WorkOrderManager

        manager = WorkOrderManager(Path(tempfile.mkdtemp()) / "work_orders.json")
        work_order = manager.create_work_order(kind="listing", title="legacy", owner="listing", context={})
        preview = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_live"],
            dry_run=True,
        )
        manager.create_work_order(kind="listing", title="new legacy", owner="listing", context={})

        result = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_live"],
            dry_run=False,
            work_order_ids=[work_order["work_order_id"]],
            preview_token=preview["preview_token"],
            confirmation="CLAIM 1",
        )

        self.assertEqual(result["skipped_reason"], "stale_preview_token")
        self.assertNotIn("cookie_id", manager.get_work_order(work_order["work_order_id"])["context"])

    def test_unknown_default_account_is_reviewed_and_explicitly_replaced(self):
        from xianyu_agent.account_attribution import repair_work_order_account_attribution
        from xianyu_agent.work_orders import WorkOrderManager

        manager = WorkOrderManager(Path(tempfile.mkdtemp()) / "work_orders.json")
        work_order = manager.create_work_order(
            kind="listing",
            title="legacy default account",
            owner="listing",
            context={"cookie_id": "default"},
        )
        preview = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_live"],
            dry_run=True,
        )

        self.assertEqual(preview["planned_updates"], 1)
        self.assertEqual(preview["items"][0]["attribution_issue"], "unknown_cookie_id")
        self.assertEqual(preview["items"][0]["source_cookie_ids"], ["default"])

        result = repair_work_order_account_attribution(
            work_order_manager=manager,
            account_ids=["seller_live"],
            dry_run=False,
            work_order_ids=[work_order["work_order_id"]],
            preview_token=preview["preview_token"],
            confirmation="CLAIM 1",
        )

        self.assertEqual(result["claimed"], 1)
        self.assertEqual(manager.get_work_order(work_order["work_order_id"])["context"]["cookie_id"], "seller_live")

    def test_resale_listing_requires_and_persists_account_when_multiple_accounts_exist(self):
        from xianyu_agent import server
        from xianyu_agent.db_manager import DBManager
        from xianyu_agent.resale import ResaleManager
        from xianyu_agent.work_orders import WorkOrderManager
        from xianyu_agent.account_runtime import AccountRuntimeStore
        from preflight_helpers import (
            add_preflight_account,
            build_commercial_terms_store,
            make_preflight_ready_candidate,
        )

        tmp = Path(tempfile.mkdtemp())
        db = DBManager(str(tmp / "accounts.db"))
        add_preflight_account(db, "seller_a")
        add_preflight_account(db, "seller_b")
        runtime = AccountRuntimeStore(tmp / "runtime.json")
        runtime.record_authenticated_activity("seller_a")
        runtime.record_authenticated_activity("seller_b")
        resale = ResaleManager(tmp / "resale")
        work_orders = WorkOrderManager(tmp / "work_orders.json")
        candidate = make_preflight_ready_candidate(resale)
        candidate_id = candidate["candidate_id"]
        terms = build_commercial_terms_store(tmp, resale, candidate, "seller_b")

        with patch.object(server, "db_manager", db), patch.object(
            server, "account_runtime_store", runtime
        ), patch.object(server, "account_commercial_terms_store", terms), patch.object(
            server, "resale_manager", resale
        ), patch.object(
            server, "work_order_manager", work_orders
        ):
            client = TestClient(server.app)
            ambiguous = client.post(f"/resale/candidates/{candidate_id}/listing-draft")
            assigned = client.post(
                f"/resale/candidates/{candidate_id}/listing-draft?cookie_id=seller_b"
            )

        self.assertEqual(ambiguous.status_code, 409, ambiguous.text)
        self.assertEqual(assigned.status_code, 200, assigned.text)
        work_order = assigned.json()["work_order"]
        approval = work_order["approvals"][0]
        self.assertEqual(work_order["context"]["cookie_id"], "seller_b")
        self.assertEqual(approval["payload"]["cookie_id"], "seller_b")
        self.assertEqual(approval["draft_snapshot"]["cookie_id"], "seller_b")
        self.assertNotIn("cookie-value-seller_b", assigned.text)


if __name__ == "__main__":
    unittest.main()
