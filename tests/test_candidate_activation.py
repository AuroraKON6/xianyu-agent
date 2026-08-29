from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from preflight_helpers import iso_now, make_preflight_ready_candidate
from xianyu_agent.candidate_activation import GATE_CODES, evaluate_candidate_activation
from xianyu_agent.candidate_evidence_store import parse_artifact_reference
from xianyu_agent.resale import ResaleManager


CHECKED_AT = "2026-08-25T09:00:00Z"


class FakeSettings:
    min_profit_amount = 20.0
    min_profit_rate = 0.30


def _manager_and_ready_candidate(checked_at=CHECKED_AT):
    root = Path(tempfile.mkdtemp())
    manager = ResaleManager(root / "resale")
    candidate = make_preflight_ready_candidate(manager, checked_at=checked_at)
    return root, manager, candidate


class CandidateActivationPureTests(unittest.TestCase):
    def test_ready_packet_has_stable_business_order(self):
        _, _, candidate = _manager_and_ready_candidate()

        packet = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )

        self.assertTrue(packet["ready"], packet)
        self.assertTrue(packet["evidence_ready"])
        self.assertEqual([gate["code"] for gate in packet["gates"]], list(GATE_CODES))
        self.assertEqual(packet["completion"], {"total": 12, "passed": 12, "remaining": 0})
        self.assertEqual(len(packet["evidence_fingerprint"]), 64)
        self.assertIsNone(packet["next_step"])

    def test_live_placeholder_candidate_is_not_commercial_evidence(self):
        manager = ResaleManager(Path(tempfile.mkdtemp()) / "resale")
        candidate = manager.create_candidate({
            "title": "T",
            "source_platform": "pdd",
            "supplier_price": 10,
            "target_sale_price": 30,
        })
        manager.verify_candidate(candidate["candidate_id"], {"supplier_verified": True})
        candidate = manager.get_candidate(candidate["candidate_id"])

        packet = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )

        self.assertFalse(packet["ready"])
        self.assertEqual(packet["next_step"]["code"], "product_identity")
        failed = set(packet["failed_gate_codes"])
        self.assertIn("supplier_source", failed)
        self.assertIn("supplier_quote_evidence", failed)
        self.assertIn("manual_decision_current", failed)

    def test_example_domains_and_generic_evidence_refs_are_rejected(self):
        _, _, candidate = _manager_and_ready_candidate()
        candidate.update({
            "source_url": "https://example.com/item/1",
            "supplier_quote_evidence_ref": "见截图",
            "images": ["https://example.com/photo.jpg"],
            "competitor_links": [
                "https://example.com/one",
                "https://example.com/two",
            ],
        })

        packet = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )

        failed = set(packet["failed_gate_codes"])
        self.assertIn("supplier_source", failed)
        self.assertIn("supplier_quote_evidence", failed)
        self.assertIn("image_assets", failed)
        self.assertIn("market_baseline", failed)

    def test_stale_and_future_observations_fail_closed(self):
        _, _, candidate = _manager_and_ready_candidate()
        candidate["supplier_quote_checked_at"] = "2026-08-24T08:59:59Z"
        candidate["stock_checked_at"] = "2026-08-25T09:01:00Z"
        candidate["competitor_checked_at"] = "2026-08-22T08:59:59Z"

        packet = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )

        failed = set(packet["failed_gate_codes"])
        self.assertIn("supplier_quote_evidence", failed)
        self.assertIn("supplier_stock_evidence", failed)
        self.assertIn("market_baseline", failed)

    def test_two_distinct_comparable_links_are_required(self):
        _, _, candidate = _manager_and_ready_candidate()
        candidate["competitor_links"] = [
            "https://www.goofish.com/item?id=one",
            "https://www.goofish.com/item?id=one",
        ]

        packet = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )

        gate = {item["code"]: item for item in packet["gates"]}["market_baseline"]
        self.assertEqual(gate["status"], "blocked")
        self.assertIn("links=1", gate["evidence"])

    def test_sensitive_text_cannot_be_used_as_evidence_reference(self):
        _, _, candidate = _manager_and_ready_candidate()
        candidate["supplier_quote_evidence_ref"] = "cookie=secret-value-in-chat"

        packet = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
        )

        gate = {item["code"]: item for item in packet["gates"]}["supplier_quote_evidence"]
        self.assertEqual(gate["status"], "blocked")
        self.assertIn("ref=invalid", gate["evidence"])
        self.assertNotIn("secret-value", json.dumps(packet, ensure_ascii=False))

    def test_strict_mode_requires_real_active_archives(self):
        _, manager, candidate = _manager_and_ready_candidate()

        ready = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
            evidence_artifacts=manager.evidence_store.artifact_lookup(candidate["candidate_id"]),
            require_archived_evidence=True,
        )
        candidate["supplier_quote_evidence_ref"] = "evidence/nonexistent-quote.png"
        fake = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
            evidence_artifacts=manager.evidence_store.artifact_lookup(candidate["candidate_id"]),
            require_archived_evidence=True,
        )

        self.assertTrue(ready["ready"], ready)
        quote_gate = {item["code"]: item for item in fake["gates"]}["supplier_quote_evidence"]
        self.assertEqual(quote_gate["status"], "blocked")
        self.assertIn("ref=archive_required", quote_gate["evidence"])

    def test_strict_mode_blocks_a_tampered_active_blob(self):
        _, manager, candidate = _manager_and_ready_candidate()
        artifact_id = parse_artifact_reference(candidate["supplier_quote_evidence_ref"])
        path, _ = manager.evidence_store.resolve_file(candidate["candidate_id"], artifact_id)
        content = bytearray(path.read_bytes())
        content[0] = ord("X") if content[0] != ord("X") else ord("Y")
        path.write_bytes(bytes(content))

        packet = evaluate_candidate_activation(
            candidate=candidate,
            settings=FakeSettings(),
            checked_at=CHECKED_AT,
            evidence_artifacts=manager.evidence_store.artifact_lookup(candidate["candidate_id"]),
            require_archived_evidence=True,
        )

        quote_gate = {item["code"]: item for item in packet["gates"]}["supplier_quote_evidence"]
        self.assertEqual(quote_gate["status"], "blocked")
        self.assertIn("ref=integrity_failed", quote_gate["evidence"])


