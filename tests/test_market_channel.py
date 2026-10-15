"""G2B market channel contract tests (A50-A55, S/H levels).

Synthetic channel clients only: no real platform request, no credential.
The R layer (real authorized probe + manual per-item verification) stays
WAITING_INPUT per the acceptance matrix.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xianyu_agent.commerce.repositories import JsonCommerceRepositories
from xianyu_agent.market_channel import (
    MARKET_CHANNEL_PROVIDER,
    ChannelAuthError,
    ChannelFetchSession,
    ChannelRateLimited,
    ChannelServerError,
    classify_price,
    normalize_market_item,
    probe_channel,
)
from xianyu_agent.providers.freshness import classify_snapshot

NOW = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)


class _ProbeClient:
    def __init__(self, facts):
        self._facts = facts

    def probe(self):
        if isinstance(self._facts, Exception):
            raise self._facts
        return self._facts


class _PagedClient:
    """Scripted channel client: pages served in order, errors raised as told."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def probe(self):
        return {"ok": True, "account_id": "acct-fixture", "sample_fields": ["title", "price"]}

    def fetch_page(self, cursor):
        self.calls.append(cursor)
        step = self.pages.pop(0) if self.pages else {"items": [], "next_cursor": "", "empty": True}
        if isinstance(step, Exception):
            raise step
        return step


def _item(product_id="8880001", price="299", **overrides):
    fields = {
        "product_id": product_id,
        "title": "不锈钢工作台 全新",
        "price": price,
        "condition": "全新",
        "spec": "120x60x85cm",
        "brand": "众商",
        "quantity": "1",
    }
    fields.update(overrides)
    return fields


class MarketChannelProbeTests(unittest.TestCase):
    def test_a50_unconfigured_and_no_credentials_never_fake_success(self):
        unconfigured = probe_channel(None, channel_configured=False)
        self.assertEqual(unconfigured["status"], "unconfigured")
        self.assertTrue(unconfigured["reasons"])
        no_credentials = probe_channel(None, channel_configured=True)
        self.assertEqual(no_credentials["status"], "no_credentials")

    def test_a50_available_requires_an_answered_probe(self):
        ok = probe_channel(_ProbeClient({"ok": True, "account_id": "acct-1", "sample_fields": ["title"]}),
                           channel_configured=True)
        self.assertEqual(ok["status"], "available")
        self.assertEqual(ok["account_id"], "acct-1")
        self.assertIn("not a write authorization", ok["note"])
        auth_bad = probe_channel(_ProbeClient(ChannelAuthError("401 from channel")), channel_configured=True)
        self.assertEqual(auth_bad["status"], "unavailable")
        self.assertEqual(auth_bad["error_kind"], "auth_invalid")
        not_ok = probe_channel(_ProbeClient({"ok": False, "error": "rate limited", "error_kind": "rate_limited"}),
                               channel_configured=True)
        self.assertEqual(not_ok["status"], "unavailable")
        self.assertEqual(not_ok["error_kind"], "rate_limited")


