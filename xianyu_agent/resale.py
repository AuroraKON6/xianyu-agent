"""Resale opportunity manager for domestic and cross-border arbitrage.

Manages the lifecycle: candidate → plan → purchase draft.
High-risk actions (approve plan, prepare draft, place order) go through
WorkOrder/Approval/ExecutionJobQueue.  MVP never auto-pays or auto-submits
supplier orders.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .candidate_activation import candidate_evidence_fingerprint
from .candidate_evidence_store import (
    CandidateEvidenceStore,
    EVIDENCE_KIND_CONFIG,
    artifact_reference,
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


BLOCKED_CATEGORIES = {
    "食品", "药品", "医疗", "母婴安全", "化妆品", "强品牌", "IP",
    "仿品", "危险品", "高客诉电子维修件",
    "food", "medicine", "medical", "baby_safety", "cosmetics",
    "branded", "ip", "counterfeit", "dangerous", "high_complaint_electronics",
}

# Scoring dimensions from docs/ops/xianyu/product-selection-playbook.md
SCORING_DIMENSIONS = [
    "cold_category",       # Does this avoid crowded consumer-electronics competition?
    "high_ticket",         # Can one order produce 100+ yuan gross profit?
    "batch_order",         # Can buyers naturally buy multiple units?
    "differentiation",     # Can copywriting/specs/service/images influence conversion?
    "supplier_advantage",  # Is there real cost/logistics/service advantage?
    "fulfillment_risk",    # Lower = better. Size, fragility, warranty, return freight.
    "compliance_risk",     # Lower = better. Prohibited goods, fake goods, rights issues.
]

RECOMMENDATION_THRESHOLDS = {
    "test": 28,    # 28-35: strong candidate
    "watch": 21,   # 21-27: worth testing
    "reject": 14,  # below 14: reject
}

STRUCTURED_LISTING_KEYS = (
    "search_keywords",
    "lead_image_requirement",
    "specification_table",
    "price_ladder",
    "trust_paragraph",
    "delivery_after_sales_notes",
    "operator_verification_questions",
)

# Category hints for auto-scoring
HIGH_TICKET_CATEGORIES = {
    "商用厨具", "餐饮设备", "机械设备", "办公家具", "工厂设备",
    "养殖设备", "制冷设备", "不锈钢制品", "木制品", "液压",
    "commercial kitchen", "restaurant equipment", "machinery",
}

BATCH_ORDER_CATEGORIES = {
    "围栏", "草坪", "防护网", "养殖网", "地垫", "桌椅",
    "工厂用品", "办公用品", "fence", "mat", "grid",
}

COLD_CATEGORIES = {
    "商用厨具", "养殖设备", "化粪池", "静电工作台", "拉布机",
    "母猪产床", "液压平台", "串串柜", "古琴", "乒乓球桌",
}


class ProductScorer:
    """Score product candidates on 7 dimensions from the product selection playbook.

    Each dimension scores 1-5. Fulfillment_risk and compliance_risk are inverted
    (1 = very high risk, 5 = very low risk).

    Total score maps to recommendation:
      28-35 → "test"   (strong candidate)
      21-27 → "watch"  (worth testing, monitor closely)
      14-20 → "weak"   (only if nothing better)
      < 14  → "reject"
    """

    def score(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Score a candidate and return scores + recommendation."""
        scores: Dict[str, int] = {}
        for dim in SCORING_DIMENSIONS:
            scorer = getattr(self, f"_score_{dim}", None)
            if scorer:
                scores[dim] = scorer(candidate)
            else:
                scores[dim] = 3  # default middle score

        total = sum(scores.values())
        recommendation = self._recommend(total)

        return {
            "scores": scores,
            "total_score": total,
            "recommendation": recommendation,
            "scored_at": _now_iso(),
        }

    def _score_cold_category(self, candidate: Dict) -> int:
        """Score cold-category potential. Higher = less crowded."""
        category = candidate.get("category", "")
        title = candidate.get("title", "")

        # Check against known cold categories
        for cold in COLD_CATEGORIES:
            if cold in category or cold in title:
                return 5

        # Consumer electronics = crowded
        crowded_keywords = {"手机", "电脑", "耳机", "平板", "数码", "电子", "phone", "laptop", "headphone"}
        for kw in crowded_keywords:
            if kw in category.lower() or kw in title.lower():
                return 1

        # B2B / industrial / commercial = generally colder
        b2b_keywords = {"商用", "工业", "工厂", "养殖", "餐饮", "设备", "equipment", "commercial", "industrial"}
        for kw in b2b_keywords:
            if kw in category or kw in title:
                return 4

        return 3

    def _score_high_ticket(self, candidate: Dict) -> int:
        """Score high-ticket potential based on price and category."""
        target_price = float(candidate.get("target_sale_price", 0))

        if target_price >= 1000:
            return 5
        if target_price >= 500:
            return 4
        if target_price >= 200:
            return 3
        if target_price >= 50:
            return 2
        return 1

    def _score_batch_order(self, candidate: Dict) -> int:
        """Score batch-order potential."""
        category = candidate.get("category", "")
        title = candidate.get("title", "")

        for kw in BATCH_ORDER_CATEGORIES:
            if kw in category or kw in title:
                return 5

        # Check for business/commercial context
        biz_keywords = {"商用", "批发", "工程", "工厂", "办公", "批量", "wholesale", "commercial"}
        for kw in biz_keywords:
            if kw in category or kw in title:
                return 4

        return 2

    def _score_differentiation(self, candidate: Dict) -> int:
        """Score non-standard differentiation potential."""
        category = candidate.get("category", "")
        title = candidate.get("title", "")

        # Standard/commodity items have low differentiation
        commodity_keywords = {"手机壳", "数据线", "充电器", "贴膜", "cable", "charger", "case"}
        for kw in commodity_keywords:
            if kw in category.lower() or kw in title.lower():
                return 1

        # Non-standard items where copywriting matters
        nonstandard_keywords = {"古琴", "艺术品", "定制", "手工", "特殊", "custom", "handmade"}
        for kw in nonstandard_keywords:
            if kw in category or kw in title:
                return 5

        # Equipment with specs that need explanation
        spec_keywords = {"设备", "机械", "操作台", "冷柜", "equipment", "machine"}
        for kw in spec_keywords:
            if kw in category or kw in title:
                return 4

        return 3

    def _score_supplier_advantage(self, candidate: Dict) -> int:
        """Score supplier advantage based on economics."""
        economics = candidate.get("economics", {})
        margin = economics.get("gross_margin", 0)
        supplier_verified = candidate.get("supplier_verified", False)

        if margin >= 0.5 and supplier_verified:
            return 5
        if margin >= 0.3 and supplier_verified:
            return 4
        if margin >= 0.2:
            return 3
        if margin >= 0.1:
            return 2
        return 1

    def _score_fulfillment_risk(self, candidate: Dict) -> int:
        """Score fulfillment risk (inverted: 5 = low risk, 1 = high risk)."""
        category = candidate.get("category", "")
        title = candidate.get("title", "")

        # Large/fragile items = higher risk
        high_risk_keywords = {"玻璃", "陶瓷", "大理石", "大型", "超重", "易碎", "glass", "ceramic", "fragile"}
        for kw in high_risk_keywords:
            if kw in category or kw in title:
                return 2

        # Small/sturdy items = lower risk
        low_risk_keywords = {"不锈钢", "金属", "塑料", "工具", "stainless", "metal", "plastic", "tool"}
        for kw in low_risk_keywords:
            if kw in category or kw in title:
                return 4

        # Check risk flags
        risk_flags = candidate.get("risk_flags", [])
        if any("fragile" in f.lower() or "易碎" in f for f in risk_flags):
            return 2

        return 3

    def _score_compliance_risk(self, candidate: Dict) -> int:
        """Score compliance risk (inverted: 5 = low risk, 1 = high risk)."""
        risk_level = candidate.get("risk_level", "low")
        risk_flags = candidate.get("risk_flags", [])

        if risk_level == "blocked":
            return 1
        if risk_level == "high":
            return 2
        if risk_level == "medium":
            return 3

        # Check for brand/IP flags
        for flag in risk_flags:
            if "brand" in flag.lower() or "ip" in flag.lower() or "品牌" in flag:
                return 2

        return 5

    @staticmethod
    def _recommend(total_score: int) -> str:
        if total_score >= RECOMMENDATION_THRESHOLDS["test"]:
            return "test"
        if total_score >= RECOMMENDATION_THRESHOLDS["watch"]:
            return "watch"
        if total_score >= RECOMMENDATION_THRESHOLDS["reject"]:
            return "weak"
        return "reject"