class CandidateActivationRevisionTests(unittest.TestCase):
    def test_material_change_recomputes_economics_and_invalidates_decision(self):
        _, manager, candidate = _manager_and_ready_candidate()
        before_revision = candidate["evidence_revision"]

        updated = manager.update_candidate_activation(
            candidate["candidate_id"],
            {"supplier_price": 720.0, "supplier_quote_evidence_ref": "evidence/quote-revised.png"},
        )

        self.assertEqual(updated["evidence_revision"], before_revision + 1)
        self.assertIsNone(updated["manual_decision"])
        self.assertEqual(updated["status"], "evaluating")
        self.assertEqual(updated["supplier_price"], 720.0)
        self.assertLess(updated["economics"]["gross_profit"], candidate["economics"]["gross_profit"])
        self.assertIn("supplier_price", updated["manual_decision_invalidation_reason"])
        self.assertEqual(updated["approved_evidence_fingerprint"], "")

    def test_noop_update_preserves_current_manual_decision(self):
        _, manager, candidate = _manager_and_ready_candidate()

        updated = manager.update_candidate_activation(
            candidate["candidate_id"],
            {"supplier_name": candidate["supplier_name"]},
        )

        self.assertEqual(updated["evidence_revision"], candidate["evidence_revision"])
        self.assertEqual(updated["manual_decision"], "approve")
        self.assertEqual(
            updated["approved_evidence_fingerprint"],
            candidate["approved_evidence_fingerprint"],
        )

    def test_commercial_terms_reprice_invalidates_old_candidate_approval(self):
        _, manager, candidate = _manager_and_ready_candidate()

        updated = manager.update_candidate_economics_from_commercial_terms(
            candidate["candidate_id"],
            cookie_id="seller-a",
            category=candidate["category"],
            platform_fee_rate=0.05,
            platform_fixed_fee=1.0,
            snapshot_version="terms-v2",
            observed_at=CHECKED_AT,
            evidence_ref="evidence/current-category-terms.png",
            return_policy="seven_day",
        )

        self.assertIsNone(updated["manual_decision"])
        self.assertEqual(updated["status"], "evaluating")
        self.assertEqual(updated["manual_decision_invalidation_reason"], "account/category commercial terms changed")

    def test_existing_listing_draft_prevents_in_place_fact_rewrite(self):
        _, manager, candidate = _manager_and_ready_candidate()
        draft = manager.create_listing_draft(candidate["candidate_id"])
        self.assertNotIn("error", draft)

        with self.assertRaisesRegex(ValueError, "create a new candidate revision"):
            manager.update_candidate_activation(
                candidate["candidate_id"],
                {"supplier_sku": "OPS-NEW-REVISION"},
            )


