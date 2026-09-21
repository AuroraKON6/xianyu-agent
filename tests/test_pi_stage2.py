"""Stage-2 tests: new roles (mock) and shadow observer."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from xianyu_agent import server
from xianyu_agent.agent_jobs import JOB_SUCCEEDED
from xianyu_agent.budget_reservations import ShadowAuthorizationLog
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.commerce.contracts import build_opportunity, build_product_match
from xianyu_agent.operating_mandates import OperatingMandateStore
from xianyu_agent.pi_jobs import (
    PiTaskRunner,
    validate_match_audit_output,
    validate_pricing_review_output,
    validate_screening_output,
)
from xianyu_agent.price_commitments import PriceCommitmentStore, ProductFactsStore
from xianyu_agent.reply_drafts import DraftReplyService
from xianyu_agent.shadow_observer import ShadowObserver, start_background_observer

UTC = timezone.utc


class FakePi:
    def __init__(self, message):
        self.message = message

    def prompt(self, message, context=None):
        return {
            "ok": True,
            "result": {
                "message": self.message,
                "session_id": "s",
                "provider": "deepseek",
                "model": "deepseek-flash",
                "turns": 1,
                "tool_calls": [],
                "usage": {"input": 10, "output": 5, "totalTokens": 15},
            },
        }


MATCH_AUDIT_OUT = '{"audits": [{"match_id": "match_1", "verdict": "investigate", "note": "尺寸未知"}], "summary": "1 条待核实"}'
PRICING_OUT = '{"reviews": [{"opportunity_id": "opp_1", "verdict": "hold", "suggested_list_price": null, "note": "运费未知"}], "summary": "1 个 hold"}'
SCREENING_OUT = '{"screenings": [{"opportunity_id": "opp_1", "verdict": "watch", "reason": "缺供应商确认"}], "summary": "1 个观察"}'


def make_runner(tmp):
    root = Path(tmp)
    repos = JsonCommerceRepositories(root)
    runner = PiTaskRunner(__import__("xianyu_agent.agent_jobs", fromlist=["AgentJobStore"]).AgentJobStore(root), repos)
    runner.start()
    return runner, repos


class ValidatorUnitTests(unittest.TestCase):
    def test_match_audit_valid_and_invalid(self):
        payload = validate_match_audit_output(MATCH_AUDIT_OUT)
        self.assertEqual(payload["audits"][0]["verdict"], "investigate")
        with self.assertRaises(ValueError):
            validate_match_audit_output('{"audits": [{"match_id": "m", "verdict": "maybe"}], "summary": "x"}')

    def test_pricing_review_valid_and_invalid(self):
        payload = validate_pricing_review_output(PRICING_OUT)
        self.assertEqual(payload["reviews"][0]["verdict"], "hold")
        with self.assertRaises(ValueError):
            validate_pricing_review_output('{"reviews": [{"opportunity_id": "o"}], "summary": "x"}')

    def test_screening_valid_and_invalid(self):
        payload = validate_screening_output(SCREENING_OUT)
        self.assertEqual(payload["screenings"][0]["verdict"], "watch")
        with self.assertRaises(ValueError):
            validate_screening_output('{"screenings": [{"opportunity_id": "o", "verdict": "maybe"}], "summary": "x"}')


class NewRoleExecutionTests(unittest.TestCase):
    def _run_role(self, role, output, seed):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            seed(repos)
            job = runner.submit_job(role)
            outcome = runner.run_once(pi_factory=lambda env: FakePi(output))
            return outcome, runner, repos, job

    def test_match_audit_end_to_end(self):
        def seed(repos):
            repos.matches.add(
                build_product_match(
                    left_snapshot_id="a", right_snapshot_id="b",
                    grade="UNKNOWN", rule_version="hard-constraints-v1",
                )
            )

        outcome, runner, repos, job = self._run_role("match_audit", MATCH_AUDIT_OUT, seed)
        self.assertEqual(outcome["outcome"], "succeeded")
        decision = repos.decisions.list()[0]
        self.assertEqual(decision["decision_type"], "match_audit")
        self.assertIn("investigate", " ".join(decision["unknowns"]))

    def test_pricing_review_end_to_end(self):
        def seed(repos):
            opportunity = repos.opportunities.create(
                build_opportunity(canonical_sku="SKU-1", source="s")
            )
            repos.opportunities.attach(
                opportunity["record_id"],
                field="economics",
                value={"scenarios": {"conservative": {"contribution_profit": {"state": "unknown"}}, "base": {}}, "cash_occupied": {"state": "known", "amount": "330", "currency": "CNY"}},
            )

        outcome, runner, repos, job = self._run_role("pricing_review", PRICING_OUT, seed)
        self.assertEqual(outcome["outcome"], "succeeded")
        decision = repos.decisions.list()[0]
        self.assertEqual(decision["decision_type"], "pricing_review")

    def test_opportunity_screening_end_to_end(self):
        def seed(repos):
            repos.opportunities.create(build_opportunity(canonical_sku="SKU-1", source="s"))

        outcome, runner, repos, job = self._run_role("opportunity_screening", SCREENING_OUT, seed)
        self.assertEqual(outcome["outcome"], "succeeded")
        decision = repos.decisions.list()[0]
        self.assertEqual(decision["decision_type"], "opportunity_screening")

    def test_unknown_role_rejected_at_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            with self.assertRaises(ValueError):
                runner.submit_job("price_police")

    def test_empty_inputs_produce_empty_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, repos = make_runner(tmp)
            runner.submit_job("match_audit")
            outcome = runner.run_once(
                pi_factory=lambda env: FakePi('{"audits": [], "summary": "没有匹配记录"}')
            )
            self.assertEqual(outcome["outcome"], "succeeded")
            decision = repos.decisions.list()[0]
            self.assertIn("no match records", decision["recommendation"])


class ShadowObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repos = JsonCommerceRepositories(self.root)
        self.facts = ProductFactsStore(self.root / "facts.json")
        self.commitments = PriceCommitmentStore(self.root / "commitments.json")
        self.drafts = DraftReplyService(self.root, self.facts, self.commitments)
        self.mandates = OperatingMandateStore(self.root)
        self.shadow_log = ShadowAuthorizationLog(self.root)
        self.observer = ShadowObserver(
            self.root,
            draft_service=self.drafts,
            opportunities=self.repos.opportunities,
            mandates=self.mandates,
            shadow_log=self.shadow_log,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_qualified_opportunity(self):
        opportunity = self.repos.opportunities.create(
            build_opportunity(canonical_sku="GZ-1", source="s")
        )
        self.repos.opportunities.transition(
            opportunity["record_id"], to_status="researching", reason="seed"
        )
        self.repos.opportunities.transition(
            opportunity["record_id"], to_status="ready_for_test", reason="seed"
        )
        return opportunity

    def test_draft_and_opportunity_produce_shadow_records(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0)
        self.drafts.draft_reply(
            cookie_id="acc-1", chat_id="c1", item_id="item-1", buyer_text="200卖吗", floor=180.0
        )
        self._seed_qualified_opportunity()

        result = self.observer.run_tick()
        self.assertEqual(result["reply_drafts"], 1)
        self.assertEqual(result["qualified_opportunities"], 1)

        metrics = self.shadow_log.metrics()
        self.assertEqual(metrics["total"], 2)
        self.assertEqual(metrics["allowed"], 0)  # no mandates -> all denied honestly

    def test_idempotent_ticks_do_not_duplicate(self):
        self.facts.upsert(cookie_id="acc-1", item_id="item-1", list_price=299.0, stock_available=3)
        self.drafts.draft_reply(
            cookie_id="acc-1", chat_id="c1", item_id="item-1", buyer_text="还有货吗"
        )
        first = self.observer.run_tick()
        second = self.observer.run_tick()
        self.assertEqual((first["reply_drafts"], second["reply_drafts"]), (1, 0))
        self.assertEqual(self.shadow_log.metrics()["total"], 1)

    def test_escalate_and_takeover_drafts_ignored(self):
        self.drafts.draft_reply(
            cookie_id="acc-1", chat_id="c1", item_id="item-1", buyer_text="我不要了，退款吧"
        )
        result = self.observer.run_tick()
        self.assertEqual(result["reply_drafts"], 0)
        self.assertEqual(self.shadow_log.metrics()["total"], 0)

    def test_background_thread_disabled(self):
        thread = start_background_observer(self.observer, interval_seconds=60, enabled=False)
        self.assertIsNone(thread)

    def test_background_thread_runs_ticks(self):
        import time

        thread = start_background_observer(self.observer, interval_seconds=0.1, enabled=True)
        self.assertIsNotNone(thread)
        try:
            self._seed_qualified_opportunity()
            deadline = time.time() + 5
            while time.time() < deadline:
                if self.shadow_log.metrics()["total"] >= 1:
                    break
                time.sleep(0.1)
            self.assertGreaterEqual(self.shadow_log.metrics()["total"], 1)
        finally:
            thread.stop_event.set()
            thread.join(timeout=5)


class ShadowApiTests(unittest.TestCase):
    def test_tick_and_status_endpoints(self):
        client = TestClient(server.app)
        tick = client.post("/commerce/shadow-observer/tick")
        self.assertEqual(tick.status_code, 200, tick.text)
        status = client.get("/commerce/shadow-observer/status")
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertIn("background_thread", body)
        self.assertIn("total", body["metrics"])

    def test_submit_unknown_role_400(self):
        client = TestClient(server.app)
        response = client.post("/commerce/pi-tasks/submit", json={"role": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported role", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
