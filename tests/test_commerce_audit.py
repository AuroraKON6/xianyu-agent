"""G5A reconciliation auditor tests (A113 checker, S level).

The auditor reads only synthetic ledgers in a temp directory. The real
PostgreSQL load (A111/A112/A114) stays environment-blocked (no instance
available) and is NOT covered here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_commerce_ledgers.py"


def _offer(record_id, sku="SKU-X", price="160"):
    return {
        "record_id": record_id, "record_type": "offer_snapshot",
        "provider_id": "supplier_file:杭州五金", "source_item_id": sku,
        "source_sku_id": sku, "observed_at": "2026-10-12T08:00:00+00:00",
        "payload_hash": f"hash-{record_id}", "eligible_price": {
            "state": "known", "amount": price, "currency": "CNY"},
    }


class CommerceAuditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-"))
        (self.tmp / "commerce").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, rows):
        (self.tmp / "commerce" / name).write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8"
        )

    def _run(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--data-dir", str(self.tmp)],
            capture_output=True, text=True, encoding="utf-8",
        )
        return result.returncode, json.loads(result.stdout or "{}")

    def test_clean_ledgers_exit_zero(self):
        offer = _offer("offer_1")
        self._write("offers.json", [offer])
        self._write("matches.json", [{
            "record_id": "match_1", "record_type": "product_match",
            "left_snapshot_id": "offer_1", "right_snapshot_id": "offer_1",
        }])
        self._write("opportunities.json", [{
            "record_id": "opp_1", "record_type": "opportunity",
            "canonical_sku": "SKU-X", "source": "supplier_file:杭州五金",
            "match_id": "match_1", "offer_snapshot_ids": ["offer_1"],
        }])
        code, report = self._run()
        self.assertEqual(code, 0, report.get("findings"))
        self.assertEqual(report["unique_key_conflicts"], 0)
        self.assertEqual(report["orphan_references"], 0)
        self.assertEqual(report["files"]["offers.json"]["rows"], 1)

    def test_duplicate_key_and_orphans_exit_two(self):
        # identical natural key incl. payload_hash -> real uniqueness conflict
        self._write("offers.json", [_offer("offer_1"), dict(_offer("offer_2"), payload_hash="hash-offer_1")])
        self._write("matches.json", [{
            "record_id": "match_1", "record_type": "product_match",
            "left_snapshot_id": "offer_1", "right_snapshot_id": "offer_missing",
        }])
        self._write("opportunities.json", [{
            "record_id": "opp_1", "record_type": "opportunity",
            "canonical_sku": "SKU-X", "source": "supplier_file:杭州五金",
            "match_id": "match_missing", "offer_snapshot_ids": [],
        }])
        self._write("agent_jobs.json", [
            {"record_id": "job_1", "record_type": "agent_job", "status": "queued",
             "idempotency_key": "dup-key"},
            {"record_id": "job_2", "record_type": "agent_job", "status": "succeeded",
             "idempotency_key": "dup-key"},
        ])
        code, report = self._run()
        self.assertEqual(code, 2)
        self.assertGreater(report["unique_key_conflicts"], 0)
        self.assertGreater(report["orphan_references"], 0)
        self.assertIn("job_1", report["open_agent_jobs"])
        joined = " ".join(report["findings"])
        self.assertIn("offers.natural_key", joined)
        self.assertIn("idempotency_key", joined)

    def test_ledger_sums_and_live_procurement_conflict(self):
        self._write("ledger.json", [
            {"record_id": "e1", "record_type": "ledger_entry", "kind": "settled",
             "direction": "credit", "amount": {"state": "known", "amount": "300", "currency": "CNY"}},
            {"record_id": "e2", "record_type": "ledger_entry", "kind": "settled",
             "direction": "debit", "amount": {"state": "known", "amount": "198", "currency": "CNY"}},
        ])
        self._write("procurement_orders.json", [
            {"record_id": "p1", "record_type": "procurement_order", "status": "ordered",
             "sales_order_ref": "xy-1"},
            {"record_id": "p2", "record_type": "procurement_order", "status": "ordered",
             "sales_order_ref": "xy-1"},
        ])
        code, report = self._run()
        self.assertEqual(code, 2)
        self.assertEqual(report["files"]["ledger.json"]["money_net"], {"CNY": "102"})
        joined = " ".join(report["findings"])
        self.assertIn("procurement_orders.live_sales_order_ref", joined)

    def test_evidence_blob_integrity_checked(self):
        digest = hashlib.sha256(b"blob-bytes").hexdigest()
        blob_dir = self.tmp / "candidate_evidence" / "blobs" / digest[:2]
        blob_dir.mkdir(parents=True, exist_ok=True)
        (blob_dir / f"{digest}.bin").write_bytes(b"blob-bytes")
        (self.tmp / "candidate_evidence" / "index.json").write_text(json.dumps({
            "schema_version": 1,
            "artifacts": [{"artifact_id": "cea_" + "a" * 24, "sha256": digest,
                           "blob_key": f"blobs/{digest[:2]}/{digest}.bin"}],
        }), encoding="utf-8")
        code, report = self._run()
        self.assertEqual(report["evidence_artifacts"], 1)
        self.assertTrue(report["evidence_integrity_ok"])

        # corrupt the blob -> finding + exit 2
        (blob_dir / f"{digest}.bin").write_bytes(b"tampered")
        code, report = self._run()
        self.assertEqual(code, 2)
        self.assertFalse(report["evidence_integrity_ok"])
        self.assertTrue(any("hash mismatch" in f for f in report["findings"]))

    def test_missing_dir_exit_one(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--data-dir", str(self.tmp / "nope")],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
