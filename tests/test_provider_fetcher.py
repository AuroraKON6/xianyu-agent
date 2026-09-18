"""WP04 provider fetch framework and supplier-file adapter tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.providers import (
    CircuitBreaker,
    QuotaCounter,
    RetryPolicy,
    SupplierFileProvider,
    classify_snapshot,
    fetch_with_policy,
)
from xianyu_agent.supplier_import import SupplierImportService

UTC = timezone.utc


def ok_page(records):
    return {"status_code": 200, "headers": {}, "body": records, "timed_out": False}


class RecordingSleep:
    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


class FetchPolicyTests(unittest.TestCase):
    def test_success_first_try(self):
        calls = []

        def transport(request):
            calls.append(request)
            return ok_page([{"sku": "A"}])

        outcome = fetch_with_policy(transport, {"q": "table"}, provider_id="test")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.records, [{"sku": "A"}])
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(len(calls), 1)

    def test_429_respects_retry_after_then_succeeds(self):
        sleeper = RecordingSleep()
        responses = [
            {"status_code": 429, "headers": {"retry-after": "2"}, "body": None, "timed_out": False},
            ok_page([{"sku": "A"}]),
        ]

        def transport(request):
            return responses.pop(0)

        outcome = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.1),
            sleep=sleeper,
        )
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(len(sleeper.delays), 1)
        self.assertGreaterEqual(sleeper.delays[0], 2.0)  # Retry-After wins over backoff

    def test_persistent_429_exhausts_retries(self):
        sleeper = RecordingSleep()

        def transport(request):
            return {"status_code": 429, "headers": {}, "body": None, "timed_out": False}

        outcome = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
            sleep=sleeper,
        )
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.error_code, "RATE_LIMITED")
        self.assertEqual(outcome.attempts, 3)

    def test_auth_failure_never_retries_and_never_trips_breaker(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1)
        calls = []

        def transport(request):
            calls.append(request)
            return {"status_code": 401, "headers": {}, "body": None, "timed_out": False}

        outcome = fetch_with_policy(transport, {}, provider_id="test", breaker=breaker)
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.error_code, "AUTH_EXPIRED")
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(breaker.state, "closed")

    def test_5xx_retries_then_succeeds_and_backoff_grows(self):
        sleeper = RecordingSleep()
        responses = [
            {"status_code": 500, "headers": {}, "body": None, "timed_out": False},
            {"status_code": 503, "headers": {}, "body": None, "timed_out": False},
            ok_page([]),
        ]

        def transport(request):
            return responses.pop(0)

        outcome = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=1.0, jitter_fraction=0.0),
            sleep=sleeper,
        )
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.status, "degraded")  # empty result is degraded, not ok
        self.assertEqual(outcome.error_code, "EMPTY_RESULT")
        self.assertEqual(sleeper.delays, [1.0, 2.0])  # exponential, no jitter

    def test_breaker_opens_after_failures_and_fast_fails(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        calls = []

        def transport(request):
            calls.append(request)
            return {"status_code": 500, "headers": {}, "body": None, "timed_out": False}

        first = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=1), breaker=breaker,
        )
        second = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=1), breaker=breaker,
        )
        self.assertEqual(breaker.state, "open")
        third = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=1), breaker=breaker,
        )
        self.assertEqual(third.error_code, "CIRCUIT_OPEN")
        # fast-fail made no transport call
        self.assertEqual(len(calls), 2)

    def test_breaker_half_open_after_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10)
        clock = {"t": 0.0}

        def transport(request):
            return {"status_code": 500, "headers": {}, "body": None, "timed_out": False}

        fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=1), breaker=breaker,
            now=lambda: clock["t"],
        )
        self.assertEqual(breaker.state, "open")
        clock["t"] += 11
        recovered = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=1), breaker=breaker,
            now=lambda: clock["t"],
        )
        # half-open allowed one probe; it failed again -> back to open
        self.assertEqual(breaker.state, "open")
        self.assertEqual(recovered.attempts, 1)

    def test_timeout_retries(self):
        sleeper = RecordingSleep()
        responses = [
            {"status_code": 0, "headers": {}, "body": None, "timed_out": True},
            ok_page([{"sku": "A"}]),
        ]

        def transport(request):
            return responses.pop(0)

        outcome = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.01),
            sleep=sleeper,
        )
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.attempts, 2)

    def test_transport_exception_is_retryable(self):
        sleeper = RecordingSleep()
        calls = []

        def transport(request):
            calls.append(request)
            if len(calls) == 1:
                raise ConnectionError("dns gone")
            return ok_page([{"sku": "A"}])

        outcome = fetch_with_policy(
            transport, {}, provider_id="test",
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.01),
            sleep=sleeper,
        )
        self.assertEqual(outcome.status, "ok")

    def test_quota_exceeded_short_circuits_without_transport(self):
        quota = QuotaCounter(limit=1, window_seconds=60)
        quota.consume()
        calls = []

        def transport(request):
            calls.append(request)
            return ok_page([])

        outcome = fetch_with_policy(transport, {}, provider_id="test", quota=quota)
        self.assertEqual(outcome.error_code, "QUOTA_EXCEEDED")
        self.assertEqual(len(calls), 0)

    def test_next_cursor_passthrough(self):
        def transport(request):
            return {"status_code": 200, "headers": {"x-next-cursor": "page2"}, "body": [{"sku": "A"}], "timed_out": False}

        outcome = fetch_with_policy(transport, {}, provider_id="test")
        self.assertEqual(outcome.next_cursor, "page2")

    def test_retry_after_unparseable_ignored(self):
        self.assertIsNone(RetryPolicy.parse_retry_after("soon"))
        self.assertIsNone(RetryPolicy.parse_retry_after(None))
        self.assertEqual(RetryPolicy.parse_retry_after("3"), 3.0)


class FreshnessTests(unittest.TestCase):
    def test_supplier_quote_24h(self):
        fresh = {"provider_id": "supplier_file:x", "observed_at": datetime.now(UTC).isoformat()}
        stale = {
            "provider_id": "supplier_file:x",
            "observed_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
        }
        self.assertEqual(classify_snapshot(fresh)["freshness"], "current")
        self.assertEqual(classify_snapshot(stale)["freshness"], "expired")

    def test_market_72h_and_unknown(self):
        market = {
            "provider_id": "market:x",
            "observed_at": (datetime.now(UTC) - timedelta(hours=71)).isoformat(),
        }
        self.assertEqual(classify_snapshot(market)["freshness"], "current")
        no_time = {"provider_id": "market:x", "observed_at": ""}
        self.assertEqual(classify_snapshot(no_time)["freshness"], "unknown")


def quote_csv(price="150", observed=None):
    return (
        "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
        f"GZ-1200,商用不锈钢工作台,{price},件,2,10,15,120x60x85cm,众商,全新\n"
    ).encode("utf-8")


class SupplierFileProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = JsonCommerceRepositories(Path(self.tmp.name))
        self.service = SupplierImportService(self.repos, Path(self.tmp.name))
        self.provider = SupplierFileProvider(self.repos, self.service)

    def tearDown(self):
        self.tmp.cleanup()

    def _import(self, text=quote_csv()):
        preview = self.service.preview_bytes(text, kind="csv")
        return self.service.confirm(
            preview["preview_id"],
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="杭州五金供应商",
            operator="test",
        )

    def test_fetch_returns_committed_snapshots_with_freshness(self):
        self._import()
        result = self.provider.fetch_offers()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["freshness_meta"]["freshness"], "current")
        self.assertEqual(result["expired_excluded"], 0)

    def test_price_change_appends_and_latest_wins(self):
        self._import()
        second = self._import(quote_csv(price="165"))
        result = self.provider.fetch_offers()
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["eligible_price"]["amount"], "165")
        # repository keeps both versions as history
        self.assertEqual(len(self.repos.offers.list()), 2)

    def test_revoked_batch_excluded(self):
        confirmed = self._import()
        self.service.revoke_batch(confirmed["batch"]["record_id"], operator="t", reason="wrong version")
        result = self.provider.fetch_offers()
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["records"], [])

    def test_expired_snapshots_excluded_reported_and_includable(self):
        # import a quote observed 30h ago: committed batch, but stale
        preview = self.service.preview_bytes(quote_csv(), kind="csv")
        self.service.confirm(
            preview["preview_id"],
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="杭州五金供应商",
            operator="test",
            observed_at=datetime.now(UTC) - timedelta(hours=30),
        )
        result = self.provider.fetch_offers()
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["expired_excluded"], 1)
        included = self.provider.fetch_offers(include_expired=True)
        self.assertEqual(len(included["records"]), 1)
        self.assertEqual(included["records"][0]["freshness_meta"]["freshness"], "expired")

    def test_missing_requested_skus_reported_not_hidden(self):
        self._import()
        result = self.provider.fetch_offers(requested_skus=["GZ-1200", "GZ-9999"])
        self.assertEqual(result["missing_skus"], ["GZ-9999"])
        self.assertEqual(result["status"], "ok")

    def test_keyword_filter(self):
        self._import()
        result = self.provider.fetch_offers(keywords=["不存在的东西"])
        self.assertEqual(result["records"], [])


if __name__ == "__main__":
    unittest.main()
