from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xianyu_agent.candidate_evidence_store import (
    CandidateEvidenceConflict,
    CandidateEvidenceError,
    CandidateEvidenceIntegrityError,
    CandidateEvidenceStore,
    artifact_reference,
    parse_artifact_reference,
)


class CandidateEvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "candidate_evidence"
        self.store = CandidateEvidenceStore(self.root)
        self.observed_at = "2026-08-25T09:00:00Z"

    def _stage(self, *, candidate_id="rc_candidate_a", kind="supplier_quote", content=None, **kwargs):
        return self.store.stage_bytes(
            candidate_id=candidate_id,
            kind=kind,
            content=(b"supplier quote 680 shipping 80\n" if content is None else content),
            original_filename=kwargs.pop("original_filename", f"{kind}.txt"),
            mime_type=kwargs.pop("mime_type", "text/plain"),
            observed_at=kwargs.pop("observed_at", self.observed_at),
            note=kwargs.pop("note", "Captured from the exact supplier SKU page."),
            **kwargs,
        )

    def test_archive_is_pending_until_activated_and_hides_blob_path(self):
        staged = self._stage()
        artifact = staged["artifact"]

        self.assertTrue(staged["created"])
        self.assertEqual(artifact["status"], "pending")
        self.assertTrue(artifact["integrity"]["ok"])
        self.assertNotIn("blob_key", artifact)
        self.assertNotIn(str(self.root), json.dumps(artifact))

        active = self.store.activate(artifact["artifact_id"])
        self.assertEqual(active["status"], "active")
        self.assertEqual(
            parse_artifact_reference(artifact_reference(active["artifact_id"])),
            active["artifact_id"],
        )

    def test_same_active_content_is_idempotent(self):
        first = self._stage()
        self.store.activate(first["artifact"]["artifact_id"])

        duplicate = self._stage()

        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["artifact"]["artifact_id"], first["artifact"]["artifact_id"])
        self.assertEqual(len(self.store.list_candidate_artifacts("rc_candidate_a")), 1)

    def test_new_content_supersedes_old_artifact_without_deleting_it(self):
        first = self._stage(content=b"supplier quote revision one\n")
        self.store.activate(first["artifact"]["artifact_id"])
        second = self._stage(content=b"supplier quote revision two\n")
        self.store.activate(second["artifact"]["artifact_id"])

        artifacts = self.store.list_candidate_artifacts("rc_candidate_a")
        by_id = {item["artifact_id"]: item for item in artifacts}
        self.assertEqual(by_id[first["artifact"]["artifact_id"]]["status"], "superseded")
        self.assertEqual(by_id[second["artifact"]["artifact_id"]]["status"], "active")
        self.assertEqual(
            by_id[second["artifact"]["artifact_id"]]["supersedes_artifact_id"],
            first["artifact"]["artifact_id"],
        )

        with self.assertRaises(CandidateEvidenceConflict):
            self._stage(content=b"supplier quote revision one\n")

    def test_candidate_binding_prevents_cross_candidate_lookup(self):
        staged = self._stage(candidate_id="rc_candidate_a")
        artifact_id = staged["artifact"]["artifact_id"]
        self.store.activate(artifact_id)

        self.assertIsNone(self.store.get_artifact("rc_candidate_b", artifact_id))
        with self.assertRaises(KeyError):
            self.store.resolve_file("rc_candidate_b", artifact_id)

    def test_tampered_blob_fails_hash_verification(self):
        staged = self._stage(content=b"original evidence bytes\n")
        artifact_id = staged["artifact"]["artifact_id"]
        self.store.activate(artifact_id)
        path, _ = self.store.resolve_file("rc_candidate_a", artifact_id)
        path.write_bytes(b"tampered evidence bytes\n")

        artifact = self.store.get_artifact("rc_candidate_a", artifact_id)
        self.assertFalse(artifact["integrity"]["ok"])
        self.assertIn(artifact["integrity"]["status"], {"size_mismatch", "hash_mismatch"})
        with self.assertRaises(CandidateEvidenceIntegrityError):
            self.store.resolve_file("rc_candidate_a", artifact_id)

    def test_rejects_executables_disguised_as_images_and_sensitive_text(self):
        with self.assertRaisesRegex(CandidateEvidenceError, "invalid PNG"):
            self._stage(content=b"MZ executable", original_filename="proof.png", mime_type="image/png")
        with self.assertRaisesRegex(CandidateEvidenceError, "credentials"):
            self._stage(content=b"cookie=secret-value\n", original_filename="proof.txt")
        with self.assertRaisesRegex(CandidateEvidenceError, "unsupported"):
            self._stage(content=b"archive", original_filename="proof.zip", mime_type="application/zip")

    def test_source_url_allows_product_ids_but_rejects_credentials(self):
        staged = self._stage(
            source_url=(
                "https://mobile.yangkeduo.com/goods.html?"
                "goods_id=13812345678&share_id=quote-20260825"
            )
        )

        self.assertEqual(
            staged["artifact"]["source_url"],
            "https://mobile.yangkeduo.com/goods.html?"
            "goods_id=13812345678&share_id=quote-20260825",
        )
        with self.assertRaisesRegex(CandidateEvidenceError, "credential parameters"):
            self._stage(source_url="https://supplier.example/sku?access_token=secret-value")
        with self.assertRaisesRegex(CandidateEvidenceError, "credentials"):
            self._stage(source_url="https://operator:password@supplier.example/sku")

    def test_rejects_empty_oversized_and_future_artifacts(self):
        with self.assertRaisesRegex(CandidateEvidenceError, "empty"):
            self._stage(content=b"")
        with self.assertRaisesRegex(CandidateEvidenceError, "10 MiB"):
            self._stage(content=b"x" * (10 * 1024 * 1024 + 1))
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.assertRaisesRegex(CandidateEvidenceError, "future"):
            self._stage(observed_at=future.isoformat())

    def test_index_survives_reload_with_integrity(self):
        staged = self._stage(kind="market_baseline", content=b"market baseline capture\n")
        artifact_id = staged["artifact"]["artifact_id"]
        self.store.activate(artifact_id)

        reloaded = CandidateEvidenceStore(self.root)
        artifact = reloaded.get_artifact("rc_candidate_a", artifact_id)

        self.assertEqual(artifact["status"], "active")
        self.assertTrue(artifact["integrity"]["ok"])
        self.assertEqual(artifact["sha256"], staged["artifact"]["sha256"])


if __name__ == "__main__":
    unittest.main()
