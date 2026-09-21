"""WP10 operating mandates, policy engine and budget reservation tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from xianyu_agent.budget_reservations import (
    BudgetError,
    BudgetReservationStore,
    ShadowAuthorizationLog,
)
from xianyu_agent.operating_mandates import (
    ActionRequest,
    MandateError,
    OperatingMandateStore,
    PolicyDecision,
    evaluate_action,
    today_key,
)

UTC = timezone.utc


def money(amount, currency="CNY"):
    return {"state": "known", "amount": str(amount), "currency": currency}


class MandateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = OperatingMandateStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _issue(self, **overrides):
        params = dict(
            operator="操作者",
            account_id="acc-1",
            action_types=["faq_reply", "price_adjust"],
            valid_until=datetime.now(UTC) + timedelta(days=7),
        )
        params.update(overrides)
        return self.store.issue(**params)

    def test_issue_and_active_window(self):
        mandate = self._issue()
        self.assertTrue(self.store.is_active(mandate))
        expired = self._issue(valid_until=datetime.now(UTC) - timedelta(hours=1))
        self.assertFalse(self.store.is_active(expired))
        future = self._issue(
            valid_from=datetime.now(UTC) + timedelta(hours=1),
            valid_until=datetime.now(UTC) + timedelta(days=7),
        )
        self.assertFalse(self.store.is_active(future))
        self.assertEqual(len(self.store.list_active()), 1)

    def test_requires_operator(self):
        with self.assertRaises(MandateError):
            self._issue(operator="  ")

    def test_revoke_blocks_active_list(self):
        mandate = self._issue()
        revoked = self.store.revoke(mandate["record_id"], operator="操作者", reason="策略变更")
        self.assertIsNotNone(revoked["revoked_at"])
        self.assertEqual(self.store.list_active(), [])
        with self.assertRaises(MandateError):
            self.store.revoke(mandate["record_id"], operator="操作者", reason="again")


class PolicyEngineTests(unittest.TestCase):
    def _mandate(self, **overrides):
        base = {
            "record_id": "mandate_1",
            "revision": 1,
            "account_id": "acc-1",
            "action_types": ["faq_reply", "price_adjust", "procurement_order"],
            "sku_scope": [],
            "category_scope": [],
            "per_transaction_limit": money("500"),
            "daily_limit": money("1000"),
            "price_floor": money("200"),
            "price_ceiling": money("400"),
            "max_actions": None,
            "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        }
        base.update(overrides)
        return base

    def test_allowed_when_all_checks_pass(self):
        decision = evaluate_action(
            ActionRequest(action_type="price_adjust", account_id="acc-1", amount=money("80"), listing_price=money("299")),
            active_mandates=[self._mandate()],
        )
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["matched_mandate"]["mandate_id"], "mandate_1")
        self.assertTrue(all(c["state"] == "pass" for c in decision["checks"]))

    def test_wrong_account_denied(self):
        decision = evaluate_action(
            ActionRequest(action_type="faq_reply", account_id="acc-OTHER"),
            active_mandates=[self._mandate()],
        )
        self.assertFalse(decision["allowed"])

    def test_action_type_out_of_scope_denied(self):
        decision = evaluate_action(
            ActionRequest(action_type="listing_publish", account_id="acc-1"),
            active_mandates=[self._mandate()],
        )
        self.assertFalse(decision["allowed"])

    def test_negative_amount_denied_regardless(self):
        decision = evaluate_action(
            ActionRequest(action_type="price_adjust", account_id="acc-1", amount=money("-5")),
            active_mandates=[self._mandate()],
        )
        self.assertFalse(decision["allowed"])
        self.assertIn("negative", " ".join(decision["reasons"]))

    def test_unknown_amount_not_authorized_for_money_actions(self):
        decision = evaluate_action(
            ActionRequest(action_type="procurement_order", account_id="acc-1", amount={"state": "unknown"}),
            active_mandates=[self._mandate()],
        )
        self.assertFalse(decision["allowed"])
        self.assertIn("unknown money", " ".join(decision["reasons"]))

    def test_mandate_without_limit_cannot_authorize_money(self):
        mandate = self._mandate(per_transaction_limit={"state": "unknown"})
        decision = evaluate_action(
            ActionRequest(action_type="procurement_order", account_id="acc-1", amount=money("100")),
            active_mandates=[mandate],
        )
        self.assertFalse(decision["allowed"])

    def test_price_floor_and_ceiling(self):
        below = evaluate_action(
            ActionRequest(action_type="price_adjust", account_id="acc-1", amount=money("10"), listing_price=money("150")),
            active_mandates=[self._mandate()],
        )
        self.assertFalse(below["allowed"])
        above = evaluate_action(
            ActionRequest(action_type="price_adjust", account_id="acc-1", amount=money("10"), listing_price=money("450")),
            active_mandates=[self._mandate()],
        )
        self.assertFalse(above["allowed"])

    def test_daily_limit_projection_includes_existing_usage(self):
        decision = evaluate_action(
            ActionRequest(action_type="procurement_order", account_id="acc-1", amount=money("600")),
            active_mandates=[self._mandate()],
            budget_usage_today={"mandate_1": 500.0},
        )
        self.assertFalse(decision["allowed"])
        ok = evaluate_action(
            ActionRequest(action_type="procurement_order", account_id="acc-1", amount=money("400")),
            active_mandates=[self._mandate()],
            budget_usage_today={"mandate_1": 500.0},
        )
        self.assertTrue(ok["allowed"])

    def test_per_transaction_limit(self):
        decision = evaluate_action(
            ActionRequest(action_type="procurement_order", account_id="acc-1", amount=money("600")),
            active_mandates=[self._mandate()],
        )
        self.assertFalse(decision["allowed"])

    def test_max_actions_count(self):
        mandate = self._mandate(max_actions=2)
        first = evaluate_action(
            ActionRequest(action_type="faq_reply", account_id="acc-1"),
            active_mandates=[mandate],
            mandate_action_counts={"mandate_1": 1},
        )
        self.assertTrue(first["allowed"])
        exhausted = evaluate_action(
            ActionRequest(action_type="faq_reply", account_id="acc-1"),
            active_mandates=[mandate],
            mandate_action_counts={"mandate_1": 2},
        )
        self.assertFalse(exhausted["allowed"])

    def test_revoked_mandate_not_in_active_list(self):
        # the engine only sees what the caller passes; the store's
        # list_active filters revoked/expired -- integration below
        decision = evaluate_action(
            ActionRequest(action_type="faq_reply", account_id="acc-1"),
            active_mandates=[],
        )
        self.assertFalse(decision["allowed"])
        self.assertIn("no active mandate", " ".join(decision["reasons"]))


class BudgetReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BudgetReservationStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_settled_actual_over_reservation_consumes_actual(self):
        """P1 regression: settle 140 after reserving 100 must consume 140."""
        record = self.store.reserve(
            account_id="acc-1", budget_bucket="procurement", job_id="job-s", amount=money("100"), limit=money("150")
        )
        self.store.settle(record["record_id"], actual_amount=money("140"))
        usage = self.store.usage(account_id="acc-1", bucket="procurement")
        self.assertEqual(Decimal(usage["settled"]), Decimal("140"))
        self.assertEqual(Decimal(usage["committed_total"]), Decimal("140"))
        # 140 + 50 > 150 must now be refused
        with self.assertRaises(BudgetError):
            self.store.reserve(
                account_id="acc-1", budget_bucket="procurement", job_id="job-t", amount=money("50"), limit=money("150")
            )
        # 10 still fits
        ok = self.store.reserve(
            account_id="acc-1", budget_bucket="procurement", job_id="job-u", amount=money("10"), limit=money("150")
        )
        self.assertEqual(ok["status"], "reserved")

    def test_reserve_and_usage(self):
        record = self.store.reserve(
            account_id="acc-1", budget_bucket="procurement", job_id="job-1", amount=money("300"), limit=money("1000")
        )
        self.assertEqual(record["status"], "reserved")
        usage = self.store.usage(account_id="acc-1", bucket="procurement")
        self.assertEqual(Decimal(usage["committed_total"]), Decimal("300"))

    def test_concurrent_over_limit_refused(self):
        self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-1", amount=money("700"), limit=money("1000"))
        with self.assertRaises(BudgetError) as ctx:
            self.store.reserve(
                account_id="acc-1", budget_bucket="procurement", job_id="job-2", amount=money("400"), limit=money("1000")
            )
        self.assertIn("exceeded", str(ctx.exception))

    def test_midnight_boundaries_are_separate_days(self):
        # 2026-09-18 15:59 UTC == 23:59 Shanghai; 16:01 UTC == next day
        before_midnight = datetime(2026, 9, 18, 15, 59, tzinfo=UTC)
        after_midnight = datetime(2026, 9, 18, 16, 1, tzinfo=UTC)
        self.store.reserve(
            account_id="acc-1", budget_bucket="procurement", job_id="job-1", amount=money("1000"), limit=money("1000"), now=before_midnight
        )
        # next Shanghai day has a fresh limit
        self.store.reserve(
            account_id="acc-1", budget_bucket="procurement", job_id="job-2", amount=money("1000"), limit=money("1000"), now=after_midnight
        )
        usage_before = self.store.usage(account_id="acc-1", bucket="procurement", day="2026-09-18")
        usage_after = self.store.usage(account_id="acc-1", bucket="procurement", day="2026-09-19")
        self.assertEqual(Decimal(usage_before["committed_total"]), Decimal("1000"))
        self.assertEqual(Decimal(usage_after["committed_total"]), Decimal("1000"))

    def test_reserve_idempotent_per_job_and_bucket(self):
        first = self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-1", amount=money("100"))
        again = self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-1", amount=money("100"))
        self.assertEqual(first["record_id"], again["record_id"])
        self.assertEqual(Decimal(self.store.usage(account_id="acc-1", bucket="procurement")["committed_total"]), Decimal("100"))

    def test_partial_settlement_records_actual(self):
        record = self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-1", amount=money("300"))
        settled = self.store.settle(record["record_id"], actual_amount=money("275.50"))
        self.assertEqual(settled["status"], "settled")
        self.assertEqual(settled["actual_amount"]["amount"], "275.50")
        with self.assertRaises(BudgetError):
            self.store.settle(record["record_id"], actual_amount=money("10"))

    def test_release_frees_usage(self):
        record = self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-1", amount=money("300"))
        self.store.release(record["record_id"], reason="action not executed")
        usage = self.store.usage(account_id="acc-1", bucket="procurement")
        self.assertEqual(Decimal(usage["committed_total"]), Decimal("0"))
        self.assertEqual(Decimal(usage["released"]), Decimal("300"))

    def test_unknown_amount_cannot_reserve_or_settle(self):
        with self.assertRaises(BudgetError):
            self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-x", amount={"state": "unknown"})
        record = self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-y", amount=money("10"))
        with self.assertRaises(BudgetError):
            self.store.settle(record["record_id"], actual_amount={"state": "unknown"})
        # unknown outcome keeps the reservation reserved (usage still counted)
        self.assertEqual(Decimal(self.store.usage(account_id="acc-1", bucket="procurement")["committed_total"]), Decimal("10"))

    def test_negative_reservation_rejected(self):
        with self.assertRaises(BudgetError):
            self.store.reserve(account_id="acc-1", budget_bucket="procurement", job_id="job-n", amount=money("-1"))


class RevocationIntegrationTests(unittest.TestCase):
    """Revoked/expired mandates must fail the live policy re-check."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mandates = OperatingMandateStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_revoked_mandate_denies_queued_action(self):
        mandate = self.mandates.issue(
            operator="操作者",
            account_id="acc-1",
            action_types=["faq_reply"],
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
        active = self.mandates.list_active()
        allowed = evaluate_action(ActionRequest(action_type="faq_reply", account_id="acc-1"), active_mandates=active)
        self.assertTrue(allowed["allowed"])

        self.mandates.revoke(mandate["record_id"], operator="操作者", reason="紧急停止")
        recheck = evaluate_action(
            ActionRequest(action_type="faq_reply", account_id="acc-1"), active_mandates=self.mandates.list_active()
        )
        self.assertFalse(recheck["allowed"])

    def test_expired_mandate_denies(self):
        self.mandates.issue(
            operator="操作者",
            account_id="acc-1",
            action_types=["faq_reply"],
            valid_until=datetime.now(UTC) + timedelta(hours=2),
        )
        later = datetime.now(UTC) + timedelta(hours=3)
        active = self.mandates.list_active(now=later)
        decision = evaluate_action(ActionRequest(action_type="faq_reply", account_id="acc-1"), active_mandates=active)
        self.assertFalse(decision["allowed"])


class ShadowLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = ShadowAuthorizationLog(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_shadow_records_never_execute_and_report_gates(self):
        decision = PolicyDecision(
            allowed=True,
            authorization_type="mandate",
            reasons=["all checks pass"],
            matched_mandate={"mandate_id": "m1", "revision": 1},
        )
        record = self.log.record(decision=decision, action_summary="faq_reply acc-1")
        self.assertEqual(record["mode"], "shadow")
        self.assertTrue(record["would_allow"])
        metrics = self.log.metrics()
        self.assertEqual(metrics["total"], 1)
        self.assertEqual(metrics["distinct_days"], 1)
        self.assertIn("NOT met", metrics["readiness_note"])


if __name__ == "__main__":
    unittest.main()
