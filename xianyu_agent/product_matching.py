"""Same-product spec matching (WP05).

Matches a market-side observation snapshot against a supplier offer
snapshot and produces a ProductMatch with a deterministic grade. The
design rule from the master plan (section 8.2):

- Title/name similarity is used ONLY for recall and reporting; it never
  proves two items are the same product. The grade is decided by hard
  constraints (condition, brand, model, dimensions, unit/quantity
  class, material when stated on both sides).
- Any hard-constraint conflict -> NOT_MATCH (e.g. used vs new, 120cm vs
  150cm, piece vs case).
- All critical constraints (condition plus dimensions-or-model) known
  and equal -> EXACT only when every checked constraint is known;
  non-critical unknowns downgrade to COMPARABLE.
- Critical constraints unknown on either side -> UNKNOWN, with a
  confirm-with-supplier note. UNKNOWN never becomes EXACT by guessing.
- 二手 and 全新 never match on title alone.

Recall similarity uses stdlib difflib. The ``_similarity`` function is
the single swap point for RapidFuzz when it is installed as a project
dependency; switching it must not change the grading rules above.

Labeled pair evaluation: ``evaluate_labeled_pairs`` scores the grader
against a JSONL fixture of human-labeled pairs. The current seed set is
deliberately small; until the labeled set reaches the planned 100-pair
holdout, precision numbers are reported with their sample size and are
NOT a claim of the >=95% target.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .commerce.contracts import (
    MATCH_COMPARABLE,
    MATCH_EXACT,
    MATCH_NOT_MATCH,
    MATCH_UNKNOWN,
    build_product_match,
)

MATCH_RULE_VERSION = "hard-constraints-v1"

# Critical constraints: at least one of dimensions/model must be known
# on both sides, plus condition, for anything stronger than UNKNOWN.
CRITICAL_FIELDS = ("condition",)

UNIT_CLASSES: Dict[str, str] = {
    "件": "piece", "个": "piece", "只": "piece", "张": "piece", "台": "piece", "块": "piece",
    "套": "set", "组": "set",
    "箱": "case",
    "对": "pair",
    "打": "dozen",
    "米": "length",
}

NEW_WORDS = ("全新", "新品", "未使用", "brand new", "new")
USED_WORDS = ("二手", "9成新", "95新", "99新", "几乎全新", "轻微使用", "used", "second-hand")

_MATERIAL_WORDS = ("不锈钢", "碳钢", "铁", "铝合金", "铝", "木质", "实木", "塑料", "pp", "abs", "玻璃钢")

_DIMENSION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|厘米|cm|毫米)?\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|厘米|cm|毫米)?(?:\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|厘米|cm|毫米)?)?",
    re.IGNORECASE,
)
_MODEL_RE = re.compile(r"[A-Za-z]{1,6}[-_]?\d{2,}[A-Za-z0-9\-]*")

_CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "双": 2, "单": 1, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
# structural counts that make otherwise-similar items different products
# (5-shelf vs 4-shelf racks, 2-door vs 3-door freezers, double sinks...)
_COUNT_FEATURE_RE = re.compile(r"([一二两双单三四五六七八九十\d]+)\s*(层|门|抽屉|抽|星|眼|灶|头|孔|缸|轮|盘|人位|座位|位)")

_STEEL_GRADES = ("304", "316", "201", "430", "2205")


def steel_grade(raw: Any) -> str:
    """Extract a stated stainless-steel grade ("" when unstated)."""
    text = _norm_text(raw)
    for grade in _STEEL_GRADES:
        if grade in text and ("不锈钢" in text or "钢" in text):
            return grade
    return ""


def _parse_cn_count(text: str) -> Optional[int]:
    clean = str(text)
    if clean.isdigit():
        return int(clean)
    # mixed token like "100双" (model digits glued to a cn numeral):
    # the trailing cn numeral is the structural count, not the digits
    if clean and clean[-1] in _CN_NUMBERS:
        return _CN_NUMBERS[clean[-1]]
    if "十" in clean:
        parts = clean.split("十")
        tens = _CN_NUMBERS.get(parts[0], 1) if parts[0] else 1
        ones = _CN_NUMBERS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    if len(clean) == 1:
        return _CN_NUMBERS.get(clean)
    return None


def normalize_feature_counts(raw: Any) -> tuple:
    """Extract structural counts (层/门/抽/星/眼/灶/头) from text."""
    text = _norm_text(raw)
    if not text:
        return ()
    found = {}
    for match in _COUNT_FEATURE_RE.finditer(text):
        count = _parse_cn_count(match.group(1))
        feature = match.group(2)
        if count is not None:
            found.setdefault(feature, count)
    return tuple(sorted(found.items()))


class MatchError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Attribute normalization
# ---------------------------------------------------------------------------


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def normalize_condition(raw: Any) -> str:
    text = _norm_text(raw)
    if not text:
        return "unknown"
    if any(word in text for word in USED_WORDS):
        return "used"
    if any(word in text for word in NEW_WORDS):
        return "new"
    return "unknown"


def normalize_unit(raw: Any) -> str:
    text = _norm_text(raw)
    return UNIT_CLASSES.get(text, "unknown" if text else "unknown")


def normalize_model(raw: Any) -> str:
    text = _norm_text(raw)
    if not text:
        return ""
    tokens = [re.sub(r"[^a-z0-9]", "", match.group(0)) for match in _MODEL_RE.finditer(text)]
    return tokens[0] if tokens else ""


def normalize_dimensions(raw: Any) -> Optional[Tuple[Decimal, Decimal, Optional[Decimal]]]:
    """Parse ``120x60x85`` / ``120*60*85cm`` style dimensions to cm."""
    text = _norm_text(raw)
    if not text:
        return None
    match = _DIMENSION_RE.search(text)
    if not match:
        return None
    groups = match.groups()
    unit = "cm"
    for candidate in (groups[1], groups[3], groups[5]):
        if candidate:
            unit = candidate.lower()
            break

    def to_cm(value: str) -> Decimal:
        number = Decimal(value)
        return number / Decimal(10) if unit in ("mm", "毫米") else number

    first = to_cm(groups[0])
    second = to_cm(groups[2])
    third = to_cm(groups[4]) if groups[4] else None
    return (first, second, third)


def normalize_material(raw: Any) -> str:
    text = _norm_text(raw)
    for material in _MATERIAL_WORDS:
        if material in text:
            return material
    return ""


def extract_match_attributes(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """Project an OfferSnapshot into the normalized comparison space."""
    attributes = snapshot.get("attributes") or {}
    name = str(attributes.get("name") or "")
    spec = str(attributes.get("spec") or "")
    sku = str(snapshot.get("source_sku_id") or snapshot.get("source_item_id") or "")
    condition_raw = str(snapshot.get("condition") or "") or name
    return {
        "record_id": snapshot.get("record_id"),
        "name": name,
        "brand": normalize_brand(attributes.get("brand")),
        "model": normalize_model(sku) or normalize_model(name),
        "dimensions": normalize_dimensions(spec) or normalize_dimensions(name),
        "material": normalize_material(attributes.get("material")) or normalize_material(spec) or normalize_material(name),
        "condition": normalize_condition(condition_raw),
        "unit_class": normalize_unit(snapshot.get("unit")),
        "feature_counts": normalize_feature_counts(spec) or normalize_feature_counts(name),
        "steel_grade": steel_grade(spec) or steel_grade(name),
    }


def normalize_brand(raw: Any) -> str:
    return _norm_text(raw)


def _dims_equal(left: Optional[Tuple[Decimal, ...]], right: Optional[Tuple[Decimal, ...]]) -> Optional[bool]:
    if left is None or right is None:
        return None
    l2 = [d for d in left if d is not None]
    r2 = [d for d in right if d is not None]
    if not l2 or not r2:
        return None
    length = min(len(l2), len(r2))
    return all(l2[i] == r2[i] for i in range(length))


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _similarity(left: str, right: str) -> float:
    """Recall-only similarity. Swap point for RapidFuzz; never grades."""
    return SequenceMatcher(None, left or "", right or "").ratio()


def _check_constraint(field_name: str, left: Any, right: Any) -> Dict[str, Any]:
    """One hard-constraint check -> pass / conflict / unknown."""
    if left in ("", "unknown", None, ()) or right in ("", "unknown", None, ()):
        state = "unknown"
    elif field_name == "dimensions":
        equal = _dims_equal(left, right)
        state = "unknown" if equal is None else ("pass" if equal else "conflict")
    elif field_name == "feature_counts":
        left_counts, right_counts = dict(left), dict(right)
        shared = set(left_counts) & set(right_counts)
        # only compare features both sides state; disjoint claims are unknown
        state = (
            "pass"
            if shared and all(left_counts[k] == right_counts[k] for k in shared)
            else ("conflict" if shared else "unknown")
        )
    elif left == right:
        state = "pass"
    else:
        state = "conflict"
    return {"field": field_name, "left": _display(left), "right": _display(right), "state": state}


def _display(value: Any) -> Any:
    if isinstance(value, tuple):
        return [str(part) for part in value]
    return value


def grade_pair(left_attrs: Mapping[str, Any], right_attrs: Mapping[str, Any]) -> Dict[str, Any]:
    """Decide the match grade from normalized attributes.

    Returns {grade, hard_constraints, conflicts, unknowns, recall_score,
    explanation, supplier_confirmation_needed}.
    """
    checks = [
        _check_constraint("condition", left_attrs.get("condition"), right_attrs.get("condition")),
        _check_constraint("brand", left_attrs.get("brand"), right_attrs.get("brand")),
        _check_constraint("model", left_attrs.get("model"), right_attrs.get("model")),
        _check_constraint("dimensions", left_attrs.get("dimensions"), right_attrs.get("dimensions")),
        _check_constraint("material", left_attrs.get("material"), right_attrs.get("material")),
        _check_constraint("unit_class", left_attrs.get("unit_class"), right_attrs.get("unit_class")),
    ]
    # structural counts are an optional constraint: absent on both sides
    # means the concept does not apply (most items have no 层/门/星);
    # stated on one side only stays unknown and downgrades EXACT
    if left_attrs.get("feature_counts") or right_attrs.get("feature_counts"):
        checks.append(
            _check_constraint("feature_counts", left_attrs.get("feature_counts"), right_attrs.get("feature_counts"))
        )
    # steel grade likewise: unstated on both sides is not_applicable;
    # 304 vs 201 stated on both sides is a hard conflict
    if left_attrs.get("steel_grade") or right_attrs.get("steel_grade"):
        checks.append(
            _check_constraint("steel_grade", left_attrs.get("steel_grade"), right_attrs.get("steel_grade"))
        )
    conflicts = [c for c in checks if c["state"] == "conflict"]
    unknowns = [c["field"] for c in checks if c["state"] == "unknown"]

    # identity anchor: at least one of model/dimensions passes and neither
    # conflicts; a conflicting anchor already made grade NOT_MATCH above
    identity_known = any(
        c["state"] == "pass" for c in checks if c["field"] in ("model", "dimensions")
    )
    critical_missing = not identity_known or "condition" in unknowns

    if conflicts:
        grade = MATCH_NOT_MATCH
        reason = "; ".join(f"{c['field']}: {c['left']!r} vs {c['right']!r}" for c in conflicts)
    elif critical_missing:
        grade = MATCH_UNKNOWN
        reason = "critical constraints unknown: " + ", ".join(unknowns or ["condition/model/dimensions"])
    elif unknowns:
        grade = MATCH_COMPARABLE
        reason = "non-critical unknowns: " + ", ".join(unknowns)
    else:
        grade = MATCH_EXACT
        reason = "all hard constraints known and equal"

    recall_score = _similarity(str(left_attrs.get("name") or ""), str(right_attrs.get("name") or ""))
    return {
        "grade": grade,
        "hard_constraints": checks,
        "conflicts": conflicts,
        "unknowns": unknowns,
        "recall_score": round(recall_score, 4),
        "explanation": f"{reason} (recall similarity {recall_score:.2f} is reporting only, not proof)",
        "supplier_confirmation_needed": grade == MATCH_UNKNOWN,
    }


class ProductMatchingService:
    def __init__(self, repos) -> None:
        self._repos = repos

    def match_snapshots(self, left_snapshot_id: str, right_snapshot_id: str) -> Dict[str, Any]:
        left = self._repos.offers.get(left_snapshot_id)
        right = self._repos.offers.get(right_snapshot_id)
        if left is None or right is None:
            raise MatchError("both snapshot ids must exist")
        result = grade_pair(extract_match_attributes(left), extract_match_attributes(right))
        record = build_product_match(
            left_snapshot_id=left_snapshot_id,
            right_snapshot_id=right_snapshot_id,
            grade=result["grade"],
            rule_version=MATCH_RULE_VERSION,
            hard_constraints=result["hard_constraints"],
            conflicts=result["conflicts"],
            recall_score=result["recall_score"],
            explanation=result["explanation"],
            left_snapshot_revision=left.get("revision"),
            right_snapshot_revision=right.get("revision"),
            source_ref=f"matching:{MATCH_RULE_VERSION}",
        )
        stored = self._repos.matches.add(record)
        stored_result = dict(result)
        stored_result["match_id"] = stored["record_id"]
        return stored_result

    def correct_grade(
        self,
        match_id: str,
        *,
        corrected_grade: str,
        operator: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Record a human correction as a new match record (audit kept)."""
        existing = self._repos.matches.get(match_id)
        if existing is None:
            raise MatchError(f"match not found: {match_id}")
        if not reason.strip():
            raise MatchError("correction requires a reason")
        record = build_product_match(
            left_snapshot_id=existing["left_snapshot_id"],
            right_snapshot_id=existing["right_snapshot_id"],
            grade=corrected_grade,
            rule_version=MATCH_RULE_VERSION,
            hard_constraints=existing.get("hard_constraints") or [],
            conflicts=existing.get("conflicts") or [],
            recall_score=existing.get("recall_score"),
            explanation=f"human correction by {operator}: {reason}",
            human_correction={"corrected_from": existing["record_id"], "operator": operator, "reason": reason},
            source_ref=f"matching:{MATCH_RULE_VERSION}",
        )
        stored = self._repos.matches.add(record)
        return stored


