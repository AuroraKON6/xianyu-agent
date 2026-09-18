"""Physical product sourcing and supplier management for dropshipping.

Inspired by Xianyu article's sourcing methodology:
- Direct factory price negotiation
- Stealth shipping (no PDD branding)
- Multi-platform sourcing (PDD, 1688, Taobao)
- Material-based product discovery
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class SourcePlatform(Enum):
    PINDUODUO = "pinduoduo"
    TAOBAO = "taobao"
    ALIBABA_1688 = "1688"
    JD = "jd"


# City mapping for product categories (from article insights)
CATEGORY_CITY_MAP = {
    "餐饮": ["成都", "重庆", "广州", "长沙"],
    "厨具": ["广州", "成都", "武汉", "郑州"],
    "机械": ["河北", "山东", "浙江"],
    "家具": ["佛山", "东莞", "赣州", "成都"],
    "养殖": ["山东", "河南", "河北"],
    "办公": ["杭州", "广州", "深圳", "成都"],
    "服装": ["广州", "杭州", "义乌", "东莞"],
    "建材": ["佛山", "泉州", "淄博"],
    "默认": ["北京", "上海", "广州", "深圳", "杭州"],
}


# Shipping instruction for suppliers. This is an operator-to-supplier
# order note only; it makes no buyer-facing claims.
# WP12: the previous fixed buyer-facing scripts ("我们在其他平台也有店
# 铺"、"好评返现" etc.) were removed -- fabricated supply relationships
# and cashback solicitation must never be generated from templates.
# Buyer responses are now assembled from verified facts only (see
# PhysicalSourcingService.generate_buyer_response).
STEALTH_TEMPLATES = {
    "order_note": "您好，已下单，麻烦快递面单不要放平台店铺信息，快递里面不要放价格表和小卡片，不要给我发短信打扰，无痕发货谢谢",
}


@dataclass
class Supplier:
    """A physical product supplier."""
    id: str
    name: str
    platform: str
    contact: str
    product_categories: List[str]
    price_list: Dict[str, float] = field(default_factory=dict)
    is_factory_direct: bool = False
    cooperation_status: str = "new"  # new, negotiating, active, paused
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SourcedProduct:
    """A product found on sourcing platforms."""
    id: str
    title: str
    platform: str
    supplier_id: str
    supplier_name: str
    cost_price: float
    market_price: float
    suggested_resale_price: float
    profit_margin: float
    shipping_cost: float
    category: str
    image_url: str
    detail_url: str
    is_bulk_supported: bool = False
    min_order_qty: int = 1
    raw_data: Dict[str, Any] = field(default_factory=dict)
    stealth_ready: bool = False
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MaterialKeyword:
    """Material-based product discovery keyword."""
    material: str
    related_products: List[str]
    search_volume_hint: str = ""


class PhysicalSourcingManager:
    """Manage physical product sourcing, suppliers, and profit calculation.

    Integrates with existing CommerceWorkflow as a new sourcing source.
    """

    def __init__(self, settings: Any, storage: Any, llm: Any) -> None:
        self.settings = settings
        self.storage = storage
        self.llm = llm
        self._suppliers: Dict[str, Supplier] = {}
        self._sourced_products: Dict[str, SourcedProduct] = {}
        self._load_data()

    def _data_dir(self) -> Path:
        path = self.storage.data_dir / "physical_sourcing"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load_data(self) -> None:
        suppliers_file = self._data_dir() / "suppliers.json"
        if suppliers_file.exists():
            try:
                data = json.loads(suppliers_file.read_text(encoding="utf-8"))
                for item in data:
                    self._suppliers[item["id"]] = Supplier(**item)
            except Exception as exc:
                logger.warning("Failed to load suppliers: %s", exc)

    def _save_data(self) -> None:
        suppliers_file = self._data_dir() / "suppliers.json"
        suppliers_file.write_text(
            json.dumps([s.to_dict() for s in self._suppliers.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ====== Supplier Management ======

    def add_supplier(
        self,
        name: str,
        platform: str,
        contact: str,
        product_categories: List[str],
        is_factory_direct: bool = False,
        notes: str = "",
    ) -> Dict[str, Any]:
        supplier = Supplier(
            id=uuid4().hex[:12],
            name=name,
            platform=platform,
            contact=contact,
            product_categories=product_categories,
            is_factory_direct=is_factory_direct,
            cooperation_status="new",
            notes=notes,
            created_at=__import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._suppliers[supplier.id] = supplier
        self._save_data()
        return supplier.to_dict()

    def update_supplier(self, supplier_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        supplier = self._suppliers.get(supplier_id)
        if not supplier:
            return None
        for key, value in updates.items():
            if hasattr(supplier, key):
                setattr(supplier, key, value)
        self._save_data()
        return supplier.to_dict()

    def remove_supplier(self, supplier_id: str) -> bool:
        if supplier_id in self._suppliers:
            del self._suppliers[supplier_id]
            self._save_data()
            return True
        return False

    def list_suppliers(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        suppliers = list(self._suppliers.values())
        if platform:
            suppliers = [s for s in suppliers if s.platform == platform]
        return [s.to_dict() for s in suppliers]

    def get_supplier(self, supplier_id: str) -> Optional[Dict[str, Any]]:
        supplier = self._suppliers.get(supplier_id)
        return supplier.to_dict() if supplier else None

    # ====== Material-based Discovery ======

    def discover_by_material(self, material: str, max_products: int = 20) -> List[Dict[str, Any]]:
        """Discover products by raw material (原材料选品法).

        Search PDD/1688 for products made of given material, then cross-check
        Xianyu demand.
        """
        import time

        # Use existing cn-ecommerce-search or web search capability
        search_queries = self._generate_material_queries(material)
        discovered = []

        for query in search_queries[:5]:
            # This would integrate with cn-ecommerce-search skill or web search
            # For now, return structured search plan
            discovered.append({
                "query": query,
                "platforms": ["pinduoduo", "1688"],
                "search_url": f"https://mobile.yangkeduo.com/search_result.html?search_key={__import__('urllib.parse').quote(query)}",
                "material": material,
            })

        self.storage.append_event("sourcing.material_discovery", {
            "material": material,
            "queries": search_queries,
            "count": len(discovered),
        })

        return discovered

    def _generate_material_queries(self, material: str) -> List[str]:
        """Generate product search queries from material keyword."""
        common_suffixes = [
            "商用", "家用", "工业", "定制", "批发",
            "操作台", "柜子", "架子", "桌子", "椅子",
            "设备", "工具", "器材", "制品", "配件",
        ]
        queries = [f"{material}{suffix}" for suffix in common_suffixes]
        # Add LLM expansion if available
        if self.llm and getattr(self.settings, "deepseek_api_key", ""):
            try:
                prompt = f"List 10 commercial/industrial products made primarily of {material}. Return as comma-separated list."
                result = self.llm.chat(
                    "You are a product sourcing expert. List products by material.",
                    prompt,
                )
                expanded = [q.strip() for q in result.split(",") if q.strip()]
                queries.extend(expanded)
            except Exception:
                pass
        return queries[:15]

    # ====== Profit Calculation ======

    def calculate_profit(
        self,
        cost_price: float,
        resale_price: float,
        shipping_cost: float = 0.0,
        platform_fee_rate: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Calculate profit margins for physical products."""
        if platform_fee_rate is None:
            platform_fee_rate = getattr(self.settings, "default_platform_fee_rate", 0.016)

        platform_fee = resale_price * platform_fee_rate
        total_cost = cost_price + shipping_cost + platform_fee
        profit = resale_price - total_cost
        profit_margin = profit / resale_price if resale_price > 0 else 0
        payback_ratio = profit / cost_price if cost_price > 0 else 0

        return {
            "cost_price": cost_price,
            "resale_price": resale_price,
            "shipping_cost": shipping_cost,
            "platform_fee": round(platform_fee, 2),
            "platform_fee_rate": platform_fee_rate,
            "total_cost": round(total_cost, 2),
            "profit": round(profit, 2),
            "profit_margin": round(profit_margin, 4),
            "payback_ratio": round(payback_ratio, 4),
            "is_viable": profit_margin >= getattr(self.settings, "min_profit_rate", 0.30),
        }

    def evaluate_sourcing_opportunity(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a physical product sourcing opportunity."""
        cost = float(payload.get("cost_price") or payload.get("cost") or 0)
        resale = float(payload.get("resale_price") or payload.get("price") or 0)
        shipping = float(payload.get("shipping_cost") or 0)
        category = str(payload.get("category") or "")

        economics = self.calculate_profit(cost, resale, shipping)

        # Determine product type
        is_standard = self._is_standard_product(category, payload.get("title", ""))

        # Get recommended cities
        cities = self._get_recommended_cities(category)

        # Generate listing strategy
        listing_strategy = self._generate_listing_strategy(
            economics["profit"],
            economics["profit_margin"],
            is_standard,
            cities,
        )

        return {
            "id": uuid4().hex[:12],
            "type": "physical_sourcing_opportunity",
            "product": payload,
            "economics": economics,
            "is_standard_product": is_standard,
            "recommended_cities": cities,
            "listing_strategy": listing_strategy,
            "stealth_templates": {
                "order_note": STEALTH_TEMPLATES["order_note"],
                "buyer_pdd_logo": STEALTH_TEMPLATES["buyer_pdd_logo"],
                "buyer_pdd_sms": STEALTH_TEMPLATES["buyer_pdd_sms"],
            },
            "created_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _is_standard_product(self, category: str, title: str) -> bool:
        """Determine if product is standard (标品) or non-standard (非标品)."""
        standard_keywords = [
            "手机", "iPhone", "华为", "小米", "美的", "格力", "海尔",
            "品牌", "正品", "官方", "原厂",
        ]
        title_lower = title.lower()
        return any(kw in title_lower for kw in standard_keywords)

    def _get_recommended_cities(self, category: str) -> List[str]:
        """Get recommended publish cities based on product category."""
        for key, cities in CATEGORY_CITY_MAP.items():
            if key in category:
                return cities
        return CATEGORY_CITY_MAP["默认"]

    def _generate_listing_strategy(
        self,
        profit: float,
        margin: float,
        is_standard: bool,
        cities: List[str],
    ) -> Dict[str, Any]:
        """Generate listing strategy based on product characteristics."""
        strategy = {
            "product_type": "standard" if is_standard else "non_standard",
            "pricing_approach": "competitive" if is_standard else "value_based",
            "description_focus": "price" if is_standard else "details_scenario",
            "recommended_cities": cities[:3],
        }

        # Low-price引流 strategy
        if profit > 50 and margin > 0.3:
            strategy["bait_price_ratio"] = 0.5
            strategy["bait_strategy"] = "similar_product_combo"
            strategy["note"] = "可用同类产品组合法做低价引流"
        elif profit > 20:
            strategy["bait_price_ratio"] = 0.6
            strategy["bait_strategy"] = "fictional_size"
            strategy["note"] = "可用虚构尺寸法做低价引流"
        else:
            strategy["bait_price_ratio"] = 1.0
            strategy["bait_strategy"] = "none"
            strategy["note"] = "利润空间小，不建议低价引流"

        return strategy

    # ====== Stealth Shipping ======

    def get_stealth_templates(self, scenario: Optional[str] = None) -> Dict[str, str]:
        """Get stealth shipping templates for different scenarios."""
        if scenario:
            return {scenario: STEALTH_TEMPLATES.get(scenario, "")}
        return dict(STEALTH_TEMPLATES)

    def generate_order_note(self, supplier_name: str = "") -> str:
        """Generate order note for stealth shipping."""
        template = STEALTH_TEMPLATES["order_note"]
        if supplier_name:
            return f"【{supplier_name}】{template}"
        return template

    def generate_buyer_response(self, issue_type: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Generate a buyer response from VERIFIED facts only.

        ``context["verified_facts"]`` maps fact keys to text the
        operator has actually verified (e.g. ``fulfillment_note``).
        Without verified facts the response stays neutral and makes no
        claims about stores, platforms, cashback or quality guarantees.
        """
        context = context or {}
        verified = context.get("verified_facts") or {}
        parts = [str(verified[key]) for key in ("fulfillment_note", "quality_note") if verified.get(key)]
        if parts:
            return "您好，" + "".join(parts) + "商品有问题随时找我处理。"
        return "您好，有任何问题我们会妥善处理的，请放心。"

    # ====== Price Monitoring ======

    def save_sourced_product(self, product: SourcedProduct) -> Dict[str, Any]:
        self._sourced_products[product.id] = product
        products_file = self._data_dir() / "sourced_products.json"
        products_file.write_text(
            json.dumps([p.to_dict() for p in self._sourced_products.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return product.to_dict()

    def list_sourced_products(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        products = list(self._sourced_products.values())
        if category:
            products = [p for p in products if category in p.category]
        return [p.to_dict() for p in products]

    def get_price_change_alert(self, product_id: str, new_cost: float) -> Optional[Dict[str, Any]]:
        """Check if supplier price changed and recalculate viability."""
        product = self._sourced_products.get(product_id)
        if not product:
            return None

        old_cost = product.cost_price
        if abs(new_cost - old_cost) < 0.01:
            return None

        product.cost_price = new_cost
        economics = self.calculate_profit(new_cost, product.suggested_resale_price, product.shipping_cost)

        return {
            "product_id": product_id,
            "old_cost": old_cost,
            "new_cost": new_cost,
            "price_change_pct": round((new_cost - old_cost) / old_cost * 100, 2),
            "new_profit_margin": economics["profit_margin"],
            "is_still_viable": economics["is_viable"],
            "alert_level": "warning" if not economics["is_viable"] else "info",
        }


# ====== Helper functions for integration ======

def looks_like_sourcing_request(text: str) -> bool:
    """Check if text is a sourcing request."""
    patterns = [
        r"货源[:：]",
        r"选品[:：]",
        r"搜货[:：]",
        r"原材料[:：]",
        r"进货[:：]",
        r"供应商[:：]",
        r"找厂家",
        r"拿货",
        r"成本",
        r"利润",
    ]
    return any(re.search(p, text) for p in patterns)


def sourcing_payload_from_text(text: str) -> Dict[str, Any]:
    """Extract sourcing parameters from text."""
    # Try to extract cost and price
    cost_match = re.search(r"成本[\s:：]*(\d+(?:\.\d+)?)", text)
    price_match = re.search(r"售价[\s:：]*(\d+(?:\.\d+)?)", text)
    shipping_match = re.search(r"运费[\s:：]*(\d+(?:\.\d+)?)", text)

    return {
        "title": text,
        "cost_price": float(cost_match.group(1)) if cost_match else None,
        "resale_price": float(price_match.group(1)) if price_match else None,
        "shipping_cost": float(shipping_match.group(1)) if shipping_match else 0,
        "category": "",
    }
