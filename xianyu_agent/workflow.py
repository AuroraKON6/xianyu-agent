from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .adapters import ListingAdapter, build_adapter
from .arbitrage import (
    VirtualProductArbitrageAgent,
    looks_like_opportunity_request,
    parse_decision_text,
    payload_from_text,
)
from .auto_reply import AutoReplyEngine
from .browser_control import EdgeBrowserController
from .browser_pool import BrowserPool, get_browser_pool
from .after_sales import AfterSalesHandler, looks_like_aftersales_request
from .config import Settings
from .feishu import FeishuNotifier
from .github_discovery import GitHubOpportunityScanner
from .image_search import ImageSearchClient
from .listing_strategy import ListingStrategyEngine, looks_like_listing_strategy_request
from .llm import DeepSeekClient, heuristic_listing
from .memory import AgentMemory, looks_like_memory_request, memory_text_from_request
from .models import ImageCandidate, ListingDraft, Order
from .order_status_handler import OrderStatusHandler, order_status_handler
from .physical_sourcing import PhysicalSourcingManager, looks_like_sourcing_request, sourcing_payload_from_text
from .platform_guard import PlatformGuard
from .product_discovery import ProductDiscovery, DiscoveryTask
from .secure_confirm import SecureConfirm
from .storage import JsonStorage
from .url_crawler import UrlDownloadCrawler, crawl_payload_from_text, looks_like_url_crawl_request
from .ruflo_integration import ruflo


SYSTEM_PROMPT = """You are a Xianyu listing operations agent.
Return only valid JSON. Optimize the listing for clarity, truthful conversion, and low customer-service burden.
Do not invent certifications, brand authorization, scarcity, shipping promises, or product claims.
Fields: title, category, price, stock, condition, description, tags, image_queries, delivery_type, delivery_payload, faqs.
delivery_type must be one of digital, physical, manual. Use digital only when the user provided a deliverable link/code/text."""

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "xianyu_agent_system.md"


