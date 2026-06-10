"""Analytics Agent - Sales analysis, profit calculation, trends.

Queries real data from SQLite database, uses MiMo V2.5 Pro for analysis.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base import BaseAgent

logger = logging.getLogger(__name__)


class AnalyticsAgent(BaseAgent):
    name = "analytics"
    description = "Sales analysis, profit calculation, trends"

    def get_dashboard_data(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get real dashboard data from database."""
        db = self.workflow.db
        cookies = db.get_all_cookies()
        all_orders = []
        all_items = []
        for cid in cookies:
            all_orders.extend(db.get_orders_by_cookie(cid))
            all_items.extend(db.get_items_by_cookie(cid))

        total_revenue = sum(o.get("amount", 0) for o in all_orders)
        status_counts = {}
        for o in all_orders:
            s = o.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "ok": True,
            "accounts": len(cookies),
            "total_orders": len(all_orders),
            "total_items": len(all_items),
            "total_revenue": round(total_revenue, 2),
            "order_status_breakdown": status_counts,
        }

    def analyze_sales(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Analyze sales data from database and provide LLM insights."""
        dashboard = self.get_dashboard_data()
        if not dashboard.get("ok"):
            return dashboard

        system = """你是闲鱼销售数据分析师。根据数据提供洞察和建议。
返回JSON: {"insights":["洞察1"],"recommendations":["建议1"],"trend":"rising/stable/declining"}"""
        result = self.chat_json(system, f"销售数据: {dashboard}")
        return {"ok": True, "dashboard": dashboard, "analysis": result} if result else {"ok": True, "dashboard": dashboard}

    def calculate_profit(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate profit (real arithmetic + LLM advice)."""
        cost = params.get("cost", 0)
        price = params.get("price", 0)
        platform_fee_rate = params.get("platform_fee_rate", 0.08)
        shipping = params.get("shipping_cost", 0)

        platform_fee = price * platform_fee_rate
        profit = price - cost - platform_fee - shipping
        profit_rate = profit / cost if cost > 0 else 0

        system = "你是利润计算分析师。根据数据给出定价建议。用中文简洁回复。"
        advice = self.chat(
            system,
            f"成本: {cost}, 售价: {price}, 平台费: {platform_fee:.2f}, 运费: {shipping}, "
            f"利润: {profit:.2f}, 利润率: {profit_rate:.1%}"
        )

        return {
            "ok": True,
            "profit": round(profit, 2),
            "profit_rate": round(profit_rate, 4),
            "platform_fee": round(platform_fee, 2),
            "advice": advice,
        }

    def generate_report(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Generate a business report from real data."""
        dashboard = self.get_dashboard_data()
        system = "你是闲鱼店铺运营分析师。根据数据摘要生成简洁的运营报告。用中文，分点列出关键指标和建议。"
        report = self.chat(system, f"店铺数据: {dashboard}")
        return {"ok": True, "report": report, "data": dashboard}

    def get_handlers(self):
        return {
            "get_dashboard_data": self.get_dashboard_data,
            "analyze_sales": self.analyze_sales,
            "calculate_profit": self.calculate_profit,
            "generate_report": self.generate_report,
        }
