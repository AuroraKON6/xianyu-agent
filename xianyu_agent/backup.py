"""Commerce data backup and recovery verification (WP14 minimal).

Backs up the transactional commerce facts (``data/commerce/`` JSON
aggregates) into a timestamped directory with a SHA-256 manifest, and
verifies any backup against that manifest. ``restore_rehearsal``
copies a backup into a scratch directory and re-verifies it -- a
recovery drill that proves the backup is actually restorable, without
touching the live data directory.

Scope honesty: this is file-level backup of the JSON aggregate stores
(the WP01 ADR-0001 alpha storage). RPO/RTO targets are NOT claimed
here -- those require the continuous-archive design in the full WP14.
The platform-reconciliation rule after restore (compare with the
platform before replaying anything) lives in the existing execution
verification path and is referenced in the runbook, not automated here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

MANIFEST_NAME = "backup_manifest.json"
COMMERCE_DIR = "commerce"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(data_dir: Path, backup_root: Path, *, label: str = "") -> Dict[str, Any]:
    """Snapshot ``<data_dir>/commerce`` into a timestamped backup dir."""
    source = Path(data_dir) / COMMERCE_DIR
    if not source.is_dir():
        raise FileNotFoundError(f"nothing to back up: {source}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{label}" if label else ""
    target = Path(backup_root) / f"commerce-{stamp}{suffix}"
    if target.exists():
        raise FileExistsError(f"backup already exists: {target}")
    target.mkdir(parents=True)

    files: List[Dict[str, Any]] = []
    for path in sorted(source.rglob("*.json")):
        if path.name.endswith(".tmp"):
            continue
        relative = path.relative_to(source).as_posix()
        shutil.copy2(path, target / relative)
        files.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "files": files,
    }
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"backup_dir": str(target), "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"]}


def verify_backup(backup_dir: Path) -> Dict[str, Any]:
    """Verify every file in a backup against its manifest."""
    backup = Path(backup_dir)
    manifest_path = backup / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"ok": False, "error": f"no manifest in {backup}"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches: List[str] = []
    checked = 0
    for entry in manifest.get("files", []):
        path = backup / entry["path"]
        if not path.is_file():
            mismatches.append(f"missing: {entry['path']}")
            continue
        if _sha256(path) != entry["sha256"]:
            mismatches.append(f"hash mismatch: {entry['path']}")
        elif path.stat().st_size != entry["bytes"]:
            mismatches.append(f"size mismatch: {entry['path']}")
        checked += 1
    extra = [p.name for p in backup.glob("*.json") if p.name != MANIFEST_NAME and p.name not in {e["path"] for e in manifest.get("files", [])}]
    return {
        "ok": not mismatches and not extra,
        "checked": checked,
        "mismatches": mismatches,
        "unexpected_files": extra,
        "created_at": manifest.get("created_at"),
    }


def restore_rehearsal(backup_dir: Path, scratch_dir: Path) -> Dict[str, Any]:
    """Restore a backup into a scratch dir and verify it end to end.

    A recovery drill only: the live data directory is never touched,
    and after any real restore the platform-reconciliation rule from
    docs/ops/execution-recovery-runbook.md still applies before any
    replay.
    """
    backup = Path(backup_dir)
    scratch = Path(scratch_dir)
    if scratch.exists():
        raise FileExistsError(f"scratch dir already exists: {scratch}")
    scratch.mkdir(parents=True)
    manifest_path = backup / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: List[str] = []
    for entry in manifest.get("files", []):
        destination = scratch / COMMERCE_DIR / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / entry["path"], destination)
        restored.append(entry["path"])
    # verify the restored copy by rebuilding a manifest against scratch
    verify_dir = scratch / "verify"
    verify_dir.mkdir()
    shutil.copy2(manifest_path, verify_dir / MANIFEST_NAME)
    for entry in manifest.get("files", []):
        source_file = scratch / COMMERCE_DIR / entry["path"]
        relative = Path(entry["path"])
        target_file = verify_dir / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
    verification = verify_backup(verify_dir)
    return {
        "restored_files": len(restored),
        "verification": verification,
        "scratch_dir": str(scratch),
        "note": "drill only; live data untouched; platform reconciliation still required after any real restore",
    }