class CommerceWorkflow:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_dirs()
        self.settings = settings
        self.storage = JsonStorage(settings.data_dir)
        self.llm = DeepSeekClient(settings)
        self.images = ImageSearchClient(settings)
        self.notifier = FeishuNotifier(settings)
        self.memory = AgentMemory(self.storage)
        self.platform_guard = PlatformGuard(settings, self.storage, self.notifier)
        self.browser = EdgeBrowserController(settings, self.storage, self.platform_guard)
        self.github_scanner = GitHubOpportunityScanner(settings, self.storage, self.llm)
        self.arbitrage_agent = VirtualProductArbitrageAgent(settings, self.storage, self.llm, self.notifier)
        self.url_crawler = UrlDownloadCrawler(settings, self.storage)
        self.product_discovery = ProductDiscovery(settings, self.storage, self.llm, self.notifier, self.platform_guard)
        self.auto_reply = AutoReplyEngine(settings, self.llm, self.storage)
        # Physical product sourcing (new)
        self.sourcing = PhysicalSourcingManager(settings, self.storage, self.llm)
        # Listing strategy engine (new)
        self.listing_strategy = ListingStrategyEngine(settings)
        # After-sales handler (new)
        self.after_sales = AfterSalesHandler(settings, self.storage, self.llm)
        # Database and multi-account management (from xianyu-super-butler merge)
        from .db_manager import db_manager
        self.db = db_manager
        self.order_handler = order_status_handler
        self.browser_pool = get_browser_pool()
        # Adapter (needs browser_pool for browser mode)
        self.adapter: ListingAdapter = build_adapter(settings, self.storage, self.browser_pool)
        # Register with Ruflo integration
        ruflo.set_workflow(self)

    def create_listing(
        self,
        raw_text: str,
        source: str = "manual",
        product_images: Optional[Iterable[str]] = None,
        publish: bool = False,
    ) -> Dict[str, Any]:
        draft = self._draft_from_text(raw_text=raw_text, source=source, product_images=list(product_images or []))
        self.storage.save_draft(draft)
        self.storage.append_event(
            "listing.created",
            {
                "draft": draft.to_dict(),
                "publish_result": None,
                "direct_publish_requested": bool(publish),
                "execution_path": "work_order_approval_queue",
            },
        )
        return {
            "draft": draft.to_dict(),
            "publish_result": None,
            "approval_required": bool(publish),
            "executed": False,
            "direct_publish_disabled": bool(publish),
        }

    def polish_all(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "count": 0,
            "results": [],
            "approval_required": True,
            "executed": False,
            "message": "Direct polish is retired; create an approved execution action instead.",
        }

    def fulfill_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        order = Order.from_payload(payload)
        if not order.delivery_payload:
            order.delivery_payload = self._delivery_payload_for_listing(order.listing_id)
        if not order.delivery_payload:
            order.delivery_payload = self._delivery_payload_for_listing(order.listing_id)
        order.raw["delivery_payload"] = order.delivery_payload
        result = self.adapter.fulfill(order)
        self.storage.append_event("order.fulfilled", {"order": order.raw, "result": result})
        return result

    def refund_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        order_id = str(payload.get("order_id") or payload.get("id") or "")
        if not order_id:
            return {"ok": False, "message": "order_id is required"}

        refund_fn = getattr(self.adapter, "refund_order", None)
        if refund_fn is None:
            result = {"ok": False, "message": "Adapter does not support refund_order"}
        else:
            result = refund_fn(payload)
        self.storage.append_event(
            "order.refund_requested",
            {
                "order_id": order_id,
                "amount_cny": payload.get("amount_cny"),
                "reason_summary": str(payload.get("reason") or "")[:120],
                "result": result,
            },
        )
        return result

    def platform_guard_status(self) -> Dict[str, Any]:
        return self.platform_guard.status()

    def platform_guard_check(self, text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.platform_guard.observe_text(text, context=context)

    def platform_guard_resume(self, note: str = "") -> Dict[str, Any]:
        return self.platform_guard.resume(note=note)

    def browser_status(self) -> Dict[str, Any]:
        return self.browser.status()

    def browser_open_url(self, url: str, wait_seconds: float = 3.0) -> Dict[str, Any]:
        return self.browser.open_url(url, wait_seconds=wait_seconds)

    def browser_open_account_login(self) -> Dict[str, Any]:
        return self.browser.open_account_login()

    def discover_github_opportunities(
        self,
        queries: Optional[Iterable[str]] = None,
        min_stars: Optional[int] = None,
        limit_per_query: Optional[int] = None,
        use_deepseek: bool = True,
        include_readme: bool = False,
    ) -> Dict[str, Any]:
        return self.github_scanner.discover(
            queries=queries,
            min_stars=min_stars,
            limit_per_query=limit_per_query,
            use_deepseek=use_deepseek,
            include_readme=include_readme,
        )

    def evaluate_virtual_product_opportunity(
        self,
        payload: Dict[str, Any],
        use_deepseek: bool = True,
        notify: Optional[bool] = None,
    ) -> Dict[str, Any]:
        return self.arbitrage_agent.evaluate(payload, use_deepseek=use_deepseek, notify=notify)

    def decide_virtual_product_opportunity(self, opportunity_id: str, decision: str, note: str = "") -> Dict[str, Any]:
        return self.arbitrage_agent.decide(opportunity_id, decision, note=note)

    def record_virtual_product_purchase(self, opportunity_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.arbitrage_agent.record_purchase(opportunity_id, payload)

    def save_virtual_product_assets(self, opportunity_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.arbitrage_agent.save_assets(opportunity_id, payload)

    def create_listing_from_opportunity(self, opportunity_id: str, publish: bool = False) -> Dict[str, Any]:
        brief = self.arbitrage_agent.listing_brief(opportunity_id)
        return self.create_listing(
            brief["text"],
            source=f"opportunity:{opportunity_id}",
            product_images=brief["images"],
            publish=publish,
        )

    def crawl_url_opportunity(
        self,
        payload: Dict[str, Any],
        use_deepseek: bool = True,
        notify: Optional[bool] = None,
    ) -> Dict[str, Any]:
        crawl = self.url_crawler.crawl(payload)
        opportunity_payload = dict(crawl["opportunity_payload"])
        opportunity_payload["market_samples"] = payload.get("market_samples") or opportunity_payload.get("market_samples") or []
        opportunity = self.evaluate_virtual_product_opportunity(
            opportunity_payload,
            use_deepseek=use_deepseek,
            notify=notify,
        )
        opportunity["crawl_id"] = crawl["id"]
        opportunity["download_candidates"] = crawl["download_candidates"]
        opportunity["downloads"] = crawl["downloads"]
        self.storage.save_opportunity(opportunity)
        return {"crawl": crawl, "opportunity": opportunity}

    # --- Product Discovery ---

    def add_discovery_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        import uuid
        task = DiscoveryTask(
            id=task_data.get("id") or uuid.uuid4().hex[:12],
            name=task_data.get("name") or task_data.get("keyword", "unnamed"),
            keyword=task_data["keyword"],
            enabled=task_data.get("enabled", True),
            max_pages=task_data.get("max_pages", self.settings.discovery_max_pages),
            min_price=task_data.get("min_price"),
            max_price=task_data.get("max_price"),
            personal_only=task_data.get("personal_only", False),
            ai_prompt=task_data.get("ai_prompt", ""),
            notify_feishu=task_data.get("notify_feishu", True),
            notify_email=task_data.get("notify_email", ""),
        )
        return self.product_discovery.add_task(task)

    def remove_discovery_task(self, task_id: str) -> bool:
        return self.product_discovery.remove_task(task_id)

    def list_discovery_tasks(self) -> list:
        return self.product_discovery.list_tasks()

    def get_discovery_results(self, task_id: str, limit: int = 50) -> list:
        return self.product_discovery.get_results(task_id, limit=limit)

    async def run_discovery(self, keyword: str, max_pages: int = 3, min_price: Optional[float] = None, max_price: Optional[float] = None) -> Dict[str, Any]:
        products = await self.product_discovery.search_products(
            keyword=keyword, max_pages=max_pages, min_price=min_price, max_price=max_price,
        )
        analyzed = []
        for product in products:
            product = await self.product_discovery.analyze_product(product)
            analyzed.append(product)
        return {
            "keyword": keyword,
            "found": len(products),
            "recommended": sum(1 for p in analyzed if p.ai_analysis and p.ai_analysis.get("is_recommended")),
            "products": [p.to_dict() for p in analyzed],
        }

    async def run_all_discovery_tasks(self) -> Dict[str, Any]:
        return await self.product_discovery.run_all_enabled_tasks()

    # --- Auto Reply ---

    def generate_auto_reply(
        self,
        buyer_id: str,
        message: str,
        product_id: str = "",
        product_title: str = "",
        product_description: str = "",
        product_price: str = "",
    ) -> Optional[str]:
        return self.auto_reply.generate_reply(
            buyer_id=buyer_id,
            message=message,
            product_id=product_id,
            product_title=product_title,
            product_description=product_description,
            product_price=product_price,
        )

    def add_reply_rule(self, rule_data: Dict[str, Any]) -> Dict[str, Any]:
        import uuid
        from .auto_reply import ReplyRule
        rule = ReplyRule(
            id=rule_data.get("id") or uuid.uuid4().hex[:8],
            keyword=rule_data["keyword"],
            reply_text=rule_data["reply_text"],
            product_id=rule_data.get("product_id", ""),
            priority=rule_data.get("priority", 0),
            enabled=rule_data.get("enabled", True),
        )
        return self.auto_reply.add_rule(rule)

    def remove_reply_rule(self, rule_id: str) -> bool:
        return self.auto_reply.remove_rule(rule_id)

    def list_reply_rules(self) -> list:
        return self.auto_reply.list_rules()

    def handle_feishu_text(self, text: str) -> Dict[str, Any]:
        if looks_like_memory_request(text):
            memory = self.memory.remember(memory_text_from_request(text), source="feishu")
            return {"type": "memory", "result": memory}
        decision = parse_decision_text(text)
        if decision:
            return {
                "type": "opportunity_decision",
                "result": self.decide_virtual_product_opportunity(decision["id"], decision["decision"], decision["note"]),
            }
        if looks_like_opportunity_request(text):
            return {
                "type": "opportunity_evaluation",
                "result": self.evaluate_virtual_product_opportunity(payload_from_text(text), use_deepseek=True, notify=True),
            }
        if looks_like_url_crawl_request(text):
            return {
                "type": "url_crawl_opportunity",
                "result": self.crawl_url_opportunity(crawl_payload_from_text(text), use_deepseek=True, notify=True),
            }
        # Sourcing commands (NEW)
        if looks_like_sourcing_request(text):
            payload = sourcing_payload_from_text(text)
            return {
                "type": "sourcing_evaluation",
                "result": self.evaluate_sourcing(payload),
            }
        # Listing strategy commands (NEW)
        if looks_like_listing_strategy_request(text):
            return {
                "type": "listing_strategy",
                "result": self.create_listing_strategy({"title": text}),
            }
        # After-sales commands (NEW)
        if looks_like_aftersales_request(text):
            return {
                "type": "after_sales",
                "result": self.create_aftersales_case({"buyer_message": text}),
            }
        # Discovery command via Feishu: "搜货: keyword" or "监控: keyword"
        if text.startswith(("搜货:", "搜货：", "监控:", "监控：")):
            sep = ":" if ":" in text else "："
            keyword = text.split(sep, 1)[1].strip()
            if keyword:
                return {
                    "type": "discovery",
                    "result": self.product_discovery.search_products(keyword=keyword),
                }
        return {"type": "listing", "result": self.create_listing(text, source="feishu", publish=False)}

    def handle_agent_message(self, text: str, publish: bool = False) -> Dict[str, Any]:
        clean = text.strip()
        if not clean:
            return {"type": "empty", "result": {"message": "text is required"}}
        if looks_like_memory_request(clean):
            memory = self.memory.remember(memory_text_from_request(clean), source="console")
            return {"type": "memory", "result": memory}
        if clean.lower().startswith(("github:", "github：")):
            query = clean.split(":", 1)[1].strip() if ":" in clean else clean.split("：", 1)[1].strip()
            return {
                "type": "github_discovery",
                "result": self.discover_github_opportunities(queries=[query], use_deepseek=True, include_readme=False),
            }
        # Discovery command: "搜货: keyword" or "discover: keyword"
        if clean.startswith(("搜货:", "搜货：", "discover:", "discover：")):
            sep = ":" if ":" in clean else "："
            keyword = clean.split(sep, 1)[1].strip()
            if keyword:
                return {
                    "type": "xianyu_discovery",
                    "result": self.product_discovery.search_products(keyword=keyword),
                }
        # Sourcing commands (NEW)
        if looks_like_sourcing_request(clean):
            payload = sourcing_payload_from_text(clean)
            return {
                "type": "sourcing_evaluation",
                "result": self.evaluate_sourcing(payload),
            }
        # Listing strategy commands (NEW)
        if looks_like_listing_strategy_request(clean):
            return {
                "type": "listing_strategy",
                "result": self.create_listing_strategy({"title": clean}),
            }
        if publish:
            return self.handle_feishu_text(clean)
        if looks_like_opportunity_request(clean):
            return {
                "type": "opportunity_evaluation",
                "result": self.evaluate_virtual_product_opportunity(payload_from_text(clean), use_deepseek=True, notify=False),
            }
        if looks_like_url_crawl_request(clean):
            return {
                "type": "url_crawl_opportunity",
                "result": self.crawl_url_opportunity(crawl_payload_from_text(clean), use_deepseek=True, notify=False),
            }
        return {"type": "listing", "result": self.create_listing(clean, source="console", publish=False)}

    def load_memory(self) -> Dict[str, Any]:
        return self.memory.load()

    def remember(self, text: str, source: str = "api") -> Dict[str, Any]:
        return self.memory.remember(text, source=source)

    def _draft_from_text(self, raw_text: str, source: str, product_images: List[str]) -> ListingDraft:
        user_prompt = f"""Create a Xianyu listing from this user request:

{raw_text}

Long-term memory and preferences:
{self.memory.context_for_prompt()}

Rules:
- Title should be concise, under 30 Chinese characters if possible.
- Description should be truthful, formatted for mobile reading, and reduce repetitive customer questions.
- Generate 2 to 5 image search queries, but do not claim searched images are owned.
- Add FAQ answers that avoid needing a human customer-service reply.
- If no digital delivery content is provided, delivery_type must be manual or physical.
- Do NOT copy other sellers' images or listing text as owned content.
- Do NOT fabricate certifications, brand authorization, sales volume, or fake reviews.
- If using low-price entry strategy, the description MUST clarify what the listed price covers (spec/size/model).
- All drafts are approval candidates only; they must NOT be auto-published.
"""
        llm_error = ""
        try:
            generated = self.llm.chat_json(_system_prompt(), user_prompt) or heuristic_listing(raw_text)
        except Exception as exc:
            llm_error = str(exc)
            generated = heuristic_listing(raw_text)
        image_queries = _string_list(generated.get("image_queries")) or [generated.get("title", "商品图")]
        candidates = self.images.search_many(image_queries)
        selected_images = list(product_images)
        if self.settings.allow_external_product_images:
            selected_images.extend([item.url for item in candidates if item.usable_for_publish])
        compliance_notes = []
        if candidates and not self.settings.allow_external_product_images:
            compliance_notes.append("External image candidates are reference-only; provide owned or authorized images for publishing.")
        if not product_images:
            compliance_notes.append("No owned product images were provided.")
        if llm_error:
            compliance_notes.append(f"LLM unavailable; heuristic draft used. Error: {llm_error}")

        return ListingDraft(
            title=str(generated.get("title") or "待上架商品")[:60],
            category=str(generated.get("category") or ""),
            price=_optional_float(generated.get("price")),
            stock=_optional_int(generated.get("stock")),
            condition=str(generated.get("condition") or "new"),
            description=str(generated.get("description") or raw_text).strip(),
            tags=_string_list(generated.get("tags")),
            image_queries=image_queries,
            image_candidates=_image_candidates(candidates),
            selected_images=selected_images,
            delivery_type=str(generated.get("delivery_type") or "manual"),
            delivery_payload=str(generated.get("delivery_payload") or ""),
            faqs=_faqs(generated.get("faqs")),
            source=source,
            raw_text=raw_text,
            compliance_notes=compliance_notes,
        )

    # ====== Physical Sourcing Methods (NEW) ======

    def search_sourcing(self, keyword: str, material: Optional[str] = None) -> Dict[str, Any]:
        """Search for physical product sourcing opportunities."""
        if material:
            return {"type": "material_discovery", "results": self.sourcing.discover_by_material(material)}
        return {"type": "sourcing_search", "keyword": keyword, "note": "Use material parameter for material-based discovery"}

    def evaluate_sourcing(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a physical product sourcing opportunity."""
        return self.sourcing.evaluate_sourcing_opportunity(payload)

    def add_supplier(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new supplier."""
        return self.sourcing.add_supplier(
            name=payload["name"],
            platform=payload.get("platform", "pinduoduo"),
            contact=payload.get("contact", ""),
            product_categories=payload.get("product_categories", []),
            is_factory_direct=payload.get("is_factory_direct", False),
            notes=payload.get("notes", ""),
        )

    def list_suppliers(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all suppliers."""
        return self.sourcing.list_suppliers(platform)

    def get_stealth_templates(self, scenario: Optional[str] = None) -> Dict[str, str]:
        """Get stealth shipping templates."""
        return self.sourcing.get_stealth_templates(scenario)

    # ====== Listing Strategy Methods (NEW) ======

    def create_listing_strategy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a listing strategy for a physical product."""
        strategy = self.listing_strategy.create_strategy(
            title=payload.get("title", ""),
            category=payload.get("category", ""),
            actual_price=float(payload.get("actual_price", 0)),
            cost_price=float(payload.get("cost_price", 0)),
            profit_margin=float(payload.get("profit_margin", 0)),
            shipping_origin=payload.get("shipping_origin", ""),
            is_standard=payload.get("is_standard"),
        )
        return strategy.to_dict()

    # ====== After Sales Methods (NEW) ======

    def create_aftersales_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create an after-sales case."""
        return self.after_sales.create_case(
            order_id=payload.get("order_id", ""),
            buyer_id=payload.get("buyer_id", ""),
            issue_type=payload.get("issue_type", self.after_sales.classify_issue(payload.get("buyer_message", ""))),
            product_title=payload.get("product_title", ""),
            product_price=float(payload.get("product_price", 0)),
            buyer_message=payload.get("buyer_message", ""),
            product_size=payload.get("product_size", "small"),
        )

    def list_aftersales_cases(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List after-sales cases."""
        return self.after_sales.list_cases(status)

    def get_aftersales_stats(self) -> Dict[str, Any]:
        """Get after-sales statistics."""
        return self.after_sales.get_stats()

    # ====== Enhanced Physical Product Listing (NEW) ======

    def create_physical_listing(self, raw_text: str, source: str = "manual", publish: bool = False) -> Dict[str, Any]:
        """Create a listing for physical product with sourcing integration."""
        # First, evaluate sourcing opportunity
        sourcing_payload = sourcing_payload_from_text(raw_text)
        if sourcing_payload.get("cost_price") or sourcing_payload.get("resale_price"):
            evaluation = self.sourcing.evaluate_sourcing_opportunity(sourcing_payload)

            # Create listing strategy
            strategy = self.listing_strategy.create_strategy(
                title=sourcing_payload.get("title", raw_text),
                category=sourcing_payload.get("category", ""),
                actual_price=sourcing_payload.get("resale_price", 0),
                cost_price=sourcing_payload.get("cost_price", 0),
                profit_margin=evaluation["economics"].get("profit_margin", 0),
            )

            # Use strategy in draft generation
            enhanced_text = self._enhance_with_strategy(raw_text, strategy)
            result = self.create_listing(enhanced_text, source=source, publish=publish)
            result["sourcing_evaluation"] = evaluation
            result["listing_strategy"] = strategy.to_dict()
            return result

        return self.create_listing(raw_text, source=source, publish=publish)

    def _enhance_with_strategy(self, raw_text: str, strategy) -> str:
        """Enhance listing text with strategy recommendations."""
        ps = strategy.price_strategy
        ls = strategy.location_strategy

        enhancements = []
        if ps.strategy_type != "none":
            enhancements.append(f"定价策略: 引流价{ps.bait_price}元，实际售价{ps.actual_price}元")
        if ls.primary_city:
            enhancements.append(f"发布地址: {ls.primary_city}")
        if strategy.description_focus:
            enhancements.append(f"描述重点: {strategy.description_focus}")

        if enhancements:
            return f"{raw_text}\n\n[策略建议]\n" + "\n".join(enhancements)
        return raw_text

    def _delivery_payload_for_listing(self, listing_id: str) -> str:
        for record in self.storage.list_published():
            if record.get("listing_id") == listing_id or record.get("external_id") == listing_id:
                return str(record.get("delivery_payload") or "")
        return ""


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _faqs(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: List[Dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            q = str(item.get("q") or item.get("question") or "").strip()
            a = str(item.get("a") or item.get("answer") or "").strip()
            if q and a:
                result.append({"q": q, "a": a})
    return result


def _image_candidates(candidates: List[ImageCandidate]) -> List[ImageCandidate]:
    return [item for item in candidates if item.url]


def _system_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return SYSTEM_PROMPT
