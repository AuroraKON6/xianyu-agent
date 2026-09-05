"""Offline intake bundle for one exact Commercial Alpha candidate.

Validation is deliberately read-only. It checks a local manifest and its files,
projects the existing 12-gate candidate activation result, and never creates a
candidate, evidence artifact, approval, batch, or platform action.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .candidate_activation import (
    MARKET_MAX_AGE_SECONDS,
    QUOTE_MAX_AGE_SECONDS,
    STOCK_MAX_AGE_SECONDS,
    evaluate_candidate_activation,
)
from .candidate_evidence_store import (
    EVIDENCE_KIND_CONFIG,
    EVIDENCE_KINDS,
    MAX_EVIDENCE_BYTES,
    CandidateEvidenceError,
    artifact_reference,
    contains_sensitive_evidence_text,
    validate_candidate_evidence_bytes,
    validate_evidence_source_url,
)
from .resale import DEFAULT_PLATFORM_FEE_RATE, DEFAULT_REFUND_RATE, ResaleManager


SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
MAX_MANIFEST_BYTES = 256 * 1024
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_LOCAL_TZ = datetime.now().astimezone().tzinfo

_TOP_LEVEL_FIELDS = {"schema_version", "candidate", "evidence"}
_CANDIDATE_FIELDS = {
    "title",
    "description",
    "category",
    "source_platform",
    "source_url",
    "supplier_name",
    "supplier_sku",
    "supplier_price",
    "supplier_shipping",
    "target_sale_price",
    "specifications",
    "images",
    "sales_platform",
    "market_scope",
    "supplier_verified",
    "supplier_verification_note",
    "minimum_order_quantity",
    "stock_status",
    "stock_quantity",
    "fulfillment_verified",
    "fulfillment_verification_note",
    "neutral_shipping_confirmed",
    "dispatch_time_hours",
    "supplier_return_policy",
    "damage_liability_note",
    "image_rights_type",
    "image_rights_evidence",
    "competitor_links",
    "competitor_price_min",
    "competitor_price_max",
    "estimated_demand_signal",
    "platform_fee_rate",
    "platform_fixed_fee",
    "expected_refund_rate",
}
_EVIDENCE_FIELDS = {"kind", "path", "observed_at", "note", "source_url"}
_STRING_FIELDS = {
    "title",
    "description",
    "category",
    "source_platform",
    "source_url",
    "supplier_name",
    "supplier_sku",
    "sales_platform",
    "market_scope",
    "supplier_verification_note",
    "stock_status",
    "fulfillment_verification_note",
    "supplier_return_policy",
    "damage_liability_note",
    "image_rights_type",
    "image_rights_evidence",
    "estimated_demand_signal",
}
_FREE_TEXT_FIELDS = {
    "description",
    "supplier_verification_note",
    "fulfillment_verification_note",
    "damage_liability_note",
    "image_rights_evidence",
    "estimated_demand_signal",
}
_BOOLEAN_FIELDS = {
    "supplier_verified",
    "fulfillment_verified",
    "neutral_shipping_confirmed",
}
_REQUIRED_NUMBERS = {
    "supplier_price": "positive",
    "supplier_shipping": "nonnegative",
    "target_sale_price": "positive",
    "minimum_order_quantity": "positive_integer",
    "stock_quantity": "positive_integer",
    "dispatch_time_hours": "positive",
    "competitor_price_min": "positive",
    "competitor_price_max": "positive",
}
_ASSUMPTION_DEFAULTS = {
    "platform_fee_rate": DEFAULT_PLATFORM_FEE_RATE,
    "platform_fixed_fee": 0.0,
    "expected_refund_rate": DEFAULT_REFUND_RATE,
}
_FRESHNESS_LIMITS = {
    "supplier_quote": QUOTE_MAX_AGE_SECONDS,
    "supplier_stock": STOCK_MAX_AGE_SECONDS,
    "market_baseline": MARKET_MAX_AGE_SECONDS,
}


class CommercialAlphaIntakeError(ValueError):
    """The intake command itself cannot proceed."""


@dataclass(frozen=True)
class _ProjectionSettings:
    min_profit_amount: float
    min_profit_rate: float


class _DuplicateJsonKey(ValueError):
    pass


def build_intake_manifest_template() -> Dict[str, Any]:
    """Return a strict, fill-in-place manifest for exactly one candidate."""
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate": {
            "title": "",
            "description": "",
            "category": "",
            "source_platform": "pdd",
            "source_url": "",
            "supplier_name": "",
            "supplier_sku": "",
            "supplier_price": 0,
            "supplier_shipping": 0,
            "target_sale_price": 0,
            "specifications": {
                "model": "",
                "size_or_capacity": "",
            },
            "images": ["images/product-01.jpg"],
            "sales_platform": "xianyu",
            "market_scope": "domestic",
            "supplier_verified": False,
            "supplier_verification_note": "",
            "minimum_order_quantity": 1,
            "stock_status": "",
            "stock_quantity": 0,
            "fulfillment_verified": False,
            "fulfillment_verification_note": "",
            "neutral_shipping_confirmed": False,
            "dispatch_time_hours": 0,
            "supplier_return_policy": "",
            "damage_liability_note": "",
            "image_rights_type": "",
            "image_rights_evidence": "",
            "competitor_links": ["", ""],
            "competitor_price_min": 0,
            "competitor_price_max": 0,
            "estimated_demand_signal": "",
            "platform_fee_rate": DEFAULT_PLATFORM_FEE_RATE,
            "platform_fixed_fee": 0,
            "expected_refund_rate": DEFAULT_REFUND_RATE,
        },
        "evidence": [
            {
                "kind": kind,
                "path": f"evidence/{kind}.txt",
                "observed_at": "",
                "note": "",
                "source_url": "",
            }
            for kind in EVIDENCE_KINDS
        ],
    }


def initialize_intake_bundle(output: Path | str) -> Dict[str, Any]:
    """Create a new empty bundle without overwriting any existing content."""
    root = Path(output)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise CommercialAlphaIntakeError(
            "output already exists and is not an empty directory; choose a new bundle directory"
        )
    root.mkdir(parents=True, exist_ok=True)
    (root / "evidence").mkdir()
    (root / "images").mkdir()
    manifest_path = root / MANIFEST_FILENAME
    temporary = root / (MANIFEST_FILENAME + ".tmp")
    temporary.write_text(
        json.dumps(build_intake_manifest_template(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return {
        "ok": True,
        "status": "initialized",
        "bundle_directory": str(root.resolve()),
        "created": [MANIFEST_FILENAME, "evidence/", "images/"],
        "next_step": "Fill manifest.json and add the referenced files, then run validate.",
    }


def validate_intake_bundle(
    bundle: Path | str,
    *,
    checked_at: Optional[str | datetime] = None,
    min_profit_amount: float = 20.0,
    min_profit_rate: float = 0.30,
) -> Dict[str, Any]:
    """Validate one intake bundle and project existing activation gates."""
    checked = _checked_datetime(checked_at)
    min_amount = _validated_threshold(min_profit_amount, "min_profit_amount")
    min_rate = _validated_threshold(min_profit_rate, "min_profit_rate", maximum=1.0)
    issues: List[Dict[str, str]] = []
    evidence_files: List[Dict[str, Any]] = []
    image_files: List[Dict[str, Any]] = []
    manifest_bytes = b""

    root = Path(bundle)
    if not root.exists() or not root.is_dir():
        issues.append(_issue(
            "bundle_directory_missing",
            "bundle",
            "Bundle directory does not exist or is not a directory.",
            "Run the init command or select the directory containing manifest.json.",
        ))
        return _report(
            checked=checked,
            min_amount=min_amount,
            min_rate=min_rate,
            manifest_bytes=manifest_bytes,
            manifest=None,
            issues=issues,
            evidence_files=evidence_files,
            image_files=image_files,
            projection=None,
        )

    root_resolved = root.resolve()
    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.exists() or not manifest_path.is_file():
        issues.append(_issue(
            "manifest_missing",
            MANIFEST_FILENAME,
            "manifest.json is missing.",
            "Run init or restore the bundle manifest.",
        ))
        return _report(
            checked=checked,
            min_amount=min_amount,
            min_rate=min_rate,
            manifest_bytes=manifest_bytes,
            manifest=None,
            issues=issues,
            evidence_files=evidence_files,
            image_files=image_files,
            projection=None,
        )
    if manifest_path.is_symlink() or not _is_within(manifest_path.resolve(), root_resolved):
        issues.append(_issue(
            "manifest_path_unsafe",
            MANIFEST_FILENAME,
            "manifest.json must be a regular file inside the bundle.",
            "Replace the link with a regular local manifest file.",
        ))
        return _report(
            checked=checked,
            min_amount=min_amount,
            min_rate=min_rate,
            manifest_bytes=manifest_bytes,
            manifest=None,
            issues=issues,
            evidence_files=evidence_files,
            image_files=image_files,
            projection=None,
        )
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        issues.append(_issue(
            "manifest_too_large",
            MANIFEST_FILENAME,
            "manifest.json exceeds 256 KiB.",
            "Remove embedded content and keep only structured facts and relative paths.",
        ))
        return _report(
            checked=checked,
            min_amount=min_amount,
            min_rate=min_rate,
            manifest_bytes=manifest_bytes,
            manifest=None,
            issues=issues,
            evidence_files=evidence_files,
            image_files=image_files,
            projection=None,
        )

    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(
            manifest_bytes.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except UnicodeDecodeError:
        issues.append(_issue(
            "manifest_encoding_invalid",
            MANIFEST_FILENAME,
            "manifest.json must be UTF-8.",
            "Save the manifest as UTF-8 JSON.",
        ))
        manifest = None
    except _DuplicateJsonKey:
        issues.append(_issue(
            "manifest_duplicate_key",
            MANIFEST_FILENAME,
            "manifest.json contains a duplicate object key.",
            "Keep exactly one value for every JSON field.",
        ))
        manifest = None
    except json.JSONDecodeError:
        issues.append(_issue(
            "manifest_json_invalid",
            MANIFEST_FILENAME,
            "manifest.json is not valid JSON.",
            "Fix the JSON syntax without embedding comments or trailing commas.",
        ))
        manifest = None

    if not isinstance(manifest, Mapping):
        if manifest is not None:
            issues.append(_issue(
                "manifest_shape_invalid",
                MANIFEST_FILENAME,
                "The manifest root must be a JSON object.",
                "Use the generated schema_version, candidate and evidence object structure.",
            ))
        return _report(
            checked=checked,
            min_amount=min_amount,
            min_rate=min_rate,
            manifest_bytes=manifest_bytes,
            manifest=None,
            issues=issues,
            evidence_files=evidence_files,
            image_files=image_files,
            projection=None,
        )

    manifest = dict(manifest)
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue(
            "schema_version_unsupported",
            "schema_version",
            f"schema_version must equal {SCHEMA_VERSION}.",
            "Regenerate a current bundle and move the real facts into it.",
        ))
    for field in sorted(set(manifest) - _TOP_LEVEL_FIELDS):
        issues.append(_issue(
            "unknown_manifest_field",
            field,
            "The manifest contains an unsupported top-level field.",
            "Remove the field or move the value into the documented candidate schema.",
        ))

    raw_candidate = manifest.get("candidate")
    if not isinstance(raw_candidate, Mapping):
        issues.append(_issue(
            "candidate_object_missing",
            "candidate",
            "candidate must be a JSON object.",
            "Restore the generated candidate object and fill one exact SKU.",
        ))
        raw_candidate = {}
    raw_candidate = dict(raw_candidate)
    for field in sorted(set(raw_candidate) - _CANDIDATE_FIELDS):
        issues.append(_issue(
            "unknown_candidate_field",
            f"candidate.{field}",
            "The candidate contains an unsupported field.",
            "Remove the field or correct the field name from the generated template.",
        ))

    raw_evidence = manifest.get("evidence")
    if not isinstance(raw_evidence, list):
        issues.append(_issue(
            "evidence_list_missing",
            "evidence",
            "evidence must be a JSON array.",
            "Restore the five generated evidence entries.",
        ))
        raw_evidence = []

    used_paths: set[Path] = set()
    validated_by_kind: Dict[str, Dict[str, Any]] = {}
    seen_kinds: set[str] = set()
    for index, raw_entry in enumerate(raw_evidence):
        field_prefix = f"evidence[{index}]"
        if not isinstance(raw_entry, Mapping):
            issues.append(_issue(
                "evidence_entry_invalid",
                field_prefix,
                "Each evidence entry must be a JSON object.",
                "Use kind, path, observed_at, note and source_url fields.",
            ))
            continue
        entry = dict(raw_entry)
        for field in sorted(set(entry) - _EVIDENCE_FIELDS):
            issues.append(_issue(
                "unknown_evidence_field",
                f"{field_prefix}.{field}",
                "The evidence entry contains an unsupported field.",
                "Remove the field or correct its name from the generated template.",
            ))
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in EVIDENCE_KINDS:
            issues.append(_issue(
                "evidence_kind_invalid",
                f"{field_prefix}.kind",
                "Evidence kind is missing or unsupported.",
                "Use one of the five required evidence kinds from the template.",
            ))
            continue
        if kind in seen_kinds:
            issues.append(_issue(
                "evidence_kind_duplicate",
                f"{field_prefix}.kind",
                f"Evidence kind {kind} appears more than once.",
                "Keep exactly one current file for each evidence kind.",
            ))
            continue
        seen_kinds.add(kind)

        resolved = _resolve_bundle_file(
            root=root,
            root_resolved=root_resolved,
            raw_path=entry.get("path"),
            field=f"{field_prefix}.path",
            issues=issues,
        )
        if resolved is None:
            continue
        path, relative_path = resolved
        if path in used_paths:
            issues.append(_issue(
                "bundle_file_reused",
                f"{field_prefix}.path",
                "The same file is referenced more than once in the bundle.",
                "Capture a distinct file for every evidence kind and product image.",
            ))
            continue
        used_paths.add(path)
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            issues.append(_issue(
                "evidence_file_too_large",
                f"{field_prefix}.path",
                "Evidence file exceeds 10 MiB.",
                "Export a smaller readable file without credentials or personal data.",
            ))
            continue
        observed_at = entry.get("observed_at")
        note = entry.get("note", "")
        source_url = entry.get("source_url", "")
        if not isinstance(observed_at, str) or not isinstance(note, str) or not isinstance(source_url, str):
            issues.append(_issue(
                "evidence_metadata_invalid",
                field_prefix,
                "observed_at, note and source_url must be strings.",
                "Restore string values and use an ISO timestamp with timezone.",
            ))
            continue
        try:
            validated = validate_candidate_evidence_bytes(
                kind=kind,
                content=path.read_bytes(),
                original_filename=path.name,
                observed_at=observed_at,
                note=note,
                source_url=source_url,
                now=checked,
            )
        except (OSError, CandidateEvidenceError) as exc:
            message = str(exc) if isinstance(exc, CandidateEvidenceError) else "Evidence file could not be read."
            issues.append(_issue(
                "evidence_file_invalid",
                f"{field_prefix}.path",
                message,
                "Replace the file or metadata, then run validation again.",
            ))
            continue

        observed = _checked_datetime(validated["observed_at"])
        age_seconds = (checked - observed).total_seconds()
        if age_seconds < 0:
            issues.append(_issue(
                "evidence_observed_in_future",
                f"{field_prefix}.observed_at",
                "Evidence observation time is later than the validation time.",
                "Correct the clock or capture time before continuing.",
            ))
        maximum_age = _FRESHNESS_LIMITS.get(kind)
        if maximum_age is not None and age_seconds > maximum_age:
            hours = maximum_age // 3600
            issues.append(_issue(
                "evidence_stale",
                f"{field_prefix}.observed_at",
                f"Evidence is older than the {hours}-hour launch limit.",
                "Capture a new current artifact for this exact SKU.",
            ))
        metadata = {
            "kind": kind,
            "relative_path": relative_path,
            "mime_type": validated["mime_type"],
            "byte_size": validated["byte_size"],
            "sha256": validated["sha256"],
            "observed_at": validated["observed_at"],
        }
        validated_by_kind[kind] = {**metadata, "note": validated["note"], "source_url": validated["source_url"]}
        evidence_files.append(metadata)

    for kind in EVIDENCE_KINDS:
        if kind not in seen_kinds:
            issues.append(_issue(
                "evidence_kind_missing",
                "evidence",
                f"Required evidence kind {kind} is missing.",
                f"Add one current {kind} entry and its referenced file.",
            ))

    raw_images = raw_candidate.get("images")
    valid_image_paths: List[str] = []
    if not isinstance(raw_images, list) or not raw_images:
        issues.append(_issue(
            "product_images_missing",
            "candidate.images",
            "At least one product image path is required.",
            "Add a real owned or authorized PNG, JPEG or WebP image inside images/.",
        ))
        raw_images = []
    for index, raw_path in enumerate(raw_images):
        field = f"candidate.images[{index}]"
        resolved = _resolve_bundle_file(
            root=root,
            root_resolved=root_resolved,
            raw_path=raw_path,
            field=field,
            issues=issues,
        )
        if resolved is None:
            continue
        path, relative_path = resolved
        if path.suffix.casefold() not in IMAGE_EXTENSIONS:
            issues.append(_issue(
                "product_image_extension_invalid",
                field,
                "Product image must be PNG, JPEG or WebP.",
                "Replace it with a supported real product image.",
            ))
            continue
        if path in used_paths:
            issues.append(_issue(
                "bundle_file_reused",
                field,
                "The same file is referenced more than once in the bundle.",
                "Use a distinct product image and evidence files.",
            ))
            continue
        used_paths.add(path)
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            issues.append(_issue(
                "product_image_too_large",
                field,
                "Product image exceeds 10 MiB.",
                "Export a smaller image while preserving inspectable product detail.",
            ))
            continue
        try:
            validated = validate_candidate_evidence_bytes(
                kind="image_rights",
                content=path.read_bytes(),
                original_filename=path.name,
                observed_at=_as_utc_z(checked),
                now=checked,
            )
        except (OSError, CandidateEvidenceError) as exc:
            message = str(exc) if isinstance(exc, CandidateEvidenceError) else "Product image could not be read."
            issues.append(_issue(
                "product_image_invalid",
                field,
                message,
                "Replace the file with a valid inspectable product image.",
            ))
            continue
        valid_image_paths.append(relative_path)
        image_files.append({
            "relative_path": relative_path,
            "mime_type": validated["mime_type"],
            "byte_size": validated["byte_size"],
            "sha256": validated["sha256"],
        })

    canonical_files = [
        {key: item[key] for key in ("kind", "relative_path", "sha256", "byte_size")}
        for item in sorted(evidence_files, key=lambda item: item["kind"])
    ] + [
        {key: item[key] for key in ("relative_path", "sha256", "byte_size")}
        for item in sorted(image_files, key=lambda item: item["relative_path"])
    ]
    fingerprint = _bundle_fingerprint(manifest, canonical_files)
    candidate, artifacts = _project_candidate(
        raw_candidate=raw_candidate,
        image_paths=valid_image_paths,
        evidence=validated_by_kind,
        bundle_fingerprint=fingerprint,
        issues=issues,
    )
    projection = evaluate_candidate_activation(
        candidate=candidate,
        settings=_ProjectionSettings(min_amount, min_rate),
        checked_at=_as_utc_z(checked),
        evidence_artifacts=artifacts,
        require_archived_evidence=True,
    )
    existing_fields = {issue["field"] for issue in issues}
    for gate in projection["gates"]:
        if gate["status"] == "pass" or gate["code"] == "manual_decision_current":
            continue
        field = f"candidate_gate.{gate['code']}"
        if field in existing_fields:
            continue
        issues.append(_issue(
            "candidate_gate_blocked",
            field,
            gate["title"],
            gate["action"],
        ))

    return _report(
        checked=checked,
        min_amount=min_amount,
        min_rate=min_rate,
        manifest_bytes=manifest_bytes,
        manifest=manifest,
        issues=issues,
        evidence_files=evidence_files,
        image_files=image_files,
        projection=projection,
        bundle_fingerprint=fingerprint,
    )


def _project_candidate(
    *,
    raw_candidate: Mapping[str, Any],
    image_paths: Sequence[str],
    evidence: Mapping[str, Mapping[str, Any]],
    bundle_fingerprint: str,
    issues: List[Dict[str, str]],
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    candidate: Dict[str, Any] = {}
    for field in _STRING_FIELDS:
        value = raw_candidate.get(field, "")
        if not isinstance(value, str):
            issues.append(_issue(
                "candidate_field_type_invalid",
                f"candidate.{field}",
                "Candidate text field must be a string.",
                "Use a JSON string for this field.",
            ))
            value = ""
        if field in _FREE_TEXT_FIELDS and contains_sensitive_evidence_text(value):
            issues.append(_issue(
                "candidate_sensitive_text",
                f"candidate.{field}",
                "Candidate free text appears to contain credentials or personal identifiers.",
                "Redact credentials, phone numbers and personal identifiers before import.",
            ))
            value = ""
        candidate[field] = value.strip()

    for field in _BOOLEAN_FIELDS:
        value = raw_candidate.get(field)
        if not isinstance(value, bool):
            issues.append(_issue(
                "candidate_boolean_invalid",
                f"candidate.{field}",
                "Candidate flag must be true or false.",
                "Use a JSON boolean after manually verifying the stated fact.",
            ))
            value = False
        candidate[field] = value

    specifications = raw_candidate.get("specifications")
    if not isinstance(specifications, Mapping):
        issues.append(_issue(
            "candidate_specifications_invalid",
            "candidate.specifications",
            "specifications must be a JSON object.",
            "Record at least two named, deliverable SKU specifications.",
        ))
        specifications = {}
    candidate["specifications"] = dict(specifications)

    competitor_links = raw_candidate.get("competitor_links")
    if not isinstance(competitor_links, list) or not all(isinstance(item, str) for item in competitor_links):
        issues.append(_issue(
            "candidate_competitor_links_invalid",
            "candidate.competitor_links",
            "competitor_links must be an array of URL strings.",
            "Record at least two distinct current comparable Xianyu links.",
        ))
        competitor_links = []
    clean_competitor_links: List[str] = []
    for index, link in enumerate(competitor_links):
        try:
            clean_competitor_links.append(validate_evidence_source_url(link))
        except CandidateEvidenceError:
            issues.append(_issue(
                "candidate_url_credentials",
                f"candidate.competitor_links[{index}]",
                "Competitor URL contains credentials or credential-like parameters.",
                "Use a clean public item URL with no session or authorization data.",
            ))
    candidate["competitor_links"] = clean_competitor_links
    candidate["images"] = list(image_paths)

    if candidate.get("source_url"):
        try:
            candidate["source_url"] = validate_evidence_source_url(candidate["source_url"])
        except CandidateEvidenceError:
            issues.append(_issue(
                "candidate_url_credentials",
                "candidate.source_url",
                "Supplier URL contains credentials or credential-like parameters.",
                "Use a clean public product URL with no session or authorization data.",
            ))
            candidate["source_url"] = ""

    for field, rule in _REQUIRED_NUMBERS.items():
        value = _number(raw_candidate.get(field))
        valid = value is not None
        if rule == "positive":
            valid = valid and value > 0
        elif rule == "nonnegative":
            valid = valid and value >= 0
        elif rule == "positive_integer":
            valid = valid and value >= 1 and value.is_integer()
        if not valid:
            issues.append(_issue(
                "candidate_number_invalid",
                f"candidate.{field}",
                f"{field} does not satisfy the required {rule} numeric rule.",
                "Replace the placeholder with the current exact-SKU value.",
            ))
            candidate[field] = None
        else:
            candidate[field] = int(value) if rule == "positive_integer" else value

    assumptions: Dict[str, float] = {}
    for field, default in _ASSUMPTION_DEFAULTS.items():
        value = _number(raw_candidate.get(field, default))
        valid = value is not None and value >= 0
        if field in {"platform_fee_rate", "expected_refund_rate"}:
            valid = valid and value <= 1
        if not valid:
            issues.append(_issue(
                "candidate_assumption_invalid",
                f"candidate.{field}",
                f"{field} must be a finite nonnegative value" + (" no greater than 1." if field != "platform_fixed_fee" else "."),
                "Use the current conservative project assumption; real account terms still override it.",
            ))
            value = default
        assumptions[field] = float(value)

    supplier_price = candidate.get("supplier_price")
    supplier_shipping = candidate.get("supplier_shipping")
    target_sale_price = candidate.get("target_sale_price")
    if all(value is not None for value in (supplier_price, supplier_shipping, target_sale_price)):
        candidate["economics"] = ResaleManager.compute_economics(
            float(supplier_price),
            float(supplier_shipping),
            float(target_sale_price),
            assumptions["platform_fee_rate"],
            assumptions["platform_fixed_fee"],
            assumptions["expected_refund_rate"],
        )
    else:
        candidate["economics"] = {}

    candidate_id = "preview_" + bundle_fingerprint[:16]
    candidate.update({
        "id": candidate_id,
        "candidate_id": candidate_id,
        "status": "evaluating",
        "manual_decision": None,
        "approved_evidence_fingerprint": "",
        "evidence_revision": 1,
        "commercial_terms_version": "",
    })
    artifacts: Dict[str, Dict[str, Any]] = {}
    for kind, metadata in evidence.items():
        artifact_id = "cea_" + hashlib.sha256(
            f"{candidate_id}\0{kind}\0{metadata['sha256']}".encode("utf-8")
        ).hexdigest()[:24]
        reference = artifact_reference(artifact_id)
        config = EVIDENCE_KIND_CONFIG[kind]
        candidate[config["ref_field"]] = reference
        if config["timestamp_field"]:
            candidate[config["timestamp_field"]] = metadata["observed_at"]
        artifacts[artifact_id] = {
            "artifact_id": artifact_id,
            "candidate_id": candidate_id,
            "kind": kind,
            "status": "active",
            "sha256": metadata["sha256"],
            "observed_at": metadata["observed_at"],
            "integrity": {"ok": True, "status": "verified_preview"},
        }
    return candidate, artifacts


def _resolve_bundle_file(
    *,
    root: Path,
    root_resolved: Path,
    raw_path: Any,
    field: str,
    issues: List[Dict[str, str]],
) -> Optional[Tuple[Path, str]]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        issues.append(_issue(
            "bundle_path_missing",
            field,
            "A non-empty relative file path is required.",
            "Place the file inside the bundle and record its relative path.",
        ))
        return None
    normalized = raw_path.strip().replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or re.match(r"^[A-Za-z]:", normalized)
        or "\x00" in normalized
    ):
        issues.append(_issue(
            "bundle_path_unsafe",
            field,
            "File path must stay inside the bundle and cannot use traversal or a drive path.",
            "Use a path such as evidence/supplier_quote.png.",
        ))
        return None
    path = root.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        issues.append(_issue(
            "bundle_file_missing",
            field,
            "Referenced file does not exist.",
            "Add the referenced real file or correct the relative path.",
        ))
        return None
    if not _is_within(resolved, root_resolved):
        issues.append(_issue(
            "bundle_path_outside",
            field,
            "Referenced file resolves outside the bundle.",
            "Replace links with a regular file stored inside the bundle.",
        ))
        return None
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            issues.append(_issue(
                "bundle_symlink_not_allowed",
                field,
                "Symbolic links are not allowed in an intake bundle.",
                "Copy the real file into the bundle instead of linking it.",
            ))
            return None
    if not resolved.is_file():
        issues.append(_issue(
            "bundle_file_not_regular",
            field,
            "Referenced path is not a regular file.",
            "Reference one regular evidence or image file.",
        ))
        return None
    return resolved, relative.as_posix()


def _report(
    *,
    checked: datetime,
    min_amount: float,
    min_rate: float,
    manifest_bytes: bytes,
    manifest: Optional[Mapping[str, Any]],
    issues: Sequence[Mapping[str, str]],
    evidence_files: Sequence[Mapping[str, Any]],
    image_files: Sequence[Mapping[str, Any]],
    projection: Optional[Mapping[str, Any]],
    bundle_fingerprint: str = "",
) -> Dict[str, Any]:
    if not bundle_fingerprint:
        bundle_fingerprint = hashlib.sha256(manifest_bytes).hexdigest() if manifest_bytes else ""
    projection_summary: Optional[Dict[str, Any]] = None
    if projection is not None:
        projection_summary = {
            "preview_only": True,
            "assumption": "Validated files are projected as active archives; no artifact was persisted.",
            "ready": bool(projection.get("ready")),
            "evidence_ready": bool(projection.get("evidence_ready")),
            "completion": dict(projection.get("completion") or {}),
            "failed_gate_codes": list(projection.get("failed_gate_codes") or []),
            "next_step": dict(projection.get("next_step") or {}) if projection.get("next_step") else None,
            "gates": list(projection.get("gates") or []),
            "evidence_fingerprint": str(projection.get("evidence_fingerprint") or ""),
        }
    bundle_ready = bool(not issues and projection and projection.get("evidence_ready"))
    first_issue = dict(issues[0]) if issues else None
    next_step: Mapping[str, Any]
    if first_issue:
        next_step = first_issue
    else:
        next_step = {
            "code": "archive_validated_bundle",
            "owner": "operator",
            "action": (
                "Create one real candidate, archive the five validated files, bind current account/category "
                "terms, then review and approve the resulting persisted revision."
            ),
        }
    return {
        "ok": True,
        "status": "pass" if bundle_ready else "blocked",
        "bundle_ready": bundle_ready,
        "commercial_ready": False,
        "writes_performed": False,
        "checked_at": _as_utc_z(checked),
        "schema_version": manifest.get("schema_version") if isinstance(manifest, Mapping) else None,
        "bundle_fingerprint": bundle_fingerprint,
        "profit_guard": {
            "min_profit_amount": min_amount,
            "min_profit_rate": min_rate,
            "account_terms_required": True,
        },
        "evidence": {
            "required_kinds": list(EVIDENCE_KINDS),
            "valid_files": sorted(evidence_files, key=lambda item: str(item.get("kind") or "")),
            "valid_count": len(evidence_files),
        },
        "images": {
            "valid_files": sorted(image_files, key=lambda item: str(item.get("relative_path") or "")),
            "valid_count": len(image_files),
        },
        "candidate_gate_projection": projection_summary,
        "issues": [dict(issue) for issue in issues],
        "next_step": dict(next_step),
        "external_gates": [
            {
                "code": "persisted_candidate_and_archives",
                "status": "pending",
                "owner": "operator+system",
            },
            {
                "code": "real_account_identity_and_runtime",
                "status": "pending",
                "owner": "operator",
            },
            {
                "code": "current_account_category_terms",
                "status": "pending",
                "owner": "operator",
            },
            {
                "code": "manual_decision_current",
                "status": "pending",
                "owner": "operator",
            },
            {
                "code": "day0_launch_preview_20_of_20",
                "status": "pending",
                "owner": "operator+system",
            },
        ],
    }


def _bundle_fingerprint(manifest: Mapping[str, Any], files: Sequence[Mapping[str, Any]]) -> str:
    payload = {
        "manifest": manifest,
        "files": sorted(
            (dict(item) for item in files),
            key=lambda item: (str(item.get("kind") or ""), str(item.get("relative_path") or "")),
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reject_duplicate_json_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _checked_datetime(value: Optional[str | datetime]) -> datetime:
    if value is None or value == "":
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        normalized = text[:-1] + "+00:00" if text[-1:] in {"Z", "z"} else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise CommercialAlphaIntakeError("checked_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TZ)
    return parsed.astimezone(timezone.utc)


def _validated_threshold(value: Any, name: str, maximum: Optional[float] = None) -> float:
    number = _number(value)
    if number is None or number < 0 or (maximum is not None and number > maximum):
        raise CommercialAlphaIntakeError(f"{name} is outside the supported range")
    return number


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _issue(code: str, field: str, message: str, action: str) -> Dict[str, str]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "action": action,
    }


def _as_utc_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=_LOCAL_TZ)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
