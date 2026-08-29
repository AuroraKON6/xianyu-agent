from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional


TERMS_MAX_AGE_SECONDS = 24 * 60 * 60
SELLER_TYPES = {"unknown", "personal_nonbusiness", "business"}
RETURN_POLICIES = {"unknown", "seven_day", "lawful_exception"}

_LOCAL_TZ = datetime.now().astimezone().tzinfo
_SENSITIVE_EVIDENCE_PATTERNS = (
    re.compile(r"(?i)(cookie|authorization|access[_-]?token|refresh[_-]?token)\s*[:=]"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
)


class AccountCommercialTermsStore:
    """JSON-backed, account-and-category scoped commercial terms evidence.

    The store intentionally keeps only operator attestations, numerical fee
    terms, timestamps, and a sanitized evidence reference. It must never hold
    cookies, identity numbers, payment credentials, phone numbers, or a full
    return address.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = RLock()
        self._snapshots: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._load()

    def get_snapshot(self, cookie_id: str, category: str) -> Optional[Dict[str, Any]]:
        clean_cookie_id = _required_text(cookie_id, "cookie_id", 120)
        clean_category = _required_text(category, "category", 120)
        with self._lock:
            account = self._snapshots.get(clean_cookie_id, {})
            snapshot = account.get(_category_key(clean_category))
            return _copy(snapshot) if snapshot else None

    def list_snapshots(self, cookie_id: str = "") -> List[Dict[str, Any]]:
        clean_cookie_id = str(cookie_id or "").strip()
        with self._lock:
            if clean_cookie_id:
                snapshots = list(self._snapshots.get(clean_cookie_id, {}).values())
            else:
                snapshots = [
                    snapshot
                    for account in self._snapshots.values()
                    for snapshot in account.values()
                ]
            snapshots.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
            return _copy(snapshots)

    def save_snapshot(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        normalized = _normalize_snapshot(payload)
        version_payload = {
            key: normalized[key]
            for key in normalized
            if key not in {"snapshot_version", "updated_at"}
        }
        normalized["snapshot_version"] = hashlib.sha256(
            json.dumps(
                version_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        normalized["updated_at"] = time.time()
        with self._lock:
            account = self._snapshots.setdefault(normalized["cookie_id"], {})
            account[_category_key(normalized["category"])] = normalized
            self._persist()
            return _copy(normalized)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_accounts = data.get("accounts") if isinstance(data, Mapping) else None
        if not isinstance(raw_accounts, Mapping):
            return
        for cookie_id, raw_categories in raw_accounts.items():
            if not isinstance(raw_categories, Mapping):
                continue
            clean_cookie_id = str(cookie_id or "").strip()
            if not clean_cookie_id:
                continue
            account: Dict[str, Dict[str, Any]] = {}
            for key, snapshot in raw_categories.items():
                if not isinstance(snapshot, Mapping):
                    continue
                try:
                    normalized = _normalize_snapshot(snapshot)
                except ValueError:
                    continue
                normalized["snapshot_version"] = str(snapshot.get("snapshot_version") or "")
                normalized["updated_at"] = _finite_nonnegative(snapshot.get("updated_at")) or 0.0
                account[str(key)] = normalized
            if account:
                self._snapshots[clean_cookie_id] = account

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(
                {"schema_version": 1, "accounts": self._snapshots},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)


def evaluate_commercial_terms(
    *,
    snapshot: Optional[Mapping[str, Any]],
    cookie_id: str,
    category: str,
    candidate: Optional[Mapping[str, Any]] = None,
    checked_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate current account/category commercial terms without side effects."""
    clean_cookie_id = str(cookie_id or "").strip()
    clean_category = str(category or "").strip()
    snapshot_dict = dict(snapshot or {})
    checked_dt = _parse_datetime(checked_at) or datetime.now(_LOCAL_TZ)
    if checked_dt.tzinfo is None:
        checked_dt = checked_dt.replace(tzinfo=_LOCAL_TZ)
    checked_at_str = _as_utc_z(checked_dt)

    gates: List[Dict[str, Any]] = []
    present = bool(snapshot_dict)
    gates.append(_gate(
        "terms_snapshot_present",
        present,
        "账号与类目商业条款快照已记录" if present else "缺少账号与类目的商业条款快照",
        "snapshot=present" if present else "snapshot=missing",
    ))

    scope_matches = bool(
        present
        and str(snapshot_dict.get("cookie_id") or "").strip() == clean_cookie_id
        and _category_key(str(snapshot_dict.get("category") or "")) == _category_key(clean_category)
    )
    gates.append(_gate(
        "terms_scope_matches",
        scope_matches,
        "条款快照与当前账号、类目一致" if scope_matches else "条款快照不属于当前账号或类目",
        "account={}/category={}".format(
            "match" if scope_matches else "mismatch",
            clean_category or "missing",
        ),
    ))

    observed = _parse_datetime(snapshot_dict.get("observed_at"))
    age_seconds: Optional[float] = None
    if observed is not None:
        age_seconds = (checked_dt.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    fresh = bool(
        observed is not None
        and age_seconds is not None
        and 0 <= age_seconds <= TERMS_MAX_AGE_SECONDS
    )
    if observed is None:
        freshness_evidence = "observed_at=missing"
    elif age_seconds is not None and age_seconds < 0:
        freshness_evidence = f"observed_at={_as_utc_z(observed)} age=future"
    else:
        freshness_evidence = f"observed_at={_as_utc_z(observed)} age={(age_seconds or 0) / 3600:.1f}h"
    gates.append(_gate(
        "terms_snapshot_fresh",
        fresh,
        "商业条款在 24 小时内核验" if fresh else "商业条款未核验、已过期或时间无效",
        freshness_evidence,
    ))

    gates.append(_boolean_snapshot_gate(
        snapshot_dict,
        "account_owner_confirmed",
        "账号由实际所有人授权运营",
        "尚未确认账号所有权或运营授权",
    ))
    seller_type = str(snapshot_dict.get("seller_type") or "unknown")
    gates.append(_gate(
        "business_seller_confirmed",
        seller_type == "business",
        "已按经营性卖家身份核验" if seller_type == "business" else "当前账号未确认经营性卖家身份",
        f"seller_type={seller_type}",
    ))
    gates.append(_boolean_snapshot_gate(
        snapshot_dict,
        "seller_agreement_confirmed",
        "当前卖家服务协议已确认",
        "尚未确认当前卖家服务协议",
    ))
    gates.append(_boolean_snapshot_gate(
        snapshot_dict,
        "payment_service_ready",
        "收款与支付服务可用",
        "收款或支付服务尚未就绪",
    ))
    gates.append(_boolean_snapshot_gate(
        snapshot_dict,
        "category_publishable",
        "当前账号可发布该类目",
        "尚未确认当前账号可发布该类目",
    ))

    return_policy = str(snapshot_dict.get("return_policy") or "unknown")
    exception_note = str(snapshot_dict.get("return_exception_basis") or "").strip()
    return_policy_ready = return_policy == "seven_day" or (
        return_policy == "lawful_exception" and bool(exception_note)
    )
    buyer_protection_ready = bool(
        return_policy_ready
        and snapshot_dict.get("return_address_ready")
        and snapshot_dict.get("after_sales_ready")
    )
    gates.append(_gate(
        "buyer_protection_ready",
        buyer_protection_ready,
        "退货规则、退货地址和售后能力已确认" if buyer_protection_ready else "买家保障与售后条件不完整",
        "return_policy={}/exception_basis={}/return_address={}/after_sales={}".format(
            return_policy,
            "present" if exception_note else "absent",
            "ready" if snapshot_dict.get("return_address_ready") else "missing",
            "ready" if snapshot_dict.get("after_sales_ready") else "missing",
        ),
    ))

    guarantee_required = bool(snapshot_dict.get("guarantee_required"))
    guarantee_ready = bool(snapshot_dict.get("guarantee_ready"))
    guarantee_amount = _finite_nonnegative(snapshot_dict.get("guarantee_amount"))
    guarantee_gate_ready = bool(
        not guarantee_required
        or (guarantee_ready and guarantee_amount is not None and guarantee_amount > 0)
    )
    gates.append(_gate(
        "guarantee_ready",
        guarantee_gate_ready,
        "保证金/买保要求已满足" if guarantee_gate_ready else "该账号类目要求保证金或买保，但尚未满足",
        "required={}/ready={}/amount={}".format(
            "yes" if guarantee_required else "no",
            "yes" if guarantee_ready else "no",
            f"{guarantee_amount:.2f}" if guarantee_amount is not None else "invalid",
        ),
    ))

    rate = _finite_nonnegative(snapshot_dict.get("platform_fee_rate"))
    fixed_fee = _finite_nonnegative(snapshot_dict.get("platform_fixed_fee"))
    evidence_note = str(snapshot_dict.get("evidence_note") or "").strip()
    evidence_ref = str(snapshot_dict.get("evidence_ref") or "").strip()
    fee_evidence_ready = bool(
        rate is not None
        and rate < 1
        and fixed_fee is not None
        and evidence_note
        and evidence_ref
    )
    gates.append(_gate(
        "fee_evidence_ready",
        fee_evidence_ready,
        "账号类目实际费率证据已记录" if fee_evidence_ready else "缺少可用的账号类目费率证据",
        "rate={}/fixed_fee={}/note={}/ref={}".format(
            f"{rate:.6f}" if rate is not None else "invalid",
            f"{fixed_fee:.2f}" if fixed_fee is not None else "invalid",
            "present" if evidence_note else "absent",
            "present" if evidence_ref else "absent",
        ),
    ))

    if candidate is not None:
        economics = candidate.get("economics") if isinstance(candidate.get("economics"), Mapping) else {}
        candidate_rate = _finite_nonnegative(economics.get("platform_fee_rate"))
        candidate_fixed = _finite_nonnegative(economics.get("platform_fixed_fee"))
        candidate_return_policy = str(candidate.get("commercial_return_policy") or "unknown")
        return_policy_matches = candidate_return_policy == str(snapshot_dict.get("return_policy") or "unknown")
        version_matches = bool(
            snapshot_dict.get("snapshot_version")
            and str(candidate.get("commercial_terms_version") or "") == str(snapshot_dict.get("snapshot_version") or "")
        )
        fee_matches = bool(
            rate is not None
            and fixed_fee is not None
            and candidate_rate is not None
            and candidate_fixed is not None
            and math.isclose(rate, candidate_rate, rel_tol=0, abs_tol=1e-9)
            and math.isclose(fixed_fee, candidate_fixed, rel_tol=0, abs_tol=0.005)
            and version_matches
            and return_policy_matches
        )
        gates.append(_gate(
            "candidate_fee_matches",
            fee_matches,
            "候选利润和售后政策已按当前条款更新" if fee_matches else "候选利润或售后政策尚未按当前条款更新",
            "rate={}/fixed_fee={}/version={}/return_policy={}".format(
                "match" if rate == candidate_rate else "mismatch",
                "match" if fixed_fee == candidate_fixed else "mismatch",
                "match" if version_matches else "mismatch",
                "match" if return_policy_matches else "mismatch",
            ),
        ))

    failed_gate_codes = [gate["code"] for gate in gates if gate["status"] != "pass"]
    return {
        "ready": not failed_gate_codes,
        "status": "ready" if not failed_gate_codes else "blocked",
        "cookie_id": clean_cookie_id,
        "category": clean_category,
        "checked_at": checked_at_str,
        "snapshot": _copy(snapshot_dict) if snapshot_dict else None,
        "gates": gates,
        "failed_gate_codes": failed_gate_codes,
        "next_step": next(
            (
                {
                    "code": gate["code"],
                    "message": gate["message"],
                    "evidence": gate["evidence"],
                }
                for gate in gates
                if gate["status"] != "pass"
            ),
            {"code": "ready", "message": "账号商业条款证据已就绪", "evidence": "all_pass"},
        ),
    }


def _normalize_snapshot(payload: Mapping[str, Any]) -> Dict[str, Any]:
    cookie_id = _required_text(payload.get("cookie_id"), "cookie_id", 120)
    category = _required_text(payload.get("category"), "category", 120)
    seller_type = str(payload.get("seller_type") or "unknown").strip()
    if seller_type not in SELLER_TYPES:
        raise ValueError(f"unsupported seller_type: {seller_type}")
    return_policy = str(payload.get("return_policy") or "unknown").strip()
    if return_policy not in RETURN_POLICIES:
        raise ValueError(f"unsupported return_policy: {return_policy}")

    observed = _parse_datetime(payload.get("observed_at"))
    if observed is None:
        raise ValueError("observed_at must be a valid ISO timestamp")
    age_seconds = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    if age_seconds < -300:
        raise ValueError("observed_at cannot be in the future")

    fee_rate = _finite_nonnegative(payload.get("platform_fee_rate"))
    if fee_rate is None or fee_rate >= 1:
        raise ValueError("platform_fee_rate must be >= 0 and < 1")
    fixed_fee = _finite_nonnegative(payload.get("platform_fixed_fee"))
    if fixed_fee is None:
        raise ValueError("platform_fixed_fee must be >= 0")
    guarantee_amount = _finite_nonnegative(payload.get("guarantee_amount"))
    if guarantee_amount is None:
        raise ValueError("guarantee_amount must be >= 0")

    evidence_note = _required_text(payload.get("evidence_note"), "evidence_note", 500)
    evidence_ref = _required_text(payload.get("evidence_ref"), "evidence_ref", 300)
    return_exception_basis = str(payload.get("return_exception_basis") or "").strip()[:300]
    _reject_sensitive_evidence(evidence_note)
    _reject_sensitive_evidence(evidence_ref)
    _reject_sensitive_evidence(return_exception_basis)

    return {
        "schema_version": 1,
        "cookie_id": cookie_id,
        "category": category,
        "seller_type": seller_type,
        "account_owner_confirmed": bool(payload.get("account_owner_confirmed")),
        "seller_agreement_confirmed": bool(payload.get("seller_agreement_confirmed")),
        "payment_service_ready": bool(payload.get("payment_service_ready")),
        "category_publishable": bool(payload.get("category_publishable")),
        "return_policy": return_policy,
        "return_exception_basis": return_exception_basis,
        "return_address_ready": bool(payload.get("return_address_ready")),
        "after_sales_ready": bool(payload.get("after_sales_ready")),
        "guarantee_required": bool(payload.get("guarantee_required")),
        "guarantee_ready": bool(payload.get("guarantee_ready")),
        "guarantee_amount": round(guarantee_amount, 2),
        "platform_fee_rate": fee_rate,
        "platform_fixed_fee": round(fixed_fee, 2),
        "observed_at": _as_utc_z(observed),
        "evidence_note": evidence_note,
        "evidence_ref": evidence_ref,
    }


def _required_text(value: Any, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters")
    return text


def _reject_sensitive_evidence(value: str) -> None:
    if any(pattern.search(value) for pattern in _SENSITIVE_EVIDENCE_PATTERNS):
        raise ValueError("evidence fields must not contain credentials, phone numbers, or identity numbers")


def _boolean_snapshot_gate(
    snapshot: Mapping[str, Any],
    code: str,
    pass_message: str,
    fail_message: str,
) -> Dict[str, Any]:
    passed = bool(snapshot.get(code))
    return _gate(code, passed, pass_message if passed else fail_message, f"{code}={'yes' if passed else 'no'}")


def _gate(code: str, passed: bool, message: str, evidence: str) -> Dict[str, Any]:
    return {
        "code": code,
        "status": "pass" if passed else "fail",
        "message": message,
        "evidence": evidence,
    }


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text[:-1] + "+00:00" if text[-1:] in {"Z", "z"} else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TZ)
    return parsed


def _as_utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _finite_nonnegative(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _category_key(category: str) -> str:
    return str(category or "").strip().casefold()


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
