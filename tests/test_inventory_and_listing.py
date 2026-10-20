"""WP12 inventory allocation, listing guard and template cleanup tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from quote_fixtures import with_declared_quote_provenance

from xianyu_agent.commerce.contracts import build_offer_snapshot
from xianyu_agent.inventory import InventoryAllocationService, InventoryError
from xianyu_agent.listing_guard import check_listing_draft, scan_listing_text
from xianyu_agent.physical_sourcing import STEALTH_TEMPLATES

UTC = timezone.utc


def supplier_snapshot(*, stock=None, stock_state="in_stock", price="150", observed=None):
    observed = observed or datetime.now(UTC)
    return with_declared_quote_provenance(build_offer_snapshot(
        provider_id="supplier_file:S供应商",
        source_item_id="GZ-1200",
        source_sku_id="GZ-1200",
        observed_at=observed,
        attributes={"name": "不锈钢工作台"},
        condition="全新",
        unit="件",
        price_type="supplier_confirmed",
        eligible_price={"state": "known", "amount": price, "currency": "CNY"},
        shipping_quote={"state": "known", "amount": "15", "currency": "CNY"},
        stock_state=stock_state if stock is not None else "unknown",
        stock_quantity=stock,
        payload_hash=f"inv-{stock}-{stock_state}-{price}-{observed.isoformat()}",
        account_id="",
        source_ref="test",
    ))


class InventoryAllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = InventoryAllocationService(Path(self.tmp.name), safety_margin=1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_known_fresh_stock_minus_safety_margin(self):
        snapshot = supplier_snapshot(stock=10)
        self.service.register_supplier_snapshot(account_id="acc-1", sku="GZ-1200", snapshot=snapshot)
        allocation = self.service.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["sellable"], 9)
        self.assertEqual(allocation["available"], 9)

    def test_unknown_stock_sells_zero(self):
        snapshot = supplier_snapshot(stock=None, stock_state="unknown")
        self.service.register_supplier_snapshot(account_id="acc-1", sku="GZ-1200", snapshot=snapshot)
        allocation = self.service.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["sellable"], 0)
        self.assertIn("unknown", allocation["sellable_reason"])

    def test_expired_supplier_stock_sells_zero(self):
        stale = supplier_snapshot(stock=10, observed=datetime.now(UTC) - timedelta(hours=30))
        self.service.register_supplier_snapshot(account_id="acc-1", sku="GZ-1200", snapshot=stale)
        allocation = self.service.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["sellable"], 0)

    def test_order_locks_and_oversell_refused(self):
        self.service.register_supplier_snapshot(
            account_id="acc-1", sku="GZ-1200", snapshot=supplier_snapshot(stock=5)
        )  # sellable 4
        self.service.lock_for_order(account_id="acc-1", sku="GZ-1200", order_id="order-1", quantity=3)
        # idempotent re-lock
        self.service.lock_for_order(account_id="acc-1", sku="GZ-1200", order_id="order-1", quantity=3)
        allocation = self.service.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["locked"], 3)
        self.assertEqual(allocation["available"], 1)
        with self.assertRaises(InventoryError):
            self.service.lock_for_order(account_id="acc-1", sku="GZ-1200", order_id="order-2", quantity=2)

    def test_release_returns_allocation(self):
        self.service.register_supplier_snapshot(
            account_id="acc-1", sku="GZ-1200", snapshot=supplier_snapshot(stock=5)
        )
        self.service.lock_for_order(account_id="acc-1", sku="GZ-1200", order_id="order-1", quantity=4)
        self.service.release_order(account_id="acc-1", sku="GZ-1200", order_id="order-1", reason="buyer cancelled")
        self.assertEqual(self.service.allocation(account_id="acc-1", sku="GZ-1200")["available"], 4)

    def test_stock_refresh_updates_sellable_but_keeps_locks(self):
        self.service.register_supplier_snapshot(
            account_id="acc-1", sku="GZ-1200", snapshot=supplier_snapshot(stock=5, price="150")
        )
        self.service.lock_for_order(account_id="acc-1", sku="GZ-1200", order_id="order-1", quantity=2)
        # supplier raises stock AND price; locks survive the refresh
        self.service.register_supplier_snapshot(
            account_id="acc-1", sku="GZ-1200", snapshot=supplier_snapshot(stock=9, price="160")
        )
        allocation = self.service.allocation(account_id="acc-1", sku="GZ-1200")
        self.assertEqual(allocation["sellable"], 8)
        self.assertEqual(allocation["locked"], 2)
        self.assertEqual(allocation["available"], 6)

    def test_unregistered_sku_lock_refused(self):
        with self.assertRaises(InventoryError):
            self.service.lock_for_order(account_id="acc-9", sku="NOPE", order_id="o", quantity=1)


class ListingGuardTests(unittest.TestCase):
    def _draft(self, **overrides):
        base = {
            "title": "商用不锈钢工作台 120x60x85 全新",
            "price": "299",
            "spec": "120x60x85cm 不锈钢",
            "description": "餐厅后厨操作台，规格见图。",
            "images": ["evidence://image_rights/img1.jpg"],
            "offer_snapshot_id": "offer_1",
            "offer_price": "150",
        }
        base.update(overrides)
        return base

    def test_clean_draft_approves(self):
        result = check_listing_draft(self._draft())
        self.assertTrue(result["approved"])
        self.assertEqual(result["issues"], [])

    def test_fabricated_multi_platform_store_blocked(self):
        result = check_listing_draft(
            self._draft(description="我们在其他平台也有店铺，都是一个仓库发货的哈")
        )
        self.assertFalse(result["approved"])
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("fabricated_multi_platform_store", codes)

    def test_cashback_blocked(self):
        result = check_listing_draft(self._draft(description="满意的话给个好评，返现5元"))
        self.assertFalse(result["approved"])
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("cashback_solicitation", codes)

    def test_arrival_guarantee_blocked(self):
        result = check_listing_draft(self._draft(description="拍下保证48小时一定到"))
        self.assertFalse(result["approved"])

    def test_missing_fields_and_images_blocked(self):
        result = check_listing_draft(self._draft(images=[], spec=""))
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("missing_images", codes)
        self.assertIn("missing_spec", codes)

    def test_price_revision_makes_draft_stale(self):
        latest = {"eligible_price": {"state": "known", "amount": "160", "currency": "CNY"}}
        result = check_listing_draft(self._draft(offer_price="150"), latest_offer=latest)
        self.assertFalse(result["approved"])
        self.assertTrue(result["stale"])
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("stale_offer_price", codes)
        # rebuilt draft on the new price passes again
        rebuilt = check_listing_draft(self._draft(offer_price="160", offer_snapshot_id="offer_2"), latest_offer=latest)
        self.assertTrue(rebuilt["approved"])

    def test_scan_listing_text_direct(self):
        issues = scan_listing_text("100%正品，绝对正品，好评返现")
        codes = {i["code"] for i in issues}
        self.assertIn("cashback_solicitation", codes)
        self.assertIn("absolute_authenticity", codes)


class TemplateCleanupTests(unittest.TestCase):
    """The removed fabricated scripts must stay removed."""

    def test_only_order_note_template_remains(self):
        self.assertEqual(set(STEALTH_TEMPLATES.keys()), {"order_note"})

    def test_no_fabricated_claims_in_remaining_template(self):
        issues = scan_listing_text(STEALTH_TEMPLATES["order_note"])
        self.assertEqual(issues, [])

    def test_buyer_response_uses_verified_facts_only(self):
        from xianyu_agent.physical_sourcing import PhysicalSourcingManager

        service = PhysicalSourcingManager.__new__(PhysicalSourcingManager)  # no init side effects
        neutral = service.generate_buyer_response("pdd_logo", context={})
        self.assertEqual(neutral, "您好，有任何问题我们会妥善处理的，请放心。")
        factual = service.generate_buyer_response(
            "pdd_logo", context={"verified_facts": {"fulfillment_note": "由本地仓库直发，48小时内出库。"}}
        )
        self.assertIn("本地仓库直发", factual)


class SourcingEvaluationCleanupTests(unittest.TestCase):
    """R06: the legacy sourcing evaluation must not crash on removed
    template keys, must report unknown fees as unknown, and must never
    generate fictional-size/misleading bait or grant eligibility."""

    def _manager(self):
        from types import SimpleNamespace

        from xianyu_agent.physical_sourcing import PhysicalSourcingManager
        from xianyu_agent.storage import JsonStorage

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = SimpleNamespace(
            default_platform_fee_rate=0.016,
            min_profit_rate=0.30,
            data_dir=Path(tmp.name),
        )
        return PhysicalSourcingManager(settings, JsonStorage(Path(tmp.name)), None)

    def test_full_evaluation_returns_without_key_error(self):
        manager = self._manager()
        report = manager.evaluate_sourcing_opportunity(
            {
                "title": "不锈钢工作台 120x60cm",
                "category": "厨具",
                "cost_price": 160,
                "resale_price": 300,
                "shipping_cost": 15,
            }
        )
        self.assertEqual(report["type"], "physical_sourcing_opportunity")
        self.assertIn("order_note", report["stealth_templates"])
        self.assertNotIn("buyer_pdd_logo", report["stealth_templates"])
        self.assertNotIn("buyer_pdd_sms", report["stealth_templates"])

    def test_missing_shipping_is_unknown_fee_and_never_viable(self):
        manager = self._manager()
        report = manager.evaluate_sourcing_opportunity(
            {
                "title": "不锈钢工作台",
                "category": "厨具",
                "cost_price": 160,
                "resale_price": 300,
                # shipping_cost deliberately absent
            }
        )
        economics = report["economics"]
        self.assertEqual(economics["fee_state"], "unknown")
        self.assertIsNone(economics["shipping_cost"])
        self.assertFalse(economics["is_viable"])

    def test_legacy_profit_entry_is_rough_estimate_only(self):
        manager = self._manager()
        economics = manager.calculate_profit(160, 300, 15)
        self.assertTrue(economics["is_viable"])  # high margin, fees known
        self.assertIn("rough_estimate_only", economics["qualification"])
        self.assertIn("commerce economics kernel", economics["qualification"])

    def test_no_fictional_size_bait_anywhere(self):
        from xianyu_agent.listing_strategy import ListingStrategyEngine

        manager = self._manager()
        for payload_shipping in (15, None):
            report = manager.evaluate_sourcing_opportunity(
                {
                    "title": "商用工作台",
                    "category": "厨具",
                    "cost_price": 200,
                    "resale_price": 260,
                    "shipping_cost": payload_shipping,
                }
            )
            strategy = report["listing_strategy"]
            self.assertNotIn(strategy.get("bait_strategy"), ("fictional_size", "title_contrast"))
        # the engine never emits the removed deceptive variants
        import inspect

        source = inspect.getsource(ListingStrategyEngine._create_price_strategy)
        self.assertNotIn("fictional_size", source)
        self.assertNotIn("title_contrast", source)


if __name__ == "__main__":
    unittest.main()
