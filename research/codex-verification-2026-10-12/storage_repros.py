"""Review-only storage evidence; failures assert missing expected invariants.

Run with --case all or a stable test_* method below. No server, finance
harness, database, network, model, platform, or MCP is invoked. SQL checks
inspect generated text only and do not claim PostgreSQL validation.
The nested backup case ALWAYS intercepts copytree before any copy. Other
backup cases copy only disjoint temporary trees behind a path guard.
Only temporary fixtures and sibling storage-repros.log are written.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
LOG = Path(__file__).with_name("storage-repros.log")
CASES = {
    "test_sql_text_literal": ("P1", "A113", "scripts/export_commerce_to_sql.py:55"),
    "test_export_authority_overwrite": ("P1", "A113", "scripts/export_commerce_to_sql.py:134"),
    "test_audit_budget_authority": ("P1", "A110/A113", "scripts/audit_commerce_ledgers.py:28"),
    "test_audit_execution_authority": ("P1", "A113", "scripts/audit_commerce_ledgers.py:185"),
    "test_export_budget_authority": ("P1", "A110/A113", "scripts/export_commerce_to_sql.py:18"),
    "test_export_execution_authority": ("P1", "A110/A113", "scripts/export_commerce_to_sql.py:18"),
    "test_audit_primary_key_collision": ("P1", "A113", "scripts/audit_commerce_ledgers.py:99"),
    "test_backup_nested_destination": ("P1", "A114", "scripts/commerce_backup_drill.py:169"),
    "test_backup_budget_integrity": ("P1", "A114", "scripts/commerce_backup_drill.py:100"),
    "test_backup_execution_integrity": ("P1", "A114", "scripts/commerce_backup_drill.py:100"),
    "test_export_radar_unique_constraint": ("P2", "A110/A113", "scripts/export_commerce_to_sql.py:94"),
    "test_export_adoption_unique_constraint": ("P2", "A110/A113", "scripts/export_commerce_to_sql.py:94"),
    "test_export_reversal_foreign_key": ("P2", "A110/A113", "scripts/export_commerce_to_sql.py:94"),
    "test_export_mixed_currency_footer": ("P2", "A113", "scripts/export_commerce_to_sql.py:108"),
}


def within(path, root):
    path = os.path.normcase(str(Path(path).resolve()))
    root = os.path.normcase(str(Path(root).resolve()))
    return os.path.commonpath([path, root]) == root


def money(value, currency="CNY"):
    return {"state": "known", "amount": str(value), "currency": currency}


class CopyIntercepted(RuntimeError):
    pass


class StorageRepros(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="codex-storage-repros-")
        cls.base = Path(cls.tmp.name)
        cls.stack = ExitStack()
        cls.io_attempts = []
        cls.original_cwd = Path.cwd()
        os.chdir(cls.base)
        cls.stack.enter_context(patch.dict(os.environ, {
            "DATA_DIR": str(cls.base), "XIANYU_ADAPTER": "stub",
            "SHADOW_OBSERVER_ENABLED": "0", "RADAR_DISPATCHER_ENABLED": "0",
        }))

        def deny_io(*args, **kwargs):
            cls.io_attempts.append("blocked")
            raise AssertionError("Review harness forbids network, databases, and external processes")

        for target, name in (
            (socket.socket, "connect"), (socket.socket, "connect_ex"),
            (socket, "create_connection"), (subprocess, "Popen"), (sqlite3, "connect"),
        ):
            cls.stack.enter_context(patch.object(target, name, deny_io))

        original_copytree = shutil.copytree

        def safe_copytree(source, destination, *args, **kwargs):
            if not within(source, cls.base) or not within(destination, cls.base):
                raise AssertionError("Backup copy escaped temporary fixture boundary")
            if within(destination, source) or within(source, destination):
                raise AssertionError("Recursive or overlapping backup copy prevented")
            return original_copytree(source, destination, *args, **kwargs)

        cls.stack.enter_context(patch.object(shutil, "copytree", safe_copytree))
        try:
            for attribute, filename in (
                ("audit", "audit_commerce_ledgers.py"),
                ("exporter", "export_commerce_to_sql.py"),
                ("backup", "commerce_backup_drill.py"),
            ):
                spec = importlib.util.spec_from_file_location(
                    "review_only_" + attribute, REPO / "scripts" / filename,
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                setattr(cls, attribute, module)
        except BaseException:
            cls._close()
            raise

    @classmethod
    def _close(cls):
        os.chdir(cls.original_cwd)
        cls.stack.close()
        cls.tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.io_attempts:
                raise AssertionError("Forbidden external I/O was attempted")
        finally:
            cls._close()

    def setUp(self):
        self.root = self.base / self._testMethodName
        self.data = self.root / "data"
        self.commerce = self.data / "commerce"
        self.commerce.mkdir(parents=True)
        self.write("commerce/offers.json", [])
        self.write("commerce/ledger.json", [])

    def write(self, relative, value):
        path = self.data / relative
        self.assertTrue(within(path, self.base), "Fixture writes must stay temporary")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def seed_budget(self):
        return self.write("commerce/budget_reservations.json", [{
            "record_id": "fixture-budget", "record_type": "budget_reservation",
            "account_id": "fixture-account", "job_id": "fixture-job",
            "budget_bucket": "procurement", "business_day": "2026-10-12",
            "status": "reserved", "amount": money(100),
        }])

    def seed_execution(self):
        return self.write("execution_jobs.json", [{
            "job_id": "fixture-unknown-execution", "work_order_id": "fixture-work-order",
            "status": "unknown", "created_at": 1, "action_index": 0,
        }])

    def test_sql_text_literal(self):
        self.assertEqual(self.exporter._sql_literal("led_1"), "'led_1'",
                         "TEXT IDs must not acquire JSON double quotes")

    def test_export_authority_overwrite(self):
        authority = self.commerce / "offers.json"
        original = authority.read_bytes()
        with redirect_stdout(io.StringIO()):
            result = self.exporter.main(["--data-dir", str(self.data), "--out", str(authority)])
        self.assertEqual(authority.read_bytes(), original,
                         "Export must preserve input authority; actual exit=%r" % result)
        self.assertNotEqual(result, 0, "An output inside the authority must be refused")

    def test_audit_budget_authority(self):
        self.seed_budget()
        report = self.audit.audit(self.data)
        self.assertIn("budget_reservations.json", report["files"],
                      "Audit must count the persisted budget authority")
        self.assertEqual(report["files"]["budget_reservations.json"]["rows"], 1)

    def test_audit_execution_authority(self):
        self.seed_execution()
        report = self.audit.audit(self.data)
        self.assertIn("fixture-unknown-execution", json.dumps(report),
                      "Actual execution_jobs UNKNOWN must not disappear behind agent_jobs")

    def test_export_budget_authority(self):
        self.seed_budget()
        result = self.exporter.export(self.data)
        self.assertIn("budget_reservations", result["footer"]["row_counts"],
                      "Migration output must include persisted budget authority")
        self.assertIn("fixture-budget", result["sql"])

    def test_export_execution_authority(self):
        self.seed_execution()
        result = self.exporter.export(self.data)
        self.assertIn("fixture-unknown-execution", result["sql"],
                      "Migration must retain actual UNKNOWN execution facts")

    def test_audit_primary_key_collision(self):
        self.write("commerce/ledger.json", [{
            "record_id": "same", "record_type": "ledger_entry", "kind": "settled",
            "direction": "credit", "amount": money(100),
        }] * 2)
        with redirect_stdout(io.StringIO()):
            result = self.audit.main(["--data-dir", str(self.data)])
        self.assertEqual(result, 2, "Duplicate ledger primary keys must block migration audit")

    def test_backup_nested_destination(self):
        nested = self.data / "empty-work"
        nested.mkdir()
        attempts = []

        def intercept(source, destination, *args, **kwargs):
            attempts.append((str(source), str(destination)))
            # NEVER delegate this call to any real copy implementation.
            raise CopyIntercepted("Nested copy was intercepted before execution")

        with patch.object(self.backup.shutil, "copytree", intercept):
            with redirect_stdout(io.StringIO()):
                try:
                    result = self.backup.main(["--source", str(self.data), "--work", str(nested)])
                except CopyIntercepted:
                    result = None
        self.assertEqual(attempts, [],
                         "CLI must reject nested work before copytree; intercepted attempts=%r" % attempts)
        self.assertEqual(result, 1, "Unsafe nested destination must be rejected")

    def tampered_restore(self, relative):
        safe_copytree = self.backup.shutil.copytree

        def corrupt_copy(source, destination, *args, **kwargs):
            result = safe_copytree(source, destination, *args, **kwargs)
            if Path(destination).name == "restored":
                target = Path(destination) / relative
                self.assertTrue(within(target, self.base))
                target.write_text("[]", encoding="utf-8")
            return result

        with patch.object(self.backup.shutil, "copytree", corrupt_copy):
            report = self.backup.drill(self.data, self.root / "work")
        self.assertTrue(report["phases"]["restore"]["hash_mismatches"],
                        "Restored authority corruption must fail verification: " + relative)

    def test_backup_budget_integrity(self):
        self.seed_budget()
        self.tampered_restore("commerce/budget_reservations.json")

    def test_backup_execution_integrity(self):
        self.seed_execution()
        self.tampered_restore("execution_jobs.json")

    def test_export_radar_unique_constraint(self):
        sql = self.exporter.export(self.data)["sql"]
        self.assertRegex(sql, r"(?is)CREATE\s+UNIQUE\s+INDEX[^;]*\bON\s+radar_events\b[^;]*idempotency_key",
                         "ADR radar idempotency constraint must exist in generated DDL")

    def test_export_adoption_unique_constraint(self):
        sql = self.exporter.export(self.data)["sql"]
        self.assertRegex(sql, r"(?is)CREATE\s+UNIQUE\s+INDEX[^;]*\bON\s+opportunity_adoptions\b[^;]*idempotency_key",
                         "ADR adoption idempotency constraint must exist in generated DDL")

    def test_export_reversal_foreign_key(self):
        sql = self.exporter.export(self.data)["sql"]
        self.assertRegex(sql, r"(?is)(?:FOREIGN\s+KEY\s*\(\s*reversal_of\s*\)\s*REFERENCES\s+ledger|reversal_of\s+\w+\s+REFERENCES\s+ledger)",
                         "ADR ledger reversal foreign key must exist in generated DDL")

    def test_export_mixed_currency_footer(self):
        self.write("commerce/ledger.json", [{
            "record_id": currency, "record_type": "ledger_entry", "kind": "settled",
            "direction": "credit", "amount": money(100, currency),
        } for currency in ("CNY", "USD")])
        result = self.exporter.export(self.data)
        self.assertEqual(result["footer"]["ledger_cny_net"], "100",
                         "CNY reconciliation must not include USD without an explicit conversion")


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    names = list(CASES) if args.case == "all" else [args.case]
    with LOG.open("w", encoding="utf-8", newline="\n") as log:
        output = Tee(sys.stdout, log)
        output.write("EXPECTED-INVARIANT STORAGE REVIEW; failures expose defects, not acceptance\n")
        output.write("BOUNDARY: temp-only; no DB/network/model/platform/MCP; nested copytree always intercepted\n")
        for name in names:
            severity, ids, ref = CASES[name]
            output.write("CASE %s | %s | %s | %s\n" % (name, severity, ids, REPO / ref))
        output.flush()
        suite = unittest.TestSuite(StorageRepros(name) for name in names)
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        output.write("BOUNDARY: external I/O attempts=%d; all fixtures temporary; finance evidence untouched\n" % len(StorageRepros.io_attempts))
        output.flush()
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
