"""Supplier quote-file import: preview -> confirm (WP03).

First deliverable data channel of the commerce pipeline: an operator
brings a real supplier quote file (CSV, XLSX or pasted structured
text), the system parses it deterministically into candidate offer
fields, shows every raw value next to its suggestion with confidence,
missing items and duplicates, and only a separate ``confirm`` turns
previewed rows into immutable OfferSnapshots.

Rules (WP03 backlog):
- Two steps only: ``preview_*`` returns an ephemeral preview bound to
  the content SHA-256 and a revision; ``confirm`` must repeat that hash
  and revision, otherwise the file changed after preview and the
  confirm is refused.
- Mapping is deterministic alias matching. The system never invents
  stock, freight, authorization or any missing money value: unknown
  stays ``unknown`` with a reason and the row stays a draft
  (``needs_confirmation``).
- Negative prices/quantities are rejected rows, not warnings.
- Case-vs-piece pricing is converted only when pieces-per-case is
  known; otherwise the per-piece price is ``unknown`` with a reason.
- Re-importing the same content for the same supplier is idempotent
  (returns the existing batch); a changed file for the same SKUs
  appends new snapshots and ``latest`` moves forward -- history is
  never overwritten.
- Batches can be revoked. Revocation marks the batch and its snapshot
  references revoked without deleting any historical record.

XLSX is parsed with the standard library (zipfile + ElementTree): cell
values only, no formula evaluation. A formula cell without a cached
value is surfaced as a needs_confirmation issue, never guessed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from .commerce.contracts import (
    PRICE_STATE_KNOWN,
    PRICE_STATE_UNKNOWN,
    Money,
    build_offer_snapshot,
    utc_now,
)
from .commerce.repositories import JsonCommerceRepositories, LockedJsonList
from .quote_provenance import source_time

IMPORT_KIND_CSV = "csv"
IMPORT_KIND_XLSX = "xlsx"
IMPORT_KIND_PASTED = "pasted_text"

MAX_IMPORT_BYTES = 2 * 1024 * 1024
PREVIEW_TTL_SECONDS = 30 * 60

ROW_OK = "ok"
ROW_NEEDS_CONFIRMATION = "needs_confirmation"
ROW_REJECTED = "rejected"

# ---------------------------------------------------------------------------
# Deterministic column aliases. Exact canonical names score 1.0, aliases 0.9.
# Anything unmapped keeps its raw value in the preview and is never guessed.
# ---------------------------------------------------------------------------

COLUMN_ALIASES: Dict[str, Sequence[str]] = {
    "sku": ("sku", "编码", "商品编码", "货号", "型号", "编号", "item no", "itemno"),
    "name": ("name", "品名", "商品名称", "名称", "product"),
    "price": ("price", "单价", "采购价", "供货价", "价格", "报价", "unit price"),
    "currency": ("currency", "币种", "货币", "报价币种"),
    "unit": ("unit", "单位", "销售单位", "计量单位"),
    "quantity_unit": ("quantity_unit", "数量单位", "库存起订单位"),
    "pieces_per_case": ("装箱量", "每箱数量", "箱规", "装箱数", "pieces per case"),
    "moq": ("moq", "起订量", "起订", "最小起订量"),
    "stock": ("stock", "库存", "库存量", "现货量", "可发货数量"),
    "freight": ("freight", "运费", "邮费", "shipping"),
    "spec": ("spec", "规格", "尺寸", "specification"),
    "brand": ("brand", "品牌"),
    "tier": ("tier", "阶梯价", "阶梯价格", "批发价"),
    "condition": ("condition", "成色"),
    "delivery": ("delivery", "发货时效", "出库时效", "发货时间"),
    "notes": ("notes", "备注", "remark"),
    "source_kind": ("source_kind", "来源类型"),
    "source_reference": ("source_reference", "origin_ref", "来源引用", "原始报价引用"),
    "source_observed_at": ("source_observed_at", "quoted_at", "实际观察时间", "报价确认时间"),
    "valid_until": ("valid_until", "expires_at", "报价有效期", "有效截止时间"),
}

CANONICAL_COLUMNS = tuple(COLUMN_ALIASES)


class ImportPreviewError(ValueError):
    """Invalid import payload or mapping problems the caller must fix."""


class ConfirmMismatch(ValueError):
    """Confirm inputs do not match the preview (hash/revision/expiry)."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _decode_text(payload: bytes) -> str:
    if isinstance(payload, str):
        return payload
    if len(payload) > MAX_IMPORT_BYTES:
        raise ImportPreviewError(f"import exceeds {MAX_IMPORT_BYTES} bytes")
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportPreviewError("could not decode file as utf-8 or gbk")


