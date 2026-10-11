"""Read-only, evidence-bound supplier intake assessment; never an authorization."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .candidate_activation import QUOTE_MAX_AGE_SECONDS, STOCK_MAX_AGE_SECONDS
from .commerce.contracts import SCOPE_DOMESTIC_XIANYU
from .quote_provenance import assess_quote_provenance
from .supplier_import import MAX_IMPORT_BYTES


POLICY_VERSION = "supplier-intake-readonly-v1"
MAX_ASSESSED_OFFERS = 1000
UNKNOWN_TEXT_VALUES = frozenset(("", "unknown", "unverified", "unspecified", "pending", "tbd",
                                "未知", "待确认", "未提供", "n/a", "none", "null", "-", "?"))


class OfferEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    currency: str = Field(default="", max_length=12)
    price_unit: str = Field(default="", max_length=40)
    quantity_unit: str = Field(default="", max_length=40)
    freight_basis: Literal["unknown", "per_unit"] = "unknown"
    freight_destination: str = Field(default="", max_length=160)
    tax_terms: str = Field(default="", max_length=1000)
    delivery_terms: str = Field(default="", max_length=1000)
    after_sales_terms: str = Field(default="", max_length=1000)
    stock_observed_at: str = Field(default="", max_length=40)
    stock_valid_until: str = Field(default="", max_length=40)
    evidence_ref: str = Field(default="", max_length=256)


class IntakeDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    batch_id: str = Field(default="", max_length=160)
    content_sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    supplier: str = Field(default="", max_length=160)
    source_kind: Literal["unknown", "demo", "supplier_original"] = "unknown"
    origin_ref: str = Field(default="", max_length=256)
    supplier_identity_ref: str = Field(default="", max_length=256)
    use_permission_ref: str = Field(default="", max_length=256)
    local_review_permitted: Optional[bool] = None
    external_llm_permitted: Optional[bool] = None
    issued_at: str = Field(default="", max_length=40)
    received_at: str = Field(default="", max_length=40)
    valid_until: str = Field(default="", max_length=40)
    offers: Dict[str, OfferEvidence] = Field(default_factory=dict, max_length=MAX_ASSESSED_OFFERS)


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    declaration: IntakeDeclaration = Field(default_factory=IntakeDeclaration)
    content_b64: str = Field(default="", max_length=4 * ((MAX_IMPORT_BYTES + 2) // 3))


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _timestamp(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _unit(value):
    text = str(value or "").strip().lower()
    if text in UNKNOWN_TEXT_VALUES:
        return ""
    return "piece" if text in ("pc", "pcs", "piece", "pieces", "件", "个") else text


def _money(value):
    if not isinstance(value, dict) or value.get("state") != "known" or value.get("currency") != "CNY":
        return False
    try:
        amount = Decimal(str(value.get("amount")))
        return amount.is_finite() and amount >= 0
    except (InvalidOperation, ValueError):
        return False


def assess_supplier_intake(batch, snapshots, declaration=None, *, quote_bytes=None, now=None):
    if not isinstance(batch, dict) or not isinstance(snapshots, list) or not all(isinstance(row, dict) for row in snapshots):
        raise ValueError("expected persisted batch and snapshot records")
    declaration = declaration or IntakeDeclaration()
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("assessment time must include timezone")
    moment = moment.astimezone(timezone.utc)
    if quote_bytes is not None and (not isinstance(quote_bytes, bytes) or len(quote_bytes) > MAX_IMPORT_BYTES):
        raise ValueError("quote must be bytes within the import size limit")
    checks = []

    def check(code, condition, detail, *, missing=False, snapshot_id=""):
        checks.append({"code": code, "state": "unknown" if missing else "pass" if condition else "fail",
                       "snapshot_id": snapshot_id, "detail": detail})

    def require_text(code, value, detail, *, snapshot_id=""):
        present = str(value or "").strip().lower() not in UNKNOWN_TEXT_VALUES
        check(code, present, detail, missing=not present, snapshot_id=snapshot_id)

    batch_id = str(batch.get("record_id") or "")
    supplier = str(batch.get("supplier") or "")
    expected_ids = batch.get("snapshot_ids") or []
    if not isinstance(expected_ids, list) or not all(isinstance(value, str) for value in expected_ids):
        raise ValueError("invalid persisted snapshot IDs")
    if len(expected_ids) > MAX_ASSESSED_OFFERS:
        raise ValueError("split import into batches of at most 1000 offers")
    expected_set = set(expected_ids)
    selected = [snapshot for snapshot in snapshots if snapshot.get("record_id") in expected_set]
    check("committed_batch", batch.get("status") == "committed", "Only a committed, non-revoked import can enter review.")
    check("batch_snapshots", bool(expected_ids) and len(expected_ids) == len(expected_set)
          and len(selected) == len(expected_ids) and {row.get("record_id") for row in selected} == expected_set,
          "Every distinct snapshot listed by the batch must exist exactly once.")
    check("declared_batch", declaration.batch_id == batch_id, "Declaration must name this exact import batch.", missing=not declaration.batch_id)
    check("declared_supplier", bool(supplier) and declaration.supplier == supplier,
          "Supplier identity must match the imported batch.", missing=not declaration.supplier)
    check("supplier_source", declaration.source_kind == "supplier_original",
          "Demo or unknown origin is not a real supplier quote.", missing=declaration.source_kind == "unknown")
    for field in ("origin_ref", "supplier_identity_ref", "use_permission_ref"):
        require_text(field, getattr(declaration, field), "Supply independently verifiable, redacted evidence; a label is not verification.")
    check("local_use_permission", declaration.local_review_permitted is True,
          "Local review permission must be independently checked.", missing=declaration.local_review_permitted is None)
    expected_hash = batch.get("content_sha256")
    actual_hash = hashlib.sha256(quote_bytes).hexdigest() if quote_bytes is not None else None
    check("original_file_sha256", bool(re.fullmatch(r"[0-9a-f]{64}", str(expected_hash or ""))) and actual_hash == expected_hash,
          "Hash the original bytes, not a reformatted export or a supplied hash string.", missing=quote_bytes is None)
    check("declared_file_sha256", bool(expected_hash) and declaration.content_sha256 == expected_hash,
          "Declaration and imported file digest must agree.", missing=not declaration.content_sha256)
    issued = _timestamp(declaration.issued_at)
    received = _timestamp(declaration.received_at)
    valid_until = _timestamp(declaration.valid_until)
    for code, raw, parsed in (("issued_at", declaration.issued_at, issued), ("received_at", declaration.received_at, received),
                              ("valid_until", declaration.valid_until, valid_until)):
        check(code, parsed is not None, "Use an ISO timestamp with an explicit timezone.", missing=not raw)
    check("quote_time_order", bool(issued and received and valid_until and issued <= received <= moment < valid_until),
          "Quote must be issued and received before this assessment, and not expired.", missing=not all((issued, received, valid_until)))
    check("quote_freshness", bool(issued and 0 <= (moment - issued).total_seconds() <= QUOTE_MAX_AGE_SECONDS),
          "Re-importing an old quote does not reset the existing quote-age policy.", missing=issued is None)
    check("unexpected_evidence_rows", set(declaration.offers).issubset(expected_set),
          "Evidence must reference snapshots from this batch, not a different supplier or attempt.")

    for snapshot in selected:
        record_id = snapshot["record_id"]
        terms = declaration.offers.get(record_id, OfferEvidence())
        attributes = snapshot.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise ValueError("invalid persisted snapshot attributes")
        observed = _timestamp(snapshot.get("observed_at"))
        provenance = assess_quote_provenance(snapshot, now=moment)
        imported = _timestamp(snapshot.get("fetched_at"))

        def row_check(code, condition, detail, *, missing=False):
            check(code, condition, detail, missing=missing, snapshot_id=record_id)

        row_check("snapshot_binding", snapshot.get("source_ref") == f"import_batch:{batch_id}"
                  and snapshot.get("provider_id") == batch.get("provider_id") == f"supplier_file:{supplier}"
                  and snapshot.get("seller_ref") == supplier and snapshot.get("scope") == SCOPE_DOMESTIC_XIANYU,
                  "Snapshot must belong to this domestic supplier and exact import batch.")
        row_check("demo_marker", provenance["source_kind"] != "demo", "Explicit demo provenance cannot be relabelled real by a declaration.")
        row_check("persisted_quote_provenance", provenance["eligible"],
                  "Shared quote provenance gate: " + (", ".join(provenance["reasons"]) or "structurally eligible, not provider-verified"))
        row_check("declared_origin_binding", declaration.source_kind == provenance["source_kind"]
                  and declaration.origin_ref == provenance["source_reference"],
                  "Declaration must agree with the immutable snapshot's source kind and source reference.",
                  missing=not declaration.origin_ref or declaration.source_kind == "unknown")
        row_check("snapshot_time", bool(observed and imported and issued and received
                  and issued <= observed <= imported <= moment and issued <= received <= imported
                  and (moment - observed).total_seconds() <= QUOTE_MAX_AGE_SECONDS),
                  "Keep source observation and import time separate; receipt and observation must not follow import.",
                  missing=any(value is None for value in (observed, imported, issued, received)))
        expires = _timestamp(snapshot.get("expires_at"))
        row_check("snapshot_expiry", not snapshot.get("expires_at") or bool(expires and moment < expires),
                  "An explicit snapshot expiry cannot be overridden by the declaration.")
        row_check("declared_validity_bound", bool(valid_until and expires and valid_until <= expires),
                  "Supplemental evidence cannot extend the validity of the imported original quote.",
                  missing=valid_until is None or expires is None)
        for field, value in (("sku", snapshot.get("source_sku_id")), ("specification", attributes.get("spec")),
                             ("condition", snapshot.get("condition"))):
            require_text(field, value, "Confirm the exact SKU, specification and condition; names alone are insufficient.", snapshot_id=record_id)
        row_check("price", _money(snapshot.get("eligible_price")), "Need finite nonnegative CNY price; missing price is not zero.")
        row_check("currency", terms.currency == "CNY", "Explicit CNY evidence is required; no currency conversion is assumed.", missing=not terms.currency)
        price_unit = _unit(terms.price_unit)
        quantity_unit = _unit(terms.quantity_unit)
        row_check("quantity_units", bool(price_unit and quantity_unit and price_unit == quantity_unit == _unit(snapshot.get("unit"))),
                  "Price, MOQ and stock must have an explicit common unit.", missing=not price_unit or not quantity_unit)
        minimum = snapshot.get("moq")
        stock = snapshot.get("stock_quantity")
        row_check("moq", type(minimum) is int and minimum > 0, "MOQ must be a positive integer in the confirmed unit.", missing=minimum is None)
        row_check("stock", snapshot.get("stock_state") == "in_stock" and type(stock) is int and stock > 0,
                  "Need an explicit available stock quantity, not a keyword or estimated range.", missing=stock is None)
        row_check("stock_covers_moq", type(stock) is int and type(minimum) is int and stock >= minimum > 0,
                  "Current stock must cover at least one minimum order.", missing=stock is None or minimum is None)
        stock_time = _timestamp(terms.stock_observed_at)
        stock_until = _timestamp(terms.stock_valid_until)
        row_check("stock_freshness", bool(stock_time and stock_until and stock_time <= moment < stock_until
                  and (moment - stock_time).total_seconds() <= STOCK_MAX_AGE_SECONDS),
                  "Stock needs dated, still-valid evidence within the existing stock-age policy.",
                  missing=not terms.stock_observed_at or not terms.stock_valid_until)
        row_check("freight", _money(snapshot.get("shipping_quote")), "Collect-on-delivery and unpriced freight are unknown, not free.")
        row_check("freight_basis", terms.freight_basis == "per_unit", "Freight must be explicitly allocated per price unit; per-order fees need a quantity allocation first.", missing=terms.freight_basis == "unknown")
        for field in ("freight_destination", "tax_terms", "delivery_terms", "after_sales_terms", "evidence_ref"):
            require_text(field, getattr(terms, field), "Supply the original term and evidence reference; no promises are inferred.", snapshot_id=record_id)

    failures = [entry for entry in checks if entry["state"] != "pass"]
    status = "blocked" if any(entry["state"] == "fail" for entry in checks) else "needs_evidence" if failures else "ready_for_human_review"
    inputs = {"batch": batch, "snapshots": selected, "declaration": declaration.model_dump(), "original_sha256": actual_hash}
    return {
        "policy_version": POLICY_VERSION, "batch_id": batch_id, "assessed_at": moment.isoformat(), "status": status,
        "input_sha256": _digest(inputs), "checks": checks, "gaps": failures,
        "snapshot_count": len(selected), "original_file_sha256": actual_hash,
        "source_kind_declared": declaration.source_kind, "provider_verified": False,
        "human_confirmation_required": True, "external_llm_authorized": False, "platform_action_authorized": False,
        "external_llm_permission_declared": declaration.external_llm_permitted,
        "warning": "Read-only structural assessment. Evidence is caller-declared, not provider-verified; a digest is not proof of authenticity. "
                   "This does not persist evidence, validate raw-cell equivalence, calculate profit, approve a supplier, dispatch a model or authorize trading. Reassess current inputs before use.",
    }


def build_supplier_intake_router(repos, imports):
    router = APIRouter(prefix="/commerce/supplier-imports", tags=["read-only supplier intake"])

    def assess(batch_id, request):
        matches = [batch for batch in imports.list_batches() if batch.get("record_id") == batch_id]
        if not matches:
            raise HTTPException(status_code=404, detail="import batch not found")
        if len(matches) != 1:
            raise HTTPException(status_code=409, detail="ambiguous import batch")
        try:
            content = base64.b64decode(request.content_b64, validate=True) if request.content_b64 else None
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid base64 quote bytes") from exc
        try:
            return assess_supplier_intake(matches[0], repos.offers.list(), request.declaration, quote_bytes=content)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409, detail="invalid or oversized persisted input; investigate without changing the ledger") from exc

    @router.get("/{batch_id}/readiness")
    def readiness(batch_id: str):
        return assess(batch_id, IntakeRequest())

    @router.post("/{batch_id}/readiness")
    def assess_evidence(batch_id: str, request: IntakeRequest):
        return assess(batch_id, request)

    return router