class MarketChannelPriceDisciplineTests(unittest.TestCase):
    def test_a51_price_kinds(self):
        full = classify_price(full_price="299")
        self.assertEqual(full["kind"], "full")
        self.assertEqual(full["money"].state, "known")

        starting = classify_price(raw_text="123起")
        self.assertEqual(starting["kind"], "starting")
        self.assertEqual(starting["money"].state, "unknown")

        rng = classify_price(raw_text="100-200")
        self.assertEqual(rng["kind"], "range")
        self.assertEqual(rng["money"].state, "unknown")

        deposit = classify_price(full_price="299", deposit_price="50")
        self.assertEqual(deposit["kind"], "deposit")
        self.assertEqual(deposit["money"].state, "unknown")

        coupon = classify_price(full_price="299", coupon_price="269")
        self.assertEqual(coupon["kind"], "post_coupon")
        self.assertEqual(coupon["money"].state, "unknown")

        sold = classify_price(sold_price="280")
        self.assertEqual(sold["kind"], "sold_hint")
        self.assertEqual(sold["money"].state, "unknown")
        self.assertIn("not a confirmed transaction", sold["money"].reason)

    def test_a51_raw_values_preserved_in_snapshot(self):
        result = normalize_market_item(_item(price_text="100-200", price=None), fetched_at=NOW, keyword="工作台")
        attributes = result["snapshot"]["attributes"]
        self.assertEqual(attributes["market_channel"]["price_kind"], "range")
        self.assertEqual(attributes["market_channel"]["raw_price"], "100-200")
        self.assertNotEqual(result["snapshot"]["eligible_price"]["state"], "known")

    def test_a52_condition_and_quantity_variants_preserved(self):
        base = normalize_market_item(_item(), fetched_at=NOW)
        used = normalize_market_item(_item(condition="9成新", product_id="8880002"), fetched_at=NOW)
        bulk = normalize_market_item(_item(quantity="10", product_id="8880003"), fetched_at=NOW)
        self.assertNotEqual(base["snapshot"]["condition"], used["snapshot"]["condition"])
        self.assertEqual(bulk["snapshot"]["attributes"]["market_channel"]["quantity_raw"], "10")
        # missing condition becomes an explicit unknown, never a guess
        missing = normalize_market_item(_item(condition="", product_id="8880004"), fetched_at=NOW)
        self.assertEqual(missing["snapshot"]["condition"], "unknown")

    def test_a54_upstream_time_is_observation_time(self):
        upstream = NOW - timedelta(days=5)
        old = normalize_market_item(_item(item_time=upstream.isoformat()), fetched_at=NOW)
        self.assertEqual(old["snapshot"]["observed_at"], upstream.isoformat())
        self.assertEqual(old["observed_time_basis"], "upstream item time")
        # the old observation is honest history: expired for business use
        classified = classify_snapshot(old["snapshot"], now=NOW)
        self.assertNotEqual(classified["freshness"], "current")

        future = normalize_market_item(_item(item_time=(NOW + timedelta(hours=3)).isoformat()),
                                       fetched_at=NOW)
        # Codex D4: future upstream time is kept (not laundered to fetch
        # time); the anomaly flag marks it ineligible for business use
        self.assertIn("anomaly", future["observed_time_basis"])
        self.assertTrue(future["snapshot"]["attributes"]["market_channel"]["future_upstream_time_anomaly"])
        # the future-time snapshot must NOT be fresh (age is negative)
        classified = classify_snapshot(future["snapshot"], now=NOW)
        self.assertNotEqual(classified["freshness"], "current")

    def test_a54_no_upstream_time_uses_fetch_time_and_says_so(self):
        result = normalize_market_item(_item(), fetched_at=NOW)
        self.assertEqual(result["snapshot"]["observed_at"], NOW.isoformat())
        self.assertEqual(result["snapshot"]["fetched_at"], NOW.isoformat())
        self.assertIn("fetch time", result["observed_time_basis"])


class ChannelFetchSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="market-channel-"))
        self.repos = JsonCommerceRepositories(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _session(self, client, **kwargs):
        defaults = dict(keyword="工作台", max_pages=3, page_retries=1, now=NOW)
        defaults.update(kwargs)
        return ChannelFetchSession(self.repos, client, **defaults)

    def test_a53_pagination_dedup_and_normal_end(self):
        client = _PagedClient([
            {"items": [_item(), _item(product_id="8880002")], "next_cursor": "c2"},
            {"items": [_item(), _item(product_id="8880003")], "next_cursor": ""},  # dup id 8880001
        ])
        report = self._session(client).fetch()
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["pages_fetched"], 2)
        self.assertEqual(len(report["snapshots"]), 3)
        self.assertEqual(report["duplicates_skipped"], 1)
        self.assertIsNone(report["resume_token"])

    def test_a53_empty_page_is_not_failure(self):
        client = _PagedClient([
            {"items": [], "next_cursor": "", "empty": True},
        ])
        report = self._session(client).fetch()
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["pages_fetched"], 1)
        self.assertEqual(report["snapshots"], [])

    def test_a53_auth_error_stops_without_retry(self):
        client = _PagedClient([
            ChannelAuthError("401 unauthorized"),
            {"items": [_item()], "next_cursor": ""},
        ])
        report = self._session(client).fetch()
        self.assertEqual(report["status"], "auth_invalid")
        self.assertTrue(report["auth_invalid"])
        self.assertEqual(len(client.calls), 1)  # no second attempt
        self.assertEqual(report["snapshots"], [])

    def test_a53_rate_limit_retries_then_degrades(self):
        client = _PagedClient([
            {"items": [_item()], "next_cursor": "c2"},
            ChannelRateLimited("429"),
            ChannelRateLimited("429"),
            {"items": [_item(product_id="8880009")], "next_cursor": ""},
        ])
        report = self._session(client).fetch()
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(len(report["snapshots"]), 1)  # partial results saved
        self.assertTrue(report["failed_pages"])
        self.assertIn("rate_limited", str(report["failed_pages"]))

    def test_a53_server_error_degrades_and_missing_items_not_inferred(self):
        client = _PagedClient([
            {"items": [_item()], "next_cursor": "c2"},
            ChannelServerError("503"),
            ChannelServerError("503"),
        ])
        report = self._session(client).fetch()
        self.assertEqual(report["status"], "degraded")
        self.assertIn("never", report["note"])
        for snapshot in report["snapshots"]:
            self.assertEqual(snapshot["stock_state"], "unknown")  # absence is not zero stock

    def test_a53_resume_token_continues_from_cursor(self):
        client1 = _PagedClient([
            {"items": [_item()], "next_cursor": "c2"},
        ])
        first = self._session(client1, max_pages=1).fetch()
        self.assertEqual(first["resume_token"]["cursor"], "c2")
        client2 = _PagedClient([
            {"items": [_item(product_id="8880007"), _item()], "next_cursor": ""},
        ])
        second = self._session(client2).fetch(resume_token=first["resume_token"])
        self.assertEqual(second["status"], "complete")
        self.assertEqual(len(second["snapshots"]), 1)  # seen id skipped as duplicate
        self.assertEqual(second["duplicates_skipped"], 1)

    def test_a54_identical_content_does_not_refresh_quote(self):
        upstream = NOW - timedelta(hours=2)
        item = _item(item_time=upstream.isoformat())
        client1 = _PagedClient([{"items": [item], "next_cursor": ""}])
        first = self._session(client1).fetch()
        # re-reading the same cached content later adds nothing new
        later = NOW + timedelta(hours=1)
        client2 = _PagedClient([{"items": [item], "next_cursor": ""}])
        second = ChannelFetchSession(self.repos, client2, keyword="工作台", now=later).fetch()
        self.assertEqual(len(first["snapshot_ids"]), 1)
        self.assertEqual(len(second["snapshot_ids"]), 1)
        self.assertEqual(first["snapshot_ids"][0], second["snapshot_ids"][0])  # same stored record
        stored = self.repos.offers.get(first["snapshot_ids"][0])
        self.assertEqual(stored["observed_at"], upstream.isoformat())  # observation time unchanged


class MarketChannelHttpTests(unittest.TestCase):
    def test_a55_unconfigured_channel_and_manual_fallback(self):
        from fastapi.testclient import TestClient

        from xianyu_agent import server as server_module

        client = TestClient(server_module.app)
        status = client.get("/commerce/market-channel/status")
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertIn(body["status"], ("unconfigured", "no_credentials"))
        self.assertTrue(body["reasons"])

        fetch = client.post("/commerce/market-channel/fetch", json={"keyword": "工作台"})
        self.assertEqual(fetch.status_code, 409, fetch.text)
        self.assertIn("channel_not_configured", fetch.text)
        self.assertIn("manual", fetch.text.lower())

        # manual observation remains the typed fallback, distinct provider
        manual = client.post(
            "/commerce/market-observations",
            json={"sku": "SKU-A55", "title": "人工观察", "price": "260", "condition": "全新", "unit": "件"},
        )
        self.assertEqual(manual.status_code, 200, manual.text)
        self.assertNotEqual(manual.json()["snapshot"]["provider_id"], MARKET_CHANNEL_PROVIDER)


if __name__ == "__main__":
    unittest.main()