def _norm_header(value: str) -> str:
    return re.sub(r"\s+", "", str(value).strip().lower())


def map_headers(headers: Sequence[str]) -> Tuple[Dict[int, Tuple[str, float]], List[str]]:
    """Map header cells to canonical fields.

    Returns ({column index: (field, confidence)}, unmapped headers).
    Duplicate headers for the same canonical field are rejected by the
    caller.
    """
    mapping: Dict[int, Tuple[str, float]] = {}
    unmapped: List[str] = []
    for index, header in enumerate(headers):
        norm = _norm_header(header)
        target = None
        confidence = 0.0
        for field_name, aliases in COLUMN_ALIASES.items():
            if norm == field_name:
                target, confidence = field_name, 1.0
                break
            if norm in aliases:
                target, confidence = field_name, 0.9
                break
        if target is None:
            unmapped.append(str(header))
        else:
            mapping[index] = (target, confidence)
    return mapping, unmapped


_NUMBER_TEXT = r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_NUMBER_RE = re.compile(_NUMBER_TEXT)
_MONEY_RE = re.compile(
    r"(?:(?:每箱|整箱|一箱)\s*)?(?:(?:CNY|RMB|人民币|￥|¥)\s*)?"
    r"(?P<amount>" + _NUMBER_TEXT + r")\s*(?:CNY|RMB|元)?\s*(?:/箱|每箱)?",
    re.IGNORECASE,
)
_PER_CASE_HINTS = ("每箱", "/箱", "整箱", "一箱")
_CASE_UNITS = ("箱", "整箱", "case")
_PIECE_UNITS = ("件", "个", "pc", "piece")
_CNY_LABELS = ("CNY", "RMB", "人民币", "元")


def _parse_number(raw: Any) -> Optional[Decimal]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    match = _NUMBER_RE.fullmatch(text)
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _parse_integer(raw: Any) -> Optional[int]:
    number = _parse_number(raw)
    if number is None or number != number.to_integral_value():
        return None
    try:
        return int(number)
    except (ValueError, ArithmeticError):
        return None


def _parse_money_cell(raw: Any) -> Money:
    """Parse a money cell; unknown stays unknown with a reason."""
    if raw is None:
        return Money.unknown("cell empty")
    text = str(raw).strip()
    if not text:
        return Money.unknown("cell empty")
    match = _MONEY_RE.fullmatch(text)
    if match is None:
        return Money.unknown(f"unparseable value: {text!r}")
    number = _parse_number(match.group("amount"))
    if number < 0:
        raise ImportPreviewError(f"negative amount not allowed: {text!r}")
    return Money.known(number)


def _parse_freight(raw: Any) -> Money:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return Money.unknown("freight not stated")
    if text in ("包邮", "免运费", "含运费", "free shipping"):
        return Money.known(Decimal("0"))
    if text in ("到付", "运费到付", "freight collect"):
        return Money.unknown("freight collect; amount unknown")
    return _parse_money_cell(raw)


_STOCK_IN_WORDS = ("现货", "有货", "充足")
_STOCK_OUT_WORDS = ("无货", "缺货", "售罄", "停产")


def _parse_stock(raw: Any) -> Tuple[str, Optional[int]]:
    if raw is None:
        return ("unknown", None)
    text = str(raw).strip()
    if not text:
        return ("unknown", None)
    number = _parse_integer(text)
    if number is not None:
        state = "out_of_stock" if number <= 0 else "in_stock"
        return (state, number)
    if text in _STOCK_IN_WORDS:
        return ("in_stock", None)
    if text in _STOCK_OUT_WORDS:
        return ("out_of_stock", None)
    return ("unknown", None)


