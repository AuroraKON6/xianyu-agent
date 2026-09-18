"""Sourcing Agent - Physical product sourcing, supplier management, material discovery.

Delegates to:
- PhysicalSourcingManager for cost/profit calculation
- cn-ecommerce-search (via existing workflow) for multi-platform sourcing
- ListingStrategyEngine for strategy generation
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..physical_sourcing import looks_like_sourcing_request, sourcing_payload_from_text

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Xianyu physical product sourcing agent.
Your role is to help discover profitable products to sell on Xianyu through dropshipping.

Responsibilities:
1. Evaluate profit margins for physical products (cost → resale price)
2. Discover products by raw material (原材料选品法)
3. Manage supplier relationships
4. Provide stealth shipping templates (pinduoduo logo/短信 hiding)
5. Cross-check market demand on Xianyu vs supply price on PDD/1688

Key methodology from the article:
- 原材料选品法: Search by material (不锈钢, 塑料, 木头) to find blue ocean products
- 高客单价优先: Focus on products with price > 500 CNY
- 非标品: Non-standard products have higher margins (price not transparent)
- 批量单: Products sold in bulk (养殖网, 人造草坪, 防护围栏) are very profitable

Always check: cost_price + shipping + platform_fee < resale_price * 0.7 (30% margin minimum)"""


class SourcingAgent(BaseAgent):
    name = "sourcing"
    description = "实物货源评估、原材料选品、供应商管理、利润核算"

    def evaluate_sourcing(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a physical product sourcing opportunity.

        输入: cost_price(成本), resale_price(售价), shipping_cost(运费), category(类别)
        输出: 利润分析、是否值得做、推荐城市、上架策略、无痕话术
        """
        result = self.workflow.evaluate_sourcing(params)
        return {"ok": True, "type": "sourcing_evaluation", "result": result}

    def discover_by_material(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """原材料选品法 - 按原材料发现蓝海产品。

        输入: material(原材料名称), 如"不锈钢"、"塑料"、"实木"
        输出: 15+ 相关产品搜索词，指向拼多多/1688搜索入口
        """
        material = params.get("material", "")
        result = self.workflow.search_sourcing(keyword=material, material=material)
        return {"ok": True, "type": "material_discovery", "result": result}

    def add_supplier(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """添加供应商信息到管理系统。

        输入: name(名称), platform(pinduoduo/1688), contact(联系方式),
              product_categories(产品类别), is_factory_direct(是否厂家)
        """
        result = self.workflow.add_supplier(params)
        return {"ok": True, "type": "supplier_added", "result": result}

    def list_suppliers(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """列出已保存的供应商。

        可选参数: platform(按平台筛选)
        """
        platform = (params or {}).get("platform")
        suppliers = self.workflow.list_suppliers(platform)
        return {"ok": True, "type": "supplier_list", "count": len(suppliers), "result": suppliers}

    def get_stealth_templates(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """获取无痕发货话术模板。

        WP12 起buyer_pdd_logo/buyer_pdd_card/buyer_pdd_sms/factory_negotiation
        四套固定话术已移除：它们捏造多平台店铺关系并含好评返现内容，
        只能由已验证经营事实生成（见 generate_buyer_response 的
        verified_facts）。当前仅保留：
        - order_note: 下单备注（给商家的无痕发货请求）

        可选参数: scenario(指定场景), 不指定则返回所有
        """
        scenario = (params or {}).get("scenario")
        templates = self.workflow.get_stealth_templates(scenario)
        return {"ok": True, "type": "stealth_templates", "result": templates}

    def contribute_to_discussion(self, topic: str, transcript: list, context: Optional[Dict[str, Any]] = None) -> str:
        """贡献货源角度的讨论意见。"""
        ctx = context or {}
        title = ctx.get("title", "未知商品")
        cost = ctx.get("cost_price", ctx.get("cost", 0))
        price = ctx.get("resale_price", ctx.get("price", 0))

        parts = [f"sourcing: 针对「{title}」货源分析:"]
        if cost and price:
            margin = (price - cost) / price if price > 0 else 0
            if margin >= 0.3:
                parts.append(f"利润空间充足(毛利率 {margin*100:.0f}%), 可推进。")
            else:
                parts.append(f"利润偏低(毛利率 {margin*100:.0f}%), 建议寻找更有优势的货源。")
        else:
            parts.append("缺少成本和售价信息, 建议补充后再评估。")
        parts.append("优先对接厂家直供, 争取出厂价, 用'团队卖货+量大'作为谈判筹码。")
        return " ".join(parts)

    def get_handlers(self) -> Dict[str, callable]:
        return {
            "evaluate_sourcing": self.evaluate_sourcing,
            "discover_by_material": self.discover_by_material,
            "add_supplier": self.add_supplier,
            "list_suppliers": self.list_suppliers,
            "get_stealth_templates": self.get_stealth_templates,
        }
