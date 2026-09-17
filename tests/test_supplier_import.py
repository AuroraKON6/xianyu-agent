"""WP03 supplier import tests: preview -> confirm -> revoke."""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.supplier_import import (
    ConfirmMismatch,
    ImportPreviewError,
    ROW_NEEDS_CONFIRMATION,
    ROW_OK,
    ROW_REJECTED,
    SupplierImportService,
)

CSV_HEADER = "货号,品名,单价,单位,起订量,库存,运费,规格\n"


def make_service(tmp: str):
    repos = JsonCommerceRepositories(Path(tmp))
    return repos, SupplierImportService(repos, Path(tmp))


def build_minimal_xlsx(rows):
    """Build a real (minimal) xlsx with the standard library."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    def cell_xml(value, header_mode=False):
        text = str(value)
        if header_mode or not text.replace(".", "").isdigit():
            escaped = text.replace("&", "&amp;").replace("<", "&lt;")
            return f'<c t="inlineStr"><is><t>{escaped}</t></is></c>'
        return f"<c><v>{text}</v></c>"

    sheet_rows = "".join(
        f'<row>{" ".join(cell_xml(v, header_mode=(i == 0)) for v in row)}</row>'
        for i, row in enumerate(rows)
    )
    sheet_xml = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{ns}"><sheetData>{sheet_rows}</sheetData></worksheet>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{ns}" xmlns:r="{r_ns}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos, self.service = make_service(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_preview_maps_aliases_and_flags_missing_freight(self):
        csv_text = CSV_HEADER + "SUP-1001,不锈钢工作台,150,件,2,10,到付,120x60x85cm\n"
        preview = self.service.preview_bytes(csv_text.encode("utf-8"), kind="csv", filename="quote.csv")
        self.assertEqual(preview["summary"][ROW_NEEDS_CONFIRMATION], 1)
        row = preview["rows"][0]
        self.assertEqual(row["fields"]["sku"], "SUP-1001")
        self.assertEqual(row["fields"]["price"], {"state": "known", "amount": "150", "currency": "CNY", "reason": None})
        # 到付 keeps freight unknown with reason, never zero
        self.assertEqual(row["fields"]["freight"]["state"], "unknown")
        self.assertIn("freight collect", row["fields"]["freight"]["reason"])
        # every header is a Chinese alias, not a canonical name
        self.assertEqual(row["confidence"], 0.9)

    def test_negative_numbers_rejected(self):
        csv_text = CSV_HEADER + "SUP-1002,桌子,-150,件,2,10,包邮,80cm\n"
        preview = self.service.preview_bytes(csv_text.encode("utf-8"), kind="csv")
        self.assertEqual(preview["rows"][0]["status"], ROW_REJECTED)

    def test_duplicate_sku_in_file_rejected_second_row(self):
        csv_text = CSV_HEADER + "SUP-1003,椅子,50,件,1,5,包邮,45cm\nSUP-1003,椅子,55,件,1,5,包邮,45cm\n"
        preview = self.service.preview_bytes(csv_text.encode("utf-8"), kind="csv")
        statuses = [row["status"] for row in preview["rows"]]
        self.assertEqual(statuses, [ROW_OK, ROW_REJECTED])

    def test_duplicate_headers_rejected(self):
        csv_text = "货号,货号,单价\nSUP-1,SUP-2,10\n"
        with self.assertRaises(ImportPreviewError) as ctx:
            self.service.preview_bytes(csv_text.encode("utf-8"), kind="csv")
        self.assertIn("duplicate columns", str(ctx.exception))

    def test_missing_sku_column_rejected(self):
        csv_text = "品名,单价\n桌子,10\n"
        with self.assertRaises(ImportPreviewError):
            self.service.preview_bytes(csv_text.encode("utf-8"), kind="csv")

    def test_case_price_conversion_and_missing_pieces(self):
        header = "货号,品名,单价,单位,装箱量,运费\n"
        # known pieces: 720 per case of 6 -> 120 per piece
        preview = self.service.preview_bytes(
            (header + "SUP-2001,架子,720,箱,6,包邮\n").encode("utf-8"), kind="csv"
        )
        row = preview["rows"][0]
        self.assertEqual(row["fields"]["price"]["amount"], "120")
        self.assertEqual(row["fields"]["pieces_per_case"], 6)
        # case price without pieces: per-piece stays unknown
        header_no_pieces = "货号,品名,单价,单位,运费\n"
        preview2 = self.service.preview_bytes(
            (header_no_pieces + "SUP-2002,架子,720,箱,包邮\n").encode("utf-8"), kind="csv"
        )
        row2 = preview2["rows"][0]
        self.assertEqual(row2["fields"]["price"]["state"], "unknown")
        self.assertEqual(row2["status"], ROW_NEEDS_CONFIRMATION)

    def test_tier_prices_and_free_shipping(self):
        header = "货号,品名,单价,运费,阶梯价\n"
        preview = self.service.preview_bytes(
            (header + "SUP-3001,柜子,160,包邮,10:145;50:140\n").encode("utf-8"), kind="csv"
        )
        row = preview["rows"][0]
        self.assertEqual(row["fields"]["freight"], {"state": "known", "amount": "0", "currency": "CNY", "reason": None})
        tiers = row["fields"]["tier_prices"]
        self.assertEqual([t["min_quantity"] for t in tiers], [10, 50])
        self.assertEqual(tiers[0]["price"]["amount"], "145")
        self.assertEqual(row["status"], ROW_OK)

    def test_xlsx_preview_reads_inline_strings_and_numbers(self):
        payload = build_minimal_xlsx(
            [
                ["货号", "品名", "单价", "运费"],
                ["SUP-4001", "凳子", "80", "包邮"],
            ]
        )
        preview = self.service.preview_bytes(payload, kind="xlsx", filename="quote.xlsx")
        self.assertEqual(preview["rows"][0]["fields"]["sku"], "SUP-4001")
        self.assertEqual(preview["rows"][0]["fields"]["price"]["amount"], "80")

    def test_gbk_csv_decoded(self):
        csv_text = CSV_HEADER + "SUP-5001,中文品名,150,件,1,3,包邮,50cm\n"
        preview = self.service.preview_bytes(csv_text.encode("gbk"), kind="csv")
        self.assertEqual(preview["rows"][0]["fields"]["name"], "中文品名")

    def test_oversized_file_rejected(self):
        big = b"a" * (2 * 1024 * 1024 + 1)
        with self.assertRaises(ImportPreviewError):
            self.service.preview_bytes(big, kind="csv")

    def test_pasted_key_value_format(self):
        text = "货号=SUP-6001 品名=台面 单价=200 运费=包邮\n货号=SUP-6002 品名=支架 单价=90 运费=到付"
        preview = self.service.preview_pasted_text(text)
        self.assertEqual(preview["summary"]["total"], 2)
        self.assertEqual(preview["rows"][0]["fields"]["sku"], "SUP-6001")
        self.assertEqual(preview["rows"][1]["fields"]["freight"]["state"], "unknown")


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos, self.service = make_service(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _preview(self, text=None):
        text = text or (CSV_HEADER + "SUP-1001,不锈钢工作台,150,件,2,10,包邮,120x60x85cm\n")
        return self.service.preview_bytes(text.encode("utf-8"), kind="csv", filename="q.csv")

    def _confirm(self, preview, **overrides):
        params = dict(
            content_sha256=preview["content_sha256"],
            revision=preview["revision"],
            supplier="杭州五金供应商",
            operator="operator",
        )
        params.update(overrides)
        return self.service.confirm(preview["preview_id"], **params)

    def test_confirm_creates_offer_snapshot(self):
        preview = self._preview()
        result = self._confirm(preview)
        self.assertTrue(result["created"])
        snapshot = result["snapshots"][0]
        self.assertEqual(snapshot["provider_id"], "supplier_file:杭州五金供应商")
        self.assertEqual(snapshot["price_type"], "supplier_confirmed")
        self.assertEqual(snapshot["eligible_price"]["amount"], "150")
        self.assertEqual(snapshot["source_ref"], f"import_batch:{result['batch']['record_id']}")

    def test_same_file_reimport_is_idempotent(self):
        preview = self._preview()
        first = self._confirm(preview)
        preview2 = self._preview()
        second = self._confirm(preview2)
        self.assertFalse(second["created"])
        self.assertEqual(first["batch"]["record_id"], second["batch"]["record_id"])
        self.assertEqual(len(self.repos.offers.list()), 1)

    def test_new_version_of_same_sku_appends_and_latest_moves(self):
        self._confirm(self._preview())
        new_text = CSV_HEADER + "SUP-1001,不锈钢工作台,165,件,2,10,包邮,120x60x85cm\n"
        second = self._confirm(self._preview(new_text))
        self.assertTrue(second["created"])
        snapshots = self.repos.offers.list()
        self.assertEqual(len(snapshots), 2)
        latest = self.repos.offers.latest(
            provider_id="supplier_file:杭州五金供应商", source_item_id="SUP-1001"
        )
        self.assertEqual(latest["eligible_price"]["amount"], "165")

    def test_confirm_rejects_changed_content_hash(self):
        preview = self._preview()
        with self.assertRaises(ConfirmMismatch):
            self._confirm(preview, content_sha256="tampered")

    def test_confirm_rejects_wrong_revision(self):
        preview = self._preview()
        with self.assertRaises(ConfirmMismatch):
            self._confirm(preview, revision=99)

    def test_confirm_rejects_unknown_preview(self):
        preview = self._preview()
        with self.assertRaises(ConfirmMismatch):
            self.service.confirm(
                "prev_missing",
                content_sha256=preview["content_sha256"],
                revision=1,
                supplier="s",
                operator="o",
            )

    def test_unconfirmed_rows_excluded_by_default(self):
        text = CSV_HEADER + "SUP-1001,桌子,150,件,1,5,包邮,1m\nSUP-1002,椅子,,件,1,5,包邮,0.5m\n"
        preview = self.service.preview_bytes(text.encode("utf-8"), kind="csv")
        result = self._confirm(preview)
        self.assertEqual(len(result["snapshots"]), 1)
        self.assertEqual(result["snapshots"][0]["source_item_id"], "SUP-1001")

    def test_revoke_marks_batch_without_deleting_history(self):
        result = self._confirm(self._preview())
        batch_id = result["batch"]["record_id"]
        revoked = self.service.revoke_batch(batch_id, operator="operator", reason="wrong price list version")
        self.assertEqual(revoked["status"], "revoked")
        self.assertIsNotNone(revoked["revoked_at"])
        # snapshots remain as history
        self.assertEqual(len(self.repos.offers.list()), 1)
        # revoked batch no longer satisfies idempotent re-import
        preview = self._preview()
        again = self._confirm(preview)
        self.assertTrue(again["created"])

    def test_failed_batch_records_partial_ids(self):
        # simulate mid-failure by pointing at a repository whose add breaks
        # after the first snapshot
        class ExplodingOffers:
            def __init__(self, inner, fail_after):
                self.inner = inner
                self.fail_after = fail_after
                self.count = 0

            def add(self, snapshot):
                if self.count >= self.fail_after:
                    raise RuntimeError("disk full")
                self.count += 1
                return self.inner.add(snapshot)

            def list(self, **kwargs):
                return self.inner.list(**kwargs)

        original = self.service._repos.offers
        text = CSV_HEADER + "SUP-A,品,10,件,1,1,包邮,x\nSUP-B,品,11,件,1,1,包邮,x\nSUP-C,品,12,件,1,1,包邮,x\n"
        preview = self.service.preview_bytes(text.encode("utf-8"), kind="csv")
        self.service._repos.offers = ExplodingOffers(original, fail_after=2)  # type: ignore[assignment]
        with self.assertRaises(RuntimeError):
            self._confirm(preview)
        self.service._repos.offers = original  # type: ignore[assignment]
        batches = self.service.list_batches()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["status"], "failed")
        self.assertEqual(len(batches[0]["snapshot_ids"]), 2)
        # the partial snapshots stay as history and the batch can be revoked
        self.service.revoke_batch(batches[0]["record_id"], operator="o", reason="partial import after failure")
        self.assertEqual(self.service.list_batches()[0]["status"], "revoked")


if __name__ == "__main__":
    unittest.main()