def _parse_tier_prices(raw: Any) -> List[Dict[str, Any]]:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return []
    tiers: List[Dict[str, Any]] = []
    for chunk in re.split(r"[;；,，]", text):
        match = re.fullmatch(r"\s*([0-9]+)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*", chunk)
        if not match or int(match.group(1)) <= 0 or any(t["min_quantity"] == int(match.group(1)) for t in tiers):
            raise ImportPreviewError("ambiguous tier prices; provide unique positive quantity:price pairs")
        tiers.append(
            {
                "min_quantity": int(match.group(1)),
                "price": Money.known(Decimal(match.group(2))).to_dict(),
            }
        )
    tiers.sort(key=lambda t: t["min_quantity"])
    return tiers


# ---------------------------------------------------------------------------
# XLSX reading (values only, standard library)
# ---------------------------------------------------------------------------

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_sheet_rows(payload: bytes) -> List[List[str]]:
    """Read the first worksheet of an xlsx as string cells.

    Formula cells appear as their cached value when present; a formula
    without a cached value becomes ``=...`` and is flagged per row by
    the parser.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportPreviewError(f"not a readable xlsx: {exc}") from exc
    with archive:
        names = archive.namelist()
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        sheet_names = [n for n in names if re.fullmatch(r"xl/worksheets/sheet1?\.xml", n)]
        if not sheet_names:
            sheet_names = sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        if not sheet_names:
            raise ImportPreviewError("xlsx has no worksheet")
        root = ElementTree.fromstring(archive.read(sheet_names[0]))
        rows: List[List[str]] = []
        for row in root.iter(f"{_NS}row"):
            cells: List[str] = []
            for cell in row.findall(f"{_NS}c"):
                cell_type = cell.get("t", "n")
                if cell_type == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(f"{_NS}t"))
                else:
                    value_node = cell.find(f"{_NS}v")
                    value = value_node.text if value_node is not None and value_node.text else ""
                    if cell_type == "s" and value != "":
                        value = shared[int(value)]
                    elif cell_type == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                formula_node = cell.find(f"{_NS}f")
                if formula_node is not None and not value:
                    formula_text = formula_node.text or ""
                    value = f"={formula_text}"
                cells.append(value.strip())
            rows.append(cells)
        return rows


# ---------------------------------------------------------------------------
# Pasted structured text: header+pipe rows, or key=value lines
# ---------------------------------------------------------------------------


def _parse_pasted(text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        raise ImportPreviewError("pasted text is empty")
    if "|" in lines[0] and "=" not in lines[0]:
        headers = [cell.strip() for cell in lines[0].split("|")]
        rows: List[Dict[str, str]] = []
        for line in lines[1:]:
            cells = [cell.strip() for cell in line.split("|")]
            cells += [""] * (len(headers) - len(cells))
            rows.append(dict(zip(headers, cells)))
        return headers, rows
    # key=value pairs: one item per line, pairs separated by whitespace or ;
    kv_line = re.compile(r"([\w\u4e00-\u9fff]+)\s*[=:：]\s*([^;；\s]+)")
    rows = []
    headers_seen: List[str] = []
    for line in lines:
        pairs = kv_line.findall(line)
        if not pairs:
            continue
        row: Dict[str, str] = {}
        for key, value in pairs:
            row[key] = value
            if key not in headers_seen:
                headers_seen.append(key)
        rows.append(row)
    if not rows:
        raise ImportPreviewError("pasted text has no parsable key=value rows")
    return headers_seen, rows


# ---------------------------------------------------------------------------
# Preview / confirm service
# ---------------------------------------------------------------------------


class SupplierImportService:
    def __init__(self, repos: JsonCommerceRepositories, data_dir: Path) -> None:
        self._repos = repos
        self._batches = LockedJsonList(Path(data_dir) / "commerce" / "import_batches.json")
        self._previews: Dict[str, Dict[str, Any]] = {}

    # ---- preview ----

    def preview_bytes(self, payload: bytes, *, kind: str, filename: str = "") -> Dict[str, Any]:
        if len(payload) > MAX_IMPORT_BYTES:
            raise ImportPreviewError(f"import exceeds {MAX_IMPORT_BYTES} bytes")
        content_sha256 = _sha256_bytes(payload)
        if kind == IMPORT_KIND_CSV:
            headers, raw_rows = self._read_csv(payload)
        elif kind == IMPORT_KIND_XLSX:
            headers, raw_rows = self._read_xlsx(payload)
        else:
            raise ImportPreviewError(f"unsupported kind for byte preview: {kind}")
        return self._build_preview(kind, filename, content_sha256, headers, raw_rows)

    def preview_pasted_text(self, text: str) -> Dict[str, Any]:
        if not text.strip():
            raise ImportPreviewError("pasted text is empty")
        payload = text.encode("utf-8")
        if len(payload) > MAX_IMPORT_BYTES:
            raise ImportPreviewError(f"import exceeds {MAX_IMPORT_BYTES} bytes")
        first_line = next(line for line in text.splitlines() if line.strip())
        # A delimited header wins over ':' inside ISO timestamps in data rows.
        for delimiter in (",", ";", "\t"):
            cells = next(csv.reader([first_line], delimiter=delimiter))
            mapping, _ = map_headers(cells)
            if len(cells) > 1 and any(field == "sku" for field, _ in mapping.values()):
                headers, raw_rows = self._read_csv(payload)
                return self._build_preview(IMPORT_KIND_PASTED, "", _sha256_bytes(payload), headers, raw_rows)
        try:
            headers, raw_rows = _parse_pasted(text)
        except ImportPreviewError:
            # plain CSV paste is equally valid structured text
            headers, raw_rows = self._read_csv(payload)
        return self._build_preview(IMPORT_KIND_PASTED, "", _sha256_bytes(payload), headers, raw_rows)

    def _read_csv(self, payload: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
        text = _decode_text(payload)
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        if not rows:
            raise ImportPreviewError("csv has no non-empty rows")
        headers = [cell.strip() for cell in rows[0]]
        raw_rows = []
        for row_number, values in enumerate(rows[1:], start=1):
            if len(values) > len(headers):
                raise ImportPreviewError(f"row {row_number} has extra cells; quote values containing delimiters")
            values += [""] * (len(headers) - len(values))
            raw_rows.append({header: value.strip() for header, value in zip(headers, values)})
        return headers, raw_rows

    def _read_xlsx(self, payload: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
        grid = _xlsx_sheet_rows(payload)
        grid = [row for row in grid if any(cell.strip() for cell in row)]
        if not grid:
            raise ImportPreviewError("xlsx has no non-empty rows")
        width = max(len(row) for row in grid)
        headers = [cell.strip() for cell in grid[0]]
        headers += [""] * (width - len(headers))
        raw_rows = []
        for values in grid[1:]:
            values += [""] * (width - len(values))
            raw_rows.append({header: value.strip() for header, value in zip(headers, values)})
        return headers, raw_rows

    def _build_preview(
        self,
        kind: str,
        filename: str,
        content_sha256: str,
        headers: List[str],
        raw_rows: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        normalized_headers = [_norm_header(header) for header in headers]
        if len(set(normalized_headers)) != len(normalized_headers):
            raise ImportPreviewError("duplicate columns are not allowed, including unmapped headers")
        mapping, unmapped = map_headers(headers)
        mapped_fields = [field for field, _ in mapping.values()]
        duplicates = {field for field in mapped_fields if mapped_fields.count(field) > 1}
        if duplicates:
            raise ImportPreviewError(f"duplicate columns for: {', '.join(sorted(duplicates))}")

        issues_global: List[str] = []
        if unmapped:
            issues_global.append(f"unmapped columns ignored: {', '.join(unmapped)}")
        if "sku" not in mapped_fields:
            raise ImportPreviewError("no sku column found; add one of: " + ", ".join(COLUMN_ALIASES["sku"]))

        seen_skus: Dict[str, int] = {}
        rows_out: List[Dict[str, Any]] = []
        for number, raw_row in enumerate(raw_rows, start=1):
            parsed = self._parse_row(number, raw_row, mapping)
            sku = parsed["fields"]["sku"]
            if sku and sku in seen_skus:
                parsed["issues"].append(f"duplicate sku in file (first seen row {seen_skus[sku]})")
                parsed["status"] = ROW_REJECTED
            elif sku:
                seen_skus[sku] = number
            rows_out.append(parsed)

        preview_id = f"prev_{uuid4().hex[:16]}"
        summary = {
            "total": len(rows_out),
            ROW_OK: sum(1 for r in rows_out if r["status"] == ROW_OK),
            ROW_NEEDS_CONFIRMATION: sum(1 for r in rows_out if r["status"] == ROW_NEEDS_CONFIRMATION),
            ROW_REJECTED: sum(1 for r in rows_out if r["status"] == ROW_REJECTED),
        }
        preview = {
            "preview_id": preview_id,
            "revision": 1,
            "kind": kind,
            "filename": filename,
            "content_sha256": content_sha256,
            "unmapped_columns": unmapped,
            "issues": issues_global,
            "rows": rows_out,
            "summary": summary,
            "created_at": utc_now().isoformat(),
            "expires_at_timestamp": utc_now().timestamp() + PREVIEW_TTL_SECONDS,
        }
        self._previews[preview_id] = preview
        return preview

    def _parse_row(self, number: int, raw_row: Mapping[str, str], mapping: Mapping[int, Tuple[str, float]]) -> Dict[str, Any]:
        issues: List[str] = []

        # raw_row is keyed by original header text in column order; map
        # positional column indexes to canonical field names
        header_order = list(raw_row.keys())
        ordered_raw: Dict[str, str] = {}
        confidence_sum = 0.0
        confidence_count = 0
        for index, (field_name, confidence) in mapping.items():
            if index < len(header_order):
                ordered_raw[field_name] = str(raw_row.get(header_order[index], "") or "")
            confidence_sum += confidence
            confidence_count += 1

        price_raw = ordered_raw.get("price", "")
        per_case_price = any(hint in price_raw for hint in _PER_CASE_HINTS)

        rejected = False
        needs_confirmation = False
        price_money = Money.unknown("price column missing or empty")
        if price_raw:
            try:
                price_money = _parse_money_cell(price_raw)
            except ImportPreviewError as exc:
                rejected = True
                issues.append(str(exc))
                price_money = Money.unknown(str(exc))
        unit = ordered_raw.get("unit", "").strip()
        source_unit = unit
        quantity_unit = ordered_raw.get("quantity_unit", "").strip().lower()
        pieces_raw = ordered_raw.get("pieces_per_case", "")
        pieces_per_case = _parse_integer(pieces_raw) if pieces_raw else None
        case_priced = per_case_price or unit.lower() in _CASE_UNITS
        if pieces_raw and (pieces_per_case is None or pieces_per_case <= 0):
            pieces_per_case = None
            needs_confirmation = True
            issues.append("pieces_per_case must be an explicit positive integer")
        if case_priced:
            if pieces_per_case:
                unit = "pc" if source_unit.lower() == "case" else "件"
            if pieces_per_case and price_money.state == PRICE_STATE_KNOWN:
                price_money = Money.known(price_money.amount / Decimal(pieces_per_case))  # type: ignore[union-attr]
                issues.append(
                    f"case price converted to per-piece using pieces_per_case={pieces_per_case}"
                )
            elif price_money.state == PRICE_STATE_KNOWN:
                price_money = Money.unknown("case price without pieces_per_case; per-piece price unknown")
                issues.append("case price given but no 装箱量 column; cannot compute per-piece price")

        stock_state, stock_quantity = _parse_stock(ordered_raw.get("stock", ""))
        moq_raw = ordered_raw.get("moq", "")
        moq = _parse_integer(moq_raw) if moq_raw else None
        for field_name, raw, value in (("stock", ordered_raw.get("stock", ""), stock_quantity), ("moq", moq_raw, moq)):
            parsed_number = _parse_number(raw)
            if parsed_number is not None and parsed_number < 0:
                rejected = True
                issues.append(f"negative {field_name}: {raw}")
            elif raw and ((field_name == "moq" and (value is None or value <= 0))
                          or (field_name == "stock" and stock_state == "unknown")):
                needs_confirmation = True
                issues.append(f"ambiguous {field_name}; provide an explicit integer quantity")
        if moq is not None and moq <= 0:
            moq = None
        try:
            freight_money = _parse_freight(ordered_raw.get("freight", ""))
        except ImportPreviewError as exc:
            rejected = True
            issues.append(str(exc))
            freight_money = Money.unknown(str(exc))
        try:
            tiers = _parse_tier_prices(ordered_raw.get("tier", ""))
        except ImportPreviewError as exc:
            needs_confirmation = True
            issues.append(str(exc))
            tiers = []

        if case_priced:
            if pieces_per_case and quantity_unit in _CASE_UNITS + _PIECE_UNITS:
                factor = pieces_per_case if quantity_unit in _CASE_UNITS else 1
                moq = moq * factor if moq is not None else None
                stock_quantity = stock_quantity * factor if stock_quantity is not None else None
                for tier in tiers:
                    tier["min_quantity"] *= factor
                    tier["price"] = Money.known(Decimal(tier["price"]["amount"]) / pieces_per_case).to_dict()
            else:
                # A case price does not establish whether inventory/MOQ count
                # pieces or cases. Never carry ambiguous counts into allocation.
                stock_state, stock_quantity, moq, tiers = "unknown", None, None, []
                needs_confirmation = True
                issues.append("case pricing requires explicit quantity_unit and positive pieces_per_case")
        elif quantity_unit and quantity_unit != unit.lower() and not (quantity_unit in _PIECE_UNITS and unit.lower() in _PIECE_UNITS):
            stock_state, stock_quantity, moq, tiers = "unknown", None, None, []
            needs_confirmation = True
            issues.append("quantity_unit differs from price unit; confirm units before use")

        currency = ordered_raw.get("currency", "").strip().upper()
        if currency and currency not in _CNY_LABELS:
            reason = f"unsupported currency: {currency}; no exchange rate is assumed"
            price_money = Money.unknown(reason)
            freight_money = Money.unknown(reason)
            tiers = []
            needs_confirmation = True
            issues.append(reason)

        parsed_fields = {
            "sku": ordered_raw.get("sku", "").strip(),
            "name": ordered_raw.get("name", "").strip(),
            "price": price_money.to_dict(),
            "unit": unit,
            "source_unit": source_unit,
            "quantity_unit": quantity_unit,
            "currency": currency or "CNY",
            "pieces_per_case": pieces_per_case,
            "moq": moq,
            "stock_state": stock_state,
            "stock_quantity": stock_quantity,
            "freight": freight_money.to_dict(),
            "spec": ordered_raw.get("spec", "").strip(),
            "brand": ordered_raw.get("brand", "").strip(),
            "tier_prices": tiers,
            "condition": ordered_raw.get("condition", "").strip(),
            "delivery": ordered_raw.get("delivery", "").strip(),
            "notes": ordered_raw.get("notes", "").strip(),
            "quote_provenance": {
                "schema_version": 1,
                "source_kind": ordered_raw.get("source_kind", "unknown").strip().lower() or "unknown",
                "source_reference": ordered_raw.get("source_reference", "").strip(),
                "source_observed_at": ordered_raw.get("source_observed_at", "").strip(),
                "valid_until": ordered_raw.get("valid_until", "").strip(),
            },
        }

        status = ROW_NEEDS_CONFIRMATION if needs_confirmation else ROW_OK
        if not parsed_fields["sku"]:
            status = ROW_REJECTED
            issues.append("missing sku")
        if rejected:
            status = ROW_REJECTED
        if parsed_fields["price"]["state"] != PRICE_STATE_KNOWN and status != ROW_REJECTED:
            status = ROW_NEEDS_CONFIRMATION
            issues.append(parsed_fields["price"].get("reason") or "price unknown")
        if parsed_fields["freight"]["state"] != PRICE_STATE_KNOWN and status == ROW_OK:
            status = ROW_NEEDS_CONFIRMATION
        if any(v.startswith("=") for v in ordered_raw.values() if v):
            issues.append("formula cell without cached value; confirm the number with the supplier")
            if status == ROW_OK:
                status = ROW_NEEDS_CONFIRMATION

        negative_checks = {"moq": parsed_fields["moq"], "stock_quantity": parsed_fields["stock_quantity"]}
        for field_name, value in negative_checks.items():
            if value is not None and value < 0:
                status = ROW_REJECTED
                issues.append(f"negative {field_name}: {value}")

        return {
            "row_number": number,
            "raw": ordered_raw,
            "fields": parsed_fields,
            "confidence": round(confidence_sum / confidence_count, 2) if confidence_count else 0.0,
            "issues": issues,
            "status": status,
        }

    # ---- confirm ----

    def confirm(
        self,
        preview_id: str,
        *,
        content_sha256: str,
        revision: int,
        supplier: str,
        operator: str,
        observed_at: Optional[datetime] = None,
        include_needs_confirmation: bool = False,
    ) -> Dict[str, Any]:
        preview = self._previews.get(preview_id)
        if preview is None:
            raise ConfirmMismatch(f"preview not found or expired: {preview_id}")
        if utc_now().timestamp() > float(preview["expires_at_timestamp"]):
            del self._previews[preview_id]
            raise ConfirmMismatch("preview expired; re-run preview")
        if preview["content_sha256"] != content_sha256:
            raise ConfirmMismatch("content hash mismatch; file changed after preview")
        if int(preview["revision"]) != int(revision):
            raise ConfirmMismatch("preview revision mismatch")

        supplier = supplier.strip()
        if not supplier:
            raise ImportPreviewError("supplier name is required")
        provider_id = f"supplier_file:{supplier}"

        # idempotent re-import of identical content for the same supplier
        # (a revoked batch no longer satisfies idempotency)
        existing = self._batches.find(
            lambda b: str(b.get("content_sha256")) == content_sha256
            and str(b.get("provider_id")) == provider_id
            and str(b.get("status")) == "committed"
        )
        if existing:
            return {"batch": existing[0], "created": False, "snapshots": self._snapshots_for(existing[0])}

        wanted = [row for row in preview["rows"] if row["status"] == ROW_OK]
        if include_needs_confirmation:
            wanted += [row for row in preview["rows"] if row["status"] == ROW_NEEDS_CONFIRMATION]
        if not wanted:
            raise ImportPreviewError("no confirmable rows (all rejected or unconfirmed)")

        imported_at = utc_now()
        batch_id = f"impb_{uuid4().hex[:16]}"
        snapshots: List[Dict[str, Any]] = []
        try:
            for row in wanted:
                fields = row["fields"]
                provenance = dict(fields["quote_provenance"])
                if not provenance["source_observed_at"] and observed_at is not None:
                    provenance["source_observed_at"] = observed_at.isoformat()
                observed = source_time(provenance["source_observed_at"])
                # The required legacy timestamp may fall back to import time,
                # but eligibility always checks the separately retained source time.
                observed = observed or imported_at
                expiry = source_time(provenance["valid_until"])
                price = Money.from_dict(fields["price"])
                freight = Money.from_dict(fields["freight"])
                payload_hash = _sha256_bytes(
                    repr(
                        [
                            provider_id,
                            fields["sku"],
                            row["raw"],
                            price.to_dict(),
                            freight.to_dict(),
                            provenance,
                        ]
                    ).encode("utf-8")
                )
                snapshot = build_offer_snapshot(
                    provider_id=provider_id,
                    source_item_id=fields["sku"],
                    source_sku_id=fields["sku"],
                    observed_at=observed,
                    fetched_at=imported_at,
                    expires_at=expiry,
                    unit=fields["unit"] or "件",
                    quantity=None,
                    condition=fields["condition"],
                    attributes={
                        key: value
                        for key, value in (
                            ("name", fields["name"]),
                            ("spec", fields["spec"]),
                            ("brand", fields["brand"]),
                            ("delivery", fields["delivery"]),
                            ("notes", fields["notes"]),
                            ("quote_provenance", provenance),
                            ("import_basis", {
                                "source_unit": fields["source_unit"],
                                "quantity_unit": fields["quantity_unit"],
                                "pieces_per_case": fields["pieces_per_case"],
                                "currency": fields["currency"],
                                "currency_assumed": not bool(row["raw"].get("currency")),
                            }),
                        )
                        if value
                    },
                    price_type="supplier_confirmed",
                    eligible_price=price.to_dict(),
                    shipping_quote=freight.to_dict(),
                    stock_state=fields["stock_state"],
                    stock_quantity=fields["stock_quantity"],
                    moq=fields["moq"],
                    tier_prices=fields["tier_prices"],
                    tax_mode="unknown",
                    seller_ref=supplier,
                    payload_hash=payload_hash,
                    account_id="",
                    source_ref=f"import_batch:{batch_id}",
                )
                stored, _created = self._repos.offers.add(snapshot)
                snapshots.append(stored)
        except Exception as exc:  # mid-failure: record partial batch, mark failed
            batch = {
                "record_id": batch_id,
                "record_type": "supplier_import_batch",
                "provider_id": provider_id,
                "supplier": supplier,
                "operator": operator,
                "content_sha256": content_sha256,
                "preview_id": preview_id,
                "status": "failed",
                "error": str(exc),
                "snapshot_ids": [s["record_id"] for s in snapshots],
                "created_at": utc_now().isoformat(),
                "revoked_at": None,
            }
            self._batches.append(batch)
            raise

        batch = {
            "record_id": batch_id,
            "record_type": "supplier_import_batch",
            "provider_id": provider_id,
            "supplier": supplier,
            "operator": operator,
            "content_sha256": content_sha256,
            "preview_id": preview_id,
            "status": "committed",
            "error": None,
            "snapshot_ids": [s["record_id"] for s in snapshots],
            "created_at": utc_now().isoformat(),
            "revoked_at": None,
        }
        self._batches.append(batch)
        del self._previews[preview_id]
        return {"batch": batch, "created": True, "snapshots": snapshots}

    def list_batches(self, *, supplier: str = "") -> List[Dict[str, Any]]:
        def predicate(batch: Mapping[str, Any]) -> bool:
            return (not supplier) or str(batch.get("supplier")) == supplier

        return self._batches.find(predicate)

    def batch_status(self, batch_id: str) -> str:
        """Status of one import batch ("" when unknown).

        Downstream consumers (procurement, providers) use this to
        refuse quotes from revoked or failed batches.
        """
        found = self._batches.find(lambda b: str(b.get("record_id")) == batch_id)
        return str(found[0].get("status")) if found else ""

    def revoke_batch(self, batch_id: str, *, operator: str, reason: str) -> Dict[str, Any]:
        if not reason.strip():
            raise ImportPreviewError("revocation requires a reason")

        def mutate(batch: Dict[str, Any]) -> None:
            if str(batch.get("status")) in ("committed", "failed"):
                batch["status"] = "revoked"
            elif str(batch.get("status")) == "revoked":
                return
            else:
                raise ImportPreviewError(f"cannot revoke batch in status {batch.get('status')!r}")
            batch["revoked_at"] = utc_now().isoformat()
            batch["revoked_by"] = operator
            batch["revoke_reason"] = reason

        return self._batches.update(batch_id, mutate)

    def _snapshots_for(self, batch: Mapping[str, Any]) -> List[Dict[str, Any]]:
        ids = set(batch.get("snapshot_ids") or [])
        return [record for record in self._repos.offers.list() if record["record_id"] in ids]
