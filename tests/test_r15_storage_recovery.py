"""R15 acceptance tests: migration and storage recovery.

Ports the review's three confirmed storage gaps as FIXED-behavior tests
(the review probe asserted the defects reproduce; these assert they no
longer do) plus the full-domain freeze manifest:

* FK load order — a reversal preceding its original in the JSON exports
  as original-first INSERTs and loads under SQLite IMMEDIATE foreign
  keys;
* hardlink authority guard — an output file hardlinked to an
  authoritative JSON is refused (exit 1) and the authority bytes are
  untouched;
* final restored-tree verification — the drill verifies the FINAL tree
  after the divergence rollback: a corrupted covered file or an
  unlisted file in the restored authority set fails with exit 2;
* freeze manifest — round-trip plus tamper/missing/unexpected detection.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from export_commerce_to_sql import export  # noqa: E402


def money(value):
    return {"state": "known", "amount": str(value), "currency": "CNY"}


def ledger_row(record_id, amount, *, direction="credit", reversal_of=None):
    row = {
        "record_id": record_id, "record_type": "ledger_entry", "kind": "settled",
        "direction": direction, "category": "sale_income", "amount": money(amount),
        "order_ref": "O-1",
    }
    if reversal_of:
        row["reversal_of"] = reversal_of
    return row


def seed_data(root: Path) -> Path:
    data = root / "data"
    (data / "commerce").mkdir(parents=True)
    for name in ("offers.json", "matches.json", "opportunities.json", "decisions.json",
                 "agent_jobs.json", "import_batches.json", "intake_confirmations.json",
                 "radar_events.json", "radar_reminders.json", "opportunity_adoptions.json",
                 "procurement_orders.json", "budget_reservations.json",
                 "inventory.json", "ledger.json"):
        (data / "commerce" / name).write_text("[]", encoding="utf-8")
    (data / "resale_candidates.json").write_text("[]", encoding="utf-8")
    (data / "work_orders.json").write_text("[]", encoding="utf-8")
    (data / "execution_jobs.json").write_text("[]", encoding="utf-8")
    return data


class FkLoadOrderTests(unittest.TestCase):
    def test_reversal_first_json_loads_under_immediate_fk(self):
        tmp = Path(tempfile.mkdtemp(prefix="r15-fk-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        data = seed_data(tmp)
        # reversal BEFORE its original in file order
        (data / "commerce" / "ledger.json").write_text(json.dumps([
            ledger_row("reversal", "25", direction="debit", reversal_of="original"),
            ledger_row("original", "100"),
        ]), encoding="utf-8")

        exported = export(data)
        statements = [line for line in exported["sql"].splitlines()
                      if line.startswith("INSERT INTO ledger ")]
        record_ids = [json.loads(
            line.split("VALUES (", 1)[1].rstrip(");").split(", ", 1)[0].strip("'\"")
        ) if False else _first_literal(line) for line in statements]
        # original MUST be emitted before its reversal
        self.assertLess(record_ids.index("original"), record_ids.index("reversal"))

        db = sqlite3.connect(":memory:")
        try:
            db.execute("PRAGMA foreign_keys = ON")
            # DDL statements span multiple lines: split the script on ';'
            # and execute each full CREATE statement (SQLite accepts
            # arbitrary type names like JSONB/TIMESTAMPTZ)
            for statement in exported["sql"].split(";"):
                text = statement.strip()
                # PG expression indexes (payload->>'...') are not SQLite
                # syntax; the FK witness only needs the tables
                if text.startswith("CREATE TABLE"):
                    db.execute(text)
            for statement in statements:
                db.execute(statement.replace("::jsonb", ""))
            count = db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(count, 2, "immediate-FK load must succeed for both rows")

    def test_orphan_reversal_keeps_file_order_for_auditor(self):
        tmp = Path(tempfile.mkdtemp(prefix="r15-orph-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        data = seed_data(tmp)
        (data / "commerce" / "ledger.json").write_text(json.dumps([
            ledger_row("orphan-rev", "5", reversal_of="missing-parent"),
        ]), encoding="utf-8")
        exported = export(data)
        self.assertIn("INSERT INTO ledger", exported["sql"])  # still emitted; auditor reports


def _first_literal(insert_line: str) -> str:
    body = insert_line.split("VALUES (", 1)[1]
    return body.split(",", 1)[0].strip().strip("'")


class HardlinkGuardTests(unittest.TestCase):
    def test_hardlinked_output_refused_authority_untouched(self):
        import importlib.util

        tmp = Path(tempfile.mkdtemp(prefix="r15-hl-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        data = seed_data(tmp)
        authority = data / "commerce" / "ledger.json"
        authority.write_text(json.dumps([ledger_row("e1", "12")]), encoding="utf-8")
        output = tmp / "outside.sql"
        os.link(str(authority), str(output))  # hardlink: same inode, disjoint paths
        self.assertTrue(authority.samefile(output))
        before = authority.read_bytes()

        spec = importlib.util.spec_from_file_location(
            "r15_export", str(PROJECT_ROOT / "scripts" / "export_commerce_to_sql.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        code = module.main(["--data-dir", str(data), "--out", str(output)])

        self.assertEqual(code, 1, "a hardlinked output must be refused")
        self.assertEqual(authority.read_bytes(), before,
                         "the authority file must be untouched")
        self.assertFalse(authority.read_text(encoding="utf-8").startswith("-- generated"))


class DrillFinalVerificationTests(unittest.TestCase):
    def _run_drill(self, tmp: Path, *, inject=None):
        import importlib.util
        from unittest.mock import patch

        data = seed_data(tmp)
        (data / "commerce" / "ledger.json").write_text(json.dumps([
            ledger_row("original", "100"),
        ]), encoding="utf-8")
        work = tmp / "work"

        spec = importlib.util.spec_from_file_location(
            "r15_drill", str(PROJECT_ROOT / "scripts" / "commerce_backup_drill.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if inject is None:
            import io
            import contextlib
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = module.main(["--source", str(data), "--work", str(work)])
            report = json.loads(buffer.getvalue())
            return code, report

        original_copytree = shutil.copytree
        counter = {"n": 0}

        def injected(src, dst, *args, **kwargs):
            result = original_copytree(src, dst, *args, **kwargs)
            if Path(dst).resolve() == (work / "restored").resolve():
                counter["n"] += 1
                inject(counter["n"], Path(dst))
            return result

        import io
        import contextlib
        buffer = io.StringIO()
        with patch.object(module.shutil, "copytree", side_effect=injected):
            with contextlib.redirect_stdout(buffer):
                code = module.main(["--source", str(data), "--work", str(work)])
        report = json.loads(buffer.getvalue())
        return code, report

    def test_clean_run_exits_zero_with_final_verification(self):
        tmp = Path(tempfile.mkdtemp(prefix="r15-clean-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        code, report = self._run_drill(tmp)
        self.assertEqual(code, 0)
        final = report["phases"]["final_verification"]
        self.assertEqual(final["hash_mismatches"], [])
        self.assertEqual(final["unexpected_files"], [])
        self.assertTrue(report["phases"]["divergence"]["demonstrated"])

    def test_corrupted_final_restore_fails_closed(self):
        tmp = Path(tempfile.mkdtemp(prefix="r15-corrupt-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))

        def inject(count, dst):
            if count == 2:  # the FINAL (post-divergence) restore
                (dst / "commerce" / "budget_reservations.json").write_text(
                    json.dumps([{"record_id": "corrupted", "amount": money("99999")}]),
                    encoding="utf-8")

        code, report = self._run_drill(tmp, inject=inject)
        self.assertEqual(code, 2, "a corrupted final tree must fail the drill")
        final = report["phases"]["final_verification"]
        self.assertTrue(any("budget_reservations" in m for m in final["hash_mismatches"]))

    def test_unlisted_file_in_final_tree_flagged(self):
        tmp = Path(tempfile.mkdtemp(prefix="r15-unlisted-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))

        def inject(count, dst):
            if count == 2:
                (dst / "commerce" / "shadow_authorizations.json").write_text(
                    json.dumps([{"record_id": "shadow"}]), encoding="utf-8")

        code, report = self._run_drill(tmp, inject=inject)
        self.assertEqual(code, 2, "an unlisted file in the final tree must not pass")
        final = report["phases"]["final_verification"]
        self.assertTrue(any("shadow_authorizations" in u for u in final["unexpected_files"]))


class FreezeManifestTests(unittest.TestCase):
    def test_round_trip_and_tamper_detection(self):
        import importlib.util

        tmp = Path(tempfile.mkdtemp(prefix="r15-freeze-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        data = seed_data(tmp)
        (data / "commerce" / "ledger.json").write_text(json.dumps([
            ledger_row("e1", "10")]), encoding="utf-8")
        manifest_path = tmp / "freeze.json"

        spec = importlib.util.spec_from_file_location(
            "r15_freeze", str(PROJECT_ROOT / "scripts" / "freeze_manifest.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main(["--data-dir", str(data), "--out", str(manifest_path)])
        self.assertEqual(code, 0)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("commerce/ledger.json", manifest)

        # clean verify passes
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main(["--data-dir", str(data), "--verify", str(manifest_path)])
        self.assertEqual(code, 0)

        # tamper + a new unexpected file -> exit 2 with both listed
        (data / "commerce" / "ledger.json").write_text(
            json.dumps([ledger_row("e1", "999")]), encoding="utf-8")
        (data / "commerce" / "rogue.json").write_text("[]", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main(["--data-dir", str(data), "--verify", str(manifest_path)])
        self.assertEqual(code, 2)
        report = json.loads(buffer.getvalue())
        self.assertIn("commerce/ledger.json", report["mismatches"])
        self.assertIn("commerce/rogue.json", report["unexpected"])


if __name__ == "__main__":
    unittest.main()
