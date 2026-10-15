"""Audit the commerce JSON ledgers and emit a reconciliation report (G5A/A113).

Read-only: never writes business data. Produces, per ledger and overall:
- row counts per record_type,
- money sums over CNY-known amounts (per direction),
- unique-key conflict counts (the keys ADR-0002 will constrain),
- orphan reference counts (snapshot/match/opportunity/ledger links),
- unfinished work (queued/leased/running/awaiting jobs, non-terminal
  procurements), UNKNOWN executions, and evidence/artifact digest checks.

Exit codes: 0 = no conflicts/orphans found; 2 = findings exist (details in
the report); 1 = input error. The report goes to stdout; redirect it to a
file outside the data directory. This is the pre-migration reconciliation
baseline; the PostgreSQL load step stays a separate, approved operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping

COMMERCE_FILES = (
    "offers.json", "matches.json", "opportunities.json", "decisions.json",
    "agent_jobs.json", "import_batches.json", "intake_confirmations.json",
    "radar_events.json", "radar_reminders.json", "opportunity_adoptions.json",
    "procurement_orders.json", "budget_reservations.json",
    "inventory.json", "ledger.json",
)
EXECUTION_FILE = "execution_jobs.json"  # execution authority: data root, not commerce/
ARTIFACT_INDEX = Path("candidate_evidence") / "index.json"
MAX_FILE_BYTES = 64 * 1024 * 1024
JOB_OPEN_STATES = {"queued", "leased", "running", "awaiting_input"}
PROC_OPEN_STATES = {"awaiting_approval", "approved", "ordered", "receiving"}


class AuditError(RuntimeError):
    pass


def _load_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AuditError(f"ledger too large to audit in one pass: {path.name}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise AuditError(f"expected a JSON list ledger: {path.name}")
    return data


def _money_sum(entries: List[Mapping[str, Any]]) -> Dict[str, str]:
    totals: Dict[str, Decimal] = {}
    for entry in entries:
        amount = entry.get("amount") or {}
        if not isinstance(amount, Mapping) or amount.get("state") != "known":
            continue
        currency = str(amount.get("currency") or "CNY")
        try:
            value = Decimal(str(amount.get("amount")))
        except (InvalidOperation, ValueError):
            continue
        signed = value if str(entry.get("direction")) == "credit" else -value
        totals[currency] = totals.get(currency, Decimal("0")) + signed
    return {currency: str(total) for currency, total in sorted(totals.items())}


def audit(data_dir: Path) -> Dict[str, Any]:
    commerce = data_dir / "commerce"
    findings: List[str] = []
    ledgers: Dict[str, List[Dict[str, Any]]] = {}
    per_file: Dict[str, Any] = {}
    for name in COMMERCE_FILES:
        rows = _load_list(commerce / name)
        ledgers[name] = rows
        types = Counter(str(row.get("record_type") or "?") for row in rows)
        per_file[name] = {
            "rows": len(rows),
            "record_types": dict(types),
            "money_net": _money_sum(rows) if name == "ledger.json" else {},
        }

    # Codex S: the execution authority lives at the data root and must not
    # disappear behind agent_jobs -- report it and its UNKNOWN facts
    execution_rows = _load_list(data_dir / EXECUTION_FILE)
    if execution_rows:
        per_file[EXECUTION_FILE] = {
            "rows": len(execution_rows),
            "statuses": dict(Counter(str(r.get("status") or "?") for r in execution_rows)),
        }

    offers = ledgers["offers.json"]
    matches = ledgers["matches.json"]
    opportunities = ledgers["opportunities.json"]
    jobs = ledgers["agent_jobs.json"]
    procurements = ledgers["procurement_orders.json"]
    ledger_entries = ledgers["ledger.json"]
    confirmations = ledgers["intake_confirmations.json"]
    adoptions = ledgers["opportunity_adoptions.json"]
    events = ledgers["radar_events.json"]

    # unique keys that ADR-0002 will constrain
    def conflicts(rows: List[Mapping[str, Any]], key_fn, label: str) -> int:
        seen: Dict[Any, int] = Counter()
        for row in rows:
            try:
                seen[key_fn(row)] += 1
            except Exception:
                seen[("<key-error>",)] += 1
        duplicated = {key: count for key, count in seen.items() if count > 1 and key != ("<key-error>",)}
        for key, count in duplicated.items():
            findings.append(f"{label}: {count}x duplicate {key}")
        return len(duplicated)

    unique_conflicts = 0
    # Codex S: record_id is the PRIMARY KEY of every ADR-0002 table; a
    # collision inside one JSON ledger blocks the migration outright
    for name, rows in ledgers.items():
        pk_seen: Dict[Any, int] = Counter()
        for row in rows:
            pk_seen[str(row.get("record_id") or "")] += 1
        for pk, count in pk_seen.items():
            if count > 1:
                findings.append(f"{name}: {count}x duplicate record_id {pk!r}")
                unique_conflicts += 1
    for name, key in ((EXECUTION_FILE, "job_id"),):
        if not execution_rows:
            continue
        id_seen: Dict[Any, int] = Counter(str(r.get(key) or "") for r in execution_rows)
        for identifier, count in id_seen.items():
            if count > 1:
                findings.append(f"{name}: {count}x duplicate {key} {identifier!r}")
                unique_conflicts += 1
    unique_conflicts += conflicts(
        offers,
        lambda r: (r.get("provider_id"), r.get("source_item_id"), r.get("source_sku_id"),
                   r.get("observed_at"), r.get("payload_hash")),
        "offers.natural_key",
    )
    unique_conflicts += conflicts(
        opportunities, lambda r: (r.get("canonical_sku"), r.get("source")), "opportunities.natural_key",
    )
    unique_conflicts += conflicts(
        [j for j in jobs if j.get("idempotency_key")],
        lambda r: r.get("idempotency_key"), "agent_jobs.idempotency_key",
    )
    unique_conflicts += conflicts(
        [c for c in confirmations if c.get("idempotency_key")],
        lambda c: c.get("idempotency_key"), "intake_confirmations.idempotency_key",
    )
    unique_conflicts += conflicts(
        [a for a in adoptions if a.get("idempotency_key")],
        lambda a: a.get("idempotency_key"), "opportunity_adoptions.idempotency_key",
    )
    unique_conflicts += conflicts(
        [e for e in events if e.get("idempotency_key")],
        lambda e: e.get("idempotency_key"), "radar_events.idempotency_key",
    )
    unique_conflicts += conflicts(
        [o for o in procurements if str(o.get("status")) != "cancelled"],
        lambda o: o.get("sales_order_ref"), "procurement_orders.live_sales_order_ref",
    )
    unique_conflicts += conflicts(
        [i for i in ledgers["inventory.json"]],
        lambda i: (i.get("account_id"), i.get("sku")), "inventory.account_sku",
    )

    # orphan references
    offer_ids = {str(row.get("record_id")) for row in offers}
    opportunity_ids = {str(row.get("record_id")) for row in opportunities}
    orphans = 0
    for match in matches:
        for side in ("left_snapshot_id", "right_snapshot_id"):
            if str(match.get(side) or "") not in offer_ids:
                findings.append(f"orphan: match {match.get('record_id')} references missing offer {match.get(side)}")
                orphans += 1
    for opportunity in opportunities:
        if str(opportunity.get("match_id") or "") not in {str(m.get("record_id")) for m in matches}:
            findings.append(f"orphan: opportunity {opportunity.get('record_id')} references missing match")
            orphans += 1
        for snapshot_id in opportunity.get("offer_snapshot_ids") or []:
            if str(snapshot_id) not in offer_ids:
                findings.append(f"orphan: opportunity {opportunity.get('record_id')} references missing offer {snapshot_id}")
                orphans += 1
        market_id = str(opportunity.get("market_snapshot_id") or "")
        if market_id and market_id not in offer_ids:
            findings.append(f"orphan: opportunity {opportunity.get('record_id')} references missing market snapshot")
            orphans += 1
    for adoption in adoptions:
        if str(adoption.get("opportunity_id") or "") not in opportunity_ids:
            findings.append(f"orphan: adoption {adoption.get('record_id')} references missing opportunity")
            orphans += 1
    for entry in ledger_entries:
        ref = str(entry.get("reversal_of") or "")
        if ref and ref not in {str(e.get("record_id")) for e in ledger_entries}:
            findings.append(f"orphan: ledger reversal {entry.get('record_id')} references missing original {ref}")
            orphans += 1
    for confirmation in confirmations:
        for snapshot_id in confirmation.get("snapshot_ids") or []:
            if str(snapshot_id) not in offer_ids:
                findings.append(f"orphan: confirmation {confirmation.get('record_id')} references missing offer {snapshot_id}")
                orphans += 1

    # unfinished work / unknown executions
    open_jobs = [j for j in jobs if str(j.get("status")) in JOB_OPEN_STATES]
    unknown_jobs = [j for j in jobs if str(j.get("status")) == "unknown"]
    unknown_execution_jobs = [
        str(j.get("job_id")) for j in execution_rows if str(j.get("status")) == "unknown"
    ]
    open_procurements = [p for p in procurements if str(p.get("status")) in PROC_OPEN_STATES]

    # evidence blob integrity (content-addressed archive)
    artifacts_ok = None
    artifact_count = 0
    index_path = data_dir / ARTIFACT_INDEX
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            artifacts = payload.get("artifacts") if isinstance(payload, Mapping) else None
            artifacts = artifacts if isinstance(artifacts, list) else []
            artifact_count = len(artifacts)
            broken = 0
            for artifact in artifacts:
                if not isinstance(artifact, Mapping):
                    continue
                digest = str(artifact.get("sha256") or "")
                blob_key = str(artifact.get("blob_key") or "")
                if not re.fullmatch(r"blobs/[0-9a-f]{2}/[0-9a-f]{64}\.bin", blob_key):
                    findings.append(f"evidence: invalid blob key on {artifact.get('artifact_id')}")
                    broken += 1
                    continue
                blob_path = data_dir / "candidate_evidence" / Path(*blob_key.split("/"))
                if not blob_path.exists():
                    findings.append(f"evidence: missing blob for {artifact.get('artifact_id')}")
                    broken += 1
                    continue
                actual = hashlib.sha256(blob_path.read_bytes()).hexdigest()
                if actual != digest:
                    findings.append(f"evidence: hash mismatch on {artifact.get('artifact_id')}")
                    broken += 1
            artifacts_ok = broken == 0
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(f"evidence: index unreadable ({exc})")
            artifacts_ok = False

    return {
        "schema_version": 1,
        "data_dir": str(data_dir),
        "files": per_file,
        "unique_key_conflicts": unique_conflicts,
        "orphan_references": orphans,
        "open_agent_jobs": [j.get("record_id") for j in open_jobs],
        "unknown_agent_jobs": [j.get("record_id") for j in unknown_jobs],
        "unknown_execution_jobs": unknown_execution_jobs,
        "open_procurements": [p.get("record_id") for p in open_procurements],
        "evidence_artifacts": artifact_count,
        "evidence_integrity_ok": artifacts_ok,
        "findings": findings,
        "note": "read-only reconciliation baseline for the G5A migration; "
                "the PostgreSQL load is a separate approved operation",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="commerce data root (e.g. data/live)")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(json.dumps({"error": f"data dir not found: {data_dir}"}), file=sys.stdout)
        return 1
    try:
        report = audit(data_dir)
    except (AuditError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stdout)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    blocking = report["unique_key_conflicts"] or report["orphan_references"] or report["findings"]
    return 2 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
