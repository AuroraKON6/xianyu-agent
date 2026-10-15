"""Intake use confirmation: archive originals, bind uses to immutable snapshots (G1A).

This module adds ONE minimal business record: a material-use confirmation
that a trusted operator checked an archived supplier original plus a
declaration and allows specific uses of specific immutable offer snapshots
until a bounded time. It is not supplier authentication, not a product
approval and never a platform-action authorization; it grants no executor
permission to publish, purchase, pay or refund.

Guarantees (acceptance A10-A19):
- The original bytes are archived content-addressed and re-hashed before
  every use; a same-size byte flip invalidates eligibility.
- Preview binds the exact input digest (batch + snapshots + declaration +
  archived artifact). Any later change must produce a fresh preview.
- Confirm is idempotent by operator idempotency key; the same key with a
  different payload is a conflict, never a silent second record. A replay
  of the same request returns the same confirmation even though the
  preview was consumed.
- The confirmed validity never extends the supplier's own quote expiry or
  the 24h quote-age policy; re-importing an old original cannot refresh it.
- Eligibility is recomputed from current facts on every call; no ready
  state is ever cached.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .candidate_activation import QUOTE_MAX_AGE_SECONDS
from .candidate_evidence_store import (
    CandidateEvidenceConflict,
    CandidateEvidenceError,
    CandidateEvidenceIntegrityError,
    CandidateEvidenceStore,
    contains_sensitive_evidence_text,
)
from .commerce.contracts import utc_now
from .commerce.repositories import JsonCommerceRepositories, LockedJsonList
from .quote_provenance import assess_quote_provenance, source_time
from .supplier_import import MAX_IMPORT_BYTES, SupplierImportService
from .supplier_intake import IntakeDeclaration, assess_supplier_intake

POLICY_VERSION = "intake-confirmation-v1"
PREVIEW_TTL_SECONDS = 30 * 60
ALLOWED_USES = ("local_analysis", "external_model", "business_use")
MAX_MATERIAL_BYTES = 2 * 1024 * 1024
MAX_MATERIALS = 8
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


class IntakeConfirmationError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _moment(now: Optional[datetime] = None) -> datetime:
    moment = now or utc_now()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise IntakeConfirmationError("confirmation time must be timezone-aware")
    return moment.astimezone(timezone.utc)


class MaterialUpload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    kind: str = Field(pattern=r"^(supplier_identity_material|supplier_use_permission_material)$")
    filename: str = Field(min_length=1, max_length=180)
    mime_type: str = Field(default="", max_length=120)
    content_b64: str = Field(min_length=1, max_length=4 * ((MAX_MATERIAL_BYTES + 2) // 3 + 8))


class IntakeConfirmationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

    filename: str = Field(min_length=1, max_length=180)
    mime_type: str = Field(default="", max_length=120)
    content_b64: str = Field(min_length=1, max_length=4 * ((MAX_IMPORT_BYTES + 2) // 3 + 8))
    declaration: IntakeDeclaration
    materials: List[MaterialUpload] = Field(default_factory=list, max_length=MAX_MATERIALS)


class IntakeConfirmationConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

    preview_id: str = Field(min_length=4, max_length=64)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{8,128}$")
    allowed_uses: List[str] = Field(min_length=1, max_length=3)
    valid_until: str = Field(min_length=10, max_length=40)
    account_scope_mode: str = Field(default="none", pattern=r"^(none|all|list)$")
    account_scope_accounts: List[str] = Field(default_factory=list, max_length=32)
    verification_note: str = Field(min_length=10, max_length=1000)


class IntakeConfirmationRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

    reason: str = Field(min_length=10, max_length=1000)


class IntakeConfirmationService:
    def __init__(
        self,
        repos: JsonCommerceRepositories,
        imports: SupplierImportService,
        evidence_store: CandidateEvidenceStore,
        data_dir: Path,
        *,
        preview_ttl_seconds: int = PREVIEW_TTL_SECONDS,
    ) -> None:
        self._repos = repos
        self._imports = imports
        self._evidence = evidence_store
        self._records: LockedJsonList = LockedJsonList(Path(data_dir) / "commerce" / "intake_confirmations.json")
        self._previews: Dict[str, Dict[str, Any]] = {}
        self._preview_ttl_seconds = int(preview_ttl_seconds)
        self._lock = RLock()

    # ------------------------------------------------------------------ batch

    def _batch(self, batch_id: str) -> Dict[str, Any]:
        found = [b for b in self._imports.list_batches() if str(b.get("record_id")) == batch_id]
        if not found:
            raise IntakeConfirmationError(f"import batch not found: {batch_id}", status_code=404)
        if len(found) != 1:
            raise IntakeConfirmationError("ambiguous import batch", status_code=409)
        return found[0]

    def _snapshots_of(self, batch: Mapping[str, Any]) -> List[Dict[str, Any]]:
        ids = list(batch.get("snapshot_ids") or [])
        known = {s["record_id"]: s for s in self._repos.offers.list()}
        missing = [i for i in ids if i not in known]
        if missing:
            raise IntakeConfirmationError(
                "batch snapshots missing from ledger; investigate without changing data",
                status_code=409,
            )
        return [known[i] for i in ids]

    def batch_view(self, batch_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        moment = _moment(now)
        batch = self._batch(batch_id)
        snapshots = self._snapshots_of(batch)
        rows = []
        for snapshot in snapshots:
            prov = assess_quote_provenance(snapshot, now=moment)
            rows.append(
                {
                    "snapshot_id": snapshot["record_id"],
                    "sku": snapshot.get("source_sku_id"),
                    "quote_eligible": prov["eligible"],
                    "quote_reasons": prov["reasons"],
                    "expires_at": snapshot.get("expires_at"),
                }
            )
        return {
            "batch_id": str(batch.get("record_id")),
            "status": str(batch.get("status")),
            "supplier": str(batch.get("supplier")),
            "content_sha256": str(batch.get("content_sha256")),
            "snapshots": rows,
            "confirmations": self.list_confirmations(batch_id=batch_id),
        }

    # ---------------------------------------------------------------- preview

    def preview(
        self,
        batch_id: str,
        *,
        content: bytes,
        filename: str,
        declaration: IntakeDeclaration,
        mime_type: str = "",
        materials: Sequence[Mapping[str, Any]] = (),
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        moment = _moment(now)
        if not isinstance(content, bytes) or not content:
            raise IntakeConfirmationError("original quote bytes are required", status_code=400)
        if len(content) > MAX_IMPORT_BYTES:
            raise IntakeConfirmationError("original quote exceeds the import size limit", status_code=400)
        # Best-effort secret scan for originals that decode as UTF-8 text;
        # GBK or binary originals keep their exact bytes and skip the scan.
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = ""
        if decoded and contains_sensitive_evidence_text(decoded):
            raise IntakeConfirmationError(
                "original quote text contains credentials or personal identifiers; redact before archiving",
                status_code=400,
            )

        batch = self._batch(batch_id)
        if str(batch.get("status")) != "committed":
            raise IntakeConfirmationError(
                f"batch is {batch.get('status')!r}; only a committed batch can be confirmed", status_code=409
            )
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != str(batch.get("content_sha256")):
            raise IntakeConfirmationError(
                "original bytes do not match the imported batch content hash", status_code=409
            )
        if not str(declaration.batch_id or "").strip():
            declaration = declaration.model_copy(
                update={"batch_id": str(batch.get("record_id")), "supplier": str(batch.get("supplier"))}
            )
        if not str(declaration.issued_at or "").strip():
            raise IntakeConfirmationError(
                "declaration issued_at is required to archive the original", status_code=400
            )

        snapshots = self._snapshots_of(batch)
        assessment = assess_supplier_intake(batch, snapshots, declaration, quote_bytes=content, now=moment)
        batch_fail_codes = sorted(
            {c["code"] for c in assessment["checks"] if not c.get("snapshot_id") and c["state"] == "fail"}
        )
        if batch_fail_codes:
            raise IntakeConfirmationError(
                "batch-level contradictions block confirmation: " + ", ".join(batch_fail_codes), status_code=409
            )
        row_fail: Dict[str, List[str]] = {}
        for check in assessment["checks"]:
            sid = str(check.get("snapshot_id") or "")
            if sid and check["state"] == "fail":
                row_fail.setdefault(sid, []).append(check["code"])
        declared_ids = set(declaration.offers.keys())
        confirmable = [
            s["record_id"]
            for s in snapshots
            if s["record_id"] not in row_fail and s["record_id"] in declared_ids
        ]
        excluded = [
            {
                "snapshot_id": s["record_id"],
                "sku": s.get("source_sku_id"),
                "reasons": sorted(row_fail.get(s["record_id"], ["not_declared_in_intake_declaration"])),
            }
            for s in snapshots
            if s["record_id"] not in confirmable
        ]

        try:
            staged = self._evidence.stage_owner_bytes(
                owner_type="supplier_batch",
                owner_id=str(batch.get("record_id")),
                kind="supplier_quote_original",
                content=content,
                original_filename=filename,
                mime_type=mime_type,
                observed_at=declaration.issued_at,
                note=f"intake original {declaration.origin_ref}".strip(),
                now=moment,
            )
        except (CandidateEvidenceError, CandidateEvidenceIntegrityError, CandidateEvidenceConflict) as exc:
            raise IntakeConfirmationError(f"original archive rejected: {exc}", status_code=400) from exc
        artifact = staged["artifact"]

        archived_materials = []
        for material in materials:
            try:
                material_bytes = base64.b64decode(str(material.get("content_b64") or ""), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IntakeConfirmationError(
                    f"invalid base64 material: {material.get('filename')}", status_code=400
                ) from exc
            if len(material_bytes) > MAX_MATERIAL_BYTES:
                raise IntakeConfirmationError(f"material exceeds size limit: {material.get('filename')}", status_code=400)
            try:
                staged_material = self._evidence.stage_owner_bytes(
                    owner_type="supplier_batch",
                    owner_id=str(batch.get("record_id")),
                    kind=str(material.get("kind")),
                    content=material_bytes,
                    original_filename=str(material.get("filename") or "material.bin"),
                    mime_type=str(material.get("mime_type") or ""),
                    observed_at=declaration.received_at or declaration.issued_at,
                    note="intake declaration material",
                    now=moment,
                )
            except (CandidateEvidenceError, CandidateEvidenceIntegrityError, CandidateEvidenceConflict) as exc:
                raise IntakeConfirmationError(f"material archive rejected: {exc}", status_code=400) from exc
            archived_materials.append(staged_material["artifact"])

        declaration_digest = _digest(declaration.model_dump())
        # Codex C2: bind each confirmable snapshot's payload_hash and revision
        # into the digest so a changed snapshot invalidates the preview.
        snapshot_bindings = {
            s["record_id"]: {
                "payload_hash": str(s.get("payload_hash") or ""),
                "revision": int(s.get("revision") or 1),
            }
            for s in snapshots
            if s["record_id"] in confirmable
        }
        digest = _digest(
            {
                "batch_id": str(batch.get("record_id")),
                "content_sha256": actual_hash,
                "declaration_sha256": declaration_digest,
                "artifact_sha256": artifact["sha256"],
                "confirmable_snapshot_ids": confirmable,
                "material_ids": [m["artifact_id"] for m in archived_materials],
                "snapshot_bindings": snapshot_bindings,
            }
        )
        preview = {
            "preview_id": f"icp_{uuid.uuid4().hex[:16]}",
            "batch_id": str(batch.get("record_id")),
            "policy_version": POLICY_VERSION,
            "original_artifact_id": artifact["artifact_id"],
            "original_sha256": actual_hash,
            "declaration_sha256": declaration_digest,
            "material_artifact_ids": [m["artifact_id"] for m in archived_materials],
            "supplier_identity_ref": declaration.supplier_identity_ref,
            "use_permission_ref": declaration.use_permission_ref,
            "external_llm_permitted": declaration.external_llm_permitted,
            "local_review_permitted": declaration.local_review_permitted,
            "confirmable_snapshot_ids": confirmable,
            "snapshot_bindings": snapshot_bindings,
            "excluded_snapshots": excluded,
            "gaps": assessment["gaps"],
            "assessment_status": assessment["status"],
            "digest": digest,
            "created_at": moment.isoformat(),
            "expires_at_timestamp": moment.timestamp() + self._preview_ttl_seconds,
        }
        with self._lock:
            # A newer preview for the same batch supersedes every earlier
            # unconsumed preview: the operator must confirm what they last
            # saw, never a stale binding (A12).
            for stale_id in [
                pid
                for pid, value in self._previews.items()
                if str(value.get("batch_id")) == str(batch.get("record_id"))
            ]:
                del self._previews[stale_id]
            self._previews[preview["preview_id"]] = preview
        return preview

    # ---------------------------------------------------------------- confirm

    def confirm(
        self,
        *,
        preview_id: str,
        preview_digest: str,
        idempotency_key: str,
        allowed_uses: Sequence[str],
        valid_until: str,
        verification_note: str,
        operator: str,
        expected_batch_id: str = "",
        account_scope_mode: str = "none",
        account_scope_accounts: Sequence[str] = (),
        now: Optional[datetime] = None,
        on_confirmed=None,
    ) -> Dict[str, Any]:
        moment = _moment(now)
        if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(str(idempotency_key or "")):
            raise IntakeConfirmationError("invalid idempotency key", status_code=400)
        uses = sorted({str(u) for u in allowed_uses})
        if not uses or any(u not in ALLOWED_USES for u in uses):
            raise IntakeConfirmationError(
                f"allowed_uses must be a non-empty subset of {list(ALLOWED_USES)}", status_code=400
            )
        if len(str(verification_note or "").strip()) < 10:
            raise IntakeConfirmationError("verification note must state what the operator checked", status_code=400)
        requested_expiry = source_time(valid_until)
        if requested_expiry is None or requested_expiry <= moment:
            raise IntakeConfirmationError("valid_until must be a timezone-aware future timestamp", status_code=400)
        if account_scope_mode not in ("none", "all", "list"):
            raise IntakeConfirmationError("invalid account scope mode", status_code=400)
        scope_accounts = sorted({str(a).strip() for a in account_scope_accounts if str(a).strip()})
        if account_scope_mode == "list" and not scope_accounts:
            raise IntakeConfirmationError("account scope 'list' requires at least one account", status_code=400)

        with self._lock:
            # idempotent replay wins before any preview lookup: the preview is
            # consumed by the first successful confirm, and replaying the same
            # request must still return the same confirmation (A16). The
            # fingerprint covers the whole confirm payload, not just the
            # preview digest, so the same key with a changed payload conflicts.
            request_fingerprint = _digest(
                {
                    "preview_digest": str(preview_digest),
                    "allowed_uses": uses,
                    "requested_valid_until": requested_expiry.isoformat(),
                    "account_scope_mode": account_scope_mode,
                    "account_scope_accounts": scope_accounts,
                    "verification_note": str(verification_note).strip(),
                }
            )
            for existing in self._records.find(lambda r: str(r.get("idempotency_key")) == idempotency_key):
                if str(existing.get("request_fingerprint")) == request_fingerprint:
                    if expected_batch_id and str(existing.get("batch_id")) != expected_batch_id:
                        raise IntakeConfirmationError(
                            "idempotency key belongs to a different batch", status_code=409
                        )
                    return {"confirmation": existing, "idempotent": True}
                raise IntakeConfirmationError(
                    "idempotency key already used with a different payload", status_code=409
                )

            preview = self._previews.get(preview_id)
            if preview is None:
                raise IntakeConfirmationError(
                    f"confirmation preview not found or expired: {preview_id}", status_code=409
                )
            if expected_batch_id and str(preview["batch_id"]) != expected_batch_id:
                raise IntakeConfirmationError("preview does not belong to this batch", status_code=409)
            if moment.timestamp() > float(preview["expires_at_timestamp"]):
                del self._previews[preview_id]
                raise IntakeConfirmationError("confirmation preview expired; re-run preview", status_code=409)
            if str(preview["digest"]) != str(preview_digest):
                raise IntakeConfirmationError(
                    "preview digest mismatch; inputs changed after preview, re-run preview", status_code=409
                )

            # re-verify current facts at confirm time (A12/A13/A17)
            batch = self._batch(str(preview["batch_id"]))
            if str(batch.get("status")) != "committed":
                raise IntakeConfirmationError(
                    f"batch became {batch.get('status')!r} after preview; re-run preview", status_code=409
                )
            artifact = self._evidence.get_owner_artifact(
                "supplier_batch", str(batch.get("record_id")), str(preview["original_artifact_id"])
            )
            if artifact is None or artifact.get("integrity", {}).get("ok") is not True:
                raise IntakeConfirmationError(
                    "archived original failed integrity re-verification", status_code=409
                )

            # Codex C2: re-verify each snapshot's payload_hash/revision against
            # the preview binding; any change after preview must invalidate.
            preview_bindings = preview.get("snapshot_bindings") or {}
            for snapshot_id in list(preview["confirmable_snapshot_ids"]):
                current = self._repos.offers.get(snapshot_id)
                if current is None:
                    raise IntakeConfirmationError(
                        f"snapshot {snapshot_id} missing from ledger; re-run preview", status_code=409
                    )
                bound = preview_bindings.get(snapshot_id) or {}
                if (str(current.get("payload_hash") or "") != str(bound.get("payload_hash") or "")
                        or int(current.get("revision") or 1) != int(bound.get("revision") or 1)):
                    raise IntakeConfirmationError(
                        f"snapshot {snapshot_id} changed after preview (payload_hash/revision mismatch); re-run preview",
                        status_code=409,
                    )

            confirmable_ids = list(preview["confirmable_snapshot_ids"])
            if not confirmable_ids:
                raise IntakeConfirmationError("no confirmable snapshots in this preview", status_code=409)
            snapshots = {s["record_id"]: s for s in self._snapshots_of(batch)}
            expiry_cap = requested_expiry
            for snapshot_id in confirmable_ids:
                snapshot = snapshots[snapshot_id]
                snap_expiry = source_time(snapshot.get("expires_at"))
                if snap_expiry is not None:
                    expiry_cap = min(expiry_cap, snap_expiry)
                meta = (snapshot.get("attributes") or {}).get("quote_provenance") or {}
                observed = source_time(meta.get("source_observed_at"))
                if observed is not None:
                    expiry_cap = min(expiry_cap, observed + timedelta(seconds=QUOTE_MAX_AGE_SECONDS))
            if expiry_cap <= moment:
                raise IntakeConfirmationError(
                    "quoted material already past its own validity; confirmation cannot extend it", status_code=409
                )

            # use-specific preconditions on declared permissions
            if "external_model" in uses and preview.get("external_llm_permitted") is not True:
                raise IntakeConfirmationError(
                    "external model use requires the declaration to explicitly permit it", status_code=409
                )
            if "business_use" in uses:
                if preview.get("local_review_permitted") is not True:
                    raise IntakeConfirmationError(
                        "business use requires the declaration to explicitly permit local review", status_code=409
                    )
                if preview.get("assessment_status") != "ready_for_human_review":
                    raise IntakeConfirmationError(
                        "business use requires a fully ready assessment (no open gaps)", status_code=409
                    )

            confirmation_id = f"ic_{uuid.uuid4().hex[:16]}"
            record = {
                "record_id": confirmation_id,
                "record_type": "intake_use_confirmation",
                "policy_version": POLICY_VERSION,
                "batch_id": str(batch.get("record_id")),
                "provider_id": str(batch.get("provider_id")),
                "supplier": str(batch.get("supplier")),
                "snapshot_ids": confirmable_ids,
                "original_artifact_id": str(preview["original_artifact_id"]),
                "original_sha256": str(preview["original_sha256"]),
                "declaration_sha256": str(preview["declaration_sha256"]),
                "material_artifact_ids": list(preview.get("material_artifact_ids") or []),
                "supplier_identity_ref": str(preview.get("supplier_identity_ref") or ""),
                "use_permission_ref": str(preview.get("use_permission_ref") or ""),
                "allowed_uses": uses,
                "account_scope": {"mode": account_scope_mode, "accounts": scope_accounts},
                "operator": str(operator),
                "verification_note": str(verification_note).strip(),
                "confirmed_at": moment.isoformat(),
                "valid_until": expiry_cap.isoformat(),
                "requested_valid_until": requested_expiry.isoformat(),
                "status": "confirmed",
                "revoked_at": None,
                "revoked_by": None,
                "revoke_reason": None,
                "superseded_by": None,
                "idempotency_key": idempotency_key,
                "input_sha256": str(preview["digest"]),
                "request_fingerprint": request_fingerprint,
                "gaps_at_confirmation": list(preview.get("gaps") or []),
            }
            try:
                self._records.append(record)
            except OSError as exc:
                raise IntakeConfirmationError(
                    "confirmation storage unavailable; retry with the same idempotency key", status_code=503
                ) from exc
            del self._previews[preview_id]
            if on_confirmed is not None:
                try:
                    on_confirmed(record)
                except Exception:  # observer failures never roll back the confirmation
                    pass
            return {"confirmation": record, "idempotent": False}

    # ------------------------------------------------------------- revoke/etc

    def revoke(self, confirmation_id: str, *, operator: str, reason: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        moment = _moment(now)
        if len(str(reason or "").strip()) < 10:
            raise IntakeConfirmationError("revocation requires a substantive reason", status_code=400)

        def mutate(record: Dict[str, Any]) -> None:
            status = str(record.get("status"))
            if status == "revoked":
                return
            if status != "confirmed":
                raise IntakeConfirmationError(f"cannot revoke record in status {status!r}", status_code=409)
            record["status"] = "revoked"
            record["revoked_at"] = moment.isoformat()
            record["revoked_by"] = str(operator)
            record["revoke_reason"] = str(reason).strip()

        try:
            return self._records.update(confirmation_id, mutate)
        except KeyError:
            raise IntakeConfirmationError(f"confirmation not found: {confirmation_id}", status_code=404) from None

    def detail(self, confirmation_id: str) -> Dict[str, Any]:
        found = self._records.find(lambda r: str(r.get("record_id")) == confirmation_id)
        if not found:
            raise IntakeConfirmationError(f"confirmation not found: {confirmation_id}", status_code=404)
        return found[0]

    def list_confirmations(self, *, batch_id: str = "") -> List[Dict[str, Any]]:
        def predicate(record: Mapping[str, Any]) -> bool:
            return (not batch_id) or str(record.get("batch_id")) == batch_id

        return sorted(self._records.find(predicate), key=lambda r: str(r.get("confirmed_at") or ""), reverse=True)

    # ------------------------------------------------------------ eligibility

    def eligibility(
        self,
        snapshot_id: str,
        *,
        use: str,
        account: str = "",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        moment = _moment(now)
        use = str(use or "")
        if use not in ALLOWED_USES:
            raise IntakeConfirmationError(f"unknown use: {use}", status_code=400)
        reasons: List[str] = []
        snapshot = self._repos.offers.get(snapshot_id)
        if not snapshot:
            return {"eligible": False, "reasons": ["snapshot_not_found"], "confirmation_id": "",
                    "policy_version": POLICY_VERSION, "checked_at": moment.isoformat()}
        source_ref = str(snapshot.get("source_ref") or "")
        batch_id = source_ref.split(":", 1)[1] if source_ref.startswith("import_batch:") else ""
        if not batch_id:
            reasons.append("snapshot_not_from_import_batch")
        else:
            batch_status = self._imports.batch_status(batch_id)
            if batch_status != "committed":
                reasons.append(f"batch_{batch_status or 'unknown'}")
        prov = assess_quote_provenance(snapshot, now=moment)
        if not prov["eligible"]:
            reasons.extend(prov["reasons"])

        candidates = [
            record
            for record in self._records.find(lambda r: str(r.get("status")) == "confirmed")
            if snapshot_id in list(record.get("snapshot_ids") or [])
        ]
        record_reasons_map: Dict[str, List[str]] = {}
        for record in candidates:
            record_reasons = _record_ineligibility_reasons(record, moment, use, account)
            artifact = self._evidence.get_owner_artifact(
                "supplier_batch", str(record.get("batch_id") or ""), str(record.get("original_artifact_id") or "")
            )
            if artifact is None:
                record_reasons.append("original_artifact_missing")
            elif artifact.get("integrity", {}).get("ok") is not True:
                record_reasons.append("original_file_modified")
            elif str(artifact.get("sha256")) != str(record.get("original_sha256")):
                record_reasons.append("original_hash_binding_mismatch")
            record_reasons_map[str(record.get("record_id"))] = record_reasons
        qualifying = [
            record for record in candidates if not record_reasons_map[str(record.get("record_id"))]
        ]
        # Codex C1: global reasons (batch revoked, provenance expired) block
        # ALL candidates regardless of per-record health; a revoked batch
        # must invalidate every confirmation referencing its snapshots.
        if qualifying and reasons:
            return {
                "eligible": False,
                "reasons": reasons,
                "confirmation_id": "",
                "policy_version": POLICY_VERSION,
                "checked_at": moment.isoformat(),
            }
        if qualifying:
            best = max(qualifying, key=lambda r: str(r.get("valid_until") or ""))
            return {
                "eligible": True,
                "reasons": [],
                "confirmation_id": best["record_id"],
                "valid_until": best.get("valid_until"),
                "allowed_uses": list(best.get("allowed_uses") or []),
                "policy_version": POLICY_VERSION,
                "checked_at": moment.isoformat(),
            }
        if not candidates:
            reasons.append("no_active_confirmation")
        else:
            seen = set(reasons)
            for extra_reasons in record_reasons_map.values():
                for extra in extra_reasons:
                    if extra not in seen:
                        seen.add(extra)
                        reasons.append(extra)
        return {
            "eligible": False,
            "reasons": reasons,
            "confirmation_id": "",
            "policy_version": POLICY_VERSION,
            "checked_at": moment.isoformat(),
        }


def _record_ineligibility_reasons(record: Mapping[str, Any], moment: datetime, use: str, account: str) -> List[str]:
    reasons: List[str] = []
    if use not in list(record.get("allowed_uses") or []):
        reasons.append(f"use_not_permitted:{use}")
    expiry = source_time(record.get("valid_until"))
    if expiry is None or expiry <= moment:
        reasons.append("confirmation_expired")
    scope = record.get("account_scope") or {}
    if use == "business_use":
        mode = str(scope.get("mode") or "none")
        if mode == "none":
            reasons.append("account_scope_none")
        elif mode == "list" and str(account) not in list(scope.get("accounts") or []):
            reasons.append("account_out_of_scope")
    return reasons


def build_intake_confirmation_router(
    repos: JsonCommerceRepositories,
    imports: SupplierImportService,
    evidence_store: CandidateEvidenceStore,
    data_dir: Path,
    *,
    token_sha256: str = "",
    operator: str = "",
    service: Optional[IntakeConfirmationService] = None,
    on_confirmed=None,
):
    from .pi_jobs import JOB_TOKEN_HEADER

    router = APIRouter(prefix="/commerce", tags=["intake use confirmation"])
    service = service or IntakeConfirmationService(repos, imports, evidence_store, Path(data_dir))
    if on_confirmed is not None and not callable(on_confirmed):
        raise IntakeConfirmationError("on_confirmed must be callable", status_code=500)
    configured_digest = str(token_sha256 or "").strip().lower()
    configured_operator = str(operator or "").strip()

    def authenticate(request: Request) -> str:
        if (not re.fullmatch(r"[0-9a-f]{64}", configured_digest)
                or not configured_operator or len(configured_operator) > 160):
            raise HTTPException(status_code=503, detail="intake confirmation is disabled")
        if JOB_TOKEN_HEADER.lower() in request.headers:
            raise HTTPException(status_code=403, detail="agent job sessions cannot confirm material use")
        scheme, separator, token = request.headers.get("Authorization", "").partition(" ")
        if (scheme.lower() != "bearer" or not separator or not 32 <= len(token) <= 512
                or not hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), configured_digest)):
            raise HTTPException(status_code=401, detail="operator credential required",
                                headers={"WWW-Authenticate": "Bearer"})
        return configured_operator

    def invoke(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except IntakeConfirmationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get("/supplier-imports/{batch_id}/confirmation")
    def batch_confirmation_status(batch_id: str):
        return invoke(service.batch_view, batch_id)

    @router.post("/supplier-imports/{batch_id}/confirmation/preview")
    def preview_confirmation(batch_id: str, request: IntakeConfirmationPreviewRequest, identity: str = Depends(authenticate)):
        try:
            content = base64.b64decode(request.content_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid base64 quote bytes") from exc
        return invoke(
            service.preview,
            batch_id,
            content=content,
            filename=request.filename,
            declaration=request.declaration,
            mime_type=request.mime_type,
            materials=[material.model_dump() for material in request.materials],
        )

    @router.post("/supplier-imports/{batch_id}/confirmation/confirm")
    def confirm_confirmation(batch_id: str, request: IntakeConfirmationConfirmRequest, identity: str = Depends(authenticate)):
        return invoke(
            service.confirm,
            preview_id=request.preview_id,
            preview_digest=request.preview_digest,
            idempotency_key=request.idempotency_key,
            allowed_uses=request.allowed_uses,
            valid_until=request.valid_until,
            account_scope_mode=request.account_scope_mode,
            account_scope_accounts=request.account_scope_accounts,
            verification_note=request.verification_note,
            operator=identity,
            expected_batch_id=batch_id,
            on_confirmed=on_confirmed,
        )

    @router.post("/intake-confirmations/{confirmation_id}/revoke")
    def revoke_confirmation(confirmation_id: str, request: IntakeConfirmationRevokeRequest, identity: str = Depends(authenticate)):
        return invoke(service.revoke, confirmation_id, operator=identity, reason=request.reason)

    @router.get("/intake-confirmations/eligibility")
    def confirmation_eligibility(snapshot_id: str, use: str, account: str = ""):
        return invoke(service.eligibility, snapshot_id, use=use, account=account)

    @router.get("/intake-confirmations/{confirmation_id}")
    def confirmation_detail(confirmation_id: str):
        return invoke(service.detail, confirmation_id)

    @router.get("/intake-confirmations")
    def confirmation_list(batch_id: str = ""):
        return {"confirmations": service.list_confirmations(batch_id=batch_id)}

    return router
