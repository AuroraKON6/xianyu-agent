"""Fixed-sample candidate export (taskbook R07, old X01–X03 goals).

Reads the research state through :class:`ResearchService` (strictly
read-only), freezes the input versions (per-file SHA-256 of the
commerce repositories) and writes three mutually consistent artifacts:

* ``candidates-<label>.json`` — full rows with per-snapshot freshness
  and provenance summaries;
* ``candidates-<label>.csv``  — the same rows, one line per opportunity;
* ``manifest.json``           — file digests, row counts, the frozen
  input manifest and the exporter version.

Properties required by the taskbook:

* same input -> the same candidate set (no duplicates; the repository
  natural key already refuses duplicate opportunities, the exporter
  itself never writes);
* expired / future / re-imported snapshots never produce NEW export
  rows — they are annotated (freshness), nothing more;
* an empty repository (no spread at all) still exports a valid, empty,
  consistent triple;
* original observation metadata is preserved verbatim from the stored
  snapshots (observed_at, unit, MOQ, spec attributes, price type).
"""
from __future__ import annotations

import csv
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .research import ResearchService

EXPORTER_VERSION = "candidate-export/1"

_REPOSITORY_FILES = (
    "offers.json",
    "matches.json",
    "opportunities.json",
    "decisions.json",
)

CSV_COLUMNS = (
    "opportunity_id",
    "status",
    "created_at",
    "canonical_sku",
    "supplier_snapshot_id",
    "market_snapshot_id",
    "supplier_price",
    "market_price",
    "spread_state",
    "spread_amount",
    "freshness",
    "decision_count",
)


class CandidateExportError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _price_text(snapshot: Optional[Dict[str, Any]]) -> str:
    if not snapshot:
        return ""
    price = snapshot.get("eligible_price") or {}
    if str(price.get("state")) != "known":
        return "unknown"
    return "%s %s" % (price.get("amount", ""), price.get("currency", ""))


def _spread_text(opportunity: Dict[str, Any]) -> Dict[str, str]:
    economics = opportunity.get("economics") or {}
    conservative = (economics.get("scenarios") or {}).get("conservative") or {}
    contribution = conservative.get("contribution_profit") or {}
    if not contribution:
        return {"spread_state": "unknown", "spread_amount": ""}
    state = str(contribution.get("state") or "unknown")
    return {
        "spread_state": state,
        "spread_amount": str(contribution.get("amount", "")) if state == "known" else "",
    }


def _freshness_text(snapshot: Optional[Dict[str, Any]]) -> str:
    if not snapshot:
        return "missing"
    from ..providers.freshness import classify_snapshot

    meta = classify_snapshot(snapshot)
    return str(meta.get("freshness") or "unknown")


class CandidateExporter:
    def __init__(self, research: ResearchService, *, clock=None) -> None:
        self._research = research
        self._clock = clock or time.time

    # ---- input freeze ------------------------------------------------------

    def input_manifest(self) -> Dict[str, Any]:
        data_dir = Path(self._research.repositories.data_dir) / "commerce"
        manifest: Dict[str, Any] = {}
        for name in _REPOSITORY_FILES:
            path = data_dir / name
            if path.is_file():
                manifest[name] = {"sha256": _sha256_file(path), "bytes": path.stat().st_size}
            else:
                manifest[name] = {"sha256": None, "bytes": 0, "note": "absent"}
        return manifest

    # ---- rows ----------------------------------------------------------------

    def _row_for(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        snapshot_ids = list(opportunity.get("offer_snapshot_ids") or [])
        supplier_id = snapshot_ids[-1] if snapshot_ids else str(opportunity.get("supplier_snapshot_id") or "")
        market_id = str(opportunity.get("market_snapshot_id") or "")
        supplier = self._research.get_offer_snapshot(supplier_id)
        market = self._research.get_offer_snapshot(market_id)
        decisions = self._research.list_decisions(opportunity_id=str(opportunity.get("record_id") or ""))
        spread = _spread_text(opportunity)
        return {
            "opportunity_id": str(opportunity.get("record_id") or ""),
            "status": str(opportunity.get("status") or ""),
            "created_at": str(opportunity.get("created_at") or ""),
            "canonical_sku": str(opportunity.get("canonical_sku") or ""),
            "supplier_snapshot_id": supplier_id,
            "market_snapshot_id": market_id,
            "supplier_price": _price_text(supplier),
            "market_price": _price_text(market),
            "spread_state": spread["spread_state"],
            "spread_amount": spread["spread_amount"],
            "freshness": _freshness_text(supplier),
            "decision_count": len(decisions),
            # preserve original observation metadata verbatim (R07)
            "supplier_observed_at": str((supplier or {}).get("observed_at") or ""),
            "supplier_unit": str((supplier or {}).get("unit") or ""),
            "supplier_moq": str(((supplier or {}).get("attributes") or {}).get("moq") or ""),
            "supplier_spec": str(((supplier or {}).get("attributes") or {}).get("spec") or ""),
            "supplier_price_type": str((supplier or {}).get("price_type") or ""),
        }

    def build_rows(self, *, status: str = "") -> List[Dict[str, Any]]:
        opportunities = self._research.list_opportunities(status=status)
        rows = [self._row_for(opportunity) for opportunity in opportunities]
        rows.sort(key=lambda row: row["opportunity_id"])
        ids = [row["opportunity_id"] for row in rows]
        if len(set(ids)) != len(ids):
            raise CandidateExportError("duplicate opportunity ids in export input")
        return rows

    # ---- export ---------------------------------------------------------------

    def export(self, output_dir: Path, *, status: str = "", label: Optional[str] = None) -> Dict[str, Any]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock()))
        label = label or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self._clock()))

        rows = self.build_rows(status=status)
        payload = {
            "generated_at": generated_at,
            "exporter_version": EXPORTER_VERSION,
            "status_filter": status,
            "row_count": len(rows),
            "candidates": rows,
        }
        json_path = output / ("candidates-%s.json" % label)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        csv_path = output / ("candidates-%s.csv" % label)
        with open(str(csv_path), "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})

        manifest = {
            "generated_at": generated_at,
            "exporter_version": EXPORTER_VERSION,
            "label": label,
            "files": {
                json_path.name: {"sha256": _sha256_file(json_path), "rows": len(rows)},
                csv_path.name: {"sha256": _sha256_file(csv_path), "rows": len(rows)},
            },
            "input_manifest": self.input_manifest(),
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "json": str(json_path),
            "csv": str(csv_path),
            "manifest": str(manifest_path),
            "rows": len(rows),
        }
