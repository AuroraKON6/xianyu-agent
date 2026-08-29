from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xianyu_agent.account_commercial_terms import (
    AccountCommercialTermsStore,
    evaluate_commercial_terms,
)
from xianyu_agent.db_manager import DBManager
from xianyu_agent.resale import ResaleManager


def _iso_now(*, hours_ago: float = 0) -> str:
    value = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _snapshot_payload(**overrides):
    payload = {
        "cookie_id": "seller_1",
        "category": "商用厨具",
        "seller_type": "business",
        "account_owner_confirmed": True,
        "seller_agreement_confirmed": True,
        "payment_service_ready": True,
        "category_publishable": True,
        "return_policy": "seven_day",
        "return_exception_basis": "",
        "return_address_ready": True,
        "after_sales_ready": True,
        "guarantee_required": False,
        "guarantee_ready": False,
        "guarantee_amount": 0,
        "platform_fee_rate": 0.05,
        "platform_fixed_fee": 3,
        "observed_at": _iso_now(),
        "evidence_note": "已在当前账号该类目发布确认页逐项核对。",
        "evidence_ref": "local-screenshot/commercial-terms-20260818.png",
    }
    payload.update(overrides)
    return payload


def _candidate(manager: ResaleManager, **overrides):
    payload = {
        "title": "商用不锈钢操作台",
        "category": "商用厨具",
        "supplier_price": 680,
        "supplier_shipping": 80,
        "target_sale_price": 1280,
        "specifications": {"material": "304"},
        "images": ["https://example.com/owned.jpg"],
    }
    payload.update(overrides)
    return manager.create_candidate(payload)


class AccountCommercialTermsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "terms.json"
        self.store = AccountCommercialTermsStore(self.path)

    def test_store_round_trip_isolated_by_account_and_category(self):
        first = self.store.save_snapshot(_snapshot_payload())
        second = self.store.save_snapshot(_snapshot_payload(cookie_id="seller_2", platform_fee_rate=0.06))
        third = self.store.save_snapshot(_snapshot_payload(category="餐饮货架", platform_fee_rate=0.04))

        reloaded = AccountCommercialTermsStore(self.path)
        self.assertEqual(reloaded.get_snapshot("seller_1", "商用厨具")["snapshot_version"], first["snapshot_version"])
        self.assertEqual(reloaded.get_snapshot("seller_2", "商用厨具")["platform_fee_rate"], 0.06)
        self.assertEqual(reloaded.get_snapshot("seller_1", "餐饮货架")["platform_fee_rate"], 0.04)
        self.assertEqual(len(reloaded.list_snapshots()), 3)
        self.assertNotEqual(first["snapshot_version"], second["snapshot_version"])
        self.assertNotIn("cookie-value", self.path.read_text(encoding="utf-8"))

    def test_store_rejects_credentials_phone_and_identity_numbers(self):
        for evidence_ref in (
            "cookie=secret",
            "截图联系人13812345678",
            "身份证110101199003071234",
        ):
            with self.subTest(evidence_ref=evidence_ref):
                with self.assertRaises(ValueError):
                    self.store.save_snapshot(_snapshot_payload(evidence_ref=evidence_ref))

    def test_ready_assessment_requires_candidate_repriced_to_same_version(self):
        snapshot = self.store.save_snapshot(_snapshot_payload())
        manager = ResaleManager(self.tmp / "resale")
        candidate = _candidate(manager)

        before = evaluate_commercial_terms(
            snapshot=snapshot,
            cookie_id="seller_1",
            category="商用厨具",
            candidate=candidate,
        )
        self.assertFalse(before["ready"])
        self.assertIn("candidate_fee_matches", before["failed_gate_codes"])

        candidate = manager.update_candidate_economics_from_commercial_terms(
            candidate["candidate_id"],
            cookie_id="seller_1",
            category="商用厨具",
            platform_fee_rate=snapshot["platform_fee_rate"],
            platform_fixed_fee=snapshot["platform_fixed_fee"],
            snapshot_version=snapshot["snapshot_version"],
            observed_at=snapshot["observed_at"],
            evidence_ref=snapshot["evidence_ref"],
            return_policy=snapshot["return_policy"],
            return_exception_basis=snapshot["return_exception_basis"],
        )
        after = evaluate_commercial_terms(
            snapshot=snapshot,
            cookie_id="seller_1",
            category="商用厨具",
            candidate=candidate,
        )

        self.assertTrue(after["ready"], after)
        self.assertEqual(candidate["economics"]["platform_fee"], 67.0)
        self.assertEqual(candidate["economics"]["platform_fixed_fee"], 3.0)
        self.assertEqual(candidate["commercial_terms_version"], snapshot["snapshot_version"])
        self.assertEqual(candidate["commercial_return_policy"], "seven_day")

    def test_stale_snapshot_and_unfunded_required_guarantee_block(self):
        stale = self.store.save_snapshot(_snapshot_payload(
            observed_at=_iso_now(hours_ago=25),
            guarantee_required=True,
            guarantee_ready=False,
            guarantee_amount=500,
        ))
        result = evaluate_commercial_terms(
            snapshot=stale,
            cookie_id="seller_1",
            category="商用厨具",
        )
        self.assertIn("terms_snapshot_fresh", result["failed_gate_codes"])
        self.assertIn("guarantee_ready", result["failed_gate_codes"])

    def test_lawful_return_exception_requires_recorded_basis(self):
        snapshot = self.store.save_snapshot(_snapshot_payload(
            return_policy="lawful_exception",
            return_exception_basis="",
        ))
        result = evaluate_commercial_terms(
            snapshot=snapshot,
            cookie_id="seller_1",
            category="商用厨具",
        )
        self.assertIn("buyer_protection_ready", result["failed_gate_codes"])


class AccountCommercialTermsRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server

        self.server = server
        self.tmp = Path(tempfile.mkdtemp())
        self.db = DBManager(str(self.tmp / "accounts.db"))
        self.db.save_cookie("seller_1", "test-cookie", "terms test")
        self.db.save_cookie_status("seller_1", True)
        self.resale = ResaleManager(self.tmp / "resale")
        self.candidate = _candidate(self.resale)
        self.store = AccountCommercialTermsStore(self.tmp / "terms.json")
        self.patches = [
            patch.object(server, "db_manager", self.db),
            patch.object(server, "resale_manager", self.resale),
            patch.object(server, "account_commercial_terms_store", self.store),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.db.close()

    def _request(self, **overrides):
        payload = _snapshot_payload()
        payload.pop("cookie_id")
        payload["candidate_id"] = self.candidate["candidate_id"]
        payload.update(overrides)
        return payload

    def test_missing_snapshot_is_a_read_only_blocked_assessment(self):
        response = self.client.get(
            "/accounts/seller_1/commercial-terms",
            params={"category": "商用厨具", "candidate_id": self.candidate["candidate_id"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["snapshot"])
        self.assertFalse(response.json()["assessment"]["ready"])
        self.assertIsNone(self.store.get_snapshot("seller_1", "商用厨具"))

    def test_save_reprices_candidate_and_returns_ready_assessment(self):
        response = self.client.post(
            "/accounts/seller_1/commercial-terms",
            json=self._request(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["assessment"]["ready"], data)
        self.assertEqual(data["candidate"]["economics"]["platform_fee"], 67.0)
        self.assertEqual(
            data["candidate"]["commercial_terms_version"],
            data["snapshot"]["snapshot_version"],
        )

        get_response = self.client.get(
            "/accounts/seller_1/commercial-terms",
            params={"category": "商用厨具", "candidate_id": self.candidate["candidate_id"]},
        )
        self.assertTrue(get_response.json()["assessment"]["ready"])

    def test_category_mismatch_and_unknown_account_are_rejected(self):
        cross_channel = _candidate(self.resale, sales_platform="shopee", market_scope="cross_border")
        mismatch = self.client.post(
            "/accounts/seller_1/commercial-terms",
            json=self._request(category="其他类目"),
        )
        unknown = self.client.post(
            "/accounts/missing/commercial-terms",
            json=self._request(),
        )
        wrong_channel = self.client.post(
            "/accounts/seller_1/commercial-terms",
            json=self._request(candidate_id=cross_channel["candidate_id"]),
        )
        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        self.assertEqual(unknown.status_code, 404, unknown.text)
        self.assertEqual(wrong_channel.status_code, 409, wrong_channel.text)
        self.assertEqual(self.store.list_snapshots(), [])


if __name__ == "__main__":
    unittest.main()
