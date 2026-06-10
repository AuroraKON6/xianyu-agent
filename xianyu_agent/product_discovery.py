"""Playwright-based Xianyu product discovery and monitoring.

Inspired by ai-goofish-monitor (spider_v2.py) and xianyu-hunter (search/filter).
Integrates into the CommerceWorkflow as a new discovery source.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredProduct:
    """A product found on Xianyu."""
    product_id: str
    title: str
    price: str
    location: str
    seller_name: str
    seller_id: str
    image_url: str
    detail_url: str
    tags: List[str] = field(default_factory=list)
    want_count: int = 0
    browse_count: int = 0
    description: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)
    ai_analysis: Optional[Dict[str, Any]] = None
    discovered_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveryTask:
    """A product discovery/monitoring task."""
    id: str
    name: str
    keyword: str
    enabled: bool = True
    max_pages: int = 3
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    personal_only: bool = False
    ai_prompt: str = ""
    notify_feishu: bool = True
    notify_email: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ProductDiscovery:
    """Xianyu product search and monitoring using Playwright.

    Uses the same API interception technique as ai-goofish-monitor:
    intercepts h5api.m.goofish.com responses to get structured product data.
    """

    SEARCH_API_PATTERN = "mtop.taobao.idlemtopsearch.pc.search"
    DETAIL_API_PATTERN = "mtop.taobao.idle.pc.detail"

    def __init__(self, settings: Any, storage: Any, llm: Any, notifier: Any, guard: Any) -> None:
        self.settings = settings
        self.storage = storage
        self.llm = llm
        self.notifier = notifier
        self.guard = guard
        self._tasks: Dict[str, DiscoveryTask] = {}
        self._results: Dict[str, List[DiscoveredProduct]] = {}
        self._load_tasks()

    def _load_tasks(self) -> None:
        tasks_file = self.settings.data_dir / "discovery" / "tasks.json"
        if tasks_file.exists():
            try:
                data = json.loads(tasks_file.read_text(encoding="utf-8"))
                for item in data:
                    task = DiscoveryTask(**item)
                    self._tasks[task.id] = task
            except Exception as exc:
                logger.warning("Failed to load discovery tasks: %s", exc)

    def _save_tasks(self) -> None:
        tasks_dir = self.settings.data_dir / "discovery"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        tasks_file = tasks_dir / "tasks.json"
        tasks_file.write_text(
            json.dumps([t.to_dict() for t in self._tasks.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_task(self, task: DiscoveryTask) -> Dict[str, Any]:
        self._tasks[task.id] = task
        self._save_tasks()
        return task.to_dict()

    def remove_task(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save_tasks()
            return True
        return False

    def list_tasks(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._tasks.values()]

    def get_results(self, task_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        results = self._results.get(task_id, [])
        return [p.to_dict() for p in results[-limit:]]

    async def search_products(
        self,
        keyword: str,
        max_pages: int = 3,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        personal_only: bool = False,
    ) -> List[DiscoveredProduct]:
        """Search Xianyu for products matching keyword using Playwright.

        Intercepts the internal API response to get structured data,
        same technique as ai-goofish-monitor's spider_v2.py.
        """
        products: List[DiscoveredProduct] = []
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
            return products

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            captured_results: List[Dict[str, Any]] = []

            async def handle_response(response: Any) -> None:
                try:
                    url = response.url
                    if self.SEARCH_API_PATTERN in url or self.DETAIL_API_PATTERN in url:
                        body = await response.json()
                        if self.SEARCH_API_PATTERN in url:
                            items = (
                                body.get("data", {})
                                .get("resultList", [])
                            )
                            for item in items:
                                data = item.get("data", {})
                                if data:
                                    captured_results.append(data)
                except Exception:
                    pass

            page.on("response", handle_response)

            for page_num in range(1, max_pages + 1):
                url = f"https://www.goofish.com/search?q={quote_plus(keyword)}"
                if min_price is not None:
                    url += f"&price_min={int(min_price * 100)}"
                if max_price is not None:
                    url += f"&price_max={int(max_price * 100)}"
                if personal_only:
                    url += "&personalOnly=true"
                if page_num > 1:
                    url += f"&page={page_num}"

                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(3)
                except Exception as exc:
                    logger.warning("Page %d navigation failed: %s", page_num, exc)
                    break

                # Rate limiting
                await asyncio.sleep(2 + (time.time() % 3))

            await browser.close()

        # Parse captured results
        seen_ids = set()
        for data in captured_results:
            product_id = str(data.get("id") or data.get("itemId") or "")
            if not product_id or product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            title = str(data.get("title") or data.get("name") or "")
            price = str(data.get("price") or data.get("soldPrice") or "")
            location = str(data.get("area") or data.get("city") or "")
            seller_name = str(data.get("sellerNick") or data.get("userName") or "")
            seller_id = str(data.get("sellerId") or data.get("userId") or "")
            image_url = str(data.get("picUrl") or data.get("imageUrl") or "")
            detail_url = f"https://www.goofish.com/item?id={product_id}"

            if not title:
                continue

            products.append(DiscoveredProduct(
                product_id=product_id,
                title=_strip_html(title),
                price=price,
                location=location,
                seller_name=seller_name,
                seller_id=seller_id,
                image_url=image_url,
                detail_url=detail_url,
                tags=_extract_tags(data),
                raw_data=data,
                discovered_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            ))

        return products

    async def analyze_product(self, product: DiscoveredProduct, custom_prompt: str = "") -> DiscoveredProduct:
        """Run AI analysis on a discovered product."""
        if not self.settings.deepseek_api_key:
            return product

        prompt = custom_prompt or self._default_analysis_prompt()
        user_msg = f"""Analyze this Xianyu product:

Title: {product.title}
Price: {product.price}
Location: {product.location}
Seller: {product.seller_name}
Tags: {', '.join(product.tags)}
Want count: {product.want_count}
Browse count: {product.browse_count}

{prompt}"""

        try:
            result = self.llm.chat_json(
                "You are a Xianyu product analysis expert. Return valid JSON with fields: is_recommended (bool), reason (str), risk_level (str: low/medium/high), suggested_action (str).",
                user_msg,
            )
            product.ai_analysis = result
        except Exception as exc:
            logger.warning("AI analysis failed for %s: %s", product.product_id, exc)
            product.ai_analysis = {"is_recommended": False, "reason": f"Analysis failed: {exc}"}

        # Run strategy scoring
        product.ai_analysis = product.ai_analysis or {}
        product.ai_analysis["scoring"] = self._score_discovered_product(product)

        return product

    def _score_discovered_product(self, product: DiscoveredProduct) -> Dict[str, Any]:
        """Score a discovered product using the strategy scoring model."""
        try:
            from xianyu_agent.resale import ProductScorer
            scorer = ProductScorer()
            candidate_like = {
                "title": product.title,
                "category": "",
                "target_sale_price": self._parse_price(product.price),
                "supplier_price": 0,
                "supplier_verified": False,
                "economics": {"gross_margin": 0},
                "risk_level": "low",
                "risk_flags": [],
            }
            return scorer.score(candidate_like)
        except Exception as exc:
            logger.warning("Scoring failed for %s: %s", product.product_id, exc)
            return {"error": str(exc)}

    @staticmethod
    def _parse_price(price_str: str) -> float:
        """Extract numeric price from string like '¥128.00' or '128'."""
        try:
            cleaned = re.sub(r"[^\d.]", "", price_str)
            return float(cleaned) if cleaned else 0.0
        except (ValueError, TypeError):
            return 0.0

    async def run_discovery_task(self, task_id: str) -> Dict[str, Any]:
        """Run a single discovery task: search + analyze + notify."""
        task = self._tasks.get(task_id)
        if not task or not task.enabled:
            return {"ok": False, "message": f"Task {task_id} not found or disabled"}

        guard_check = self.guard.before_action("product_discovery", {"keyword": task.keyword, "task_id": task_id})
        if not guard_check.get("allow"):
            return {"ok": False, "message": f"Guard blocked: {guard_check.get('reason')}", "guard": guard_check}

        products = await self.search_products(
            keyword=task.keyword,
            max_pages=task.max_pages,
            min_price=task.min_price,
            max_price=task.max_price,
            personal_only=task.personal_only,
        )

        analyzed = []
        for product in products:
            product = await self.analyze_product(product, custom_prompt=task.ai_prompt)
            analyzed.append(product)

            if product.ai_analysis and product.ai_analysis.get("is_recommended"):
                self._notify_recommendation(task, product)

        self._results[task_id] = analyzed
        self._save_results(task_id, analyzed)
        self.storage.append_event("discovery.completed", {
            "task_id": task_id,
            "keyword": task.keyword,
            "found": len(products),
            "recommended": sum(1 for p in analyzed if p.ai_analysis and p.ai_analysis.get("is_recommended")),
        })

        return {
            "ok": True,
            "task_id": task_id,
            "keyword": task.keyword,
            "found": len(products),
            "recommended": sum(1 for p in analyzed if p.ai_analysis and p.ai_analysis.get("is_recommended")),
            "products": [p.to_dict() for p in analyzed],
        }

    async def run_all_enabled_tasks(self) -> Dict[str, Any]:
        """Run all enabled discovery tasks."""
        results = []
        for task_id, task in self._tasks.items():
            if task.enabled:
                result = await self.run_discovery_task(task_id)
                results.append(result)
                await asyncio.sleep(5)  # Delay between tasks
        return {"tasks_run": len(results), "results": results}

    def _notify_recommendation(self, task: DiscoveryTask, product: DiscoveredProduct) -> None:
        """Send notification for a recommended product."""
        msg = (
            f"[Discovery] Recommended product found!\n"
            f"Keyword: {task.keyword}\n"
            f"Title: {product.title}\n"
            f"Price: {product.price}\n"
            f"Location: {product.location}\n"
            f"Seller: {product.seller_name}\n"
            f"Link: {product.detail_url}\n"
            f"Reason: {product.ai_analysis.get('reason', 'N/A')}"
        )
        if task.notify_feishu:
            self.notifier.send_text(msg)
        if task.notify_email:
            # Email notification would go here
            pass

    def _save_results(self, task_id: str, products: List[DiscoveredProduct]) -> None:
        results_dir = self.settings.data_dir / "discovery" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        results_file = results_dir / f"{task_id}.json"
        results_file.write_text(
            json.dumps([p.to_dict() for p in products], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _default_analysis_prompt(self) -> str:
        return """Evaluate this product for resale potential on Xianyu:
1. Is the price reasonable for the category?
2. Is the seller trustworthy (based on available info)?
3. Are there any red flags (too cheap, suspicious tags, etc.)?
4. What is the risk level (low/medium/high)?
5. Should this be recommended for purchase or further investigation?"""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _extract_tags(data: Dict[str, Any]) -> List[str]:
    tags = []
    for key in ("tags", "tagList", "itemTags"):
        val = data.get(key)
        if isinstance(val, list):
            tags.extend(str(t) for t in val if t)
        elif isinstance(val, str) and val:
            tags.append(val)
    return tags[:10]