# ---------------------------------------------------------------------------
# Labeled pair evaluation
# ---------------------------------------------------------------------------


def evaluate_labeled_pairs(fixture_path: Path, *, verbose: bool = False) -> Dict[str, Any]:
    """Score the grader against labeled pairs.

    Fixture rows: {"left": {...snapshot-like attrs...}, "right": {...},
    "label": "EXACT|COMPARABLE|NOT_MATCH|UNKNOWN", "note": "..."}.
    Reports precision for EXACT with its sample size; small samples are
    reported as small, never as target-met.
    """
    rows = [
        json.loads(line)
        for line in Path(fixture_path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not rows:
        raise MatchError(f"labeled fixture empty: {fixture_path}")

    def attrs(side: Mapping[str, Any]) -> Dict[str, Any]:
        def first_non_empty(*values: Any) -> Any:
            # try each value until one yields a non-empty result (an
            # existing-but-uninformative spec must not block the name
            # fallback)
            for value in values:
                if value:
                    return value
            return None

        return {
            "name": str(side.get("name") or ""),
            "brand": normalize_brand(side.get("brand")),
            "model": first_non_empty(
                normalize_model(side.get("model")),
                normalize_model(side.get("sku")),
                normalize_model(side.get("name")),
            )
            or "",
            "dimensions": first_non_empty(
                normalize_dimensions(side.get("spec")),
                normalize_dimensions(side.get("name")),
            ),
            "material": first_non_empty(
                normalize_material(side.get("material")),
                normalize_material(side.get("spec")),
                normalize_material(side.get("name")),
            )
            or "",
            "condition": normalize_condition(first_non_empty(side.get("condition"), side.get("name"))),
            "unit_class": normalize_unit(side.get("unit")),
            "feature_counts": first_non_empty(
                normalize_feature_counts(side.get("spec")),
                normalize_feature_counts(side.get("name")),
            )
            or (),
            "steel_grade": first_non_empty(
                steel_grade(side.get("spec")),
                steel_grade(side.get("name")),
            )
            or "",
        }

    confusion: Dict[str, Dict[str, int]] = {}
    mistakes: List[Dict[str, Any]] = []
    for row in rows:
        graded = grade_pair(attrs(row["left"]), attrs(row["right"]))
        actual = graded["grade"]
        expected = row["label"]
        confusion.setdefault(expected, {}).setdefault(actual, 0)
        confusion[expected][actual] += 1
        if actual != expected:
            mistakes.append({"expected": expected, "actual": actual, "note": row.get("note", "")})

    exact_true = sum(confusion.get("EXACT", {}).values())
    exact_correct = confusion.get("EXACT", {}).get("EXACT", 0)
    exact_predicted = sum(bucket.get("EXACT", 0) for bucket in confusion.values())
    precision = (exact_correct / exact_predicted) if exact_predicted else None
    return {
        "fixture": str(fixture_path),
        "pair_count": len(rows),
        "exact_precision": precision,
        "exact_predicted": exact_predicted,
        "exact_true": exact_true,
        "exact_correct": exact_correct,
        "sample_size_note": f"n={len(rows)} labeled pairs in this file; the >=95% EXACT precision target is only claimed from a clean holdout split",
        "mistakes": mistakes,
        "confusion": confusion,
    }
