"""G5A backup/restore drill over the JSON ledgers (A114, no-database part).

Usage:
  python scripts/commerce_backup_drill.py --source DATA_DIR --work EMPTY_TMP

Phases (all copies; the source is only ever read):
1. backup   - copy every commerce ledger + evidence archive into work/backup
              with a SHA-256 manifest; measure wall time
2. restore  - rebuild from the backup into work/restored (as if into an
              empty isolated directory) and verify every hash; measure time
3. divergence - append one synthetic ledger row to the RESTORED copy,
              roll the restored copy back to the backup, and report that
              the row is gone: a blind restore after new writes LOSES data,
              which is exactly why ADR-0002 forbids blind rollback

Report JSON on stdout; exit 0 when all hashes verify (divergence findings
are expected demonstrations, not failures), 1 on input error, 2 on hash
mismatch (a real defect).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

COMMERCE_FILES = (
    "offers.json", "matches.json", "opportunities.json", "decisions.json",
    "agent_jobs.json", "import_batches.json", "intake_confirmations.json",
    "radar_events.json", "radar_reminders.json", "opportunity_adoptions.json",
    "procurement_orders.json", "budget_reservations.json",
    "inventory.json", "ledger.json",
)
TOP_LEVEL_FILES = ("resale_candidates.json", "work_orders.json", "execution_jobs.json")
EVIDENCE_DIR = "candidate_evidence"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(source: Path) -> list:
    files = []
    for name in COMMERCE_FILES:
        candidate = source / "commerce" / name
        if candidate.exists():
            files.append(candidate)
    for name in TOP_LEVEL_FILES:
        candidate = source / name
        if candidate.exists():
            files.append(candidate)
    evidence = source / EVIDENCE_DIR
    if evidence.exists():
        for path in sorted(evidence.rglob("*")):
            if path.is_file():
                files.append(path)
    return files


def _manifest(files) -> dict:
    return {
        str(path.relative_to(path.parents[1] if path.parent.name == "commerce" else path.parent)
            if path.parent.name == "commerce" else path.relative_to(path.parents[0])):
            _sha256(path)
        for path in files
    }


def drill(source: Path, work: Path) -> dict:
    report: dict = {"source": str(source), "work": str(work), "phases": {}}
    files = _collect(source)
    backup_dir = work / "backup"
    restored_dir = work / "restored"
    work.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    shutil.copytree(source, backup_dir, dirs_exist_ok=True)
    manifest = {
        str(path.relative_to(source)): _sha256(path)
        for path in files
    }
    (backup_dir / "_backup_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report["phases"]["backup"] = {
        "files": len(manifest),
        "seconds": round(time.monotonic() - started, 3),
    }

    started = time.monotonic()
    shutil.copytree(backup_dir, restored_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("_backup_manifest.json"))
    mismatches = []
    restored_files = _collect(restored_dir)
    for path in restored_files:
        relative = str(path.relative_to(restored_dir))
        expected = manifest.get(relative)
        if expected is None:
            mismatches.append(f"unexpected file after restore: {relative}")
        elif _sha256(path) != expected:
            mismatches.append(f"hash mismatch after restore: {relative}")
    missing = sorted(set(manifest) - {str(p.relative_to(restored_dir)) for p in restored_files})
    for relative in missing:
        mismatches.append(f"missing after restore: {relative}")
    report["phases"]["restore"] = {
        "files": len(restored_files),
        "seconds": round(time.monotonic() - started, 3),
        "hash_mismatches": mismatches,
        "missing_files": missing,
    }

    # divergence demonstration: a new ledger write after the backup point
    # disappears under a blind restore -- restoring an OLD snapshot after
    # NEW transactions is forbidden (ADR-0002 §2.3) for exactly this reason
    divergence = {"demonstrated": False, "lost_rows": 0}
    ledger_path = restored_dir / "commerce" / "ledger.json"
    if ledger_path.exists():
        try:
            rows = json.loads(ledger_path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            rows = None
        if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
            rows.append({
                "record_id": "drill_synthetic_0001", "record_type": "ledger_entry",
                "kind": "settled", "direction": "debit",
                "amount": {"state": "known", "amount": "1", "currency": "CNY"},
            })
            ledger_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            # blind rollback = replace the mutated tree with the backup again
            shutil.rmtree(restored_dir)
            shutil.copytree(backup_dir, restored_dir,
                            ignore=shutil.ignore_patterns("_backup_manifest.json"))
            rolled_back = json.loads(ledger_path.read_text(encoding="utf-8") or "[]")
            divergence = {
                "demonstrated": True,
                "lost_rows": 1 if not any(
                    isinstance(r, dict) and r.get("record_id") == "drill_synthetic_0001"
                    for r in rolled_back
                ) else 0,
                "note": "a post-backup write vanished under blind restore; real rollback must "
                        "export diffs and reconcile manually, never blind-restore",
            }
        else:
            divergence = {
                "demonstrated": False,
                "lost_rows": 0,
                "note": "ledger not a clean list of objects; divergence demo skipped",
            }
    report["phases"]["divergence"] = divergence
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="JSON-ledger backup/restore drill")
    parser.add_argument("--source", required=True, help="data dir to back up (read-only)")
    parser.add_argument("--work", required=True, help="empty working dir for backup/restored copies")
    args = parser.parse_args(argv)
    source = Path(args.source).resolve()
    work = Path(args.work).resolve()
    if not source.exists():
        print(json.dumps({"error": f"source not found: {source}"}))
        return 1
    if work.exists() and any(work.iterdir()):
        print(json.dumps({"error": "work dir must be empty or absent"}))
        return 1
    # Codex S6: work must not be inside (or contain) the source; a nested
    # destination would recursively copy itself into the backup.
    if work == source or source in work.parents or work in source.parents:
        print(json.dumps({"error": f"work dir {work} overlaps source {source}; refusing"}))
        return 1
    report = drill(source, work)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["phases"]["restore"]["hash_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
