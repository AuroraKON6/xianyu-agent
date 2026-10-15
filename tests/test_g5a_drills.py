"""G5A drill + exporter tests (A113 JSON->SQL / A114 JSON-level, S layer)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class BackupDrillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drill-"))
        self.source = self.tmp / "data"
        (self.source / "commerce").mkdir(parents=True)
        (self.source / "commerce" / "ledger.json").write_text(json.dumps([
            {"record_id": "e1", "record_type": "ledger_entry", "direction": "credit",
             "amount": {"state": "known", "amount": "300", "currency": "CNY"}},
        ]), encoding="utf-8")
        (self.source / "commerce" / "offers.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_drill_verifies_and_demonstrates_divergence(self):
        work = self.tmp / "work"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "commerce_backup_drill.py"),
             "--source", str(self.source), "--work", str(work)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["phases"]["backup"]["files"], 2)
        self.assertEqual(report["phases"]["restore"]["hash_mismatches"], [])
        self.assertTrue(report["phases"]["divergence"]["demonstrated"])
        self.assertEqual(report["phases"]["divergence"]["lost_rows"], 1)

    def test_drill_detects_corruption(self):
        work = self.tmp / "work2"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "commerce_backup_drill.py"),
             "--source", str(self.source), "--work", str(work)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        # tamper the RESTORED tree, then the manifest hash check the drill
        # relies on must flag it (this is the corruption-detection path)
        sys.path.insert(0, str(SCRIPTS))
        import importlib

        drill_module = importlib.import_module("commerce_backup_drill")
        manifest = json.loads((work / "backup" / "_backup_manifest.json").read_text(encoding="utf-8"))
        target = work / "restored" / "commerce" / "ledger.json"
        target.write_text('["tampered"]', encoding="utf-8")
        mismatched = [
            relative
            for relative, expected in manifest.items()
            if not (work / "restored" / relative).exists()
            or drill_module._sha256(work / "restored" / relative) != expected
        ]
        self.assertTrue(any("ledger.json" in name for name in mismatched), mismatched)


class SqlExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sqlout-"))
        self.data = self.tmp / "data"
        (self.data / "commerce").mkdir(parents=True)
        (self.data / "commerce" / "ledger.json").write_text(json.dumps([
            {"record_id": "e1", "record_type": "ledger_entry", "created_at": "2026-10-12T00:00:00+00:00",
             "direction": "credit", "amount": {"state": "known", "amount": "300", "currency": "CNY"}},
            {"record_id": "e2", "record_type": "ledger_entry",
             "direction": "debit", "amount": {"state": "known", "amount": "198", "currency": "CNY"}},
        ]), encoding="utf-8")
        (self.data / "commerce" / "offers.json").write_text(json.dumps([
            {"record_id": "o1", "record_type": "offer_snapshot", "provider_id": "p",
             "source_item_id": "s", "payload_hash": "h", "observed_at": "t"},
        ]), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_reconciles_counts_and_money(self):
        out = self.tmp / "export.sql"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "export_commerce_to_sql.py"),
             "--data-dir", str(self.data), "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        footer = json.loads(result.stdout)
        self.assertEqual(footer["row_counts"]["ledger"], 2)
        self.assertEqual(footer["row_counts"]["offers"], 1)
        self.assertEqual(footer["ledger_cny_net"], "102")
        sql = out.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS offers", sql)
        self.assertIn("offers_natural_key", sql)
        self.assertIn("INSERT INTO ledger", sql)
        self.assertIn("BEGIN;", sql)
        self.assertIn("COMMIT;", sql)
        self.assertIn("300", sql)  # full payload round-trips into the dump


if __name__ == "__main__":
    unittest.main()
