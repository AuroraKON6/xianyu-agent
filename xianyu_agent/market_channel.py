"""Read-only market observation channel (G2B): probe, normalize, fetch.

One channel at a time, reusing the existing account context instead of
deploying a second browser/account system. The module owns three honest
behaviors and no platform writes:

1. Probing never fakes success: without credentials, without a configured
   client, or with an auth failure the probe returns a structured
   ``unavailable``/``auth_invalid`` status; only a real answered probe can
   report ``available`` (A50).
2. Price discipline (A51): a listing price is usable as a display-price
   assumption only when it is an exact full price. Starting prices
   (``123起``), ranges (``100-200``), deposits (定金) and post-coupon
   prices (券后) keep their raw value and an explicit price kind, and the
   eligible price stays ``unknown`` with the reason. A field merely named
   ``soldPrice`` is never treated as a transaction fact.
3. Fetch sessions classify everything (A53/A54): pagination dedupes by
   platform item id and carries a resume token; an empty page is a normal
   end, an auth error stops the session without blind retries, rate
   limits and server errors retry a bounded number of times and then
   degrade the session; partial results are saved but the session is
   marked ``degraded``; a missing item is never inferred as delisted or
   zero stock; the upstream item time (when present) is the observation
   time so re-reading a cache cannot refresh a quote, and the fetch time
   is recorded separately.

Snapshots land through the shared offers repository under
``market:xianyu_channel`` -- a different provider than manual
observations (``market:xianyu_observation``), so downstream panels can
always tell an automated fetch from a human-recorded observation (A55).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .commerce.contracts import (
    PRICE_TYPE_LIST,
    Money,
    build_offer_snapshot,
    utc_now,
)

MARKET_CHANNEL_PROVIDER = "market:xianyu_channel"
CHANNEL_NAME = "xianyu_market_read"
PRICE_KIND_FULL = "full"
PRICE_KIND_STARTING = "starting"
PRICE_KIND_RANGE = "range"
PRICE_KIND_DEPOSIT = "deposit"
PRICE_KIND_POST_COUPON = "post_coupon"
PRICE_KIND_SOLD_HINT = "sold_hint"
PRICE_KIND_UNKNOWN = "unknown"

DEFAULT_MAX_PAGES = 5
DEFAULT_PAGE_RETRIES = 2

_RANGE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*[-~至到]\s*([0-9]+(?:\.[0-9]+)?)\s*$")
_NUMBER_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*$")


class MarketChannelError(RuntimeError):
    """Base error carrying a machine-readable classification."""

    kind = "protocol_error"

    def __init__(self, message: str, *, kind: str = "") -> None:
        super().__init__(message)
        if kind:
            self.kind = str(kind)


class ChannelAuthError(MarketChannelError):
    """401/403 or expired credentials: stop, never retry, never bypass."""

    kind = "auth_invalid"


class ChannelRateLimited(MarketChannelError):
    kind = "rate_limited"


class ChannelServerError(MarketChannelError):
    kind = "server_error"


class ChannelTimeout(ChannelServerError):
    kind = "timeout"


# --------------------------------------------------------------------- probe


def probe_channel(client: Optional[Any] = None, *, channel_configured: bool = False) -> Dict[str, Any]:
    """Structured read-only capability probe; never a fake success.

    ``client`` follows the channel protocol: ``probe()`` returns
    ``{"ok": bool, "account_id": str, "sample_fields": [...], "error": str,
    "error_kind": str}``. Without a configured channel or client the probe
    answers honestly instead of pretending.
    """
    base = {
        "channel": CHANNEL_NAME,
        "probed_at": utc_now().isoformat(),
        "provider_id": MARKET_CHANNEL_PROVIDER,
        "read_only": True,
    }
    if not channel_configured:
        return {**base, "status": "unconfigured",
                "reasons": ["no channel is configured; manual observations remain available"],
                "account_id": "", "sample_fields": []}
    if client is None:
        return {**base, "status": "no_credentials",
                "reasons": ["channel configured but no credentialed client is available"],
                "account_id": "", "sample_fields": []}
    try:
        facts = client.probe()
    except MarketChannelError as exc:
        return {**base, "status": "unavailable", "reasons": [str(exc)], "error_kind": exc.kind,
                "account_id": "", "sample_fields": []}
    except Exception as exc:  # unknown client failure is still structured
        return {**base, "status": "unavailable", "reasons": [f"probe failed: {exc}"],
                "error_kind": "protocol_error", "account_id": "", "sample_fields": []}
    # Codex D8: non-mapping probe results must be structured failures, not crashes
    if not isinstance(facts, Mapping):
        return {**base, "status": "unavailable", "reasons": [f"probe returned non-mapping {type(facts).__name__}"],
                "error_kind": "protocol_error", "account_id": "", "sample_fields": []}
    if not facts.get("ok"):
        reasons = [str((facts or {}).get("error") or "probe reported not-ok")]
        return {**base, "status": "unavailable", "reasons": reasons,
                "error_kind": str((facts or {}).get("error_kind") or "protocol_error"),
                "account_id": str((facts or {}).get("account_id") or ""),
                "sample_fields": list((facts or {}).get("sample_fields") or [])}
    return {**base, "status": "available", "reasons": [],
            "account_id": str(facts.get("account_id") or ""),
            "sample_fields": [str(f) for f in (facts.get("sample_fields") or [])],
            "note": "probe success proves the read path only; it is not a write authorization"}


# ----------------------------------------------------------------- normalize


def classify_price(*, full_price: Any = None, display_price: Any = None, sold_price: Any = None,
                   coupon_price: Any = None, deposit_price: Any = None, raw_text: str = "") -> Dict[str, Any]:
    """Classify a listing price into (kind, Money). Exact full price only."""
    raw_text = str(raw_text or "").strip()

    def exact(value: Any) -> Optional[Decimal]:
        match = _NUMBER_RE.fullmatch(str(value if value is not None else ""))
        if match is None:
            return None
        try:
            return Decimal(match.group(1))
        except InvalidOperation:
            return None

    # Codex D9: conditional text markers override numeric fields; a page
    # showing "50" alongside "50定金" describes the deposit, not the full price
    conditional_markers = ("定金", "券后", "订金", "预付")
    if any(marker in raw_text for marker in conditional_markers):
        kind = "deposit" if any(m in raw_text for m in ("定金", "订金", "预付")) else PRICE_KIND_POST_COUPON
        return {"kind": kind, "money": Money.unknown(f"conditional price text {raw_text!r}; not the full payable price")}

    if deposit_price not in (None, ""):
        return {"kind": PRICE_KIND_DEPOSIT, "money": Money.unknown("deposit amount is not the payable price")}
    if coupon_price not in (None, ""):
        return {"kind": PRICE_KIND_POST_COUPON,
                "money": Money.unknown("post-coupon price; eligibility unknown, kept as display context")}
    range_match = _RANGE_RE.fullmatch(raw_text) if raw_text else None
    if range_match is not None:
        return {"kind": PRICE_KIND_RANGE,
                "money": Money.unknown(f"price range {raw_text!r}; exact payable price unknown")}
    if raw_text and (raw_text.endswith("起") or raw_text.startswith("起") or "起" in raw_text):
        return {"kind": PRICE_KIND_STARTING,
                "money": Money.unknown(f"starting price {raw_text!r}; not an exact landing price")}
    full = exact(full_price if full_price is not None else display_price)
    if full is not None:
        return {"kind": PRICE_KIND_FULL, "money": Money.known(full)}
    if raw_text:
        stripped = exact(raw_text.rstrip("起"))
        if stripped is not None and raw_text.endswith("起"):
            return {"kind": PRICE_KIND_STARTING,
                    "money": Money.unknown(f"starting price {raw_text!r}; not an exact landing price")}
        return {"kind": PRICE_KIND_UNKNOWN, "money": Money.unknown(f"unparseable price text {raw_text!r}")}
    if sold_price not in (None, ""):
        # a field merely named soldPrice is a hint, never a transaction fact
        return {"kind": PRICE_KIND_SOLD_HINT,
                "money": Money.unknown("sold-price field is not a confirmed transaction; kept as context")}
    return {"kind": PRICE_KIND_UNKNOWN, "money": Money.unknown("no price field present")}


def _upstream_time(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None  # a naive upstream time cannot be an honest observation time
    return parsed


def normalize_market_item(raw: Mapping[str, Any], *, fetched_at: datetime, keyword: str = "") -> Dict[str, Any]:
    """Raw channel item -> OfferSnapshot fields with preserved provenance.

    The upstream item time is the observation time when present (so cache
    re-reads never refresh quotes); otherwise the fetch time is used and
    explicitly flagged. Missing condition/quantity/spec stay unknown
    markers -- never zero, never guessed.
    """
    product_id = str(raw.get("product_id") or raw.get("item_id") or "").strip()
    if not product_id:
        raise MarketChannelError("market item without product_id cannot become a snapshot")
    price = classify_price(
        full_price=raw.get("price"),
        display_price=raw.get("display_price"),
        sold_price=raw.get("sold_price"),
        coupon_price=raw.get("coupon_price"),
        deposit_price=raw.get("deposit_price"),
        raw_text=str(raw.get("price_text") or ""),
    )
    observed = _upstream_time(raw.get("item_time") or raw.get("published_at") or raw.get("updated_at"))
    future_anomaly = False
    if observed is not None and observed > fetched_at:
        # Codex D4: a future upstream clock is NOT silently replaced with
        # fetch time (that launders it into "current"); the anomaly is kept
        # visible and the snapshot is marked ineligible for business use.
        future_anomaly = True
        observed_note = "future upstream time anomaly; observation kept at upstream value, flagged ineligible"
    else:
        observed_note = "upstream item time" if observed is not None else "no upstream time; observation time = fetch time"
    if observed is None:
        observed = fetched_at
    condition = str(raw.get("condition") or "").strip() or "unknown"
    quantity_raw = raw.get("quantity")
    attributes: Dict[str, Any] = {
        "title": str(raw.get("title") or ""),
        "spec": str(raw.get("spec") or ""),
        "brand": str(raw.get("brand") or ""),
        "market_channel": {
            "channel": CHANNEL_NAME,
            "price_kind": price["kind"],
            "raw_price": str(raw.get("price_text") or raw.get("price") or raw.get("display_price")
                             or raw.get("coupon_price") or raw.get("deposit_price") or raw.get("sold_price") or ""),
            "keyword": str(keyword or ""),
            "location": str(raw.get("location") or ""),
            "seller_name": str(raw.get("seller_name") or ""),
            "observed_time_basis": observed_note,
            "future_upstream_time_anomaly": future_anomaly,
            "quantity_raw": "" if quantity_raw in (None, "") else str(quantity_raw),
        },
    }
    for key, value in attributes.items():
        if isinstance(value, str) and not value:
            attributes[key] = ""
    payload_hash = hashlib.sha256(
        repr([
            MARKET_CHANNEL_PROVIDER, product_id, keyword,
            attributes["title"], attributes["spec"], attributes["brand"], condition,
            price["kind"], price["money"].to_dict(),
            attributes["market_channel"]["raw_price"],
            # Codex D10: quantity must be in the hash so qty 1→10 creates a
            # new snapshot instead of being deduplicated away
            attributes["market_channel"]["quantity_raw"],
            observed.isoformat(),
        ]).encode("utf-8")
    ).hexdigest()
    snapshot = build_offer_snapshot(
        provider_id=MARKET_CHANNEL_PROVIDER,
        source_item_id=product_id,
        source_sku_id=str(keyword or product_id),
        observed_at=observed,
        fetched_at=fetched_at,
        unit=str(raw.get("unit") or "").strip() or "件",
        quantity=None,
        condition=condition,
        attributes={k: v for k, v in attributes.items() if v != ""},
        price_type=PRICE_TYPE_LIST,
        eligible_price=price["money"].to_dict(),
        shipping_quote=Money.unknown("channel listing does not state freight").to_dict(),
        stock_state="unknown",
        stock_quantity=None,
        moq=None,
        tier_prices=[],
        tax_mode="unknown",
        seller_ref=str(raw.get("seller_name") or ""),
        payload_hash=payload_hash,
        account_id="",
        source_ref=f"market_channel:{CHANNEL_NAME}",
    )
    return {
        "snapshot": snapshot,
        "price_kind": price["kind"],
        "observed_at": observed.isoformat(),
        "observed_time_basis": observed_note,
        "product_id": product_id,
    }


class BrowserMarketProbeClient:
    """Read-only probe client over the EXISTING browser account context.

    Honesty ladder (A50 -- a cached claim is never proof):
    - no active BrowserAdapter (stub/unknown) -> ``no_credentials``
    - adapter present but no stored session   -> ``no_credentials``
    - stored session present but unverified   -> ``unverified_session``:
      the login-state file is a cache, not a probe; verifying it means a
      REAL platform read, which is an authorized action
    - ``real_probe_enabled`` (operator opt-in env) + platform read agrees
      -> ``available`` with the account id and readable field list
    """

    READ_SAMPLE_FIELDS = [
        "title", "display_price", "area", "userNickName", "itemId",
        "picUrl", "wantCount", "browseCount",
    ]

    def __init__(self, adapter_resolver, *, cookie_id: str = "default",
                 real_probe_enabled: bool = False) -> None:
        self._resolver = adapter_resolver
        self._cookie_id = str(cookie_id)
        self._real_probe_enabled = bool(real_probe_enabled)

    def probe(self) -> Dict[str, Any]:
        adapter = self._resolver()
        if adapter is None:
            return {"ok": False, "error_kind": "no_credentials",
                    "error": "no active browser adapter (stub/unknown mode)"}
        try:
            state = adapter.get_login_state(self._cookie_id)
        except Exception as exc:
            return {"ok": False, "error_kind": "protocol_error",
                    "error": f"login state unreadable: {exc}"}
        if not isinstance(state, Mapping) or not state.get("logged_in"):
            return {"ok": False, "error_kind": "no_credentials",
                    "error": "no stored session for this account context"}
        if not self._real_probe_enabled:
            return {"ok": False, "error_kind": "unverified_session",
                    "error": "stored session present but unverified; a real read probe is an "
                             "authorized platform action (set MARKET_CHANNEL_PROBE_AUTHORIZED=1 "
                             "with the account owner's approval)"}
        try:
            result = adapter._run_sync(  # noqa: SLF001 - existing sync bridge
                adapter.check_login_status, self._cookie_id
            )
        except Exception as exc:
            return {"ok": False, "error_kind": "protocol_error",
                    "error": f"probe execution failed: {exc}"}
        if not isinstance(result, Mapping) or not result.get("logged_in"):
            return {"ok": False, "error_kind": "auth_invalid",
                    "error": "platform read probe says the session is not logged in"}
        return {
            "ok": True,
            "account_id": str(state.get("cookie_id") or self._cookie_id),
            "sample_fields": list(self.READ_SAMPLE_FIELDS),
            "probe": "real read check succeeded (login verified via search API)",
        }


# ------------------------------------------------------------- fetch session


class ChannelFetchSession:
    """Drive one bounded, resumable read over a channel client.

    ``client.fetch_page(cursor)`` returns ``{"items": [...], "next_cursor":
    str, "empty": bool}`` or raises a typed MarketChannelError.
    """

    def __init__(self, repos, client: Any, *, keyword: str = "", max_pages: int = DEFAULT_MAX_PAGES,
                 page_retries: int = DEFAULT_PAGE_RETRIES, sleep=None, now: Optional[datetime] = None,
                 quota_ledger=None, quota_source: str = "", quota_manual: bool = False) -> None:
        self._repos = repos
        self._client = client
        self._keyword = str(keyword or "")
        self._max_pages = max(1, int(max_pages))
        self._page_retries = max(0, int(page_retries))
        self._sleep = sleep or (lambda _seconds: None)
        self._now = now
        self._auth_invalid = False
        # Codex D7: composition-root wiring. When the caller does not pass a
        # per-source quota ledger, default to the persisted ledger that lives
        # next to the repositories, so real fetch attempts (retries and later
        # pages included) are metered against the recorded contract.
        self._quota_ledger = quota_ledger
        if self._quota_ledger is None:
            data_dir = getattr(repos, "data_dir", None)
            if data_dir is not None:
                from .source_quotas import SourceQuotaLedger
                self._quota_ledger = SourceQuotaLedger(data_dir)
        self._quota_source = str(quota_source or MARKET_CHANNEL_PROVIDER)
        self._quota_manual = bool(quota_manual)
        # Codex D7: the CALLER precharges the session's first request (the
        # fetch call itself is one authorized attempt); the session meters
        # every additional attempt itself. Unmanaged sources (no contract
        # recorded anywhere) stay unmetered here -- the background dispatcher
        # refuses unattended loops against them separately.
        self._first_request_pending = True

    def fetch(self, *, resume_token: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if resume_token is not None and not isinstance(resume_token, Mapping):
            raise MarketChannelError("resume_token must be a mapping")
        fetched_at = self._now or utc_now()
        cursor = str((resume_token or {}).get("cursor") or "")
        seen_ids = {str(value) for value in (resume_token or {}).get("seen_ids") or []}
        snapshots: List[Dict[str, Any]] = []
        duplicates_skipped = 0
        pages_fetched = 0
        failed_pages: List[Dict[str, Any]] = []
        status = "complete"
        stop_reason = ""
        next_cursor = cursor

        for _ in range(self._max_pages):
            self._auth_invalid = False
            page = self._fetch_page_with_retries(next_cursor, failed_pages)
            if page is None:
                if self._auth_invalid:
                    status = "auth_invalid"
                    stop_reason = "credentials rejected by the channel; session stopped without retry"
                else:
                    status = "degraded"
                    stop_reason = "page failed after retries; partial results saved"
                break
            pages_fetched += 1
            # Codex D12: a page without items/empty/next_cursor keys is
            # malformed, not a successful empty page; stop the session
            if not isinstance(page, dict) or ("items" not in page and "empty" not in page):
                failed_pages.append({"page_cursor": next_cursor, "error_kind": "protocol_error",
                                     "error": "page response missing items/empty keys; not a valid page"})
                status = "degraded"
                stop_reason = "malformed page response; no valid data structure"
                break
            items = list(page.get("items") or [])
            if not items and bool(page.get("empty")):
                next_cursor = ""
                break  # a genuinely empty page is a normal end, not a failure
            for raw in items:
                if not isinstance(raw, Mapping):
                    failed_pages.append({"page_cursor": next_cursor, "error_kind": "protocol_error",
                                         "error": "non-mapping item skipped"})
                    status = "degraded"
                    continue
                product_id = str(raw.get("product_id") or raw.get("item_id") or "")
                if product_id and product_id in seen_ids:
                    duplicates_skipped += 1
                    continue
                try:
                    normalized = normalize_market_item(raw, fetched_at=fetched_at, keyword=self._keyword)
                except MarketChannelError as exc:
                    failed_pages.append({"page_cursor": next_cursor, "error_kind": exc.kind,
                                         "error": str(exc), "product_id": product_id})
                    status = "degraded"
                    continue
                stored, created = self._repos.offers.add(normalized["snapshot"])
                snapshots.append(stored)
                if product_id:
                    # Codex D11: always update seen_ids, even for already-stored
                    # snapshots, so the resume token tracks dedup correctly
                    seen_ids.add(product_id)
            next_cursor = str(page.get("next_cursor") or "")
            if not next_cursor:
                break

        resume_token_out = None
        if next_cursor:
            resume_token_out = {"cursor": next_cursor, "seen_ids": sorted(seen_ids), "keyword": self._keyword}
        return {
            "channel": CHANNEL_NAME,
            "provider_id": MARKET_CHANNEL_PROVIDER,
            "status": status,
            "stop_reason": stop_reason,
            "auth_invalid": status == "auth_invalid",
            "keyword": self._keyword,
            "pages_fetched": pages_fetched,
            "snapshots": snapshots,
            "snapshot_ids": [s["record_id"] for s in snapshots],
            "duplicates_skipped": duplicates_skipped,
            "failed_pages": failed_pages,
            "resume_token": resume_token_out,
            "fetched_at": fetched_at.isoformat(),
            "note": "listing snapshots are display-price observations; missing items are never "
                    "inferred as delisted or zero stock",
        }

    def _fetch_page_with_retries(self, cursor: str, failed_pages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        attempts = self._page_retries + 1
        last: Dict[str, Any] = {}
        for attempt in range(attempts):
            # Codex D7: meter every attempt EXCEPT the session's first --
            # that one is precharged by the caller who authorized the fetch
            if (
                self._quota_ledger is not None
                and self._quota_source
                and self._quota_ledger.is_managed(self._quota_source)
            ):
                if self._first_request_pending:
                    self._first_request_pending = False
                else:
                    try:
                        self._quota_ledger.check_and_consume(
                            self._quota_source, manual=self._quota_manual, now=self._now
                        )
                    except Exception as exc:  # QuotaExhausted / UnattendedQuotaUnknown
                        failed_pages.append({"page_cursor": cursor, "error_kind": "quota_exhausted",
                                             "error": str(exc), "attempt": attempt + 1})
                        return None
            else:
                # unmanaged source: still consume the precharge marker so a
                # later managed window cannot double-skip the first request
                self._first_request_pending = False
            try:
                return self._client.fetch_page(cursor)
            except ChannelAuthError as exc:
                # never retry an auth failure and never try to bypass it
                self._auth_invalid = True
                failed_pages.append({"page_cursor": cursor, "error_kind": exc.kind, "error": str(exc)})
                return None
            except MarketChannelError as exc:
                last = {"page_cursor": cursor, "error_kind": exc.kind, "error": str(exc), "attempt": attempt + 1}
                self._sleep(0)  # injectable for tests; production passes a real sleeper
        failed_pages.append(last)
        return None
