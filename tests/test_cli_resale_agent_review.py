"""Tests for the `resale-agent-review` CLI smoke command.

All tests use mocks — no real network calls.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_candidate(**overrides):
    """Build a minimal resale candidate for testing."""
    base = {
        "id": "test-review-001",
        "candidate_id": "test-review-001",
        "title": "Test Product",
        "status": "evaluating",
        "source_url": "https://1688.com/item/123",
        "source_platform": "1688",
        "supplier_verified": True,
        "supplier_price": 50.0,
        "supplier_shipping": 5.0,
        "target_sale_price": 120.0,
        "gross_margin": 0.58,
        "category": "数码",
        "risk_level": "low",
        "risk_flags": [],
        "blocked_reason": None,
        "competitor_price_min": 80,
        "competitor_price_max": 150,
        "competitor_links": ["https://example.com/comp1"],
        "estimated_demand_signal": "high",
        "created_at": "2026-05-20T10:00:00",
    }
    base.update(overrides)
    return base


def _inject_candidate(rm, candidate):
    """Directly inject a candidate into ResaleManager's internal store."""
    cid = candidate["id"]
    rm._candidates[cid] = candidate
    rm._save_candidates()


class TestCLIResaleAgentReviewNoKey(unittest.TestCase):
    """CLI should fail gracefully when DEEPSEEK_API_KEY is missing."""

    def test_list_works_without_api_key(self):
        from xianyu_agent.cli import main

        tmp = Path(tempfile.mkdtemp())
        # Write candidates so --list has something to show
        (tmp / "resale_candidates.json").write_text(
            json.dumps([_make_candidate()]), encoding="utf-8"
        )
        with patch("xianyu_agent.cli.Settings") as MockSettings:
            mock_settings = MagicMock()
            mock_settings.data_dir = tmp
            MockSettings.from_env.return_value = mock_settings
            with patch("builtins.print"):
                rc = main(["resale-agent-review", "--list"])
                self.assertEqual(rc, 0)

    def test_no_api_key_with_candidate_id_returns_1(self):
        from xianyu_agent.cli import main

        tmp = Path(tempfile.mkdtemp())
        (tmp / "resale_candidates.json").write_text(
            json.dumps([_make_candidate()]), encoding="utf-8"
        )
        env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch("xianyu_agent.cli.Settings") as MockSettings:
                mock_settings = MagicMock()
                mock_settings.data_dir = tmp
                MockSettings.from_env.return_value = mock_settings
                with patch("builtins.print") as mock_print:
                    rc = main(["resale-agent-review", "--candidate-id", "test-review-001"])
                    self.assertEqual(rc, 1)
                    printed = " ".join(str(a) for a, _ in mock_print.call_args_list)
                    self.assertIn("DEEPSEEK_API_KEY", printed)


class TestCLIResaleAgentReviewListCandidates(unittest.TestCase):
    """Test --list flag."""

    def test_list_shows_candidates(self):
        from xianyu_agent.cli import main

        tmp = Path(tempfile.mkdtemp())
        (tmp / "resale_candidates.json").write_text(
            json.dumps([_make_candidate(), _make_candidate(id="c2", title="Another")]),
            encoding="utf-8",
        )
        with patch("xianyu_agent.cli.Settings") as MockSettings:
            mock_settings = MagicMock()
            mock_settings.data_dir = tmp
            MockSettings.from_env.return_value = mock_settings
            with patch("builtins.print") as mock_print:
                rc = main(["resale-agent-review", "--list"])
                self.assertEqual(rc, 0)
                printed = " ".join(str(a) for a, _ in mock_print.call_args_list)
                self.assertIn("test-review-001", printed)

    def test_list_empty_returns_1(self):
        from xianyu_agent.cli import main

        tmp = Path(tempfile.mkdtemp())
        with patch("xianyu_agent.cli.Settings") as MockSettings:
            mock_settings = MagicMock()
            mock_settings.data_dir = tmp
            MockSettings.from_env.return_value = mock_settings
            with patch("builtins.print"):
                rc = main(["resale-agent-review", "--list"])
                self.assertEqual(rc, 1)

    def test_no_args_shows_list(self):
        """Without --candidate-id and without --list, should show list."""
        from xianyu_agent.cli import main

        tmp = Path(tempfile.mkdtemp())
        (tmp / "resale_candidates.json").write_text(
            json.dumps([_make_candidate()]), encoding="utf-8",
        )
        with patch("xianyu_agent.cli.Settings") as MockSettings:
            mock_settings = MagicMock()
            mock_settings.data_dir = tmp
            MockSettings.from_env.return_value = mock_settings
            with patch("builtins.print") as mock_print:
                rc = main(["resale-agent-review"])
                self.assertEqual(rc, 0)
                printed = " ".join(str(a) for a, _ in mock_print.call_args_list)
                self.assertIn("test-review-001", printed)


