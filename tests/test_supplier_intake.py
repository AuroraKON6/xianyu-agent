import base64
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.supplier_import import SupplierImportService
from xianyu_agent.supplier_intake import IntakeDeclaration, assess_supplier_intake, build_supplier_intake_router


class SupplierIntakeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="supplier-intake-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.now = datetime.now(timezone.utc)
        self.repos = JsonCommerceRepositories(self.root)
        self.imports = SupplierImportService(self.repos, self.root)
        quote_observed = (self.now - timedelta(minutes=10)).isoformat()
        quote_expiry = (self.now + timedelta(hours=1)).isoformat()
        self.quote = ("sku,name,price,currency,unit,moq,stock,freight,spec,condition,source_kind,source_reference,source_observed_at,valid_until\n"
                      "FIXTURE-1,Table,150,CNY,pc,2,10,15,120x60x85cm,new,supplier_original,fixture-original-receipt,"
                      f"{quote_observed},{quote_expiry}\n").encode()
        preview = self.imports.preview_bytes(self.quote, kind="csv")
        imported = self.imports.confirm(preview["preview_id"], content_sha256=preview["content_sha256"],
                                        revision=1, supplier="isolated-intake-fixture", operator="fixture",
                                        observed_at=self.now - timedelta(minutes=10))
        self.batch = imported["batch"]
        self.snapshots = imported["snapshots"]
        self.now = datetime.now(timezone.utc)
        self.snapshot_id = self.snapshots[0]["record_id"]
        self.metadata = {
            "batch_id": self.batch["record_id"], "content_sha256": self.batch["content_sha256"],
            "supplier": self.batch["supplier"], "source_kind": "supplier_original",
            "origin_ref": "fixture-original-receipt", "supplier_identity_ref": "fixture-identity-check",
            "use_permission_ref": "fixture-local-review-permission", "local_review_permitted": True,
            "external_llm_permitted": False,
            "issued_at": (self.now - timedelta(hours=1)).isoformat(),
            "received_at": (self.now - timedelta(minutes=30)).isoformat(),
            "valid_until": quote_expiry,
            "offers": {self.snapshot_id: {
                "currency": "CNY", "price_unit": "pc", "quantity_unit": "pc", "freight_basis": "per_unit",
                "freight_destination": "fixture destination only", "tax_terms": "fixture stated price includes tax",
                "delivery_terms": "fixture dispatch within three days", "after_sales_terms": "fixture defect return terms",
                "stock_observed_at": (self.now - timedelta(minutes=30)).isoformat(),
                "stock_valid_until": (self.now + timedelta(hours=1)).isoformat(), "evidence_ref": "fixture-row-1",
            }},
        }
        app = FastAPI()
        app.include_router(build_supplier_intake_router(self.repos, self.imports))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def assess(self, metadata=None, *, batch=None, snapshots=None, quote=None):
        return assess_supplier_intake(batch or self.batch, self.snapshots if snapshots is None else snapshots,
                                      IntakeDeclaration.model_validate(self.metadata if metadata is None else metadata),
                                      quote_bytes=self.quote if quote is None else quote, now=self.now)

    def manifest(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_gap(self, report, code):
        self.assertIn(code, [entry["code"] for entry in report["gaps"]])
        self.assertNotEqual(report["status"], "ready_for_human_review")
        self.assertFalse(report["external_llm_authorized"])
        self.assertFalse(report["platform_action_authorized"])

    def test_complete_fixture_is_only_ready_for_human_review_and_writes_nothing(self):
        before = self.manifest()
        report = self.assess()
        self.assertEqual(report["status"], "ready_for_human_review", report["gaps"])
        self.assertTrue(report["human_confirmation_required"])
        self.assertFalse(report["provider_verified"])
        self.assertFalse(report["external_llm_authorized"])
        self.assertFalse(report["platform_action_authorized"])
        self.assertEqual(self.manifest(), before)

    def test_original_bytes_are_required_not_just_a_correct_hash_claim(self):
        report = assess_supplier_intake(self.batch, self.snapshots, IntakeDeclaration.model_validate(self.metadata), now=self.now)
        self.assert_gap(report, "original_file_sha256")
        self.assert_gap(self.assess(quote=self.quote.replace(b"150", b"151")), "original_file_sha256")
        self.assertNotEqual(self.assess()["input_sha256"], self.assess(quote=self.quote + b"\n")["input_sha256"])

    def test_missing_origin_identity_or_permission_stays_unknown(self):
        for field in ("origin_ref", "supplier_identity_ref", "use_permission_ref", "local_review_permitted", "source_kind"):
            metadata = copy.deepcopy(self.metadata)
            del metadata[field]
            with self.subTest(field=field):
                report = self.assess(metadata)
                self.assertNotEqual(report["status"], "ready_for_human_review")

    def test_demo_and_explicit_demo_marker_cannot_be_relabelled_real(self):
        metadata = copy.deepcopy(self.metadata)
        metadata["source_kind"] = "demo"
        self.assert_gap(self.assess(metadata), "supplier_source")
        snapshots = copy.deepcopy(self.snapshots)
        snapshots[0]["attributes"]["notes"] = "source=demo; this is not a real supplier"
        self.assert_gap(self.assess(snapshots=snapshots), "demo_marker")

    def test_shared_provenance_is_not_overridden_by_supplemental_claims(self):
        for kind in ("demo", "unknown"):
            snapshots = copy.deepcopy(self.snapshots)
            snapshots[0]["attributes"]["quote_provenance"]["source_kind"] = kind
            with self.subTest(kind=kind):
                self.assert_gap(self.assess(snapshots=snapshots), "persisted_quote_provenance")
        metadata = copy.deepcopy(self.metadata)
        metadata["valid_until"] = (self.now + timedelta(days=1)).isoformat()
        self.assert_gap(self.assess(metadata), "declared_validity_bound")
        metadata = copy.deepcopy(self.metadata)
        metadata["origin_ref"] = "different-source-reference"
        self.assert_gap(self.assess(metadata), "declared_origin_binding")

    def test_receipt_after_observation_but_before_import_is_valid(self):
        metadata = copy.deepcopy(self.metadata)
        metadata["received_at"] = (self.now - timedelta(minutes=5)).isoformat()
        self.assertEqual(self.assess(metadata)["status"], "ready_for_human_review")

    def test_declared_provider_supplier_and_batch_must_match(self):
        for field, code in (("batch_id", "declared_batch"), ("supplier", "declared_supplier"), ("content_sha256", "declared_file_sha256")):
            metadata = copy.deepcopy(self.metadata)
            metadata[field] = "0" * 64 if field == "content_sha256" else "different"
            with self.subTest(field=field):
                self.assert_gap(self.assess(metadata), code)
        snapshots = copy.deepcopy(self.snapshots)
        snapshots[0]["seller_ref"] = "another-supplier"
        self.assert_gap(self.assess(snapshots=snapshots), "snapshot_binding")

    def test_revoked_partial_and_duplicate_batches_fail_closed(self):
        for status in ("failed", "revoked", "pending"):
            batch = {**self.batch, "status": status}
            self.assert_gap(self.assess(batch=batch), "committed_batch")
        self.assert_gap(self.assess(snapshots=[]), "batch_snapshots")
        self.assert_gap(self.assess(snapshots=self.snapshots * 2), "batch_snapshots")
        self.assert_gap(self.assess(batch={**self.batch, "snapshot_ids": [self.snapshot_id] * 2}), "batch_snapshots")

    def test_expired_future_naive_and_reimported_old_quote_are_not_current(self):
        for changes, code in (
            ({"valid_until": self.now.isoformat()}, "quote_time_order"),
            ({"issued_at": (self.now + timedelta(hours=1)).isoformat()}, "quote_time_order"),
            ({"issued_at": self.now.replace(tzinfo=None).isoformat()}, "issued_at"),
            ({"issued_at": (self.now - timedelta(days=2)).isoformat()}, "quote_freshness"),
            ({"received_at": (self.now + timedelta(hours=1)).isoformat()}, "quote_time_order"),
        ):
            with self.subTest(changes=changes):
                self.assert_gap(self.assess({**self.metadata, **changes}), code)

    def test_snapshot_future_time_and_explicit_expiry_are_respected(self):
        for field, value, code in (
            ("observed_at", (self.now + timedelta(minutes=1)).isoformat(), "snapshot_time"),
            ("expires_at", (self.now - timedelta(seconds=1)).isoformat(), "snapshot_expiry"),
        ):
            snapshots = copy.deepcopy(self.snapshots)
            snapshots[0][field] = value
            self.assert_gap(self.assess(snapshots=snapshots), code)

    def test_unit_currency_and_missing_freight_terms_are_not_assumed(self):
        for field, value, code in (
            ("currency", "USD", "currency"), ("price_unit", "case", "quantity_units"),
            ("quantity_unit", "", "quantity_units"), ("freight_basis", "unknown", "freight_basis"),
            ("freight_destination", "", "freight_destination"),
        ):
            metadata = copy.deepcopy(self.metadata)
            metadata["offers"][self.snapshot_id][field] = value
            with self.subTest(field=field):
                self.assert_gap(self.assess(metadata), code)

    def test_numeric_stock_moq_and_money_fail_closed(self):
        for field, value, code in (
            ("moq", True, "moq"), ("moq", 1.5, "moq"), ("stock_quantity", None, "stock"),
            ("stock_quantity", 1, "stock_covers_moq"),
            ("shipping_quote", {"state": "unknown", "amount": None}, "freight"),
            ("eligible_price", {"state": "known", "amount": "NaN", "currency": "CNY"}, "price"),
            ("eligible_price", {"state": "known", "amount": "-1", "currency": "CNY"}, "price"),
        ):
            snapshots = copy.deepcopy(self.snapshots)
            snapshots[0][field] = value
            with self.subTest(field=field):
                self.assert_gap(self.assess(snapshots=snapshots), code)

    def test_stock_date_and_terms_are_required_per_exact_snapshot(self):
        for field in ("stock_observed_at", "stock_valid_until", "tax_terms", "delivery_terms", "after_sales_terms", "evidence_ref"):
            metadata = copy.deepcopy(self.metadata)
            metadata["offers"][self.snapshot_id][field] = ""
            with self.subTest(field=field):
                self.assertNotEqual(self.assess(metadata)["status"], "ready_for_human_review")
        metadata = copy.deepcopy(self.metadata)
        metadata["offers"]["different-snapshot"] = metadata["offers"].pop(self.snapshot_id)
        self.assert_gap(self.assess(metadata), "unexpected_evidence_rows")

    def test_declared_external_permission_does_not_authorize_model_or_trade(self):
        metadata = copy.deepcopy(self.metadata)
        metadata["external_llm_permitted"] = True
        report = self.assess(metadata)
        self.assertEqual(report["status"], "ready_for_human_review")
        self.assertTrue(report["external_llm_permission_declared"])
        self.assertFalse(report["external_llm_authorized"])
        self.assertFalse(report["platform_action_authorized"])

    def test_unknown_text_and_invalid_persisted_shapes_are_not_ready(self):
        metadata = copy.deepcopy(self.metadata)
        metadata["supplier_identity_ref"] = "unknown"
        self.assert_gap(self.assess(metadata), "supplier_identity_ref")
        snapshots = copy.deepcopy(self.snapshots)
        snapshots[0]["condition"] = "未知"
        self.assert_gap(self.assess(snapshots=snapshots), "condition")
        snapshots[0]["attributes"] = ["corrupt"]
        with self.assertRaises(ValueError):
            self.assess(snapshots=snapshots)

    def test_matching_unknown_unit_labels_do_not_establish_a_unit(self):
        for label in ("unknown", "未知", "-"):
            snapshots = copy.deepcopy(self.snapshots)
            snapshots[0]["unit"] = label
            metadata = copy.deepcopy(self.metadata)
            metadata["offers"][self.snapshot_id]["price_unit"] = label
            metadata["offers"][self.snapshot_id]["quantity_unit"] = label
            with self.subTest(label=label):
                self.assert_gap(self.assess(metadata, snapshots=snapshots), "quantity_units")

    def test_cli_refuses_source_changes_without_creating_output_or_data(self):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "check_supplier_intake.py"
        spec = importlib.util.spec_from_file_location("intake_cli", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original_read = module.read_bounded
        reads = {}

        def changing_read(path, limit):
            content = original_read(path, limit)
            reads[str(path)] = reads.get(str(path), 0) + 1
            return content + b" " if reads[str(path)] > 1 else content

        before = self.manifest()
        with patch.object(module, "read_bounded", side_effect=changing_read), patch("builtins.print") as printed:
            code = module.main(["--data-dir", str(self.root), "--batch-id", self.batch["record_id"]])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(printed.call_args.args[0])["status"], "input_error")
        self.assertEqual(self.manifest(), before)
        with patch("builtins.print"):
            code = module.main(["--data-dir", str(self.root / "does-not-exist"), "--batch-id", "missing"])
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "does-not-exist").exists())

    def test_readiness_digest_changes_with_snapshot_and_declaration(self):
        baseline = self.assess()["input_sha256"]
        changed = copy.deepcopy(self.snapshots)
        changed[0]["eligible_price"]["amount"] = "160"
        self.assertNotEqual(self.assess(snapshots=changed)["input_sha256"], baseline)
        metadata = copy.deepcopy(self.metadata)
        metadata["origin_ref"] = "different-receipt"
        self.assertNotEqual(self.assess(metadata)["input_sha256"], baseline)

    def test_http_rejects_oversized_and_ambiguous_inputs(self):
        path = f"/commerce/supplier-imports/{self.batch['record_id']}/readiness"
        self.assertEqual(self.client.post(path, json={"content_b64": "A" * (3 * 1024 * 1024)}).status_code, 422)
        with patch.object(self.imports, "list_batches", return_value=[self.batch, self.batch]):
            self.assertEqual(self.client.get(path).status_code, 409)
        with patch.object(self.repos.offers, "list", return_value=["corrupt"]):
            self.assertEqual(self.client.get(path).status_code, 409)

    def test_readonly_http_missing_proof_and_full_proof_never_mutate(self):
        before = self.manifest()
        path = f"/commerce/supplier-imports/{self.batch['record_id']}/readiness"
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["status"], "ready_for_human_review")
        response = self.client.post(path, json={"declaration": self.metadata,
                                               "content_b64": base64.b64encode(self.quote).decode()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ready_for_human_review", response.json()["gaps"])
        self.assertEqual(self.manifest(), before)

    def test_http_rejects_spoofed_operator_type_coercion_and_bad_base64(self):
        path = f"/commerce/supplier-imports/{self.batch['record_id']}/readiness"
        for body in ({"declaration": {"operator": "admin"}}, {"declaration": {"local_review_permitted": "true"}},
                     {"content_b64": "not-base64"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(path, json=body).status_code, 422)
        self.assertEqual(self.client.get("/commerce/supplier-imports/missing/readiness").status_code, 404)

    def test_restart_and_cli_readonly_exit_codes(self):
        restarted = SupplierImportService(JsonCommerceRepositories(self.root), self.root)
        self.assertEqual(restarted.list_batches()[0], self.batch)
        quote_path = self.root / "quote.csv"
        declaration_path = self.root / "declaration.json"
        quote_path.write_bytes(self.quote)
        declaration_path.write_text(json.dumps(self.metadata), encoding="utf-8")
        before = self.manifest()
        command = [sys.executable, "scripts/check_supplier_intake.py", "--data-dir", str(self.root),
                   "--batch-id", self.batch["record_id"]]
        options = {"cwd": str(Path(__file__).resolve().parents[1]), "capture_output": True,
                   "timeout": 30, "creationflags": 0x08000000 if os.name == "nt" else 0,
                   "env": {**os.environ, "DEEPSEEK_API_KEY": "", "PYTHONUTF8": "1"}}
        missing = subprocess.run(command, **options)
        self.assertEqual(missing.returncode, 2, missing.stderr)
        complete = subprocess.run(command + ["--quote-file", str(quote_path), "--declaration", str(declaration_path)], **options)
        self.assertEqual(complete.returncode, 0, complete.stderr)
        report = json.loads(complete.stdout)
        self.assertEqual(report["status"], "ready_for_human_review")
        self.assertEqual(len(report["read_file_sha256"]), 2)
        self.assertEqual(self.manifest(), before)

    def test_actual_application_mount_uses_temporary_state(self):
        script = """
import hashlib
import os
from pathlib import Path
from fastapi.testclient import TestClient
from xianyu_agent.server import app
with TestClient(app) as client:
    response = client.get('/commerce/supplier-imports/missing/readiness')
    assert response.status_code == 404, response.text
    response = client.post('/commerce/supplier-imports/missing/readiness', json={}, headers={'X-Xianyu-Job-Token':'not-an-operator'})
    assert response.status_code == 403, response.text
    quote = 'sku,price,currency,unit,moq,stock,freight,spec,condition,notes\\nFIXTURE-2,150,CNY,pc,2,10,15,120x60cm,new,source=demo\\n'
    preview = client.post('/commerce/supplier-imports/preview', json={'kind':'csv', 'content':quote}).json()
    imported = client.post('/commerce/supplier-imports/' + preview['preview_id'] + '/confirm', json={
        'content_sha256':preview['content_sha256'], 'revision':preview['revision'], 'supplier':'isolated-http-fixture', 'operator':'fixture'})
    assert imported.status_code == 200, imported.text
    batch_id = imported.json()['batch']['record_id']
    root = Path(os.environ['DATA_DIR'])
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file()}
    response = client.get('/commerce/supplier-imports/' + batch_id + '/readiness')
    assert response.status_code == 200, response.text
    assert response.json()['status'] != 'ready_for_human_review'
    assert 'demo_marker' in [entry['code'] for entry in response.json()['gaps']]
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file()}
    assert before == after
print('SUPPLIER_INTAKE_ROUTER_MOUNTED')
"""
        root = self.root / "application"
        process = subprocess.run([sys.executable, "-c", script], cwd=str(Path(__file__).resolve().parents[1]),
                                 env={**os.environ, "DATA_DIR": str(root), "DB_PATH": str(root / "db.sqlite"),
                                      "DEEPSEEK_API_KEY": "", "PI_ENABLED": "0", "SHADOW_OBSERVER_ENABLED": "0",
                                      "XIANYU_ADAPTER": "stub", "PYTHONUTF8": "1"},
                                 capture_output=True, timeout=40, creationflags=0x08000000 if os.name == "nt" else 0)
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", errors="replace"))
        self.assertIn(b"SUPPLIER_INTAKE_ROUTER_MOUNTED", process.stdout)


if __name__ == "__main__":
    unittest.main()
