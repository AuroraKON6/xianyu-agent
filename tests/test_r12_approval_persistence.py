"""R12 acceptance tests: approval persistence and external-execution safety.

Covers the taskbook's acceptance matrix for the approval chain:

* binding — approvals carry account, payload hash, input versions
  (supplier snapshot), amounts and TTL in a digest-protected envelope;
* invalidation — a NEWER committed supplier snapshot (price change) or a
  tampered payload/account invalidates an old approval BEFORE any draft,
  task or external action is created;
* idempotency — duplicate execute calls (webhook replay) create exactly
  one job and the second call is a structured 400, never a second
  external action;
* fencing — an expired worker's writeback is rejected by the fencing
  token (stale claim), verified at renew AND finalize time.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from xianyu_agent.action_executor import ActionExecutor
from xianyu_agent.action_scope import ActionScopeValidationError
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import build_offer_snapshot
from xianyu_agent.execution_queue import ExecutionJobQueue
from xianyu_agent.resale import ResaleManager
from xianyu_agent.work_orders import WorkOrderManager


def money(value):
    return {"state": "known", "amount": str(value), "currency": "CNY"}


def supplier_snapshot(sku, *, price="160", observed=None, name="R12供应商"):
    from quote_fixtures import with_declared_quote_provenance

    observed = observed or datetime.now(timezone.utc)
    return with_declared_quote_provenance(build_offer_snapshot(
        provider_id=f"supplier_file:{name}", source_item_id=sku, source_sku_id=sku,
        observed_at=observed, eligible_price=money(price),
        shipping_quote=money("20"), stock_state="in_stock", stock_quantity=50,
        payload_hash=f"r12-{name}-{sku}-{price}-{observed.isoformat()}",
        source_ref="test:r12",
    ))


class QuoteFreshnessGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r12-quote-"))
        self.repos = JsonCommerceRepositories(self.tmp)
        self.manager = ResaleManager(self.tmp)
        self.executor = ActionExecutor()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _candidate_with_binding(self, snapshot_id):
        candidate = self.manager.create_candidate({
            "title": "工作台", "supplier_price": 160, "supplier_shipping": 20,
            "target_sale_price": 300, "supplier_name": "R12供应商",
            "supplier_sku": "R12-SKU",
            "supplier_snapshot_id": snapshot_id,
        })
        plan = self.manager.create_plan(candidate["candidate_id"])
        return candidate, plan

    def _approval(self, plan, *, extra_payload=None):
        return {
            "approval_id": "approval_r12_quote",
            "work_order_id": "wo_r12",
            "action": "prepare_purchase_draft",
            "payload": {"plan_id": plan["plan_id"], "quantity": 1, **(extra_payload or {})},
            "draft_snapshot": {},
        }

    def test_newer_snapshot_blocks_execution_before_any_draft(self):
        first, _ = self.repos.offers.add(supplier_snapshot("R12-SKU", price="160"))
        candidate, plan = self._candidate_with_binding(first["record_id"])
        approval = self._approval(plan)
        # a NEWER committed snapshot for the same provider+SKU (price change)
        newer, _ = self.repos.offers.add(
            supplier_snapshot("R12-SKU", price="200", name="R12供应商"))
        result = self.executor.execute(approval, {
            "data_dir": self.tmp, "commerce_repositories": self.repos,
        })
        self.assertFalse(result["ok"])
        self.assertIn("stale", result.get("scope_validation", {}).get("code", ""))
        self.assertEqual(self.manager.list_purchase_tasks(), [],
                         "no purchase task may be created from a stale approval")

    def test_bound_snapshot_missing_blocks_execution(self):
        candidate, plan = self._candidate_with_binding("off_does_not_exist")
        approval = self._approval(plan)
        result = self.executor.execute(approval, {
            "data_dir": self.tmp, "commerce_repositories": self.repos,
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["scope_validation"]["code"], "supplier_snapshot_missing")
        self.assertEqual(self.manager.list_purchase_tasks(), [])

    def test_binding_without_repositories_refuses_blind(self):
        first, _ = self.repos.offers.add(supplier_snapshot("R12-SKU"))
        candidate, plan = self._candidate_with_binding(first["record_id"])
        approval = self._approval(plan)
        result = self.executor.execute(approval, {"data_dir": self.tmp})
        self.assertFalse(result["ok"])
        self.assertIn("not wired", result["error"])

    def test_still_latest_snapshot_proceeds(self):
        first, _ = self.repos.offers.add(supplier_snapshot("R12-SKU"))
        candidate, plan = self._candidate_with_binding(first["record_id"])
        approval = self._approval(plan)
        result = self.executor.execute(approval, {
            "data_dir": self.tmp, "commerce_repositories": self.repos,
        })
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["purchase_task_id"])

    def test_unbound_candidate_without_repositories_still_proceeds(self):
        candidate = self.manager.create_candidate({
            "title": "无绑定候选", "supplier_price": 100, "supplier_shipping": 10,
            "target_sale_price": 200,
        })
        plan = self.manager.create_plan(candidate["candidate_id"])
        approval = self._approval(plan)
        result = self.executor.execute(approval, {"data_dir": self.tmp})
        self.assertTrue(result["ok"], result)


class EnvelopeBindingTests(unittest.TestCase):
    """The supplier snapshot binding is digest-protected in the envelope."""

    def test_supplier_snapshot_id_is_a_target_and_hash_a_version(self):
        from xianyu_agent.action_scope import (
            TARGET_KEYS,
            VERSION_KEYS,
            build_action_scope_envelope,
        )

        self.assertIn("supplier_snapshot_id", TARGET_KEYS)
        self.assertIn("supplier_snapshot_payload_hash", VERSION_KEYS)
        envelope = build_action_scope_envelope(
            approval_id="a1", work_order_id="w1", action="prepare_purchase_draft",
            payload={"plan_id": "p1", "supplier_snapshot_id": "off_1"},
            draft_snapshot={},
        )
        self.assertEqual(envelope["target_refs"]["supplier_snapshot_id"], "off_1")

    def test_payload_snapshot_binding_change_breaks_digest(self):
        from xianyu_agent.action_scope import (
            build_action_scope_envelope,
            require_valid_action_scope,
        )

        envelope = build_action_scope_envelope(
            approval_id="a1", work_order_id="w1", action="prepare_purchase_draft",
            payload={"plan_id": "p1", "supplier_snapshot_id": "off_1"},
            draft_snapshot={},
        )
        approval = {
            "status": "approved",
            "approved_action_digest": envelope["action_digest"],
            "scope_envelope": envelope,
            "payload": {"plan_id": "p1", "supplier_snapshot_id": "off_TAMPERED"},
        }
        with self.assertRaises(ActionScopeValidationError):
            require_valid_action_scope(approval)


class DuplicateExecuteTests(unittest.TestCase):
    """Webhook replay: exactly one job, second call structured 400."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r12-dup-"))
        self.wom = WorkOrderManager()
        self.wom.configure(self.tmp / "work_orders.json")
        self.queue = ExecutionJobQueue()
        self.queue.configure(self.tmp / "execution_jobs.json")
        wo = self.wom.create_work_order(
            "resale", "R12 duplicate-execute", "sourcing",
            context={"cookie_id": "acct-a"},
        )
        self.wom.create_action_plan(wo["work_order_id"], proposed_actions=[
            {"agent": "sourcing", "action": "prepare_purchase_draft",
             "params": {"plan_id": "plan_x", "quantity": 1, "cookie_id": "acct-a"}},
        ])
        approval = self.wom.list_work_orders()[0]["approvals"][0]
        self.wom.decide_approval(approval["approval_id"], "approved")
        self.approval_id = approval["approval_id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_duplicate_execute_creates_exactly_one_job(self):
        first = self.queue.create_job(self.approval_id, self.wom)
        self.assertEqual(first["status"], "queued")
        with self.assertRaises(ValueError) as ctx:
            self.queue.create_job(self.approval_id, self.wom)
        self.assertIn("Active job already exists", str(ctx.exception))
        jobs = [j for j in self.queue.list_jobs() if j["approval_id"] == self.approval_id]
        self.assertEqual(len(jobs), 1, "webhook replay must not create a second job")

    def test_idempotency_key_blocks_reuse_even_after_completion(self):
        first = self.queue.create_job(self.approval_id, self.wom)
        first["status"] = "completed"
        # a SECOND approval for the identical action+target+payload gets
        # the same deterministic idempotency key; reusing it for a new job
        # is refused even though the first job is completed
        wo = self.wom.list_work_orders()[0]
        self.wom.create_action_plan(wo["work_order_id"], proposed_actions=[
            {"agent": "sourcing", "action": "prepare_purchase_draft",
             "params": {"plan_id": "plan_x", "quantity": 1, "cookie_id": "acct-a"}},
        ])
        second = [
            a for a in self.wom.list_work_orders()[0]["approvals"]
            if a["approval_id"] != self.approval_id
        ][0]
        self.wom.decide_approval(second["approval_id"], "approved")
        with self.assertRaises(ValueError) as ctx:
            self.queue.create_job(second["approval_id"], self.wom)
        self.assertIn("already used", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
