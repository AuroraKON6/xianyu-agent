"""R07 acceptance tests: fixed-sample candidate export.

* JSON / CSV / manifest are mutually consistent (same rows, digests match);
* same input produces the same candidate set — duplicates are refused at
  the repository natural key and re-export never adds rows;
* expired snapshots are annotated, never turned into new rows;
* an empty repository (no spread at all) still exports a valid triple;
* exporting is strictly read-only (repository file hashes unchanged);
* original observation metadata (time, unit, MOQ, spec, price type) is
  preserved verbatim on the export rows.
"""
from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quote_fixtures import with_declared_quote_provenance

from xianyu_agent.application.candidate_export import CandidateExporter
from xianyu_agent.application.research import ResearchService
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import build_offer_snapshot

UTC = timezone.utc


def _supplier_snapshot(sku, *, price="150", observed=None, spec="120x60x85cm", moq="2"):
    observed = observed or datetime.now(UTC)
    return with_declared_quote_provenance(build_offer_snapshot(
        provider_id="supplier_file:R07供应商",
        source_item_id=sku,
        source_sku_id=sku,
        observed_at=observed,
        attributes={"name": "不锈钢工作台", "spec": spec, "moq": moq},
        condition="全新",
        unit="件",
        price_type="supplier_confirmed",
        eligible_price={"state": "known", "amount": price, "currency": "CNY"},
        shipping_quote={"state": "known", "amount": "15", "currency": "CNY"},
        stock_state="unknown",
        payload_hash="r07-%s-%s-%s" % (sku, price, observed.isoformat()),
        account_id="",
        source_ref="test",
    ))


def _market_snapshot(sku, *, price="300", observed=None):
    observed = observed or datetime.now(UTC)
    return build_offer_snapshot(
        provider_id="manual_observation",
        source_item_id=sku,
        source_sku_id=sku,
        observed_at=observed,
        attributes={"name": "工作台 市场观察"},
        condition="全新",
        unit="件",
        price_type="market_observed",
        eligible_price={"state": "known", "amount": price, "currency": "CNY"},
        shipping_quote={"state": "unknown"},
        stock_state="unknown",
        payload_hash="r07m-%s-%s-%s" % (sku, price, observed.isoformat()),
        account_id="",
        source_ref="test",
    )


class CandidateExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repos = JsonCommerceRepositories(self.root)
        self.research = ResearchService(self.repos)
        self.exporter = CandidateExporter(self.research)

    def _seed_opportunity(self, sku, *, supplier_price="150", observed=None):
        supplier, _ = self.repos.offers.add(_supplier_snapshot(sku, price=supplier_price, observed=observed))
        market, _ = self.repos.offers.add(_market_snapshot(sku))
        return self.repos.opportunities.create({
            "record_type": "opportunity",
            "record_id": "opp-r07-%s" % sku,
            "canonical_sku": sku,
            "source": "resale_candidate",
            "status": "matched",
            "created_at": datetime.now(UTC).isoformat(),
            "offer_snapshot_ids": [supplier["record_id"]],
            "market_snapshot_id": market["record_id"],
            "economics": {
                "inputs": {},
                "scenarios": {
                    "conservative": {
                        "contribution_profit": {"state": "known", "amount": "80", "currency": "CNY"},
                    }
                },
            },
        })

    def _repo_hashes(self):
        commerce = self.root / "commerce"
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(commerce.glob("*.json"))
            if path.is_file()
        }

    def test_export_triple_is_consistent(self):
        self._seed_opportunity("R07-A")
        out = Path(tempfile.mkdtemp(prefix="r07-export-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(out, ignore_errors=True))
        result = self.exporter.export(out, label="t1")
        self.assertEqual(result["rows"], 1)

        payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["row_count"], 1)
        with open(result["csv"], encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["opportunity_id"], payload["candidates"][0]["opportunity_id"])

        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        for name, entry in manifest["files"].items():
            digest = hashlib.sha256((out / name).read_bytes()).hexdigest()
            self.assertEqual(digest, entry["sha256"], name)
            self.assertEqual(entry["rows"], 1)
        self.assertIn("offers.json", manifest["input_manifest"])

    def test_same_input_no_duplicate_candidates_and_reexport_stable(self):
        first = self._seed_opportunity("R07-B")
        with self.assertRaises(Exception):
            self.repos.opportunities.create({
                "record_type": "opportunity",
                "record_id": "opp-r07-B-dup",
                "canonical_sku": "R07-B",
                "source": "resale_candidate",
                "status": "matched",
                "created_at": datetime.now(UTC).isoformat(),
            })
        rows1 = self.exporter.build_rows()
        rows2 = self.exporter.build_rows()
        self.assertEqual([r["opportunity_id"] for r in rows1], [first["record_id"]])
        self.assertEqual([r["opportunity_id"] for r in rows2], [r["opportunity_id"] for r in rows1])

    def test_expired_snapshot_is_annotated_not_new(self):
        stale_time = datetime.now(UTC) - timedelta(days=400)
        self._seed_opportunity("R07-C", observed=stale_time)
        rows = self.exporter.build_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["freshness"], "expired")
        # a re-imported NEWER snapshot does not create a new export row
        self.repos.offers.add(_supplier_snapshot("R07-C", price="140"))
        rows_after = self.exporter.build_rows()
        self.assertEqual([r["opportunity_id"] for r in rows_after], [rows[0]["opportunity_id"]])

    def test_empty_repository_exports_valid_empty_triple(self):
        out = Path(tempfile.mkdtemp(prefix="r07-empty-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(out, ignore_errors=True))
        result = self.exporter.export(out, label="empty")
        self.assertEqual(result["rows"], 0)
        payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["candidates"], [])
        with open(result["csv"], encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 0)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["files"][Path(result["json"]).name]["rows"], 0)

    def test_export_is_read_only(self):
        self._seed_opportunity("R07-D")
        before = self._repo_hashes()
        out = Path(tempfile.mkdtemp(prefix="r07-ro-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(out, ignore_errors=True))
        self.exporter.export(out, label="ro")
        self.assertEqual(self._repo_hashes(), before)

    def test_observation_metadata_preserved_verbatim(self):
        observed = datetime.now(UTC) - timedelta(hours=2)
        self._seed_opportunity("R07-E", observed=observed)
        row = self.exporter.build_rows()[0]
        supplier = self.repos.offers.get(row["supplier_snapshot_id"])
        self.assertEqual(row["supplier_observed_at"], supplier["observed_at"])
        self.assertEqual(row["supplier_unit"], supplier["unit"])
        self.assertEqual(row["supplier_price_type"], supplier["price_type"])
        self.assertEqual(row["supplier_spec"], supplier["attributes"]["spec"])
        self.assertEqual(row["supplier_moq"], supplier["attributes"]["moq"])
        self.assertEqual(row["supplier_price"], "150 CNY")


if __name__ == "__main__":
    unittest.main()
