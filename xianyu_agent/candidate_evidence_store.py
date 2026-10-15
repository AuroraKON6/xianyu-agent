from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, urlparse


MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
EVIDENCE_KINDS = (
    "supplier_quote",
    "supplier_stock",
    "supplier_fulfillment",
    "image_rights",
    "market_baseline",
)
# Owner-scoped archive extension (G1A): immutable originals that belong to a
# supplier import batch rather than a resale candidate. These artifacts never
# satisfy candidate launch gates; EVIDENCE_KINDS above stays unchanged.
OWNER_TYPES = ("candidate", "supplier_batch")
SUPPLIER_INTAKE_KINDS = (
    "supplier_quote_original",
    "supplier_identity_material",
    "supplier_use_permission_material",
)
EVIDENCE_KIND_CONFIG: Dict[str, Dict[str, str]] = {
    "supplier_quote": {
        "ref_field": "supplier_quote_evidence_ref",
        "timestamp_field": "supplier_quote_checked_at",
        "label": "供应商报价",
    },
    "supplier_stock": {
        "ref_field": "stock_evidence_ref",
        "timestamp_field": "stock_checked_at",
        "label": "供应商库存",
    },
    "supplier_fulfillment": {
        "ref_field": "fulfillment_evidence_ref",
        "timestamp_field": "",
        "label": "供应商履约",
    },
    "image_rights": {
        "ref_field": "image_rights_evidence_ref",
        "timestamp_field": "",
        "label": "图片使用权",
    },
    "market_baseline": {
        "ref_field": "competitor_evidence_ref",
        "timestamp_field": "competitor_checked_at",
        "label": "竞品基线",
    },
}

_EXTENSION_MIME_TYPES: Dict[str, Tuple[str, ...]] = {
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".webp": ("image/webp",),
    ".pdf": ("application/pdf",),
    ".html": ("text/html",),
    ".htm": ("text/html",),
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/plain"),
    ".json": ("application/json", "text/json", "text/plain"),
    ".csv": ("text/csv", "text/plain"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
}
_CANONICAL_MIME_TYPE = {
    extension: mime_types[0]
    for extension, mime_types in _EXTENSION_MIME_TYPES.items()
}
# .csv stays out of _TEXT_EXTENSIONS on purpose: archived supplier originals
# must keep their exact bytes (GBK is a legal encoding for them), so the
# UTF-8-only decode/sensitive-scan path cannot be mandatory here; callers that
# stage originals run a best-effort scan when the bytes decode as UTF-8.
_TEXT_EXTENSIONS = {".html", ".htm", ".txt", ".md", ".json"}
_ARTIFACT_ID_PATTERN = re.compile(r"^cea_[a-f0-9]{24}$")
_CANDIDATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,119}$")
_BLOB_KEY_PATTERN = re.compile(r"^blobs/[a-f0-9]{2}/[a-f0-9]{64}\.bin$")
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(
        r"(?i)(?:cookie|authorization|access[_-]?token|refresh[_-]?token|"
        r"api[_-]?key|password|secret)\s*[:=]"
    ),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
)
_SENSITIVE_URL_QUERY_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "passwd",
    "refreshtoken",
    "secret",
    "sessionkey",
    "signature",
    "token",
}
_LOCAL_TZ = datetime.now().astimezone().tzinfo


class CandidateEvidenceError(ValueError):
    """Base error for rejected candidate evidence operations."""


class CandidateEvidenceConflict(CandidateEvidenceError):
    """The requested operation conflicts with immutable evidence history."""


class CandidateEvidenceIntegrityError(CandidateEvidenceError):
    """The content-addressed blob is missing or no longer matches its digest."""