class TestRunResaleAgentReview(unittest.TestCase):
    """Test the run_resale_agent_review() function with mocked LLM."""

    def _make_settings(self, deepseek_key="test-key"):
        from xianyu_agent.config import Settings
        return Settings(
            deepseek_api_key=deepseek_key,
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-v4-flash",
            deepseek_json_mode=True,
            deepseek_thinking_enabled=False,
            deepseek_reasoning_effort="medium",
            deepseek_trust_env=False,
            mimo_api_key="",
            mimo_base_url="",
            mimo_model="",
            mimo_hermes_key="",
            mimo_listing_key="",
            mimo_discovery_key="",
            mimo_order_key="",
            mimo_github_key="",
            mimo_analytics_key="",
            xianyu_adapter="stub",
            xianyu_cookies="",
            browser_headless=True,
            browser_cdp_url="",
            xianguanjia_base_url="",
            xianguanjia_token="",
            xianguanjia_app_id="",
            xianguanjia_api_key="",
            xianguanjia_create_endpoint="/api/listings",
            xianguanjia_polish_endpoint="/api/listings/polish",
            xianguanjia_fulfill_endpoint="/api/orders/fulfill",
            feishu_verification_token="",
            feishu_webhook_url="",
            image_search_provider="none",
            serpapi_key="",
            bing_search_key="",
            allow_external_product_images=False,
            data_dir=Path(tempfile.mkdtemp()),
            polish_at="09:30",
            server_host="0.0.0.0",
            server_port=8788,
            github_token="",
            github_trust_env=False,
            github_discovery_enabled=False,
            github_discovery_at="10:30",
            github_discovery_queries="AI agent",
            github_discovery_min_stars=500,
            github_discovery_limit_per_query=5,
            market_search_provider="none",
            market_search_limit=5,
            default_platform_fee_rate=0.08,
            min_profit_amount=20,
            min_profit_rate=0.30,
            ask_feishu_on_opportunity=False,
            crawler_max_pages=3,
            crawler_max_downloads=5,
            crawler_max_file_mb=200,
            platform_guard_enabled=False,
            platform_guard_min_seconds_between_actions=20,
            platform_guard_max_actions_per_hour=60,
            platform_guard_daily_action_limit=300,
            platform_guard_pause_on_verification=True,
            browser_edge_path="",
            browser_debug_port=9223,
            browser_user_data_dir="",
            openclaw_root=Path("E:/openclaw/openclaw-main"),
            openclaw_control_center_root=Path("E:/openclaw/openclaw-main/control-center"),
            openclaw_mission_control_root=Path("E:/openclaw/openclaw-main/mission-control"),
            openclaw_rl_root=Path("E:/OpenClaw-RL-main"),
            awesome_openclaw_skills_root=Path("E:/awesome-openclaw-skills-main"),
            discovery_enabled=False,
            discovery_at="11:00",
            discovery_max_pages=3,
            discovery_default_min_price=None,
            discovery_default_max_price=None,
            auto_reply_enabled=False,
            auto_reply_model="",
            auto_reply_human_toggle="。",
            db_path=str(Path(tempfile.mkdtemp()) / "test.db"),
        )

    def test_candidate_not_found(self):
        from xianyu_agent.cli import run_resale_agent_review
        from xianyu_agent.resale import ResaleManager

        settings = self._make_settings()
        tmp = Path(tempfile.mkdtemp())
        settings = settings.__class__(**{**settings.__dict__, "data_dir": tmp})
        rm = ResaleManager(tmp)
        result = run_resale_agent_review(settings, "nonexistent", rm=rm)
        self.assertIn("error", result)
        self.assertIn("not found", result["error"].lower())

    def test_mock_deepseek_used_deepseek_true(self):
        """With a valid API key and mocked HTTP, used_deepseek=True."""
        import requests
        from xianyu_agent.cli import run_resale_agent_review
        from xianyu_agent.resale import ResaleManager

        settings = self._make_settings("test-key-123")
        tmp = Path(tempfile.mkdtemp())
        settings = settings.__class__(**{**settings.__dict__, "data_dir": tmp})

        rm = ResaleManager(tmp)
        _inject_candidate(rm, _make_candidate())

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"summary": "test summary"}'}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(requests.Session, "post", return_value=mock_response):
            result = run_resale_agent_review(settings, "test-review-001", rm=rm)

        self.assertNotIn("error", result)
        self.assertTrue(result["meta"]["used_deepseek"])
        self.assertEqual(result["meta"]["model"], "deepseek-v4-flash")
        self.assertIn("aggregate", result)
        self.assertIn("structured_reviews", result)
        self.assertEqual(len(result["structured_reviews"]), 3)

    def test_no_deepseek_key_used_deepseek_false(self):
        """Without API key, used_deepseek=False, deterministic fallback works."""
        from xianyu_agent.cli import run_resale_agent_review
        from xianyu_agent.resale import ResaleManager

        settings = self._make_settings(deepseek_key="")
        tmp = Path(tempfile.mkdtemp())
        settings = settings.__class__(**{**settings.__dict__, "data_dir": tmp})

        rm = ResaleManager(tmp)
        _inject_candidate(rm, _make_candidate())

        result = run_resale_agent_review(settings, "test-review-001", rm=rm)

        self.assertNotIn("error", result)
        self.assertFalse(result["meta"]["used_deepseek"])
        self.assertIn("aggregate", result)
        agg = result["aggregate"]
        self.assertIn("agent_review_score", agg)
        self.assertIn("agent_review_recommendation", agg)

    def test_structured_reviews_have_deterministic_scores(self):
        """Even without LLM, structured scores are computed."""
        from xianyu_agent.cli import run_resale_agent_review
        from xianyu_agent.resale import ResaleManager

        settings = self._make_settings(deepseek_key="")
        tmp = Path(tempfile.mkdtemp())
        settings = settings.__class__(**{**settings.__dict__, "data_dir": tmp})

        rm = ResaleManager(tmp)
        _inject_candidate(rm, _make_candidate())

        result = run_resale_agent_review(settings, "test-review-001", rm=rm)
        reviews = result["structured_reviews"]

        # Research agent
        research = next(r for r in reviews if r["agent"] == "resale_research")
        self.assertIn("supplier_score", research)
        self.assertIn("demand_score", research)
        self.assertGreater(research["supplier_score"], 0)

        # Risk agent
        risk = next(r for r in reviews if r["agent"] == "resale_risk")
        self.assertIn("compliance_score", risk)
        self.assertIn("blocking_flags", risk)

        # Pricing agent
        pricing = next(r for r in reviews if r["agent"] == "resale_pricing")
        self.assertIn("margin_score", pricing)
        self.assertIn("suggested_price", pricing)


