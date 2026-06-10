"""Discovery Agent - Market research and product discovery.

Delegates to workflow for real Xianyu search via Playwright,
then uses MiMo V2.5 Pro for AI analysis of results.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base import BaseAgent

logger = logging.getLogger(__name__)


class DiscoveryAgent(BaseAgent):
    name = "discovery"
    description = "Market research and product discovery with strategy-driven modes"

    # Strategy discovery modes from docs/ops/xianyu/product-selection-playbook.md
    STRATEGY_MODES = {
        "raw_material": "原材料选品法：从原材料出发发现冷门产品",
        "high_ticket": "高客单价扫描：500元以上的产品",
        "batch_demand": "批量需求扫描：围栏、地垫、餐饮用品、工厂用品等",
        "regional": "区域扫描：匹配发货地与需求城市",
        "same_product_multi_link": "同商品多链接测试：分析竞品如何用不同标题/图片/价格/城市做差异化",
    }

    RAW_MATERIALS = ["不锈钢", "木头", "塑料", "玻璃", "金属", "铁", "铝合金", "石材", "亚克力"]

    def search_products(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Search Xianyu for products using real Playwright scraping.

        Params: keyword, max_pages, min_price, max_price
        Returns real product data + AI analysis.
        """
        import asyncio
        keyword = params.get("keyword", "")
        max_pages = params.get("max_pages", 3)
        min_price = params.get("min_price")
        max_price = params.get("max_price")
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run,
                        self.workflow.run_discovery(keyword, max_pages, min_price, max_price)
                    )
                    result = future.result(timeout=120)
            else:
                result = loop.run_until_complete(
                    self.workflow.run_discovery(keyword, max_pages, min_price, max_price)
                )
            return {"ok": True, "results": result}
        except Exception as e:
            logger.error("Product search failed: %s", e)
            return {"ok": False, "error": str(e)}

    def strategy_discovery(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run strategy-driven discovery based on the product selection playbook.

        Params:
          mode: raw_material | high_ticket | batch_demand | regional
          material: (for raw_material mode) specific material to search
          city: (for regional mode) target city
          min_price: (for high_ticket mode) minimum price threshold, default 500
        """
        mode = params.get("mode", "raw_material")
        if mode not in self.STRATEGY_MODES:
            return {"ok": False, "error": f"Unknown mode: {mode}. Available: {list(self.STRATEGY_MODES.keys())}"}

        keywords = self._get_strategy_keywords(mode, params)
        if not keywords:
            return {"ok": False, "error": "No keywords generated for this mode"}

        results = []
        for keyword in keywords[:3]:  # Limit to 3 keywords per run
            search_params = {
                "keyword": keyword,
                "max_pages": params.get("max_pages", 2),
            }
            if mode == "high_ticket":
                search_params["min_price"] = params.get("min_price", 500)
            result = self.search_products(search_params)
            if result.get("ok"):
                for product in result.get("results", []):
                    product["strategy_mode"] = mode
                    product["strategy_keyword"] = keyword
                results.extend(result.get("results", []))

        return {
            "ok": True,
            "mode": mode,
            "keywords_searched": keywords[:3],
            "total_found": len(results),
            "results": results,
        }

    def _get_strategy_keywords(self, mode: str, params: Dict[str, Any]) -> List[str]:
        """Generate keywords for a strategy discovery mode."""
        if mode == "raw_material":
            material = params.get("material", "")
            if material:
                return [material]
            return self.RAW_MATERIALS[:3]

        if mode == "high_ticket":
            category = params.get("category", "")
            base = [category] if category else ["商用厨具", "机械设备", "办公家具"]
            return base

        if mode == "batch_demand":
            return ["围栏", "养殖网", "地垫", "餐饮用品", "工厂用品"]

        if mode == "regional":
            city = params.get("city", "")
            keyword = params.get("keyword", "操作台")
            if city:
                return [f"{city} {keyword}"]
            return [keyword]

        if mode == "same_product_multi_link":
            keyword = params.get("keyword", "")
            if keyword:
                return [keyword]
            # Default: pick a product type likely to have multiple seller links
            return ["不锈钢操作台", "商用冷柜", "养殖围栏"]

        return []

    def list_tasks(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List all discovery search tasks."""
        tasks = self.workflow.list_discovery_tasks()
        return {"ok": True, "tasks": tasks}

    def add_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a recurring product discovery task."""
        result = self.workflow.add_discovery_task(params)
        return {"ok": True, "task": result}

    def remove_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove a discovery task."""
        task_id = params.get("task_id", "")
        ok = self.workflow.remove_discovery_task(task_id)
        return {"ok": ok}

    def get_results(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get results for a discovery task."""
        task_id = params.get("task_id", "")
        limit = params.get("limit", 50)
        results = self.workflow.get_discovery_results(task_id, limit)
        return {"ok": True, "results": results}

    def analyze_market(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze market using LLM (supplementary to real search)."""
        category = params.get("category", "")
        system = """你是闲鱼市场分析师。分析指定品类的市场趋势。
返回JSON: {"category":"","trend":"rising/stable/declining","avg_price":0,"demand_level":"high/medium/low","recommendations":[]}"""
        result = self.chat_json(system, f"分析品类: {category}")
        return {"ok": True, "analysis": result} if result else {"ok": False, "error": "Analysis failed"}

    def get_handlers(self):
        return {
            "search_products": self.search_products,
            "strategy_discovery": self.strategy_discovery,
            "list_tasks": self.list_tasks,
            "add_task": self.add_task,
            "remove_task": self.remove_task,
            "get_results": self.get_results,
            "analyze_market": self.analyze_market,
        }
