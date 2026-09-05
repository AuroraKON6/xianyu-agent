from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote, urlsplit, urlunsplit


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(cookie|token|authorization|password|secret|session)\b\s*[:=]\s*([^\s,;]+)"
)


async def capture_browser_write_evidence(
    page: Any,
    *,
    data_dir: Path,
    action: str,
    cookie_id: str,
    stage: str,
    write_attempted: bool,
    error: str = "",
    blocker: str = "",
    target_ref: str = "",
) -> Dict[str, Any]:
    """Persist a best-effort screenshot and a credential-free failure manifest."""
    captured_at = time.time()
    raw_cookie_id = str(cookie_id or "default")
    raw_target_ref = str(target_ref or "")
    clean_action = _safe_component(action, "unknown_action")
    clean_stage = _safe_component(stage, "unknown_stage")
    account_scope = _sha256_text(raw_cookie_id)[:16]
    target_scope = _sha256_text(raw_target_ref) if raw_target_ref else ""
    evidence_id = f"bwe_{uuid.uuid4().hex[:16]}"
    known_refs = (raw_cookie_id, raw_target_ref)
    page_url = _redact_known_values(
        sanitize_page_url(str(getattr(page, "url", "") or "")),
        known_refs,
        1000,
    )
    page_title = _redact_known_values(await _page_title(page), known_refs, 200)
    error_summary = _redact_known_values(error, known_refs, 500)
    blocker_summary = _redact_known_values(
        blocker or "browser_write_failure",
        known_refs,
        200,
    )

    evidence: Dict[str, Any] = {
        "evidence_id": evidence_id,
        "adapter": "browser",
        "adapter_status": "page_available" if page is not None else "unavailable",
        "after_screenshot": "",
        "evidence_manifest": "",
        "screenshot_sha256": "",
        "screenshot_bytes": 0,
        "page_url": page_url,
        "page_title": page_title,
        "captured_at": captured_at,
        "action_stage": clean_stage,
        "write_attempted": bool(write_attempted),
        "account_scope": account_scope,
        "target_scope_sha256": target_scope,
        "detected_blocker": blocker_summary,
        "capture_status": "metadata_only",
        "capture_error": "",
        "error_summary": error_summary,
        "operator_next_action": (
            "verify_platform_before_retry" if write_attempted else "inspect_browser_failure"
        ),
    }

    root = Path(data_dir).resolve()
    day = datetime.fromtimestamp(captured_at, tz=timezone.utc).strftime("%Y-%m-%d")
    folder = root / "browser_write_evidence" / account_scope / day
    stem = f"{datetime.fromtimestamp(captured_at, tz=timezone.utc).strftime('%H%M%S')}_{clean_action}_{evidence_id[-8:]}"
    screenshot_path = folder / f"{stem}.png"
    manifest_path = folder / f"{stem}.json"

    capture_errors = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        evidence["capture_error"] = _redact_known_values(exc, known_refs, 300)
        return evidence

    screenshot_fn = getattr(page, "screenshot", None)
    if callable(screenshot_fn):
        try:
            result = screenshot_fn(path=str(screenshot_path), full_page=True)
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
            if screenshot_path.is_file():
                screenshot_bytes = screenshot_path.read_bytes()
                evidence["after_screenshot"] = _relative_path(screenshot_path, root)
                evidence["screenshot_sha256"] = hashlib.sha256(screenshot_bytes).hexdigest()
                evidence["screenshot_bytes"] = len(screenshot_bytes)
                evidence["capture_status"] = "complete"
            else:
                capture_errors.append("screenshot method returned without creating a file")
        except Exception as exc:
            capture_errors.append(_redact_known_values(exc, known_refs, 300))
    else:
        capture_errors.append("page screenshot is unavailable")

    evidence["capture_error"] = "; ".join(item for item in capture_errors if item)[:500]
    manifest = {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "adapter": "browser",
        "adapter_status": evidence["adapter_status"],
        "action": clean_action,
        "stage": clean_stage,
        "captured_at": captured_at,
        "account_scope": account_scope,
        "target_scope_sha256": target_scope,
        "write_attempted": bool(write_attempted),
        "page_url": page_url,
        "page_title": page_title,
        "error_summary": error_summary,
        "detected_blocker": blocker_summary,
        "capture_status": evidence["capture_status"],
        "capture_error": evidence["capture_error"],
        "screenshot": {
            "path": evidence["after_screenshot"],
            "sha256": evidence["screenshot_sha256"],
            "bytes": evidence["screenshot_bytes"],
        },
    }
    try:
        _atomic_write_json(manifest_path, manifest)
        evidence["evidence_manifest"] = _relative_path(manifest_path, root)
    except OSError as exc:
        extra = _redact_known_values(exc, known_refs, 300)
        evidence["capture_error"] = "; ".join(
            item for item in (evidence["capture_error"], extra) if item
        )[:500]
    return evidence


def sanitize_page_url(value: str) -> str:
    """Keep only an HTTP(S) origin and path, never query credentials or fragments."""
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    hostname = str(parsed.hostname).lower()
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))[:1000]


async def _page_title(page: Any) -> str:
    title_fn = getattr(page, "title", None)
    if not callable(title_fn):
        return ""
    try:
        value = title_fn()
        if asyncio.iscoroutine(value) or asyncio.isfuture(value):
            value = await value
        return _redact_text(value, 200)
    except Exception:
        return ""


def _redact_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text[: max(0, int(limit))]


def _redact_known_values(value: Any, known_values: tuple[str, ...], limit: int) -> str:
    text = _redact_text(value, max(limit * 2, limit))
    for raw in known_values:
        secret = str(raw or "").strip()
        if not secret or secret == "default":
            continue
        variants = {secret, quote(secret, safe="")}
        for variant in variants:
            if variant:
                text = re.sub(re.escape(variant), "[REDACTED_REF]", text, flags=re.IGNORECASE)
    return text[: max(0, int(limit))]


def _safe_component(value: Any, fallback: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip()).strip("._-")
    return (clean or fallback)[:80]


def _relative_path(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
