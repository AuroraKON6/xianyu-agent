"""Offline independent probes; failures preserve the unmodified GLM behavior."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


class BoundaryProbes(unittest.TestCase):
    def setUp(self):
        import test_intake_confirmation as fixtures

        self.fixture_module = fixtures
        self.fixture = fixtures.IntakeConfirmationServiceTests(
            "test_a10_archive_roundtrip_and_restart"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_batch_revocation_invalidates_active_confirmation(self):
        fixture = self.fixture
        preview = fixture._preview()
        fixture._confirm(preview, uses=("local_analysis", "external_model"))
        fixture.stack.imports.revoke_batch(
            fixture.batch["record_id"], operator="fixture", reason="isolated supplier batch revoked"
        )
        state = fixture.service.eligibility(
            fixture.snapshot_a["record_id"], use="external_model", now=self.fixture_module.NOW
        )
        self.assertFalse(state["eligible"], "revoked batch remains eligible: " + json.dumps(state))

    def test_snapshot_change_after_preview_invalidates_binding(self):
        fixture = self.fixture
        preview = fixture._preview()

        def change(row):
            row["eligible_price"]["amount"] = "98765.43"
            row["revision"] = int(row.get("revision", 1)) + 1

        fixture.stack.repos.offers._store.update(fixture.snapshot_a["record_id"], change)
        try:
            fixture._confirm(preview)
        except Exception as exc:
            from xianyu_agent.intake_confirmation import IntakeConfirmationError

            if isinstance(exc, IntakeConfirmationError):
                return
            raise
        state = fixture.service.eligibility(
            fixture.snapshot_a["record_id"], use="local_analysis", now=self.fixture_module.NOW
        )
        self.assertFalse(state["eligible"], "changed snapshot still uses old confirmation: " + json.dumps(state))

    def test_job_token_cannot_read_unapproved_quote_amount(self):
        from fastapi.testclient import TestClient
        from xianyu_agent import server
        from xianyu_agent.pi_jobs import JOB_TOKEN_HEADER

        fixture = self.fixture
        sid = fixture.snapshot_a["record_id"]
        state = fixture.service.eligibility(sid, use="external_model", now=self.fixture_module.NOW)
        self.assertFalse(state["eligible"])
        token = server.pi_job_gate.issue("codex-fixture-read", max_tool_calls=2)
        self.addCleanup(server.pi_job_gate.release, token)
        with patch.object(server, "commerce_repositories", fixture.stack.repos):
            client = TestClient(server.app)
            try:
                response = client.get("/commerce/offers", headers={JOB_TOKEN_HEADER: token})
            finally:
                client.close()
        if response.status_code == 403:
            return
        self.assertEqual(response.status_code, 200)
        exposed = [row for row in response.json().get("offers", []) if row.get("record_id") == sid]
        price = exposed[0].get("eligible_price", {}) if exposed else {}
        self.assertNotEqual(price.get("state"), "known", "job GET exposed unapproved quote: " + json.dumps(price))

    def _priced_opportunity(self):
        from datetime import timedelta
        from decimal import Decimal
        from xianyu_agent.commerce.contracts import Money, build_offer_snapshot, build_opportunity, build_product_match
        from xianyu_agent.opportunity_service import OpportunityService

        fixture = self.fixture
        repos = fixture.stack.repos
        now = self.fixture_module.NOW
        market, _ = repos.offers.add(build_offer_snapshot(
            provider_id="market:xianyu_observation", source_item_id="codex-fixture-market",
            source_sku_id="SKU-A", observed_at=now, fetched_at=now,
            expires_at=now + timedelta(hours=72), unit="件", quantity=None,
            condition="全新", attributes={"title": "isolated sample"}, price_type="display",
            eligible_price=Money.known(Decimal("300")).to_dict(),
            shipping_quote=Money.known(Decimal("0")).to_dict(), stock_state="unknown",
            stock_quantity=None, moq=None, tier_prices=[], tax_mode="unknown", seller_ref="",
            payload_hash="fixture:codex-market", account_id="", source_ref="observation:fixture",
        ))
        match = repos.matches.add(build_product_match(
            left_snapshot_id=market["record_id"], right_snapshot_id=fixture.snapshot_a["record_id"],
            grade="EXACT", rule_version="fixture", explanation="synthetic exact pair",
            source_ref="matching:fixture",
        ))
        opportunity = repos.opportunities.create(build_opportunity(
            canonical_sku="SKU-A", source=fixture.snapshot_a["provider_id"], title="isolated",
            match_id=match["record_id"], offer_snapshot_ids=[fixture.snapshot_a["record_id"]],
            market_snapshot_id=market["record_id"],
        ))
        money = lambda value: Money.known(Decimal(value)).to_dict()
        with patch("xianyu_agent.opportunity_service.utc_now", return_value=now):
            OpportunityService(repos).evaluate(
                opportunity["record_id"], packaging=money("5"), expected_return_loss=money("12"),
                attributable_costs=money("4"), platform_fee_rate="0.03", platform_fee_fixed="0",
                quantity=2,
            )
        return repos.opportunities.get(opportunity["record_id"])

    def _runner(self):
        from xianyu_agent.agent_jobs import AgentJobStore
        from xianyu_agent.pi_jobs import PiTaskRunner

        fixture = self.fixture
        return PiTaskRunner(
            AgentJobStore(fixture.tmp), fixture.stack.repos, data_dir=fixture.tmp,
            import_service=fixture.stack.imports,
            outbound_gate=lambda _: {"eligible": False, "reasons": ["no_active_confirmation"]},
        )

    def test_missing_supplier_ref_does_not_disable_pricing_gate(self):
        opportunity = self._priced_opportunity()

        def change(row):
            row["offer_snapshot_ids"] = ["missing-supplier-snapshot"]

        self.fixture.stack.repos.opportunities._store.update(opportunity["record_id"], change)
        row = self._runner()._collect_pricing_inputs()["opportunities"][0]
        self.assertTrue(row["material_withheld"], "missing reference fails open: " + json.dumps(row, ensure_ascii=True))

    def test_screening_reason_does_not_export_unapproved_profit(self):
        opportunity = self._priced_opportunity()
        self.assertIn("conservative profit", opportunity["status_reason"])
        row = self._runner()._collect_screening_inputs()["opportunities"][0]
        self.assertNotIn("conservative profit", row.get("status_reason", ""),
                         "screening exports derived money: " + json.dumps(row, ensure_ascii=True))

    def test_mcp_probe_reports_failure_with_nonzero_exit(self):
        import importlib.util

        path = ROOT / "research" / "real-source-demo-2026-10-12" / "mcp_probe.py"
        spec = importlib.util.spec_from_file_location("codex_probe_fixture", str(path))
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)

        class FailingClient:
            def initialize(self):
                raise RuntimeError("isolated fake MCP failure")

            def close(self):
                pass

        with tempfile.TemporaryDirectory(prefix="codex-mcp-exit-") as directory:
            probe.__file__ = str(Path(directory) / "mcp_probe.py")
            with patch.object(probe, "McpStdioClient", FailingClient):
                code = probe.main()
            recorded = json.loads((Path(directory) / "mcp_probe_result.json").read_text(encoding="utf-8"))
        self.assertIn("error", recorded)
        self.assertNotEqual(code, 0, "MCP failure incorrectly returns exit 0")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="all")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="codex-app-review-") as directory:
        os.environ.update({
            "DATA_DIR": str(Path(directory) / "data"), "DB_PATH": str(Path(directory) / "db.sqlite"),
            "XIANYU_ADAPTER": "stub", "SHADOW_OBSERVER_ENABLED": "0", "RADAR_DISPATCHER_ENABLED": "0",
            "AUTO_REPLY_ENABLED": "0", "MARKET_CHANNEL_PROBE_AUTHORIZED": "0",
            "DEEPSEEK_API_KEY": "", "MIMO_API_KEY": "",
        })
        loader = unittest.TestLoader()
        names = loader.getTestCaseNames(BoundaryProbes)
        if args.case != "all":
            names = [name for name in names if name == args.case]
            if not names:
                parser.error("unknown case: " + args.case)
        suite = unittest.TestSuite(BoundaryProbes(name) for name in names)
        try:
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            server = sys.modules.get("xianyu_agent.server")
            if server is not None:
                server.pi_task_runner.stop()
                server.pi_runtime.stop()
                server.db_manager.close()


if __name__ == "__main__":
    sys.exit(main())
