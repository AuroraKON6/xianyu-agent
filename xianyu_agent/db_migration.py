"""Migrate the legacy SQLite database into the configured live data boundary.

Contract:
- The source database is never modified or deleted.
- A timestamped byte-copy backup with a SHA-256 sidecar is written before any
  target write; the backup is verified by hash after creation.
- A non-empty target is never overwritten. Re-running against a target that
  already matches the source table inventory is an idempotent no-op.
- Output reports table names and row counts only. Row contents are never
  printed because the ``cookies`` table stores account credentials.

Usage:
    python -m xianyu_agent.db_migration --source <old.db> --target <live.db> \
        [--dry-run] [--backup-dir <dir>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MIGRATION_SCHEMA_VERSION = 1


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _user_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def _row_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table in _user_tables(conn):
        counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return counts


def inspect_database(path: Path) -> Dict[str, Any]:
    conn = _connect_readonly(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return {
            "path": str(path),
            "exists": True,
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
            "tables": _row_counts(conn),
            "integrity_check": integrity,
        }
    finally:
        conn.close()


def _tables_match(source_counts: Dict[str, int], target_counts: Dict[str, int]) -> bool:
    return source_counts == target_counts


def _write_backup(source: Path, backup_dir: Path) -> Dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{source.name}.{timestamp}.bak"
    shutil.copy2(source, backup_path)
    digest = file_sha256(backup_path)
    source_digest = file_sha256(source)
    if digest != source_digest:
        raise RuntimeError(
            "backup verification failed: backup hash differs from source"
        )
    sidecar = backup_path.with_suffix(".bak.sha256.json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": MIGRATION_SCHEMA_VERSION,
                "backup_path": str(backup_path),
                "source_path": str(source),
                "sha256": digest,
                "size_bytes": backup_path.stat().st_size,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "backup_path": str(backup_path),
        "sha256": digest,
        "size_bytes": backup_path.stat().st_size,
        "sidecar": str(sidecar),
    }


def run_migration(
    source: str | Path,
    target: str | Path,
    backup_dir: Optional[str | Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    source_path = Path(source).absolute()
    target_path = Path(target).absolute()
    if not source_path.is_file():
        raise RuntimeError(f"source database does not exist: {source_path}")
    if source_path == target_path:
        raise RuntimeError("source and target must be different files")

    report: Dict[str, Any] = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "mode": "dry_run" if dry_run else "migrate",
        "source": inspect_database(source_path),
        "target": {"path": str(target_path), "exists": target_path.is_file()},
        "backup": None,
        "verified": False,
        "errors": [],
    }
    source_counts = report["source"]["tables"]

    if target_path.is_file():
        target_info = inspect_database(target_path)
        report["target"] = target_info
        if target_info["tables"]:
            if _tables_match(source_counts, target_info["tables"]):
                report["mode"] = "already_migrated"
                report["verified"] = target_info["integrity_check"] == "ok"
                return report
            report["errors"].append(
                "target database is non-empty and differs from the source; "
                "refusing to overwrite"
            )
            raise RuntimeError(report["errors"][0])

    if dry_run:
        report["plan"] = {
            "would_backup_to": str(
                Path(backup_dir).absolute()
                if backup_dir
                else source_path.parent / "sqlite-migration-backup"
            ),
            "would_create_target": str(target_path),
            "tables": source_counts,
            "rows_total": sum(source_counts.values()),
        }
        return report

    resolved_backup_dir = (
        Path(backup_dir).absolute()
        if backup_dir
        else source_path.parent / "sqlite-migration-backup"
    )
    report["backup"] = _write_backup(source_path, resolved_backup_dir)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    source_conn = sqlite3.connect(str(source_path))
    try:
        target_conn = sqlite3.connect(str(target_path))
        try:
            source_conn.backup(target_conn)
            target_conn.commit()
        finally:
            target_conn.close()
    finally:
        source_conn.close()

    migrated = inspect_database(target_path)
    report["target"] = migrated
    if migrated["integrity_check"] != "ok":
        report["errors"].append("target integrity_check failed after copy")
        raise RuntimeError(report["errors"][0])
    if not _tables_match(source_counts, migrated["tables"]):
        report["errors"].append("row counts differ between source and target")
        raise RuntimeError(report["errors"][0])
    report["verified"] = True
    report["rows_total"] = sum(migrated["tables"].values())
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy SQLite into the live data boundary (non-destructive)"
    )
    parser.add_argument("--source", required=True, help="legacy database file")
    parser.add_argument("--target", required=True, help="live database file")
    parser.add_argument("--backup-dir", help="backup directory (default: alongside source)")
    parser.add_argument("--dry-run", action="store_true", help="inspect and plan without writing")
    args = parser.parse_args(argv)
    try:
        report = run_migration(
            source=args.source,
            target=args.target,
            backup_dir=args.backup_dir,
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        print(
            json.dumps(
                {"schema_version": MIGRATION_SCHEMA_VERSION, "errors": [str(exc)]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
