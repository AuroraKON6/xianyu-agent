import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xianyu_agent.action_executor import ActionExecutor
from xianyu_agent.commercial_trial import CommercialTrialStore, STAGE_CODES, reconcile_trial
from xianyu_agent.execution_queue import ExecutionJobQueue
from xianyu_agent.message_intake import MessageIntakeLedger
from xianyu_agent.order_runtime import OrderRuntimeStore
from xianyu_agent.resale import ResaleManager
from xianyu_agent.work_orders import WorkOrderManager


class CommercialAlphaFirstOrderE2ETests(unittest.TestCase):
    def test_first_order_chain_crosses_real_managers_without_sensitive_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            trials = CommercialTrialStore(root / "commercial_trials.json")
            work_orders = WorkOrderManager(root / "work_orders.json")
            jobs = ExecutionJobQueue(root / "execution_jobs.json")
            intake = MessageIntakeLedger(root / "message_intake.json")
            runtime_orders = OrderRuntimeStore(root / "order_runtime.json")
            resale = ResaleManager(root / "resale")
            executor = ActionExecutor()
            executor.register_handler(
                "publish_listing",
                lambda approval, context: {
                    "ok": True,
                    "cookie_id": "seller_alpha",
                    "publish_result": {"ok": True, "external_id": "item_alpha_1"},
                },
            )
            executor.register_handler(
                "send_message",
                lambda approval, context: {"ok": True, "sent": True},
            )

            candidate = resale.create_candidate({
                "title": "不锈钢商用工作台 1200x600",
                "description": "304 不锈钢，尺寸下单前复核",
                "source_platform": "pdd",
                "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=alpha",
                "supplier_price": 500,
                "supplier_shipping": 20,
                "target_sale_price": 620,
                "platform_fee_rate": 0.0323,
                "expected_refund_rate": 0,
                "category": "商用厨具",
                "images": ["https://example.com/owned-worktable.jpg"],
                "specifications": {"material": "304", "size": "1200x600"},
            })
            resale.verify_candidate(candidate["candidate_id"], {
                "supplier_verified": True,
                "supplier_verification_note": "已核对报价、库存、发货和破损责任",
                "supplier_quote_checked_at": time.time(),
                "stock_checked_at": time.time(),
                "fulfillment_verified": True,
                "fulfillment_verification_note": "48 小时内发货，供应商承担错货破损",
                "image_rights_type": "owned",
                "image_rights_evidence": "operator-owned-photo-001",
            })
            resale.set_decision(candidate["candidate_id"], "approve", "首批只发布一个规格")

            trial = trials.create_trial(
                cookie_id="seller_alpha",
                category="商用厨具",
                controls={
                    "max_candidates": 1,
                    "target_candidates": 1,
                    "max_published": 1,
                    "target_inquiries": 1,
                    "target_paid_orders": 1,
                    "cash_budget": 1000,
                },
                candidate_ids=[candidate["candidate_id"]],
            )
            trial_id = trial["trial_id"]

            publish_work = work_orders.create_work_order(
                kind="resale_listing",
                title="首批商品发布",
                owner="resale",
                context={
                    "commercial_trial_id": trial_id,
                    "cookie_id": "seller_alpha",
                    "candidate_id": candidate["candidate_id"],
                },
                risk_level="high",
            )
            work_orders.create_action_plan(
                publish_work["work_order_id"],
                proposed_actions=[{
                    "agent": "listing",
                    "action": "publish_listing",
                    "description": "发布首批商品",
                    "params": {
                        "candidate_id": candidate["candidate_id"],
                        "cookie_id": "seller_alpha",
                        "draft_id": "draft_alpha_1",
                    },
                }],
            )
            publish_approval = work_orders.get_work_order(publish_work["work_order_id"])["approvals"][0]
            work_orders.decide_approval(publish_approval["approval_id"], "approved", "证据与预算已复核")
            jobs.create_job(publish_approval["approval_id"], work_orders)
            jobs.run_pending(executor, work_orders, {})

            message_work = work_orders.create_work_order(
                kind="customer_message",
                title="真实询盘回复",
                owner="customer_service",
                context={"cookie_id": "seller_alpha", "item_id": "item_alpha_1"},
                risk_level="normal",
            )
            work_orders.create_action_plan(
                message_work["work_order_id"],
                proposed_actions=[{
                    "agent": "customer_service",
                    "action": "send_message",
                    "description": "发送已核对的规格回复",
                    "params": {
                        "cookie_id": "seller_alpha",
                        "item_id": "item_alpha_1",
                        "buyer_id": "buyer-secret-must-not-leak",
                        "message": "可以发，拍前请确认尺寸。",
                    },
                }],
            )
            message_approval = work_orders.get_work_order(message_work["work_order_id"])["approvals"][0]
            intake_record = intake.record_created(
                event_key="message_id:browser:seller_alpha:msg_alpha_1",
                source="browser",
                buyer_id="buyer-secret-must-not-leak",
                chat_id="chat-secret-must-not-leak",
                item_id="item_alpha_1",
                incoming_message_summary="buyer-message-must-not-leak",
                reply_summary="reply-summary-must-not-leak",
                risk_flags=[],
                work_order_id=message_work["work_order_id"],
                approval_id=message_approval["approval_id"],
                cookie_id="seller_alpha",
            )
            work_orders.decide_approval(message_approval["approval_id"], "approved", "规格和交付承诺已核对")
            jobs.create_job(message_approval["approval_id"], work_orders)
            jobs.run_pending(executor, work_orders, {})

            runtime_orders.upsert_order(
                order_id="xy_alpha_order_1",
                cookie_id="seller_alpha",
                item_id="item_alpha_1",
                buyer_id="buyer-secret-must-not-leak",
                amount=620,
                status="paid",
                source="browser",
            )
            runtime_orders.acquire_fulfillment_lock(
                "xy_alpha_order_1",
                owner=f"commercial_trial:{trial_id}",
                reason="首单履约",
            )

            plan = resale.create_plan(candidate["candidate_id"])
            purchase_draft = resale.create_purchase_draft(
                plan["plan_id"],
                quantity=1,
                buyer_address_summary="上海浦东新区***",
                commercial_trial_id=trial_id,
                xianyu_order_id="xy_alpha_order_1",
                cookie_id="seller_alpha",
                item_id="item_alpha_1",
                sale_amount=620,
                fulfillment_lock_owner=f"commercial_trial:{trial_id}",
            )
            purchase_work = work_orders.create_work_order(
                kind="resale_purchase",
                title="首单采购交接",
                owner="resale",
                context={
                    "commercial_trial_id": trial_id,
                    "cookie_id": "seller_alpha",
                    "candidate_id": candidate["candidate_id"],
                    "xianyu_order_id": "xy_alpha_order_1",
                },
                risk_level="high",
            )
            work_orders.create_action_plan(
                purchase_work["work_order_id"],
                proposed_actions=[{
                    "agent": "resale",
                    "action": "prepare_purchase_draft",
                    "description": "建立人工采购任务",
                    "params": {
                        "draft_id": purchase_draft["draft_id"],
                        "cookie_id": "seller_alpha",
                    },
                }],
            )
            purchase_approval = work_orders.get_work_order(purchase_work["work_order_id"])["approvals"][0]
            work_orders.decide_approval(purchase_approval["approval_id"], "approved", "库存和付款金额已复核")
            trials.record_fulfillment_handoff(
                trial_id,
                {
                    "order_id": "xy_alpha_order_1",
                    "item_id": "item_alpha_1",
                    "candidate_id": candidate["candidate_id"],
                    "plan_id": plan["plan_id"],
                    "purchase_draft_id": purchase_draft["draft_id"],
                    "work_order_id": purchase_work["work_order_id"],
                    "approval_id": purchase_approval["approval_id"],
                    "lock_owner": f"commercial_trial:{trial_id}",
                    "quantity": 1,
                    "total_cost": 520,
                    "created_at": time.time(),
                },
            )
            purchase_task = resale.create_purchase_task(purchase_draft)
            resale.update_purchase_task(purchase_task["purchase_task_id"], {
                "status": "ordered",
                "supplier_order_id": "supplier_alpha_order_1",
                "tracking_number": "supplier_alpha_tracking_1",
                "manual_order_note": "供应商实付 500 元，另计运费 20 元",
                "ordered_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            })
            resale_order = resale.create_order_from_task(
                purchase_task["purchase_task_id"],
                overrides={"sale_amount": 620, "shipping_cost": 20, "platform_fee": 20},
            )
            resale.update_resale_order(resale_order["resale_order_id"], {
                "status": "shipped",
                "xianyu_tracking_number": "xianyu_alpha_tracking_1",
            })
            runtime_orders.update_status("xy_alpha_order_1", "shipped", context="platform sync")
            runtime_orders.update_status("xy_alpha_order_1", "delivered", context="platform sync")
            resale.update_resale_order(resale_order["resale_order_id"], {
                "status": "completed",
                "sale_amount": 620,
                "supplier_cost": 500,
                "shipping_cost": 20,
                "platform_fee": 20,
                "financial_evidence_note": "已按平台实收和供应商账单逐项核对",
                "financial_evidence_ref": "screenshots/alpha-first-order-ledger.png",
                "financial_reconciled_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            })
            trials.decide(
                trial_id,
                "complete",
                "首单履约及利润已复核",
                allow_complete=True,
                review_eligible=True,
            )

            reconciled = reconcile_trial(
                trial=trials.get_trial(trial_id),
                account={
                    "cookie_id": "seller_alpha",
                    "configured": True,
                    "enabled": True,
                    "prompt_ready": True,
                    "sync_current": True,
                    "runtime_connected": True,
                    "runtime_fresh": True,
                    "runtime_unblocked": True,
                },
                candidates=resale.list_candidates(),
                work_orders=work_orders.list_work_orders(limit=100),
                jobs=jobs.list_jobs(limit=100),
                intake_records=intake.list_recent(limit=100),
                runtime_orders=runtime_orders.list_orders(limit=100),
                resale_orders=resale.list_resale_orders(),
                preflight_results={candidate["candidate_id"]: {"ready": True}},
            )

            self.assertEqual(tuple(stage["code"] for stage in reconciled["stages"]), STAGE_CODES)
            self.assertTrue(
                all(stage["status"] == "pass" for stage in reconciled["stages"]),
                {"stages": reconciled["stages"], "orders": resale.list_resale_orders()},
            )
            self.assertEqual(reconciled["inquiry_count"], 1)
            self.assertEqual(reconciled["inquiry_response_count"], 1)
            self.assertEqual(reconciled["paid_order_count"], 1)
            self.assertEqual(reconciled["realized_profit"], 80)
            self.assertFalse(reconciled["fulfillment_gaps"])
            self.assertFalse(reconciled["profit_reconciliation_gaps"])
            rendered = json.dumps(reconciled, ensure_ascii=False)
            self.assertNotIn("buyer-secret-must-not-leak", rendered)
            self.assertNotIn("chat-secret-must-not-leak", rendered)
            self.assertNotIn("buyer-message-must-not-leak", rendered)
            self.assertNotIn("reply-summary-must-not-leak", rendered)
