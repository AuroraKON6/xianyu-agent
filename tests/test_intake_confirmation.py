"""G1A intake use confirmation contract tests (acceptance A10-A19).

All inputs are synthetic isolated fixtures; no live data, real supplier,
real model call or platform action is touched. Failure injection for A18 is
in-process (monkeypatched OSError on the confirmation store persist step and
a fresh service reload), and process-level restart is simulated by
reconstructing every store from the same data directory.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quote_fixtures import declared_quote_csv

from xianyu_agent.candidate_evidence_store import CandidateEvidenceStore
from xianyu_agent.commerce.repositories import JsonCommerceRepositories
from xianyu_agent.intake_confirmation import (
    IntakeConfirmationError,
    IntakeConfirmationService,
    build_intake_confirmation_router,
)
from xianyu_agent.supplier_import import SupplierImportService
from xianyu_agent.supplier_intake import IntakeDeclaration, OfferEvidence

NOW = datetime(2026, 10, 11, 8, 0, 0, tzinfo=timezone.utc)
CSV_BODY = (
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
    "SKU-A,不锈钢工作台,150,件,2,10,包邮,120x60x85cm,众商,全新\n"
    "SKU-B,沥水篮,30,件,5,40,包邮,60x40x20cm,众商,全新\n"
).encode("utf-8")


def _offer_terms(**overrides):
    fields = {
        "currency": "CNY",
        "price_unit": "件",
        "quantity_unit": "件",
        "freight_basis": "per_unit",
        "freight_destination": "杭州仓自提点半径50公里",
        "tax_terms": "含税出厂价，测试假设",
        "delivery_terms": "48小时内出库，德邦物流",
        "after_sales_terms": "错货破损供应商承担往返运费",
        "stock_observed_at": (NOW - timedelta(hours=1)).isoformat(),
        "stock_valid_until": (NOW + timedelta(hours=12)).isoformat(),
        "evidence_ref": "fixture:row-evidence",
    }
    fields.update(overrides)
    return OfferEvidence(**fields)


def _declaration(batch, snapshots, *, issued=None, received=None, valid_until=None, **overrides):
    # issued <= observed(=NOW-30min) <= imported(=NOW-5min) <= moment(=NOW)
    issued = issued or NOW - timedelta(minutes=95)
    received = received or NOW - timedelta(minutes=50)
    valid_until = valid_until or NOW + timedelta(hours=20)
    fields = {
        "batch_id": str(batch["record_id"]),
        "content_sha256": str(batch["content_sha256"]),
        "supplier": str(batch["supplier"]),
        "source_kind": "supplier_original",
        "origin_ref": "isolated-fixture-reference",
        "supplier_identity_ref": "fixture:supplier-identity",
        "use_permission_ref": "fixture:use-permission",
        "local_review_permitted": True,
        "external_llm_permitted": True,
        "issued_at": issued.isoformat(),
        "received_at": received.isoformat(),
        "valid_until": valid_until.isoformat(),
        # R21: fully-ready fixtures declare all five supply entities
        "entities": {
            "quote_entity": str(batch["supplier"]),
            "promising_entity": str(batch["supplier"]),
            "collection_entity": str(batch["supplier"]),
            "shipping_entity": str(batch["supplier"]),
            "returns_entity": str(batch["supplier"]),
        },
        "offers": {s["record_id"]: _offer_terms() for s in snapshots},
    }
    fields.update(overrides)
    return IntakeDeclaration(**fields)


class _Stack:
    def __init__(self, root: Path):
        self.root = root
        self.repos = JsonCommerceRepositories(root)
        self.imports = SupplierImportService(self.repos, root)
        self.evidence = CandidateEvidenceStore(root / "candidate_evidence")

    def service(self):
        return IntakeConfirmationService(self.repos, self.imports, self.evidence, self.root)

    def import_batch(self, csv_body=None, observed=None):
        observed = observed or NOW
        payload = declared_quote_csv(csv_body or CSV_BODY, observed=observed)
        preview = self.imports.preview_bytes(payload, kind="csv", filename="quote.csv")
        result = self.imports.confirm(
            preview["preview_id"],
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="杭州五金供应商",
            operator="test",
        )
        return result["batch"], payload, result["snapshots"]


class IntakeConfirmationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="intake-confirmation-"))
        self.stack = _Stack(self.tmp)
        self.service = self.stack.service()
        # anchor the fixture clock to the real import moment: the import
        # service stamps fetched_at with the real clock, and every intake
        # check requires imported_at <= assessment moment
        global NOW
        imported_marker = datetime.now(timezone.utc)
        observed = imported_marker - timedelta(minutes=30)
        self.batch, self.quote_bytes, self.snapshots = self.stack.import_batch(observed=observed)
        NOW = imported_marker + timedelta(minutes=5)
        self.snapshot_a, self.snapshot_b = self.snapshots

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _preview(self, **overrides):
        declaration = _declaration(self.batch, self.snapshots, **overrides)
        return self.service.preview(
            str(self.batch["record_id"]),
            content=self.quote_bytes,
            filename="quote.csv",
            declaration=declaration,
            now=NOW,
        )

    def _confirm(self, preview, *, key="confirm-key-0001", uses=("local_analysis",), **overrides):
        fields = {
            "preview_id": preview["preview_id"],
            "preview_digest": preview["digest"],
            "idempotency_key": key,
            "allowed_uses": list(uses),
            "valid_until": (NOW + timedelta(hours=6)).isoformat(),
            "verification_note": "逐项核对了原始单元格与声明字段",
            "operator": "操作者甲",
            "now": NOW,
        }
        fields.update(overrides)
        return self.service.confirm(**fields)

    # ------------------------------------------------------------------- A10

    def test_a10_archive_roundtrip_and_restart(self):
        preview = self._preview()
        result = self._confirm(preview)
        record = result["confirmation"]

        # restart: rebuild every store from the same directory
        reloaded = _Stack(self.tmp).service()
        detail = reloaded.detail(record["record_id"])
        self.assertEqual(detail["status"], "confirmed")
        self.assertEqual(detail["original_sha256"], hashlib.sha256(self.quote_bytes).hexdigest())

        artifact = reloaded._evidence.get_owner_artifact(
            "supplier_batch", str(self.batch["record_id"]), detail["original_artifact_id"]
        )
        self.assertIsNotNone(artifact)
        path, meta = reloaded._evidence.resolve_owner_file(
            "supplier_batch", str(self.batch["record_id"]), detail["original_artifact_id"]
        )
        self.assertEqual(path.read_bytes(), self.quote_bytes)  # 原始字节不变
        self.assertEqual(meta["original_filename"], "quote.csv")

        # field mapping retained on the immutable snapshots
        attributes = self.snapshot_a["attributes"]
        self.assertEqual(attributes["quote_provenance"]["source_kind"], "supplier_original")
        self.assertEqual(attributes["import_basis"]["currency"], "CNY")
        self.assertEqual(self.snapshot_a["eligible_price"]["amount"], "150")

    def test_a11_owner_scoped_archive_without_fake_candidate(self):
        preview = self._preview()
        result = self._confirm(preview)
        record = result["confirmation"]
        artifacts = self.stack.evidence.list_owner_artifacts(
            "supplier_batch", str(self.batch["record_id"])
        )
        self.assertTrue(any(a["artifact_id"] == record["original_artifact_id"] for a in artifacts))
        for artifact in artifacts:
            self.assertEqual(artifact["owner_type"], "supplier_batch")
            self.assertEqual(artifact.get("candidate_id") or "", "")
        # candidate queries never see supplier artifacts; no candidate ledger appears
        self.assertEqual(self.stack.evidence.list_candidate_artifacts("cand_fixture"), [])
        self.assertFalse((self.tmp / "resale_candidates.json").exists())

    # ------------------------------------------------------------------- A12

    def test_a12_newer_preview_invalidates_old_binding(self):
        preview1 = self._preview()
        preview2 = self._preview(origin_ref="fixture:supplier-quote-revised")
        self.assertNotEqual(preview1["digest"], preview2["digest"])
        with self.assertRaises(IntakeConfirmationError) as ctx:
            self._confirm(preview1)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_a12_digest_mismatch_and_quote_deadline(self):
        preview = self._preview()
        with self.assertRaises(IntakeConfirmationError):
            self._confirm(preview, preview_digest="0" * 64)
        # quote deadline passes between preview and confirm (preview TTL widened
        # so the deadline itself, not the TTL, is what rejects the confirm)
        tight_service = IntakeConfirmationService(
            self.stack.repos, self.stack.imports, self.stack.evidence, self.tmp,
            preview_ttl_seconds=4 * 3600,
        )
        observed = NOW - timedelta(minutes=30)
        payload = declared_quote_csv(CSV_BODY, observed=observed)
        import csv as _csv
        import io as _io

        rows = list(_csv.DictReader(_io.StringIO(payload.decode("utf-8"))))
        for row in rows:
            row["valid_until"] = (NOW + timedelta(hours=1)).isoformat()
        buffer = _io.StringIO()
        writer = _csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        payload = buffer.getvalue().encode("utf-8")
        import_preview = self.stack.imports.preview_bytes(payload, kind="csv", filename="tight.csv")
        imported = self.stack.imports.confirm(
            import_preview["preview_id"],
            content_sha256=import_preview["content_sha256"],
            revision=import_preview["revision"],
            supplier="杭州五金供应商",
            operator="test",
        )
        tight_batch = imported["batch"]
        tight_snapshots = imported["snapshots"]
        declaration = _declaration(tight_batch, tight_snapshots)
        tight_preview = tight_service.preview(
            str(tight_batch["record_id"]),
            content=payload,
            filename="tight.csv",
            declaration=declaration,
            now=NOW,
        )
        with self.assertRaises(IntakeConfirmationError) as ctx:
            tight_service.confirm(
                preview_id=tight_preview["preview_id"],
                preview_digest=tight_preview["digest"],
                idempotency_key="deadline-key-0001",
                allowed_uses=["local_analysis"],
                valid_until=(NOW + timedelta(hours=5)).isoformat(),
                verification_note="核对了报价截止时间语义",
                operator="操作者甲",
                now=NOW + timedelta(minutes=90),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        # batch revoked after preview
        self.stack.imports.revoke_batch(str(self.batch["record_id"]), operator="test", reason="供应商撤回报价测试")
        with self.assertRaises(IntakeConfirmationError) as ctx:
            self._confirm(preview)
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------------------------- A13

    def test_a13_same_size_byte_flip_breaks_eligibility(self):
        preview = self._preview()
        record = self._confirm(preview)["confirmation"]
        blob_path, _ = self.stack.evidence.resolve_owner_file(
            "supplier_batch", str(self.batch["record_id"]), record["original_artifact_id"]
        )
        original = blob_path.read_bytes()
        flipped = bytearray(original)
        flipped[0] ^= 0x01  # same size, one byte changed
        blob_path.write_bytes(bytes(flipped))
        outcome = self.service.eligibility(
            self.snapshot_a["record_id"], use="local_analysis", now=NOW
        )
        self.assertFalse(outcome["eligible"])
        self.assertIn("original_file_modified", outcome["reasons"])

    # ------------------------------------------------------------------- A15

    def test_a15_partial_sku_use_and_account_isolation(self):
        declaration = _declaration(
            self.batch,
            self.snapshots,
            offers={self.snapshot_a["record_id"]: _offer_terms()},
        )
        preview = self.service.preview(
            str(self.batch["record_id"]),
            content=self.quote_bytes,
            filename="quote.csv",
            declaration=declaration,
            now=NOW,
        )
        self.assertEqual(preview["confirmable_snapshot_ids"], [self.snapshot_a["record_id"]])
        self._confirm(preview)

        ok = self.service.eligibility(self.snapshot_a["record_id"], use="local_analysis", now=NOW)
        self.assertTrue(ok["eligible"], ok["reasons"])

        other_sku = self.service.eligibility(self.snapshot_b["record_id"], use="local_analysis", now=NOW)
        self.assertFalse(other_sku["eligible"])
        self.assertIn("no_active_confirmation", other_sku["reasons"])

        wrong_use = self.service.eligibility(self.snapshot_a["record_id"], use="external_model", now=NOW)
        self.assertFalse(wrong_use["eligible"])
        self.assertIn("use_not_permitted:external_model", wrong_use["reasons"])

    def test_a15_business_use_requires_account_scope(self):
        preview = self._preview()
        record = self._confirm(
            preview,
            uses=("business_use",),
            account_scope_mode="list",
            account_scope_accounts=["acct-1"],
        )["confirmation"]
        ok = self.service.eligibility(
            self.snapshot_a["record_id"], use="business_use", account="acct-1", now=NOW
        )
        self.assertTrue(ok["eligible"], ok["reasons"])
        bad = self.service.eligibility(
            self.snapshot_a["record_id"], use="business_use", account="acct-2", now=NOW
        )
        self.assertFalse(bad["eligible"])
        self.assertIn("account_out_of_scope", bad["reasons"])

    # ------------------------------------------------------------------- A16

    def test_a16_replay_conflict_and_concurrency(self):
        preview = self._preview()
        first = self._confirm(preview, key="replay-key-0001")
        second = self._confirm(preview, key="replay-key-0001")  # preview consumed, replay path
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["confirmation"]["record_id"], second["confirmation"]["record_id"])
        self.assertEqual(len(self.service.list_confirmations()), 1)

        # same key with a different payload is a conflict
        preview_b = self._preview()
        with self.assertRaises(IntakeConfirmationError) as ctx:
            self._confirm(
                preview_b,
                key="replay-key-0001",
                valid_until=(NOW + timedelta(hours=2)).isoformat(),
            )
        self.assertEqual(ctx.exception.status_code, 409)

        # concurrent confirms with distinct keys both land exactly once
        preview_c = self._preview()
        results = []
        errors = []

        def worker(name):
            try:
                results.append(
                    self._confirm(preview_c, key=f"concurrent-{name}")
                )
            except IntakeConfirmationError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # first thread consumes the preview; the rest hit preview-not-found (409)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(self.service.list_confirmations()), 2)

    # ------------------------------------------------------------------- A17

    def test_a17_validity_never_extends_quote_terms(self):
        preview = self._preview()
        record = self._confirm(
            preview, valid_until=(NOW + timedelta(hours=100)).isoformat()
        )["confirmation"]
        expires = datetime.fromisoformat(self.snapshot_a["expires_at"])
        self.assertEqual(datetime.fromisoformat(record["valid_until"]), expires)

        later = self.service.eligibility(
            self.snapshot_a["record_id"], use="local_analysis", now=expires + timedelta(minutes=1)
        )
        self.assertFalse(later["eligible"])
        self.assertIn("confirmation_expired", later["reasons"])

    def test_a17_revoke_batch_and_confirmation(self):
        preview = self._preview()
        record = self._confirm(preview)["confirmation"]
        self.service.revoke(record["record_id"], operator="操作者甲", reason="发现声明与原件不符，撤销确认")
        after = self.service.eligibility(self.snapshot_a["record_id"], use="local_analysis", now=NOW)
        self.assertFalse(after["eligible"])
        self.assertIn("no_active_confirmation", after["reasons"])
        # history retained
        self.assertEqual(self.service.detail(record["record_id"])["status"], "revoked")

        self.stack.imports.revoke_batch(str(self.batch["record_id"]), operator="test", reason="批次撤销测试理由")
        with self.assertRaises(IntakeConfirmationError):
            self._preview()

    def test_a17_reimported_old_original_stays_stale(self):
        stale_observed = NOW - timedelta(hours=30)
        batch2, payload2, snapshots2 = self.stack.import_batch(observed=stale_observed)
        declaration = _declaration(
            batch2,
            snapshots2,
            issued=stale_observed,
            received=stale_observed + timedelta(minutes=30),
            valid_until=NOW + timedelta(hours=10),
        )
        with self.assertRaises(IntakeConfirmationError) as ctx:
            self.service.preview(
                str(batch2["record_id"]),
                content=payload2,
                filename="quote2.csv",
                declaration=declaration,
                now=NOW,
            )
        self.assertIn("quote_freshness", str(ctx.exception))
        outcome = self.service.eligibility(snapshots2[0]["record_id"], use="local_analysis", now=NOW)
        self.assertFalse(outcome["eligible"])
        self.assertIn("quote_expired", outcome["reasons"])

    # ------------------------------------------------------------------- A18

    def test_a18_persist_failure_leaves_no_ghost_and_replay_recovers(self):
        preview = self._preview()
        store = self.service._records
        original_persist = store._persist

        def failing_persist():
            raise OSError("disk full (fixture)")

        store._persist = failing_persist
        with self.assertRaises(IntakeConfirmationError) as ctx:
            self._confirm(preview, key="recover-key-0001")
        self.assertEqual(ctx.exception.status_code, 503)
        # the in-memory rollback kept the ledger clean
        self.assertEqual(self.service.list_confirmations(), [])
        store._persist = original_persist
        store._load()
        preview_again = self._preview()
        result = self._confirm(preview_again, key="recover-key-0001")
        self.assertFalse(result["idempotent"])
        reloaded = _Stack(self.tmp).service()
        self.assertEqual(len(reloaded.list_confirmations()), 1)

    # ------------------------------------------------------------------- A19

    def test_a19_precise_gaps_and_partial_confirmation(self):
        # a hard contradiction (unit mismatch) excludes the row; a missing
        # optional term stays a recorded gap without blocking local analysis
        declaration = _declaration(
            self.batch,
            self.snapshots,
            offers={
                self.snapshot_a["record_id"]: _offer_terms(),
                self.snapshot_b["record_id"]: _offer_terms(
                    quantity_unit="箱", after_sales_terms=""
                ),
            },
        )
        preview = self.service.preview(
            str(self.batch["record_id"]),
            content=self.quote_bytes,
            filename="quote.csv",
            declaration=declaration,
            now=NOW,
        )
        self.assertEqual(preview["confirmable_snapshot_ids"], [self.snapshot_a["record_id"]])
        excluded = {row["snapshot_id"]: row["reasons"] for row in preview["excluded_snapshots"]}
        self.assertIn("quantity_units", excluded[self.snapshot_b["record_id"]])
        gap_codes = {
            (gap.get("code"), gap.get("snapshot_id"))
            for gap in preview["gaps"]
        }
        self.assertIn(("after_sales_terms", self.snapshot_b["record_id"]), gap_codes)
        record = self._confirm(preview)["confirmation"]
        self.assertEqual(record["snapshot_ids"], [self.snapshot_a["record_id"]])
        self.assertTrue(record["gaps_at_confirmation"])

    def test_a19_demo_marker_not_confirmable(self):
        import csv as _csv
        import io as _io

        payload = declared_quote_csv(CSV_BODY, observed=NOW)
        rows = list(_csv.DictReader(_io.StringIO(payload.decode("utf-8"))))
        for row in rows:
            row["source_kind"] = "demo"
        buffer = _io.StringIO()
        writer = _csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        demo_payload = buffer.getvalue().encode("utf-8")

        import_preview = self.stack.imports.preview_bytes(demo_payload, kind="csv", filename="demo.csv")
        imported = self.stack.imports.confirm(
            import_preview["preview_id"],
            content_sha256=import_preview["content_sha256"],
            revision=import_preview["revision"],
            supplier="杭州五金供应商",
            operator="test",
        )
        demo_batch = imported["batch"]
        demo_snapshots = imported["snapshots"]
        declaration = _declaration(demo_batch, demo_snapshots)
        preview = self.service.preview(
            str(demo_batch["record_id"]),
            content=demo_payload,
            filename="demo.csv",
            declaration=declaration,
            now=NOW,
        )
        # demo markers are row-level failures: nothing is confirmable
        self.assertEqual(preview["confirmable_snapshot_ids"], [])
        self.assertTrue(
            all(
                "demo_marker" in row["reasons"]
                for row in preview["excluded_snapshots"]
            ),
            preview["excluded_snapshots"],
        )
        with self.assertRaises(IntakeConfirmationError) as ctx:
            self._confirm(preview, key="demo-key-000001")
        self.assertIn("no confirmable snapshots", str(ctx.exception))

    def test_a19_cross_batch_reference_rejected(self):
        other_body = CSV_BODY.replace(b"SKU-A", b"SKU-C")
        other_batch, other_payload, other_snapshots = self.stack.import_batch(csv_body=other_body)
        declaration = _declaration(
            self.batch,
            self.snapshots,
            offers={
                self.snapshot_a["record_id"]: _offer_terms(),
                other_snapshots[0]["record_id"]: _offer_terms(),
            },
        )
        with self.assertRaises(IntakeConfirmationError) as ctx:
            self.service.preview(
                str(self.batch["record_id"]),
                content=self.quote_bytes,
                filename="quote.csv",
                declaration=declaration,
                now=NOW,
            )
        self.assertIn("unexpected_evidence_rows", str(ctx.exception))


class IntakeConfirmationHttpTests(unittest.TestCase):
    """HTTP-level auth and routing against the real router wiring (A14 + H)."""

    TOKEN = "unit-test-operator-token-0123456789abcdef"
    TOKEN_SHA = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()

    def setUp(self):
        # the HTTP router always uses the real clock, so pin the fixture
        # clock to "now" for this class. Resolve the module THROUGH the
        # running class: under discovery the module may load as
        # "test_intake_confirmation" while "tests.test_intake_confirmation"
        # is a SEPARATE instance whose NOW patch would never apply (a
        # fixture time bomb once the wall clock passed NOW+20h).
        self._module = sys.modules[type(self).__module__]
        own = self._module
        self._original_now = own.NOW
        own.NOW = datetime.now(timezone.utc)
        self.tmp = Path(tempfile.mkdtemp(prefix="intake-http-"))
        self.stack = _Stack(self.tmp)
        self.batch, self.quote_bytes, self.snapshots = self.stack.import_batch(
            observed=own.NOW - timedelta(minutes=30)
        )

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(
            build_intake_confirmation_router(
                self.stack.repos,
                self.stack.imports,
                self.stack.evidence,
                self.tmp,
                token_sha256=self.TOKEN_SHA,
                operator="配置操作者",
            )
        )
        self.client = TestClient(app)
        self._test_client_cls = TestClient

    def tearDown(self):
        self._module.NOW = self._original_now
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _preview_payload(self):
        declaration = _declaration(self.batch, self.snapshots)
        import base64

        return {
            "filename": "quote.csv",
            "content_b64": base64.b64encode(self.quote_bytes).decode("ascii"),
            "declaration": declaration.model_dump(),
        }

    def test_a14_operator_channel_only(self):
        url = f"/commerce/supplier-imports/{self.batch['record_id']}/confirmation/preview"
        # no credential
        response = self.client.post(url, json=self._preview_payload())
        self.assertEqual(response.status_code, 401, response.text)
        # pi job session token is rejected before credential evaluation
        response = self.client.post(
            url,
            json=self._preview_payload(),
            headers={"Authorization": f"Bearer {self.TOKEN}", "X-Xianyu-Job-Token": "jobtok"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        # wrong credential
        response = self.client.post(
            url, json=self._preview_payload(), headers={"Authorization": "Bearer wrong-token"}
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_a14_operator_identity_not_taken_from_body(self):
        url = f"/commerce/supplier-imports/{self.batch['record_id']}/confirmation/preview"
        payload = self._preview_payload()
        payload["operator"] = "伪造操作者"
        response = self.client.post(
            url, json=payload, headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        self.assertEqual(response.status_code, 422, response.text)  # extra field forbidden

        payload = self._preview_payload()
        response = self.client.post(
            url, json=payload, headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()

        confirm_url = f"/commerce/supplier-imports/{self.batch['record_id']}/confirmation/confirm"
        confirm_payload = {
            "preview_id": preview["preview_id"],
            "preview_digest": preview["digest"],
            "idempotency_key": "http-confirm-0001",
            "allowed_uses": ["local_analysis"],
            "valid_until": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
            "verification_note": "HTTP 层核对了原件与声明一致性",
        }
        response = self.client.post(
            confirm_url, json=confirm_payload, headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()["confirmation"]
        self.assertEqual(record["operator"], "配置操作者")  # from trusted config, never the body

    def test_http_flow_readonly_and_status(self):
        view = self.client.get(f"/commerce/supplier-imports/{self.batch['record_id']}/confirmation")
        self.assertEqual(view.status_code, 200, view.text)
        body = view.json()
        self.assertEqual(body["status"], "committed")
        self.assertEqual(len(body["snapshots"]), 2)

        missing = self.client.get("/commerce/intake-confirmations/ic_missing")
        self.assertEqual(missing.status_code, 404)

        eligible = self.client.get(
            "/commerce/intake-confirmations/eligibility",
            params={"snapshot_id": self.snapshots[0]["record_id"], "use": "local_analysis"},
        )
        self.assertEqual(eligible.status_code, 200, eligible.text)
        self.assertFalse(eligible.json()["eligible"])
        self.assertIn("no_active_confirmation", eligible.json()["reasons"])


class ServerAppMountTests(unittest.TestCase):
    """The real server app mounts the router; unconfigured mutating routes
    answer 503 while read-only eligibility stays available."""

    def test_mounted_router_gates(self):
        from fastapi.testclient import TestClient

        from xianyu_agent import server as server_module

        client = TestClient(server_module.app)
        response = client.post(
            "/commerce/supplier-imports/impb_nonexistent/confirmation/preview",
            json={
                "filename": "q.csv",
                "content_b64": "aGk=",
                "declaration": {},
            },
        )
        self.assertIn(response.status_code, (503, 422), response.text)
        response = client.get(
            "/commerce/intake-confirmations/eligibility",
            params={"snapshot_id": "offer_missing", "use": "local_analysis"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["eligible"])


if __name__ == "__main__":
    unittest.main()
