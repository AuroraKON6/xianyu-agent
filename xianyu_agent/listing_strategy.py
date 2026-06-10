"""Listing strategy for Xianyu physical product dropshipping.

Implements article's key strategies:
- Low-price bait (50% of actual price)
- City selection based on product category / shipping origin
- Standard vs non-standard product differentiation
- First image optimization
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


# Product category to city mapping (from article insights)
CATEGORY_CITIES = {
    "餐饮": ["成都", "重庆", "广州", "长沙", "武汉"],
    "厨具": ["广州", "成都", "武汉", "郑州", "西安"],
    "机械": ["河北", "山东", "浙江", "江苏"],
    "液压": ["河北", "山东", "江苏"],
    "家具": ["佛山", "东莞", "赣州", "成都", "苏州"],
    "办公": ["杭州", "广州", "深圳", "成都", "武汉"],
    "养殖": ["山东", "河南", "河北", "江苏"],
    "服装": ["广州", "杭州", "义乌", "东莞", "深圳"],
    "建材": ["佛山", "泉州", "淄博", "临沂"],
    "电子": ["深圳", "东莞", "苏州", "成都"],
    "默认": ["北京", "上海", "广州", "深圳", "杭州", "成都"],
}

# Shipping origin city adjacencies (for "nearby tier-1/2 cities" strategy)
CITY_ADJACENT = {
    "河北": ["石家庄", "唐山", "北京", "天津"],
    "山东": ["济南", "青岛", "烟台", "潍坊"],
    "浙江": ["杭州", "宁波", "温州", "金华"],
    "江苏": ["南京", "苏州", "无锡", "常州"],
    "广东": ["广州", "深圳", "佛山", "东莞"],
    "四川": ["成都", "绵阳", "德阳"],
    "河南": ["郑州", "洛阳", "新乡"],
}


@dataclass
class PriceStrategy:
    """Pricing strategy for a listing."""
    actual_price: float
    bait_price: float
    bait_price_ratio: float
    strategy_type: str  # similar_product_combo, fictional_size, title_contrast
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LocationStrategy:
    """Location/publish city strategy."""
    primary_city: str
    alternative_cities: List[str]
    reasoning: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImageStrategy:
    """First image strategy recommendations."""
    image_type: str  # scene, pile, text_overlay, plain
    recommendations: List[str]
    text_overlay_suggestions: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ListingStrategyPlan:
    """Complete strategy plan for a physical product listing."""
    id: str
    product_title: str
    category: str
    is_standard: bool
    price_strategy: PriceStrategy
    location_strategy: LocationStrategy
    image_strategy: ImageStrategy
    description_focus: str
    tag_suggestions: List[str]
    competitive_notes: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ListingStrategyEngine:
    """Generate optimized listing strategies based on product characteristics."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def create_strategy(
        self,
        title: str,
        category: str,
        actual_price: float,
        cost_price: float,
        profit_margin: float,
        shipping_origin: str = "",
        is_standard: Optional[bool] = None,
    ) -> ListingStrategyPlan:
        """Create a complete listing strategy for a physical product."""
        if is_standard is None:
            is_standard = self._detect_standard_product(title, category)

        price_strategy = self._create_price_strategy(actual_price, profit_margin)
        location_strategy = self._create_location_strategy(category, shipping_origin)
        image_strategy = self._create_image_strategy(category, is_standard)
        description_focus = self._get_description_focus(is_standard, category)
        tags = self._generate_tags(title, category)

        return ListingStrategyPlan(
            id=uuid4().hex[:12],
            product_title=title,
            category=category,
            is_standard=is_standard,
            price_strategy=price_strategy,
            location_strategy=location_strategy,
            image_strategy=image_strategy,
            description_focus=description_focus,
            tag_suggestions=tags,
            created_at=__import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _detect_standard_product(self, title: str, category: str) -> bool:
        """Detect if product is standard (标品) or non-standard (非标品)."""
        standard_indicators = [
            "iPhone", "iPad", "华为", "小米", "OPPO", "vivo", "三星",
            "美的", "格力", "海尔", "西门子", "松下",
            "品牌", "正品", "官方", "原厂", "原装",
            "统一价", "全国统一", "官方定价",
        ]
        non_standard_indicators = [
            "定制", "定做", "加工", "厂家", "工厂", "批发",
            "不锈钢", "实木", "铁艺", "塑料", "玻璃",
            "商用", "工业", "养殖", "农业", "工地",
        ]

        title_lower = title.lower()
        std_score = sum(1 for ind in standard_indicators if ind in title_lower)
        non_std_score = sum(1 for ind in non_standard_indicators if ind in title_lower)

        return std_score > non_std_score

    def _create_price_strategy(self, actual_price: float, profit_margin: float) -> PriceStrategy:
        """Create pricing strategy with low-price bait."""
        if profit_margin >= 0.5 and actual_price >= 200:
            # High margin + high price: use similar product combo
            bait_price = actual_price * 0.5
            return PriceStrategy(
                actual_price=actual_price,
                bait_price=round(bait_price, 2),
                bait_price_ratio=0.5,
                strategy_type="similar_product_combo",
                explanation="用同类低价产品引流，文案写清楚标价对应哪款",
            )
        elif profit_margin >= 0.3 and actual_price >= 100:
            # Medium margin: use fictional size
            bait_price = actual_price * 0.5
            return PriceStrategy(
                actual_price=actual_price,
                bait_price=round(bait_price, 2),
                bait_price_ratio=0.5,
                strategy_type="fictional_size",
                explanation="虚构小尺寸产品作为引流价，文案标注缺货",
            )
        elif profit_margin >= 0.2:
            # Lower margin: use title contrast
            bait_price = actual_price * 0.6
            return PriceStrategy(
                actual_price=actual_price,
                bait_price=round(bait_price, 2),
                bait_price_ratio=0.6,
                strategy_type="title_contrast",
                explanation="标题中大小规格价格差异，首图展示大规格",
            )
        else:
            # Too low margin: no bait
            return PriceStrategy(
                actual_price=actual_price,
                bait_price=actual_price,
                bait_price_ratio=1.0,
                strategy_type="none",
                explanation="利润空间不足，不建议低价引流",
            )

    def _create_location_strategy(self, category: str, shipping_origin: str) -> LocationStrategy:
        """Create location strategy based on category and shipping origin."""
        cities = []
        reasoning = ""

        # Strategy 1: Based on category
        for key, city_list in CATEGORY_CITIES.items():
            if key in category:
                cities = city_list[:3]
                reasoning = f"根据产品类别'{category}'选择产业集中城市"
                break

        # Strategy 2: Based on shipping origin (if provided)
        if shipping_origin and not cities:
            for province, adjacent in CITY_ADJACENT.items():
                if province in shipping_origin:
                    cities = adjacent[:3]
                    reasoning = f"根据发货地'{shipping_origin}'选择周边一二线城市"
                    break

        if not cities:
            cities = CATEGORY_CITIES["默认"][:3]
            reasoning = "使用默认高流量城市"

        return LocationStrategy(
            primary_city=cities[0],
            alternative_cities=cities[1:],
            reasoning=reasoning,
        )

    def _create_image_strategy(self, category: str, is_standard: bool) -> ImageStrategy:
        """Create first image strategy."""
        if is_standard:
            return ImageStrategy(
                image_type="plain",
                recommendations=[
                    "使用高清实拍图，白色/纯色背景",
                    "产品占画面80%以上",
                    "展示产品正面完整图",
                ],
                text_overlay_suggestions=["正品保证", "全新未拆封"],
            )
        else:
            # Non-standard: scene or pile image
            scene_categories = ["餐饮", "厨具", "办公", "家具", "养殖"]
            if any(c in category for c in scene_categories):
                return ImageStrategy(
                    image_type="scene",
                    recommendations=[
                        "使用场景图：产品在实际使用环境",
                        "展示产品尺寸比例（可放参照物）",
                        "正方形高清图，光线充足",
                    ],
                    text_overlay_suggestions=[
                        f"{category}专用",
                        "厂家直销",
                        "支持定制",
                    ],
                )
            else:
                return ImageStrategy(
                    image_type="pile",
                    recommendations=[
                        "产品堆积图：多件产品整齐摆放",
                        "展示库存充足、厂家实力",
                        "加文字标签突出卖点",
                    ],
                    text_overlay_suggestions=[
                        "厂家批发",
                        "量大从优",
                        "现货速发",
                    ],
                )

    def _get_description_focus(self, is_standard: bool, category: str) -> str:
        """Determine description focus based on product type."""
        if is_standard:
            return "price"
        non_standard_focus = {
            "餐饮": "材质、尺寸、功能、售后",
            "厨具": "材质、尺寸、功率、售后",
            "机械": "参数、功能、保修、物流",
            "家具": "材质、尺寸、颜色、安装",
            "养殖": "规格、材质、耐用性、售后",
            "办公": "尺寸、材质、功能、安装",
        }
        for key, focus in non_standard_focus.items():
            if key in category:
                return focus
        return "材质、尺寸、功能、售后"

    def _generate_tags(self, title: str, category: str) -> List[str]:
        """Generate tag suggestions for listing."""
        base_tags = [category]

        # Extract potential tags from title
        common_tags = [
            "全新", "包邮", "厂家直销", "商用", "家用", "工业级",
            "不锈钢", "实木", "定制", "现货", "一件代发",
        ]
        for tag in common_tags:
            if tag in title:
                base_tags.append(tag)

        return base_tags[:8]

    def generate_bait_description(
        self,
        strategy_type: str,
        actual_price: float,
        bait_price: float,
        product_specs: List[Dict[str, Any]],
    ) -> str:
        """Generate description text incorporating bait pricing strategy."""
        if strategy_type == "similar_product_combo":
            return (
                f"【引流款】{bait_price}元起为低配/小规格款，"
                f"实际产品价格为{actual_price}元。\n"
                f"不同规格价格不同，请看下方规格表选择。\n"
                f"所有产品均支持定制，详情咨询客服。"
            )
        elif strategy_type == "fictional_size":
            return (
                f"【标价说明】标价{bait_price}元为小尺寸款（目前缺货），\n"
                f"实际在售规格价格为{actual_price}元。\n"
                f"具体规格价格请看详情或咨询客服。"
            )
        elif strategy_type == "title_contrast":
            return (
                f"【规格价格】单门/小规格{bait_price}元，"
                f"双门/大规格{actual_price}元。\n"
                f"不同尺寸不同价格，请按需选择。"
            )
        else:
            return f"价格{actual_price}元，正品保证，质量可靠。"

    def generate_competitive_analysis(self, title: str, category: str) -> Dict[str, Any]:
        """Generate competitive analysis notes for listing optimization."""
        return {
            "price_positioning": "低于淘宝京东，高于拼多多" if not self._detect_standard_product(title, category) else "对标拼多多",
            "differentiation_angle": (
                "强调材质/定制/服务" if not self._detect_standard_product(title, category)
                else "强调正品/售后/速度"
            ),
            "service_emphasis": [
                "48小时内发货",
                "支持退换",
                "质量问题包赔",
            ],
            "trust_signals": [
                "实拍图",
                "细节图",
                "工厂/仓库图",
            ],
        }


# ====== Integration helpers ======

def looks_like_listing_strategy_request(text: str) -> bool:
    """Check if text is a listing strategy request."""
    import re
    patterns = [
        r"上架策略",
        r"怎么上架",
        r"如何发布",
        r"引流",
        r"标价",
        r"发布地址",
        r"首图",
        r"标品|非标",
    ]
    return any(re.search(p, text) for p in patterns)