class CandidateEvidenceStore:
    """Content-addressed, append-only evidence archive for resale candidates."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.blob_root = self.root / "blobs"
        self._lock = RLock()
        self._artifacts: Dict[str, Dict[str, Any]] = {}
        self._load()

    def stage_bytes(
        self,
        *,
        candidate_id: str,
        kind: str,
        content: bytes,
        original_filename: str,
        mime_type: str = "",
        observed_at: str = "",
        note: str = "",
        source_url: str = "",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Validate and persist an immutable pending candidate artifact.

        A pending artifact does not satisfy any launch gate until the caller has
        updated the candidate reference and explicitly activated it.
        """
        clean_candidate_id = _candidate_id(candidate_id)
        validated = validate_candidate_evidence_bytes(
            kind=kind,
            content=content,
            original_filename=original_filename,
            mime_type=mime_type,
            observed_at=observed_at,
            note=note,
            source_url=source_url,
            now=now,
        )
        artifact = self._stage_record(
            owner_type="candidate",
            owner_id=clean_candidate_id,
            artifact_owner_field="candidate_id",
            validated=validated,
            content=content,
        )
        return artifact

    def stage_owner_bytes(
        self,
        *,
        owner_type: str,
        owner_id: str,
        kind: str,
        content: bytes,
        original_filename: str,
        mime_type: str = "",
        observed_at: str = "",
        note: str = "",
        source_url: str = "",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Archive an immutable artifact scoped to a non-candidate owner.

        Used by the intake confirmation flow (G1A) to keep supplier import
        originals, identity material and use-permission material in the same
        content-addressed archive without inventing placeholder candidates.
        These artifacts never appear in candidate queries and never satisfy
        candidate launch gates.
        """
        clean_owner_type = str(owner_type or "").strip()
        if clean_owner_type not in OWNER_TYPES or clean_owner_type == "candidate":
            raise CandidateEvidenceError("owner_type must be a non-candidate archive owner")
        clean_owner_id = _candidate_id(owner_id)
        validated = validate_candidate_evidence_bytes(
            kind=kind,
            content=content,
            original_filename=original_filename,
            mime_type=mime_type,
            observed_at=observed_at,
            note=note,
            source_url=source_url,
            now=now,
            allowed_kinds=SUPPLIER_INTAKE_KINDS,
        )
        return self._stage_record(
            owner_type=clean_owner_type,
            owner_id=clean_owner_id,
            artifact_owner_field="owner_id",
            validated=validated,
            content=content,
        )

    def _stage_record(
        self,
        *,
        owner_type: str,
        owner_id: str,
        artifact_owner_field: str,
        validated: Mapping[str, Any],
        content: bytes,
    ) -> Dict[str, Any]:
        clean_kind = validated["kind"]
        filename = validated["original_filename"]
        digest = validated["sha256"]
        if owner_type == "candidate":
            artifact_seed = f"{owner_id}\0{clean_kind}\0{digest}"
        else:
            artifact_seed = f"{owner_type}\0{owner_id}\0{clean_kind}\0{digest}"
        artifact_id = "cea_" + hashlib.sha256(artifact_seed.encode("utf-8")).hexdigest()[:24]
        blob_key = f"blobs/{digest[:2]}/{digest}.bin"

        with self._lock:
            existing = self._artifacts.get(artifact_id)
            if existing:
                if existing.get("status") == "superseded":
                    raise CandidateEvidenceConflict(
                        "this exact evidence file was already superseded; capture a new current artifact"
                    )
                if existing.get("status") == "orphaned":
                    existing["status"] = "pending"
                    existing["orphaned_at"] = ""
                    self._persist()
                return {
                    "artifact": self._public_metadata(existing, verify_integrity=True),
                    "created": False,
                }

            blob_path = self._blob_path(blob_key)
            if blob_path.exists():
                existing_digest = hashlib.sha256(blob_path.read_bytes()).hexdigest()
                if existing_digest != digest:
                    raise CandidateEvidenceIntegrityError(
                        "existing content-addressed blob failed integrity verification"
                    )
            else:
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = blob_path.with_suffix(".tmp")
                tmp_path.write_bytes(content)
                tmp_path.replace(blob_path)

            active = self._active_for_owner(owner_type, owner_id, clean_kind)
            artifact = {
                "artifact_id": artifact_id,
                "candidate_id": owner_id if artifact_owner_field == "candidate_id" else "",
                "owner_type": owner_type,
                "owner_id": owner_id if artifact_owner_field == "owner_id" else "",
                "kind": clean_kind,
                "original_filename": filename,
                "mime_type": validated["mime_type"],
                "byte_size": validated["byte_size"],
                "sha256": digest,
                "blob_key": blob_key,
                "created_at": validated["validated_at"],
                "observed_at": validated["observed_at"],
                "note": validated["note"],
                "source_url": validated["source_url"],
                "status": "pending",
                "activated_at": "",
                "supersedes_artifact_id": str((active or {}).get("artifact_id") or ""),
                "superseded_at": "",
                "orphaned_at": "",
            }
            self._artifacts[artifact_id] = artifact
            self._persist()
            return {
                "artifact": self._public_metadata(artifact, verify_integrity=True),
                "created": True,
            }

    def activate(self, artifact_id: str) -> Dict[str, Any]:
        clean_id = _artifact_id(artifact_id)
        with self._lock:
            artifact = self._require_artifact(clean_id)
            if artifact.get("status") == "superseded":
                raise CandidateEvidenceConflict("superseded evidence cannot be reactivated")
            integrity = self._integrity(artifact)
            if not integrity["ok"]:
                raise CandidateEvidenceIntegrityError("evidence blob failed integrity verification")
            now = _as_utc_z(datetime.now(timezone.utc))
            owner = _owner_key(artifact)
            for other in self._artifacts.values():
                if (
                    other.get("artifact_id") != clean_id
                    and _owner_key(other) == owner
                    and other.get("kind") == artifact.get("kind")
                    and other.get("status") == "active"
                ):
                    other["status"] = "superseded"
                    other["superseded_at"] = now
            artifact["status"] = "active"
            artifact["activated_at"] = artifact.get("activated_at") or now
            artifact["orphaned_at"] = ""
            self._persist()
            return self._public_metadata(artifact, verify_integrity=True)

    def mark_orphaned(self, artifact_id: str) -> None:
        clean_id = _artifact_id(artifact_id)
        with self._lock:
            artifact = self._artifacts.get(clean_id)
            if not artifact or artifact.get("status") == "active":
                return
            artifact["status"] = "orphaned"
            artifact["orphaned_at"] = _as_utc_z(datetime.now(timezone.utc))
            self._persist()

    def list_candidate_artifacts(
        self,
        candidate_id: str,
        *,
        include_orphaned: bool = False,
        verify_integrity: bool = True,
    ) -> List[Dict[str, Any]]:
        clean_candidate_id = _candidate_id(candidate_id)
        with self._lock:
            items = [
                artifact
                for artifact in self._artifacts.values()
                if artifact.get("candidate_id") == clean_candidate_id
                and (include_orphaned or artifact.get("status") != "orphaned")
            ]
            items.sort(
                key=lambda item: (
                    str(item.get("observed_at") or ""),
                    str(item.get("created_at") or ""),
                ),
                reverse=True,
            )
            return [
                self._public_metadata(item, verify_integrity=verify_integrity)
                for item in items
            ]

    def artifact_lookup(self, candidate_id: str) -> Dict[str, Dict[str, Any]]:
        return {
            item["artifact_id"]: item
            for item in self.list_candidate_artifacts(
                candidate_id,
                include_orphaned=True,
                verify_integrity=True,
            )
        }

    def get_artifact(
        self,
        candidate_id: str,
        artifact_id: str,
        *,
        verify_integrity: bool = True,
    ) -> Optional[Dict[str, Any]]:
        clean_candidate_id = _candidate_id(candidate_id)
        clean_id = _artifact_id(artifact_id)
        with self._lock:
            artifact = self._artifacts.get(clean_id)
            if not artifact or artifact.get("candidate_id") != clean_candidate_id:
                return None
            return self._public_metadata(artifact, verify_integrity=verify_integrity)

    def resolve_file(self, candidate_id: str, artifact_id: str) -> Tuple[Path, Dict[str, Any]]:
        clean_candidate_id = _candidate_id(candidate_id)
        clean_id = _artifact_id(artifact_id)
        with self._lock:
            artifact = self._artifacts.get(clean_id)
            if not artifact or artifact.get("candidate_id") != clean_candidate_id:
                raise KeyError("candidate evidence artifact not found")
            integrity = self._integrity(artifact)
            if not integrity["ok"]:
                raise CandidateEvidenceIntegrityError("evidence blob failed integrity verification")
            return self._blob_path(str(artifact.get("blob_key") or "")), self._public_metadata(
                artifact,
                verify_integrity=True,
            )

    def list_owner_artifacts(
        self,
        owner_type: str,
        owner_id: str,
        *,
        include_orphaned: bool = False,
        verify_integrity: bool = True,
    ) -> List[Dict[str, Any]]:
        clean_owner_type = str(owner_type or "").strip()
        if clean_owner_type not in OWNER_TYPES or clean_owner_type == "candidate":
            raise CandidateEvidenceError("owner_type must be a non-candidate archive owner")
        clean_owner_id = _candidate_id(owner_id)
        with self._lock:
            items = [
                artifact
                for artifact in self._artifacts.values()
                if _owner_key(artifact) == (clean_owner_type, clean_owner_id)
                and (include_orphaned or artifact.get("status") != "orphaned")
            ]
            items.sort(
                key=lambda item: (
                    str(item.get("observed_at") or ""),
                    str(item.get("created_at") or ""),
                ),
                reverse=True,
            )
            return [
                self._public_metadata(item, verify_integrity=verify_integrity)
                for item in items
            ]

    def get_owner_artifact(
        self,
        owner_type: str,
        owner_id: str,
        artifact_id: str,
        *,
        verify_integrity: bool = True,
    ) -> Optional[Dict[str, Any]]:
        clean_owner_type = str(owner_type or "").strip()
        if clean_owner_type not in OWNER_TYPES or clean_owner_type == "candidate":
            raise CandidateEvidenceError("owner_type must be a non-candidate archive owner")
        clean_owner_id = _candidate_id(owner_id)
        clean_id = _artifact_id(artifact_id)
        with self._lock:
            artifact = self._artifacts.get(clean_id)
            if not artifact or _owner_key(artifact) != (clean_owner_type, clean_owner_id):
                return None
            return self._public_metadata(artifact, verify_integrity=verify_integrity)

    def resolve_owner_file(self, owner_type: str, owner_id: str, artifact_id: str) -> Tuple[Path, Dict[str, Any]]:
        artifact = self.get_owner_artifact(owner_type, owner_id, artifact_id, verify_integrity=False)
        if artifact is None:
            raise KeyError("owner evidence artifact not found")
        with self._lock:
            stored = self._artifacts.get(artifact["artifact_id"])
            if stored is None:
                raise KeyError("owner evidence artifact not found")
            integrity = self._integrity(stored)
            if not integrity["ok"]:
                raise CandidateEvidenceIntegrityError("evidence blob failed integrity verification")
            return self._blob_path(str(stored.get("blob_key") or "")), self._public_metadata(
                stored,
                verify_integrity=True,
            )

    def _active_for_owner(self, owner_type: str, owner_id: str, kind: str) -> Optional[Dict[str, Any]]:
        return next(
            (
                artifact
                for artifact in self._artifacts.values()
                if _owner_key(artifact) == (owner_type, owner_id)
                and artifact.get("kind") == kind
                and artifact.get("status") == "active"
            ),
            None,
        )

    def _active_for_kind(self, candidate_id: str, kind: str) -> Optional[Dict[str, Any]]:
        return self._active_for_owner("candidate", candidate_id, kind)

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_artifacts = payload.get("artifacts") if isinstance(payload, Mapping) else None
        if not isinstance(raw_artifacts, list):
            return
        for raw in raw_artifacts:
            if not isinstance(raw, Mapping):
                continue
            artifact_id = str(raw.get("artifact_id") or "")
            candidate_id = str(raw.get("candidate_id") or "")
            kind = str(raw.get("kind") or "")
            blob_key = str(raw.get("blob_key") or "")
            owner_type = str(raw.get("owner_type") or "candidate")
            if not _ARTIFACT_ID_PATTERN.fullmatch(artifact_id) or not _BLOB_KEY_PATTERN.fullmatch(blob_key):
                continue
            if owner_type == "supplier_batch":
                owner_id = str(raw.get("owner_id") or "")
                if (
                    candidate_id != ""
                    or not _CANDIDATE_ID_PATTERN.fullmatch(owner_id)
                    or kind not in SUPPLIER_INTAKE_KINDS
                ):
                    continue
            else:
                if not _CANDIDATE_ID_PATTERN.fullmatch(candidate_id) or kind not in EVIDENCE_KINDS:
                    continue
            artifact = dict(raw)
            artifact["status"] = (
                str(raw.get("status") or "orphaned")
                if str(raw.get("status") or "") in {"pending", "active", "superseded", "orphaned"}
                else "orphaned"
            )
            self._artifacts[artifact_id] = artifact

    def _persist(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp_path = self.index_path.with_suffix(".json.tmp")
        artifacts = sorted(
            self._artifacts.values(),
            key=lambda item: (str(item.get("created_at") or ""), str(item.get("artifact_id") or "")),
        )
        tmp_path.write_text(
            json.dumps(
                {"schema_version": 1, "artifacts": artifacts},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(self.index_path)

    def _public_metadata(self, artifact: Mapping[str, Any], *, verify_integrity: bool) -> Dict[str, Any]:
        public_keys = (
            "artifact_id",
            "candidate_id",
            "owner_type",
            "owner_id",
            "kind",
            "original_filename",
            "mime_type",
            "byte_size",
            "sha256",
            "created_at",
            "observed_at",
            "note",
            "source_url",
            "status",
            "activated_at",
            "supersedes_artifact_id",
            "superseded_at",
            "orphaned_at",
        )
        result = {key: artifact.get(key) for key in public_keys}
        result["integrity"] = (
            self._integrity(artifact)
            if verify_integrity
            else {"ok": None, "status": "not_checked"}
        )
        return result

    def _integrity(self, artifact: Mapping[str, Any]) -> Dict[str, Any]:
        expected_size = int(artifact.get("byte_size") or 0)
        expected_digest = str(artifact.get("sha256") or "")
        try:
            path = self._blob_path(str(artifact.get("blob_key") or ""))
            stat = path.stat()
        except (OSError, CandidateEvidenceIntegrityError):
            return {"ok": False, "status": "missing"}
        if stat.st_size != expected_size:
            return {"ok": False, "status": "size_mismatch"}
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return {"ok": False, "status": "unreadable"}
        ok = digest == expected_digest
        status = "verified" if ok else "hash_mismatch"
        return {"ok": ok, "status": status}

    def _blob_path(self, blob_key: str) -> Path:
        normalized = str(blob_key or "").replace("\\", "/")
        if not _BLOB_KEY_PATTERN.fullmatch(normalized):
            raise CandidateEvidenceIntegrityError("invalid evidence blob key")
        root = self.root.resolve()
        path = (self.root / Path(*normalized.split("/"))).resolve()
        if root not in path.parents:
            raise CandidateEvidenceIntegrityError("evidence blob escaped archive root")
        return path

    def _require_artifact(self, artifact_id: str) -> Dict[str, Any]:
        artifact = self._artifacts.get(artifact_id)
        if not artifact:
            raise KeyError("candidate evidence artifact not found")
        return artifact


def artifact_reference(artifact_id: str) -> str:
    return "artifact:" + _artifact_id(artifact_id)


def parse_artifact_reference(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith("artifact:"):
        return ""
    artifact_id = text.split(":", 1)[1]
    return artifact_id if _ARTIFACT_ID_PATTERN.fullmatch(artifact_id) else ""


def validate_candidate_evidence_bytes(
    *,
    kind: str,
    content: bytes,
    original_filename: str,
    mime_type: str = "",
    observed_at: str = "",
    note: str = "",
    source_url: str = "",
    now: Optional[datetime] = None,
    allowed_kinds: Tuple[str, ...] = EVIDENCE_KINDS,
) -> Dict[str, Any]:
    """Apply the archive's file and metadata rules without persisting data."""
    clean_kind = str(kind or "").strip()
    if clean_kind not in allowed_kinds:
        raise CandidateEvidenceError("unsupported evidence kind")
    if not isinstance(content, bytes):
        raise CandidateEvidenceError("evidence content must be bytes")
    if not content:
        raise CandidateEvidenceError("evidence file is empty")
    if len(content) > MAX_EVIDENCE_BYTES:
        raise CandidateEvidenceError("evidence file exceeds 10 MiB")

    filename, extension = _clean_filename(original_filename)
    clean_mime_type = _validate_file_content(content, extension, mime_type)
    clean_note = _clean_text(note, "note", 500)
    clean_source_url = _clean_source_url(source_url)
    if extension in _TEXT_EXTENSIONS:
        decoded = _decode_text(content)
        if _contains_sensitive_text(decoded):
            raise CandidateEvidenceError(
                "text evidence contains credentials or personal identifiers; redact it before upload"
            )

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_LOCAL_TZ)
    current = current.astimezone(timezone.utc)
    observed = _parse_observed_at(observed_at)
    if observed > current + timedelta(seconds=60):
        raise CandidateEvidenceError("observed_at cannot be in the future")

    return {
        "kind": clean_kind,
        "original_filename": filename,
        "extension": extension,
        "mime_type": clean_mime_type,
        "byte_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "validated_at": _as_utc_z(current),
        "observed_at": _as_utc_z(observed),
        "note": clean_note,
        "source_url": clean_source_url,
    }


