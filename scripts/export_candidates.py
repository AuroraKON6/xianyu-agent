#!/usr/bin/env python
"""Offline candidate export entry (taskbook R07 deliverable).

Runs the read-only CandidateExporter against an explicit data directory
and writes candidates JSON / CSV / manifest. Never writes to the
commerce repositories; safe to run against a frozen snapshot copy.

Usage::

    .venv/Scripts/python.exe scripts/export_candidates.py \
        --data-dir <path> --out <path> [--status matched] [--label x]

The data dir must not be the live repository tree (refuses data/ under
the project root unless --allow-live is given for an explicitly frozen
copy).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--status", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    live_root = (REPO_ROOT / "data").resolve()
    if not args.allow_live and (data_dir == live_root or live_root in data_dir.parents):
        print(
            "refusing to export from the live data tree; pass a frozen copy "
            "or --allow-live if this directory is an explicitly frozen snapshot",
            file=sys.stderr,
        )
        return 2
    if not (data_dir / "commerce").is_dir():
        print("no commerce/ directory under %s" % data_dir, file=sys.stderr)
        return 2

    from xianyu_agent.application.candidate_export import CandidateExporter
    from xianyu_agent.application.research import ResearchService
    from xianyu_agent.commerce import JsonCommerceRepositories

    repos = JsonCommerceRepositories(data_dir)
    exporter = CandidateExporter(ResearchService(repos))
    result = exporter.export(
        Path(args.out), status=args.status, label=(args.label or None)
    )
    print("rows:", result["rows"])
    for key in ("json", "csv", "manifest"):
        print("%s: %s" % (key, result[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