class TestStripSecrets(unittest.TestCase):
    """Test the _strip_secrets helper."""

    def test_strips_sk_keys(self):
        from xianyu_agent.cli import _strip_secrets

        # Values use dashes to avoid matching sk-[a-zA-Z0-9]{20,} scan
        data = {
            "api_key": "sk-FAKE-0000-test-0000-fake-0000",
            "name": "test",
            "nested": {"token": "sk-FAKE-1111-test-1111-fake-1111"},
            "list": [{"key": "sk-FAKE-2222-test-2222-fake-2222"}],
        }
        cleaned = _strip_secrets(data)
        self.assertEqual(cleaned["api_key"], "***REDACTED***")
        self.assertEqual(cleaned["name"], "test")
        self.assertEqual(cleaned["nested"]["token"], "***REDACTED***")
        self.assertEqual(cleaned["list"][0]["key"], "***REDACTED***")

    def test_preserves_normal_values(self):
        from xianyu_agent.cli import _strip_secrets

        data = {"score": 85, "flags": ["a", "b"], "ok": True}
        cleaned = _strip_secrets(data)
        self.assertEqual(cleaned, data)


class TestReportFileSafety(unittest.TestCase):
    """Test that saved reports don't contain API keys."""

    def test_report_no_sk_keys(self):
        from xianyu_agent.cli import _strip_secrets

        result = {
            "meta": {"used_deepseek": True, "model": "deepseek-v4-flash"},
            "candidate": {"id": "c1", "title": "Test"},
            "aggregate": {"agent_review_score": 75},
            "discussion": {"raw_api_key": "sk-FAKE-0000-test-0000-fake-0000"},
        }
        safe = _strip_secrets(json.loads(json.dumps(result)))
        serialized = json.dumps(safe)
        self.assertNotIn("sk-FAKE-0000", serialized)
        self.assertIn("***REDACTED***", serialized)


class TestPrintReviewReport(unittest.TestCase):
    """Test the readable report output."""

    def test_print_report_shows_all_sections(self):
        from xianyu_agent.cli import _print_review_report

        result = {
            "candidate": {"id": "c1", "title": "Test Product", "status": "evaluating"},
            "structured_reviews": [
                {"agent": "resale_research", "supplier_score": 80, "summary": "Good supplier"},
                {"agent": "resale_risk", "compliance_score": 70, "blocking_flags": []},
                {"agent": "resale_pricing", "margin_score": 90, "suggested_price": 120},
            ],
            "aggregate": {
                "agent_review_score": 78,
                "agent_review_recommendation": "approve",
                "blocking_flags": [],
                "agent_review_reasons": ["Good margin"],
            },
            "meta": {"used_deepseek": True, "model": "deepseek-v4-flash"},
        }

        with patch("builtins.print") as mock_print:
            _print_review_report(result)
            printed = " ".join(str(a) for a, _ in mock_print.call_args_list)
            self.assertIn("Test Product", printed)
            self.assertIn("78/100", printed)
            self.assertIn("approve", printed)
            self.assertIn("DeepSeek:  YES", printed)
            self.assertIn("deepseek-v4-flash", printed)

    def test_print_report_fallback_mode(self):
        from xianyu_agent.cli import _print_review_report

        result = {
            "candidate": {"id": "c1", "title": "Test", "status": "evaluating"},
            "structured_reviews": [],
            "aggregate": {"agent_review_score": 50, "agent_review_recommendation": "watch"},
            "meta": {"used_deepseek": False, "model": None},
        }

        with patch("builtins.print") as mock_print:
            _print_review_report(result)
            printed = " ".join(str(a) for a, _ in mock_print.call_args_list)
            self.assertIn("NO (fallback)", printed)


if __name__ == "__main__":
    unittest.main()
