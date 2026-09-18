"""Commerce data backup CLI (WP14).

Usage:
    .venv/Scripts/python.exe scripts/backup_commerce.py create  [--label NAME]
    .venv/Scripts/python.exe scripts/backup_commerce.py verify  <backup_dir>
    .venv/Scripts/python.exe scripts/backup_commerce.py drill   <backup_dir>

- create: snapshot data/commerce into backups/commerce-<stamp> with a
  SHA-256 manifest (defaults to the live data dir from .env).
- verify: check an existing backup against its manifest.
- drill:  restore rehearsal into a scratch directory and verify; the
  live data directory is never touched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xianyu_agent.backup import create_backup, restore_rehearsal, verify_backup  # noqa: E402
from xianyu_agent.config import Settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Commerce data backup CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    create_p = sub.add_parser("create", help="create a new backup")
    create_p.add_argument("--label", default="")
    verify_p = sub.add_parser("verify", help="verify a backup")
    verify_p.add_argument("backup_dir")
    drill_p = sub.add_parser("drill", help="restore rehearsal into a scratch dir")
    drill_p.add_argument("backup_dir")
    args = parser.parse_args()

    settings = Settings.from_env()
    backup_root = ROOT / "backups"

    if args.command == "create":
        result = create_backup(settings.data_dir, backup_root, label=args.label)
        print(json.dumps(result, ensure_ascii=False))
        verification = verify_backup(Path(result["backup_dir"]))
        print(json.dumps(verification, ensure_ascii=False))
        return 0 if verification["ok"] else 1
    if args.command == "verify":
        verification = verify_backup(Path(args.backup_dir))
        print(json.dumps(verification, ensure_ascii=False))
        return 0 if verification["ok"] else 1
    if args.command == "drill":
        scratch = backup_root / "drill-scratch"
        if scratch.exists():
            print(f"scratch dir exists, remove it first: {scratch}")
            return 1
        result = restore_rehearsal(Path(args.backup_dir), scratch)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["verification"]["ok"] else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
