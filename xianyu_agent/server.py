from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .config import Settings
from .db_manager import db_manager
from .feishu import extract_feishu_text, extract_xianyu_inbound_message, verify_feishu_token
from .scheduler import github_discovery_loop, polish_loop, product_discovery_loop
from .workflow import CommerceWorkflow
from .llm_manager import llm_manager
from .agent_registry import registry
from .agents import create_all_agents
from .agents.base import BaseAgent
from .agent_scheduler import start_agent_scheduler
from .models import ListingDraft
from .openclaw_router import OpenClawRouter
from .work_orders import work_order_manager
from .action_executor import action_executor
from .bridge_events import bridge_events
from .execution_queue import execution_queue
from .message_intake import get_message_intake_runbooks, message_intake_ledger
from .opc_dashboard import OPCDashboard
from .resale import ResaleManager, init_resale_manager, resale_manager

settings = Settings.from_env()
workflow = CommerceWorkflow(settings)
app = FastAPI(title="Xianyu Commerce Agent", version="0.6.0")

# Initialize multi-agent system with real workflow
llm_manager.init(settings)
BaseAgent.set_workflow(workflow)
registry.configure_persistence(settings.data_dir / "agent_collaboration.jsonl")
work_order_manager.configure(settings.data_dir / "work_orders.json")
execution_queue.configure(settings.data_dir / "execution_jobs.json")
message_intake_ledger.configure(settings.data_dir / "message_intake.json")
bridge_events.configure(settings.data_dir / "bridge_events.json")
resale_manager = init_resale_manager(settings.data_dir)
BaseAgent.set_registry(registry)
BaseAgent.set_work_order_manager(work_order_manager)
agents = create_all_agents()
for name, agent in agents.items():
    registry.register_agent(name, agent)
    for action, handler in agent.get_handlers().items():
        registry.register_handler(name, action, handler)
openclaw_router = OpenClawRouter(registry=registry, workflow=workflow)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.on_event("startup")
async def on_startup() -> None:
    # Legacy scheduler loops
    asyncio.create_task(polish_loop(workflow))
    asyncio.create_task(github_discovery_loop(workflow))
    asyncio.create_task(product_discovery_loop(workflow))
    # Agent scheduler loops
    asyncio.create_task(start_agent_scheduler(agents, settings))


@app.get("/health")
def health() -> Dict[str, Any]:
    memory = workflow.load_memory()
    return {
        "ok": True,
        "adapter": workflow.adapter.name,
        "model": settings.deepseek_model,
        "deepseek_configured": bool(settings.deepseek_api_key),
        "memory_updated_at": memory.get("updated_at", ""),
    }


@app.get("/", response_class=HTMLResponse)
def root() -> FileResponse:
    return FileResponse(WEB_DIR / "console.html")


@app.get("/console", response_class=HTMLResponse)
def console() -> FileResponse:
    return FileResponse(WEB_DIR / "console.html")


@app.get("/console/style.css")
def console_css() -> FileResponse:
    return FileResponse(WEB_DIR / "style.css")


@app.get("/console/app.js")
def console_js() -> FileResponse:
    return FileResponse(WEB_DIR / "app.js")


@app.get("/memory")
def memory() -> Dict[str, Any]:
    return workflow.load_memory()


