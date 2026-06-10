"""Tests for trial run module."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xianyu_agent.trial_run import TrialRun, save_trial_state, load_trial_state


class TrialRunTests(unittest.TestCase):
    def test_init_creates_steps(self):
        trial = TrialRun(category="商用厨具")
        self.assertEqual(len(trial.steps), 7)
        self.assertEqual(trial.category, "商用厨具")

    def test_complete_step(self):
        trial = TrialRun()
        trial.complete_step(1, {"candidates_found": 5})
        self.assertEqual(trial.steps[0]["status"], "completed")
        self.assertEqual(trial.steps[0]["output"]["candidates_found"], 5)

    def test_get_progress(self):
        trial = TrialRun()
        progress = trial.get_progress()
        self.assertEqual(progress["progress"], "0/7")
        self.assertEqual(progress["current_step"], "discovery")

    def test_progress_after_completion(self):
        trial = TrialRun()
        trial.complete_step(1)
        trial.complete_step(2)
        progress = trial.get_progress()
        self.assertEqual(progress["progress"], "2/7")
        self.assertEqual(progress["current_step"], "listing_drafts")

    def test_generate_report(self):
        trial = TrialRun(category="商用厨具")
        candidates = [
            {"candidate_id": "rc_001", "title": "不锈钢操作台", "category": "商用厨具",
             "status": "approved", "manual_decision": "approve",
             "scoring": {"total_score": 30, "recommendation": "test"}},
        ]
        orders = [
            {"order_id": "ro_001", "status": "completed", "actual_profit": 200.0},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = trial.generate_report(candidates, orders, Path(tmpdir))
            self.assertTrue(result["ok"])
            self.assertIn("file", result)
            self.assertEqual(result["stats"]["candidates"], 1)
            self.assertEqual(result["stats"]["orders"], 1)
            self.assertEqual(result["stats"]["revenue"], 200.0)

            content = Path(result["file"]).read_text(encoding="utf-8")
            self.assertIn("试运行报告", content)
            self.assertIn("商用厨具", content)
            self.assertIn("不锈钢操作台", content)

    def test_save_and_load_state(self):
        trial = TrialRun(category="养殖设备", target_candidates=10)
        trial.complete_step(1, {"found": 8})

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            save_trial_state(path, trial)

            loaded = load_trial_state(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["category"], "养殖设备")
            self.assertEqual(loaded["target_candidates"], 10)
            self.assertEqual(loaded["steps"][0]["status"], "completed")

    def test_load_nonexistent_returns_none(self):
        result = load_trial_state(Path("/nonexistent/path"))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
