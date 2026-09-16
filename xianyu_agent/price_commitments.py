"""Structured bargaining memory and product facts.

Deterministic, append-only fact tables that record what prices have actually
been committed in a chat. The LLM never writes these values; they are
extracted with fixed regex rules from inbound buyer messages and from replies
whose platform send is confirmed, and they only ever move in one direction:

- ``seller_lowest_committed`` only moves down (the floor already promised).
- ``buyer_highest_offer`` only moves up (the best offer already made).

Product facts carry the authoritative listing values (list price, stock,
shipping) that reply drafting must respect; they are revised by explicit
operator/API upserts with an immutable revision history.

Extraction is deliberately conservative: only explicit CNY amounts match,
absurd amounts are dropped, and when a list price is known, extracted
amounts far outside a plausible offer band are ignored.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

PRICE_SCHEMA_VERSION = 1
MIN_PLAUSIBLE_AMOUNT = 0.01
MAX_PLAUSIBLE_AMOUNT = 1_000_000.0
DEFAULT_MIN_OFFER_RATIO = 0.1
DEFAULT_MAX_OFFER_RATIO = 1.05

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


_PRICE_PATTERNS: tuple = (
    ("prefixed_currency", re.compile(r"(?:￥|¥)\s*(\d{1,7}(?:\.\d{1,2})?)(?![\d.])")),
    (
        "suffixed_unit",
        re.compile(r"(?<![\d.])(\d{1,7}(?:\.\d{1,2})?)\s*(?:元|块钱?)(?![\d.])"),
    ),
    (
        "offer_shorthand_suffix",
        re.compile(r"(?<![\d.])(\d{1,7}(?:\.\d{1,2})?)\s*出(?![\d.])"),
    ),
    (
        "offer_shorthand_prefix",
        re.compile(r"(?<![\d.])出\s*(\d{1,7}(?:\.\d{1,2})?)(?![\d.])"),
    ),
)


def extract_price_mentions(text: str) -> List[Dict[str, Any]]:
    """Return explicit CNY amounts mentioned in a message (deterministic)."""
    clean = str(text or "")
    mentions: List[Dict[str, Any]] = []
    for pattern_name, pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(clean):
            try:
                amount = round(float(match.group(1)), 2)
            except ValueError:
                continue
            if not MIN_PLAUSIBLE_AMOUNT <= amount <= MAX_PLAUSIBLE_AMOUNT:
                continue
            start = max(0, match.start() - 12)
            end = min(len(clean), match.end() + 12)
            mentions.append(
                {
                    "amount": amount,
                    "pattern": pattern_name,
                    "excerpt": clean[start:end].strip(),
                }
            )
    # Stable order, deduplicate identical (amount, excerpt) pairs.
    seen = set()
    unique = []
    for mention in sorted(mentions, key=lambda item: item["amount"]):
        key = (mention["amount"], mention["excerpt"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(mention)
    return unique


def plausible_against_list_price(
    amount: float, list_price: Optional[float]
) -> bool:
    if list_price is None or list_price <= 0:
        return True
    return (
        DEFAULT_MIN_OFFER_RATIO * list_price <= amount <= DEFAULT_MAX_OFFER_RATIO * list_price
    )


class PriceCommitmentStore:
    """Monotonic bargaining memory keyed by account + chat + item."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = Path(
            storage_path or Path("data") / "price_commitments.json"
        )
        self._thread_lock = _path_lock(self.storage_path)
        self._records: Dict[str, Dict[str, Any]] = {}
        self._load()

    def configure(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
        self._thread_lock = _path_lock(self.storage_path)
        self._records = {}
        self._load()

    def record_buyer_offer(
        self,
        *,
        cookie_id: str,
        chat_id: str,
        item_id: str,
        amount: float,
        source: str,
        excerpt: str = "",
        list_price: Optional[float] = None,
        occurred_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a buyer offer; the highest offer ever seen wins."""
        clean = _validate_scope(cookie_id, chat_id, item_id)
        value = _validate_amount(amount)
        if not plausible_against_list_price(value, list_price):
            return {"status": "skipped_implausible", "amount": value}
        with self._locked_state():
            record = self._ensure_record(clean)
            key = clean["key"]
            history_source = f"buyer:{source}"
            if _history_has_source(record, history_source):
                return {"status": "already_recorded"}
            self._append_history(
                record,
                actor="buyer",
                amount=value,
                source=history_source,
                excerpt=excerpt,
                occurred_at=occurred_at,
                applied=False,
            )
            current = record.get("buyer_highest_offer") or {}
            applied = False
            if value > float(current.get("amount") or 0):
                record["buyer_highest_offer"] = {
                    "amount": value,
                    "updated_at": time.time(),
                    "source": history_source,
                    "excerpt": str(excerpt or "")[:120],
                }
                applied = True
            record["history"][-1]["applied"] = applied
            record["updated_at"] = time.time()
            self._save_unlocked()
            return {"status": "applied" if applied else "superseded", "record": _copy(record)}

    def record_seller_commit(
        self,
        *,
        cookie_id: str,
        chat_id: str,
        item_id: str,
        amount: float,
        source: str,
        excerpt: str = "",
        occurred_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a seller price commitment; only the lowest floor ever sticks."""
        clean = _validate_scope(cookie_id, chat_id, item_id)
        value = _validate_amount(amount)
        with self._locked_state():
            record = self._ensure_record(clean)
            history_source = f"seller:{source}"
            if _history_has_source(record, history_source):
                return {"status": "already_recorded"}
            self._append_history(
                record,
                actor="seller",
                amount=value,
                source=history_source,
                excerpt=excerpt,
                occurred_at=occurred_at,
                applied=False,
            )
            current = record.get("seller_lowest_committed") or {}
            applied = False
            if not current or value < float(current.get("amount") or 0):
                record["seller_lowest_committed"] = {
                    "amount": value,
                    "updated_at": time.time(),
                    "source": history_source,
                    "excerpt": str(excerpt or "")[:120],
                }
                applied = True
            record["history"][-1]["applied"] = applied
            record["updated_at"] = time.time()
            self._save_unlocked()
            return {"status": "applied" if applied else "superseded", "record": _copy(record)}

    def get_record(
        self, *, cookie_id: str, chat_id: str, item_id: str
    ) -> Optional[Dict[str, Any]]:
        key = _scope_key(cookie_id, chat_id, item_id)
        with self._thread_lock:
            record = self._records.get(key)
            return _copy(record) if record else None

    def list_records(
        self,
        *,
        cookie_id: str = "",
        chat_id: str = "",
        item_id: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._thread_lock:
            selected = []
            for record in self._records.values():
                if cookie_id and str(record.get("cookie_id") or "") != str(cookie_id):
                    continue
                if chat_id and str(record.get("chat_id") or "") != str(chat_id):
                    continue
                if item_id and str(record.get("item_id") or "") != str(item_id):
                    continue
                selected.append(record)
            selected.sort(key=lambda item: item.get("updated_at") or 0)
            return [_copy(record) for record in selected[-max(1, int(limit)) :]]

    def metrics(self) -> Dict[str, Any]:
        with self._thread_lock:
            return {
                "schema_version": PRICE_SCHEMA_VERSION,
                "record_count": len(self._records),
                "chats_with_seller_floor": sum(
                    1 for record in self._records.values() if record.get("seller_lowest_committed")
                ),
                "chats_with_buyer_offer": sum(
                    1 for record in self._records.values() if record.get("buyer_highest_offer")
                ),
            }

    # ---- internals ----

    def _ensure_record(self, clean: Dict[str, str]) -> Dict[str, Any]:
        record = self._records.get(clean["key"])
        if record is None:
            record = {
                "schema_version": PRICE_SCHEMA_VERSION,
                "cookie_id": clean["cookie_id"],
                "chat_id": clean["chat_id"],
                "item_id": clean["item_id"],
                "seller_lowest_committed": None,
                "buyer_highest_offer": None,
                "history": [],
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            self._records[clean["key"]] = record
        return record

    def _append_history(
        self,
        record: Dict[str, Any],
        *,
        actor: str,
        amount: float,
        source: str,
        excerpt: str,
        occurred_at: Optional[float],
        applied: bool,
    ) -> None:
        record["history"].append(
            {
                "entry_id": "pc_" + uuid.uuid4().hex[:10],
                "actor": actor,
                "amount": amount,
                "source": source[:200],
                "excerpt": str(excerpt or "")[:120],
                "occurred_at": float(occurred_at) if occurred_at else time.time(),
                "recorded_at": time.time(),
                "applied": applied,
            }
        )

    def _load(self) -> None:
        with self._locked_state_reload_only():
            pass

    @contextmanager
    def _locked_state(self):
        with self._thread_lock:
            with self._exclusive_file_lock():
                self._reload_unlocked()
                yield

    @contextmanager
    def _locked_state_reload_only(self):
        with self._thread_lock:
            with self._exclusive_file_lock():
                self._reload_unlocked()
                yield

    @contextmanager
    def _exclusive_file_lock(self):
        lock_path = self.storage_path.with_name(f"{self.storage_path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _reload_unlocked(self) -> None:
        self._records = {}
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("cookie_id") is not None:
                key = _scope_key(
                    item.get("cookie_id", ""), item.get("chat_id", ""), item.get("item_id", "")
                )
                self._records[key] = item

    def _save_unlocked(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.storage_path.with_name(
            f".{self.storage_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(list(self._records.values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self.storage_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


class ProductFactsStore:
    """Authoritative listing facts per account + item with revision history."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = Path(storage_path or Path("data") / "product_facts.json")
        self._thread_lock = _path_lock(self.storage_path)
        self._facts: Dict[str, Dict[str, Any]] = {}
        self._load()

    def configure(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
        self._thread_lock = _path_lock(self.storage_path)
        self._facts = {}
        self._load()

    def upsert(
        self,
        *,
        cookie_id: str,
        item_id: str,
        list_price: Optional[float] = None,
        stock_available: Optional[int] = None,
        ships_in_days: Optional[int] = None,
        condition: str = "",
        notes: str = "",
        source: str = "operator",
    ) -> Dict[str, Any]:
        if not str(cookie_id or "").strip() or not str(item_id or "").strip():
            raise ValueError("product facts require cookie_id and item_id")
        clean_list_price = (
            round(float(list_price), 2)
            if list_price is not None and float(list_price) > 0
            else None
        )
        clean_stock = (
            max(0, int(stock_available)) if stock_available is not None else None
        )
        clean_ships = max(0, int(ships_in_days)) if ships_in_days is not None else None
        key = _scope_key(cookie_id, "", item_id, separator="|facts|")
        with self._thread_lock:
            current = self._facts.get(key) or {
                "schema_version": PRICE_SCHEMA_VERSION,
                "cookie_id": str(cookie_id).strip(),
                "item_id": str(item_id).strip(),
                "list_price": None,
                "stock_available": None,
                "ships_in_days": None,
                "condition": "",
                "notes": "",
                "revisions": [],
                "created_at": time.time(),
            }
            revision = {
                "revision_id": "pf_" + uuid.uuid4().hex[:10],
                "recorded_at": time.time(),
                "source": str(source or "operator")[:120],
                "changed": {},
            }
            for field, value in (
                ("list_price", clean_list_price),
                ("stock_available", clean_stock),
                ("ships_in_days", clean_ships),
                ("condition", str(condition or "").strip()[:200]),
                ("notes", str(notes or "").strip()[:500]),
            ):
                if current.get(field) != value and value is not None:
                    revision["changed"][field] = value
                    current[field] = value
            if revision["changed"]:
                current["revisions"].append(revision)
                current["updated_at"] = time.time()
                self._facts[key] = current
                self._save_unlocked()
            return _copy(current)

    def get(self, *, cookie_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        key = _scope_key(cookie_id, "", item_id, separator="|facts|")
        with self._thread_lock:
            facts = self._facts.get(key)
            return _copy(facts) if facts else None

    def list_facts(
        self, *, cookie_id: str = "", item_id: str = "", limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self._thread_lock:
            selected = []
            for facts in self._facts.values():
                if cookie_id and str(facts.get("cookie_id") or "") != str(cookie_id):
                    continue
                if item_id and str(facts.get("item_id") or "") != str(item_id):
                    continue
                selected.append(facts)
            selected.sort(key=lambda item: item.get("updated_at") or 0)
            return [_copy(facts) for facts in selected[-max(1, int(limit)) :]]

    def _load(self) -> None:
        self._facts = {}
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("cookie_id") is not None:
                key = _scope_key(
                    item.get("cookie_id", ""), "", item.get("item_id", ""), separator="|facts|"
                )
                self._facts[key] = item

    def _save_unlocked(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.storage_path.with_name(
            f".{self.storage_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(list(self._facts.values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self.storage_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def _scope_key(cookie_id: str, chat_id: str, item_id: str, separator: str = "|chat|") -> str:
    return separator.join(
        (str(cookie_id or "").strip(), str(chat_id or "").strip(), str(item_id or "").strip())
    )


def _validate_scope(cookie_id: str, chat_id: str, item_id: str) -> Dict[str, str]:
    clean = {
        "cookie_id": str(cookie_id or "").strip(),
        "chat_id": str(chat_id or "").strip(),
        "item_id": str(item_id or "").strip(),
    }
    if not clean["cookie_id"]:
        raise ValueError("price commitment requires cookie_id")
    key = _scope_key(clean["cookie_id"], clean["chat_id"], clean["item_id"])
    return {**clean, "key": key}


def _validate_amount(amount: float) -> float:
    value = round(float(amount), 2)
    if not MIN_PLAUSIBLE_AMOUNT <= value <= MAX_PLAUSIBLE_AMOUNT:
        raise ValueError(f"price amount out of plausible range: {value}")
    return value


def _history_has_source(record: Mapping[str, Any], source: str) -> bool:
    return any(
        str(entry.get("source") or "") == str(source) for entry in record.get("history", [])
    )


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


price_commitment_store = PriceCommitmentStore()
product_facts_store = ProductFactsStore()