@app.post("/memory")
async def remember(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    text = str(payload.get("text") or payload.get("note") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    return workflow.remember(text, source=str(payload.get("source") or "api"))


@app.post("/agent/message")
async def agent_message(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    return openclaw_router.process_message(
        text,
        context=payload.get("context") or {},
        publish=bool(payload.get("publish", True)),
    )


@app.get("/guard/status")
def guard_status() -> Dict[str, Any]:
    return workflow.platform_guard_status()


@app.post("/guard/check")
async def guard_check(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return workflow.platform_guard_check(str(payload.get("text") or ""), context=payload.get("context") or {})


@app.post("/guard/resume")
async def guard_resume(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return workflow.platform_guard_resume(note=str(payload.get("note") or ""))


@app.get("/edge/status")
def edge_browser_status() -> Dict[str, Any]:
    """Edge browser CDP status (legacy)."""
    return workflow.browser_status()


@app.post("/edge/open")
async def browser_open(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return workflow.browser_open_url(
            str(payload.get("url") or ""),
            wait_seconds=float(payload.get("wait_seconds") or 3.0),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/listings")
async def create_listing(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    text = str(payload.get("text") or payload.get("raw_text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    images = payload.get("images") or []
    publish = bool(payload.get("publish", True))
    return workflow.create_listing(text, source="api", product_images=images, publish=publish)


@app.post("/feishu/events")
async def feishu_events(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    if not verify_feishu_token(payload, settings):
        raise HTTPException(status_code=403, detail="invalid feishu token")
    inbound_message = extract_xianyu_inbound_message(payload)
    if inbound_message:
        return {
            "ok": True,
            "result": _create_inbound_message_work_order(InboundMessageRequest(**inbound_message)),
        }
    text = extract_feishu_text(payload)
    if not text:
        return {"ok": True, "ignored": "no text message found"}
    result = workflow.handle_feishu_text(text)
    return {"ok": True, "result": result}


@app.post("/orders/paid")
async def order_paid(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return workflow.fulfill_order(payload)


@app.post("/tasks/polish")
def polish() -> Dict[str, Any]:
    return workflow.polish_all()


@app.post("/research/github")
async def github_research(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    queries = payload.get("queries")
    if not queries and payload.get("query"):
        queries = [payload["query"]]
    return workflow.discover_github_opportunities(
        queries=queries,
        min_stars=payload.get("min_stars"),
        limit_per_query=payload.get("limit"),
        use_deepseek=bool(payload.get("use_deepseek", True)),
        include_readme=bool(payload.get("include_readme", False)),
    )


@app.post("/opportunities/evaluate")
async def evaluate_opportunity(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return workflow.evaluate_virtual_product_opportunity(
        payload,
        use_deepseek=bool(payload.get("use_deepseek", True)),
        notify=payload.get("notify"),
    )


@app.post("/crawler/url")
async def crawl_url(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return workflow.crawl_url_opportunity(
            payload,
            use_deepseek=bool(payload.get("use_deepseek", True)),
            notify=payload.get("notify"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/opportunities/{opportunity_id}/decision")
async def decide_opportunity(opportunity_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return workflow.decide_virtual_product_opportunity(
        opportunity_id,
        str(payload.get("decision") or ""),
        note=str(payload.get("note") or ""),
    )


@app.post("/opportunities/{opportunity_id}/purchase")
async def record_opportunity_purchase(opportunity_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return workflow.record_virtual_product_purchase(opportunity_id, payload)


@app.post("/opportunities/{opportunity_id}/assets")
async def save_opportunity_assets(opportunity_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return workflow.save_virtual_product_assets(opportunity_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/opportunities/{opportunity_id}/list")
async def list_opportunity(opportunity_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return workflow.create_listing_from_opportunity(opportunity_id, publish=bool(payload.get("publish", True)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Product Discovery Endpoints ---

@app.post("/discovery/search")
async def discovery_search(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")
    return await workflow.run_discovery(
        keyword=keyword,
        max_pages=int(payload.get("max_pages") or 3),
        min_price=payload.get("min_price"),
        max_price=payload.get("max_price"),
    )


@app.get("/discovery/tasks")
def discovery_tasks() -> list:
    return workflow.list_discovery_tasks()


@app.post("/discovery/tasks")
async def add_discovery_task(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not payload.get("keyword"):
        raise HTTPException(status_code=400, detail="keyword is required")
    return workflow.add_discovery_task(payload)


@app.delete("/discovery/tasks/{task_id}")
def remove_discovery_task(task_id: str) -> Dict[str, Any]:
    ok = workflow.remove_discovery_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True}


@app.get("/discovery/tasks/{task_id}/results")
def discovery_results(task_id: str, limit: int = 50) -> list:
    return workflow.get_discovery_results(task_id, limit=limit)


@app.post("/discovery/run-all")
async def run_all_discovery() -> Dict[str, Any]:
    return await workflow.run_all_discovery_tasks()


# --- Physical Sourcing Endpoints (NEW) ---

@app.post("/sourcing/evaluate")
async def sourcing_evaluate(request: Request) -> Dict[str, Any]:
    """Evaluate a physical product sourcing opportunity."""
    payload = await request.json()
    return workflow.evaluate_sourcing(payload)


@app.post("/sourcing/material")
async def sourcing_material(request: Request) -> Dict[str, Any]:
    """Discover products by raw material (原材料选品法)."""
    payload = await request.json()
    material = str(payload.get("material") or "").strip()
    if not material:
        raise HTTPException(status_code=400, detail="material is required")
    return {"results": workflow.search_sourcing(material=material)}


@app.post("/sourcing/suppliers")
async def add_supplier(request: Request) -> Dict[str, Any]:
    """Add a new supplier."""
    payload = await request.json()
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    return workflow.add_supplier(payload)


@app.get("/sourcing/suppliers")
def list_suppliers(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all suppliers."""
    return workflow.list_suppliers(platform)


@app.get("/sourcing/stealth-templates")
def get_stealth_templates(scenario: Optional[str] = None) -> Dict[str, str]:
    """Get stealth shipping templates."""
    return workflow.get_stealth_templates(scenario)


# --- Listing Strategy Endpoints (NEW) ---

@app.post("/listings/strategy")
async def create_listing_strategy(request: Request) -> Dict[str, Any]:
    """Create a listing strategy for a physical product."""
    payload = await request.json()
    if not payload.get("title"):
        raise HTTPException(status_code=400, detail="title is required")
    return workflow.create_listing_strategy(payload)


@app.post("/listings/physical")
async def create_physical_listing(request: Request) -> Dict[str, Any]:
    """Create a physical product listing with sourcing integration."""
    payload = await request.json()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    return workflow.create_physical_listing(text, source="api", publish=bool(payload.get("publish", True)))


# --- After Sales Endpoints (NEW) ---

@app.post("/aftersales/cases")
async def create_aftersales_case(request: Request) -> Dict[str, Any]:
    """Create an after-sales case."""
    payload = await request.json()
    if not payload.get("buyer_message"):
        raise HTTPException(status_code=400, detail="buyer_message is required")
    return workflow.create_aftersales_case(payload)


@app.get("/aftersales/cases")
def list_aftersales_cases(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List after-sales cases."""
    return workflow.list_aftersales_cases(status)


@app.get("/aftersales/stats")
def get_aftersales_stats() -> Dict[str, Any]:
    """Get after-sales statistics."""
    return workflow.get_aftersales_stats()


# --- Auto Reply Endpoints ---

@app.post("/auto-reply/generate")
async def auto_reply_generate(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    buyer_id = str(payload.get("buyer_id") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not buyer_id or not message:
        raise HTTPException(status_code=400, detail="buyer_id and message are required")
    reply = workflow.generate_auto_reply(
        buyer_id=buyer_id,
        message=message,
        product_id=str(payload.get("product_id") or ""),
        product_title=str(payload.get("product_title") or ""),
        product_description=str(payload.get("product_description") or ""),
        product_price=str(payload.get("product_price") or ""),
    )
    return {"reply": reply, "has_reply": reply is not None}


@app.get("/auto-reply/rules")
def list_reply_rules() -> list:
    return workflow.list_reply_rules()


@app.post("/auto-reply/rules")
async def add_reply_rule(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not payload.get("keyword") or not payload.get("reply_text"):
        raise HTTPException(status_code=400, detail="keyword and reply_text are required")
    return workflow.add_reply_rule(payload)


@app.delete("/auto-reply/rules/{rule_id}")
def remove_reply_rule(rule_id: str) -> Dict[str, Any]:
    ok = workflow.remove_reply_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"ok": True}


# --- Multi-Account Management Endpoints ---


class CookieCreateRequest(BaseModel):
    cookie_id: str
    cookie_value: str
    remark: str = ""


class CookieUpdateRequest(BaseModel):
    cookie_value: str


class KeywordRequest(BaseModel):
    keyword: str
    reply: str


class KeywordsBatchRequest(BaseModel):
    keywords: List[Dict[str, str]]


class AIReplySettingsRequest(BaseModel):
    ai_enabled: bool = False
    model_name: str = "deepseek-v4-flash"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    max_discount_percent: int = 10
    max_discount_amount: int = 100
    max_bargain_rounds: int = 3
    custom_prompts: Dict[str, str] = {}


@app.get("/accounts")
def list_accounts() -> List[Dict[str, Any]]:
    """List all cookie accounts with their status."""
    cookies = db_manager.get_all_cookies()
    statuses = db_manager.get_all_cookie_status()
    result = []
    for cookie_id, cookie_value in cookies.items():
        details = db_manager.get_cookie_details(cookie_id) or {}
        result.append({
            "cookie_id": cookie_id,
            "enabled": statuses.get(cookie_id, True),
            "auto_confirm": bool(details.get("auto_confirm", 1)),
            "remark": details.get("remark", ""),
            "pause_duration": details.get("pause_duration", 10),
            "created_at": details.get("created_at", ""),
        })
    return result


@app.post("/accounts")
def add_account(req: CookieCreateRequest) -> Dict[str, Any]:
    """Add a new cookie account."""
    db_manager.save_cookie(req.cookie_id, req.cookie_value, req.remark)
    return {"ok": True, "cookie_id": req.cookie_id}


@app.put("/accounts/{cookie_id}")
def update_account(cookie_id: str, req: CookieUpdateRequest) -> Dict[str, Any]:
    """Update cookie value for an account."""
    existing = db_manager.get_cookie(cookie_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Account not found")
    db_manager.update_cookie_value(cookie_id, req.cookie_value)
    return {"ok": True}


@app.delete("/accounts/{cookie_id}")
def delete_account(cookie_id: str) -> Dict[str, Any]:
    """Delete a cookie account."""
    existing = db_manager.get_cookie(cookie_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Account not found")
    db_manager.delete_cookie(cookie_id)
    return {"ok": True}


@app.post("/accounts/{cookie_id}/toggle")
def toggle_account(cookie_id: str) -> Dict[str, Any]:
    """Toggle account enabled/disabled status."""
    current = db_manager.get_all_cookie_status().get(cookie_id, True)
    db_manager.save_cookie_status(cookie_id, not current)
    return {"ok": True, "enabled": not current}


@app.post("/accounts/{cookie_id}/auto-confirm")
def set_auto_confirm(cookie_id: str, enabled: bool = True) -> Dict[str, Any]:
    """Set auto-confirm delivery for an account."""
    db_manager.set_auto_confirm(cookie_id, enabled)
    return {"ok": True, "auto_confirm": enabled}


@app.get("/accounts/{cookie_id}/keywords")
def get_account_keywords(cookie_id: str) -> List[Dict[str, str]]:
    """Get keywords for an account."""
    keywords = db_manager.get_keywords(cookie_id)
    return [{"keyword": k, "reply": r} for k, r in keywords]


@app.post("/accounts/{cookie_id}/keywords")
def add_account_keyword(cookie_id: str, req: KeywordRequest) -> Dict[str, Any]:
    """Add a keyword rule for an account."""
    existing = db_manager.get_keywords(cookie_id)
    existing.append((req.keyword, req.reply))
    db_manager.save_keywords(cookie_id, existing)
    return {"ok": True}


@app.put("/accounts/{cookie_id}/keywords")
def replace_account_keywords(cookie_id: str, req: KeywordsBatchRequest) -> Dict[str, Any]:
    """Replace all keywords for an account."""
    keywords = [(k["keyword"], k["reply"]) for k in req.keywords]
    db_manager.save_keywords(cookie_id, keywords)
    return {"ok": True, "count": len(keywords)}


@app.get("/accounts/{cookie_id}/ai-settings")
def get_ai_settings(cookie_id: str) -> Dict[str, Any]:
    """Get AI reply settings for an account."""
    settings = db_manager.get_ai_reply_settings(cookie_id)
    if not settings:
        return {"cookie_id": cookie_id, "ai_enabled": False}
    return settings


@app.post("/accounts/{cookie_id}/ai-settings")
def save_ai_settings(cookie_id: str, req: AIReplySettingsRequest) -> Dict[str, Any]:
    """Save AI reply settings for an account."""
    db_manager.save_ai_reply_settings(cookie_id, req.dict())
    return {"ok": True}


@app.get("/accounts/{cookie_id}/orders")
def get_account_orders(cookie_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get orders for an account."""
    return db_manager.get_orders_by_cookie(cookie_id, status=status)


@app.get("/orders/{order_id}")
def get_order(order_id: str) -> Dict[str, Any]:
    """Get order details."""
    order = db_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/accounts/{cookie_id}/conversations")
def get_conversations(cookie_id: str, chat_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Get conversation history for a chat."""
    return db_manager.get_conversation_history(cookie_id, chat_id, limit)


@app.get("/accounts/{cookie_id}/items")
def get_account_items(cookie_id: str) -> List[Dict[str, Any]]:
    """Get items for an account."""
    return db_manager.get_items_by_cookie(cookie_id)


@app.get("/items")
def get_all_items() -> List[Dict[str, Any]]:
    """Get all items across accounts."""
    return db_manager.get_all_items()


@app.get("/browser-pool/status")
def browser_pool_status() -> Dict[str, Any]:
    """Get browser pool status."""
    return workflow.browser_pool.get_pool_status()


@app.get("/order-handler/status")
def order_handler_status() -> Dict[str, Any]:
    """Get order status handler info."""
    from .order_status_handler import order_status_handler
    return {
        "status_labels": order_status_handler.STATUS_LABELS,
        "valid_transitions": order_status_handler.VALID_TRANSITIONS,
        "pending_updates": len(order_status_handler.pending_updates),
    }


# --- Ruflo Multi-Agent Orchestration Endpoints ---


class RufloSpawnRequest(BaseModel):
    task: str
    params: Dict[str, Any] = {}


class RufloMemoryRequest(BaseModel):
    key: str
    value: Any
    namespace: str = "default"
    ttl: int = 0


@app.get("/ruflo/status")
def ruflo_status() -> Dict[str, Any]:
    """Get Ruflo agent status."""
    from .ruflo_integration import ruflo
    return ruflo.swarm_status()


@app.get("/ruflo/capabilities")
def ruflo_capabilities() -> List[str]:
    """List agent capabilities for swarm coordination."""
    from .ruflo_integration import ruflo
    return ruflo.swarm_capabilities()


@app.post("/ruflo/spawn")
async def ruflo_spawn(req: RufloSpawnRequest) -> Dict[str, Any]:
    """Spawn an agent task via Ruflo."""
    from .ruflo_integration import ruflo
    return await ruflo.agent_spawn(req.task, req.params)


@app.post("/ruflo/memory/store")
def ruflo_memory_store(req: RufloMemoryRequest) -> Dict[str, Any]:
    """Store a value in Ruflo memory."""
    from .ruflo_integration import ruflo
    return ruflo.memory_store(req.key, req.value, req.namespace, req.ttl)


@app.get("/ruflo/memory/search")
def ruflo_memory_search(query: str, namespace: str = "default", limit: int = 10) -> List[Dict[str, Any]]:
    """Search Ruflo memory."""
    from .ruflo_integration import ruflo
    return ruflo.memory_search(query, namespace, limit)


@app.get("/ruflo/memory/{namespace}/{key}")
def ruflo_memory_get(namespace: str, key: str) -> Dict[str, Any]:
    """Get a specific memory entry."""
    from .ruflo_integration import ruflo
    value = ruflo.memory_get(key, namespace)
    if value is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"key": key, "namespace": namespace, "value": value}


# --- Multi-Agent System Endpoints ---


class AgentTaskRequest(BaseModel):
    agent: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)


class OpenClawMessageRequest(BaseModel):
    message: str
    context: Dict[str, Any] = Field(default_factory=dict)
    publish: bool = True


class AgentDiscussionRequest(BaseModel):
    topic: str
    participants: List[str] = Field(default_factory=list)
    rounds: int = 2
    context: Dict[str, Any] = Field(default_factory=dict)


class WorkOrderCreateRequest(BaseModel):
    kind: str
    title: str
    owner: str = "openclaw"
    context: Dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "normal"


class WorkOrderPlanRequest(BaseModel):
    discussion: Dict[str, Any] = Field(default_factory=dict)
    proposed_actions: List[Dict[str, Any]] = Field(default_factory=list)


class ApprovalDecisionRequest(BaseModel):
    decision: str
    note: str = ""


class EcommerceListingDraftRequest(BaseModel):
    candidate_id: str
    title: str
    description: str = ""
    price_cny: Optional[float] = None
    stock: Optional[int] = None
    source_platform: str = "ecommerce"
    supplier_name: str = ""
    source_url: str = ""
    image_url: str = ""
    tags: List[str] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


@app.get("/agents")
def list_agents() -> List[Dict[str, Any]]:
    """List all registered agents and their status."""
    return llm_manager.list_agents()


@app.get("/agents/status")
def agents_status() -> Dict[str, Any]:
    """Get agent registry status."""
    return registry.get_status()


@app.get("/agents/collaboration")
def agents_collaboration() -> Dict[str, Any]:
    """Get the current agent collaboration topology."""
    status = registry.get_status()
    return {
        "entrypoint": "openclaw",
        "registered_agents": status["registered_agents"],
        "collaboration": {
            "listing": ["discovery", "analytics"],
            "customer_service": ["order", "listing"],
            "order": ["customer_service", "analytics"],
            "github": ["analytics", "listing"],
            "discovery": ["analytics", "listing"],
            "analytics": ["listing", "discovery", "order"],
        },
        "recent_messages": registry.get_message_history(10),
    }


@app.post("/agents/dispatch")
async def dispatch_agent_task(req: AgentTaskRequest) -> Dict[str, Any]:
    """Dispatch a task to a specific agent."""
    task = registry.dispatch(req.agent, req.action, req.params)
    # Check if the result itself indicates failure
    ok = task.status == "completed"
    if ok and isinstance(task.result, dict) and task.result.get("ok") is False:
        ok = False
    return {
        "ok": ok,
        "task_id": task.task_id,
        "agent": task.agent,
        "action": task.action,
        "status": task.status,
        "result": task.result,
        "error": task.error,
    }


@app.post("/agents/openclaw/message")
async def openclaw_message(req: OpenClawMessageRequest) -> Dict[str, Any]:
    """Send a message to OpenClaw, the direct orchestrator."""
    return openclaw_router.process_message(req.message, req.context, publish=req.publish)


@app.post("/agents/hermes/message")
async def hermes_message(req: OpenClawMessageRequest) -> Dict[str, Any]:
    """Compatibility alias for older clients; routes through OpenClaw."""
    result = openclaw_router.process_message(req.message, req.context, publish=req.publish)
    result["deprecated"] = "Use /agents/openclaw/message"
    return result


@app.post("/agents/customer-service/reply")
async def customer_service_reply(request: Request) -> Dict[str, Any]:
    """Generate a customer service reply using DeepSeek V4 Flash."""
    payload = await request.json()
    cs = agents.get("customer_service")
    if not cs:
        raise HTTPException(status_code=500, detail="Customer service agent not initialized")
    reply = cs.generate_reply(
        buyer_id=str(payload.get("buyer_id", "api_user")),
        message=str(payload.get("message", "")),
        product_id=str(payload.get("product_id", "")),
        product_title=str(payload.get("product_title", "")),
        product_description=str(payload.get("product_description", "")),
        product_price=str(payload.get("product_price", "")),
        cookie_id=str(payload.get("cookie_id", "")),
    )
    return {"ok": True, "reply": reply, "has_reply": reply is not None}


@app.post("/agents/listing/create")
async def listing_create(request: Request) -> Dict[str, Any]:
    """Create a product listing using MiMo."""
    payload = await request.json()
    listing_agent = agents.get("listing")
    if not listing_agent:
        raise HTTPException(status_code=500, detail="Listing agent not initialized")
    return listing_agent.create_listing(payload)


@app.post("/agents/discovery/analyze")
async def discovery_analyze(request: Request) -> Dict[str, Any]:
    """Analyze market using MiMo."""
    payload = await request.json()
    discovery = agents.get("discovery")
    if not discovery:
        raise HTTPException(status_code=500, detail="Discovery agent not initialized")
    return discovery.analyze_market(payload)


@app.post("/agents/discovery/opportunities")
async def discovery_opportunities(request: Request) -> Dict[str, Any]:
    """Find product opportunities using MiMo."""
    payload = await request.json()
    discovery = agents.get("discovery")
    if not discovery:
        raise HTTPException(status_code=500, detail="Discovery agent not initialized")
    return discovery.find_opportunities(payload)


@app.post("/agents/analytics/profit")
async def analytics_profit(request: Request) -> Dict[str, Any]:
    """Calculate profit using MiMo."""
    payload = await request.json()
    analytics = agents.get("analytics")
    if not analytics:
        raise HTTPException(status_code=500, detail="Analytics agent not initialized")
    try:
        return analytics.calculate_profit(payload)
    except Exception as e:
        import traceback
        logger.error("Profit calculation failed: %s\n%s", e, traceback.format_exc())
        # Return profit without LLM advice as fallback
        cost = payload.get("cost", 0)
        price = payload.get("price", 0)
        platform_fee_rate = payload.get("platform_fee_rate", 0.08)
        shipping = payload.get("shipping_cost", 0)
        platform_fee = price * platform_fee_rate
        profit = price - cost - platform_fee - shipping
        profit_rate = profit / cost if cost > 0 else 0
        return {
            "ok": True,
            "profit": round(profit, 2),
            "profit_rate": round(profit_rate, 4),
            "platform_fee": round(platform_fee, 2),
            "advice": "(LLM unavailable)",
        }


@app.post("/agents/analytics/report")
async def analytics_report(request: Request) -> Dict[str, Any]:
    """Generate business report using MiMo."""
    payload = await request.json()
    analytics = agents.get("analytics")
    if not analytics:
        raise HTTPException(status_code=500, detail="Analytics agent not initialized")
    return analytics.generate_report(payload)


@app.post("/agents/github/analyze")
async def github_analyze(request: Request) -> Dict[str, Any]:
    """Analyze a GitHub repo using MiMo."""
    payload = await request.json()
    github = agents.get("github")
    if not github:
        raise HTTPException(status_code=500, detail="GitHub agent not initialized")
    return github.analyze_repo(payload)


@app.get("/agents/tasks")
def agent_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    """Get agent task history."""
    return registry.get_task_history(limit)


@app.get("/agents/messages")
def agent_messages(limit: int = 50) -> List[Dict[str, Any]]:
    """Get agent-to-agent message history."""
    return registry.get_message_history(limit)


@app.post("/agents/discuss")
async def agents_discuss(req: AgentDiscussionRequest) -> Dict[str, Any]:
    """Run a round-robin discussion among agents."""
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    participants = req.participants or ["listing", "discovery", "analytics"]
    return registry.discuss(topic=topic, participants=participants, rounds=req.rounds, context=req.context)


@app.get("/agents/discussions")
def agent_discussions(limit: int = 20) -> List[Dict[str, Any]]:
    """Get recent multi-agent discussions."""
    return registry.get_discussion_history(limit)


@app.get("/work-orders")
def list_work_orders(limit: int = 50) -> List[Dict[str, Any]]:
    """List recent realistic multi-agent work orders."""
    return work_order_manager.list_work_orders(limit)


@app.post("/work-orders")
async def create_work_order(req: WorkOrderCreateRequest) -> Dict[str, Any]:
    """Create a work order with owner, goal, risk level, and context."""
    if not req.kind.strip() or not req.title.strip():
        raise HTTPException(status_code=400, detail="kind and title are required")
    return work_order_manager.create_work_order(
        kind=req.kind.strip(),
        title=req.title.strip(),
        owner=req.owner.strip() or "openclaw",
        context=req.context,
        risk_level=req.risk_level or "normal",
    )


@app.get("/work-orders/{work_order_id}")
def get_work_order(work_order_id: str) -> Dict[str, Any]:
    """Get a single work order."""
    try:
        return work_order_manager.get_work_order(work_order_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/work-orders/{work_order_id}/plan")
async def create_work_order_plan(work_order_id: str, req: WorkOrderPlanRequest) -> Dict[str, Any]:
    """Create an action plan for a work order."""
    try:
        return work_order_manager.create_action_plan(
            work_order_id,
            discussion=req.discussion or None,
            proposed_actions=req.proposed_actions or None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/integrations/ecommerce/listing-drafts")
async def create_ecommerce_listing_draft(req: EcommerceListingDraftRequest) -> Dict[str, Any]:
    """Create a Xianyu listing draft approval from an ecommerce candidate.

    This endpoint only creates a WorkOrder and pending publish approval.
    It does not approve, enqueue, publish, message, order, pay, or refund.
    """
    candidate_id = req.candidate_id.strip()
    title = req.title.strip()
    if not candidate_id:
        raise HTTPException(status_code=400, detail="candidate_id is required")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    work_order = work_order_manager.create_work_order(
        kind="ecommerce_candidate",
        title=f"Xianyu draft: {title[:60]}",
        owner="listing",
        context={
            "source_project": "ecommerce-openclaw-system",
            "candidate_id": candidate_id,
            "source_platform": req.source_platform,
            "supplier_name": req.supplier_name,
            "source_url": req.source_url,
            "ecommerce_context": req.context,
        },
        risk_level="normal",
    )

    draft_snapshot = {
        "source_project": "ecommerce-openclaw-system",
        "candidate_id": candidate_id,
        "title": title[:120],
        "price_cny": req.price_cny,
        "stock": req.stock,
        "source_platform": req.source_platform,
        "supplier_name": req.supplier_name,
        "has_image_reference": bool(req.image_url),
        "approval_note": "Only creates Xianyu draft approval; no auto publish.",
    }

    plan = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[
            {
                "agent": "listing",
                "action": "publish_listing",
                "description": f"Create Xianyu publish approval for ecommerce candidate {candidate_id}",
                "params": {
                    "candidate_id": candidate_id,
                    "title": title,
                    "description": req.description,
                    "price": req.price_cny,
                    "stock": req.stock,
                    "source_platform": req.source_platform,
                    "source_url": req.source_url,
                    "image_url": req.image_url,
                    "tags": req.tags,
                },
                "draft_snapshot": draft_snapshot,
            },
        ],
    )

    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
        "candidate_id": candidate_id,
    }


class ReviewResultRequest(BaseModel):
    candidate_id: str
    xianyu_scoring: Optional[Dict[str, Any]] = None
    listing_status: Optional[str] = None
    inquiry_count: int = 0
    order_count: int = 0
    after_sales_count: int = 0
    operator_decision: Optional[str] = None
    operator_note: Optional[str] = None


@app.post("/integrations/ecommerce/review-results")
async def push_review_results(req: ReviewResultRequest) -> Dict[str, Any]:
    """Push Xianyu candidate review results to ecommerce. Idempotent."""
    candidate_id = req.candidate_id.strip()
    if not candidate_id:
        raise HTTPException(status_code=400, detail="candidate_id is required")

    today = time.strftime("%Y-%m-%d", time.localtime())
    idempotency_key = f"{candidate_id}_review_{today}"

    event = bridge_events.record(
        direction="xianyu_to_ecommerce",
        event_type="review_result",
        candidate_id=candidate_id,
        idempotency_key=idempotency_key,
        payload={
            "xianyu_scoring": req.xianyu_scoring,
            "listing_status": req.listing_status,
            "inquiry_count": req.inquiry_count,
            "order_count": req.order_count,
            "after_sales_count": req.after_sales_count,
            "operator_decision": req.operator_decision,
            "operator_note": req.operator_note,
        },
    )

    return {"ok": True, "event": event, "duplicate": event.get("duplicate_count", 0) > 0}


@app.get("/integrations/ecommerce/bridge-events")
async def list_bridge_events(
    direction: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """List bridge events. Read-only."""
    events = bridge_events.list_events(direction=direction, event_type=event_type, limit=limit)
    return {"ok": True, "events": events, "metrics": bridge_events.metrics()}


@app.post("/approvals/{approval_id}/decision")
async def decide_approval(approval_id: str, req: ApprovalDecisionRequest) -> Dict[str, Any]:
    """Approve or reject a pending human approval request."""
    try:
        return work_order_manager.decide_approval(approval_id, req.decision, note=req.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/approvals/{approval_id}/execute")
async def execute_approved_approval(approval_id: str) -> Dict[str, Any]:
    """Enqueue an approved approval for execution (creates a job, does not execute immediately)."""
    try:
        job = execution_queue.create_job(approval_id, work_order_manager)
        return {"ok": True, "job_id": job["job_id"], "status": job["status"], "action": job["action"]}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ============================================================
# Execution Queue Endpoints
# ============================================================

@app.get("/execution-jobs")
def list_execution_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    """List execution jobs."""
    return execution_queue.list_jobs(limit)


@app.get("/execution-jobs/{job_id}")
def get_execution_job(job_id: str) -> Dict[str, Any]:
    """Get a single execution job."""
    try:
        return execution_queue.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/execution-jobs/run-next")
async def run_next_execution_jobs() -> Dict[str, Any]:
    """Execute the next batch of queued jobs."""
    results = execution_queue.run_next(
        action_executor,
        work_order_manager,
        {
            "data_dir": settings.data_dir,
            "workflow": workflow,
        },
    )
    return {"ok": True, "executed": len(results), "results": results}


@app.post("/execution-jobs/run-pending")
async def run_pending_execution_jobs() -> Dict[str, Any]:
    """Execute all currently queued jobs that are ready to run."""
    results = execution_queue.run_pending(
        action_executor,
        work_order_manager,
        {
            "data_dir": settings.data_dir,
            "workflow": workflow,
        },
    )
    return {"ok": True, "executed": len(results), "results": results}


@app.post("/execution-jobs/{job_id}/retry")
async def retry_execution_job(job_id: str) -> Dict[str, Any]:
    """Retry a failed execution job."""
    try:
        return execution_queue.retry_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/execution-jobs/{job_id}/cancel")
async def cancel_execution_job(job_id: str) -> Dict[str, Any]:
    """Cancel a queued execution job."""
    try:
        return execution_queue.cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ============================================================
# Workflow Order Fulfillment Endpoints
# ============================================================

class FulfillOrderRequest(BaseModel):
    delivery_type: str = "digital"
    delivery_payload: str = ""
    cookie_id: str = "default"
    context: Dict[str, Any] = Field(default_factory=dict)


@app.post("/workflows/orders/{order_id}/fulfill")
async def fulfill_order_endpoint(order_id: str, req: FulfillOrderRequest) -> Dict[str, Any]:
    """Create a work order for order fulfillment with approval gate (does not execute directly)."""
    work_order = work_order_manager.create_work_order(
        kind="order_fulfillment",
        title=f"发货 {order_id}",
        owner="order",
        context={"order_id": order_id, "delivery_type": req.delivery_type, "cookie_id": req.cookie_id, **req.context},
    )

    # Mask delivery_payload for snapshot (don't expose full sensitive content)
    payload_summary = req.delivery_payload
    if len(payload_summary) > 20:
        payload_summary = payload_summary[:8] + "***" + payload_summary[-4:]

    plan = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[
            {
                "agent": "order",
                "action": "fulfill_order",
                "description": f"为 {order_id} 发货 ({req.delivery_type})",
                "params": {
                    "order_id": order_id,
                    "delivery_type": req.delivery_type,
                    "delivery_payload": req.delivery_payload,
                    "cookie_id": req.cookie_id,
                },
                "draft_snapshot": {
                    "order_id": order_id,
                    "delivery_type": req.delivery_type,
                    "delivery_payload_summary": payload_summary,
                    "cookie_id": req.cookie_id,
                },
            },
        ],
    )
    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
    }


# ============================================================
# Workflow Customer Message Endpoints
# ============================================================

import re

_RISK_PATTERNS = [
    re.compile(r"微信|vx|VX|weixin|WeChat", re.IGNORECASE),
    re.compile(r"QQ|qq", re.IGNORECASE),
    re.compile(r"支付宝|alipay", re.IGNORECASE),
    re.compile(r"银行卡|bank\s*card", re.IGNORECASE),
    re.compile(r"1[3-9]\d{9}"),  # Chinese phone numbers
    re.compile(r"\d{5,}"),  # long number sequences (possible IDs/phones)
]


def _detect_risk_flags(message: str) -> list:
    """Detect sensitive keywords in message content."""
    flags = []
    for pattern in _RISK_PATTERNS:
        if pattern.search(message):
            flags.append(pattern.pattern)
    return flags


class SendMessageRequest(BaseModel):
    message: str
    cookie_id: str = "default"
    chat_id: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "normal"


@app.post("/workflows/messages/{buyer_id}/send")
async def send_message_endpoint(buyer_id: str, req: SendMessageRequest) -> Dict[str, Any]:
    """Create a work order for sending a message with approval gate (does not send directly)."""
    risk_flags = _detect_risk_flags(req.message)
    risk_level = req.risk_level
    if risk_flags and risk_level == "normal":
        risk_level = "high"

    message_summary = req.message
    if len(message_summary) > 80:
        message_summary = message_summary[:77] + "..."

    work_order = work_order_manager.create_work_order(
        kind="customer_message",
        title=f"消息 {buyer_id}",
        owner="customer_service",
        context={"buyer_id": buyer_id, "message": req.message, "cookie_id": req.cookie_id, **req.context},
        risk_level=risk_level,
    )

    plan = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[
            {
                "agent": "customer_service",
                "action": "send_message",
                "description": f"给 {buyer_id} 发送消息",
                "params": {
                    "buyer_id": buyer_id,
                    "message": req.message,
                    "cookie_id": req.cookie_id,
                    "chat_id": req.chat_id,
                },
                "draft_snapshot": {
                    "buyer_id": buyer_id,
                    "chat_id": req.chat_id,
                    "cookie_id": req.cookie_id,
                    "message_summary": message_summary,
                    "risk_flags": risk_flags,
                },
            },
        ],
    )
    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
        "risk_level": risk_level,
        "risk_flags": risk_flags,
    }


# ============================================================
# Workflow Delete / Refund / Dispute Reply Endpoints
# ============================================================

class DeleteItemRequest(BaseModel):
    reason: str = ""
    cookie_id: str = "default"
    item_title: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)


class RefundOrderRequest(BaseModel):
    amount_cny: Optional[float] = None
    reason: str = ""
    buyer_id: str = ""
    cookie_id: str = "default"
    context: Dict[str, Any] = Field(default_factory=dict)


class DisputeReplyRequest(BaseModel):
    order_id: str = ""
    buyer_id: str
    message: str
    cookie_id: str = "default"
    chat_id: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)


def _snapshot_summary(value: str, limit: int = 80) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


# ============================================================
# Workflow Automatic Message Intake Endpoints
# ============================================================

class InboundMessageRequest(BaseModel):
    source: str = "xianyu_im"
    message_id: str = ""
    buyer_id: str
    buyer_name: str = ""
    message: str
    chat_id: str = ""
    item_id: str = ""
    product_id: str = ""
    product_title: str = ""
    product_description: str = ""
    product_price: str = ""
    cookie_id: str = "default"
    conversation: List[Dict[str, Any]] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class MessageIntakeRetentionRequest(BaseModel):
    retention_days: float = 14
    max_records: int = 5000
    dry_run: bool = False


class MessageIntakeHealthCheckRequest(BaseModel):
    window_minutes: float = 60
    failure_threshold: int = 3
    duplicate_rate_threshold: float = 0.5
    duplicate_min_events: int = 5
    stale_after_minutes: float = 30
    notification_hooks: List[str] = Field(default_factory=list)


def _normalize_conversation(req: InboundMessageRequest) -> List[Dict[str, Any]]:
    normalized = []
    for turn in req.conversation:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or turn.get("message") or "").strip()
        if not content:
            continue
        normalized.append(
            {
                "role": str(turn.get("role") or "buyer"),
                "content": content,
                "time": str(turn.get("time") or turn.get("created_at") or ""),
            }
        )
    if not normalized:
        normalized.append({"role": "buyer", "content": req.message.strip(), "time": ""})
    return normalized


def _dedupe_flags(flags: List[str]) -> List[str]:
    result = []
    seen = set()
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        result.append(flag)
    return result


def _create_inbound_message_work_order(req: InboundMessageRequest) -> Dict[str, Any]:
    buyer_id = req.buyer_id.strip()
    incoming_message = req.message.strip()
    if not buyer_id or not incoming_message:
        raise HTTPException(status_code=400, detail="buyer_id and message are required")

    product_id = (req.product_id or req.item_id).strip()
    event_key = message_intake_ledger.event_key(
        source=req.source,
        cookie_id=req.cookie_id,
        buyer_id=buyer_id,
        chat_id=req.chat_id,
        item_id=req.item_id,
        message=incoming_message,
        message_id=req.message_id,
    )
    existing = message_intake_ledger.find_created(event_key)
    if existing:
        duplicate = message_intake_ledger.record_duplicate(
            event_key=event_key,
            source=req.source,
            duplicate_of=existing.get("work_order_id") or existing.get("intake_id", ""),
        )
        return {
            "ok": True,
            "type": "customer_message_intake_duplicate",
            "duplicate": True,
            "approval_required": False,
            "event_key": event_key,
            "existing_work_order_id": existing.get("work_order_id", ""),
            "existing_record": existing,
            "intake_record": duplicate,
        }

    conversation = _normalize_conversation(req)
    generation_error = ""
    reply_candidate = ""
    try:
        reply_candidate = workflow.generate_auto_reply(
            buyer_id=buyer_id,
            message=incoming_message,
            product_id=product_id,
            product_title=req.product_title,
            product_description=req.product_description,
            product_price=req.product_price,
        ) or ""
    except Exception as exc:
        generation_error = str(exc)

    risk_flags = _dedupe_flags(_detect_risk_flags(incoming_message) + _detect_risk_flags(reply_candidate))
    risk_level = "high" if risk_flags else "normal"
    context = {
        "source": req.source,
        "buyer_id": buyer_id,
        "buyer_name": req.buyer_name,
        "incoming_message": incoming_message,
        "reply_candidate": reply_candidate,
        "chat_id": req.chat_id,
        "item_id": req.item_id,
        "product_id": product_id,
        "product_title": req.product_title,
        "cookie_id": req.cookie_id,
        "conversation": conversation,
        **req.context,
    }
    if generation_error:
        context["generation_error"] = generation_error

    work_order = work_order_manager.create_work_order(
        kind="customer_message",
        title=f"Inbound buyer message {buyer_id}",
        owner="customer_service",
        context=context,
        risk_level=risk_level,
    )

    plan = None
    if reply_candidate:
        plan = work_order_manager.create_action_plan(
            work_order["work_order_id"],
            proposed_actions=[
                {
                    "agent": "customer_service",
                    "action": "send_message",
                    "description": f"Review draft reply for {buyer_id}",
                    "params": {
                        "buyer_id": buyer_id,
                        "message": reply_candidate,
                        "cookie_id": req.cookie_id,
                        "chat_id": req.chat_id,
                        "source": req.source,
                        "item_id": req.item_id,
                        "incoming_message": incoming_message,
                        "conversation_context": conversation,
                    },
                    "draft_snapshot": {
                        "source": req.source,
                        "buyer_id": buyer_id,
                        "buyer_name": req.buyer_name,
                        "chat_id": req.chat_id,
                        "item_id": req.item_id,
                        "cookie_id": req.cookie_id,
                        "incoming_message_summary": _snapshot_summary(incoming_message, 80),
                        "reply_summary": _snapshot_summary(reply_candidate, 120),
                        "risk_flags": risk_flags,
                        "conversation_turns": len(conversation),
                        "approval_note": "Inbound buyer messages create draft replies only; approval is required before sending.",
                    },
                }
            ],
        )

    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    approval_id = ""
    if work_order.get("approvals"):
        approval_id = str(work_order["approvals"][0].get("approval_id") or "")
    intake_status = "created" if reply_candidate else "manual_reply"
    if generation_error:
        intake_status = "generation_failed"
    intake_record = message_intake_ledger.record_created(
        event_key=event_key,
        source=req.source,
        buyer_id=buyer_id,
        chat_id=req.chat_id,
        item_id=req.item_id,
        incoming_message_summary=_snapshot_summary(incoming_message, 80),
        reply_summary=_snapshot_summary(reply_candidate, 120),
        risk_flags=risk_flags,
        work_order_id=work_order["work_order_id"],
        approval_id=approval_id,
        status=intake_status,
        generation_error=generation_error,
    )
    return {
        "ok": True,
        "type": "customer_message_intake",
        "duplicate": False,
        "event_key": event_key,
        "intake_record": intake_record,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": bool(plan and plan.get("status") == "needs_approval"),
        "reply_candidate": reply_candidate,
        "risk_level": risk_level,
        "risk_flags": risk_flags,
        "needs_manual_reply": not bool(reply_candidate),
    }


def _retry_message_intake_record(intake_id: str) -> Dict[str, Any]:
    record = message_intake_ledger.get_record(intake_id)
    if not record:
        raise HTTPException(status_code=404, detail="intake record not found")
    if record.get("status") not in {"manual_reply", "generation_failed", "retry_manual_reply", "retry_generation_failed"}:
        raise HTTPException(status_code=400, detail="only manual-reply fallback records can be retried")

    work_order_id = str(record.get("work_order_id") or "")
    try:
        work_order = work_order_manager.get_work_order(work_order_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="source work order not found") from exc

    context = work_order.get("context") or {}
    buyer_id = str(context.get("buyer_id") or record.get("buyer_id") or "").strip()
    incoming_message = str(context.get("incoming_message") or record.get("incoming_message_summary") or "").strip()
    if not buyer_id or not incoming_message:
        raise HTTPException(status_code=400, detail="source intake record is missing buyer_id or message")

    conversation = context.get("conversation") or [{"role": "buyer", "content": incoming_message, "time": ""}]
    reply_candidate = ""
    generation_error = ""
    try:
        reply_candidate = workflow.generate_auto_reply(
            buyer_id=buyer_id,
            message=incoming_message,
            product_id=str(context.get("product_id") or context.get("item_id") or record.get("item_id") or ""),
            product_title=str(context.get("product_title") or ""),
            product_description=str(context.get("product_description") or ""),
            product_price=str(context.get("product_price") or ""),
        ) or ""
    except Exception as exc:
        generation_error = str(exc)

    risk_flags = _dedupe_flags(_detect_risk_flags(incoming_message) + _detect_risk_flags(reply_candidate))
    if generation_error or not reply_candidate:
        retry_status = "retry_generation_failed" if generation_error else "retry_manual_reply"
        intake_record = message_intake_ledger.record_retry_result(
            intake_id=intake_id,
            status=retry_status,
            reply_summary="",
            approval_id="",
            generation_error=generation_error,
            risk_flags=risk_flags,
        )
        return {
            "ok": True,
            "type": "customer_message_intake_retry",
            "intake_record": intake_record,
            "work_order": work_order,
            "action_plan": None,
            "approval_required": False,
            "reply_candidate": "",
            "risk_flags": risk_flags,
            "needs_manual_reply": True,
            "generation_error": generation_error,
        }

    plan = work_order_manager.create_action_plan(
        work_order_id,
        proposed_actions=[
            {
                "agent": "customer_service",
                "action": "send_message",
                "description": f"Review retry draft reply for {buyer_id}",
                "params": {
                    "buyer_id": buyer_id,
                    "message": reply_candidate,
                    "cookie_id": str(context.get("cookie_id") or "default"),
                    "chat_id": str(context.get("chat_id") or record.get("chat_id") or ""),
                    "source": str(context.get("source") or record.get("source") or ""),
                    "item_id": str(context.get("item_id") or record.get("item_id") or ""),
                    "incoming_message": incoming_message,
                    "conversation_context": conversation,
                    "retry_of_intake_id": intake_id,
                },
                "draft_snapshot": {
                    "source": str(context.get("source") or record.get("source") or ""),
                    "buyer_id": buyer_id,
                    "buyer_name": str(context.get("buyer_name") or ""),
                    "chat_id": str(context.get("chat_id") or record.get("chat_id") or ""),
                    "item_id": str(context.get("item_id") or record.get("item_id") or ""),
                    "cookie_id": str(context.get("cookie_id") or "default"),
                    "incoming_message_summary": _snapshot_summary(incoming_message, 80),
                    "reply_summary": _snapshot_summary(reply_candidate, 120),
                    "risk_flags": risk_flags,
                    "conversation_turns": len(conversation) if isinstance(conversation, list) else 1,
                    "approval_note": "Retry generated a draft reply only; approval is required before sending.",
                    "retry_of_intake_id": intake_id,
                },
            }
        ],
    )
    work_order = work_order_manager.get_work_order(work_order_id)
    approval_id = ""
    if work_order.get("approvals"):
        approval_id = str(work_order["approvals"][-1].get("approval_id") or "")
    intake_record = message_intake_ledger.record_retry_result(
        intake_id=intake_id,
        status="retry_created",
        reply_summary=_snapshot_summary(reply_candidate, 120),
        approval_id=approval_id,
        generation_error="",
        risk_flags=risk_flags,
    )
    return {
        "ok": True,
        "type": "customer_message_intake_retry",
        "intake_record": intake_record,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
        "reply_candidate": reply_candidate,
        "risk_flags": risk_flags,
        "needs_manual_reply": False,
    }


@app.post("/workflows/messages/inbound")
async def inbound_message_endpoint(req: InboundMessageRequest) -> Dict[str, Any]:
    """Create a draft reply approval from an inbound buyer message."""
    result = _create_inbound_message_work_order(req)
    approval_required = result.get("approval_required", False)
    return {**result, "approval_required": approval_required}


@app.get("/message-intake/recent")
def message_intake_recent(limit: int = 20) -> List[Dict[str, Any]]:
    return message_intake_ledger.list_recent(limit=limit)


@app.get("/message-intake/metrics")
def message_intake_metrics() -> Dict[str, Any]:
    return message_intake_ledger.metrics()


@app.get("/message-intake/health")
def message_intake_health(
    window_minutes: float = 60,
    failure_threshold: int = 3,
    duplicate_rate_threshold: float = 0.5,
    duplicate_min_events: int = 5,
    stale_after_minutes: float = 30,
) -> Dict[str, Any]:
    return message_intake_ledger.health(
        window_seconds=max(0, float(window_minutes) * 60),
        failure_threshold=failure_threshold,
        duplicate_rate_threshold=duplicate_rate_threshold,
        duplicate_min_events=duplicate_min_events,
        stale_after_seconds=max(0, float(stale_after_minutes) * 60),
    )


@app.post("/message-intake/retention/prune")
async def message_intake_retention_prune(req: MessageIntakeRetentionRequest) -> Dict[str, Any]:
    return message_intake_ledger.prune(
        max_age_seconds=max(0, float(req.retention_days) * 86400),
        max_records=max(0, int(req.max_records)),
        dry_run=req.dry_run,
    )


@app.get("/message-intake/runbooks")
def message_intake_runbooks(alert_code: str = "") -> Dict[str, Any]:
    codes = [part.strip() for part in str(alert_code or "").split(",") if part.strip()]
    return {"ok": True, "runbooks": get_message_intake_runbooks(codes or None)}


@app.get("/message-intake/incident-bundle")
def message_intake_incident_bundle(
    limit: int = 20,
    window_minutes: float = 60,
    failure_threshold: int = 3,
    duplicate_rate_threshold: float = 0.5,
    duplicate_min_events: int = 5,
    stale_after_minutes: float = 30,
) -> Dict[str, Any]:
    return message_intake_ledger.incident_bundle(
        limit=limit,
        health_options={
            "window_seconds": max(0, float(window_minutes) * 60),
            "failure_threshold": failure_threshold,
            "duplicate_rate_threshold": duplicate_rate_threshold,
            "duplicate_min_events": duplicate_min_events,
            "stale_after_seconds": max(0, float(stale_after_minutes) * 60),
        },
    )


@app.post("/message-intake/health-check/dry-run")
async def message_intake_health_check_dry_run(req: MessageIntakeHealthCheckRequest) -> Dict[str, Any]:
    return message_intake_ledger.dry_run_health_check(
        window_seconds=max(0, float(req.window_minutes) * 60),
        failure_threshold=req.failure_threshold,
        duplicate_rate_threshold=req.duplicate_rate_threshold,
        duplicate_min_events=req.duplicate_min_events,
        stale_after_seconds=max(0, float(req.stale_after_minutes) * 60),
        notification_hooks=req.notification_hooks,
    )


@app.post("/message-intake/{intake_id}/retry")
async def message_intake_retry(intake_id: str) -> Dict[str, Any]:
    result = _retry_message_intake_record(intake_id)
    return {**result, "approval_required": result.get("approval_required", False)}


@app.post("/workflows/items/{item_id}/delete")
async def delete_item_endpoint(item_id: str, req: DeleteItemRequest) -> Dict[str, Any]:
    """Create a delete listing work order with approval gate (does not delete directly)."""
    item_id = item_id.strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id is required")

    work_order = work_order_manager.create_work_order(
        kind="delete_item",
        title=f"Delete listing {item_id}",
        owner="listing",
        context={
            "item_id": item_id,
            "item_title": req.item_title,
            "reason": req.reason,
            "cookie_id": req.cookie_id,
            **req.context,
        },
        risk_level="high",
    )

    plan = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[
            {
                "agent": "listing",
                "action": "delete_item",
                "description": f"Delete listing {item_id}",
                "params": {
                    "item_id": item_id,
                    "reason": req.reason,
                    "cookie_id": req.cookie_id,
                },
                "draft_snapshot": {
                    "item_id": item_id,
                    "item_title": _snapshot_summary(req.item_title, 80),
                    "cookie_id": req.cookie_id,
                    "reason_summary": _snapshot_summary(req.reason, 120),
                    "approval_note": "Deletion is destructive and requires explicit approval.",
                },
            },
        ],
    )
    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
    }


@app.post("/workflows/refunds/{order_id}/refund")
async def refund_order_endpoint(order_id: str, req: RefundOrderRequest) -> Dict[str, Any]:
    """Create a refund work order with approval gate (does not refund directly)."""
    order_id = order_id.strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id is required")
    if req.amount_cny is not None and req.amount_cny < 0:
        raise HTTPException(status_code=400, detail="amount_cny must be non-negative")

    work_order = work_order_manager.create_work_order(
        kind="refund_order",
        title=f"Refund order {order_id}",
        owner="order",
        context={
            "order_id": order_id,
            "buyer_id": req.buyer_id,
            "amount_cny": req.amount_cny,
            "reason": req.reason,
            "cookie_id": req.cookie_id,
            **req.context,
        },
        risk_level="critical",
    )

    plan = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[
            {
                "agent": "order",
                "action": "refund_order",
                "description": f"Refund order {order_id}",
                "params": {
                    "order_id": order_id,
                    "amount_cny": req.amount_cny,
                    "reason": req.reason,
                    "buyer_id": req.buyer_id,
                    "cookie_id": req.cookie_id,
                },
                "draft_snapshot": {
                    "order_id": order_id,
                    "amount_cny": req.amount_cny,
                    "buyer_id": req.buyer_id,
                    "cookie_id": req.cookie_id,
                    "reason_summary": _snapshot_summary(req.reason, 120),
                    "approval_note": "Refund is a financial action and requires explicit approval.",
                },
            },
        ],
    )
    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
    }


@app.post("/workflows/disputes/{dispute_id}/reply")
async def dispute_reply_endpoint(dispute_id: str, req: DisputeReplyRequest) -> Dict[str, Any]:
    """Create a dispute reply draft with approval gate (does not send directly)."""
    dispute_id = dispute_id.strip()
    if not dispute_id:
        raise HTTPException(status_code=400, detail="dispute_id is required")
    if not req.buyer_id.strip() or not req.message.strip():
        raise HTTPException(status_code=400, detail="buyer_id and message are required")

    risk_flags = _detect_risk_flags(req.message)
    work_order = work_order_manager.create_work_order(
        kind="dispute_reply",
        title=f"Dispute reply {dispute_id}",
        owner="customer_service",
        context={
            "dispute_id": dispute_id,
            "order_id": req.order_id,
            "buyer_id": req.buyer_id,
            "message": req.message,
            "cookie_id": req.cookie_id,
            **req.context,
        },
        risk_level="critical" if risk_flags else "high",
    )

    plan = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[
            {
                "agent": "customer_service",
                "action": "sensitive_reply",
                "description": f"Send dispute reply {dispute_id}",
                "params": {
                    "dispute_id": dispute_id,
                    "order_id": req.order_id,
                    "buyer_id": req.buyer_id,
                    "message": req.message,
                    "cookie_id": req.cookie_id,
                    "chat_id": req.chat_id,
                },
                "draft_snapshot": {
                    "dispute_id": dispute_id,
                    "order_id": req.order_id,
                    "buyer_id": req.buyer_id,
                    "chat_id": req.chat_id,
                    "cookie_id": req.cookie_id,
                    "message_summary": _snapshot_summary(req.message, 80),
                    "risk_flags": risk_flags,
                    "approval_note": "Dispute replies are sensitive and require explicit approval.",
                },
            },
        ],
    )
    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
        "risk_flags": risk_flags,
    }


# ============================================================
# Resale Opportunity Endpoints
# ============================================================

@app.post("/resale/candidates")
async def create_resale_candidate(request: Request) -> Dict[str, Any]:
    """Create a new resale candidate and evaluate its economics/risk."""
    payload = await request.json()
    if not payload.get("title"):
        raise HTTPException(status_code=400, detail="title is required")
    if not payload.get("source_platform"):
        raise HTTPException(status_code=400, detail="source_platform is required (pdd/1688)")
    if not payload.get("supplier_price") and payload.get("supplier_price") != 0:
        raise HTTPException(status_code=400, detail="supplier_price is required")
    candidate = resale_manager.create_candidate(payload)
    return {"ok": True, "candidate": candidate}


@app.get("/resale/candidates")
async def list_resale_candidates(
    status: Optional[str] = None,
    source_platform: Optional[str] = None,
    market_scope: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List resale candidates with optional filters."""
    return resale_manager.list_candidates(
        status=status,
        source_platform=source_platform,
        market_scope=market_scope,
    )


@app.get("/resale/candidates/{candidate_id}")
async def get_resale_candidate(candidate_id: str) -> Dict[str, Any]:
    """Get a single resale candidate by ID."""
    candidate = resale_manager.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@app.post("/resale/candidates/{candidate_id}/plan")
async def create_resale_plan(candidate_id: str, request: Request) -> Dict[str, Any]:
    """Create a resale plan for a candidate."""
    overrides = await request.json() if await request.body() else {}
    plan = resale_manager.create_plan(candidate_id, overrides)
    if not plan:
        candidate = resale_manager.get_candidate(candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        raise HTTPException(status_code=400, detail="Cannot create plan for blocked candidate")
    return {"ok": True, "plan": plan}


class PurchaseDraftRequest(BaseModel):
    quantity: int = 1
    buyer_address_summary: str = ""
    buyer_address_full: str = ""


@app.post("/resale/plans/{plan_id}/purchase-draft")
async def create_purchase_draft_endpoint(plan_id: str, req: PurchaseDraftRequest) -> Dict[str, Any]:
    """Create a purchase draft through WorkOrder + Approval flow.

    Does NOT auto-pay or auto-submit. Returns a WorkOrder with pending approval.
    """
    plan = resale_manager.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    candidate = resale_manager.get_candidate(plan.get("candidate_id", ""))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found for plan")

    draft = resale_manager.create_purchase_draft(
        plan_id=plan_id,
        quantity=req.quantity,
        buyer_address_summary=req.buyer_address_summary,
        buyer_address_full=req.buyer_address_full,
    )
    if not draft:
        raise HTTPException(status_code=400, detail="Failed to create purchase draft")

    # Pass agent review discussion to WorkOrder if available
    agent_discussion = candidate.get("agent_reviews")

    # Create WorkOrder with high risk level
    title = f"采购草稿: {candidate.get('title', '')} x{req.quantity}"
    work_order = work_order_manager.create_work_order(
        kind="resale_purchase",
        title=title,
        owner="resale",
        context={
            "plan_id": plan_id,
            "candidate_id": candidate.get("candidate_id"),
            "draft_id": draft.get("draft_id"),
            "supplier_url": candidate.get("source_url", ""),
            "total_cost": draft.get("total_cost", 0),
        },
        risk_level="high",
    )

    # Create action plan requiring approval
    plan_result = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        discussion=agent_discussion,
        proposed_actions=[{
            "agent": "resale",
            "action": "prepare_purchase_draft",
            "description": f"生成采购草稿: {candidate.get('title', '')} x{req.quantity}",
            "params": {
                "plan_id": plan_id,
                "draft_id": draft.get("draft_id"),
                "quantity": req.quantity,
                "buyer_address_summary": req.buyer_address_summary,
            },
            "draft_snapshot": {
                "supplier_url": candidate.get("source_url", ""),
                "product_specs": candidate.get("specifications", {}),
                "quantity": req.quantity,
                "total_cost": draft.get("total_cost", 0),
                "profit_review": draft.get("profit_review", {}),
                "risk_warnings": draft.get("risk_warnings", []),
            },
            "requires_approval": True,
        }],
    )

    # Link draft to work order
    resale_manager.update_purchase_draft(draft["draft_id"], {
        "work_order_id": work_order["work_order_id"],
    })

    # Update plan status
    resale_manager.update_plan(plan_id, {
        "status": "pending_approval",
        "work_order_id": work_order["work_order_id"],
    })

    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "purchase_draft": draft,
        "work_order": work_order,
        "approval_required": True,
        "message": "采购草稿已创建，需人工审批后才能继续。MVP 不会自动付款或提交供应商订单。",
    }


class VerifyRequest(BaseModel):
    supplier_verified: bool = False
    supplier_verification_note: str = ""
    competitor_links: list = []
    competitor_price_min: Optional[float] = None
    competitor_price_max: Optional[float] = None
    estimated_demand_signal: str = ""


@app.post("/resale/candidates/{candidate_id}/verify")
async def verify_resale_candidate(candidate_id: str, req: VerifyRequest) -> Dict[str, Any]:
    """Record supplier verification results for a candidate."""
    result = resale_manager.verify_candidate(candidate_id, req.model_dump())
    if not result:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return {"ok": True, "candidate": result}


class DecisionRequest(BaseModel):
    decision: str
    note: str = ""
    override_reason: str = ""


@app.post("/resale/candidates/{candidate_id}/decision")
async def set_resale_decision(candidate_id: str, req: DecisionRequest) -> Dict[str, Any]:
    """Set manual decision (watch/approve/reject) for a candidate."""
    result = resale_manager.set_decision(candidate_id, req.decision, req.note, req.override_reason)
    if not result:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"ok": True, "candidate": result}


@app.post("/resale/candidates/{candidate_id}/agent-review")
async def agent_review_resale_candidate(candidate_id: str) -> Dict[str, Any]:
    """Trigger multi-agent discussion and structured review for a resale candidate."""
    candidate = resale_manager.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    discussion = registry.discuss(
        topic=f"倒卖候选评审: {candidate.get('title', '')}",
        participants=["resale_research", "resale_risk", "resale_pricing"],
        rounds=2,
        context={"candidate": candidate},
    )
    # Structured reviews from each agent
    structured_reviews = []
    for agent_name in ["resale_research", "resale_risk", "resale_pricing"]:
        agent = registry._agents.get(agent_name)
        if agent and hasattr(agent, "review_candidate"):
            structured_reviews.append(agent.review_candidate(candidate))
    updated = resale_manager.save_agent_review(candidate_id, discussion, structured_reviews)
    aggregate = resale_manager.aggregate_agent_review(candidate_id) or {}
    return {"ok": True, "candidate": updated, "discussion": discussion, "aggregate": aggregate}


@app.get("/resale/dashboard/summary")
async def get_resale_dashboard_summary() -> Dict[str, Any]:
    """Get aggregated resale dashboard summary."""
    return resale_manager.get_dashboard_summary()


@app.post("/resale/candidates/{candidate_id}/listing-draft")
async def create_listing_draft_endpoint(candidate_id: str) -> Dict[str, Any]:
    """Create a listing draft from an approved+verified candidate.

    Does NOT auto-publish. Creates WorkOrder with publish_listing approval.
    """
    candidate = resale_manager.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    draft = resale_manager.create_listing_draft(candidate_id)
    if not draft:
        raise HTTPException(status_code=400, detail="Failed to create listing draft")
    if "error" in draft:
        raise HTTPException(status_code=400, detail=draft["error"])

    # Pass agent review discussion to WorkOrder if available
    agent_discussion = candidate.get("agent_reviews")

    # Create WorkOrder
    work_order = work_order_manager.create_work_order(
        kind="resale_listing",
        title=f"上架闲鱼: {candidate.get('title', '')}",
        owner="resale",
        context={
            "candidate_id": candidate_id,
            "draft_id": draft.get("draft_id"),
            "title": draft.get("title"),
            "price": draft.get("price"),
        },
        risk_level="normal",
    )

    # Create action plan with publish_listing
    work_order_manager.create_action_plan(
        work_order["work_order_id"],
        discussion=agent_discussion,
        proposed_actions=[{
            "agent": "resale",
            "action": "publish_listing",
            "description": f"上架闲鱼: {candidate.get('title', '')}",
            "params": {
                "draft_id": draft.get("draft_id"),
                "candidate_id": candidate_id,
            },
            "draft_snapshot": {
                "title": draft.get("title"),
                "price": draft.get("price"),
                "stock": draft.get("stock"),
                "description": draft.get("description", "")[:200],
                "delivery_type": draft.get("delivery_type"),
                "category": draft.get("category"),
                "candidate_id": candidate_id,
                "gross_margin": draft.get("gross_margin"),
                "supplier_verified": True,
            },
            "requires_approval": True,
        }],
    )

    # Link draft to work order
    resale_manager.update_listing_draft(draft["draft_id"], {
        "work_order_id": work_order["work_order_id"],
    })

    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    return {
        "ok": True,
        "listing_draft": draft,
        "work_order": work_order,
        "approval_required": True,
        "message": "上架草稿已创建，需审批后才能发布。不会自动发布。",
    }


# ============================================================
# Purchase Task Endpoints
# ============================================================

class MarkOrderedRequest(BaseModel):
    supplier_order_id: str = ""
    tracking_number: str = ""
    manual_order_note: str = ""


@app.get("/resale/purchase-tasks")
async def list_purchase_tasks(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List purchase tasks, optionally filtered by status."""
    return resale_manager.list_purchase_tasks(status=status)


@app.get("/resale/purchase-tasks/{purchase_task_id}")
async def get_purchase_task(purchase_task_id: str) -> Dict[str, Any]:
    """Get a single purchase task."""
    task = resale_manager.get_purchase_task(purchase_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Purchase task not found")
    return task


@app.post("/resale/purchase-tasks/{purchase_task_id}/mark-ordered")
async def mark_purchase_task_ordered(purchase_task_id: str, request: MarkOrderedRequest) -> Dict[str, Any]:
    """Mark a purchase task as ordered (manual supplier order completed)."""
    task = resale_manager.get_purchase_task(purchase_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Purchase task not found")
    if task["status"] != "pending_manual_order":
        raise HTTPException(status_code=400, detail=f"Cannot mark ordered: status is '{task['status']}'")
    updated = resale_manager.update_purchase_task(purchase_task_id, {
        "status": "ordered",
        "supplier_order_id": request.supplier_order_id,
        "tracking_number": request.tracking_number,
        "manual_order_note": request.manual_order_note,
        "ordered_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    })
    return {"ok": True, "purchase_task": updated}


@app.post("/resale/purchase-tasks/{purchase_task_id}/cancel")
async def cancel_purchase_task(purchase_task_id: str) -> Dict[str, Any]:
    """Cancel a pending purchase task."""
    task = resale_manager.get_purchase_task(purchase_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Purchase task not found")
    if task["status"] != "pending_manual_order":
        raise HTTPException(status_code=400, detail=f"Cannot cancel: status is '{task['status']}'")
    updated = resale_manager.update_purchase_task(purchase_task_id, {"status": "cancelled"})
    return {"ok": True, "purchase_task": updated}


class CreateOrderFromTaskRequest(BaseModel):
    xianyu_order_id: str = ""
    sale_amount: float = 0
    platform_fee: float = 0
    shipping_cost: float = 0
    buyer_note: str = ""
    supplier_note: str = ""
    aftersale_note: str = ""
    allow_duplicate: bool = False


@app.post("/resale/purchase-tasks/{purchase_task_id}/create-order")
async def create_order_from_task(purchase_task_id: str, request: CreateOrderFromTaskRequest) -> Dict[str, Any]:
    """Create a ResaleOrder from a PurchaseTask with auto-filled fields."""
    result = resale_manager.create_order_from_task(
        purchase_task_id,
        overrides=request.model_dump(exclude={"allow_duplicate"}),
        allow_duplicate=request.allow_duplicate,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Purchase task not found")
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return {"ok": True, "resale_order": result}


# ============================================================
# Resale Order Endpoints
# ============================================================

class CreateResaleOrderRequest(BaseModel):
    xianyu_order_id: str = ""
    purchase_task_id: str = ""
    purchase_draft_id: str = ""
    plan_id: str = ""
    candidate_id: str = ""
    listing_draft_id: str = ""
    supplier_order_id: str = ""
    supplier_tracking_number: str = ""
    xianyu_tracking_number: str = ""
    sale_amount: float = 0
    supplier_cost: float = 0
    shipping_cost: float = 0
    platform_fee: float = 0
    status: str = "pending_purchase"
    buyer_note: str = ""
    supplier_note: str = ""
    aftersale_note: str = ""


class UpdateResaleOrderRequest(BaseModel):
    status: Optional[str] = None
    xianyu_order_id: Optional[str] = None
    supplier_order_id: Optional[str] = None
    supplier_tracking_number: Optional[str] = None
    xianyu_tracking_number: Optional[str] = None
    sale_amount: Optional[float] = None
    supplier_cost: Optional[float] = None
    shipping_cost: Optional[float] = None
    platform_fee: Optional[float] = None
    buyer_note: Optional[str] = None
    supplier_note: Optional[str] = None
    aftersale_note: Optional[str] = None


@app.post("/resale/orders")
async def create_resale_order(request: CreateResaleOrderRequest) -> Dict[str, Any]:
    """Create a resale order for reconciliation."""
    order = resale_manager.create_resale_order(request.model_dump())
    return {"ok": True, "resale_order": order}


@app.get("/resale/orders")
async def list_resale_orders(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List resale orders, optionally filtered by status."""
    return resale_manager.list_resale_orders(status=status)


@app.get("/resale/orders/dashboard")
async def get_resale_orders_dashboard() -> Dict[str, Any]:
    """Get resale orders dashboard summary."""
    return resale_manager.get_orders_dashboard()


@app.get("/resale/orders/{resale_order_id}")
async def get_resale_order(resale_order_id: str) -> Dict[str, Any]:
    """Get a single resale order."""
    order = resale_manager.get_resale_order(resale_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Resale order not found")
    return order


@app.post("/resale/orders/{resale_order_id}/status")
async def update_resale_order_status(resale_order_id: str, request: UpdateResaleOrderRequest) -> Dict[str, Any]:
    """Update resale order status and fields."""
    order = resale_manager.get_resale_order(resale_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Resale order not found")
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    if "status" in updates and updates["status"] not in ResaleManager.VALID_ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {updates['status']}")
    updated = resale_manager.update_resale_order(resale_order_id, updates)
    return {"ok": True, "resale_order": updated}


@app.get("/resale/orders/{resale_order_id}/detail")
async def get_resale_order_detail(resale_order_id: str) -> Dict[str, Any]:
    """Get full chain detail for a resale order."""
    detail = resale_manager.get_order_detail(resale_order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Resale order not found")
    return detail


# ============================================================
# Browser Adapter Endpoints
# ============================================================

@app.post("/browser/login-cookies")
async def browser_login_cookies(request: Request) -> Dict[str, Any]:
    """Login using cookies."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active. Set XIANYU_ADAPTER=browser"}
    payload = await request.json()
    return await adapter.login_with_cookies(
        cookie_string=payload.get("cookies", ""),
        cookie_id=payload.get("cookie_id", "default"),
    )

@app.post("/browser/publish")
async def browser_publish(request: Request) -> Dict[str, Any]:
    """Publish a listing directly through the browser adapter."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active. Set XIANYU_ADAPTER=browser"}

    payload = await request.json()
    draft_payload = payload.get("draft") if isinstance(payload.get("draft"), dict) else payload
    title = str(draft_payload.get("title") or draft_payload.get("text") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title or text is required")

    raw_price = draft_payload.get("price")
    try:
        price = float(raw_price) if raw_price not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="price must be a number") from exc

    raw_stock = draft_payload.get("stock")
    try:
        stock = int(raw_stock) if raw_stock not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="stock must be an integer") from exc

    images = draft_payload.get("selected_images") or draft_payload.get("images") or []
    draft = ListingDraft(
        title=title,
        category=str(draft_payload.get("category") or ""),
        price=price,
        stock=stock,
        condition=str(draft_payload.get("condition") or "new"),
        location=str(draft_payload.get("location") or ""),
        description=str(draft_payload.get("description") or ""),
        tags=list(draft_payload.get("tags") or []),
        selected_images=list(images),
        delivery_type=str(draft_payload.get("delivery_type") or "manual"),
        delivery_payload=str(draft_payload.get("delivery_payload") or ""),
        source="browser-api",
        raw_text=str(draft_payload.get("raw_text") or draft_payload.get("text") or ""),
    )
    workflow.storage.save_draft(draft)

    if payload.get("publish") is False:
        return {"ok": True, "draft": draft.to_dict(), "publish_result": None}

    result = await adapter.publish_listing(draft, cookie_id=str(payload.get("cookie_id") or "default"))
    if result.ok:
        workflow.storage.save_publish_result(result, draft)
    workflow.storage.append_event(
        "browser.publish",
        {"draft": draft.to_dict(), "publish_result": result.to_dict()},
    )
    return {"ok": result.ok, "draft": draft.to_dict(), "publish_result": result.to_dict()}

@app.get("/browser/publish/confirm")
async def browser_publish_confirm(cookie_id: str = "default") -> Dict[str, Any]:
    """Confirm a browser publish after manual verification or delayed navigation."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active. Set XIANYU_ADAPTER=browser"}
    result = await adapter.confirm_current_publish(cookie_id=str(cookie_id or "default"))
    workflow.storage.append_event("browser.publish.confirm", result)
    return result

@app.get("/browser/status")
async def browser_status(cookie_id: str = "default") -> Dict[str, Any]:
    """Check browser login status."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active. Set XIANYU_ADAPTER=browser"}
    return await adapter.check_login_status(cookie_id)

@app.get("/browser/login-state")
async def browser_login_state(cookie_id: str = "default") -> Dict[str, Any]:
    """Get saved login state."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    return adapter.get_login_state(cookie_id)

@app.get("/browser/orders")
async def browser_orders(cookie_id: str = "default", status: str = "") -> Dict[str, Any]:
    """Get orders via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    orders = await adapter.get_orders(cookie_id, status)
    return {"ok": True, "count": len(orders), "orders": orders}

@app.get("/browser/messages")
async def browser_messages(cookie_id: str = "default", buyer_id: str = "") -> Dict[str, Any]:
    """Get messages via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    messages = await adapter.get_messages(buyer_id, cookie_id)
    return {"ok": True, "count": len(messages), "messages": messages}

@app.get("/browser/conversations")
async def browser_conversations(cookie_id: str = "default") -> Dict[str, Any]:
    """Get conversation list via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    convos = await adapter.get_conversations(cookie_id)
    return {"ok": True, "count": len(convos), "conversations": convos}

@app.post("/browser/send-message")
async def browser_send_message(request: Request) -> Dict[str, Any]:
    """Send message to buyer via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    payload = await request.json()
    return await adapter.send_message(
        buyer_id=payload.get("buyer_id", ""),
        message=payload.get("message", ""),
        cookie_id=payload.get("cookie_id", "default"),
    )

@app.get("/browser/my-listings")
async def browser_my_listings(cookie_id: str = "default") -> Dict[str, Any]:
    """Get my published listings via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    items = await adapter.get_my_listings(cookie_id)
    return {"ok": True, "count": len(items), "items": items}

@app.get("/browser/item/{item_id}")
async def browser_item_detail(item_id: str, cookie_id: str = "default") -> Dict[str, Any]:
    """Get item detail via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    detail = await adapter.get_item_detail(item_id, cookie_id)
    if detail:
        return {"ok": True, "item": detail}
    return {"ok": False, "error": "Item not found"}

@app.post("/browser/search")
async def browser_search(request: Request) -> Dict[str, Any]:
    """Search products via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    payload = await request.json()
    items = await adapter.search_products(
        keyword=payload.get("keyword", ""),
        max_pages=payload.get("max_pages", 1),
        min_price=payload.get("min_price", 0),
        max_price=payload.get("max_price", 0),
        cookie_id=payload.get("cookie_id", "default"),
    )
    return {"ok": True, "count": len(items), "items": items}

@app.delete("/browser/item/{item_id}")
async def browser_delete_item(item_id: str, cookie_id: str = "default") -> Dict[str, Any]:
    """Delete a listing via browser."""
    from .browser_adapter import BrowserAdapter
    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    return await adapter.delete_item(item_id, cookie_id)


# ---------------------------------------------------------------------------
# OPC Dashboard (read-only)
# ---------------------------------------------------------------------------

def _opc_dashboard() -> OPCDashboard:
    """Build OPCDashboard with available data sources."""
    return OPCDashboard(
        resale_manager=resale_manager,
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
    )


@app.get("/dashboard/opc-daily")
async def get_opc_daily_dashboard() -> Dict[str, Any]:
    """Aggregate OPC daily dashboard. Read-only, no side effects."""
    return _opc_dashboard().get_opc_daily()


@app.post("/dashboard/weekly-review")
async def post_weekly_review() -> Dict[str, Any]:
    """Generate and save a weekly review Markdown snapshot. Local file only."""
    reviews_dir = settings.data_dir / "reviews"
    return _opc_dashboard().generate_weekly_review(reviews_dir)


# ---------------------------------------------------------------------------
# Trial Run (Phase 7)
# ---------------------------------------------------------------------------

from .trial_run import TrialRun, save_trial_state

_trial: Optional[TrialRun] = None


@app.get("/trial-run/status")
async def get_trial_run_status() -> Dict[str, Any]:
    """Get current trial run progress. Read-only."""
    global _trial
    if _trial is None:
        # Try to load from disk
        from .trial_run import load_trial_state
        state_path = settings.data_dir / "trial_run_state.json"
        state = load_trial_state(state_path)
        if state:
            _trial = TrialRun(
                category=state.get("category", "商用厨具"),
                target_candidates=state.get("target_candidates", 5),
                target_approvals=state.get("target_approvals", 1),
                monitor_days=state.get("monitor_days", 14),
            )
            _trial.steps = state.get("steps", _trial.steps)
        else:
            _trial = TrialRun()
            save_trial_state(settings.data_dir / "trial_run_state.json", _trial)
    return _trial.get_progress()


@app.post("/trial-run/init")
async def init_trial_run(
    category: str = "商用厨具",
    target_candidates: int = 5,
    target_approvals: int = 1,
    monitor_days: int = 14,
) -> Dict[str, Any]:
    """Initialize a new trial run. Creates plan only, no execution."""
    global _trial
    _trial = TrialRun(
        category=category,
        target_candidates=target_candidates,
        target_approvals=target_approvals,
        monitor_days=monitor_days,
    )
    save_trial_state(settings.data_dir / "trial_run_state.json", _trial)
    return {"ok": True, "trial": _trial.get_progress()}


@app.post("/trial-run/complete-step/{step}")
async def complete_trial_step(step: int, output: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Mark a trial run step as completed. Does not execute the step."""
    global _trial
    if _trial is None:
        raise HTTPException(status_code=400, detail="Trial run not initialized")
    _trial.complete_step(step, output)
    save_trial_state(settings.data_dir / "trial_run_state.json", _trial)
    return {"ok": True, "trial": _trial.get_progress()}


@app.post("/trial-run/report")
async def generate_trial_report() -> Dict[str, Any]:
    """Generate trial run report. Read-only aggregation."""
    global _trial
    if _trial is None:
        raise HTTPException(status_code=400, detail="Trial run not initialized")

    candidates = resale_manager.list_candidates() if resale_manager else []
    orders = resale_manager.list_resale_orders() if resale_manager and hasattr(resale_manager, "list_resale_orders") else []
    reviews_dir = settings.data_dir / "reviews"
    return _trial.generate_report(candidates, orders, reviews_dir)
