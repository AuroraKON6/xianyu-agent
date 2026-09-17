"""WP01 commerce contract tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from xianyu_agent.commerce import contracts


UTC = timezone.utc


def at(hour: int, minute: int = 0) -> datetime:
    # 2026-09-18 is a Friday; 16:00 UTC == 2026-09-19 00:00 Shanghai
    return datetime(2026, 9, 18, hour, minute, tzinfo=UTC)


class MoneyTests(unittest.TestCase):
    def test_known_money_requires_decimal_and_serializes_as_string(self):
        money = contracts.Money.known("150.25")
        data = money.to_dict()
        self.assertEqual(data["state"], "known")
        self.assertEqual(data["amount"], "150.25")
        self.assertEqual(data["currency"], "CNY")
        self.assertIsInstance(data["amount"], str)

    def test_float_money_rejected(self):
        with self.assertRaises(contracts.ContractError):
            contracts.Money.known(1.5)  # type: ignore[arg-type]

    def test_unknown_and_not_applicable_carry_no_amount(self):
        unknown = contracts.Money.unknown("supplier did not state freight")
        self.assertEqual(unknown.state, "unknown")
        self.assertIsNone(unknown.to_dict()["amount"])
        n_a = contracts.Money.not_applicable()
        self.assertEqual(n_a.state, "not_applicable")

    def test_add_propagates_unknown_instead_of_zero(self):
        known = contracts.Money.known(Decimal("100"))
        unknown = contracts.Money.unknown("no freight quote")
        self.assertEqual(known.add(unknown).state, "unknown")
        self.assertEqual(unknown.add(known).state, "unknown")

    def test_add_known_sums_decimals(self):
        total = contracts.Money.known(Decimal("100.05")).add(contracts.Money.known(Decimal("0.20")))
        self.assertEqual(total.amount, Decimal("100.25"))

    def test_add_currency_mismatch_raises(self):
        with self.assertRaises(contracts.CurrencyMismatch):
            contracts.Money.known(Decimal("1"), "CNY").add(contracts.Money.known(Decimal("1"), "USD"))

    def test_add_not_applicable_rules(self):
        both_na = contracts.Money.not_applicable().add(contracts.Money.not_applicable())
        self.assertEqual(both_na.state, "not_applicable")
        with self.assertRaises(contracts.ContractError):
            contracts.Money.not_applicable().add(contracts.Money.known(Decimal("1")))

    def test_roundtrip(self):
        for money in (
            contracts.Money.known(Decimal("12.30")),
            contracts.Money.unknown("missing"),
            contracts.Money.not_applicable(),
        ):
            restored = contracts.Money.from_dict(money.to_dict())
            self.assertEqual(restored.state, money.state)
            self.assertEqual(restored.amount, money.amount)


class BusinessDayTests(unittest.TestCase):
    def test_business_day_boundary_is_shanghai(self):
        # 2026-09-18 16:00 UTC is 2026-09-19 00:00 in Asia/Shanghai
        self.assertEqual(contracts.business_day(at(16, 0)), "2026-09-19")
        # one minute earlier is still the 18th in Shanghai
        self.assertEqual(contracts.business_day(at(15, 59)), "2026-09-18")

    def test_naive_datetime_rejected(self):
        with self.assertRaises(contracts.ContractError):
            contracts.business_day(datetime(2026, 9, 18, 12, 0))


class OfferSnapshotTests(unittest.TestCase):
    def _snapshot(self, **overrides):
        params = dict(
            provider_id="supplier_file:batch001",
            source_item_id="SUP-1001",
            source_sku_id="SUP-1001-A",
            observed_at=at(2),
            payload_hash="abc123",
            eligible_price=contracts.Money.known(Decimal("150")).to_dict(),
            shipping_quote={"state": "unknown", "reason": "not stated"},
        )
        params.update(overrides)
        return contracts.build_offer_snapshot(**params)

    def test_missing_money_is_unknown_not_zero(self):
        record = self._snapshot()
        self.assertEqual(record["eligible_price"]["state"], "known")
        self.assertEqual(record["shipping_quote"]["state"], "unknown")
        self.assertIsNone(record["shipping_quote"]["amount"])

    def test_requires_provider_item_and_hash(self):
        with self.assertRaises(contracts.ContractError):
            self._snapshot(provider_id="")
        with self.assertRaises(contracts.ContractError):
            self._snapshot(payload_hash="")

    def test_supplier_research_offers_may_be_account_independent_but_carry_source(self):
        record = self._snapshot(account_id="", source_ref="import:batch001")
        self.assertEqual(record["account_id"], "")
        self.assertEqual(record["source_ref"], "import:batch001")

    def test_natural_key_stable(self):
        a = self._snapshot()
        b = self._snapshot(record_id="offer_other")
        self.assertEqual(contracts.offer_natural_key(a), contracts.offer_natural_key(b))


class ProductMatchTests(unittest.TestCase):
    def test_grades_validated(self):
        base = dict(
            left_snapshot_id="offer_a",
            right_snapshot_id="offer_b",
            rule_version="hard-v1",
        )
        for grade in ("EXACT", "COMPARABLE", "NOT_MATCH", "UNKNOWN"):
            record = contracts.build_product_match(grade=grade, **base)
            self.assertEqual(record["grade"], grade)
        with self.assertRaises(contracts.ContractError):
            contracts.build_product_match(grade="MAYBE", **base)

    def test_self_match_rejected(self):
        with self.assertRaises(contracts.ContractError):
            contracts.build_product_match(
                left_snapshot_id="offer_a",
                right_snapshot_id="offer_a",
                grade="EXACT",
                rule_version="hard-v1",
            )


class OpportunityTests(unittest.TestCase):
    def test_requires_reason_for_transition(self):
        record = contracts.build_opportunity(canonical_sku="SKU-1", source="supplier_file:batch001")
        with self.assertRaises(contracts.ContractError):
            contracts.apply_opportunity_transition(record, to_status="researching", reason="  ")

    def test_transition_order_enforced(self):
        record = contracts.build_opportunity(canonical_sku="SKU-1", source="s")
        with self.assertRaises(contracts.ContractError):
            contracts.apply_opportunity_transition(record, to_status="validated", reason="skip")
        contracts.apply_opportunity_transition(record, to_status="researching", reason="match evidence attached")
        contracts.apply_opportunity_transition(record, to_status="ready_for_test", reason="economics complete")
        self.assertEqual(record["status"], "ready_for_test")
        self.assertEqual(record["revision"], 3)
        self.assertEqual(len(record["history"]), 3)

    def test_terminal_states(self):
        record = contracts.build_opportunity(canonical_sku="SKU-1", source="s")
        contracts.apply_opportunity_transition(record, to_status="rejected", reason="no profit")
        with self.assertRaises(contracts.ContractError):
            contracts.apply_opportunity_transition(record, to_status="researching", reason="reopen")


class LaterContractShapeTests(unittest.TestCase):
    """WP07/10/13 shapes: construction validation only for now."""

    def test_agent_job_status_validated(self):
        job = contracts.build_agent_job(role="matching", status="queued")
        self.assertEqual(job["record_type"], "agent_job")
        with self.assertRaises(contracts.ContractError):
            contracts.build_agent_job(role="matching", status="zombie")

    def test_mandate_requires_operator_account_actions_and_window(self):
        until = at(20)
        mandate = contracts.build_operating_mandate(
            operator="operator",
            account_id="acc1",
            action_types=["faq_reply"],
            valid_until=until,
        )
        self.assertEqual(mandate["revision"], 1)
        with self.assertRaises(contracts.ContractError):
            contracts.build_operating_mandate(
                operator="operator",
                account_id="acc1",
                action_types=[],
                valid_until=until,
            )

    def test_authorization_cannot_fake_mandate_without_reference(self):
        with self.assertRaises(contracts.ContractError):
            contracts.build_authorization_record(
                decision_id="dec1",
                action_digest="digest",
                authorization_type="mandate",
            )

    def test_ledger_requires_known_amount(self):
        with self.assertRaises(contracts.ContractError):
            contracts.build_ledger_entry(
                kind="settled",
                direction="debit",
                category="procurement",
                amount={"state": "unknown"},
            )

    def test_procurement_order_quantity_positive(self):
        with self.assertRaises(contracts.ContractError):
            contracts.build_procurement_order(
                supplier_ref="sup",
                canonical_sku="SKU",
                quantity=0,
            )


class EnvelopeTests(unittest.TestCase):
    def test_every_record_carries_traceability_envelope(self):
        records = [
            contracts.build_offer_snapshot(
                provider_id="p",
                source_item_id="i",
                observed_at=at(1),
                payload_hash="h",
            ),
            contracts.build_product_match(
                left_snapshot_id="a",
                right_snapshot_id="b",
                grade="UNKNOWN",
                rule_version="v1",
            ),
            contracts.build_opportunity(canonical_sku="S", source="src"),
            contracts.build_decision_record(summary="s", actor_role="pricing", decision_type="price"),
            contracts.build_agent_job(role="discovery"),
        ]
        for record in records:
            for field_name in ("record_id", "scope", "schema_version", "revision", "trace_id", "created_at"):
                self.assertIn(field_name, record, record["record_type"])
            self.assertEqual(record["scope"], "domestic_xianyu")
            self.assertEqual(record["schema_version"], 1)
            # created_at is timezone-aware ISO with explicit offset
            self.assertIn("+00:00", record["created_at"])


if __name__ == "__main__":
    unittest.main()