VALID_SOURCE_PLATFORMS = {"pdd", "1688"}
VALID_SALES_PLATFORMS = {"xianyu", "shopee", "lazada"}
VALID_MARKET_SCOPES = {"domestic", "cross_border"}

DEFAULT_PLATFORM_FEE_RATE = 0.08
DEFAULT_REFUND_RATE = 0.02

CANDIDATE_ACTIVATION_UPDATE_FIELDS = {
    "title", "description", "category", "source_platform", "source_url",
    "supplier_name", "supplier_sku", "supplier_price", "supplier_shipping",
    "target_sale_price", "specifications", "images", "sales_platform",
    "market_scope", "supplier_verified", "supplier_verification_note",
    "supplier_quote_checked_at", "supplier_quote_evidence_ref",
    "minimum_order_quantity", "stock_status", "stock_quantity",
    "stock_checked_at", "stock_evidence_ref", "fulfillment_verified",
    "fulfillment_verification_note", "fulfillment_evidence_ref",
    "neutral_shipping_confirmed", "dispatch_time_hours",
    "supplier_return_policy", "damage_liability_note", "image_rights_type",
    "image_rights_evidence", "image_rights_evidence_ref", "competitor_links",
    "competitor_price_min", "competitor_price_max", "competitor_checked_at",
    "competitor_evidence_ref", "estimated_demand_signal",
}

VERIFY_UPDATE_FIELDS = {
    "supplier_verified", "supplier_verification_note",
    "supplier_quote_checked_at", "supplier_quote_evidence_ref",
    "minimum_order_quantity", "stock_status", "stock_quantity",
    "stock_checked_at", "stock_evidence_ref", "fulfillment_verified",
    "fulfillment_verification_note", "fulfillment_evidence_ref",
    "neutral_shipping_confirmed", "dispatch_time_hours",
    "supplier_return_policy", "damage_liability_note", "image_rights_type",
    "image_rights_evidence", "image_rights_evidence_ref", "images",
    "competitor_links", "competitor_price_min", "competitor_price_max",
    "competitor_checked_at", "competitor_evidence_ref",
    "estimated_demand_signal",
}

_ACTIVATION_FLOAT_FIELDS = {
    "supplier_price", "supplier_shipping", "target_sale_price",
    "competitor_price_min", "competitor_price_max", "dispatch_time_hours",
}
_ACTIVATION_INT_FIELDS = {"minimum_order_quantity", "stock_quantity"}
_ACTIVATION_BOOL_FIELDS = {
    "supplier_verified", "fulfillment_verified", "neutral_shipping_confirmed",
}
_ACTIVATION_LIST_FIELDS = {"images", "competitor_links"}


def _build_structured_listing_fields(candidate: Dict[str, Any], specs: Dict[str, Any]) -> Dict[str, Any]:
    title = str(candidate.get("title") or "").strip()
    category = str(candidate.get("category") or "").strip()
    target_price = _safe_float(candidate.get("target_sale_price"))
    supplier_shipping = _safe_float(candidate.get("supplier_shipping"))
    supplier_note = str(candidate.get("supplier_verification_note") or "").strip()
    return_boundary = _commercial_return_boundary(candidate)
    search_keywords = _listing_keywords(title, category, specs)
    fields = {
        "search_keywords": search_keywords,
        "lead_image_requirement": {
            "format": "1:1正方形高清图",
            "must_show": "完整商品和使用场景，优先展示实际应用环境",
            "source_boundary": "必须使用自有实拍、供应商授权素材或可商用图片，禁止复制其他卖家图片",
            "differentiation_overlay": _image_overlay_text(title, specs),
        },
        "specification_table": _specification_table(candidate, specs),
        "price_ladder": _price_ladder(target_price),
        "trust_paragraph": (
            "供应商已验证，商品按实际规格发货。质量、材质、尺寸和发货方式以下单前确认为准；"
            "如需定制或批量采购，先确认规格、库存和物流费用。"
            + (f" 供应商备注：{supplier_note}" if supplier_note else "")
        ),
        "delivery_after_sales_notes": {
            "shipping_method": "快递或物流，大件商品优先走物流",
            "dispatch_time": "拍下后48小时内发货，特殊规格先确认库存",
            "shipping_cost": supplier_shipping,
            "damage_process": "到货如有破损，保留外包装和照片，24小时内反馈处理",
            "return_boundary": return_boundary,
            "return_policy": str(candidate.get("commercial_return_policy") or "unknown"),
            "commercial_terms_version": str(candidate.get("commercial_terms_version") or ""),
        },
        "operator_verification_questions": [
            "供应商价格是否为当前有效报价？",
            "图片是否为自有实拍或供应商授权素材？",
            "规格表是否和供应商实际可发规格一致？",
            "运费、发货方式和发货时效是否已确认？",
            "是否存在禁售、侵权、夸大宣传或误导性低价问题？",
            "退货和售后说明是否与当前账号、类目的商业条款一致？",
        ],
    }
    fields["structured_fields_complete"] = all(bool(fields.get(key)) for key in STRUCTURED_LISTING_KEYS)
    return fields


def _commercial_return_boundary(candidate: Dict[str, Any]) -> str:
    policy = str(candidate.get("commercial_return_policy") or "unknown").strip()
    if policy == "seven_day":
        return "依法支持七日无理由退货，具体条件和运费承担以平台规则及商品页面公示为准；规格尺寸请下单前确认"
    if policy == "lawful_exception":
        return "本商品按平台公示的依法适用类目例外处理退货；质量问题按平台规则处理，规格尺寸请下单前确认"
    return "退换货按平台规则及商品页面公示执行；质量问题请保留凭证，规格尺寸请下单前确认"


def _listing_keywords(title: str, category: str, specs: Dict[str, Any]) -> List[str]:
    keywords: List[str] = []
    _append_unique(keywords, title[:30])
    for token in title.replace("/", " ").replace("｜", " ").split():
        _append_unique(keywords, token[:30])
    for hint in ("操作台", "冷柜", "货架", "工作台", "商用", "厨房设备", "餐饮设备", "不锈钢", "机械", "设备"):
        if hint in title or hint in category or any(hint in str(value) for value in specs.values()):
            _append_unique(keywords, hint)
    _append_unique(keywords, category)
    for key, value in specs.items():
        if value:
            _append_unique(keywords, str(value)[:30])
        if key:
            _append_unique(keywords, str(key)[:30])
    for fallback in ("闲鱼同城", "商用设备", "批量采购", "现货发货", "规格可询"):
        _append_unique(keywords, fallback)
    return keywords[:10]


def _specification_table(candidate: Dict[str, Any], specs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "specification": str(specs.get("specification") or specs.get("款式") or "默认款"),
            "size": str(specs.get("size") or specs.get("尺寸") or "下单前确认"),
            "material": str(specs.get("material") or specs.get("材质") or "下单前确认"),
            "feature": str(specs.get("feature") or specs.get("功能") or candidate.get("category") or "常规"),
            "price": _safe_float(candidate.get("target_sale_price")),
        }
    ]


def _price_ladder(target_price: float) -> List[Dict[str, Any]]:
    entry_price = round(target_price, 2) if target_price else 0
    return [
        {
            "role": "visible_entry",
            "label": "当前标价",
            "price": entry_price,
            "explanation": "标价为当前基础规格价格，其他规格下单前确认。",
        },
        {
            "role": "upgrade_reference",
            "label": "升级规格",
            "price": round(entry_price * 1.25, 2) if entry_price else 0,
            "explanation": "更大尺寸、升级材质或附加功能可作为加价项。",
        },
    ]


def _image_overlay_text(title: str, specs: Dict[str, Any]) -> str:
    material = str(specs.get("material") or specs.get("材质") or "").strip()
    feature = str(specs.get("feature") or specs.get("功能") or "").strip()
    if material and feature:
        return f"{material} / {feature}"
    if material:
        return material
    if feature:
        return feature
    return title[:12] or "实拍现货"


