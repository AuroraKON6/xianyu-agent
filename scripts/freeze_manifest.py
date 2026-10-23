#!/usr/bin/env python
"""Full-domain migration freeze manifest (taskbook R15).

Enumerates EVERY authoritative write domain (the JSON ledgers the
single-writer process owns) and produces a freeze-point manifest:
path -> {sha256, bytes}. `--verify` compares the current tree against a
recorded manifest and reports every mismatch, missing and unexpected
file — the pre-migration equivalence check R15 requires (record counts,
totals and per-file hashes must match before the PG cutover).

Dirty data is REPORTED, never auto-repaired.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# every authoritative write domain (BASELINE_20261020 domain census)
DOMAIN_DIRECTORIES = ("commerce", "physical_sourcing", "guard", "discovery",
                      "research", "opportunities", "products", "drafts",
                      "outbox", "crawls", "reports", "after_sales",
                      "pi-validation", "test-assets")
DOMAIN_FILES = (
    "work_orders.json", "execution_jobs.json", "resale_candidates.json",
    "message_intake.json", "order_runtime_state.json", "published.json",
    "events.jsonl", "agent_collaboration.jsonl",
)
# live sub-tree carries its own commerce/ ledgers
LIVE_SUBTREE = "live"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect(data_dir: Path) -> dict:
    entries = {}

    def add(path: Path) -> None:
        if path.is_file():
            entries[path.relative_to(data_dir).as_posix()] = {
                "sha256": _sha256(path), "bytes": path.stat().st_size,
            }

    for name in DOMAIN_FILES:
        candidate = data_dir / name
        if candidate.exists():
            add(candidate)
    for directory in DOMAIN_DIRECTORIES:
        root = data_dir / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            add(path)
    live = data_dir / LIVE_SUBTREE
    if live.exists():
        for path in sorted(live.rglob("*")):
            add(path)
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="full-domain freeze manifest")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", help="write the manifest to this path")
    parser.add_argument("--verify", help="verify the tree against this manifest")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        print(json.dumps({"error": f"data dir not found: {data_dir}"}))
        return 1

    current = collect(data_dir)

    if args.verify:
        recorded = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        mismatches = sorted(
            name for name, meta in recorded.items()
            if name not in current or current[name] != meta
        )
        missing = sorted(set(recorded) - set(current))
        unexpected = sorted(set(current) - set(recorded))
        report = {
            "verified": not mismatches and not missing and not unexpected,
            "file_count": len(current),
            "recorded_count": len(recorded),
            "mismatches": mismatches,
            "missing": missing,
            "unexpected": unexpected,
            "note": "dirty data is reported, never auto-repaired",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["verified"] else 2

    if args.out:
        out = Path(args.out).resolve()
        if out == data_dir or data_dir in out.parents or out in data_dir.parents:
            print(json.dumps({"error": "manifest path overlaps the data dir; refusing"}))
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True))
    print(json.dumps({"file_count": len(current)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
