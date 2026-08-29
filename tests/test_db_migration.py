import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xianyu_agent.db_manager import DBManager
from xianyu_agent.db_migration import file_sha256, run_migration


def _build_source_db(path: Path) -> None:
    manager = DBManager(str(path))
    try:
        manager.save_cookie("acc-1", "cookie-value-1", remark="test")
        manager.save_cookie("acc-2", "cookie-value-2", remark="test")
        manager._execute(
            "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
            ("acc-1", "price", "fixed reply"),
        )
    finally:
        manager.close()


def _nonzero_rows(tables: dict) -> dict:
    return {name: count for name, count in tables.items() if count}


class DbMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "legacy.db"
        _build_source_db(self.source)
        self.source_sha = file_sha256(self.source)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_run_writes_nothing_and_reports_plan(self):
        target = self.root / "live" / "xianyu_data.db"
        report = run_migration(self.source, target, dry_run=True)
        self.assertEqual(report["mode"], "dry_run")
        self.assertFalse(target.exists())
        self.assertFalse(Path(report["plan"]["would_backup_to"]).exists())
        self.assertEqual(_nonzero_rows(report["plan"]["tables"]), {"cookies": 2, "keywords": 1})
        self.assertIn("cookies", report["plan"]["tables"])
        self.assertEqual(report["source"]["sha256"], self.source_sha)

    def test_migration_copies_rows_verifies_and_never_touches_source(self):
        target = self.root / "live" / "xianyu_data.db"
        backup_dir = self.root / "backups"
        report = run_migration(self.source, target, backup_dir=backup_dir)
        self.assertEqual(report["mode"], "migrate")
        self.assertTrue(report["verified"])
        self.assertEqual(_nonzero_rows(report["target"]["tables"]), {"cookies": 2, "keywords": 1})
        self.assertEqual(report["target"]["tables"], report["source"]["tables"])
        self.assertEqual(report["target"]["integrity_check"], "ok")
        self.assertEqual(file_sha256(self.source), self.source_sha)

        backup = Path(report["backup"]["backup_path"])
        self.assertTrue(backup.is_file())
        self.assertEqual(report["backup"]["sha256"], self.source_sha)
        sidecar = json.loads(Path(report["backup"]["sidecar"]).read_text(encoding="utf-8"))
        self.assertEqual(sidecar["sha256"], self.source_sha)

        conn = sqlite3.connect(str(target))
        try:
            values = [row[0] for row in conn.execute("SELECT value FROM cookies ORDER BY id")]
        finally:
            conn.close()
        self.assertEqual(values, ["cookie-value-1", "cookie-value-2"])

    def test_rerun_on_migrated_target_is_idempotent_no_op(self):
        target = self.root / "live" / "xianyu_data.db"
        first = run_migration(self.source, target)
        target_sha = file_sha256(target)
        second = run_migration(self.source, target)
        self.assertEqual(second["mode"], "already_migrated")
        self.assertTrue(second["verified"])
        self.assertIsNone(second.get("backup"))
        self.assertEqual(file_sha256(target), target_sha)
        self.assertEqual(first["target"]["tables"], second["target"]["tables"])

    def test_non_empty_divergent_target_is_refused(self):
        target = self.root / "live" / "xianyu_data.db"
        run_migration(self.source, target)
        divergent = self.root / "other.db"
        _build_source_db(divergent)
        conn = sqlite3.connect(str(divergent))
        try:
            conn.execute("INSERT INTO cookies (id, value) VALUES ('acc-3', 'x')")
            conn.commit()
        finally:
            conn.close()
        target_before = file_sha256(target)
        with self.assertRaises(RuntimeError):
            run_migration(divergent, target)
        self.assertEqual(file_sha256(target), target_before)

    def test_missing_source_is_rejected(self):
        with self.assertRaises(RuntimeError):
            run_migration(self.root / "missing.db", self.root / "live.db")

    def test_source_and_target_must_differ(self):
        with self.assertRaises(RuntimeError):
            run_migration(self.source, self.source)


if __name__ == "__main__":
    unittest.main()