def _append_unique(items: List[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in items:
        items.append(text)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class ResaleManager:
    """Persistent manager for resale candidates, plans, and purchase drafts."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_store = CandidateEvidenceStore(self._data_dir / "candidate_evidence")
        self._candidates_path = self._data_dir / "resale_candidates.json"
        self._plans_path = self._data_dir / "resale_plans.json"
        self._drafts_path = self._data_dir / "resale_drafts.json"
        self._listing_drafts_path = self._data_dir / "resale_listing_drafts.json"
        self._listing_drafts_dir = self._data_dir / "drafts"
        self._listing_drafts_dir.mkdir(parents=True, exist_ok=True)
        self._purchase_tasks_path = self._data_dir / "resale_purchase_tasks.json"
        self._resale_orders_path = self._data_dir / "resale_orders.json"
        self._candidates: Dict[str, Dict] = {}
        self._plans: Dict[str, Dict] = {}
        self._drafts: Dict[str, Dict] = {}
        self._listing_drafts: Dict[str, Dict] = {}
        self._purchase_tasks: Dict[str, Dict] = {}
        self._resale_orders: Dict[str, Dict] = {}
        self._scorer = ProductScorer()
        self._load_all()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        self._candidates = self._load_json(self._candidates_path)
        self._plans = self._load_json(self._plans_path)
        self._drafts = self._load_json(self._drafts_path)
        self._listing_drafts = self._load_json(self._listing_drafts_path)
        self._purchase_tasks = self._load_json(self._purchase_tasks_path)
        self._resale_orders = self._load_json(self._resale_orders_path)

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Dict]:
        if not path.exists():
            return {}
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(items, list):
                return {item["id"]: item for item in items if "id" in item}
            return {}
        except (json.JSONDecodeError, KeyError):
            return {}

    def _save_candidates(self) -> None:
        self._save_json(self._candidates_path, list(self._candidates.values()))

    def _save_plans(self) -> None:
        self._save_json(self._plans_path, list(self._plans.values()))

    def _save_drafts(self) -> None:
        self._save_json(self._drafts_path, list(self._drafts.values()))

    def _save_listing_drafts(self) -> None:
        self._save_json(self._listing_drafts_path, list(self._listing_drafts.values()))

    def _save_purchase_tasks(self) -> None:
        self._save_json(self._purchase_tasks_path, list(self._purchase_tasks.values()))

    def _save_resale_orders(self) -> None:
        self._save_json(self._resale_orders_path, list(self._resale_orders.values()))

    @staticmethod
    def _save_json(path: Path, items: list) -> None:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_executable_draft(self, draft: Dict) -> None:
        """Write draft to data/drafts/{draft_id}.json so handle_publish_listing can read it."""
        exec_draft = {
            "id": draft["draft_id"],
            "title": draft.get("title", ""),
            "category": draft.get("category", ""),
            "price": draft.get("price"),
            "stock": draft.get("stock"),
            "condition": "new",
            "location": "",
            "description": draft.get("description", ""),
            "tags": [],
            "selected_images": draft.get("images", []),
            "delivery_type": draft.get("delivery_type", "shipping"),
            "delivery_payload": "",
            "source": draft.get("source", ""),
            "raw_text": "",
        }
        for key in STRUCTURED_LISTING_KEYS:
            exec_draft[key] = draft.get(key)
        exec_draft["structured_fields_complete"] = bool(draft.get("structured_fields_complete"))
        path = self._listing_drafts_dir / f"{draft['draft_id']}.json"
        path.write_text(json.dumps(exec_draft, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Economics
    # ------------------------------------------------------------------

    @staticmethod
    def compute_economics(
        supplier_price: float,
        supplier_shipping: float,
        target_sale_price: float,
        platform_fee_rate: float = DEFAULT_PLATFORM_FEE_RATE,
        platform_fixed_fee: float = 0.0,
        expected_refund_rate: float = DEFAULT_REFUND_RATE,
    ) -> Dict[str, Any]:
        variable_platform_fee = round(target_sale_price * platform_fee_rate, 2)
        platform_fee = round(variable_platform_fee + platform_fixed_fee, 2)
        refund_loss = round(target_sale_price * expected_refund_rate, 2)
        gross_profit = round(
            target_sale_price - supplier_price - supplier_shipping - platform_fee - refund_loss, 2
        )
        gross_margin = round(gross_profit / target_sale_price, 4) if target_sale_price > 0 else 0.0
        return {
            "supplier_price": supplier_price,
            "supplier_shipping": supplier_shipping,
            "target_sale_price": target_sale_price,
            "platform_fee_rate": platform_fee_rate,
            "platform_fixed_fee": platform_fixed_fee,
            "variable_platform_fee": variable_platform_fee,
            "expected_refund_rate": expected_refund_rate,
            "platform_fee": platform_fee,
            "refund_loss": refund_loss,
            "gross_profit": gross_profit,
            "gross_margin": gross_margin,
        }

    # ------------------------------------------------------------------
    # Risk evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def is_blocked_category(category: str) -> bool:
        if not category:
            return False
        cat_lower = category.strip().lower()
        for blocked in BLOCKED_CATEGORIES:
            if blocked.lower() in cat_lower or cat_lower in blocked.lower():
                return True
        return False

    def evaluate_risk(self, payload: Dict) -> Dict[str, Any]:
        flags: List[str] = []
        level = "low"
        blocked_reason: Optional[str] = None

        category = payload.get("category", "")
        if self.is_blocked_category(category):
            flags.append(f"blocked_category: {category}")
            level = "blocked"
            blocked_reason = f"类目 '{category}' 属于高风险/禁用类目"

        supplier_price = float(payload.get("supplier_price", 0))
        target_price = float(payload.get("target_sale_price", 0))

        if supplier_price <= 0:
            flags.append("invalid_supplier_price")
            level = max(level, "high")

        if target_price <= 0:
            flags.append("invalid_target_price")
            level = max(level, "high")

        if target_price > 0 and supplier_price > 0:
            if supplier_price >= target_price:
                flags.append("supplier_price_exceeds_target")
                level = "blocked"
                blocked_reason = "供应商价格 >= 目标售价，无利润空间"

        brand = payload.get("brand_hint", "")
        if brand and any(kw in brand.lower() for kw in ["nike", "adidas", "apple", "louis vuitton", "gucci"]):
            flags.append(f"potential_brand_infringement: {brand}")
            level = max(level, "high")

        source_platform = payload.get("source_platform", "")
        if source_platform not in VALID_SOURCE_PLATFORMS and source_platform:
            flags.append(f"unknown_source_platform: {source_platform}")

        sales_platform = payload.get("sales_platform", "")
        if sales_platform not in VALID_SALES_PLATFORMS and sales_platform:
            flags.append(f"unknown_sales_platform: {sales_platform}")

        economics = self.compute_economics(
            supplier_price, float(payload.get("supplier_shipping", 0)), target_price,
            float(payload.get("platform_fee_rate", DEFAULT_PLATFORM_FEE_RATE)),
            float(payload.get("platform_fixed_fee", 0)),
            float(payload.get("expected_refund_rate", DEFAULT_REFUND_RATE)),
        )
        if economics["gross_profit"] < 0:
            flags.append("negative_gross_profit")
            if level not in ("blocked",):
                level = max(level, "high")

        if economics["gross_margin"] < 0.1 and economics["gross_margin"] >= 0:
            flags.append("low_margin_under_10pct")
            if level not in ("blocked",):
                level = max(level, "medium")

        return {
            "risk_level": level,
            "risk_flags": flags,
            "blocked_reason": blocked_reason,
        }

    # ------------------------------------------------------------------
    # Candidate CRUD
    # ------------------------------------------------------------------

    def create_candidate(self, payload: Dict) -> Dict:
        candidate_id = _new_id("rc")
        now = _now_iso()

        supplier_price = float(payload.get("supplier_price", 0))
        supplier_shipping = float(payload.get("supplier_shipping", 0))
        target_sale_price = float(payload.get("target_sale_price", 0))
        platform_fee_rate = float(payload.get("platform_fee_rate", DEFAULT_PLATFORM_FEE_RATE))
        platform_fixed_fee = float(payload.get("platform_fixed_fee", 0))
        expected_refund_rate = float(payload.get("expected_refund_rate", DEFAULT_REFUND_RATE))

        economics = self.compute_economics(
            supplier_price, supplier_shipping, target_sale_price,
            platform_fee_rate, platform_fixed_fee, expected_refund_rate,
        )
        risk = self.evaluate_risk(payload)

        source_platform = payload.get("source_platform", "pdd")
        market_scope = payload.get("market_scope", "domestic")
        sales_platform = payload.get("sales_platform", "xianyu")

        status = "evaluating"
        if risk["risk_level"] == "blocked":
            status = "blocked"

        candidate: Dict[str, Any] = {
            "id": candidate_id,
            "candidate_id": candidate_id,
            "title": payload.get("title", ""),
            "description": payload.get("description", ""),
            "source_platform": source_platform,
            "source_url": payload.get("source_url", ""),
            "supplier_name": payload.get("supplier_name", ""),
            "supplier_sku": payload.get("supplier_sku", ""),
            "supplier_price": supplier_price,
            "supplier_shipping": supplier_shipping,
            "market_scope": market_scope,
            "sales_platform": sales_platform,
            "target_sale_price": target_sale_price,
            "category": payload.get("category", ""),
            "images": payload.get("images", []),
            "specifications": payload.get("specifications", {}),
            "status": status,
            "risk_level": risk["risk_level"],
            "risk_flags": risk["risk_flags"],
            "blocked_reason": risk["blocked_reason"],
            "economics": economics,
            "notes": payload.get("notes", ""),
            "supplier_verified": False,
            "supplier_verification_note": "",
            "supplier_quote_checked_at": None,
            "supplier_quote_evidence_ref": "",
            # R12: bind the candidate's economics to the exact supplier
            # snapshot they were priced from (when the intake flow knows
            # it) — a newer committed snapshot then invalidates approvals
            # derived from this candidate at execution time
            "supplier_snapshot_id": str(payload.get("supplier_snapshot_id") or ""),
            "supplier_snapshot_payload_hash": str(payload.get("supplier_snapshot_payload_hash") or ""),
            "minimum_order_quantity": None,
            "stock_status": "",
            "stock_quantity": None,
            "stock_checked_at": None,
            "stock_evidence_ref": "",
            "fulfillment_verified": False,
            "fulfillment_verification_note": "",
            "fulfillment_evidence_ref": "",
            "neutral_shipping_confirmed": False,
            "dispatch_time_hours": None,
            "supplier_return_policy": "",
            "damage_liability_note": "",
            "image_rights_type": "",
            "image_rights_evidence": "",
            "image_rights_evidence_ref": "",
            "competitor_links": [],
            "competitor_price_min": None,
            "competitor_price_max": None,
            "competitor_checked_at": None,
            "competitor_evidence_ref": "",
            "estimated_demand_signal": "",
            "manual_decision": None,
            "manual_decision_note": "",
            "approved_evidence_fingerprint": "",
            "manual_decision_approved_at": "",
            "manual_decision_invalidated_at": "",
            "manual_decision_invalidation_reason": "",
            "evidence_revision": 1,
            "commercial_terms_version": "",
            "commercial_terms_cookie_id": "",
            "commercial_terms_category": "",
            "commercial_terms_observed_at": "",
            "commercial_terms_evidence_ref": "",
            "commercial_return_policy": "unknown",
            "commercial_return_exception_basis": "",
            "verified_at": None,
            "listing_draft_id": None,
            "created_at": now,
            "updated_at": now,
        }
        # Run strategy scoring
        scoring = self._scorer.score(candidate)
        candidate["scoring"] = scoring
        self._candidates[candidate_id] = candidate
        self._save_candidates()
        return _copy(candidate)

    def list_candidates(
        self,
        status: Optional[str] = None,
        source_platform: Optional[str] = None,
        market_scope: Optional[str] = None,
    ) -> List[Dict]:
        results = list(self._candidates.values())
        if status:
            results = [c for c in results if c.get("status") == status]
        if source_platform:
            results = [c for c in results if c.get("source_platform") == source_platform]
        if market_scope:
            results = [c for c in results if c.get("market_scope") == market_scope]
        results.sort(key=lambda c: c.get("created_at", ""), reverse=True)
        return _copy(results)

    def get_candidate(self, candidate_id: str) -> Optional[Dict]:
        c = self._candidates.get(candidate_id)
        return _copy(c) if c else None

    def score_candidate(self, candidate_id: str) -> Optional[Dict]:
        """Re-score an existing candidate and return the scoring result."""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        scoring = self._scorer.score(candidate)
        candidate["scoring"] = scoring
        candidate["updated_at"] = _now_iso()
        self._save_candidates()
        return _copy(scoring)

    def update_candidate_economics_from_commercial_terms(
        self,
        candidate_id: str,
        *,
        cookie_id: str,
        category: str,
        platform_fee_rate: float,
        platform_fixed_fee: float,
        snapshot_version: str,
        observed_at: str,
        evidence_ref: str,
        return_policy: str = "unknown",
        return_exception_basis: str = "",
    ) -> Optional[Dict]:
        """Reprice one candidate from a current account/category terms snapshot."""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        if str(candidate.get("category") or "").strip().casefold() != str(category or "").strip().casefold():
            raise ValueError("candidate category does not match commercial terms")
        before_fingerprint = candidate_evidence_fingerprint(candidate)

        rate = float(platform_fee_rate)
        fixed_fee = float(platform_fixed_fee)
        if not math.isfinite(rate) or rate < 0 or rate >= 1:
            raise ValueError("platform_fee_rate must be >= 0 and < 1")
        if not math.isfinite(fixed_fee) or fixed_fee < 0:
            raise ValueError("platform_fixed_fee must be >= 0")

        refund_rate = _safe_float(
            (candidate.get("economics") or {}).get("expected_refund_rate", DEFAULT_REFUND_RATE)
        )
        candidate["economics"] = self.compute_economics(
            _safe_float(candidate.get("supplier_price")),
            _safe_float(candidate.get("supplier_shipping")),
            _safe_float(candidate.get("target_sale_price")),
            rate,
            fixed_fee,
            refund_rate,
        )
        candidate["commercial_terms_version"] = str(snapshot_version or "").strip()
        candidate["commercial_terms_cookie_id"] = str(cookie_id or "").strip()
        candidate["commercial_terms_category"] = str(category or "").strip()
        candidate["commercial_terms_observed_at"] = str(observed_at or "").strip()
        candidate["commercial_terms_evidence_ref"] = str(evidence_ref or "").strip()[:300]
        candidate["commercial_return_policy"] = str(return_policy or "unknown").strip()
        candidate["commercial_return_exception_basis"] = str(return_exception_basis or "").strip()[:300]

        risk_payload = dict(candidate)
        risk_payload["platform_fee_rate"] = rate
        risk_payload["platform_fixed_fee"] = fixed_fee
        risk_payload["expected_refund_rate"] = refund_rate
        risk = self.evaluate_risk(risk_payload)
        candidate["risk_level"] = risk["risk_level"]
        candidate["risk_flags"] = risk["risk_flags"]
        candidate["blocked_reason"] = risk["blocked_reason"]
        if risk["risk_level"] == "blocked":
            candidate["status"] = "blocked"
        after_fingerprint = candidate_evidence_fingerprint(candidate)
        if before_fingerprint != after_fingerprint:
            now = _now_iso()
            candidate["evidence_revision"] = int(candidate.get("evidence_revision") or 1) + 1
            if candidate.get("manual_decision") is not None:
                candidate["manual_decision_invalidated_at"] = now
                candidate["manual_decision_invalidation_reason"] = "account/category commercial terms changed"
            candidate["manual_decision"] = None
            candidate["manual_decision_note"] = ""
            candidate["manual_decision_override_reason"] = ""
            candidate["approved_evidence_fingerprint"] = ""
            candidate["manual_decision_approved_at"] = ""
            if candidate.get("status") != "blocked":
                candidate["status"] = "evaluating"
        candidate["scoring"] = self._scorer.score(candidate)
        candidate["updated_at"] = _now_iso()
        self._save_candidates()
        return _copy(candidate)

    # ------------------------------------------------------------------
    # Plan CRUD
    # ------------------------------------------------------------------

    def create_plan(self, candidate_id: str, overrides: Optional[Dict] = None) -> Optional[Dict]:
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        if candidate.get("status") == "blocked":
            return None

        overrides = overrides or {}
        plan_id = _new_id("rp")
        now = _now_iso()

        plan: Dict[str, Any] = {
            "id": plan_id,
            "plan_id": plan_id,
            "candidate_id": candidate_id,
            "status": "draft",
            "listing_title": overrides.get("listing_title", candidate.get("title", "")),
            "listing_description": overrides.get("listing_description", candidate.get("description", "")),
            "pricing_strategy": overrides.get("pricing_strategy", {
                "recommended_price": candidate.get("target_sale_price", 0),
                "min_acceptable": round(candidate.get("target_sale_price", 0) * 0.9, 2),
                "bundling_notes": "",
            }),
            "risk_assessment": {
                "level": candidate.get("risk_level", "low"),
                "flags": candidate.get("risk_flags", []),
                "mitigation_notes": overrides.get("mitigation_notes", ""),
            },
            "work_order_id": None,
            "created_at": now,
            "updated_at": now,
        }
        self._plans[plan_id] = plan
        self._save_plans()
        return _copy(plan)

    def get_plan(self, plan_id: str) -> Optional[Dict]:
        p = self._plans.get(plan_id)
        return _copy(p) if p else None

    def update_plan(self, plan_id: str, updates: Dict) -> Optional[Dict]:
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        for key, value in updates.items():
            if key in ("status", "work_order_id", "listing_title", "listing_description",
                       "pricing_strategy", "risk_assessment"):
                plan[key] = value
        plan["updated_at"] = _now_iso()
        self._save_plans()
        return _copy(plan)

    # ------------------------------------------------------------------
    # Purchase Draft CRUD
    # ------------------------------------------------------------------

    def create_purchase_draft(
        self,
        plan_id: str,
        quantity: int = 1,
        buyer_address_summary: str = "",
        buyer_address_full: str = "",
        commercial_trial_id: str = "",
        xianyu_order_id: str = "",
        cookie_id: str = "",
        item_id: str = "",
        sale_amount: float = 0,
        fulfillment_lock_owner: str = "",
    ) -> Optional[Dict]:
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        candidate = self._candidates.get(plan.get("candidate_id"))
        if not candidate:
            return None

        draft_id = _new_id("pd")
        now = _now_iso()

        supplier_price = candidate.get("supplier_price", 0)
        supplier_shipping = candidate.get("supplier_shipping", 0)
        total_cost = round(supplier_price * quantity + supplier_shipping, 2)

        economics = candidate.get("economics", {})
        profit_review = {
            "unit_gross_profit": economics.get("gross_profit", 0),
            "unit_gross_margin": economics.get("gross_margin", 0),
            "total_cost": total_cost,
            "expected_revenue": round(candidate.get("target_sale_price", 0) * quantity, 2),
            "expected_profit": round(economics.get("gross_profit", 0) * quantity, 2),
        }

        risk_warnings: List[str] = []
        if candidate.get("risk_level") in ("high", "blocked"):
            risk_warnings.append(f"高风险候选: {candidate.get('risk_level')}")
        for flag in candidate.get("risk_flags", []):
            risk_warnings.append(flag)
        if economics.get("gross_margin", 0) < 0.1:
            risk_warnings.append("毛利率低于10%，需谨慎")
        risk_warnings.append("MVP 阶段不会自动付款或提交供应商订单")

        masked_address = buyer_address_summary
        if not masked_address and buyer_address_full:
            masked_address = buyer_address_full[:6] + "***" + buyer_address_full[-4:] if len(buyer_address_full) > 10 else "***"

        draft: Dict[str, Any] = {
            "id": draft_id,
            "draft_id": draft_id,
            "plan_id": plan_id,
            "candidate_id": plan.get("candidate_id"),
            "status": "pending_approval",
            "supplier_url": candidate.get("source_url", ""),
            "product_specs": candidate.get("specifications", {}),
            "quantity": quantity,
            "buyer_address": masked_address or "",
            "commercial_trial_id": str(commercial_trial_id or "").strip(),
            "xianyu_order_id": str(xianyu_order_id or "").strip(),
            "cookie_id": str(cookie_id or "").strip(),
            "item_id": str(item_id or "").strip(),
            "sale_amount": round(float(sale_amount or 0), 2),
            "fulfillment_lock_owner": str(fulfillment_lock_owner or "").strip(),
            "total_cost": total_cost,
            "profit_review": profit_review,
            "risk_warnings": risk_warnings,
            "work_order_id": None,
            "approval_id": None,
            "created_at": now,
            "updated_at": now,
        }
        self._drafts[draft_id] = draft
        self._save_drafts()
        return _copy(draft)

    def get_purchase_draft(self, draft_id: str) -> Optional[Dict]:
        d = self._drafts.get(draft_id)
        return _copy(d) if d else None

    def update_purchase_draft(self, draft_id: str, updates: Dict) -> Optional[Dict]:
        draft = self._drafts.get(draft_id)
        if not draft:
            return None
        for key, value in updates.items():
            if key in ("status", "work_order_id", "approval_id"):
                draft[key] = value
        draft["updated_at"] = _now_iso()
        self._save_drafts()
        return _copy(draft)

    # ------------------------------------------------------------------
    # Verification & Decision
    # ------------------------------------------------------------------

    def verify_candidate(self, candidate_id: str, payload: Dict) -> Optional[Dict]:
        """Compatibility entrypoint for partial evidence updates."""
        return self._update_candidate_activation_fields(
            candidate_id,
            payload,
            allowed_fields=VERIFY_UPDATE_FIELDS,
        )

    def update_candidate_activation(self, candidate_id: str, payload: Dict) -> Optional[Dict]:
        """Update one candidate's launch evidence as a versioned fact set.

        Material changes invalidate any existing manual decision. A candidate
        with a generated listing draft cannot be changed in place because the
        draft and its approval payload represent the previous fact revision.
        """
        return self._update_candidate_activation_fields(
            candidate_id,
            payload,
            allowed_fields=CANDIDATE_ACTIVATION_UPDATE_FIELDS,
        )

    def archive_candidate_evidence(
        self,
        candidate_id: str,
        *,
        kind: str,
        content: bytes,
        original_filename: str,
        mime_type: str,
        observed_at: str,
        note: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Archive one immutable artifact and bind it to a candidate revision."""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        if candidate.get("listing_draft_id"):
            raise ValueError(
                "candidate already has a listing draft; create a new candidate revision before archiving evidence"
            )
        config = EVIDENCE_KIND_CONFIG.get(str(kind or "").strip())
        if not config:
            raise ValueError("unsupported evidence kind")

        staged = self.evidence_store.stage_bytes(
            candidate_id=candidate_id,
            kind=kind,
            content=content,
            original_filename=original_filename,
            mime_type=mime_type,
            observed_at=observed_at,
            note=note,
            source_url=source_url,
        )
        artifact = dict(staged["artifact"])
        updates: Dict[str, Any] = {
            config["ref_field"]: artifact_reference(artifact["artifact_id"]),
        }
        timestamp_field = str(config.get("timestamp_field") or "")
        if timestamp_field:
            updates[timestamp_field] = artifact["observed_at"]
        try:
            updated = self.update_candidate_activation(candidate_id, updates)
            if updated is None:
                raise ValueError("candidate not found while binding evidence")
        except Exception:
            if staged.get("created"):
                self.evidence_store.mark_orphaned(artifact["artifact_id"])
            raise

        active_artifact = self.evidence_store.activate(artifact["artifact_id"])
        return {
            "candidate": updated,
            "artifact": active_artifact,
            "created": bool(staged.get("created")),
        }

    def _update_candidate_activation_fields(
        self,
        candidate_id: str,
        payload: Dict,
        *,
        allowed_fields: set,
    ) -> Optional[Dict]:
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None

        normalized: Dict[str, Any] = {}
        for key, raw_value in dict(payload or {}).items():
            if key not in allowed_fields:
                continue
            value = raw_value
            if key in _ACTIVATION_FLOAT_FIELDS:
                if value in (None, ""):
                    value = None
                else:
                    value = float(value)
                    if not math.isfinite(value) or value < 0:
                        raise ValueError(f"{key} must be a finite nonnegative number")
            elif key in _ACTIVATION_INT_FIELDS:
                if value in (None, ""):
                    value = None
                else:
                    number = float(value)
                    if not math.isfinite(number) or number < 0 or not number.is_integer():
                        raise ValueError(f"{key} must be a nonnegative integer")
                    value = int(number)
            elif key in _ACTIVATION_BOOL_FIELDS:
                value = bool(value)
            elif key in _ACTIVATION_LIST_FIELDS:
                if value is None:
                    value = []
                if not isinstance(value, list):
                    raise ValueError(f"{key} must be a list")
                value = [str(item).strip() for item in value if str(item or "").strip()]
            elif key == "specifications":
                if value is None:
                    value = {}
                if not isinstance(value, dict):
                    raise ValueError("specifications must be an object")
            elif value is None:
                value = ""
            elif isinstance(value, str):
                value = value.strip()
            normalized[key] = value

        changed = {
            key: value for key, value in normalized.items()
            if candidate.get(key) != value
        }
        if changed and candidate.get("listing_draft_id"):
            raise ValueError(
                "candidate already has a listing draft; create a new candidate revision instead of changing approved facts"
            )

        before_fingerprint = candidate_evidence_fingerprint(candidate)
        candidate.update(changed)

        economics_fields = {"supplier_price", "supplier_shipping", "target_sale_price"}
        if economics_fields.intersection(changed):
            current_economics = candidate.get("economics") if isinstance(candidate.get("economics"), dict) else {}
            candidate["economics"] = self.compute_economics(
                _safe_float(candidate.get("supplier_price")),
                _safe_float(candidate.get("supplier_shipping")),
                _safe_float(candidate.get("target_sale_price")),
                _safe_float(current_economics.get("platform_fee_rate", DEFAULT_PLATFORM_FEE_RATE)),
                _safe_float(current_economics.get("platform_fixed_fee", 0)),
                _safe_float(current_economics.get("expected_refund_rate", DEFAULT_REFUND_RATE)),
            )

        if changed:
            risk_payload = dict(candidate)
            current_economics = candidate.get("economics") if isinstance(candidate.get("economics"), dict) else {}
            risk_payload["platform_fee_rate"] = current_economics.get("platform_fee_rate", DEFAULT_PLATFORM_FEE_RATE)
            risk_payload["platform_fixed_fee"] = current_economics.get("platform_fixed_fee", 0)
            risk_payload["expected_refund_rate"] = current_economics.get("expected_refund_rate", DEFAULT_REFUND_RATE)
            risk = self.evaluate_risk(risk_payload)
            candidate["risk_level"] = risk["risk_level"]
            candidate["risk_flags"] = risk["risk_flags"]
            candidate["blocked_reason"] = risk["blocked_reason"]
            if risk["risk_level"] == "blocked":
                candidate["status"] = "blocked"

        after_fingerprint = candidate_evidence_fingerprint(candidate)
        now = _now_iso()
        if changed and before_fingerprint != after_fingerprint:
            candidate["evidence_revision"] = int(candidate.get("evidence_revision") or 1) + 1
            if candidate.get("manual_decision") is not None:
                candidate["manual_decision_invalidated_at"] = now
                candidate["manual_decision_invalidation_reason"] = (
                    "candidate evidence changed: " + ",".join(sorted(changed))
                )[:500]
            candidate["manual_decision"] = None
            candidate["manual_decision_note"] = ""
            candidate["manual_decision_override_reason"] = ""
            candidate["approved_evidence_fingerprint"] = ""
            candidate["manual_decision_approved_at"] = ""
            candidate["agent_review_stale"] = bool(candidate.get("agent_reviews"))
            if candidate.get("status") != "blocked":
                candidate["status"] = "evaluating"

        if changed:
            candidate["scoring"] = self._scorer.score(candidate)
        candidate["verified_at"] = now
        candidate["updated_at"] = now
        self._save_candidates()
        return _copy(candidate)

    VALID_DECISIONS = {"watch", "approve", "reject"}

    def set_decision(self, candidate_id: str, decision: str, note: str = "", override_reason: str = "") -> Optional[Dict]:
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        if decision not in self.VALID_DECISIONS:
            return None
        if decision == "approve" and candidate.get("status") == "blocked":
            return {"error": "cannot approve blocked candidate", "blocked_reason": candidate.get("blocked_reason", "")}
        if (
            decision == "approve"
            and candidate.get("agent_review_recommendation") == "reject"
            and not str(override_reason or "").strip()
        ):
            return {"error": "override_reason is required when agent review recommendation is reject"}
        if override_reason:
            candidate["manual_decision_override_reason"] = override_reason
        candidate["manual_decision"] = decision
        candidate["manual_decision_note"] = note
        candidate["updated_at"] = _now_iso()
        if decision == "approve":
            candidate["status"] = "approved"
            candidate["approved_evidence_fingerprint"] = candidate_evidence_fingerprint(candidate)
            candidate["manual_decision_approved_at"] = candidate["updated_at"]
            candidate["manual_decision_invalidated_at"] = ""
            candidate["manual_decision_invalidation_reason"] = ""
        elif decision == "reject":
            candidate["status"] = "rejected"
            candidate["approved_evidence_fingerprint"] = ""
            candidate["manual_decision_approved_at"] = ""
        else:
            candidate["status"] = "evaluating"
            candidate["approved_evidence_fingerprint"] = ""
            candidate["manual_decision_approved_at"] = ""
        self._save_candidates()
        return _copy(candidate)

    def get_dashboard_summary(self) -> Dict[str, Any]:
        candidates = list(self._candidates.values())
        total = len(candidates)
        blocked = sum(1 for c in candidates if c.get("status") == "blocked")
        approved = sum(1 for c in candidates if c.get("manual_decision") == "approve")
        rejected = sum(1 for c in candidates if c.get("manual_decision") == "reject")
        watch = sum(1 for c in candidates if c.get("manual_decision") == "watch")

        margins = [
            c["economics"]["gross_margin"]
            for c in candidates
            if c.get("status") != "blocked" and c.get("economics", {}).get("gross_margin") is not None
        ]
        avg_margin = round(sum(margins) / len(margins), 4) if margins else 0.0

        flag_counts: Dict[str, int] = {}
        for c in candidates:
            for flag in c.get("risk_flags", []):
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
        top_flags = sorted(flag_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_risk_flags = [f[0] for f in top_flags]

        ready = sum(
            1 for c in candidates
            if c.get("manual_decision") == "approve" and c.get("supplier_verified")
        )
        missing_verification = sum(
            1 for c in candidates
            if not c.get("supplier_verified") and c.get("status") != "blocked"
        )

        from collections import defaultdict
        daily: Dict[str, int] = defaultdict(int)
        for c in candidates:
            created = c.get("created_at", "")
            if created:
                date_str = created[:10]
                daily[date_str] += 1

        return {
            "total_candidates": total,
            "blocked_count": blocked,
            "approved_count": approved,
            "rejected_count": rejected,
            "watch_count": watch,
            "avg_gross_margin": avg_margin,
            "top_risk_flags": top_risk_flags,
            "candidates_ready_for_listing": ready,
            "candidates_missing_supplier_verification": missing_verification,
            "daily_candidate_count": dict(daily),
            "scoring_summary": self._scoring_summary(candidates),
        }

    def _scoring_summary(self, candidates: List[Dict]) -> Dict[str, Any]:
        """Aggregate scoring stats across all candidates."""
        rec_counts: Dict[str, int] = {}
        scored = 0
        total_score = 0
        for c in candidates:
            scoring = c.get("scoring", {})
            if scoring:
                scored += 1
                total_score += scoring.get("total_score", 0)
                rec = scoring.get("recommendation", "unknown")
                rec_counts[rec] = rec_counts.get(rec, 0) + 1
        return {
            "scored_count": scored,
            "avg_score": round(total_score / scored, 1) if scored else 0,
            "recommendation_counts": rec_counts,
        }

    def list_by_recommendation(self, recommendation: str) -> List[Dict]:
        """List candidates filtered by scoring recommendation (test/watch/weak/reject)."""
        results = [
            c for c in self._candidates.values()
            if c.get("scoring", {}).get("recommendation") == recommendation
        ]
        results.sort(key=lambda c: c.get("scoring", {}).get("total_score", 0), reverse=True)
        return _copy(results)

    # ------------------------------------------------------------------
    # Listing Draft
    # ------------------------------------------------------------------

    def create_listing_draft(self, candidate_id: str) -> Optional[Dict]:
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        if candidate.get("status") == "blocked":
            return {"error": "blocked candidate cannot generate listing draft"}
        if not candidate.get("supplier_verified"):
            return {"error": "supplier not verified"}
        if candidate.get("manual_decision") != "approve":
            return {"error": "manual decision must be approve"}
        approved_fingerprint = str(candidate.get("approved_evidence_fingerprint") or "").strip()
        if not approved_fingerprint or approved_fingerprint != candidate_evidence_fingerprint(candidate):
            return {"error": "manual decision is stale for the current candidate evidence"}

        draft_id = _new_id("ld")
        now = _now_iso()

        specs = candidate.get("specifications", {})
        if not isinstance(specs, dict):
            specs = {}
        spec_text = ", ".join(f"{k}: {v}" for k, v in specs.items()) if specs else "无"

        risk_flags = candidate.get("risk_flags", [])
        risk_text = "；".join(risk_flags) if risk_flags else "无特殊风险"

        structured_fields = _build_structured_listing_fields(candidate, specs)

        description = "\n".join([
            f"【商品规格】{spec_text}",
            f"【发货说明】拍下后48小时内发货，{candidate.get('sales_platform', 'xianyu')}平台发货",
            f"【售后边界】{_commercial_return_boundary(candidate)}",
            f"【风险提醒】{risk_text}",
        ])

        draft: Dict[str, Any] = {
            "id": draft_id,
            "draft_id": draft_id,
            "candidate_id": candidate_id,
            "source": f"resale:{candidate_id}",
            "title": candidate.get("title", ""),
            "description": description,
            "price": candidate.get("target_sale_price", 0),
            "stock": 1,
            "delivery_type": "shipping",
            "category": candidate.get("category", ""),
            "images": candidate.get("images", []),
            "specifications": specs,
            "sales_platform": candidate.get("sales_platform", "xianyu"),
            "market_scope": candidate.get("market_scope", "domestic"),
            "gross_margin": candidate.get("economics", {}).get("gross_margin", 0),
            "supplier_verified": True,
            "status": "draft",
            "work_order_id": None,
            "created_at": now,
            "updated_at": now,
        }
        draft.update(structured_fields)
        self._listing_drafts[draft_id] = draft
        self._save_listing_drafts()
        self._write_executable_draft(draft)
        # Link draft to candidate
        candidate["listing_draft_id"] = draft_id
        candidate["updated_at"] = now
        self._save_candidates()
        return _copy(draft)

    def get_listing_draft(self, draft_id: str) -> Optional[Dict]:
        d = self._listing_drafts.get(draft_id)
        return _copy(d) if d else None

    def update_listing_draft(self, draft_id: str, updates: Dict) -> Optional[Dict]:
        draft = self._listing_drafts.get(draft_id)
        if not draft:
            return None
        for key, value in updates.items():
            if key in ("status", "work_order_id"):
                draft[key] = value
        draft["updated_at"] = _now_iso()
        self._save_listing_drafts()
        return _copy(draft)

    # ------------------------------------------------------------------
    # Purchase Task CRUD
    # ------------------------------------------------------------------

    VALID_TASK_STATUSES = {"pending_manual_order", "ordered", "cancelled", "failed"}

    def create_purchase_task(self, draft: Dict) -> Dict:
        """Create a purchase task from an executed purchase draft."""
        task_id = _new_id("pt")
        now = _now_iso()

        risk_warnings = draft.get("risk_warnings", [])
        if "MVP 阶段不会自动付款或提交供应商订单" not in risk_warnings:
            risk_warnings.append("MVP 阶段不会自动付款或提交供应商订单")

        task: Dict[str, Any] = {
            "id": task_id,
            "purchase_task_id": task_id,
            "purchase_draft_id": draft.get("draft_id", ""),
            "plan_id": draft.get("plan_id", ""),
            "candidate_id": draft.get("candidate_id", ""),
            "supplier_url": draft.get("supplier_url", ""),
            "product_specs": draft.get("product_specs", {}),
            "quantity": draft.get("quantity", 1),
            "masked_buyer_address": draft.get("buyer_address", ""),
            "commercial_trial_id": draft.get("commercial_trial_id", ""),
            "xianyu_order_id": draft.get("xianyu_order_id", ""),
            "cookie_id": draft.get("cookie_id", ""),
            "item_id": draft.get("item_id", ""),
            "sale_amount": draft.get("sale_amount", 0),
            "fulfillment_lock_owner": draft.get("fulfillment_lock_owner", ""),
            "payable_amount": draft.get("total_cost", 0),
            "expected_profit": draft.get("profit_review", {}).get("expected_profit", 0),
            "risk_warnings": risk_warnings,
            "status": "pending_manual_order",
            "manual_order_note": "",
            "supplier_order_id": "",
            "tracking_number": "",
            "created_at": now,
            "updated_at": now,
            "ordered_at": None,
        }
        self._purchase_tasks[task_id] = task
        self._save_purchase_tasks()
        return _copy(task)

    def get_purchase_task(self, task_id: str) -> Optional[Dict]:
        t = self._purchase_tasks.get(task_id)
        return _copy(t) if t else None

    def list_purchase_tasks(self, status: Optional[str] = None) -> List[Dict]:
        results = list(self._purchase_tasks.values())
        if status:
            results = [t for t in results if t.get("status") == status]
        results.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return _copy(results)

    def update_purchase_task(self, task_id: str, updates: Dict) -> Optional[Dict]:
        task = self._purchase_tasks.get(task_id)
        if not task:
            return None
        for key, value in updates.items():
            if key in ("status", "manual_order_note", "supplier_order_id", "tracking_number", "ordered_at"):
                task[key] = value
        task["updated_at"] = _now_iso()
        self._save_purchase_tasks()
        return _copy(task)

    # ------------------------------------------------------------------
    # Resale Order CRUD
    # ------------------------------------------------------------------

    VALID_ORDER_STATUSES = {
        "pending_purchase", "purchased", "shipped", "completed",
        "cancelled", "refund_risk", "refunded",
    }

    def create_resale_order(self, payload: Dict) -> Dict:
        """Create a resale order for reconciliation."""
        order_id = _new_id("ro")
        now = _now_iso()

        sale_amount = float(payload.get("sale_amount", 0))
        supplier_cost = float(payload.get("supplier_cost", 0))
        shipping_cost = float(payload.get("shipping_cost", 0))
        platform_fee = float(payload.get("platform_fee", 0))

        actual_profit = round(sale_amount - supplier_cost - shipping_cost - platform_fee, 2)
        actual_margin = round(actual_profit / sale_amount, 4) if sale_amount > 0 else 0.0

        order: Dict[str, Any] = {
            "id": order_id,
            "resale_order_id": order_id,
            "xianyu_order_id": payload.get("xianyu_order_id", ""),
            "purchase_task_id": payload.get("purchase_task_id", ""),
            "purchase_draft_id": payload.get("purchase_draft_id", ""),
            "plan_id": payload.get("plan_id", ""),
            "candidate_id": payload.get("candidate_id", ""),
            "listing_draft_id": payload.get("listing_draft_id", ""),
            "commercial_trial_id": payload.get("commercial_trial_id", ""),
            "cookie_id": payload.get("cookie_id", ""),
            "item_id": payload.get("item_id", ""),
            "fulfillment_lock_owner": payload.get("fulfillment_lock_owner", ""),
            "supplier_order_id": payload.get("supplier_order_id", ""),
            "supplier_tracking_number": payload.get("supplier_tracking_number", ""),
            "xianyu_tracking_number": payload.get("xianyu_tracking_number", ""),
            "sale_amount": sale_amount,
            "supplier_cost": supplier_cost,
            "shipping_cost": shipping_cost,
            "platform_fee": platform_fee,
            "actual_profit": actual_profit,
            "actual_margin": actual_margin,
            "status": payload.get("status", "pending_purchase"),
            "buyer_note": payload.get("buyer_note", ""),
            "supplier_note": payload.get("supplier_note", ""),
            "aftersale_note": payload.get("aftersale_note", ""),
            "financial_evidence_note": payload.get("financial_evidence_note", ""),
            "financial_evidence_ref": payload.get("financial_evidence_ref", ""),
            "financial_reconciled_at": payload.get("financial_reconciled_at", ""),
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        self._resale_orders[order_id] = order
        self._save_resale_orders()
        return _copy(order)

    def get_resale_order(self, order_id: str) -> Optional[Dict]:
        o = self._resale_orders.get(order_id)
        return _copy(o) if o else None

    def list_resale_orders(self, status: Optional[str] = None) -> List[Dict]:
        results = list(self._resale_orders.values())
        if status:
            results = [o for o in results if o.get("status") == status]
        results.sort(key=lambda o: o.get("created_at", ""), reverse=True)
        return _copy(results)

    def update_resale_order(self, order_id: str, updates: Dict) -> Optional[Dict]:
        order = self._resale_orders.get(order_id)
        if not order:
            return None

        recalc = False
        for key, value in updates.items():
            if key in ("status", "xianyu_order_id", "supplier_order_id",
                       "supplier_tracking_number", "xianyu_tracking_number",
                       "buyer_note", "supplier_note", "aftersale_note",
                       "financial_evidence_note", "financial_evidence_ref",
                       "financial_reconciled_at"):
                order[key] = value
            elif key in ("sale_amount", "supplier_cost", "shipping_cost", "platform_fee"):
                order[key] = float(value)
                recalc = True

        # Recalculate profit if financials changed
        if recalc:
            sale = order.get("sale_amount", 0)
            order["actual_profit"] = round(
                sale - order.get("supplier_cost", 0) - order.get("shipping_cost", 0) - order.get("platform_fee", 0), 2
            )
            order["actual_margin"] = round(order["actual_profit"] / sale, 4) if sale > 0 else 0.0

        order["updated_at"] = _now_iso()
        if updates.get("status") == "completed":
            order["completed_at"] = _now_iso()

        self._save_resale_orders()
        return _copy(order)

    def create_order_from_task(
        self,
        purchase_task_id: str,
        overrides: Optional[Dict] = None,
        allow_duplicate: bool = False,
    ) -> Optional[Dict]:
        """Create a ResaleOrder from a PurchaseTask, auto-filling related fields."""
        task = self._purchase_tasks.get(purchase_task_id)
        if not task:
            return None

        # Check for duplicate active order on same task
        if not allow_duplicate:
            for o in self._resale_orders.values():
                if o.get("purchase_task_id") == purchase_task_id and o.get("status") not in ("cancelled", "refunded"):
                    return {"error": "active order already exists for this purchase task"}

        overrides = overrides or {}

        xianyu_order_id = str(
            overrides.get("xianyu_order_id") or task.get("xianyu_order_id") or ""
        ).strip()
        if xianyu_order_id and not allow_duplicate:
            for order in self._resale_orders.values():
                if (
                    str(order.get("xianyu_order_id") or "").strip() == xianyu_order_id
                    and order.get("status") not in ("cancelled", "refunded")
                ):
                    return {"error": "active order already exists for this Xianyu order"}

        # Auto-determine status from task status
        default_status = "purchased" if task.get("status") == "ordered" else "pending_purchase"

        # Try to find listing_draft_id from candidate
        candidate_id = task.get("candidate_id", "")
        listing_draft_id = ""
        if candidate_id:
            candidate = self._candidates.get(candidate_id)
            if candidate:
                listing_draft_id = candidate.get("listing_draft_id", "")

        payload = {
            "purchase_task_id": purchase_task_id,
            "purchase_draft_id": task.get("purchase_draft_id", ""),
            "plan_id": task.get("plan_id", ""),
            "candidate_id": candidate_id,
            "listing_draft_id": listing_draft_id,
            "supplier_order_id": task.get("supplier_order_id", ""),
            "supplier_tracking_number": task.get("tracking_number", ""),
            "supplier_cost": task.get("payable_amount", 0),
            "shipping_cost": overrides.get("shipping_cost", 0),
            "status": overrides.get("status", default_status),
            "xianyu_order_id": xianyu_order_id,
            "commercial_trial_id": task.get("commercial_trial_id", ""),
            "cookie_id": task.get("cookie_id", ""),
            "item_id": task.get("item_id", ""),
            "fulfillment_lock_owner": task.get("fulfillment_lock_owner", ""),
            "sale_amount": overrides.get("sale_amount") or task.get("sale_amount", 0),
            "platform_fee": overrides.get("platform_fee", 0),
            "buyer_note": overrides.get("buyer_note", ""),
            "supplier_note": overrides.get("supplier_note", ""),
            "aftersale_note": overrides.get("aftersale_note", ""),
            "financial_evidence_note": overrides.get("financial_evidence_note", ""),
            "financial_evidence_ref": overrides.get("financial_evidence_ref", ""),
            "financial_reconciled_at": overrides.get("financial_reconciled_at", ""),
        }
        return self.create_resale_order(payload)

    def get_order_detail(self, order_id: str) -> Optional[Dict]:
        """Return the full chain: order + task + draft + plan + candidate + listing_draft."""
        order = self._resale_orders.get(order_id)
        if not order:
            return None

        task_id = order.get("purchase_task_id", "")
        draft_id = order.get("purchase_draft_id", "")
        plan_id = order.get("plan_id", "")
        candidate_id = order.get("candidate_id", "")
        listing_draft_id = order.get("listing_draft_id", "")

        return {
            "order": _copy(order),
            "purchase_task": _copy(self._purchase_tasks.get(task_id)) if task_id else None,
            "purchase_draft": _copy(self._drafts.get(draft_id)) if draft_id else None,
            "plan": _copy(self._plans.get(plan_id)) if plan_id else None,
            "candidate": _copy(self._candidates.get(candidate_id)) if candidate_id else None,
            "listing_draft": _copy(self._listing_drafts.get(listing_draft_id)) if listing_draft_id else None,
        }

    # ------------------------------------------------------------------
    # Agent review
    # ------------------------------------------------------------------

    def save_agent_review(
        self,
        candidate_id: str,
        discussion: Dict[str, Any],
        structured_reviews: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict]:
        """Save agent discussion and structured reviews to candidate."""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        candidate["agent_reviews"] = discussion
        candidate["agent_reviewed_at"] = _now_iso()
        if structured_reviews:
            candidate["agent_structured_reviews"] = structured_reviews
            aggregate = self.aggregate_agent_review(candidate_id)
            if aggregate:
                candidate["agent_review_score"] = aggregate["agent_review_score"]
                candidate["agent_review_recommendation"] = aggregate["agent_review_recommendation"]
                candidate["agent_review_reasons"] = aggregate["agent_review_reasons"]
                candidate["blocking_flags"] = aggregate["blocking_flags"]
        candidate["updated_at"] = _now_iso()
        self._save_candidates()
        return _copy(candidate)

    def aggregate_agent_review(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Compute aggregate score from structured agent reviews."""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return None
        reviews = candidate.get("agent_structured_reviews", [])
        if not reviews:
            return None

        research = next((r for r in reviews if r.get("agent") == "resale_research"), None)
        risk = next((r for r in reviews if r.get("agent") == "resale_risk"), None)
        pricing = next((r for r in reviews if r.get("agent") == "resale_pricing"), None)

        # Compute sub-averages
        research_avg = 0.0
        if research:
            research_avg = (research.get("supplier_score", 0) + research.get("demand_score", 0)) / 2

        risk_avg = 0.0
        if risk:
            risk_avg = (
                risk.get("compliance_score", 0)
                + risk.get("fulfillment_risk_score", 0)
                + risk.get("aftersale_risk_score", 0)
            ) / 3

        pricing_avg = 0.0
        if pricing:
            pricing_avg = (pricing.get("margin_score", 0) + pricing.get("price_competitiveness_score", 0)) / 2

        # Weighted average: research 25%, risk 40%, pricing 35%
        agent_review_score = round(research_avg * 0.25 + risk_avg * 0.40 + pricing_avg * 0.35, 1)

        # Blocking flags: union of all agents
        blocking_flags: List[str] = []
        if risk:
            blocking_flags.extend(risk.get("blocking_flags", []))

        # Recommendation
        if blocking_flags or agent_review_score < 50:
            recommendation = "reject"
        elif agent_review_score >= 75:
            recommendation = "approve"
        else:
            recommendation = "watch"

        # Reasons
        reasons = []
        if research:
            reasons.append(research.get("summary", ""))
        if risk:
            reasons.append(risk.get("summary", ""))
        if pricing:
            reasons.append(pricing.get("summary", ""))

        # If blocked status, override recommendation
        if candidate.get("status") == "blocked":
            recommendation = "reject"
            if "高风险已阻断" not in blocking_flags:
                blocking_flags.append("高风险已阻断")

        return {
            "agent_review_score": agent_review_score,
            "agent_review_recommendation": recommendation,
            "agent_review_reasons": [r for r in reasons if r],
            "blocking_flags": blocking_flags,
        }

    def get_orders_dashboard(self) -> Dict[str, Any]:
        orders = list(self._resale_orders.values())
        total = len(orders)

        status_counts: Dict[str, int] = {}
        for o in orders:
            s = o.get("status", "pending_purchase")
            status_counts[s] = status_counts.get(s, 0) + 1

        total_sale = round(sum(o.get("sale_amount", 0) for o in orders), 2)
        total_profit = round(sum(o.get("actual_profit", 0) for o in orders), 2)
        margins = [o["actual_margin"] for o in orders if o.get("sale_amount", 0) > 0]
        avg_margin = round(sum(margins) / len(margins), 4) if margins else 0.0

        missing_tracking = sum(
            1 for o in orders
            if o.get("status") in ("purchased", "shipped")
            and not o.get("supplier_tracking_number")
        )
        negative_profit = sum(1 for o in orders if o.get("actual_profit", 0) < 0)

        return {
            "total_orders": total,
            "purchased_count": status_counts.get("purchased", 0),
            "shipped_count": status_counts.get("shipped", 0),
            "completed_count": status_counts.get("completed", 0),
            "refund_risk_count": status_counts.get("refund_risk", 0),
            "refunded_count": status_counts.get("refunded", 0),
            "total_sale_amount": total_sale,
            "total_actual_profit": total_profit,
            "avg_actual_margin": avg_margin,
            "orders_missing_tracking": missing_tracking,
            "orders_with_negative_profit": negative_profit,
        }


# Module-level singleton — initialized by server startup
resale_manager: Optional[ResaleManager] = None


def init_resale_manager(data_dir: Path) -> ResaleManager:
    global resale_manager
    resale_manager = ResaleManager(data_dir)
    return resale_manager
