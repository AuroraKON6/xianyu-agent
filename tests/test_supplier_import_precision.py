"""Commercial input must not gain precision or change units while parsing."""
import csv
import io
import tempfile
import unittest
from pathlib import Path

from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.supplier_import import (
    ImportPreviewError, ROW_NEEDS_CONFIRMATION, ROW_OK, ROW_REJECTED, SupplierImportService,
)


class SupplierInputPrecisionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repos = JsonCommerceRepositories(self.root)
        self.service = SupplierImportService(self.repos, self.root)

    def preview(self, **changes):
        fields = dict(sku="SOURCE-001", name="Table", price="150", unit="pc", moq="1",
                      stock="10", freight="15", spec="120x60x85cm")
        fields.update(changes)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(fields))
        writer.writeheader()
        writer.writerow(fields)
        return self.service.preview_bytes(output.getvalue().encode(), kind="csv")

    def confirm(self, preview, **kwargs):
        return self.service.confirm(preview["preview_id"], content_sha256=preview["content_sha256"],
                                    revision=preview["revision"], supplier="isolated-fixture-supplier",
                                    operator="fixture", **kwargs)

    def test_row_numbers_are_not_overwritten_by_prices(self):
        preview = self.service.preview_bytes(b"sku,price,freight\nA,150,0\nB,200,0\n", kind="csv")
        self.assertEqual([r["row_number"] for r in preview["rows"]], [1, 2])

    def test_ambiguous_prices_remain_unknown(self):
        for price in ("150-200", "150/200", "1e3", "about 150", "150+", "150 or 200"):
            with self.subTest(price=price):
                row = self.preview(price=price)["rows"][0]
                self.assertEqual(row["fields"]["price"]["state"], "unknown")
                self.assertEqual(row["status"], ROW_NEEDS_CONFIRMATION)

    def test_foreign_currency_text_is_not_relabelled_cny(self):
        for price in ("USD 150", "$150", "EUR 150", "150 USD"):
            with self.subTest(price=price):
                self.assertEqual(self.preview(price=price)["rows"][0]["fields"]["price"]["state"], "unknown")

    def test_explicit_currency_column_cannot_be_ignored(self):
        row = self.preview(currency="USD")["rows"][0]
        self.assertEqual(row["fields"]["price"]["state"], "unknown")
        self.assertEqual(row["status"], ROW_NEEDS_CONFIRMATION)
        self.assertIn("currency", row["raw"])

    def test_explicit_cny_and_grouped_decimal_prices_still_work(self):
        for price in ("CNY 1,500.25", "1500.25 CNY", "1,500.25"):
            with self.subTest(price=price):
                row = self.preview(price=price, currency="CNY")["rows"][0]
                self.assertEqual(row["fields"]["price"]["amount"], "1500.25")
                self.assertEqual(row["status"], ROW_OK)

    def test_invalid_comma_grouping_is_not_silently_removed(self):
        for price in ("15,50", "1,00,000", "1,,500"):
            with self.subTest(price=price):
                self.assertEqual(self.preview(price=price)["rows"][0]["fields"]["price"]["state"], "unknown")

    def test_fractional_and_ambiguous_stock_is_not_rounded(self):
        for stock in ("1.5", "1e3", "10-20", "more than 10"):
            with self.subTest(stock=stock):
                row = self.preview(stock=stock)["rows"][0]
                self.assertIsNone(row["fields"]["stock_quantity"])
                self.assertEqual(row["fields"]["stock_state"], "unknown")
                self.assertEqual(row["status"], ROW_NEEDS_CONFIRMATION)

    def test_fractional_and_zero_moq_cannot_create_a_valid_minimum(self):
        for moq in ("1.5", "0", "1-5"):
            with self.subTest(moq=moq):
                row = self.preview(moq=moq)["rows"][0]
                self.assertIsNone(row["fields"]["moq"])
                self.assertEqual(row["status"], ROW_NEEDS_CONFIRMATION)

    def test_negative_freight_rejects_row_not_entire_file(self):
        preview = self.service.preview_bytes(b"sku,price,freight\nA,150,-10\nB,200,15\n", kind="csv")
        self.assertEqual([r["status"] for r in preview["rows"]], [ROW_REJECTED, ROW_OK])
        result = self.confirm(preview)
        self.assertEqual([s["source_sku_id"] for s in result["snapshots"]], ["B"])

    def test_stock_negation_is_not_substring_matched_as_available(self):
        row = self.preview(stock="not in stock; 10 expected")["rows"][0]
        self.assertIsNone(row["fields"]["stock_quantity"])
        self.assertEqual(row["fields"]["stock_state"], "unknown")

    def test_invalid_case_sizes_never_produce_a_known_unit_price(self):
        for size in ("0", "-6", "1.5", "6-10"):
            with self.subTest(size=size):
                row = self.preview(price="720", unit="case", pieces_per_case=size)["rows"][0]
                self.assertEqual(row["fields"]["price"]["state"], "unknown")
                self.assertNotEqual(row["status"], ROW_OK)

    def test_case_price_normalizes_price_unit_but_does_not_guess_quantity_units(self):
        row = self.preview(price="720", unit="case", pieces_per_case="6")["rows"][0]
        self.assertEqual(row["fields"]["price"]["amount"], "120")
        self.assertEqual(row["fields"]["unit"], "pc")
        self.assertEqual(row["fields"]["source_unit"], "case")
        self.assertIsNone(row["fields"]["stock_quantity"])
        self.assertIsNone(row["fields"]["moq"])
        self.assertEqual(row["fields"]["stock_state"], "unknown")
        self.assertEqual(row["status"], ROW_NEEDS_CONFIRMATION)

    def test_case_quantities_convert_only_with_explicit_case_quantity_unit(self):
        row = self.preview(price="720", unit="case", pieces_per_case="6", quantity_unit="case",
                           moq="2", stock="10")["rows"][0]
        self.assertEqual(row["fields"]["moq"], 12)
        self.assertEqual(row["fields"]["stock_quantity"], 60)
        self.assertEqual(row["fields"]["unit"], "pc")
        self.assertEqual(row["status"], ROW_OK)

    def test_case_with_explicit_piece_counts_keeps_counts_and_archives_conversion(self):
        preview = self.preview(price="720", unit="case", pieces_per_case="6", quantity_unit="pc")
        row = preview["rows"][0]
        self.assertEqual(row["fields"]["stock_quantity"], 10)
        result = self.confirm(preview)
        snapshot = result["snapshots"][0]
        self.assertEqual(snapshot["unit"], "pc")
        self.assertEqual(snapshot["attributes"]["import_basis"]["source_unit"], "case")
        self.assertEqual(snapshot["attributes"]["import_basis"]["pieces_per_case"], 6)

    def test_ambiguous_freight_and_tier_values_stay_unconfirmed(self):
        row = self.preview(freight="10-20", tier="10:140;20:130USD")["rows"][0]
        self.assertEqual(row["fields"]["freight"]["state"], "unknown")
        self.assertEqual(row["fields"]["tier_prices"], [])
        self.assertEqual(row["status"], ROW_NEEDS_CONFIRMATION)

    def test_needs_confirmation_cannot_turn_unknown_into_zero(self):
        preview = self.preview(price="USD 150", stock="1.5", freight="10-20")
        with self.assertRaises(ImportPreviewError):
            self.confirm(preview)
        snapshot = self.confirm(preview, include_needs_confirmation=True)["snapshots"][0]
        self.assertEqual(snapshot["eligible_price"]["state"], "unknown")
        self.assertEqual(snapshot["shipping_quote"]["state"], "unknown")
        self.assertIsNone(snapshot["stock_quantity"])

    def test_case_unit_is_normalized_even_when_price_is_unknown(self):
        row = self.preview(price="pending", unit="case", pieces_per_case="6", quantity_unit="case")["rows"][0]
        self.assertEqual(row["fields"]["unit"], "pc")
        self.assertEqual(row["fields"]["stock_quantity"], 60)
        self.assertEqual(row["fields"]["price"]["state"], "unknown")

    def test_duplicate_unmapped_columns_cannot_shift_financial_fields(self):
        with self.assertRaises(ImportPreviewError):
            self.service.preview_bytes(b"sku,other,other,price,freight\nA,x,y,150,15\n", kind="csv")

    def test_extra_csv_cells_are_not_silently_discarded(self):
        with self.assertRaises(ImportPreviewError):
            self.service.preview_bytes(b"sku,price,freight\nA,1,500,15\n", kind="csv")


if __name__ == "__main__":
    unittest.main()