def contains_sensitive_evidence_text(value: Any) -> bool:
    """Return whether free-form evidence text appears to contain secrets or PII."""
    return _contains_sensitive_text(str(value or ""))


def validate_evidence_source_url(value: Any) -> str:
    """Validate a source URL with the same credential rules as the archive."""
    return _clean_source_url(value)


def _owner_key(artifact: Mapping[str, Any]) -> Tuple[str, str]:
    if str(artifact.get("owner_type") or "candidate") == "supplier_batch":
        return ("supplier_batch", str(artifact.get("owner_id") or ""))
    return ("candidate", str(artifact.get("candidate_id") or ""))


def _candidate_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _CANDIDATE_ID_PATTERN.fullmatch(text):
        raise CandidateEvidenceError("invalid candidate_id")
    return text


def _artifact_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _ARTIFACT_ID_PATTERN.fullmatch(text):
        raise CandidateEvidenceError("invalid artifact_id")
    return text


def _clean_filename(value: Any) -> Tuple[str, str]:
    raw = str(value or "").replace("\\", "/")
    filename = raw.rsplit("/", 1)[-1].strip()
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    if not filename or len(filename) > 180:
        raise CandidateEvidenceError("invalid evidence filename")
    extension = Path(filename).suffix.casefold()
    if extension not in _EXTENSION_MIME_TYPES:
        raise CandidateEvidenceError("unsupported evidence file extension")
    return filename, extension


