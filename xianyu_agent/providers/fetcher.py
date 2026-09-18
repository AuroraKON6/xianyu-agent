"""Fetch framework for provider adapters (WP04).

One place for the operational behavior every online source needs:
classified error codes, bounded retries with exponential backoff and
jitter, Retry-After compliance, quota accounting and a circuit breaker.

Design constraints:
- The transport is injectable (``transport(request) -> response``).
  Tests never touch the network; no adapter may bypass this framework
  with ad-hoc ``requests`` calls when it lands here.
- Retries are only for safely repeatable reads (5xx, timeout, 429).
  Auth failures (401/403) never retry and never trip the breaker --
  they are configuration problems, not transient load.
- A 429 response is respected: the next attempt waits at least
  ``Retry-After`` when that header parses.
- Quota is checked before every attempt; exceeding the limit returns
  ``QUOTA_EXCEEDED`` without calling the transport.
- The circuit breaker opens after consecutive transport failures and
  fast-fails with ``CIRCUIT_OPEN``; it half-opens after the cooldown.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

FETCH_OK = "ok"
FETCH_DEGRADED = "degraded"
FETCH_ERROR = "error"

ERROR_AUTH_EXPIRED = "AUTH_EXPIRED"
ERROR_RATE_LIMITED = "RATE_LIMITED"
ERROR_SERVER = "SERVER_ERROR"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_CLIENT = "CLIENT_ERROR"
ERROR_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
ERROR_CIRCUIT_OPEN = "CIRCUIT_OPEN"
ERROR_TRANSPORT = "TRANSPORT_ERROR"

RETRYABLE_CODES = {ERROR_RATE_LIMITED, ERROR_SERVER, ERROR_TIMEOUT}


class FetchFrameworkError(RuntimeError):
    pass


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 8.0
    jitter_fraction: float = 0.25

    def delay_for(self, attempt: int, *, rand: Optional[random.Random] = None) -> float:
        rng = rand or random
        exponential = min(self.base_delay_seconds * (2 ** max(0, attempt - 1)), self.max_delay_seconds)
        jitter = exponential * self.jitter_fraction * rng.random()
        return exponential + jitter

    @staticmethod
    def parse_retry_after(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            seconds = float(str(value).strip())
        except ValueError:
            return None
        return seconds if seconds >= 0 else None


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    consecutive_failures: int = 0
    opened_at: Optional[float] = None
    state: str = "closed"  # closed | open | half_open

    def allow(self, now: Optional[float] = None) -> bool:
        moment = now if now is not None else time.monotonic()
        if self.state == "closed":
            return True
        if self.state == "open":
            if self.opened_at is not None and (moment - self.opened_at) >= self.cooldown_seconds:
                self.state = "half_open"
                return True
            return False
        return True  # half_open: allow one probe

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self.state = "closed"

    def record_failure(self, now: Optional[float] = None) -> None:
        moment = now if now is not None else time.monotonic()
        self.consecutive_failures += 1
        if self.state == "half_open" or self.consecutive_failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = moment


@dataclass
class QuotaCounter:
    limit: Optional[int] = None
    window_seconds: float = 3600.0
    events: List[float] = field(default_factory=list)

    def _prune(self, now: float) -> None:
        if self.window_seconds <= 0:
            self.events = []
            return
        self.events = [t for t in self.events if (now - t) < self.window_seconds]

    def used(self, now: Optional[float] = None) -> int:
        moment = now if now is not None else time.monotonic()
        self._prune(moment)
        return len(self.events)

    def would_exceed(self, now: Optional[float] = None) -> bool:
        if self.limit is None:
            return False
        return self.used(now) >= self.limit

    def consume(self, now: Optional[float] = None) -> None:
        moment = now if now is not None else time.monotonic()
        self.events.append(moment)
        self._prune(moment)


@dataclass
class FetchOutcome:
    provider_id: str
    status: str = FETCH_ERROR  # ok | degraded | error
    records: List[Dict[str, Any]] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: str = ""
    retry_after_seconds: Optional[float] = None
    attempts: int = 0
    next_cursor: Optional[str] = None
    quota_used: int = 0
    last_success_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "record_count": len(self.records),
            "records": self.records,
            "error": None
            if self.error_code is None
            else {"code": self.error_code, "message": self.error_message, "retry_after_seconds": self.retry_after_seconds},
            "attempts": self.attempts,
            "next_cursor": self.next_cursor,
            "quota_used": self.quota_used,
            "last_success_at": self.last_success_at,
        }


def classify_transport_failure(response: Mapping[str, Any]) -> Optional[str]:
    """Map one transport response to an error code (None = success)."""
    if response.get("timed_out"):
        return ERROR_TIMEOUT
    status = int(response.get("status_code") or 0)
    if 200 <= status < 300:
        return None
    if status in (401, 403):
        return ERROR_AUTH_EXPIRED
    if status == 429:
        return ERROR_RATE_LIMITED
    if status >= 500:
        return ERROR_SERVER
    return ERROR_CLIENT


def fetch_with_policy(
    transport: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    request: Mapping[str, Any],
    *,
    provider_id: str,
    retry_policy: Optional[RetryPolicy] = None,
    breaker: Optional[CircuitBreaker] = None,
    quota: Optional[QuotaCounter] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    clock: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
) -> FetchOutcome:
    """Run one logical fetch through retry/backoff/breaker/quota.

    ``transport`` receives the request mapping and returns a response
    mapping: ``{"status_code": int, "headers": {...}, "body": Any,
    "timed_out": bool}``. Raises from the transport are treated as
    TRANSPORT_ERROR (retryable through the policy).
    """
    policy = retry_policy or RetryPolicy()
    outcome = FetchOutcome(provider_id=provider_id)

    if quota is not None:
        if quota.would_exceed(now()):
            outcome.status = FETCH_ERROR
            outcome.error_code = ERROR_QUOTA_EXCEEDED
            outcome.quota_used = quota.used(now())
            return outcome

    if breaker is not None and not breaker.allow(now()):
        outcome.status = FETCH_ERROR
        outcome.error_code = ERROR_CIRCUIT_OPEN
        outcome.error_message = "circuit open; source degraded until cooldown"
        return outcome

    last_code: Optional[str] = None
    last_message = ""
    for attempt in range(1, policy.max_attempts + 1):
        outcome.attempts = attempt
        try:
            response = transport(dict(request))
        except Exception as exc:  # noqa: BLE001 - transport errors are data
            last_code = ERROR_TRANSPORT
            last_message = str(exc)
            response = None

        if response is not None:
            code = classify_transport_failure(response)
            body = response.get("body")
            outcome.next_cursor = str((response.get("headers") or {}).get("x-next-cursor") or "") or None
        else:
            code = last_code
            body = None

        if code is None:
            if quota is not None:
                quota.consume(now())
            if breaker is not None:
                breaker.record_success()
            outcome.status = FETCH_OK
            outcome.records = list(body) if isinstance(body, list) else []
            outcome.last_success_at = clock()
            if not outcome.records:
                outcome.status = FETCH_DEGRADED
                outcome.error_code = "EMPTY_RESULT"
                outcome.error_message = "transport ok but zero records"
            return outcome

        last_code = code
        last_message = f"status={response.get('status_code') if response else 'exception'}"
        if code == ERROR_RATE_LIMITED and response is not None:
            outcome.retry_after_seconds = RetryPolicy.parse_retry_after(
                (response.get("headers") or {}).get("retry-after")
            )

        if code == ERROR_AUTH_EXPIRED:
            break  # never retry auth problems

        if code == ERROR_SERVER or code == ERROR_TIMEOUT or code == ERROR_TRANSPORT:
            if breaker is not None:
                breaker.record_failure(now())

        if attempt < policy.max_attempts and code in RETRYABLE_CODES | {ERROR_TRANSPORT}:
            delay = policy.delay_for(attempt)
            if outcome.retry_after_seconds is not None:
                delay = max(delay, outcome.retry_after_seconds)
            sleep(delay)

    if quota is not None:
        quota.consume(now())
    outcome.status = FETCH_ERROR
    outcome.error_code = last_code
    outcome.error_message = last_message or "exhausted retries"
    return outcome
