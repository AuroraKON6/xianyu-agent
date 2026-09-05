from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from xianyu_agent.commercial_alpha_intake import (
    CommercialAlphaIntakeError,
    initialize_intake_bundle,
    validate_intake_bundle,
)


CHECKED_AT = "2026-09-05T08:00:00Z"
ROOT = Path(__file__).resolve().parents[1]


def _valid_manifest() -> dict:
    observed = {
        "supplier_quote": "2026-09-05T07:30:00Z",
        "supplier_stock": "2026-09-05T07:30:00Z",
        "supplier_fulfillment": "2026-09-01T08:00:00Z",
        "image_rights": "2026-09-01T08:00:00Z",
        "market_baseline": "2026-09-04T08:00:00Z",
    }
    return {
        "schema_version": 1,
        "candidate": {
            "title": "商用不锈钢双层操作台 1200x600",
            "description": "Exact supplier SKU for a one-item Commercial Alpha test.",
            "category": "商用厨具",
            "source_platform": "pdd",
            "source_url": "https://mobile.yangkeduo.com/goods.html?goods_id=881234567890",
            "supplier_name": "华东不锈钢设备店",
            "supplier_sku": "OPS-1200-2L",
            "supplier_price": 680,
            "supplier_shipping": 80,
            "target_sale_price": 1480,
            "specifications": {
                "size": "1200x600x800mm",
                "material": "201 stainless steel",
            },
            "images": ["images/product-01.jpg"],
            "sales_platform": "xianyu",
            "market_scope": "domestic",
            "supplier_verified": True,
            "supplier_verification_note": "Verified shop identity and exact SKU in platform chat.",
            "minimum_order_quantity": 1,
            "stock_status": "in_stock",
            "stock_quantity": 12,
            "fulfillment_verified": True,
            "fulfillment_verification_note": "Supplier confirmed neutral shipment and traceable dispatch.",
            "neutral_shipping_confirmed": True,
            "dispatch_time_hours": 48,
            "supplier_return_policy": "conditional",
            "damage_liability_note": "Supplier replaces transport-damaged panels after photo verification.",
            "image_rights_type": "supplier_authorized",
            "image_rights_evidence": "Supplier granted listing use for the exact supplied product images.",
            "competitor_links": [
                "https://www.goofish.com/item?id=901001",
                "https://www.goofish.com/item?id=901002",
            ],
            "competitor_price_min": 1380,
            "competitor_price_max": 1680,
            "estimated_demand_signal": "Two comparable listings showed recent inquiry markers during capture.",
            "platform_fee_rate": 0.08,
            "platform_fixed_fee": 0,
            "expected_refund_rate": 0.02,
        },
        "evidence": [
            {
                "kind": kind,
                "path": f"evidence/{kind}.txt",
                "observed_at": observed[kind],
                "note": f"Current {kind} evidence for exact supplier SKU OPS-1200-2L.",
                "source_url": "",
            }
            for kind in (
                "supplier_quote",
                "supplier_stock",
                "supplier_fulfillment",
                "image_rights",
                "market_baseline",
            )
        ],
    }


def _write_valid_bundle(root: Path) -> dict:
    (root / "evidence").mkdir(parents=True)
    (root / "images").mkdir()
    manifest = _valid_manifest()
    for entry in manifest["evidence"]:
        kind = entry["kind"]
        (root / entry["path"]).write_text(
            f"Inspectable {kind} facts for exact SKU OPS-1200-2L.\n",
            encoding="utf-8",
        )
    (root / "images" / "product-01.jpg").write_bytes(
        b"\xff\xd8\xff\xe0commercial-alpha-product-image"
    )
    _write_manifest(root, manifest)
    return manifest


