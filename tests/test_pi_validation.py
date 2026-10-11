import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xianyu_agent.pi_validation import (
    ROOT, build_worker_env, check_identity, run_validation, validate_run_root,
)


class ValidationIsolationTests(unittest.TestCase):
    def test_live_root_descendant_and_ancestor_are_refused(self):
        for path in (ROOT / "data" / "live", ROOT / "data" / "live" / "demo", ROOT / "data"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_run_root(path)

    def test_existing_empty_directory_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                validate_run_root(Path(directory))

    def test_real_mode_requires_explicit_enable_before_creating_files(self):
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.Popen") as start:
            root = Path(directory) / "new-run"
            with self.assertRaisesRegex(ValueError, "allow-model"):
                run_validation(root, mode="real", allow_model=False)
            start.assert_not_called()
            self.assertFalse(root.exists())

    def test_environment_isolated_and_offline_model_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = build_worker_env(root, "run-test", "offline", {
                "DATA_DIR": str(ROOT / "data" / "live"), "DB_PATH": "live.db",
                "DEEPSEEK_API_KEY": "must-not-be-used", "SHADOW_OBSERVER_ENABLED": "1",
                "PI_USAGE_RECONCILE_TOKEN_SHA256": "must-not-propagate",
            })
            self.assertEqual(Path(env["DATA_DIR"]), root / "data")
            self.assertEqual(Path(env["DB_PATH"]), root / "data" / "validation.db")
            self.assertEqual(env["PI_ENABLED"], "0")
            self.assertEqual(env["DEEPSEEK_API_KEY"], "")
            self.assertEqual(env["SHADOW_OBSERVER_ENABLED"], "0")
            self.assertEqual(env["PI_USAGE_RECONCILE_TOKEN_SHA256"], "")

    def test_identity_mismatch_refused(self):
        expected = {"run_id": "own", "pid": 42, "data_dir": "isolated", "session_id": "s", "mode": "offline"}
        for key in expected:
            changed = dict(expected)
            changed[key] = "other"
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                check_identity(changed, expected)

    def test_offline_owned_server_demo_chain_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            report = run_validation(root, mode="offline", allow_model=False)
            self.assertTrue(report["passed"], report)
            self.assertEqual(report["mode"], "offline")
            self.assertEqual(report["real_model_calls"], 0)
            self.assertEqual(report["quote_count"], 2)
            self.assertTrue(report["live_unchanged"])
            self.assertEqual(report["gate_probe"], {"read": 200, "write": 403, "unlisted": 403})
            self.assertFalse(report["forced_shutdown"])
            saved = json.loads((root / "validation.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, report)
            self.assertEqual(saved["submitted_job_id"], saved["outcome"]["job_id"])
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", saved["server_port"])), 0)


if __name__ == "__main__":
    unittest.main()
