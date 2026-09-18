"""WP14 backup / verify / restore-drill tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xianyu_agent.backup import create_backup, restore_rehearsal, verify_backup


def _seed_commerce(data_dir: Path) -> None:
    commerce = data_dir / "commerce"
    commerce.mkdir(parents=True, exist_ok=True)
    (commerce / "offers.json").write_text(
        json.dumps([{"record_id": "offer_1", "price": "150"}], ensure_ascii=False), encoding="utf-8"
    )
    (commerce / "decisions.json").write_text(
        json.dumps([{"record_id": "dec_1"}], ensure_ascii=False), encoding="utf-8"
    )


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "live"
        _seed_commerce(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_verify_roundtrip(self):
        result = create_backup(self.data, self.root / "backups", label="test")
        self.assertEqual(result["file_count"], 2)
        verification = verify_backup(Path(result["backup_dir"]))
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["checked"], 2)

    def test_tampered_backup_detected(self):
        result = create_backup(self.data, self.root / "backups")
        backup_dir = Path(result["backup_dir"])
        offers = backup_dir / "offers.json"
        offers.write_text('[{"record_id": "offer_1", "price": "999"}]', encoding="utf-8")
        verification = verify_backup(backup_dir)
        self.assertFalse(verification["ok"])
        self.assertTrue(any("offers.json" in m for m in verification["mismatches"]))

    def test_missing_file_detected(self):
        result = create_backup(self.data, self.root / "backups")
        (Path(result["backup_dir"]) / "decisions.json").unlink()
        verification = verify_backup(Path(result["backup_dir"]))
        self.assertFalse(verification["ok"])
        self.assertTrue(any("missing" in m for m in verification["mismatches"]))

    def test_restore_rehearsal_verifies_without_touching_live(self):
        result = create_backup(self.data, self.root / "backups")
        live_hash_before = (self.data / "commerce" / "offers.json").read_bytes()
        drill = restore_rehearsal(Path(result["backup_dir"]), self.root / "scratch")
        self.assertEqual(drill["restored_files"], 2)
        self.assertTrue(drill["verification"]["ok"])
        # live data untouched by the drill
        self.assertEqual((self.data / "commerce" / "offers.json").read_bytes(), live_hash_before)
        # restored copy is usable JSON
        restored = json.loads((self.root / "scratch" / "commerce" / "offers.json").read_text(encoding="utf-8"))
        self.assertEqual(restored[0]["record_id"], "offer_1")

    def test_backup_refuses_when_nothing_to_backup(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(FileNotFoundError):
                create_backup(Path(empty) / "nope", Path(empty) / "backups")


if __name__ == "__main__":
    unittest.main()