def _validate_file_content(content: bytes, extension: str, mime_type: str) -> str:
    clean_mime = str(mime_type or "").split(";", 1)[0].strip().casefold()
    if clean_mime in {"", "application/octet-stream"}:
        clean_mime = _CANONICAL_MIME_TYPE[extension]
    if clean_mime not in _EXTENSION_MIME_TYPES[extension]:
        raise CandidateEvidenceError("evidence MIME type does not match its extension")

    if extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CandidateEvidenceError("invalid PNG evidence content")
    if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise CandidateEvidenceError("invalid JPEG evidence content")
    if extension == ".webp" and not (
        len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    ):
        raise CandidateEvidenceError("invalid WebP evidence content")
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise CandidateEvidenceError("invalid PDF evidence content")
    if extension == ".xlsx" and not content.startswith(b"PK\x03\x04"):
        raise CandidateEvidenceError("invalid XLSX evidence content")
    if extension in _TEXT_EXTENSIONS:
        text = _decode_text(content)
        if "\x00" in text:
            raise CandidateEvidenceError("text evidence contains binary data")
        if extension == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise CandidateEvidenceError("invalid JSON evidence content") from exc
    return clean_mime


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CandidateEvidenceError("text evidence must be UTF-8") from exc


def _contains_sensitive_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_TEXT_PATTERNS)


def _clean_text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise CandidateEvidenceError(f"{field} is too long")
    if _contains_sensitive_text(text):
        raise CandidateEvidenceError(f"{field} contains credentials or personal identifiers")
    return text


def _clean_source_url(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 1000:
        raise CandidateEvidenceError("source_url is too long")
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise CandidateEvidenceError("invalid evidence source_url") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise CandidateEvidenceError("evidence source_url must be HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise CandidateEvidenceError("evidence source_url cannot contain credentials")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized_key in _SENSITIVE_URL_QUERY_KEYS:
            raise CandidateEvidenceError("evidence source_url cannot contain credential parameters")
    return text


def _parse_observed_at(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise CandidateEvidenceError("observed_at is required")
    normalized = text[:-1] + "+00:00" if text[-1:] in {"Z", "z"} else text
    try:
        observed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CandidateEvidenceError("observed_at must be an ISO timestamp") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=_LOCAL_TZ)
    return observed.astimezone(timezone.utc)


def _as_utc_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=_LOCAL_TZ)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