class CandidateActivationRouteTests(unittest.TestCase):
    def setUp(self):
        from xianyu_agent import server

        self.server = server
        self.root, self.manager, self.candidate = _manager_and_ready_candidate(checked_at=None)
        self.client = TestClient(server.app)
        self.patch = patch.object(server, "resale_manager", self.manager)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_get_packet_is_read_only(self):
        candidates_file = self.root / "resale" / "resale_candidates.json"
        before = candidates_file.read_bytes()

        response = self.client.get(
            f"/resale/candidates/{self.candidate['candidate_id']}/activation-packet"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ready"])
        self.assertEqual(candidates_file.read_bytes(), before)

    def test_patch_returns_new_packet_and_explicit_decision_invalidation(self):
        response = self.client.patch(
            f"/resale/candidates/{self.candidate['candidate_id']}/activation-packet",
            json={
                "supplier_price": 730,
                "supplier_quote_evidence_ref": "evidence/quote-730-current.png",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["manual_decision_invalidated"])
        self.assertFalse(body["activation_packet"]["ready"])
        self.assertIn("manual_decision_current", body["activation_packet"]["failed_gate_codes"])
        self.assertIsNone(body["candidate"]["manual_decision"])

    def test_candidate_list_includes_compact_activation_summary(self):
        response = self.client.get("/resale/candidates?market_scope=domestic")

        self.assertEqual(response.status_code, 200, response.text)
        summary = response.json()[0]["activation_summary"]
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["completion"]["total"], 12)
        self.assertNotIn("evidence_fingerprint", summary)

    def test_unknown_candidate_returns_404(self):
        response = self.client.get("/resale/candidates/rc_missing/activation-packet")
        self.assertEqual(response.status_code, 404)

    def test_archive_metadata_and_download_are_candidate_bound(self):
        response = self.client.get(
            f"/resale/candidates/{self.candidate['candidate_id']}/evidence-artifacts"
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["count"], 5)
        artifact = body["artifacts"][0]
        self.assertNotIn("blob_key", artifact)
        self.assertNotIn(str(self.root), json.dumps(body, ensure_ascii=False))

        download = self.client.get(
            f"/resale/candidates/{self.candidate['candidate_id']}/evidence-artifacts/"
            f"{artifact['artifact_id']}/file"
        )
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.headers["cache-control"], "no-store")
        self.assertEqual(download.headers["x-content-type-options"], "nosniff")
        self.assertTrue(download.content)

        wrong_candidate = self.client.get(
            f"/resale/candidates/rc_other/evidence-artifacts/{artifact['artifact_id']}/file"
        )
        self.assertEqual(wrong_candidate.status_code, 404)

    def test_raw_upload_binds_canonical_ref_and_invalidates_old_decision(self):
        observed_at = iso_now()
        response = self.client.post(
            f"/resale/candidates/{self.candidate['candidate_id']}/evidence-artifacts",
            params={
                "kind": "supplier_quote",
                "observed_at": observed_at,
                "filename": "supplier-quote-recheck.txt",
                "note": "Exact SKU price and shipping rechecked in the supplier page.",
            },
            content=b"supplier quote revised to 700 shipping 80\n",
            headers={"Content-Type": "text/plain"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertTrue(body["manual_decision_invalidated"])
        self.assertEqual(body["artifact"]["status"], "active")
        self.assertEqual(body["artifact"]["kind"], "supplier_quote")
        self.assertEqual(
            body["candidate"]["supplier_quote_evidence_ref"],
            "artifact:" + body["artifact"]["artifact_id"],
        )
        self.assertIsNone(body["candidate"]["manual_decision"])
        self.assertIn("manual_decision_current", body["activation_packet"]["failed_gate_codes"])

        artifacts = self.manager.evidence_store.list_candidate_artifacts(
            self.candidate["candidate_id"]
        )
        quote_statuses = {
            item["artifact_id"]: item["status"]
            for item in artifacts
            if item["kind"] == "supplier_quote"
        }
        self.assertEqual(set(quote_statuses.values()), {"active", "superseded"})

    def test_upload_is_rejected_after_listing_draft_without_creating_artifact(self):
        draft = self.manager.create_listing_draft(self.candidate["candidate_id"])
        self.assertNotIn("error", draft)
        before = self.manager.evidence_store.list_candidate_artifacts(self.candidate["candidate_id"])

        response = self.client.post(
            f"/resale/candidates/{self.candidate['candidate_id']}/evidence-artifacts",
            params={
                "kind": "supplier_quote",
                "observed_at": iso_now(),
                "filename": "late-proof.txt",
            },
            content=b"late evidence must not rewrite approved draft\n",
            headers={"Content-Type": "text/plain"},
        )

        self.assertEqual(response.status_code, 409, response.text)
        after = self.manager.evidence_store.list_candidate_artifacts(self.candidate["candidate_id"])
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