def _write_manifest(root: Path, manifest: dict) -> None:
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _snapshot(root: Path) -> dict:
    result = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        stat = path.stat()
        result[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


class CommercialAlphaIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "bundle"
        self.root.mkdir()
        self.manifest = _write_valid_bundle(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def validate(self):
        return validate_intake_bundle(
            self.root,
            checked_at=CHECKED_AT,
            min_profit_amount=20,
            min_profit_rate=0.30,
        )

    def test_valid_bundle_passes_input_gate_but_not_commercial_gate(self):
        report = self.validate()

        self.assertEqual(report["status"], "pass", report)
        self.assertTrue(report["bundle_ready"])
        self.assertFalse(report["commercial_ready"])
        self.assertFalse(report["writes_performed"])
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["evidence"]["valid_count"], 5)
        self.assertEqual(report["images"]["valid_count"], 1)
        projection = report["candidate_gate_projection"]
        self.assertTrue(projection["evidence_ready"], projection)
        self.assertFalse(projection["ready"])
        self.assertEqual(projection["completion"], {"total": 12, "passed": 11, "remaining": 1})
        self.assertEqual(projection["failed_gate_codes"], ["manual_decision_current"])
        self.assertEqual(report["next_step"]["code"], "archive_validated_bundle")

    def test_validation_is_deterministic_and_does_not_mutate_bundle(self):
        before = _snapshot(self.root)
        first = self.validate()
        middle = _snapshot(self.root)
        second = self.validate()
        after = _snapshot(self.root)

        self.assertEqual(before, middle)
        self.assertEqual(middle, after)
        self.assertEqual(first["bundle_fingerprint"], second["bundle_fingerprint"])
        self.assertEqual(
            first["candidate_gate_projection"]["evidence_fingerprint"],
            second["candidate_gate_projection"]["evidence_fingerprint"],
        )

    def test_missing_and_duplicate_evidence_kinds_fail_closed(self):
        manifest = deepcopy(self.manifest)
        manifest["evidence"] = manifest["evidence"][:-1]
        duplicate = deepcopy(manifest["evidence"][0])
        duplicate["path"] = "evidence/supplier_quote-copy.txt"
        (self.root / duplicate["path"]).write_text("Second quote capture.\n", encoding="utf-8")
        manifest["evidence"].append(duplicate)
        _write_manifest(self.root, manifest)

        report = self.validate()
        codes = [issue["code"] for issue in report["issues"]]

        self.assertFalse(report["bundle_ready"])
        self.assertIn("evidence_kind_duplicate", codes)
        self.assertIn("evidence_kind_missing", codes)
        self.assertIn("market_baseline", report["candidate_gate_projection"]["failed_gate_codes"])

    def test_path_traversal_is_rejected_without_reading_outside_file(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside bundle\n", encoding="utf-8")
        manifest = deepcopy(self.manifest)
        manifest["evidence"][0]["path"] = "../outside.txt"
        _write_manifest(self.root, manifest)

        report = self.validate()

        self.assertIn("bundle_path_unsafe", {item["code"] for item in report["issues"]})
        self.assertNotIn(str(outside), json.dumps(report, ensure_ascii=False))

    def test_symlink_is_rejected(self):
        outside = self.root.parent / "outside-link-target.txt"
        outside.write_text("outside link target\n", encoding="utf-8")
        link = self.root / "evidence" / "linked-quote.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        manifest = deepcopy(self.manifest)
        manifest["evidence"][0]["path"] = "evidence/linked-quote.txt"
        _write_manifest(self.root, manifest)

        report = self.validate()

        self.assertTrue(
            {"bundle_path_outside", "bundle_symlink_not_allowed"}
            & {item["code"] for item in report["issues"]}
        )

    def test_disguised_image_and_oversized_evidence_are_rejected(self):
        (self.root / "images" / "product-01.jpg").write_bytes(b"MZ executable")
        quote = self.root / "evidence" / "supplier_quote.txt"
        with quote.open("wb") as stream:
            stream.truncate(10 * 1024 * 1024 + 1)

        report = self.validate()
        codes = {item["code"] for item in report["issues"]}

        self.assertIn("evidence_file_too_large", codes)
        self.assertIn("product_image_invalid", codes)

    def test_sensitive_text_evidence_is_rejected_without_echoing_secret(self):
        secret = "cookie=do-not-echo-this-value"
        (self.root / "evidence" / "supplier_stock.txt").write_text(secret, encoding="utf-8")

        report = self.validate()
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertIn("evidence_file_invalid", {item["code"] for item in report["issues"]})
        self.assertNotIn("do-not-echo-this-value", encoded)

    def test_candidate_urls_reject_embedded_credentials_without_echoing_them(self):
        manifest = deepcopy(self.manifest)
        manifest["candidate"]["source_url"] = "https://operator:do-not-echo@supplier.invalid/item"
        _write_manifest(self.root, manifest)

        report = self.validate()
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertIn("candidate_url_credentials", {item["code"] for item in report["issues"]})
        self.assertNotIn("do-not-echo", encoded)

    def test_stale_and_future_evidence_are_separate_blockers(self):
        manifest = deepcopy(self.manifest)
        manifest["evidence"][0]["observed_at"] = "2026-09-04T07:59:59Z"
        manifest["evidence"][1]["observed_at"] = "2026-09-05T08:00:30Z"
        _write_manifest(self.root, manifest)

        report = self.validate()
        codes = [item["code"] for item in report["issues"]]

        self.assertIn("evidence_stale", codes)
        self.assertIn("evidence_observed_in_future", codes)
        failed = report["candidate_gate_projection"]["failed_gate_codes"]
        self.assertIn("supplier_quote_evidence", failed)
        self.assertIn("supplier_stock_evidence", failed)

    def test_placeholder_candidate_stays_blocked(self):
        manifest = deepcopy(self.manifest)
        manifest["candidate"]["title"] = "T"
        manifest["candidate"]["specifications"] = {"model": ""}
        _write_manifest(self.root, manifest)

        report = self.validate()

        self.assertFalse(report["bundle_ready"])
        self.assertEqual(
            report["candidate_gate_projection"]["next_step"]["code"],
            "product_identity",
        )

    def test_duplicate_json_key_is_rejected(self):
        (self.root / "manifest.json").write_text(
            '{"schema_version":1,"schema_version":1,"candidate":{},"evidence":[]}',
            encoding="utf-8",
        )

        report = self.validate()

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["issues"][0]["code"], "manifest_duplicate_key")
        self.assertIsNone(report["candidate_gate_projection"])

    def test_init_refuses_nonempty_directory(self):
        output = Path(self.temp.name) / "new-bundle"
        initialized = initialize_intake_bundle(output)

        self.assertEqual(initialized["status"], "initialized")
        self.assertTrue((output / "manifest.json").is_file())
        self.assertTrue((output / "evidence").is_dir())
        self.assertTrue((output / "images").is_dir())
        with self.assertRaisesRegex(CommercialAlphaIntakeError, "not an empty directory"):
            initialize_intake_bundle(output)

    def test_cli_returns_zero_for_valid_bundle_and_two_for_blocked_bundle(self):
        command = [
            sys.executable,
            str(ROOT / "scripts" / "commercial_alpha_intake.py"),
            "validate",
            "--bundle",
            str(self.root),
            "--checked-at",
            CHECKED_AT,
            "--min-profit-amount",
            "20",
            "--min-profit-rate",
            "0.30",
        ]
        passed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertTrue(json.loads(passed.stdout)["bundle_ready"])

        (self.root / "evidence" / "supplier_quote.txt").unlink()
        blocked = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertFalse(json.loads(blocked.stdout)["bundle_ready"])


if __name__ == "__main__":
    unittest.main()
