"""WP08 script tests: mock model client, separate from real runs.

The real-run evidence is produced by scripts/run_wp08_real_model_task.py
against the live provider; these tests only prove the job/decision
plumbing with a stubbed client (mock and real proofs are reported
separately).
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_wp08_real_model_task.py"


def load_script():
    spec = importlib.util.spec_from_file_location("run_wp08_real_model_task", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSettings:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.deepseek_model = "mock-model"
        self.mimo_api_key = ""


class FakeClient:
    def __init__(self, settings):
        self.settings = settings

    @property
    def enabled(self):
        return True

    def chat_json(self, system, user, temperature=0.3, max_tokens=500):
        return {"topic": "库存", "urgent": True, "reason": "现货紧张"}


class Wp08ScriptMockTests(unittest.TestCase):
    def test_mock_run_completes_job_and_decision(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "Settings") as mock_settings, patch.object(
                module, "DeepSeekClient", FakeClient
            ):
                mock_settings.from_env.return_value = FakeSettings(tmp)
                exit_code = module.main()
            self.assertEqual(exit_code, 0)

            import json

            jobs = json.load(open(Path(tmp) / "commerce" / "agent_jobs.json", encoding="utf-8"))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "succeeded")
            self.assertEqual(jobs[0]["role"], "supplier_note_triage")
            self.assertTrue(jobs[0]["result_ref"].startswith("decision:"))

            decisions = json.load(open(Path(tmp) / "commerce" / "decisions.json", encoding="utf-8"))
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["decision_type"], "supplier_note_triage")
            self.assertEqual(decisions[0]["model_version"], "mock-model")

    def test_disabled_provider_reports_failure_without_job(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "Settings") as mock_settings:

                class DisabledClient(FakeClient):
                    @property
                    def enabled(self):
                        return False

                mock_settings.from_env.return_value = FakeSettings(tmp)
                with patch.object(module, "DeepSeekClient", DisabledClient):
                    exit_code = module.main()
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
