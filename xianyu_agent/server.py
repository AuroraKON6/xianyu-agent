from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .config import Settings
from .db_manager import configure_db_manager, db_manager
from .feishu import extract_feishu_text, extract_xianyu_inbound_message, verify_feishu_token
from .scheduler import github_discovery_loop, product_discovery_loop
from .workflow import CommerceWorkflow
from .llm_manager import llm_manager
from .agent_registry import registry
from .agents import create_all_agents
from .agents.base import BaseAgent
from .agent_scheduler import start_agent_scheduler
from .account_attribution import (
    repair_work_order_account_attribution,
    resolve_account_cookie_id,
    review_unassigned_work,
)
from .account_profiles import build_account_profiles
from .account_ops_queues import build_account_ops_queues
from .account_prompt_profiles import build_bootstrap_ai_reply_settings, build_prompt_profile
from .account_runtime import AccountRuntimeStore
from .account_commercial_terms import (
    AccountCommercialTermsStore,
    evaluate_commercial_terms,
)
from .conversation_profiles import summarize_conversation
from .discovery_inbox import DiscoveryInboxStore
from .models import ListingDraft
from .pi_runtime import PiRpcClient, PiRuntimeError
from .work_orders import work_order_manager
from .action_executor import action_executor
from .bridge_events import bridge_events
from .chat_events import OUTBOUND_SEND_ACTIONS, chat_event_store
from .commerce import JsonCommerceRepositories
from .opportunity_service import OpportunityError, OpportunityService
from .product_matching import MatchError, ProductMatchingService
from .supplier_import import (
    ConfirmMismatch,
    ImportPreviewError,
    SupplierImportService,
)
from .price_commitments import (
    extract_price_mentions,
    price_commitment_store,
    product_facts_store,
)
from .execution_queue import execution_queue
from .first_run_readiness import build_first_run_readiness, RUNTIME_HEARTBEAT_MAX_AGE_SECONDS
from .message_intake import get_message_intake_runbooks, message_intake_ledger
from .message_envelope import normalize_message_envelope, to_inbound_request_payload, with_ack_status
from .message_import_scheduler import MessageImportSchedulerStore
from .reply_outbox import (
    build_reply_metadata,
    find_reply_by_idempotency_key,
    list_reply_outbox,
    outbox_item_for_approval,
    summarize_reply_outbox,
    validate_reply_idempotency_match,
)
from .opc_dashboard import OPCDashboard
from .order_runtime import OrderRuntimeStore
from .candidate_activation import evaluate_candidate_activation
from .candidate_evidence_store import (
    CandidateEvidenceConflict,
    CandidateEvidenceError,
    CandidateEvidenceIntegrityError,
    EVIDENCE_KINDS,
    MAX_EVIDENCE_BYTES,
)
from .commercial_preflight import run_commercial_preflight
from .commercial_launch import build_commercial_launch_preview, day_zero_controls
from .commercial_trial import (
    CommercialTrialStore,
    reconcile_trial,
    render_trial_markdown,
    validate_controls,
)
from .resale import ResaleManager, init_resale_manager, resale_manager
from .sync_persistence import sync_account_snapshot
from .action_scope import ActionScopeValidationError, listing_draft_revision

settings = Settings.from_env()
# Bind the SQLite singleton to the explicitly configured live data boundary
# before anything touches the database. Import-time lazy resolution already
# avoids default-path creation; this call makes the composition root the
# single authority for the path.
configure_db_manager(settings.db_path)
workflow = CommerceWorkflow(settings)
if hasattr(workflow.adapter, "set_cookie_provider"):
    workflow.adapter.set_cookie_provider(db_manager.get_cookie)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Xianyu Pi Commerce Agent", version="0.7.0")

# Initialize multi-agent system with real workflow
llm_manager.init(settings)
BaseAgent.set_workflow(workflow)
registry.configure_persistence(settings.data_dir / "agent_collaboration.jsonl")
work_order_manager.configure(settings.data_dir / "work_orders.json")
execution_queue.configure(settings.data_dir / "execution_jobs.json")
message_intake_ledger.configure(settings.data_dir / "message_intake.json")
chat_event_store.configure(settings.data_dir / "chat_events.json")
price_commitment_store.configure(settings.data_dir / "price_commitments.json")
commerce_repositories = JsonCommerceRepositories(settings.data_dir)
supplier_import_service = SupplierImportService(commerce_repositories, settings.data_dir)
product_matching_service = ProductMatchingService(commerce_repositories)
opportunity_service = OpportunityService(commerce_repositories)
from .providers import ProviderCapabilityRegistry  # noqa: E402
from .providers.supplier_file import SupplierFileProvider  # noqa: E402
from .agent_jobs import AgentJobError, AgentJobStore, FencingError  # noqa: E402
from .budget_reservations import BudgetError, BudgetReservationStore, ShadowAuthorizationLog  # noqa: E402
from .reply_drafts import DraftReplyService  # noqa: E402
from .pi_jobs import (  # noqa: E402
    JOB_TOKEN_HEADER,
    JobPolicyError,
    PiTaskRunner,
    ReadOnlyJobGate,
)
from .shadow_observer import ShadowObserver, start_background_observer  # noqa: E402
from .operating_mandates import (  # noqa: E402
    ActionRequest,
    MandateError,
    OperatingMandateStore,
    evaluate_action,
    today_key,
)

provider_capability_registry = ProviderCapabilityRegistry(settings.data_dir)
provider_capability_registry.seed_defaults()
supplier_file_provider = SupplierFileProvider(commerce_repositories, supplier_import_service)
agent_job_store = AgentJobStore(settings.data_dir)
operating_mandate_store = OperatingMandateStore(settings.data_dir)
budget_reservation_store = BudgetReservationStore(settings.data_dir)
shadow_authorization_log = ShadowAuthorizationLog(settings.data_dir)
reply_draft_service = DraftReplyService(settings.data_dir, product_facts_store, price_commitment_store)
pi_job_gate = ReadOnlyJobGate()
pi_task_runner = PiTaskRunner(
    agent_job_store,
    commerce_repositories,
    data_dir=settings.data_dir,
    import_service=supplier_import_service,
    gate=pi_job_gate,
    per_job_token_budget=int(os.environ.get("PI_JOB_TOKEN_BUDGET", "50000")),
    daily_token_budget=int(os.environ.get("PI_DAILY_TOKEN_BUDGET", "200000")),
)
pi_task_runner.start()
shadow_observer = ShadowObserver(
    settings.data_dir,
    draft_service=reply_draft_service,
    opportunities=commerce_repositories.opportunities,
    mandates=operating_mandate_store,
    shadow_log=shadow_authorization_log,
    budget_store=budget_reservation_store,
)
shadow_observer_thread = start_background_observer(
    shadow_observer,
    interval_seconds=float(os.environ.get("SHADOW_OBSERVER_INTERVAL_SECONDS", "60")),
    enabled=os.environ.get("SHADOW_OBSERVER_ENABLED", "1") not in ("0", "false", "False"),
)


@app.middleware("http")
async def pi_job_readonly_gate(request, call_next):
    """Program-enforced read-only policy for job Pi sessions.

    Requests carrying a job token are restricted to GET on the
    read-only allowlist and capped in count; violations get 403 with
    the policy reason. Operator sessions (no token) are untouched.
    """
    token = request.headers.get(JOB_TOKEN_HEADER)
    if not token:
        return await call_next(request)
    from fastapi.responses import JSONResponse

    try:
        pi_job_gate.authorize(token, method=request.method, path=request.url.path)
    except JobPolicyError as exc:
        return JSONResponse(status_code=403, content={"detail": f"job tool policy: {exc}"})
    return await call_next(request)
product_facts_store.configure(settings.data_dir / "product_facts.json")
message_import_scheduler_store = MessageImportSchedulerStore(settings.data_dir / "message_import_scheduler.json")
bridge_events.configure(settings.data_dir / "bridge_events.json")
account_runtime_store = AccountRuntimeStore(settings.data_dir / "account_runtime_state.json")
account_commercial_terms_store = AccountCommercialTermsStore(
    settings.data_dir / "account_commercial_terms.json"
)
order_runtime_store = OrderRuntimeStore(settings.data_dir / "order_runtime_state.json")
discovery_inbox_store = DiscoveryInboxStore(settings.data_dir / "discovery_inbox.json")
commercial_trial_store = CommercialTrialStore(settings.data_dir / "commercial_trials.json")
commercial_trial_action_lock = RLock()
account_attribution_action_lock = RLock()
browser_account_import_lock = RLock()
candidate_evidence_action_lock = RLock()
browser_account_import_commits: Dict[str, Dict[str, Any]] = {}
browser_account_import_inflight: set[str] = set()
resale_manager = init_resale_manager(settings.data_dir)
BaseAgent.set_registry(registry)
BaseAgent.set_work_order_manager(work_order_manager)
agents = create_all_agents()
for name, agent in agents.items():
    registry.register_agent(name, agent)
    for action, handler in agent.get_handlers().items():
        registry.register_handler(name, action, handler)
pi_runtime = PiRpcClient.from_env(PROJECT_ROOT, settings.data_dir)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{settings.server_port}",
        f"http://localhost:{settings.server_port}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
WEB_DIR = PROJECT_ROOT / "web"


@app.on_event("startup")
async def on_startup() -> None:
    # Bind and open SQLite at the configured live boundary before any request
    # or scheduler loop can touch the database.
    db_manager.init_db()
    execution_queue.reconcile_expired_leases(work_order_manager)
    # Read-only/background discovery loops. Platform writes are queue-only.
    asyncio.create_task(github_discovery_loop(workflow))
    asyncio.create_task(product_discovery_loop(workflow))
    # Agent scheduler loops
    asyncio.create_task(start_agent_scheduler(agents, settings))
    asyncio.create_task(message_import_scheduler_loop())


@app.on_event("shutdown")
def on_shutdown() -> None:
    pi_runtime.stop()


@app.get("/health")
def health() -> Dict[str, Any]:
    memory = workflow.load_memory()
    return {
        "ok": True,
        "orchestrator": "pi",
        "adapter": workflow.adapter.name,
        "model": pi_runtime.config.model,
        "deepseek_configured": bool(settings.deepseek_api_key),
        "memory_updated_at": memory.get("updated_at", ""),
        "pi": pi_runtime.status(active=False),
        "legacy_openclaw_available": False,
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


class PiMessageRequest(BaseModel):
    text: str = ""
    message: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    publish: bool = False


def _run_pi_message(req: PiMessageRequest) -> Dict[str, Any]:
    text = str(req.text or req.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        return pi_runtime.prompt(text, context=req.context, publish=req.publish)
    except PiRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/agent/message")
def agent_message(req: PiMessageRequest) -> Dict[str, Any]:
    """Primary operator entrypoint backed by the project-local Pi runtime."""
    return _run_pi_message(req)


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
    draft_result = workflow.create_listing(text, source="api", product_images=images, publish=False)
    if not publish:
        return {**draft_result, "approval_required": False, "executed": False}
    cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
    return _create_publish_listing_work_order(
        draft_result.get("draft") or {},
        cookie_id=cookie_id,
        kind="listing",
    )


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
    order_id = str(payload.get("order_id") or payload.get("id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id is required")
    cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
    return await fulfill_order_endpoint(
        order_id,
        FulfillOrderRequest(
            delivery_type=str(payload.get("delivery_type") or "digital"),
            delivery_payload=str(payload.get("delivery_payload") or ""),
            cookie_id=cookie_id or "default",
            context={"source": "orders_paid_compatibility"},
        ),
    )


@app.post("/tasks/polish")
def polish() -> Dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="Direct polish is retired; use an approved execution action.",
    )


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
        publish = bool(payload.get("publish", True))
        draft_result = workflow.create_listing_from_opportunity(opportunity_id, publish=False)
        if not publish:
            return {**draft_result, "approval_required": False, "executed": False}
        cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
        return _create_publish_listing_work_order(
            draft_result.get("draft") or {},
            cookie_id=cookie_id,
            kind="opportunity_listing",
        )
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


class DiscoveryInboxCreateRequest(BaseModel):
    source: str = "manual"
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    title: str = ""
    summary: str = ""
    category_hint: str = ""
    supplier_hint: str = ""
    price_hint: float = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DiscoveryInboxRejectRequest(BaseModel):
    reason: str = ""


class DiscoveryInboxPromoteRequest(BaseModel):
    source_platform: str = ""
    source_url: str = ""
    supplier_price: Optional[float] = None
    supplier_shipping: Optional[float] = None
    target_sale_price: Optional[float] = None
    category: str = ""
    market_scope: str = "domestic"
    sales_platform: str = "xianyu"
    platform_fee_rate: Optional[float] = None
    expected_refund_rate: Optional[float] = None
    notes: str = ""
    specifications: Dict[str, Any] = Field(default_factory=dict)


@app.post("/discovery/inbox")
def create_discovery_inbox_event(req: DiscoveryInboxCreateRequest) -> Dict[str, Any]:
    event = discovery_inbox_store.add_event(
        source=req.source,
        raw_payload=req.raw_payload,
        title=req.title,
        summary=req.summary,
        category_hint=req.category_hint,
        supplier_hint=req.supplier_hint,
        price_hint=req.price_hint,
        metadata=req.metadata,
    )
    return {"ok": True, "event": event}


@app.get("/discovery/inbox")
def list_discovery_inbox(status: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    return {
        "events": discovery_inbox_store.list_events(status=status, limit=limit),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.post("/discovery/inbox/{event_id}/mark-read")
def mark_discovery_inbox_event_read(event_id: str) -> Dict[str, Any]:
    try:
        event = discovery_inbox_store.mark_read(event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Discovery inbox event not found") from exc
    return {"ok": True, "event": event}


@app.post("/discovery/inbox/{event_id}/reject")
def reject_discovery_inbox_event(event_id: str, req: DiscoveryInboxRejectRequest) -> Dict[str, Any]:
    try:
        event = discovery_inbox_store.reject_event(event_id, reason=req.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Discovery inbox event not found") from exc
    return {"ok": True, "event": event}


@app.post("/discovery/inbox/{event_id}/promote-candidate")
def promote_discovery_inbox_event(event_id: str, req: DiscoveryInboxPromoteRequest) -> Dict[str, Any]:
    overrides = {
        key: value
        for key, value in req.model_dump().items()
        if value not in (None, "", {})
    }
    try:
        return discovery_inbox_store.promote_to_candidate(
            event_id,
            resale_manager=resale_manager,
            overrides=overrides,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Discovery inbox event not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    publish = bool(payload.get("publish", True))
    draft_result = workflow.create_physical_listing(text, source="api", publish=False)
    if not publish:
        return {**draft_result, "approval_required": False, "executed": False}
    cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
    return _create_publish_listing_work_order(
        draft_result.get("draft") or {},
        cookie_id=cookie_id,
        kind="physical_listing",
    )


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


class BrowserAccountImportCommitRequest(BaseModel):
    preview_token: str = Field(min_length=8, max_length=128)
    confirmation: str = Field(min_length=4, max_length=64)
    remark: str = Field(default="", max_length=160)


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


class OrderRuntimeUpsertRequest(BaseModel):
    order_id: str
    cookie_id: str = ""
    status: str = "pending_payment"
    item_id: str = ""
    buyer_id: str = ""
    amount: float = 0
    source: str = "manual"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrderRuntimeStatusRequest(BaseModel):
    status: str
    context: str = ""
    operator_action: str = ""
    blocker: str = ""


class OrderRuntimeLockRequest(BaseModel):
    owner: str = "operator"
    reason: str = ""


class AccountCommercialTermsRequest(BaseModel):
    category: str = Field(min_length=1, max_length=120)
    candidate_id: str = Field(default="", max_length=120)
    seller_type: str = Field(default="unknown", max_length=40)
    account_owner_confirmed: bool = False
    seller_agreement_confirmed: bool = False
    payment_service_ready: bool = False
    category_publishable: bool = False
    return_policy: str = Field(default="unknown", max_length=40)
    return_exception_basis: str = Field(default="", max_length=300)
    return_address_ready: bool = False
    after_sales_ready: bool = False
    guarantee_required: bool = False
    guarantee_ready: bool = False
    guarantee_amount: float = Field(default=0, ge=0)
    platform_fee_rate: float = Field(ge=0, lt=1)
    platform_fixed_fee: float = Field(default=0, ge=0)
    observed_at: str = Field(min_length=10, max_length=80)
    evidence_note: str = Field(min_length=4, max_length=500)
    evidence_ref: str = Field(min_length=1, max_length=300)


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


@app.get("/accounts/profiles")
def list_account_profiles() -> List[Dict[str, Any]]:
    """List account capability profiles without exposing cookie values or API keys."""
    return build_account_profiles(db_manager)


@app.get("/accounts/runtime")
def list_account_runtime() -> Dict[str, Any]:
    """List sanitized account runtime states without exposing cookies or raw chat ids."""
    profiles = build_account_profiles(db_manager)
    cookie_ids = [str(profile.get("cookie_id") or "") for profile in profiles if profile.get("cookie_id")]
    states = account_runtime_store.list_public_states(cookie_ids)
    return {
        "accounts": [states[cookie_id] for cookie_id in cookie_ids],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _active_browser_adapter() -> Any:
    from .browser_adapter import BrowserAdapter

    adapter = workflow.adapter
    return adapter if isinstance(adapter, BrowserAdapter) else None


async def _reset_browser_adapter_session() -> None:
    adapter = _active_browser_adapter()
    if adapter is not None:
        await adapter.close()


def _raise_browser_account_import_error(result: Mapping[str, Any]) -> None:
    detail = {
        "error": str(result.get("error_code") or "browser_account_import_failed"),
        "message": str(result.get("error") or "Browser account import failed.")[:240],
    }
    missing = [str(item) for item in result.get("missing_cookie_names") or [] if str(item)]
    if missing:
        detail["missing_cookie_names"] = missing
    raise HTTPException(status_code=409, detail=detail)


@app.post("/accounts/browser-import/open")
def open_browser_account_import() -> Dict[str, Any]:
    """Open the dedicated login browser and return no page or credential data."""
    try:
        return workflow.browser_open_account_login()
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "browser_login_open_failed",
                "message": str(exc)[:240],
            },
        ) from exc


@app.post("/accounts/browser-import/preview")
async def preview_browser_account_import() -> Dict[str, Any]:
    """Verify and identify the current browser account without returning cookies."""
    adapter = _active_browser_adapter()
    if adapter is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "browser_adapter_inactive",
                "message": "Set XIANYU_ADAPTER=browser before importing a browser account.",
            },
        )
    result = await adapter.preview_current_account_import()
    if not result.get("ok"):
        _raise_browser_account_import_error(result)
    account_id = str(result.get("account_id") or "").strip()
    return {
        "ok": True,
        "preview_token": result["preview_token"],
        "account_id": account_id,
        "display_name": str(result.get("display_name") or "")[:80],
        "existing_account": _account_exists(account_id),
        "operation": "refresh" if _account_exists(account_id) else "create",
        "cookie_count": int(result.get("cookie_count") or 0),
        "required_cookie_names": list(result.get("required_cookie_names") or []),
        "session_ref": str(result.get("session_ref") or "")[:12],
        "probe": str(result.get("probe") or ""),
        "observed_at": float(result.get("observed_at") or 0),
        "expires_at": float(result.get("expires_at") or 0),
        "confirmation_phrase": str(result.get("confirmation_phrase") or ""),
    }


@app.post("/accounts/browser-import/commit")
async def commit_browser_account_import(req: BrowserAccountImportCommitRequest) -> Dict[str, Any]:
    """Reverify the preview, then create or refresh exactly one account."""
    preview_token = req.preview_token.strip()
    expected_confirmation = f"BIND {preview_token[:8].upper()}"
    if req.confirmation.strip() != expected_confirmation:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "account_import_confirmation_mismatch",
                "expected_confirmation": expected_confirmation,
            },
        )

    now = time.time()
    with browser_account_import_lock:
        expired = [
            token
            for token, item in browser_account_import_commits.items()
            if float(item.get("cache_expires_at") or 0) < now
        ]
        for token in expired:
            browser_account_import_commits.pop(token, None)
        cached = browser_account_import_commits.get(preview_token)
        if cached:
            return {key: value for key, value in cached.items() if key != "cache_expires_at"}
        if preview_token in browser_account_import_inflight:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "account_import_commit_in_progress",
                    "message": "This account import is already being committed.",
                },
            )
        browser_account_import_inflight.add(preview_token)

    try:
        adapter = _active_browser_adapter()
        if adapter is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "browser_adapter_inactive",
                    "message": "Set XIANYU_ADAPTER=browser before importing a browser account.",
                },
            )
        captured = await adapter.confirm_current_account_import(preview_token)
        if not captured.get("ok"):
            _raise_browser_account_import_error(captured)

        account_id = str(captured.get("account_id") or "").strip()
        cookie_value = str(captured.get("cookie_string") or "").strip()
        if not account_id or not cookie_value:
            _raise_browser_account_import_error(
                {
                    "error_code": "browser_account_evidence_incomplete",
                    "error": "The verified browser account did not provide complete credentials.",
                }
            )

        existing = db_manager.get_cookie_details(account_id) or {}
        operation = "refresh" if existing else "create"
        clean_remark = req.remark.strip()
        if existing:
            db_manager.update_cookie_value(account_id, cookie_value)
            if clean_remark:
                db_manager.update_cookie_remark(account_id, clean_remark)
            enabled = db_manager.get_all_cookie_status().get(account_id, True)
            remark = clean_remark or str(existing.get("remark") or "")
        else:
            remark = clean_remark or str(captured.get("display_name") or "")[:80]
            db_manager.save_cookie(account_id, cookie_value, remark)
            db_manager.save_cookie_status(account_id, True)
            enabled = True

        observed_at = float(captured.get("observed_at") or now)
        account_runtime_store.record_authenticated_activity(account_id, timestamp=observed_at)
        runtime = account_runtime_store.record_cookie_refresh(account_id, timestamp=observed_at)
        await adapter.close()

        result = {
            "ok": True,
            "operation": operation,
            "cookie_id": account_id,
            "display_name": str(captured.get("display_name") or "")[:80],
            "remark": remark,
            "enabled": bool(enabled),
            "cookie_count": int(captured.get("cookie_count") or 0),
            "session_ref": str(captured.get("session_ref") or "")[:12],
            "probe": str(captured.get("probe") or ""),
            "runtime": runtime,
            "next_steps": [
                "bootstrap_prompt_profile",
                "sync_orders_and_listings",
                "record_account_commercial_terms",
            ],
            "imported_at": now,
        }
        with browser_account_import_lock:
            browser_account_import_commits[preview_token] = {
                **result,
                "cache_expires_at": now + 300,
            }
        return result
    finally:
        with browser_account_import_lock:
            browser_account_import_inflight.discard(preview_token)


def _account_exists(cookie_id: str) -> bool:
    return str(cookie_id or "").strip() in {
        str(account_id or "").strip()
        for account_id in db_manager.get_all_cookies().keys()
    }


def _candidate_is_xianyu_domestic(candidate: Mapping[str, Any]) -> bool:
    return bool(
        str(candidate.get("sales_platform") or "xianyu").strip().lower() == "xianyu"
        and str(candidate.get("market_scope") or "domestic").strip().lower() == "domestic"
    )


def _commercial_terms_assessment(
    *,
    cookie_id: str,
    category: str,
    candidate: Optional[Mapping[str, Any]] = None,
    checked_at: Optional[str] = None,
) -> Dict[str, Any]:
    clean_cookie_id = str(cookie_id or "").strip()
    clean_category = str(category or "").strip()
    snapshot = None
    if clean_cookie_id and clean_category:
        snapshot = account_commercial_terms_store.get_snapshot(clean_cookie_id, clean_category)
    return evaluate_commercial_terms(
        snapshot=snapshot,
        cookie_id=clean_cookie_id,
        category=clean_category,
        candidate=candidate,
        checked_at=checked_at,
    )


@app.get("/accounts/{cookie_id}/commercial-terms")
def get_account_commercial_terms(
    cookie_id: str,
    category: str,
    candidate_id: str = "",
) -> Dict[str, Any]:
    """Return current sanitized commercial terms evidence and readiness."""
    if not _account_exists(cookie_id):
        raise HTTPException(status_code=404, detail="Account not found")
    candidate = None
    if candidate_id:
        candidate = resale_manager.get_candidate(candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if not _candidate_is_xianyu_domestic(candidate):
            raise HTTPException(status_code=409, detail="Candidate is not a domestic Xianyu candidate")
        if str(candidate.get("category") or "").strip().casefold() != str(category or "").strip().casefold():
            raise HTTPException(status_code=409, detail="Candidate category does not match commercial terms")
    assessment = _commercial_terms_assessment(
        cookie_id=cookie_id,
        category=category,
        candidate=candidate,
    )
    return {
        "ok": True,
        "snapshot": assessment.get("snapshot"),
        "assessment": assessment,
    }


@app.post("/accounts/{cookie_id}/commercial-terms")
def save_account_commercial_terms(
    cookie_id: str,
    req: AccountCommercialTermsRequest,
) -> Dict[str, Any]:
    """Persist current account/category terms and reprice an optional candidate."""
    if not _account_exists(cookie_id):
        raise HTTPException(status_code=404, detail="Account not found")
    candidate_id = str(req.candidate_id or "").strip()
    candidate = resale_manager.get_candidate(candidate_id) if candidate_id else None
    if candidate_id and not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if candidate is not None and not _candidate_is_xianyu_domestic(candidate):
        raise HTTPException(status_code=409, detail="Candidate is not a domestic Xianyu candidate")
    if candidate is not None and (
        str(candidate.get("category") or "").strip().casefold()
        != str(req.category or "").strip().casefold()
    ):
        raise HTTPException(status_code=409, detail="Candidate category does not match commercial terms")

    if hasattr(req, "model_dump"):
        payload = req.model_dump()
    else:
        payload = req.dict()
    payload.pop("candidate_id", None)
    payload.update({"cookie_id": cookie_id, "category": req.category})
    try:
        snapshot = account_commercial_terms_store.save_snapshot(payload)
        if candidate is not None:
            candidate = resale_manager.update_candidate_economics_from_commercial_terms(
                candidate_id,
                cookie_id=cookie_id,
                category=snapshot["category"],
                platform_fee_rate=snapshot["platform_fee_rate"],
                platform_fixed_fee=snapshot["platform_fixed_fee"],
                snapshot_version=snapshot["snapshot_version"],
                observed_at=snapshot["observed_at"],
                evidence_ref=snapshot["evidence_ref"],
                return_policy=snapshot["return_policy"],
                return_exception_basis=snapshot["return_exception_basis"],
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    assessment = evaluate_commercial_terms(
        snapshot=snapshot,
        cookie_id=cookie_id,
        category=req.category,
        candidate=candidate,
    )
    return {
        "ok": True,
        "snapshot": snapshot,
        "assessment": assessment,
        "candidate": candidate,
    }


@app.get("/accounts/{cookie_id}/prompt-profile")
def get_account_prompt_profile(cookie_id: str) -> Dict[str, Any]:
    """Return AI prompt readiness for one account without prompt bodies or secrets."""
    if not db_manager.get_cookie(cookie_id):
        raise HTTPException(status_code=404, detail="Account not found")
    profile = build_prompt_profile(db_manager.get_ai_reply_settings(cookie_id) or {})
    return {"cookie_id": cookie_id, **profile}


@app.post("/accounts/{cookie_id}/prompt-profile/bootstrap")
def bootstrap_account_prompt_profile(cookie_id: str) -> Dict[str, Any]:
    """Initialize required AI prompt keys for one account and return a sanitized profile."""
    if not db_manager.get_cookie(cookie_id):
        raise HTTPException(status_code=404, detail="Account not found")
    settings = build_bootstrap_ai_reply_settings(db_manager.get_ai_reply_settings(cookie_id) or {})
    db_manager.save_ai_reply_settings(cookie_id, settings)
    profile = build_prompt_profile(db_manager.get_ai_reply_settings(cookie_id) or settings)
    return {"cookie_id": cookie_id, **profile}


@app.get("/accounts/{cookie_id}/conversation-summary")
def get_account_conversation_summary(
    cookie_id: str,
    chat_id: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Return a redacted summary of one account conversation."""
    if not db_manager.get_cookie(cookie_id):
        raise HTTPException(status_code=404, detail="Account not found")
    safe_limit = max(1, min(int(limit or 20), 100))
    history = db_manager.get_conversation_history(cookie_id, chat_id, safe_limit)
    summary = summarize_conversation(history)
    return {"cookie_id": cookie_id, **summary}


@app.post("/accounts")
async def add_account(req: CookieCreateRequest) -> Dict[str, Any]:
    """Add a new cookie account."""
    cookie_id = req.cookie_id.strip()
    cookie_value = req.cookie_value.strip()
    remark = req.remark.strip()
    if not cookie_id:
        raise HTTPException(status_code=400, detail="cookie_id is required")
    if not cookie_value:
        raise HTTPException(status_code=400, detail="cookie_value is required")
    db_manager.save_cookie(cookie_id, cookie_value, remark)
    db_manager.save_cookie_status(cookie_id, True)
    await _reset_browser_adapter_session()
    return {"ok": True, "cookie_id": cookie_id, "enabled": True, "remark": remark}


@app.put("/accounts/{cookie_id}")
async def update_account(cookie_id: str, req: CookieUpdateRequest) -> Dict[str, Any]:
    """Update cookie value for an account."""
    existing = db_manager.get_cookie(cookie_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Account not found")
    cookie_value = req.cookie_value.strip()
    if not cookie_value:
        raise HTTPException(status_code=400, detail="cookie_value is required")
    db_manager.update_cookie_value(cookie_id, cookie_value)
    await _reset_browser_adapter_session()
    return {"ok": True}


@app.delete("/accounts/{cookie_id}")
async def delete_account(cookie_id: str) -> Dict[str, Any]:
    """Delete a cookie account."""
    existing = db_manager.get_cookie(cookie_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Account not found")
    db_manager.delete_cookie(cookie_id)
    await _reset_browser_adapter_session()
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


@app.get("/orders/runtime")
def list_order_runtime(status: Optional[str] = None, cookie_id: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    """List local order operating state for the one-person-company dashboard."""
    return {
        "orders": order_runtime_store.list_orders(status=status, cookie_id=cookie_id, limit=limit),
        "summary": order_runtime_store.summary(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.post("/orders/runtime")
def upsert_order_runtime(req: OrderRuntimeUpsertRequest) -> Dict[str, Any]:
    """Create or update local runtime state for an order."""
    try:
        order = order_runtime_store.upsert_order(**req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "order": order}


@app.post("/orders/runtime/{order_id}/status")
def update_order_runtime_status(order_id: str, req: OrderRuntimeStatusRequest) -> Dict[str, Any]:
    """Update local order runtime status and operator guidance."""
    try:
        order = order_runtime_store.update_status(
            order_id,
            req.status,
            context=req.context,
            operator_action=req.operator_action,
            blocker=req.blocker,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "order": order}


@app.post("/orders/runtime/{order_id}/fulfillment-lock")
def acquire_order_fulfillment_lock(order_id: str, req: OrderRuntimeLockRequest) -> Dict[str, Any]:
    """Acquire an idempotency lock before doing fulfillment work."""
    try:
        order = order_runtime_store.acquire_fulfillment_lock(order_id, owner=req.owner, reason=req.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "order": order}


@app.delete("/orders/runtime/{order_id}/fulfillment-lock")
def release_order_fulfillment_lock(order_id: str, owner: str = "") -> Dict[str, Any]:
    """Release a local fulfillment idempotency lock."""
    try:
        order = order_runtime_store.release_fulfillment_lock(order_id, owner=owner)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "order": order}


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
    publish: bool = False


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
    cookie_id: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)


def _resolve_operator_cookie_id(requested: str = "", context: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the account a new operator task should belong to."""
    return resolve_account_cookie_id(
        account_ids=db_manager.get_all_cookies().keys(),
        requested=requested,
        context=context,
    )


def _with_operator_cookie_context(context: Optional[Dict[str, Any]], requested: str = "") -> Dict[str, Any]:
    merged = dict(context or {})
    cookie_id = _resolve_operator_cookie_id(requested=requested, context=merged)
    if cookie_id:
        merged["cookie_id"] = cookie_id
    return merged


def _resolve_enabled_operator_cookie_id(requested: str = "") -> str:
    """Resolve one enabled account for a mutating workflow or reject ambiguity."""
    configured_accounts = {
        str(account_id or "").strip()
        for account_id in db_manager.get_all_cookies().keys()
        if str(account_id or "").strip()
    }
    requested = str(requested or "").strip()
    if requested == "default" and "default" not in configured_accounts:
        requested = ""
    cookie_id = _resolve_operator_cookie_id(requested=requested)
    if len(configured_accounts) > 1 and not cookie_id:
        raise HTTPException(status_code=409, detail="cookie_id is required when multiple seller accounts are configured")
    if cookie_id and cookie_id not in configured_accounts:
        raise HTTPException(status_code=404, detail="Seller account not found")
    if cookie_id and not db_manager.get_all_cookie_status().get(cookie_id, True):
        raise HTTPException(status_code=409, detail="Selected seller account is disabled")
    return cookie_id


def _create_publish_listing_work_order(
    draft: Dict[str, Any],
    *,
    cookie_id: str = "",
    kind: str = "listing",
) -> Dict[str, Any]:
    """Convert a persisted listing draft into the one approved execution path."""
    draft_id = str(draft.get("id") or draft.get("draft_id") or "").strip()
    if not draft_id:
        raise HTTPException(status_code=400, detail="draft_id is required")
    title = str(draft.get("title") or "Untitled listing").strip()
    context = {
        "draft_id": draft_id,
        "title": title,
        "price": draft.get("price"),
        **({"cookie_id": cookie_id} if cookie_id else {}),
    }
    work_order = work_order_manager.create_work_order(
        kind=kind,
        title=f"Publish listing: {title}",
        owner="listing",
        context=context,
        risk_level="normal",
    )
    plan = work_order_manager.create_action_plan(
        work_order["work_order_id"],
        proposed_actions=[
            {
                "agent": "listing",
                "action": "publish_listing",
                "description": f"Publish listing: {title}",
                "params": {
                    "draft_id": draft_id,
                    "expected_draft_revision": listing_draft_revision(draft),
                    **({"cookie_id": cookie_id} if cookie_id else {}),
                },
                "draft_snapshot": {
                    "title": title,
                    "price": draft.get("price"),
                    "stock": draft.get("stock"),
                    "description": str(draft.get("description") or "")[:200],
                    "delivery_type": draft.get("delivery_type"),
                    "category": draft.get("category"),
                    "image_count": len(draft.get("selected_images") or draft.get("images") or []),
                    **({"cookie_id": cookie_id} if cookie_id else {}),
                },
                "requires_approval": True,
            }
        ],
    )
    return {
        "ok": True,
        "draft": draft,
        "work_order": work_order_manager.get_work_order(work_order["work_order_id"]),
        "action_plan": plan,
        "approval_required": True,
        "executed": False,
    }


def _find_cookie_id(value: Any) -> str:
    if isinstance(value, dict):
        direct = value.get("cookie_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for child in value.values():
            found = _find_cookie_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_cookie_id(child)
            if found:
                return found
    return ""


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
        "entrypoint": "pi",
        "legacy_entrypoint": None,
        "legacy_routes": "retired",
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


@app.get("/agents/pi/status")
def pi_status(active: bool = False) -> Dict[str, Any]:
    """Inspect Pi configuration, optionally starting it for an RPC handshake."""
    return pi_runtime.status(active=active)


@app.post("/agents/pi/message")
def pi_message(req: PiMessageRequest) -> Dict[str, Any]:
    """Send an operator turn through the sole primary Pi orchestrator."""
    return _run_pi_message(req)


@app.post("/agents/dispatch")
async def dispatch_agent_task(req: AgentTaskRequest) -> Dict[str, Any]:
    """Retired because arbitrary dispatch could bypass typed business flows."""
    raise HTTPException(status_code=410, detail="Generic agent dispatch is retired; use typed Pi tools and business APIs.")


@app.post("/agents/openclaw/message")
async def openclaw_message(req: OpenClawMessageRequest) -> Dict[str, Any]:
    """Deprecated compatibility route for the former OpenClaw orchestrator."""
    raise HTTPException(status_code=410, detail="OpenClaw is retired; use /agents/pi/message.")


@app.post("/agents/hermes/message")
async def hermes_message(req: OpenClawMessageRequest) -> Dict[str, Any]:
    """Compatibility alias for older clients; routes through OpenClaw."""
    raise HTTPException(status_code=410, detail="Hermes/OpenClaw is retired; use /agents/pi/message.")


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
    """Create a draft and, when requested, an approval-backed publish task."""
    payload = await request.json()
    text = str(payload.get("text") or payload.get("raw_text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    publish = bool(payload.get("publish", True))
    draft_result = workflow.create_listing(
        text,
        source="legacy_agent_api",
        product_images=payload.get("images") or [],
        publish=False,
    )
    if not publish:
        return {**draft_result, "approval_required": False, "executed": False}
    cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
    return _create_publish_listing_work_order(
        draft_result.get("draft") or {},
        cookie_id=cookie_id,
        kind="listing",
    )


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
    context = _with_operator_cookie_context(req.context)
    return work_order_manager.create_work_order(
        kind=req.kind.strip(),
        title=req.title.strip(),
        owner=req.owner.strip() or "openclaw",
        context=context,
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
    cookie_id = _resolve_operator_cookie_id(requested=req.cookie_id, context=req.context)
    ecommerce_context = dict(req.context or {})

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
            "cookie_id": cookie_id,
            "ecommerce_context": ecommerce_context,
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
        "cookie_id": cookie_id,
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
                    "cookie_id": cookie_id,
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
    except ActionScopeValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
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
    except ActionScopeValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ============================================================
# Execution Queue Endpoints
# ============================================================

class ExecutionVerificationRequest(BaseModel):
    resolution: str
    note: str
    external_id: str = ""
    page_url: str = ""
    screenshot: str = ""
    checked_by: str = "operator"

@app.get("/execution-jobs")
def list_execution_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    """List execution jobs."""
    execution_queue.reconcile_expired_leases(work_order_manager)
    return execution_queue.list_jobs(limit)


@app.get("/execution-jobs/{job_id}")
def get_execution_job(job_id: str) -> Dict[str, Any]:
    """Get a single execution job."""
    try:
        return execution_queue.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _record_outbound_chat_events(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Append one outbound chat fact per finished reply-send execution job.

    A confirmed send also commits the reply's lowest explicit price as the
    seller floor for that chat; unverified outcomes commit nothing.
    """
    recorded: List[Dict[str, Any]] = []
    for result in results or []:
        job_id = str((result or {}).get("job_id") or "")
        status = str((result or {}).get("status") or "")
        if not job_id or status not in {"completed", "verification_required"}:
            continue
        try:
            job = execution_queue.get_job(job_id)
        except KeyError:
            continue
        if str(job.get("action") or "") not in OUTBOUND_SEND_ACTIONS:
            continue
        scope = job.get("scope_envelope") if isinstance(job.get("scope_envelope"), dict) else {}
        reply_text = ""
        source_event_key = ""
        source_message_id = ""
        proposal_id = ""
        approval_id = str(job.get("approval_id") or "")
        for work_order in work_order_manager.list_work_orders(limit=1000):
            matched = False
            for approval in work_order.get("approvals", []):
                if str(approval.get("approval_id") or "") != approval_id:
                    continue
                payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
                reply_text = str(payload.get("message") or "")
                source_event_key = str(payload.get("reply_source_event_key") or "")
                source_message_id = str(payload.get("reply_source_message_id") or "")
                proposal_id = str(payload.get("reply_idempotency_key") or "")
                matched = True
                break
            if matched:
                break
        try:
            record = chat_event_store.append_outbound(
                execution_job_id=job_id,
                cookie_id=str(scope.get("cookie_id") or ""),
                chat_id=str(scope.get("chat_id") or ""),
                buyer_id=str(scope.get("buyer_id") or ""),
                item_id=str(scope.get("item_id") or ""),
                message_text=reply_text,
                reply_proposal_id=proposal_id,
                source_event_key=source_event_key,
                source_message_id=source_message_id,
                send_outcome=(
                    "verification_required" if status == "verification_required" else "sent"
                ),
            )
            entry = {
                "job_id": job_id,
                "chat_event_id": record["event"]["event_id"],
                "chat_event_status": record["status"],
            }
            # A price only becomes a seller commitment once the platform send
            # is confirmed; verification_required outcomes commit nothing.
            if status == "completed" and reply_text:
                mentions = extract_price_mentions(reply_text)
                if mentions:
                    floor = min(mention["amount"] for mention in mentions)
                    floor_mention = next(
                        mention for mention in mentions if mention["amount"] == floor
                    )
                    commitment = price_commitment_store.record_seller_commit(
                        cookie_id=str(scope.get("cookie_id") or ""),
                        chat_id=str(scope.get("chat_id") or ""),
                        item_id=str(scope.get("item_id") or ""),
                        amount=floor,
                        source=f"execution_job:{job_id}",
                        excerpt=floor_mention["excerpt"],
                    )
                    entry["seller_commit"] = {
                        "amount": floor,
                        "status": commitment["status"],
                    }
            recorded.append(entry)
        except ValueError as exc:
            recorded.append({"job_id": job_id, "chat_event_error": str(exc)})
    return recorded


@app.post("/execution-jobs/run-next")
async def run_next_execution_jobs() -> Dict[str, Any]:
    """Execute the next batch of queued jobs."""
    results = execution_queue.run_next(
        action_executor,
        work_order_manager,
        {
            "data_dir": settings.data_dir,
            "workflow": workflow,
            "commercial_trial_store": commercial_trial_store,
        },
    )
    chat_events_recorded = _record_outbound_chat_events(results)
    return {
        "ok": True,
        "executed": len(results),
        "results": results,
        "chat_events_recorded": chat_events_recorded,
    }


@app.post("/execution-jobs/run-pending")
async def run_pending_execution_jobs() -> Dict[str, Any]:
    """Execute all currently queued jobs that are ready to run."""
    results = execution_queue.run_pending(
        action_executor,
        work_order_manager,
        {
            "data_dir": settings.data_dir,
            "workflow": workflow,
            "commercial_trial_store": commercial_trial_store,
        },
    )
    chat_events_recorded = _record_outbound_chat_events(results)
    return {
        "ok": True,
        "executed": len(results),
        "results": results,
        "chat_events_recorded": chat_events_recorded,
    }


@app.get("/chat-events")
def list_chat_events(
    cookie_id: str = "",
    chat_id: str = "",
    item_id: str = "",
    direction: str = "",
    limit: int = 50,
    before_sequence: Optional[int] = None,
) -> Dict[str, Any]:
    """Read the append-only chat fact stream (paginated by sequence)."""
    events = chat_event_store.list_events(
        cookie_id=cookie_id,
        chat_id=chat_id,
        item_id=item_id,
        direction=direction,
        limit=limit,
        before_sequence=before_sequence,
    )
    chain = chat_event_store.verify_chain()
    return {"ok": bool(chain.get("ok")), "events": events, "chain": chain}


@app.get("/chat-events/metrics")
def chat_events_metrics() -> Dict[str, Any]:
    """Chat stream counts and hash-chain verification state."""
    return chat_event_store.metrics()


@app.get("/price-commitments")
def list_price_commitments(
    cookie_id: str = "",
    chat_id: str = "",
    item_id: str = "",
    limit: int = 100,
) -> Dict[str, Any]:
    """Read the monotonic bargaining memory for account + chat + item scopes."""
    return {
        "ok": True,
        "records": price_commitment_store.list_records(
            cookie_id=cookie_id, chat_id=chat_id, item_id=item_id, limit=limit
        ),
        "metrics": price_commitment_store.metrics(),
    }


class ProductFactsRequest(BaseModel):
    cookie_id: str
    item_id: str
    list_price: Optional[float] = None
    stock_available: Optional[int] = None
    ships_in_days: Optional[int] = None
    condition: str = ""
    notes: str = ""


@app.get("/product-facts")
def list_product_facts(
    cookie_id: str = "", item_id: str = "", limit: int = 100
) -> Dict[str, Any]:
    """Read authoritative listing facts used for offer plausibility and drafting."""
    return {
        "ok": True,
        "facts": product_facts_store.list_facts(
            cookie_id=cookie_id, item_id=item_id, limit=limit
        ),
    }


@app.put("/product-facts")
def upsert_product_facts(req: ProductFactsRequest) -> Dict[str, Any]:
    """Register or revise authoritative listing facts (revision history kept)."""
    if not req.cookie_id.strip() or not req.item_id.strip():
        raise HTTPException(status_code=400, detail="cookie_id and item_id are required")
    try:
        facts = product_facts_store.upsert(
            cookie_id=req.cookie_id.strip(),
            item_id=req.item_id.strip(),
            list_price=req.list_price,
            stock_available=req.stock_available,
            ships_in_days=req.ships_in_days,
            condition=req.condition,
            notes=req.notes,
            source="operator_api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "facts": facts}


@app.get("/reply-trace")
def reply_trace(cookie_id: str = "", chat_id: str = "") -> Dict[str, Any]:
    """Read-only replay of one chat's message, price, and facts timeline.

    The spine is the append-only chat event stream; each outbound event is
    joined with its Reply Outbox status, and price commitment history and
    product fact revisions merge into the same time-ordered view.
    """
    clean_cookie = cookie_id.strip()
    clean_chat = chat_id.strip()
    if not clean_cookie or not clean_chat:
        raise HTTPException(
            status_code=400, detail="cookie_id and chat_id are required"
        )
    events = chat_event_store.list_events(
        cookie_id=clean_cookie, chat_id=clean_chat, limit=500
    )
    outbox_items = [
        item
        for item in list_reply_outbox(
            work_order_manager=work_order_manager,
            execution_queue=execution_queue,
            message_intake_ledger=message_intake_ledger,
            limit=1000,
        )
        if str(item.get("cookie_id") or "") == clean_cookie
        and str(item.get("chat_id") or "") == clean_chat
    ]
    outbox_by_job = {
        str(item.get("execution_job_id") or ""): item
        for item in outbox_items
        if item.get("execution_job_id")
    }
    price_records = price_commitment_store.list_records(
        cookie_id=clean_cookie, chat_id=clean_chat, limit=50
    )
    facts_items = product_facts_store.list_facts(cookie_id=clean_cookie, limit=100)

    timeline: List[Dict[str, Any]] = []
    for event in events:
        job_id = str(event.get("execution_job_id") or "")
        outbox_item = outbox_by_job.get(job_id)
        timeline.append(
            {
                "ts": event.get("recorded_at"),
                "kind": f"chat.{event.get('direction')}",
                "source_message_id": str(event.get("source_message_id") or ""),
                "message_excerpt": str(event.get("message_text") or "")[:80],
                "execution_job_id": job_id,
                "send_outcome": event.get("send_outcome"),
                "reply_status": str((outbox_item or {}).get("status") or ""),
                "verification_required": (
                    (outbox_item or {}).get("status") == "verification_required"
                ),
            }
        )
    for record in price_records:
        for entry in record.get("history", []):
            timeline.append(
                {
                    "ts": entry.get("recorded_at"),
                    "kind": f"price.{entry.get('actor')}",
                    "item_id": str(record.get("item_id") or ""),
                    "amount": entry.get("amount"),
                    "applied": bool(entry.get("applied")),
                    "source": str(entry.get("source") or ""),
                    "excerpt": str(entry.get("excerpt") or ""),
                }
            )
    for facts_item in facts_items:
        for revision in facts_item.get("revisions", []):
            timeline.append(
                {
                    "ts": revision.get("recorded_at"),
                    "kind": "facts.revision",
                    "item_id": str(facts_item.get("item_id") or ""),
                    "changed": revision.get("changed"),
                    "source": str(revision.get("source") or ""),
                }
            )
    timeline.sort(key=lambda row: row.get("ts") or 0)
    return {
        "ok": True,
        "cookie_id": clean_cookie,
        "chat_id": clean_chat,
        "entry_count": len(timeline),
        "timeline": timeline,
        "current_state": {
            "price_records": [
                {
                    "item_id": str(record.get("item_id") or ""),
                    "seller_lowest_committed": record.get("seller_lowest_committed"),
                    "buyer_highest_offer": record.get("buyer_highest_offer"),
                }
                for record in price_records
            ],
            "product_facts": [
                {
                    "item_id": str(facts_item.get("item_id") or ""),
                    "list_price": facts_item.get("list_price"),
                    "stock_available": facts_item.get("stock_available"),
                    "ships_in_days": facts_item.get("ships_in_days"),
                }
                for facts_item in facts_items
            ],
        },
        "chain": chat_event_store.verify_chain(),
    }


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


@app.post("/execution-jobs/{job_id}/verification")
async def resolve_execution_job_verification(
    job_id: str,
    req: ExecutionVerificationRequest,
) -> Dict[str, Any]:
    """Close an unknown platform outcome only after an explicit operator check."""
    try:
        return execution_queue.resolve_verification(
            job_id,
            resolution=req.resolution,
            note=req.note,
            evidence={
                "external_id": req.external_id,
                "page_url": req.page_url,
                "screenshot": req.screenshot,
                "checked_by": req.checked_by,
            },
            work_order_manager=work_order_manager,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionScopeValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
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
    order_id = order_id.strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id is required")
    cookie_id = _resolve_enabled_operator_cookie_id(req.cookie_id) or "default"
    work_order = work_order_manager.create_work_order(
        kind="order_fulfillment",
        title=f"发货 {order_id}",
        owner="order",
        context={**req.context, "order_id": order_id, "delivery_type": req.delivery_type, "cookie_id": cookie_id},
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
                    "cookie_id": cookie_id,
                },
                "draft_snapshot": {
                    "order_id": order_id,
                    "delivery_type": req.delivery_type,
                    "delivery_payload_summary": payload_summary,
                    "cookie_id": cookie_id,
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
    item_id: str = ""
    idempotency_key: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "normal"


@app.post("/workflows/messages/{buyer_id}/send")
async def send_message_endpoint(buyer_id: str, req: SendMessageRequest) -> Dict[str, Any]:
    """Create a work order for sending a message with approval gate (does not send directly)."""
    buyer_id = buyer_id.strip()
    if not buyer_id:
        raise HTTPException(status_code=400, detail="buyer_id is required")
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    cookie_id = _resolve_enabled_operator_cookie_id(req.cookie_id) or "default"
    risk_flags = _detect_risk_flags(req.message)
    risk_level = req.risk_level
    if risk_flags and risk_level == "normal":
        risk_level = "high"

    message_summary = req.message
    if len(message_summary) > 80:
        message_summary = message_summary[:77] + "..."

    reply_metadata = build_reply_metadata(
        action="send_message",
        cookie_id=cookie_id,
        buyer_id=buyer_id,
        message=req.message,
        chat_id=req.chat_id,
        item_id=req.item_id or str(req.context.get("item_id") or ""),
        source=str(req.context.get("source") or "manual"),
        source_event_key=str(req.context.get("source_event_key") or ""),
        source_message_id=str(req.context.get("source_message_id") or ""),
        reply_revision=req.context.get("reply_revision") or 1,
        idempotency_key=req.idempotency_key,
    )
    existing = _find_existing_reply(reply_metadata)
    if existing:
        approval_id = str(existing["approval"].get("approval_id") or "")
        return {
            "ok": True,
            "duplicate": True,
            "work_order": existing["work_order"],
            "action_plan": existing["work_order"].get("action_plan"),
            "approval_required": existing["approval"].get("status") == "pending",
            "risk_level": existing["work_order"].get("risk_level", risk_level),
            "risk_flags": list((existing["approval"].get("draft_snapshot") or {}).get("risk_flags") or []),
            "reply_outbox": _reply_outbox_projection(approval_id),
        }

    work_order = work_order_manager.create_work_order(
        kind="customer_message",
        title=f"消息 {buyer_id}",
        owner="customer_service",
        context={
            **req.context,
            "buyer_id": buyer_id,
            "message": req.message,
            "cookie_id": cookie_id,
            "chat_id": req.chat_id,
            "item_id": req.item_id or str(req.context.get("item_id") or ""),
            **reply_metadata,
        },
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
                    "cookie_id": cookie_id,
                    "chat_id": req.chat_id,
                    "item_id": req.item_id or str(req.context.get("item_id") or ""),
                    **reply_metadata,
                },
                "draft_snapshot": {
                    "buyer_id": buyer_id,
                    "chat_id": req.chat_id,
                    "item_id": req.item_id or str(req.context.get("item_id") or ""),
                    "cookie_id": cookie_id,
                    "message_summary": message_summary,
                    "risk_flags": risk_flags,
                    **reply_metadata,
                },
            },
        ],
    )
    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    approval_id = str((work_order.get("approvals") or [{}])[0].get("approval_id") or "")
    return {
        "ok": True,
        "duplicate": False,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
        "risk_level": risk_level,
        "risk_flags": risk_flags,
        "reply_outbox": _reply_outbox_projection(approval_id),
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
    idempotency_key: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)


def _snapshot_summary(value: str, limit: int = 80) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _reply_outbox_projection(approval_id: str) -> Optional[Dict[str, Any]]:
    item = outbox_item_for_approval(
        approval_id,
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
    )
    if item and str(item.get("cookie_id") or ""):
        cookie_id = str(item.get("cookie_id") or "")
        chat_id = str(item.get("chat_id") or "")
        item_id = str(item.get("item_id") or "")
        record = price_commitment_store.get_record(
            cookie_id=cookie_id, chat_id=chat_id, item_id=item_id
        )
        facts = (
            product_facts_store.get(cookie_id=cookie_id, item_id=item_id)
            if item_id
            else None
        )
        item["price_context"] = {
            "seller_lowest_committed": (record or {}).get("seller_lowest_committed"),
            "buyer_highest_offer": (record or {}).get("buyer_highest_offer"),
            "list_price": (facts or {}).get("list_price"),
        }
    return item


def _find_existing_reply(reply_metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    existing = find_reply_by_idempotency_key(
        work_order_manager,
        str(reply_metadata.get("reply_idempotency_key") or ""),
    )
    if existing:
        try:
            validate_reply_idempotency_match(existing, reply_metadata)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return existing


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


class MessageEnvelopeRequest(BaseModel):
    source: str = "manual"
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    cookie_id: str = "default"
    defaults: Dict[str, Any] = Field(default_factory=dict)


class BrowserMessageImportRequest(BaseModel):
    cookie_id: str = "default"
    buyer_id: str = ""
    buyer_name: str = ""
    chat_id: str = ""
    item_id: str = ""
    limit: int = 50


class BrowserConversationImportRequest(BaseModel):
    cookie_id: str = "default"
    include_read: bool = False
    min_unread: int = 1
    max_conversations: int = 20
    messages_per_conversation: int = 20
    item_id: str = ""


class MessageImportSchedulerConfigRequest(BaseModel):
    enabled: bool = False
    cookie_id: str = "default"
    interval_seconds: int = 300
    include_read: bool = False
    min_unread: int = 1
    max_conversations: int = 20
    messages_per_conversation: int = 20
    item_id: str = ""


class MessageImportSchedulerBootstrapRequest(BaseModel):
    preset: str = "observe"
    overwrite: bool = False
    include_disabled: bool = False
    dry_run: bool = False


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


class AccountAttributionRepairRequest(BaseModel):
    cookie_id: str = ""
    dry_run: bool = True
    limit: int = 500
    mode: str = "claim"
    work_order_ids: List[str] = Field(default_factory=list)
    preview_token: str = Field(default="", max_length=128)
    confirmation: str = Field(default="", max_length=64)


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


def _message_envelope_from_context(context: Dict[str, Any]) -> Dict[str, Any]:
    envelope = context.get("message_envelope") if isinstance(context, dict) else None
    return dict(envelope or {}) if isinstance(envelope, dict) else {}


def _browser_message_import_defaults(req: BrowserMessageImportRequest) -> Dict[str, Any]:
    return {
        "buyer_id": req.buyer_id,
        "buyer_name": req.buyer_name,
        "chat_id": req.chat_id,
        "item_id": req.item_id,
    }


def _import_message_envelopes(
    *,
    source: str,
    raw_messages: List[Dict[str, Any]],
    cookie_id: str,
    defaults: Dict[str, Any],
    limit: int = 50,
) -> Dict[str, Any]:
    max_items = max(0, int(limit or 0))
    messages = list(raw_messages or [])[:max_items]
    summary = {
        "ok": True,
        "type": f"{source}_message_import",
        "source": source,
        "cookie_id": cookie_id,
        "buyer_id": str(defaults.get("buyer_id") or ""),
        "total": len(messages),
        "created": 0,
        "duplicates": 0,
        "skipped_outbound": 0,
        "skipped_invalid": 0,
        "failed": 0,
        "results": [],
    }
    for index, raw in enumerate(messages):
        if not isinstance(raw, dict):
            summary["skipped_invalid"] += 1
            summary["results"].append({"index": index, "status": "skipped_invalid", "reason": "message is not an object"})
            continue

        message_defaults = dict(defaults)
        if not message_defaults.get("buyer_name") and raw.get("sender"):
            message_defaults["buyer_name"] = raw.get("sender")
        envelope = normalize_message_envelope(
            source=source,
            raw_payload=raw,
            cookie_id=cookie_id,
            defaults=message_defaults,
        )

        if envelope.get("direction") != "inbound":
            summary["skipped_outbound"] += 1
            summary["results"].append(
                {
                    "index": index,
                    "status": "skipped_outbound",
                    "envelope_id": envelope.get("envelope_id", ""),
                    "source_message_id": envelope.get("source_message_id", ""),
                }
            )
            continue

        if not str(envelope.get("buyer_id") or "").strip() or not str(envelope.get("message_text") or "").strip():
            summary["skipped_invalid"] += 1
            summary["results"].append(
                {
                    "index": index,
                    "status": "skipped_invalid",
                    "envelope_id": envelope.get("envelope_id", ""),
                    "source_message_id": envelope.get("source_message_id", ""),
                    "reason": "missing buyer_id or message_text",
                }
            )
            continue

        try:
            inbound_payload = to_inbound_request_payload(envelope)
            result = _create_inbound_message_work_order(InboundMessageRequest(**inbound_payload))
        except Exception as exc:  # pragma: no cover - defensive batch isolation
            summary["failed"] += 1
            summary["results"].append(
                {
                    "index": index,
                    "status": "failed",
                    "envelope_id": envelope.get("envelope_id", ""),
                    "source_message_id": envelope.get("source_message_id", ""),
                    "error": str(exc),
                }
            )
            continue

        duplicate = bool(result.get("duplicate"))
        if duplicate:
            summary["duplicates"] += 1
            status = "duplicate"
        else:
            summary["created"] += 1
            status = "created"
        intake_record = result.get("intake_record") or {}
        work_order = result.get("work_order") or {}
        summary["results"].append(
            {
                "index": index,
                "status": status,
                "envelope_id": (result.get("message_envelope") or {}).get("envelope_id", envelope.get("envelope_id", "")),
                "source_message_id": envelope.get("source_message_id", ""),
                "ack_status": (result.get("message_envelope") or {}).get("ack_status", ""),
                "intake_id": intake_record.get("intake_id", ""),
                "work_order_id": work_order.get("work_order_id") or result.get("existing_work_order_id", ""),
            }
        )
    return summary


def _conversation_unread_count(conversation: Dict[str, Any]) -> int:
    try:
        return int(conversation.get("unread") or 0)
    except (TypeError, ValueError):
        return 0


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


async def _import_browser_conversations(
    *,
    adapter: Any,
    req: BrowserConversationImportRequest,
) -> Dict[str, Any]:
    conversations = list(await _maybe_await(adapter.get_conversations(cookie_id=req.cookie_id)) or [])
    max_conversations = max(0, int(req.max_conversations or 0))
    result = {
        "ok": True,
        "type": "browser_conversation_import",
        "source": "browser",
        "cookie_id": req.cookie_id,
        "conversations_total": len(conversations),
        "conversations_selected": 0,
        "conversations_skipped_read": 0,
        "conversations_skipped_invalid": 0,
        "conversations_skipped_limit": 0,
        "created": 0,
        "duplicates": 0,
        "skipped_outbound": 0,
        "skipped_invalid": 0,
        "failed": 0,
        "conversation_results": [],
    }
    for conversation in conversations:
        if not isinstance(conversation, dict):
            result["conversations_skipped_invalid"] += 1
            continue

        unread = _conversation_unread_count(conversation)
        if not req.include_read and unread < max(1, int(req.min_unread or 1)):
            result["conversations_skipped_read"] += 1
            continue

        if result["conversations_selected"] >= max_conversations:
            result["conversations_skipped_limit"] += 1
            continue

        buyer_id = str(conversation.get("buyer_id") or conversation.get("buyerId") or "").strip()
        if not buyer_id:
            result["conversations_skipped_invalid"] += 1
            continue

        buyer_name = str(conversation.get("buyer_name") or conversation.get("buyerName") or "").strip()
        chat_id = str(conversation.get("session_id") or conversation.get("sessionId") or conversation.get("chat_id") or "").strip()
        messages = list(await _maybe_await(adapter.get_messages(buyer_id=buyer_id, cookie_id=req.cookie_id)) or [])
        import_result = _import_message_envelopes(
            source="browser",
            raw_messages=messages,
            cookie_id=req.cookie_id,
            defaults={
                "buyer_id": buyer_id,
                "buyer_name": buyer_name,
                "chat_id": chat_id,
                "item_id": req.item_id,
            },
            limit=req.messages_per_conversation,
        )

        result["conversations_selected"] += 1
        result["created"] += int(import_result.get("created") or 0)
        result["duplicates"] += int(import_result.get("duplicates") or 0)
        result["skipped_outbound"] += int(import_result.get("skipped_outbound") or 0)
        result["skipped_invalid"] += int(import_result.get("skipped_invalid") or 0)
        result["failed"] += int(import_result.get("failed") or 0)
        result["conversation_results"].append(
            {
                "buyer_id": buyer_id,
                "buyer_name": buyer_name,
                "chat_id": chat_id,
                "unread": unread,
                "last_message": str(conversation.get("last_message") or ""),
                "import_result": import_result,
            }
        )
    return result


async def _run_message_import_scheduler_once(cookie_id: Optional[str] = None) -> Dict[str, Any]:
    from .browser_adapter import BrowserAdapter

    adapter = workflow.adapter
    message_import_scheduler_store.record_started(cookie_id=cookie_id)
    if not isinstance(adapter, BrowserAdapter):
        scheduler = message_import_scheduler_store.record_error("Browser adapter not active", cookie_id=cookie_id)
        return {
            "ok": False,
            "type": "message_import_scheduler_run",
            "error": "Browser adapter not active",
            "scheduler": scheduler,
        }

    try:
        req = BrowserConversationImportRequest(**message_import_scheduler_store.request_payload(cookie_id=cookie_id))
        import_result = await _import_browser_conversations(adapter=adapter, req=req)
    except Exception as exc:
        scheduler = message_import_scheduler_store.record_error(str(exc), cookie_id=cookie_id)
        return {
            "ok": False,
            "type": "message_import_scheduler_run",
            "error": str(exc),
            "scheduler": scheduler,
        }

    scheduler = message_import_scheduler_store.record_result(import_result, cookie_id=cookie_id)
    return {
        "ok": True,
        "type": "message_import_scheduler_run",
        "import_result": import_result,
        "scheduler": scheduler,
    }


async def message_import_scheduler_loop() -> None:
    while True:
        await asyncio.sleep(15)
        for cookie_id in message_import_scheduler_store.due_profiles():
            await _run_message_import_scheduler_once(cookie_id=cookie_id)


def _record_buyer_price_offers(
    cookie_id: str, chat_id: str, item_id: str, message_text: str, source_message_id: str
) -> List[Dict[str, Any]]:
    """Record deterministic price offers extracted from an inbound message.

    Offers are filtered against the known list price when product facts
    exist; the LLM never writes these values.
    """
    if not cookie_id or not message_text:
        return []
    list_price = None
    if item_id:
        facts = product_facts_store.get(cookie_id=cookie_id, item_id=item_id)
        if facts:
            list_price = facts.get("list_price")
    recorded = []
    for mention in extract_price_mentions(message_text):
        result = price_commitment_store.record_buyer_offer(
            cookie_id=cookie_id,
            chat_id=chat_id,
            item_id=item_id,
            amount=mention["amount"],
            source=source_message_id or "inbound",
            excerpt=mention["excerpt"],
            list_price=list_price,
        )
        recorded.append(
            {
                "amount": mention["amount"],
                "pattern": mention["pattern"],
                "status": result["status"],
            }
        )
    return recorded


def _create_inbound_message_work_order(req: InboundMessageRequest) -> Dict[str, Any]:
    buyer_id = req.buyer_id.strip()
    incoming_message = req.message.strip()
    if not buyer_id or not incoming_message:
        raise HTTPException(status_code=400, detail="buyer_id and message are required")

    product_id = (req.product_id or req.item_id).strip()
    cookie_id = _resolve_operator_cookie_id(requested=req.cookie_id, context=req.context)
    message_envelope = _message_envelope_from_context(req.context)
    # The chat fact stream always records with a full envelope; legacy callers
    # that carry no context envelope still get one normalized from the request.
    chat_envelope = message_envelope or normalize_message_envelope(
        source=req.source,
        raw_payload={
            "message_id": req.message_id,
            "buyer_id": buyer_id,
            "buyer_name": req.buyer_name,
            "chat_id": req.chat_id,
            "item_id": product_id,
            "message": incoming_message,
        },
        cookie_id=cookie_id,
    )
    event_key = str(message_envelope.get("dedupe_key") or "").strip()
    if not event_key:
        event_key = message_intake_ledger.event_key(
            source=req.source,
            cookie_id=cookie_id,
            buyer_id=buyer_id,
            chat_id=req.chat_id,
            item_id=req.item_id,
            message=incoming_message,
            message_id=req.message_id,
        )
    existing = message_intake_ledger.find_created(event_key)
    if existing:
        existing_approval_id = str(existing.get("approval_id") or "")
        duplicate_envelope = with_ack_status(message_envelope, "duplicate") if message_envelope else {}
        duplicate = message_intake_ledger.record_duplicate(
            event_key=event_key,
            source=req.source,
            duplicate_of=existing.get("work_order_id") or existing.get("intake_id", ""),
            envelope_id=str(duplicate_envelope.get("envelope_id") or ""),
            source_message_id=str(duplicate_envelope.get("source_message_id") or req.message_id),
            ack_status=str(duplicate_envelope.get("ack_status") or "duplicate"),
        )
        chat_event_store.append_inbound(
            chat_envelope,
            intake_id=str(duplicate.get("intake_id") or ""),
        )
        return {
            "ok": True,
            "type": "customer_message_intake_duplicate",
            "duplicate": True,
            "approval_required": False,
            "event_key": event_key,
            "message_envelope": duplicate_envelope,
            "existing_work_order_id": existing.get("work_order_id", ""),
            "existing_record": existing,
            "intake_record": duplicate,
            "reply_outbox": (
                _reply_outbox_projection(existing_approval_id)
                if existing_approval_id
                else None
            ),
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
    reply_metadata = {}
    if reply_candidate:
        reply_metadata = build_reply_metadata(
            action="send_message",
            cookie_id=cookie_id,
            buyer_id=buyer_id,
            message=reply_candidate,
            chat_id=req.chat_id,
            item_id=req.item_id,
            source=req.source,
            source_event_key=event_key,
            source_message_id=str(message_envelope.get("source_message_id") or req.message_id),
            reply_revision=1,
        )
    context = {
        **req.context,
        "source": req.source,
        "buyer_id": buyer_id,
        "buyer_name": req.buyer_name,
        "incoming_message": incoming_message,
        "reply_candidate": reply_candidate,
        "chat_id": req.chat_id,
        "item_id": req.item_id,
        "product_id": product_id,
        "product_title": req.product_title,
        "cookie_id": cookie_id,
        "conversation": conversation,
        **reply_metadata,
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
                        "cookie_id": cookie_id,
                        "chat_id": req.chat_id,
                        "source": req.source,
                        "item_id": req.item_id,
                        "incoming_message": incoming_message,
                        "conversation_context": conversation,
                        **reply_metadata,
                    },
                    "draft_snapshot": {
                        "source": req.source,
                        "buyer_id": buyer_id,
                        "buyer_name": req.buyer_name,
                        "chat_id": req.chat_id,
                        "item_id": req.item_id,
                        "cookie_id": cookie_id,
                        "incoming_message_summary": _snapshot_summary(incoming_message, 80),
                        "reply_summary": _snapshot_summary(reply_candidate, 120),
                        "risk_flags": risk_flags,
                        "conversation_turns": len(conversation),
                        "approval_note": "Inbound buyer messages create draft replies only; approval is required before sending.",
                        **reply_metadata,
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
        cookie_id=cookie_id,
        status=intake_status,
        generation_error=generation_error,
        envelope_id=str(message_envelope.get("envelope_id") or ""),
        source_message_id=str(message_envelope.get("source_message_id") or req.message_id),
        ack_status="recorded" if message_envelope else "",
    )
    recorded_envelope = with_ack_status(message_envelope, "recorded") if message_envelope else {}
    chat_event_store.append_inbound(
        chat_envelope,
        intake_id=str(intake_record.get("intake_id") or ""),
    )
    buyer_price_offers = _record_buyer_price_offers(
        cookie_id,
        req.chat_id,
        product_id,
        incoming_message,
        str(chat_envelope.get("source_message_id") or req.message_id),
    )
    reply_outbox = _reply_outbox_projection(approval_id) if approval_id else None
    return {
        "ok": True,
        "type": "customer_message_intake",
        "duplicate": False,
        "event_key": event_key,
        "message_envelope": recorded_envelope,
        "intake_record": intake_record,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": bool(plan and plan.get("status") == "needs_approval"),
        "reply_candidate": reply_candidate,
        "risk_level": risk_level,
        "risk_flags": risk_flags,
        "needs_manual_reply": not bool(reply_candidate),
        "buyer_price_offers": buyer_price_offers,
        "reply_outbox": reply_outbox,
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

    reply_revision = int(record.get("retry_count") or 0) + 2
    reply_metadata = build_reply_metadata(
        action="send_message",
        cookie_id=str(context.get("cookie_id") or "default"),
        buyer_id=buyer_id,
        message=reply_candidate,
        chat_id=str(context.get("chat_id") or record.get("chat_id") or ""),
        item_id=str(context.get("item_id") or record.get("item_id") or ""),
        source=str(context.get("source") or record.get("source") or ""),
        source_event_key=str(record.get("event_key") or ""),
        source_message_id=str(record.get("source_message_id") or ""),
        reply_revision=reply_revision,
    )
    existing_reply = _find_existing_reply(reply_metadata)
    if existing_reply:
        existing_approval = existing_reply["approval"]
        approval_id = str(existing_approval.get("approval_id") or "")
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
            "duplicate": True,
            "intake_record": intake_record,
            "work_order": existing_reply["work_order"],
            "action_plan": existing_reply["work_order"].get("action_plan"),
            "approval_required": existing_approval.get("status") == "pending",
            "reply_candidate": reply_candidate,
            "risk_flags": risk_flags,
            "needs_manual_reply": False,
            "reply_outbox": _reply_outbox_projection(approval_id),
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
                    **reply_metadata,
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
                    **reply_metadata,
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
    reply_outbox = _reply_outbox_projection(approval_id)
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
        "duplicate": False,
        "reply_outbox": reply_outbox,
    }


@app.post("/workflows/messages/inbound")
async def inbound_message_endpoint(req: InboundMessageRequest) -> Dict[str, Any]:
    """Create a draft reply approval from an inbound buyer message."""
    result = _create_inbound_message_work_order(req)
    approval_required = result.get("approval_required", False)
    return {**result, "approval_required": approval_required}


@app.post("/message-intake/envelope")
async def message_intake_envelope(req: MessageEnvelopeRequest) -> Dict[str, Any]:
    """Normalize a source message envelope, then feed it into the inbound draft flow."""
    envelope = normalize_message_envelope(
        source=req.source,
        raw_payload=req.raw_payload,
        cookie_id=req.cookie_id,
        defaults=req.defaults,
    )
    inbound_payload = to_inbound_request_payload(envelope)
    result = _create_inbound_message_work_order(InboundMessageRequest(**inbound_payload))
    approval_required = result.get("approval_required", False)
    return {**result, "approval_required": approval_required}


@app.post("/message-intake/import-browser")
async def message_intake_import_browser(req: BrowserMessageImportRequest) -> Dict[str, Any]:
    """Import browser-adapter messages into the normalized intake flow."""
    from .browser_adapter import BrowserAdapter

    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}

    messages_result = adapter.get_messages(buyer_id=req.buyer_id, cookie_id=req.cookie_id)
    if asyncio.iscoroutine(messages_result):
        messages = await messages_result
    else:
        messages = messages_result
    return _import_message_envelopes(
        source="browser",
        raw_messages=list(messages or []),
        cookie_id=req.cookie_id,
        defaults=_browser_message_import_defaults(req),
        limit=req.limit,
    )


@app.post("/message-intake/import-browser-conversations")
async def message_intake_import_browser_conversations(req: BrowserConversationImportRequest) -> Dict[str, Any]:
    """Import unread browser conversations into the normalized intake flow."""
    from .browser_adapter import BrowserAdapter

    adapter = workflow.adapter
    if not isinstance(adapter, BrowserAdapter):
        return {"ok": False, "error": "Browser adapter not active"}
    return await _import_browser_conversations(adapter=adapter, req=req)


@app.get("/message-intake/import-scheduler")
def message_intake_import_scheduler_status() -> Dict[str, Any]:
    """Return browser message import scheduler status."""
    return message_import_scheduler_store.snapshot()


@app.post("/message-intake/import-scheduler")
def message_intake_import_scheduler_configure(req: MessageImportSchedulerConfigRequest) -> Dict[str, Any]:
    """Configure browser message import scheduler."""
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return message_import_scheduler_store.configure(payload)


@app.post("/message-intake/import-scheduler/bootstrap-accounts")
def message_intake_import_scheduler_bootstrap_accounts(
    req: MessageImportSchedulerBootstrapRequest,
) -> Dict[str, Any]:
    """Create scheduler profiles from configured account profiles."""
    return message_import_scheduler_store.bootstrap_from_account_profiles(
        build_account_profiles(db_manager),
        preset=req.preset,
        overwrite=req.overwrite,
        include_disabled=req.include_disabled,
        dry_run=req.dry_run,
    )


@app.post("/message-intake/import-scheduler/run-now")
async def message_intake_import_scheduler_run_now(cookie_id: Optional[str] = None) -> Dict[str, Any]:
    """Run the configured browser message import scheduler immediately."""
    return await _run_message_import_scheduler_once(cookie_id=cookie_id)


@app.get("/message-intake/recent")
def message_intake_recent(limit: int = 20) -> List[Dict[str, Any]]:
    return message_intake_ledger.list_recent(limit=limit)


@app.get("/reply-outbox")
def reply_outbox_list(
    status: str = "",
    cookie_id: str = "",
    limit: int = 100,
) -> Dict[str, Any]:
    """Return the authoritative reply lifecycle projected from approvals and execution jobs."""
    execution_queue.reconcile_expired_leases(work_order_manager)
    items = list_reply_outbox(
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
        status=status,
        cookie_id=cookie_id,
        limit=max(0, min(int(limit), 500)),
    )
    return {
        "ok": True,
        "items": items,
        "summary": summarize_reply_outbox(items),
        "source_of_truth": "WorkOrder + Approval + ExecutionJobQueue",
    }


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
    cookie_id = _resolve_enabled_operator_cookie_id(req.cookie_id) or "default"

    work_order = work_order_manager.create_work_order(
        kind="delete_item",
        title=f"Delete listing {item_id}",
        owner="listing",
        context={
            "item_id": item_id,
            "item_title": req.item_title,
            "reason": req.reason,
            **req.context,
            "cookie_id": cookie_id,
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
                    "cookie_id": cookie_id,
                },
                "draft_snapshot": {
                    "item_id": item_id,
                    "item_title": _snapshot_summary(req.item_title, 80),
                    "cookie_id": cookie_id,
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
    cookie_id = _resolve_enabled_operator_cookie_id(req.cookie_id) or "default"

    work_order = work_order_manager.create_work_order(
        kind="refund_order",
        title=f"Refund order {order_id}",
        owner="order",
        context={
            "order_id": order_id,
            "buyer_id": req.buyer_id,
            "amount_cny": req.amount_cny,
            "reason": req.reason,
            **req.context,
            "cookie_id": cookie_id,
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
                    "cookie_id": cookie_id,
                },
                "draft_snapshot": {
                    "order_id": order_id,
                    "amount_cny": req.amount_cny,
                    "buyer_id": req.buyer_id,
                    "cookie_id": cookie_id,
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
    cookie_id = _resolve_enabled_operator_cookie_id(req.cookie_id) or "default"

    risk_flags = _detect_risk_flags(req.message)
    reply_metadata = build_reply_metadata(
        action="sensitive_reply",
        cookie_id=cookie_id,
        buyer_id=req.buyer_id,
        message=req.message,
        chat_id=req.chat_id,
        order_id=req.order_id,
        dispute_id=dispute_id,
        source="dispute",
        source_event_key=str(req.context.get("source_event_key") or ""),
        source_message_id=str(req.context.get("source_message_id") or ""),
        reply_revision=req.context.get("reply_revision") or 1,
        idempotency_key=req.idempotency_key,
    )
    existing = _find_existing_reply(reply_metadata)
    if existing:
        approval_id = str(existing["approval"].get("approval_id") or "")
        return {
            "ok": True,
            "duplicate": True,
            "work_order": existing["work_order"],
            "action_plan": existing["work_order"].get("action_plan"),
            "approval_required": existing["approval"].get("status") == "pending",
            "risk_flags": list((existing["approval"].get("draft_snapshot") or {}).get("risk_flags") or []),
            "reply_outbox": _reply_outbox_projection(approval_id),
        }
    work_order = work_order_manager.create_work_order(
        kind="dispute_reply",
        title=f"Dispute reply {dispute_id}",
        owner="customer_service",
        context={
            **req.context,
            "dispute_id": dispute_id,
            "order_id": req.order_id,
            "buyer_id": req.buyer_id,
            "message": req.message,
            "cookie_id": cookie_id,
            **reply_metadata,
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
                    "cookie_id": cookie_id,
                    "chat_id": req.chat_id,
                    **reply_metadata,
                },
                "draft_snapshot": {
                    "dispute_id": dispute_id,
                    "order_id": req.order_id,
                    "buyer_id": req.buyer_id,
                    "chat_id": req.chat_id,
                    "cookie_id": cookie_id,
                    "message_summary": _snapshot_summary(req.message, 80),
                    "risk_flags": risk_flags,
                    "approval_note": "Dispute replies are sensitive and require explicit approval.",
                    **reply_metadata,
                },
            },
        ],
    )
    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    approval_id = str((work_order.get("approvals") or [{}])[0].get("approval_id") or "")
    return {
        "ok": True,
        "duplicate": False,
        "work_order": work_order,
        "action_plan": plan,
        "approval_required": plan.get("status") == "needs_approval",
        "risk_flags": risk_flags,
        "reply_outbox": _reply_outbox_projection(approval_id),
    }


# ============================================================
# Resale Opportunity Endpoints
# ============================================================


class CandidateActivationUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=160)
    description: Optional[str] = Field(default=None, max_length=2000)
    category: Optional[str] = Field(default=None, max_length=120)
    source_platform: Optional[str] = Field(default=None, max_length=40)
    source_url: Optional[str] = Field(default=None, max_length=1000)
    supplier_name: Optional[str] = Field(default=None, max_length=160)
    supplier_sku: Optional[str] = Field(default=None, max_length=160)
    supplier_price: Optional[float] = Field(default=None, ge=0)
    supplier_shipping: Optional[float] = Field(default=None, ge=0)
    target_sale_price: Optional[float] = Field(default=None, ge=0)
    specifications: Optional[Dict[str, Any]] = None
    images: Optional[List[str]] = None
    sales_platform: Optional[str] = Field(default=None, max_length=40)
    market_scope: Optional[str] = Field(default=None, max_length=40)
    supplier_verified: Optional[bool] = None
    supplier_verification_note: Optional[str] = Field(default=None, max_length=500)
    supplier_quote_checked_at: Optional[str] = Field(default=None, max_length=80)
    supplier_quote_evidence_ref: Optional[str] = Field(default=None, max_length=300)
    minimum_order_quantity: Optional[int] = Field(default=None, ge=0)
    stock_status: Optional[str] = Field(default=None, max_length=40)
    stock_quantity: Optional[int] = Field(default=None, ge=0)
    stock_checked_at: Optional[str] = Field(default=None, max_length=80)
    stock_evidence_ref: Optional[str] = Field(default=None, max_length=300)
    fulfillment_verified: Optional[bool] = None
    fulfillment_verification_note: Optional[str] = Field(default=None, max_length=500)
    fulfillment_evidence_ref: Optional[str] = Field(default=None, max_length=300)
    neutral_shipping_confirmed: Optional[bool] = None
    dispatch_time_hours: Optional[float] = Field(default=None, ge=0)
    supplier_return_policy: Optional[str] = Field(default=None, max_length=40)
    damage_liability_note: Optional[str] = Field(default=None, max_length=500)
    image_rights_type: Optional[str] = Field(default=None, max_length=40)
    image_rights_evidence: Optional[str] = Field(default=None, max_length=500)
    image_rights_evidence_ref: Optional[str] = Field(default=None, max_length=300)
    competitor_links: Optional[List[str]] = None
    competitor_price_min: Optional[float] = Field(default=None, ge=0)
    competitor_price_max: Optional[float] = Field(default=None, ge=0)
    competitor_checked_at: Optional[str] = Field(default=None, max_length=80)
    competitor_evidence_ref: Optional[str] = Field(default=None, max_length=300)
    estimated_demand_signal: Optional[str] = Field(default=None, max_length=500)


def _candidate_activation_packet_for(candidate_id: str, checked_at: str = "") -> Dict[str, Any]:
    candidate = resale_manager.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _evaluate_candidate_activation_strict(
        candidate,
        checked_at=checked_at or None,
    )


def _evaluate_candidate_activation_strict(
    candidate: Mapping[str, Any],
    *,
    checked_at: Optional[str] = None,
) -> Dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
    artifacts = resale_manager.evidence_store.artifact_lookup(candidate_id)
    return evaluate_candidate_activation(
        candidate=candidate,
        settings=settings,
        checked_at=checked_at,
        evidence_artifacts=artifacts,
        require_archived_evidence=True,
    )


def _activation_summary(packet: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "ready": bool(packet.get("ready")),
        "evidence_ready": bool(packet.get("evidence_ready")),
        "completion": dict(packet.get("completion") or {}),
        "failed_gate_codes": list(packet.get("failed_gate_codes") or []),
        "next_step": dict(packet.get("next_step") or {}) if packet.get("next_step") else None,
        "evidence_revision": packet.get("evidence_revision"),
    }

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
    candidates = resale_manager.list_candidates(
        status=status,
        source_platform=source_platform,
        market_scope=market_scope,
    )
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for candidate in candidates:
        packet = _evaluate_candidate_activation_strict(candidate, checked_at=checked_at)
        candidate["activation_summary"] = _activation_summary(packet)
    return candidates


@app.get("/resale/candidates/{candidate_id}")
async def get_resale_candidate(candidate_id: str) -> Dict[str, Any]:
    """Get a single resale candidate by ID."""
    candidate = resale_manager.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@app.get("/resale/candidates/{candidate_id}/activation-packet")
def get_candidate_activation_packet(candidate_id: str) -> Dict[str, Any]:
    """Return the read-only, account-independent one-product evidence packet."""
    return _candidate_activation_packet_for(candidate_id)


@app.patch("/resale/candidates/{candidate_id}/activation-packet")
def update_candidate_activation_packet(
    candidate_id: str,
    req: CandidateActivationUpdateRequest,
) -> Dict[str, Any]:
    """Save a versioned candidate fact set and return its new readiness packet."""
    if hasattr(req, "model_dump"):
        payload = req.model_dump(exclude_unset=True)
    else:
        payload = req.dict(exclude_unset=True)
    before = resale_manager.get_candidate(candidate_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        candidate = resale_manager.update_candidate_activation(candidate_id, payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    packet = _evaluate_candidate_activation_strict(candidate)
    return {
        "ok": True,
        "candidate": candidate,
        "activation_packet": packet,
        "manual_decision_invalidated": bool(
            before.get("manual_decision") is not None
            and candidate.get("manual_decision") is None
            and candidate.get("evidence_revision") != before.get("evidence_revision")
        ),
    }


@app.get("/resale/candidates/{candidate_id}/evidence-artifacts")
def list_candidate_evidence_artifacts(candidate_id: str) -> Dict[str, Any]:
    """Return archive metadata only; file-system paths are never exposed."""
    candidate = resale_manager.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    artifacts = resale_manager.evidence_store.list_candidate_artifacts(candidate_id)
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "required_kinds": list(EVIDENCE_KINDS),
        "count": len(artifacts),
        "artifacts": artifacts,
    }


@app.post("/resale/candidates/{candidate_id}/evidence-artifacts")
async def upload_candidate_evidence_artifact(
    candidate_id: str,
    request: Request,
    kind: str,
    observed_at: str,
    filename: str,
    note: str = "",
    source_url: str = "",
) -> Dict[str, Any]:
    """Archive a raw evidence file, bind it, then activate the new revision."""
    before = resale_manager.get_candidate(candidate_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError:
        content_length = 0
    if content_length > MAX_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail="Evidence file exceeds 10 MiB")
    content = await request.body()
    if len(content) > MAX_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail="Evidence file exceeds 10 MiB")
    mime_type = str(request.headers.get("content-type") or "application/octet-stream")

    try:
        with candidate_evidence_action_lock:
            result = resale_manager.archive_candidate_evidence(
                candidate_id,
                kind=kind,
                content=content,
                original_filename=filename,
                mime_type=mime_type,
                observed_at=observed_at,
                note=note,
                source_url=source_url,
            )
    except CandidateEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CandidateEvidenceIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CandidateEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        status_code = 409 if "listing draft" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    candidate = dict(result["candidate"])
    packet = _evaluate_candidate_activation_strict(candidate)
    return {
        "ok": True,
        "candidate": candidate,
        "artifact": result["artifact"],
        "created": bool(result.get("created")),
        "activation_packet": packet,
        "manual_decision_invalidated": bool(
            before.get("manual_decision") is not None
            and candidate.get("manual_decision") is None
            and candidate.get("evidence_revision") != before.get("evidence_revision")
        ),
    }


@app.get("/resale/candidates/{candidate_id}/evidence-artifacts/{artifact_id}/file")
def download_candidate_evidence_artifact(candidate_id: str, artifact_id: str) -> FileResponse:
    """Download one candidate-bound artifact through a resolved archive path."""
    if resale_manager.get_candidate(candidate_id) is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        path, artifact = resale_manager.evidence_store.resolve_file(candidate_id, artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Evidence artifact not found") from exc
    except CandidateEvidenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=str(artifact.get("mime_type") or "application/octet-stream"),
        filename=str(artifact.get("original_filename") or "evidence.bin"),
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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


def _create_purchase_draft_side_effects(
    plan_id: str,
    req: PurchaseDraftRequest,
    *,
    commercial_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Create one approval-gated purchase draft from a validated plan.

    ``commercial_context`` carries only audit linkage. Buyer addresses remain
    masked in the draft and supplier payment/submission is never performed by
    this helper.
    """
    plan = resale_manager.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    candidate = resale_manager.get_candidate(plan.get("candidate_id", ""))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found for plan")

    commercial = dict(commercial_context or {})
    draft = resale_manager.create_purchase_draft(
        plan_id=plan_id,
        quantity=req.quantity,
        buyer_address_summary=req.buyer_address_summary,
        buyer_address_full=req.buyer_address_full,
        commercial_trial_id=str(commercial.get("commercial_trial_id") or ""),
        xianyu_order_id=str(commercial.get("xianyu_order_id") or ""),
        cookie_id=str(commercial.get("cookie_id") or ""),
        item_id=str(commercial.get("item_id") or ""),
        sale_amount=float(commercial.get("sale_amount") or 0),
        fulfillment_lock_owner=str(commercial.get("fulfillment_lock_owner") or ""),
    )
    if not draft:
        raise HTTPException(status_code=400, detail="Failed to create purchase draft")

    agent_discussion = candidate.get("agent_reviews")
    title = f"采购草稿: {candidate.get('title', '')} x{req.quantity}"
    context = {
        "plan_id": plan_id,
        "candidate_id": candidate.get("candidate_id"),
        "draft_id": draft.get("draft_id"),
        "supplier_url": candidate.get("source_url", ""),
        "total_cost": draft.get("total_cost", 0),
    }
    context.update({key: value for key, value in commercial.items() if value not in (None, "")})
    work_order = work_order_manager.create_work_order(
        kind="resale_purchase",
        title=title,
        owner="resale",
        context=context,
        risk_level="high",
    )

    params = {
        "plan_id": plan_id,
        "draft_id": draft.get("draft_id"),
        "quantity": req.quantity,
        "buyer_address_summary": req.buyer_address_summary,
    }
    params.update({key: value for key, value in commercial.items() if value not in (None, "")})
    work_order_manager.create_action_plan(
        work_order["work_order_id"],
        discussion=agent_discussion,
        proposed_actions=[{
            "agent": "resale",
            "action": "prepare_purchase_draft",
            "description": f"生成采购草稿: {candidate.get('title', '')} x{req.quantity}",
            "params": params,
            "draft_snapshot": {
                "supplier_url": candidate.get("source_url", ""),
                "product_specs": candidate.get("specifications", {}),
                "quantity": req.quantity,
                "total_cost": draft.get("total_cost", 0),
                "sale_amount": draft.get("sale_amount", 0),
                "xianyu_order_id": draft.get("xianyu_order_id", ""),
                "profit_review": draft.get("profit_review", {}),
                "risk_warnings": draft.get("risk_warnings", []),
            },
            "requires_approval": True,
        }],
    )

    work_order = work_order_manager.get_work_order(work_order["work_order_id"])
    approvals = work_order.get("approvals") or []
    approval_id = str(approvals[0].get("approval_id") or "") if approvals else ""
    resale_manager.update_purchase_draft(draft["draft_id"], {
        "work_order_id": work_order["work_order_id"],
        "approval_id": approval_id,
    })
    resale_manager.update_plan(plan_id, {
        "status": "pending_approval",
        "work_order_id": work_order["work_order_id"],
    })
    draft = resale_manager.get_purchase_draft(draft["draft_id"]) or draft
    return {
        "ok": True,
        "purchase_draft": draft,
        "work_order": work_order,
        "approval_required": True,
        "message": "采购草稿已创建，需人工审批后才能继续。MVP 不会自动付款或提交供应商订单。",
    }


@app.post("/resale/plans/{plan_id}/purchase-draft")
async def create_purchase_draft_endpoint(plan_id: str, req: PurchaseDraftRequest) -> Dict[str, Any]:
    """Create a purchase draft through WorkOrder + Approval flow.

    Does NOT auto-pay or auto-submit. Returns a WorkOrder with pending approval.
    """
    return _create_purchase_draft_side_effects(plan_id, req)


class VerifyRequest(BaseModel):
    supplier_verified: bool = False
    supplier_verification_note: str = ""
    supplier_quote_checked_at: Optional[str] = None
    supplier_quote_evidence_ref: str = ""
    minimum_order_quantity: Optional[int] = None
    stock_status: str = ""
    stock_quantity: Optional[int] = None
    stock_checked_at: Optional[str] = None
    stock_evidence_ref: str = ""
    fulfillment_verified: bool = False
    fulfillment_verification_note: str = ""
    fulfillment_evidence_ref: str = ""
    neutral_shipping_confirmed: bool = False
    dispatch_time_hours: Optional[float] = None
    supplier_return_policy: str = ""
    damage_liability_note: str = ""
    image_rights_type: str = ""
    image_rights_evidence: str = ""
    image_rights_evidence_ref: str = ""
    images: Optional[list] = None
    competitor_links: Optional[list] = None
    competitor_price_min: Optional[float] = None
    competitor_price_max: Optional[float] = None
    competitor_checked_at: Optional[str] = None
    competitor_evidence_ref: str = ""
    estimated_demand_signal: str = ""


@app.post("/resale/candidates/{candidate_id}/verify")
async def verify_resale_candidate(candidate_id: str, req: VerifyRequest) -> Dict[str, Any]:
    """Record supplier verification results for a candidate.

    Only fields explicitly present in the request body are updated, so an
    existing candidate keeps its images and competitor data when the operator
    submits just part of the evidence form. Explicit `null` still clears a
    field; an omitted field is preserved.
    """
    fields_set = getattr(req, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(req, "__fields_set__", set())
    payload = {field: getattr(req, field) for field in fields_set}
    try:
        result = resale_manager.verify_candidate(candidate_id, payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


def _build_preflight_account(cookie_id: str, queues_snapshot: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Assemble a sanitized account-resolution dict for commercial preflight.

    Reuses the configured-account API (db_manager) and the existing account
    readiness snapshot so preflight never invents a second source of truth.
    Returns only booleans and the cookie id — never cookie values, auth
    headers, prompt bodies, or raw runtime payloads.
    """
    requested = str(cookie_id or "").strip()
    configured_accounts = {
        str(account_id or "").strip()
        for account_id in db_manager.get_all_cookies().keys()
        if str(account_id or "").strip()
    }
    explicit = bool(requested)
    configured = requested in configured_accounts if requested else False
    enabled = bool(configured and db_manager.get_all_cookie_status().get(requested, True))
    account: Dict[str, Any] = {
        "cookie_id": requested,
        "explicit": explicit,
        "configured": configured,
        "enabled": enabled,
        "account_enabled": enabled,
        "prompt_ready": False,
        "sync_current": False,
        "runtime_connected": False,
        "runtime_fresh": False,
        "runtime_unblocked": False,
    }
    if configured:
        queues = dict(queues_snapshot) if queues_snapshot is not None else _account_queues_snapshot()
        entry = next(
            (
                item
                for item in queues.get("accounts", [])
                if str(item.get("cookie_id") or "") == requested
            ),
            None,
        )
        if entry is not None:
            account.update(_preflight_readiness(entry))
    return account


def _preflight_readiness(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the six account-scoped readiness booleans from a queue entry."""
    prompt = entry.get("prompt_profile") or {}
    freshness = entry.get("sync_freshness") or {}
    runtime = entry.get("account_runtime") or {}
    status = str(runtime.get("connection_status") or "")
    now = time.time()
    heartbeat = float(runtime.get("last_heartbeat_at") or 0)
    heartbeat_age = max(0.0, now - heartbeat) if heartbeat else float("inf")
    connected = status in {"connected", "degraded"}
    return {
        "account_enabled": bool(entry.get("enabled", True)),
        "prompt_ready": bool(prompt.get("ready")),
        "sync_current": all(
            isinstance(freshness.get(resource), Mapping) and bool(freshness[resource].get("fresh"))
            for resource in ("orders", "listings")
        ),
        "runtime_connected": connected,
        "runtime_fresh": bool(
            connected
            and heartbeat_age <= RUNTIME_HEARTBEAT_MAX_AGE_SECONDS
            and runtime.get("token_fresh")
            and runtime.get("cookie_fresh")
        ),
        "runtime_unblocked": bool(
            connected
            and not runtime.get("pending_verification_reason")
            and not runtime.get("last_error")
        ),
    }


def _commercial_preflight_for(candidate_id: str, cookie_id: str) -> Dict[str, Any]:
    """Run the pure commercial preflight for one candidate + account. Read-only."""
    candidate = resale_manager.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    account = _build_preflight_account(cookie_id)
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    commercial_terms = _commercial_terms_assessment(
        cookie_id=str(cookie_id or "").strip(),
        category=str(candidate.get("category") or "").strip(),
        candidate=candidate,
        checked_at=checked_at,
    )
    return run_commercial_preflight(
        candidate=candidate,
        account=account,
        settings=settings,
        checked_at=checked_at,
        commercial_terms=commercial_terms,
        candidate_evidence_artifacts=resale_manager.evidence_store.artifact_lookup(candidate_id),
        require_archived_candidate_evidence=True,
    )


@app.get("/resale/candidates/{candidate_id}/publish-preflight")
def get_publish_preflight(candidate_id: str, cookie_id: str = "") -> Dict[str, Any]:
    """Read-only evidence-based publish preflight for one candidate.

    Always returns HTTP 200 with the sanitized result. Missing/ambiguous/
    unknown/disabled accounts produce a blocked result with the account gate
    failed rather than an exception, matching the dashboard readiness pattern.
    """
    return _commercial_preflight_for(candidate_id, cookie_id)


def _strict_listing_draft_side_effects(candidate_id: str, cookie_id: str) -> Dict[str, Any]:
    """Create a listing draft + WorkOrder + publish approval after strict preflight.

    Shared by the resale candidate endpoint and the commercial trial batch
    endpoint so the side-effect path is never duplicated. Strict commercial
    preflight runs BEFORE any draft/workorder/approval side effect; a
    missing/ambiguous/unknown/disabled account or any failed gate raises
    HTTP 409 with the sanitized preflight detail. Does NOT auto-publish.
    """
    candidate = resale_manager.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    preflight = _commercial_preflight_for(candidate_id, cookie_id)
    if not preflight["ready"]:
        raise HTTPException(status_code=409, detail=preflight)

    resolved_cookie_id = _resolve_enabled_operator_cookie_id(cookie_id)

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
            **({"cookie_id": resolved_cookie_id} if resolved_cookie_id else {}),
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
                "expected_draft_revision": listing_draft_revision(draft),
                "preflight": preflight["snapshot"],
                **({"cookie_id": resolved_cookie_id} if resolved_cookie_id else {}),
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
                "search_keywords": draft.get("search_keywords", []),
                "lead_image_requirement": draft.get("lead_image_requirement", {}),
                "specification_table": draft.get("specification_table", []),
                "price_ladder": draft.get("price_ladder", []),
                "trust_paragraph": draft.get("trust_paragraph", ""),
                "delivery_after_sales_notes": draft.get("delivery_after_sales_notes", {}),
                "operator_verification_questions": draft.get("operator_verification_questions", []),
                "structured_fields_complete": draft.get("structured_fields_complete", False),
                "preflight": preflight["snapshot"],
                **({"cookie_id": resolved_cookie_id} if resolved_cookie_id else {}),
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


@app.post("/resale/candidates/{candidate_id}/listing-draft")
async def create_listing_draft_endpoint(candidate_id: str, cookie_id: str = "") -> Dict[str, Any]:
    """Create a listing draft from an approved+verified candidate.

    Strict commercial preflight runs BEFORE any draft/workorder/approval side
    effect. A missing/ambiguous/unknown/disabled account or any failed gate
    returns HTTP 409 with the sanitized preflight detail. On success creates a
    WorkOrder with publish_listing approval. Does NOT auto-publish.
    """
    return _strict_listing_draft_side_effects(candidate_id, cookie_id)


# ============================================================
# Commercial Alpha Trial Endpoints (Phase 19)
# ============================================================

def _reconcile_commercial_trial(trial: Mapping[str, Any]) -> Dict[str, Any]:
    """Reconcile one trial against the current live managers. Read-only."""
    cookie_id = str(trial.get("cookie_id") or "").strip()
    candidate_ids = [
        str(cid or "").strip()
        for cid in trial.get("candidate_ids") or []
        if str(cid or "").strip()
    ]
    candidates: List[Dict[str, Any]] = []
    preflight_results: Dict[str, Any] = {}
    for candidate_id in candidate_ids:
        candidate = resale_manager.get_candidate(candidate_id)
        if not candidate:
            continue
        candidates.append(candidate)
        preflight_results[candidate_id] = _commercial_preflight_for(candidate_id, cookie_id)
    return reconcile_trial(
        trial=trial,
        account=_build_preflight_account(cookie_id),
        candidates=candidates,
        work_orders=work_order_manager.list_work_orders(limit=500),
        jobs=execution_queue.list_jobs(limit=500),
        intake_records=message_intake_ledger.list_recent(limit=200),
        runtime_orders=order_runtime_store.list_orders(limit=500),
        resale_orders=resale_manager.list_resale_orders(),
        preflight_results=preflight_results,
        checked_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    )


class CommercialTrialCreateRequest(BaseModel):
    cookie_id: str = ""
    category: str = ""
    controls: Optional[Dict[str, Any]] = None
    candidate_ids: Optional[List[str]] = None


class CommercialLaunchRequest(BaseModel):
    cookie_id: str = Field(default="", max_length=120)
    candidate_id: str = Field(default="", max_length=120)
    controls: Optional[Dict[str, Any]] = None
    preview_token: str = Field(default="", max_length=128)
    confirmation: str = Field(default="", max_length=64)


class CommercialTrialDecisionRequest(BaseModel):
    decision: str = ""
    note: str = ""


class CommercialTrafficObservationRequest(BaseModel):
    views: int = Field(ge=0, le=100_000_000)
    wants: int = Field(ge=0, le=100_000_000)
    evidence_note: str = Field(min_length=4, max_length=300)
    evidence_ref: str = Field(default="", max_length=300)
    observed_at: Optional[float] = None


class CommercialExperimentStartRequest(BaseModel):
    variable: str = ""
    hypothesis: str = Field(min_length=4, max_length=300)
    before_value: str = Field(min_length=1, max_length=300)
    after_value: str = Field(min_length=1, max_length=300)
    planned_days: int = Field(default=0, ge=0, le=14)


class CommercialExperimentDecisionRequest(BaseModel):
    decision: str = ""
    note: str = Field(min_length=4, max_length=500)
    allow_early: bool = False


class CommercialExperimentActivationRequest(BaseModel):
    manual_confirmation: bool = False
    evidence_note: str = Field(default="", max_length=500)
    evidence_ref: str = Field(default="", max_length=300)


class CommercialExperimentCancelRequest(BaseModel):
    note: str = Field(min_length=4, max_length=500)


def _commercial_launch_account_version(queues: Mapping[str, Any], cookie_id: str) -> Dict[str, Any]:
    entry = next(
        (
            item for item in queues.get("accounts") or []
            if isinstance(item, Mapping) and str(item.get("cookie_id") or "") == cookie_id
        ),
        {},
    )
    freshness = entry.get("sync_freshness") if isinstance(entry.get("sync_freshness"), Mapping) else {}
    runtime = entry.get("account_runtime") if isinstance(entry.get("account_runtime"), Mapping) else {}
    prompt = entry.get("prompt_profile") if isinstance(entry.get("prompt_profile"), Mapping) else {}
    return {
        "prompt_ready": bool(prompt.get("ready")),
        "orders_sync": {
            "fresh": bool((freshness.get("orders") or {}).get("fresh")),
            "last_synced_at": (freshness.get("orders") or {}).get("last_synced_at"),
        },
        "listings_sync": {
            "fresh": bool((freshness.get("listings") or {}).get("fresh")),
            "last_synced_at": (freshness.get("listings") or {}).get("last_synced_at"),
        },
        "runtime": {
            "connection_status": str(runtime.get("connection_status") or ""),
            "last_heartbeat_at": runtime.get("last_heartbeat_at"),
            "token_fresh": bool(runtime.get("token_fresh")),
            "cookie_fresh": bool(runtime.get("cookie_fresh")),
            "has_verification_blocker": bool(runtime.get("pending_verification_reason")),
            "has_runtime_error": bool(runtime.get("last_error")),
        },
    }


def _build_commercial_launch_preview(req: CommercialLaunchRequest) -> Dict[str, Any]:
    cookie_id = str(req.cookie_id or "").strip()
    candidate_id = str(req.candidate_id or "").strip()
    queues = _account_queues_snapshot()
    account = _build_preflight_account(cookie_id, queues)
    candidate = resale_manager.get_candidate(candidate_id) if candidate_id else None
    preflight: Dict[str, Any] = {}
    commercial_terms = _commercial_terms_assessment(
        cookie_id=cookie_id,
        category=str((candidate or {}).get("category") or "").strip(),
        candidate=candidate,
    )
    if candidate is not None:
        preflight = run_commercial_preflight(
            candidate=candidate,
            account=account,
            settings=settings,
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            commercial_terms=commercial_terms,
            candidate_evidence_artifacts=resale_manager.evidence_store.artifact_lookup(candidate_id),
            require_archived_candidate_evidence=True,
        )
    configured_accounts = list(db_manager.get_all_cookies().keys())
    unassigned_review = review_unassigned_work(
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
        account_ids=configured_accounts,
        limit=500,
    )
    return build_commercial_launch_preview(
        cookie_id=cookie_id,
        candidate=candidate,
        account=account,
        account_version=_commercial_launch_account_version(queues, cookie_id),
        adapter_health=_current_adapter_health(),
        unassigned_review=unassigned_review,
        unassigned_queue_counts=((queues.get("unassigned") or {}).get("queues") or {}),
        active_trials=commercial_trial_store.active_trials(),
        candidate_preflight=preflight,
        commercial_terms=commercial_terms,
        controls=req.controls,
    )


@app.post("/commercial-launch/preview")
def preview_commercial_launch(req: CommercialLaunchRequest) -> Dict[str, Any]:
    """Read-only Day-0 gate snapshot for one account and one candidate."""
    return _build_commercial_launch_preview(req)


@app.post("/commercial-launch/start")
def start_commercial_launch(req: CommercialLaunchRequest) -> Dict[str, Any]:
    """Atomically start one strict Day-0 batch after a current preview."""
    token = str(req.preview_token or "").strip()
    confirmation = str(req.confirmation or "").strip()
    if not token:
        raise HTTPException(
            status_code=409,
            detail={"error": "commercial_launch_blocked", "reason": "preview_token_required"},
        )
    if confirmation != "START 1":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "commercial_launch_blocked",
                "reason": "confirmation_mismatch",
                "expected_confirmation": "START 1",
            },
        )

    with commercial_trial_action_lock:
        existing = commercial_trial_store.trial_for_launch_key(token)
        if existing is not None:
            requested_controls, requested_control_errors = validate_controls(day_zero_controls(req.controls))
            same_launch = bool(
                str(existing.get("cookie_id") or "") == str(req.cookie_id or "").strip()
                and list(existing.get("candidate_ids") or []) == [str(req.candidate_id or "").strip()]
                and str(existing.get("launch_mode") or "") == "day0"
                and not requested_control_errors
                and dict(existing.get("controls") or {}) == requested_controls
            )
            if not same_launch:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "commercial_launch_blocked", "reason": "launch_token_conflict"},
                )
            return {
                "ok": True,
                "idempotent": True,
                "launch_token": token,
                "trial": _reconcile_commercial_trial(existing),
            }

        preview = _build_commercial_launch_preview(req)
        if token != str(preview.get("preview_token") or ""):
            raise HTTPException(
                status_code=409,
                detail={"error": "commercial_launch_blocked", "reason": "stale_preview_token"},
            )
        if not preview.get("ready"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "commercial_launch_blocked",
                    "reason": "launch_gates_blocked",
                    "failed_gate_codes": preview.get("failed_gate_codes") or [],
                    "next_step": preview.get("next_step") or {},
                },
            )
        try:
            trial = commercial_trial_store.create_trial(
                cookie_id=preview["cookie_id"],
                category=preview["category"],
                controls=preview["controls"],
                candidate_ids=[preview["candidate_id"]],
                launch_key=token,
                launch_mode="day0",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "commercial_launch_blocked", "reason": str(exc)},
            ) from exc
    return {
        "ok": True,
        "idempotent": False,
        "launch_token": token,
        "trial": _reconcile_commercial_trial(trial),
    }


@app.post("/commercial-trials", deprecated=True)
def create_commercial_trial(req: CommercialTrialCreateRequest) -> Dict[str, Any]:
    """Reject the retired direct-create path so Day-0 gates cannot be bypassed."""
    raise HTTPException(
        status_code=409,
        detail={
            "error": "strict_commercial_launch_required",
            "reason": "legacy_direct_creation_disabled",
            "preview_endpoint": "/commercial-launch/preview",
            "start_endpoint": "/commercial-launch/start",
        },
    )


@app.get("/commercial-trials")
def list_commercial_trials() -> Dict[str, Any]:
    """List all commercial alpha trials with live reconciliation."""
    trials = [_reconcile_commercial_trial(trial) for trial in commercial_trial_store.list_trials()]
    return {"ok": True, "trials": trials, "count": len(trials)}


@app.get("/commercial-trials/active")
def get_active_commercial_trial(cookie_id: str = "") -> Dict[str, Any]:
    """Return the most recent active/paused trial, or null when none exists.

    Defined before the parameter route so ``/active`` never matches
    ``/{trial_id}``.
    """
    active = commercial_trial_store.active_trials()
    requested_cookie_id = str(cookie_id or "").strip()
    if requested_cookie_id:
        active = [trial for trial in active if str(trial.get("cookie_id") or "") == requested_cookie_id]
    if not active:
        return {"ok": True, "trial": None, "trials": [], "count": 0}
    active.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    reconciled = [_reconcile_commercial_trial(trial) for trial in active]
    return {"ok": True, "trial": reconciled[0], "trials": reconciled, "count": len(reconciled)}


@app.get("/commercial-trials/{trial_id}")
def get_commercial_trial(trial_id: str) -> Dict[str, Any]:
    """Return one trial with live reconciliation."""
    trial = commercial_trial_store.get_trial(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    return _reconcile_commercial_trial(trial)


def _trial_published_item(reconciled: Mapping[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    clean_item_id = str(item_id or "").strip()
    for item in reconciled.get("published_items") or []:
        if isinstance(item, Mapping) and str(item.get("item_id") or "") == clean_item_id:
            return dict(item)
    return None


@app.post("/commercial-trials/{trial_id}/items/{item_id}/traffic-observations")
def record_commercial_traffic_observation(
    trial_id: str,
    item_id: str,
    req: CommercialTrafficObservationRequest,
) -> Dict[str, Any]:
    """Append or update today's operator-verified cumulative traffic snapshot."""
    trial = commercial_trial_store.get_trial(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    reconciled = _reconcile_commercial_trial(trial)
    published = _trial_published_item(reconciled, item_id)
    if not published:
        raise HTTPException(
            status_code=409,
            detail={"error": "traffic_observation_blocked", "reason": "item_not_in_real_publish_evidence"},
        )
    observed_at = time.time() if req.observed_at is None else float(req.observed_at)
    published_at = time.mktime(time.strptime(str(published.get("published_at") or ""), "%Y-%m-%dT%H:%M:%S"))
    if observed_at < published_at:
        raise HTTPException(
            status_code=409,
            detail={"error": "traffic_observation_blocked", "reason": "observation_predates_publish"},
        )
    try:
        observation = commercial_trial_store.record_traffic_observation(
            trial_id,
            item_id=item_id,
            candidate_id=str(published.get("candidate_id") or ""),
            views=req.views,
            wants=req.wants,
            source="operator",
            evidence_note=req.evidence_note,
            evidence_ref=req.evidence_ref,
            observed_at=observed_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "ok": True,
        "observation": observation,
        "trial": _reconcile_commercial_trial(commercial_trial_store.get_trial(trial_id) or {}),
    }


@app.post("/commercial-trials/{trial_id}/items/{item_id}/experiments")
def start_commercial_item_experiment(
    trial_id: str,
    item_id: str,
    req: CommercialExperimentStartRequest,
) -> Dict[str, Any]:
    """Propose one change and create its approval; timing starts after confirmation."""
    with commercial_trial_action_lock:
        trial = commercial_trial_store.get_trial(trial_id)
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        reconciled = _reconcile_commercial_trial(trial)
        published = _trial_published_item(reconciled, item_id)
        if not published:
            raise HTTPException(
                status_code=409,
                detail={"error": "experiment_blocked", "reason": "item_not_in_real_publish_evidence"},
            )
        if reconciled.get("stop_required") or str(reconciled.get("lifecycle") or "") != "active":
            raise HTTPException(
                status_code=409,
                detail={"error": "experiment_blocked", "reason": "trial_not_active_or_stop_required"},
            )
        performance = next(
            (item for item in reconciled.get("item_performance") or [] if str(item.get("item_id") or "") == item_id),
            {},
        )
        planned_days = int(req.planned_days or reconciled.get("controls", {}).get("experiment_days") or 3)
        try:
            experiment = commercial_trial_store.propose_experiment(
                trial_id,
                item_id=item_id,
                candidate_id=str(published.get("candidate_id") or ""),
                variable=req.variable,
                hypothesis=req.hypothesis,
                before_value=req.before_value,
                after_value=req.after_value,
                planned_days=planned_days,
                baseline_inquiries=int(performance.get("inquiries") or 0),
                baseline_paid_orders=int(performance.get("paid_orders") or 0),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        try:
            work_order = work_order_manager.create_work_order(
                kind="listing_experiment",
                title=f"单变量实验: {req.variable} / {item_id}",
                owner="resale",
                context={
                    "commercial_trial_id": trial_id,
                    "experiment_id": experiment["experiment_id"],
                    "cookie_id": trial.get("cookie_id", ""),
                    "item_id": item_id,
                    "candidate_id": str(published.get("candidate_id") or ""),
                    "variable": req.variable,
                },
                risk_level="high",
            )
            work_order_manager.create_action_plan(
                work_order["work_order_id"],
                proposed_actions=[{
                    "agent": "listing",
                    "action": "edit_listing",
                    "description": "提交单变量商品编辑，完成后仍需同步或凭证确认",
                    "params": {
                        "commercial_trial_id": trial_id,
                        "experiment_id": experiment["experiment_id"],
                        "cookie_id": trial.get("cookie_id", ""),
                        "item_id": item_id,
                        "candidate_id": str(published.get("candidate_id") or ""),
                        "variable": req.variable,
                        "before_value": req.before_value,
                        "after_value": req.after_value,
                    },
                    "draft_snapshot": {
                        "item_id": item_id,
                        "variable": req.variable,
                        "before_value": req.before_value,
                        "after_value": req.after_value,
                        "cookie_id": trial.get("cookie_id", ""),
                    },
                }],
            )
            work_order = work_order_manager.get_work_order(work_order["work_order_id"])
            approval = next(
                (item for item in work_order.get("approvals") or [] if item.get("action") == "edit_listing"),
                None,
            )
            if not approval:
                raise RuntimeError("edit_listing approval was not created")
            commercial_trial_store.link_experiment_execution(
                trial_id,
                experiment["experiment_id"],
                work_order_id=work_order["work_order_id"],
                approval_id=str(approval.get("approval_id") or ""),
            )
        except Exception as exc:
            commercial_trial_store.cancel_experiment(
                trial_id,
                experiment["experiment_id"],
                "实验编辑审批创建失败",
            )
            raise HTTPException(status_code=500, detail=f"Experiment approval creation failed: {exc}")
        reconciled = _reconcile_commercial_trial(commercial_trial_store.get_trial(trial_id) or {})
        linked = next(
            (item for item in reconciled.get("experiments") or [] if item.get("experiment_id") == experiment["experiment_id"]),
            experiment,
        )
        return {
            "ok": True,
            "experiment": linked,
            "work_order": work_order,
            "approval_required": True,
            "trial": reconciled,
        }


def _commercial_experiment_approval(experiment: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    approval_id = str(experiment.get("approval_id") or "")
    work_order_id = str(experiment.get("work_order_id") or "")
    if not approval_id or not work_order_id:
        return None
    try:
        work_order = work_order_manager.get_work_order(work_order_id)
    except KeyError:
        return None
    return next(
        (item for item in work_order.get("approvals") or [] if str(item.get("approval_id") or "") == approval_id),
        None,
    )


def _complete_commercial_experiment_approval(approval_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return work_order_manager.complete_approval_manually(approval_id, result)
    except ActionScopeValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "experiment_activation_blocked", "reason": exc.code},
        ) from exc


@app.post("/commercial-trials/{trial_id}/experiments/{experiment_id}/activate")
def activate_commercial_item_experiment(
    trial_id: str,
    experiment_id: str,
    req: CommercialExperimentActivationRequest,
) -> Dict[str, Any]:
    """Confirm the platform change, then and only then start experiment time."""
    with commercial_trial_action_lock:
        trial = commercial_trial_store.get_trial(trial_id)
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        reconciled = _reconcile_commercial_trial(trial)
        if reconciled.get("stop_required") or str(reconciled.get("lifecycle") or "") != "active":
            raise HTTPException(
                status_code=409,
                detail={"error": "experiment_activation_blocked", "reason": "trial_not_active_or_stop_required"},
            )
        experiment = next(
            (item for item in reconciled.get("experiments") or [] if item.get("experiment_id") == experiment_id),
            None,
        )
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if str(experiment.get("status") or "") == "active":
            return {"ok": True, "idempotent": True, "experiment": experiment, "trial": reconciled}
        if str(experiment.get("status") or "") != "proposed":
            raise HTTPException(status_code=409, detail="Only proposed experiments can be activated")
        approval = _commercial_experiment_approval(experiment)
        if not approval or str(approval.get("status") or "") != "approved":
            raise HTTPException(
                status_code=409,
                detail={"error": "experiment_activation_blocked", "reason": "edit_approval_not_approved"},
            )

        cookie_id = str(trial.get("cookie_id") or "")
        item_id = str(experiment.get("item_id") or "")
        variable = str(experiment.get("variable") or "")
        sync_state = db_manager.get_account_sync_state(cookie_id).get("listings") or {}
        last_sync_epoch = float(sync_state.get("last_synced_epoch") or 0.0)
        if not bool(sync_state.get("fresh")) or last_sync_epoch < float(experiment.get("proposed_at") or 0):
            raise HTTPException(
                status_code=409,
                detail={"error": "experiment_activation_blocked", "reason": "listing_sync_after_proposal_required"},
            )
        current_item = next(
            (item for item in db_manager.get_items_by_cookie(cookie_id) if str(item.get("item_id") or "") == item_id),
            None,
        )
        if current_item is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "experiment_activation_blocked", "reason": "item_missing_from_latest_sync"},
            )

        if variable == "title":
            confirmed = str(current_item.get("title") or "").strip() == str(experiment.get("after_value") or "").strip()
        elif variable == "price":
            try:
                confirmed = abs(float(current_item.get("price")) - float(experiment.get("after_value"))) <= 0.01
            except (TypeError, ValueError):
                confirmed = False
        else:
            confirmed = False

        manual_mode = variable not in {"title", "price"}
        if manual_mode:
            if not req.manual_confirmation or len(req.evidence_note.strip()) < 8 or len(req.evidence_ref.strip()) < 2:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "experiment_activation_blocked", "reason": "manual_edit_evidence_required"},
                )
            if (
                str(approval.get("execution_status") or "") != "completed"
                or not bool(experiment.get("approval_execution_ok"))
            ):
                _complete_commercial_experiment_approval(
                    str(approval.get("approval_id") or ""),
                    {
                        "ok": True,
                        "action": "edit_listing",
                        "item_id": item_id,
                        "variable": variable,
                        "evidence_note": req.evidence_note.strip()[:500],
                        "evidence_ref": req.evidence_ref.strip()[:300],
                    },
                )
            mode = "manual"
            note = req.evidence_note
        else:
            if not confirmed:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "experiment_activation_blocked", "reason": "synced_value_does_not_match_proposal"},
                )
            automated_edit_succeeded = (
                str(experiment.get("job_status") or "") == "completed"
                and bool(experiment.get("job_result_ok"))
            )
            if not automated_edit_succeeded and not req.manual_confirmation:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "experiment_activation_blocked", "reason": "edit_execution_not_completed"},
                )
            if not automated_edit_succeeded:
                _complete_commercial_experiment_approval(
                    str(approval.get("approval_id") or ""),
                    {
                        "ok": True,
                        "action": "edit_listing",
                        "item_id": item_id,
                        "variable": variable,
                        "evidence_note": req.evidence_note.strip()[:500]
                        or "operator edit confirmed by the latest listing sync",
                        "evidence_ref": req.evidence_ref.strip()[:300]
                        or f"account_sync:listings:{item_id}",
                    },
                )
            mode = "synced"
            note = req.evidence_note.strip() or f"latest listing sync confirms {variable}"

        try:
            activated = commercial_trial_store.activate_experiment(
                trial_id,
                experiment_id,
                mode=mode,
                note=note,
                evidence_ref=req.evidence_ref.strip() or f"account_sync:listings:{item_id}",
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {
            "ok": True,
            "idempotent": False,
            "experiment": activated,
            "trial": _reconcile_commercial_trial(commercial_trial_store.get_trial(trial_id) or {}),
        }


@app.post("/commercial-trials/{trial_id}/experiments/{experiment_id}/cancel")
def cancel_commercial_item_experiment(
    trial_id: str,
    experiment_id: str,
    req: CommercialExperimentCancelRequest,
) -> Dict[str, Any]:
    with commercial_trial_action_lock:
        trial = commercial_trial_store.get_trial(trial_id)
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        reconciled = _reconcile_commercial_trial(trial)
        experiment = next(
            (item for item in reconciled.get("experiments") or [] if item.get("experiment_id") == experiment_id),
            None,
        )
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if str(experiment.get("status") or "") != "proposed":
            raise HTTPException(status_code=409, detail="Only proposed experiments can be cancelled")

        approval = _commercial_experiment_approval(experiment)
        approval_id = str(experiment.get("approval_id") or "")
        jobs = execution_queue.get_jobs_for_approval(approval_id) if approval_id else []
        if any(str(job.get("status") or "") == "running" for job in jobs):
            raise HTTPException(
                status_code=409,
                detail={"error": "experiment_cancel_blocked", "reason": "edit_execution_is_running"},
            )
        successful_jobs = [
            job
            for job in jobs
            if str(job.get("status") or "") == "completed"
            and isinstance(job.get("result"), Mapping)
            and bool(job["result"].get("ok"))
        ]
        approval_result = approval.get("execution_result") if isinstance(approval, Mapping) else {}
        if successful_jobs or (
            isinstance(approval_result, Mapping)
            and bool(approval_result.get("ok"))
            and str((approval or {}).get("execution_status") or "") == "completed"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "experiment_cancel_blocked",
                    "reason": "platform_change_already_submitted_activate_or_reconcile_first",
                },
            )
        queued_job_ids = [
            str(job.get("job_id") or "")
            for job in jobs
            if str(job.get("status") or "") == "queued"
        ]
        for job_id in queued_job_ids:
            execution_queue.cancel_job(job_id)
        if approval and str(approval.get("status") or "") in {"pending", "approved"}:
            work_order_manager.decide_approval(
                str(approval.get("approval_id") or ""),
                "rejected",
                note=f"实验提案取消：{req.note}",
            )
        try:
            cancelled = commercial_trial_store.cancel_experiment(trial_id, experiment_id, req.note)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {
            "ok": True,
            "experiment": cancelled,
            "cancelled_jobs": len(queued_job_ids),
            "trial": _reconcile_commercial_trial(commercial_trial_store.get_trial(trial_id) or {}),
        }


@app.post("/commercial-trials/{trial_id}/experiments/{experiment_id}/decision")
def decide_commercial_item_experiment(
    trial_id: str,
    experiment_id: str,
    req: CommercialExperimentDecisionRequest,
) -> Dict[str, Any]:
    """Close an experiment using the latest post-change evidence snapshot."""
    trial = commercial_trial_store.get_trial(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    reconciled = _reconcile_commercial_trial(trial)
    experiment = next(
        (item for item in reconciled.get("experiments") or [] if str(item.get("experiment_id") or "") == experiment_id),
        None,
    )
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    performance = next(
        (
            item for item in reconciled.get("item_performance") or []
            if str(item.get("item_id") or "") == str(experiment.get("item_id") or "")
        ),
        {},
    )
    try:
        decision = commercial_trial_store.decide_experiment(
            trial_id,
            experiment_id,
            decision=req.decision,
            note=req.note,
            current_inquiries=int(performance.get("inquiries") or 0),
            current_paid_orders=int(performance.get("paid_orders") or 0),
            allow_early=req.allow_early,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Experiment not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "ok": True,
        "experiment": decision,
        "trial": _reconcile_commercial_trial(commercial_trial_store.get_trial(trial_id) or {}),
    }


@app.post("/commercial-trials/{trial_id}/candidates/{candidate_id}")
def attach_trial_candidate(trial_id: str, candidate_id: str) -> Dict[str, Any]:
    """Attach a candidate to a trial.

    Validates candidate existence, exact category match, uniqueness, and the
    max_candidates cap.
    """
    trial = commercial_trial_store.get_trial(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    candidate = resale_manager.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if str(candidate.get("category") or "").strip() != str(trial.get("category") or "").strip():
        raise HTTPException(status_code=409, detail="Candidate category does not match trial category")
    if candidate_id in trial.get("candidate_ids", []):
        raise HTTPException(status_code=409, detail="Candidate already attached")
    if len(trial.get("candidate_ids", [])) >= int(trial.get("controls", {}).get("max_candidates") or 0):
        raise HTTPException(status_code=409, detail="Trial candidate cap reached")
    try:
        updated = commercial_trial_store.attach_candidate(trial_id, candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _reconcile_commercial_trial(updated)


@app.post("/commercial-trials/{trial_id}/decision")
def decide_commercial_trial(trial_id: str, req: CommercialTrialDecisionRequest) -> Dict[str, Any]:
    """Record an operator lifecycle decision.

    ``complete`` requires the monitor window to have elapsed or profit to be
    reconciled. ``pause``/``stop`` block new batch draft creation.
    """
    trial = commercial_trial_store.get_trial(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    reconciled = _reconcile_commercial_trial(trial)
    allow_complete = bool(reconciled.get("monitor_elapsed")) or any(
        stage.get("code") == "profit_reconciled" and stage.get("status") == "pass"
        for stage in reconciled.get("stages", [])
    )
    try:
        updated = commercial_trial_store.decide(
            trial_id,
            req.decision,
            req.note,
            allow_complete=allow_complete,
            review_eligible=bool(reconciled.get("review_eligible")) or req.decision.strip().lower() == "stop",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _reconcile_commercial_trial(updated)


@app.post("/commercial-trials/{trial_id}/report")
def commercial_trial_report(trial_id: str) -> Dict[str, Any]:
    """Generate a local Markdown trial report. No remote side effects."""
    trial = commercial_trial_store.get_trial(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")
    reconciled = _reconcile_commercial_trial(trial)
    reports_dir = settings.data_dir / "commercial_trial_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = reports_dir / f"{trial_id}.md"
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(render_trial_markdown(reconciled), encoding="utf-8")
    temp_path.replace(output_path)
    return {
        "ok": True,
        "file": str(output_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }


@app.post("/commercial-trials/{trial_id}/candidates/{candidate_id}/listing-draft")
def commercial_trial_listing_draft(trial_id: str, candidate_id: str) -> Dict[str, Any]:
    """Create a batch listing draft through the shared strict side-effect path.

    Validates membership, lifecycle, stop_required, and max_published BEFORE
    sharing the existing strict preflight/draft/WorkOrder implementation.
    """
    with commercial_trial_action_lock:
        trial = commercial_trial_store.get_trial(trial_id)
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        if candidate_id not in trial.get("candidate_ids", []):
            raise HTTPException(status_code=409, detail="Candidate is not attached to this trial")
        if str(trial.get("lifecycle") or "") != "active":
            raise HTTPException(
                status_code=409,
                detail={"error": "batch_listing_draft_blocked", "reason": f"lifecycle_{trial.get('lifecycle')}"},
            )

        reconciled = _reconcile_commercial_trial(trial)
        if reconciled.get("stop_required"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "batch_listing_draft_blocked",
                    "reason": "stop_required",
                    "stop_reasons": reconciled.get("stop_reasons", []),
                },
            )
        if candidate_id in set(reconciled.get("committed_candidate_ids") or []):
            raise HTTPException(
                status_code=409,
                detail={"error": "batch_listing_draft_blocked", "reason": "candidate_already_committed"},
            )
        max_published = int(reconciled.get("controls", {}).get("max_published") or 1)
        if int(reconciled.get("publish_commitment_count") or 0) >= max_published:
            raise HTTPException(
                status_code=409,
                detail={"error": "batch_listing_draft_blocked", "reason": "max_publish_commitments_reached"},
            )
        candidate = resale_manager.get_candidate(candidate_id) or {}
        try:
            unit_cash_required = round(
                float(candidate.get("supplier_price") or 0) + float(candidate.get("supplier_shipping") or 0),
                2,
            )
        except (TypeError, ValueError):
            unit_cash_required = float("nan")
        if not math.isfinite(unit_cash_required) or unit_cash_required <= 0:
            raise HTTPException(
                status_code=409,
                detail={"error": "batch_listing_draft_blocked", "reason": "candidate_cash_requirement_invalid"},
            )
        remaining_cash = float(reconciled.get("remaining_cash_budget") or 0)
        if unit_cash_required > remaining_cash:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "batch_listing_draft_blocked",
                    "reason": "insufficient_cash_budget",
                    "unit_cash_required": unit_cash_required,
                    "remaining_cash_budget": remaining_cash,
                },
            )
        return _strict_listing_draft_side_effects(candidate_id, trial.get("cookie_id", ""))


class CommercialTrialFulfillmentHandoffRequest(BaseModel):
    quantity: int = Field(default=1, ge=1, le=100)
    buyer_address_summary: str = Field(min_length=2, max_length=80)
    specs_confirmed: bool = False
    address_confirmed: bool = False
    supplier_stock_reconfirmed: bool = False
    supplier_quote_reconfirmed: bool = False
    operator_note: str = Field(default="", max_length=500)


def _sanitized_runtime_order(order: Mapping[str, Any]) -> Dict[str, Any]:
    lock = order.get("fulfillment_lock") if isinstance(order.get("fulfillment_lock"), Mapping) else {}
    return {
        "order_id": str(order.get("order_id") or ""),
        "cookie_id": str(order.get("cookie_id") or ""),
        "item_id": str(order.get("item_id") or ""),
        "status": str(order.get("status") or ""),
        "amount": round(float(order.get("amount") or 0), 2),
        "locked": bool(lock.get("locked")),
        "lock_owner": str(lock.get("owner") or "") if bool(lock.get("locked")) else "",
        "operator_next_action": str(order.get("operator_next_action") or ""),
        "pending_blocker": str(order.get("pending_blocker") or "")[:200],
    }


def _commercial_handoff_response(
    trial_id: str,
    handoff: Mapping[str, Any],
    *,
    idempotent: bool,
) -> Dict[str, Any]:
    try:
        runtime_order = order_runtime_store.get_order(str(handoff.get("order_id") or ""))
    except KeyError:
        runtime_order = {"order_id": handoff.get("order_id", ""), "status": "missing"}
    return {
        "ok": True,
        "idempotent": idempotent,
        "handoff": dict(handoff),
        "runtime_order": _sanitized_runtime_order(runtime_order),
        "purchase_draft": resale_manager.get_purchase_draft(str(handoff.get("purchase_draft_id") or "")),
        "work_order": work_order_manager.get_work_order(str(handoff.get("work_order_id") or "")),
        "trial": _reconcile_commercial_trial(commercial_trial_store.get_trial(trial_id) or {}),
        "message": "履约已交接到采购审批。批准后只会生成手工采购任务，不会自动付款或下单。",
    }


@app.post("/commercial-trials/{trial_id}/orders/{order_id}/fulfillment-handoff")
def create_commercial_trial_fulfillment_handoff(
    trial_id: str,
    order_id: str,
    req: CommercialTrialFulfillmentHandoffRequest,
) -> Dict[str, Any]:
    """Bind one observed paid order to a locked, approval-gated purchase flow."""
    confirmations = (
        req.specs_confirmed,
        req.address_confirmed,
        req.supplier_stock_reconfirmed,
        req.supplier_quote_reconfirmed,
    )
    if not all(confirmations):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "fulfillment_handoff_blocked",
                "reason": "required_confirmations_missing",
            },
        )

    with commercial_trial_action_lock:
        trial = commercial_trial_store.get_trial(trial_id)
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        existing = commercial_trial_store.fulfillment_handoff_for_order(trial_id, order_id)
        if existing:
            return _commercial_handoff_response(trial_id, existing, idempotent=True)

        reconciled = _reconcile_commercial_trial(trial)
        order_link = next(
            (link for link in reconciled.get("order_links") or [] if str(link.get("order_id") or "") == order_id),
            None,
        )
        if not order_link:
            raise HTTPException(
                status_code=409,
                detail={"error": "fulfillment_handoff_blocked", "reason": "order_not_in_trial_paid_evidence"},
            )
        if str(order_link.get("status") or "") not in {"paid", "pending_shipment"}:
            raise HTTPException(
                status_code=409,
                detail={"error": "fulfillment_handoff_blocked", "reason": "order_not_waiting_for_fulfillment"},
            )
        if reconciled.get("stop_required"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "fulfillment_handoff_blocked",
                    "reason": "stop_required",
                    "stop_reasons": reconciled.get("stop_reasons", []),
                },
            )

        candidate_id = str(order_link.get("candidate_id") or "")
        candidate = resale_manager.get_candidate(candidate_id) or {}
        if not candidate:
            raise HTTPException(status_code=409, detail="Trial order candidate is missing")
        for resale_order in resale_manager.list_resale_orders():
            if str(resale_order.get("xianyu_order_id") or "") == order_id:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "fulfillment_handoff_blocked", "reason": "order_already_in_reconciliation"},
                )

        try:
            supplier_price = float(candidate.get("supplier_price") or 0)
            supplier_shipping = float(candidate.get("supplier_shipping") or 0)
        except (TypeError, ValueError):
            supplier_price = float("nan")
            supplier_shipping = float("nan")
        total_cost = round(supplier_price * req.quantity + supplier_shipping, 2)
        if not math.isfinite(total_cost) or total_cost <= 0:
            raise HTTPException(
                status_code=409,
                detail={"error": "fulfillment_handoff_blocked", "reason": "procurement_cost_invalid"},
            )
        current_fallback = supplier_price + supplier_shipping
        projected_exposure = round(
            max(0.0, float(reconciled.get("exposure") or 0) - current_fallback) + total_cost,
            2,
        )
        cash_budget = float(reconciled.get("controls", {}).get("cash_budget") or 0)
        if projected_exposure > cash_budget:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "fulfillment_handoff_blocked",
                    "reason": "procurement_budget_exceeded",
                    "projected_exposure": projected_exposure,
                    "cash_budget": cash_budget,
                },
            )

        lock_owner = f"commercial_trial:{trial_id}"
        runtime_order = order_runtime_store.get_order(order_id)
        runtime_lock = runtime_order.get("fulfillment_lock") if isinstance(runtime_order.get("fulfillment_lock"), Mapping) else {}
        if runtime_lock.get("locked") and str(runtime_lock.get("owner") or "") != lock_owner:
            raise HTTPException(
                status_code=409,
                detail={"error": "fulfillment_handoff_blocked", "reason": "order_locked_by_other_operator"},
            )
        newly_acquired = not bool(runtime_lock.get("locked"))
        order_runtime_store.acquire_fulfillment_lock(
            order_id,
            owner=lock_owner,
            reason="Commercial Alpha paid-order fulfillment handoff",
        )

        try:
            plan = resale_manager.create_plan(candidate_id, {
                "mitigation_notes": f"Commercial Alpha {trial_id} paid order {order_id}",
            })
            if not plan:
                raise HTTPException(status_code=409, detail="Cannot create procurement plan for candidate")
            purchase = _create_purchase_draft_side_effects(
                plan["plan_id"],
                PurchaseDraftRequest(
                    quantity=req.quantity,
                    buyer_address_summary=req.buyer_address_summary,
                ),
                commercial_context={
                    "commercial_trial_id": trial_id,
                    "xianyu_order_id": order_id,
                    "cookie_id": trial.get("cookie_id", ""),
                    "item_id": order_link.get("item_id", ""),
                    "sale_amount": order_link.get("amount", 0),
                    "fulfillment_lock_owner": lock_owner,
                    "quantity": req.quantity,
                    "operator_note": req.operator_note,
                },
            )
            work_order = purchase.get("work_order") or {}
            approvals = work_order.get("approvals") or []
            approval_id = str(approvals[0].get("approval_id") or "") if approvals else ""
            draft = purchase.get("purchase_draft") or {}
            handoff = commercial_trial_store.record_fulfillment_handoff(
                trial_id,
                {
                    "order_id": order_id,
                    "item_id": order_link.get("item_id", ""),
                    "candidate_id": candidate_id,
                    "plan_id": plan.get("plan_id", ""),
                    "purchase_draft_id": draft.get("draft_id", ""),
                    "work_order_id": work_order.get("work_order_id", ""),
                    "approval_id": approval_id,
                    "lock_owner": lock_owner,
                    "quantity": req.quantity,
                    "total_cost": draft.get("total_cost", total_cost),
                    "operator_note": req.operator_note,
                    "created_at": time.time(),
                },
            )
        except Exception:
            if newly_acquired:
                order_runtime_store.release_fulfillment_lock(order_id, owner=lock_owner)
            raise
        return _commercial_handoff_response(trial_id, handoff, idempotent=False)


@app.post("/commercial-trials/{trial_id}/orders/{order_id}/fulfillment-release")
def release_commercial_trial_fulfillment_handoff(trial_id: str, order_id: str) -> Dict[str, Any]:
    """Release the batch fulfillment lock after platform shipment or closure evidence."""
    with commercial_trial_action_lock:
        trial = commercial_trial_store.get_trial(trial_id)
        if not trial:
            raise HTTPException(status_code=404, detail="Trial not found")
        handoff = commercial_trial_store.fulfillment_handoff_for_order(trial_id, order_id)
        if not handoff:
            raise HTTPException(status_code=404, detail="Fulfillment handoff not found")
        runtime_order = order_runtime_store.get_order(order_id)
        approval_status = "missing"
        try:
            work_order = work_order_manager.get_work_order(str(handoff.get("work_order_id") or ""))
        except KeyError:
            work_order = {}
        for approval in work_order.get("approvals") or []:
            if str(approval.get("approval_id") or "") == str(handoff.get("approval_id") or ""):
                approval_status = str(approval.get("status") or "pending")
                break
        terminal_status = str(runtime_order.get("status") or "") in {
            "shipped", "delivered", "refund_requested", "disputed", "closed",
        }
        if not terminal_status and approval_status != "rejected":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "fulfillment_release_blocked",
                    "reason": "order_not_shipped_or_closed_and_approval_not_rejected",
                },
            )
        lock_owner = str(handoff.get("lock_owner") or "")
        order_runtime_store.release_fulfillment_lock(order_id, owner=lock_owner)
        released = commercial_trial_store.release_fulfillment_handoff(trial_id, order_id)
        return {
            "ok": True,
            "handoff": released,
            "runtime_order": _sanitized_runtime_order(order_runtime_store.get_order(order_id)),
            "trial": _reconcile_commercial_trial(commercial_trial_store.get_trial(trial_id) or {}),
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
    financial_evidence_note: str = Field(default="", max_length=500)
    financial_evidence_ref: str = Field(default="", max_length=300)
    financial_reconciled_at: str = Field(default="", max_length=40)


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
    financial_evidence_note: Optional[str] = Field(default=None, max_length=500)
    financial_evidence_ref: Optional[str] = Field(default=None, max_length=300)
    financial_reconciled_at: Optional[str] = Field(default=None, max_length=40)


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


def _commercial_resale_transition_gaps(
    current: Mapping[str, Any],
    updates: Mapping[str, Any],
) -> List[str]:
    merged = {**dict(current), **dict(updates)}
    trial_id = str(merged.get("commercial_trial_id") or "").strip()
    target_status = str(merged.get("status") or "").strip()
    if not trial_id or target_status not in {"shipped", "completed"}:
        return []

    gaps: List[str] = []
    required = {
        "xianyu_order_id": "xianyu_order_id_missing",
        "purchase_task_id": "purchase_task_link_missing",
        "purchase_draft_id": "purchase_draft_link_missing",
        "candidate_id": "candidate_link_missing",
        "cookie_id": "cookie_link_missing",
        "item_id": "item_link_missing",
        "fulfillment_lock_owner": "fulfillment_lock_link_missing",
        "supplier_order_id": "supplier_order_id_missing",
        "supplier_tracking_number": "supplier_tracking_missing",
        "xianyu_tracking_number": "xianyu_tracking_missing",
    }
    for field, reason in required.items():
        if not str(merged.get(field) or "").strip():
            gaps.append(reason)
    expected_lock_owner = f"commercial_trial:{trial_id}"
    if str(merged.get("fulfillment_lock_owner") or "") != expected_lock_owner:
        gaps.append("fulfillment_lock_owner_mismatch")

    trial = commercial_trial_store.get_trial(trial_id)
    if not trial:
        gaps.append("commercial_trial_missing")
    else:
        if str(trial.get("cookie_id") or "") != str(merged.get("cookie_id") or ""):
            gaps.append("trial_account_mismatch")
        handoff = commercial_trial_store.fulfillment_handoff_for_order(
            trial_id,
            str(merged.get("xianyu_order_id") or ""),
        )
        if not handoff:
            gaps.append("fulfillment_handoff_missing")
        elif (
            str(handoff.get("item_id") or "") != str(merged.get("item_id") or "")
            or str(handoff.get("candidate_id") or "") != str(merged.get("candidate_id") or "")
        ):
            gaps.append("fulfillment_handoff_linkage_mismatch")
        elif str(handoff.get("lock_owner") or "") != expected_lock_owner:
            gaps.append("fulfillment_handoff_lock_owner_mismatch")

    purchase_task = resale_manager.get_purchase_task(str(merged.get("purchase_task_id") or ""))
    if not purchase_task:
        gaps.append("purchase_task_missing")
    else:
        for field in ("commercial_trial_id", "xianyu_order_id", "cookie_id", "item_id", "candidate_id"):
            if str(purchase_task.get(field) or "") != str(merged.get(field) or ""):
                gaps.append(f"purchase_task_{field}_mismatch")

    if target_status == "shipped" and str(current.get("status") or "") not in {"purchased", "shipped"}:
        gaps.append("order_must_be_purchased_before_shipped")

    if target_status == "completed":
        if str(current.get("status") or "") not in {"shipped", "completed"}:
            gaps.append("order_must_be_shipped_before_completed")
        if len(str(merged.get("financial_evidence_note") or "").strip()) < 8:
            gaps.append("financial_evidence_note_missing")
        if len(str(merged.get("financial_evidence_ref") or "").strip()) < 2:
            gaps.append("financial_evidence_ref_missing")
        numbers: Dict[str, Optional[float]] = {}
        for field in ("sale_amount", "supplier_cost", "shipping_cost", "platform_fee"):
            try:
                value = float(merged.get(field))
            except (TypeError, ValueError):
                value = float("nan")
            numbers[field] = value if math.isfinite(value) else None
        if numbers["sale_amount"] is None or numbers["sale_amount"] <= 0:
            gaps.append("sale_amount_invalid")
        if numbers["supplier_cost"] is None or numbers["supplier_cost"] <= 0:
            gaps.append("supplier_cost_invalid")
        if numbers["shipping_cost"] is None or numbers["shipping_cost"] < 0:
            gaps.append("shipping_cost_invalid")
        if numbers["platform_fee"] is None or numbers["platform_fee"] < 0:
            gaps.append("platform_fee_invalid")
        try:
            runtime_order = order_runtime_store.get_order(str(merged.get("xianyu_order_id") or ""))
        except (KeyError, ValueError):
            runtime_order = None
        if not runtime_order:
            gaps.append("platform_order_missing")
        else:
            delivered = str(runtime_order.get("status") or "") == "delivered" or any(
                str(item.get("new_status") or "") == "delivered"
                for item in runtime_order.get("status_history") or []
                if isinstance(item, Mapping)
            )
            if not delivered:
                gaps.append("platform_delivery_not_confirmed")
            runtime_amount = runtime_order.get("amount")
            try:
                runtime_amount = float(runtime_amount)
            except (TypeError, ValueError):
                runtime_amount = float("nan")
            if (
                not math.isfinite(runtime_amount)
                or numbers["sale_amount"] is None
                or abs(runtime_amount - numbers["sale_amount"]) > 0.01
            ):
                gaps.append("sale_amount_not_confirmed_by_platform_order")
    return list(dict.fromkeys(gaps))


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
    if updates.get("status") == "completed" and str(order.get("commercial_trial_id") or ""):
        updates["financial_reconciled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    transition_gaps = _commercial_resale_transition_gaps(order, updates)
    if transition_gaps:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "commercial_order_transition_blocked",
                "target_status": updates.get("status") or order.get("status"),
                "reasons": transition_gaps,
            },
        )
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
    cookie_id = str(payload.get("cookie_id") or "default").strip() or "default"
    cookie_value = str(payload.get("cookies") or "").strip()
    if not cookie_value:
        raise HTTPException(status_code=400, detail="cookies are required")
    result = await adapter.login_with_cookies(cookie_string=cookie_value, cookie_id=cookie_id)
    if result.get("ok") and _account_exists(cookie_id):
        db_manager.update_cookie_value(cookie_id, cookie_value)
    return result

@app.post("/browser/publish")
async def browser_publish(request: Request) -> Dict[str, Any]:
    """Compatibility route that creates a draft approval, never a direct publish."""
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
        return {
            "ok": True,
            "draft": draft.to_dict(),
            "publish_result": None,
            "approval_required": False,
            "executed": False,
        }

    cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
    return _create_publish_listing_work_order(
        draft.to_dict(),
        cookie_id=cookie_id,
        kind="browser_listing",
    )

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
    """Compatibility route that creates a message approval, never a direct send."""
    payload = await request.json()
    buyer_id = str(payload.get("buyer_id") or "").strip()
    if not buyer_id:
        raise HTTPException(status_code=400, detail="buyer_id is required")
    cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
    return await send_message_endpoint(
        buyer_id,
        SendMessageRequest(
            message=str(payload.get("message") or ""),
            cookie_id=cookie_id or "default",
            chat_id=str(payload.get("chat_id") or ""),
            context={"source": "browser_compatibility"},
            risk_level=str(payload.get("risk_level") or "normal"),
        ),
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
    cookie_id = _resolve_enabled_operator_cookie_id(str(payload.get("cookie_id") or ""))
    items = await adapter.search_products(
        keyword=payload.get("keyword", ""),
        max_pages=payload.get("max_pages", 1),
        min_price=payload.get("min_price", 0),
        max_price=payload.get("max_price", 0),
        cookie_id=cookie_id or "default",
    )
    return {"ok": True, "count": len(items), "items": items}

@app.delete("/browser/item/{item_id}")
async def browser_delete_item(item_id: str, cookie_id: str = "default") -> Dict[str, Any]:
    """Compatibility route that creates a deletion approval, never a direct delete."""
    resolved_cookie_id = _resolve_enabled_operator_cookie_id(cookie_id)
    return await delete_item_endpoint(
        item_id,
        DeleteItemRequest(
            reason="Requested through browser compatibility route",
            cookie_id=resolved_cookie_id or "default",
            context={"source": "browser_compatibility"},
        ),
    )


@app.get("/adapter/sync-preview")
def adapter_sync_preview(cookie_id: str = "default", status: str = "") -> Dict[str, Any]:
    """Preview provider-neutral order/listing sync through the active adapter.

    Read-only: this route does not persist synced data, enqueue jobs, publish,
    send messages, fulfill orders, or mutate platform state.
    """
    adapter = workflow.adapter
    orders = adapter.sync_orders(cookie_id=cookie_id, status=status)
    listings = adapter.sync_my_listings(cookie_id=cookie_id)
    return {
        "ok": bool(orders.get("ok")) and bool(listings.get("ok")),
        "adapter": getattr(adapter, "name", "unknown"),
        "cookie_id": cookie_id,
        "status": status,
        "orders": orders,
        "listings": listings,
    }


def _capture_synced_commercial_metrics(cookie_id: str) -> Dict[str, Any]:
    """Persist only adapter counters explicitly marked as available."""
    rows = {
        str(item.get("item_id") or ""): item
        for item in db_manager.get_items_by_cookie(cookie_id)
        if str(item.get("item_id") or "") and bool(item.get("metrics_available"))
    }
    captured: List[str] = []
    skipped: List[Dict[str, str]] = []
    for trial in commercial_trial_store.active_trials():
        if str(trial.get("cookie_id") or "") != cookie_id:
            continue
        reconciled = _reconcile_commercial_trial(trial)
        for published in reconciled.get("published_items") or []:
            item_id = str(published.get("item_id") or "")
            row = rows.get(item_id)
            if not row:
                continue
            try:
                observed_at = float(row.get("metrics_observed_at") or 0)
                published_at = time.mktime(
                    time.strptime(str(published.get("published_at") or ""), "%Y-%m-%dT%H:%M:%S")
                )
                if observed_at < published_at:
                    raise ValueError("synced metrics predate publish evidence")
                observation = commercial_trial_store.record_traffic_observation(
                    str(trial.get("trial_id") or ""),
                    item_id=item_id,
                    candidate_id=str(published.get("candidate_id") or ""),
                    views=int(row.get("browse_count") or 0),
                    wants=int(row.get("want_count") or 0),
                    source="adapter",
                    evidence_note="adapter listing sync",
                    evidence_ref=f"account_sync:listings:{item_id}",
                    observed_at=observed_at,
                )
                captured.append(str(observation.get("observation_id") or ""))
            except (TypeError, ValueError) as exc:
                skipped.append({"item_id": item_id, "reason": str(exc)[:120]})
    return {
        "captured": len(set(captured)),
        "observation_ids": sorted(set(captured)),
        "skipped": skipped,
    }


@app.post("/adapter/sync-persist")
def adapter_sync_persist(cookie_id: str = "default", status: str = "") -> Dict[str, Any]:
    """Sync orders/listings through the active adapter and persist a local safe snapshot."""
    result = sync_account_snapshot(
        db=db_manager,
        adapter=workflow.adapter,
        cookie_id=cookie_id,
        status=status,
        order_runtime_store=order_runtime_store,
    )
    if result.get("ok"):
        account_runtime_store.record_authenticated_activity(cookie_id)
    else:
        public_errors = [
            str((result.get(resource) or {}).get("error") or "").strip()
            for resource in ("orders", "listings")
        ]
        error_summary = "; ".join(error for error in public_errors if error) or "account sync failed"
        auth_markers = ("login", "auth", "token", "cookie", "expired", "unauthorized", "forbidden")
        verification_reason = (
            "account authentication requires operator attention"
            if any(marker in error_summary.lower() for marker in auth_markers)
            else ""
        )
        account_runtime_store.record_error(
            cookie_id,
            error_summary,
            pending_verification_reason=verification_reason,
        )
    if bool((result.get("listings") or {}).get("ok")):
        result["commercial_trial_metrics"] = _capture_synced_commercial_metrics(cookie_id)
    return result


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
        discovery_inbox_store=discovery_inbox_store,
        order_runtime_store=order_runtime_store,
        account_profile_provider=lambda: build_account_profiles(db_manager),
        adapter_health_provider=_current_adapter_health,
        message_import_scheduler_provider=lambda: message_import_scheduler_store.snapshot(),
    )


def _current_adapter_health() -> Dict[str, Any]:
    """Return current execution adapter health without exposing secrets."""
    adapter = workflow.adapter
    sync_capabilities = {
        "orders": callable(getattr(adapter, "sync_orders", None)),
        "listings": callable(getattr(adapter, "sync_my_listings", None)),
    }
    if hasattr(adapter, "preflight"):
        health = adapter.preflight()
        health.setdefault("sync_capabilities", sync_capabilities)
        return health
    adapter_name = getattr(adapter, "name", "unknown")
    return {
        "adapter": adapter_name,
        "configured": True,
        "ready": adapter_name in {"stub", "browser"},
        "missing": [],
        "sync_capabilities": sync_capabilities,
        "notes": [
            "stub writes local outbox payloads only" if adapter_name == "stub" else "adapter is loaded",
        ],
    }


def _account_queues_snapshot() -> Dict[str, Any]:
    """Build the current per-account operator queue snapshot."""
    account_profiles = build_account_profiles(db_manager)
    cookie_ids = [str(profile.get("cookie_id") or "") for profile in account_profiles if profile.get("cookie_id")]
    return build_account_ops_queues(
        account_profiles=account_profiles,
        prompt_profile_provider=lambda cookie_id: build_prompt_profile(
            db_manager.get_ai_reply_settings(cookie_id) or {}
        ),
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
        adapter_health=_current_adapter_health(),
        account_runtime_states=account_runtime_store.list_public_states(cookie_ids),
    )


@app.get("/dashboard/opc-daily")
async def get_opc_daily_dashboard() -> Dict[str, Any]:
    """Aggregate OPC daily dashboard. Read-only, no side effects."""
    return _opc_dashboard().get_opc_daily()


@app.get("/dashboard/account-queues")
async def get_dashboard_account_queues() -> Dict[str, Any]:
    """Return per-account operator queues. Read-only, no side effects."""
    return _account_queues_snapshot()


@app.get("/dashboard/first-run-readiness")
async def get_dashboard_first_run_readiness() -> Dict[str, Any]:
    """Return readiness gates for the first controlled real-account loop."""
    account_profiles = build_account_profiles(db_manager)
    return build_first_run_readiness(
        account_profiles=account_profiles,
        account_queues=_account_queues_snapshot(),
        adapter_health=_current_adapter_health(),
    )


@app.get("/dashboard/account-queues/unassigned-work")
def get_dashboard_unassigned_work(limit: int = 500) -> Dict[str, Any]:
    """Return a sanitized, versioned review before any legacy work is resolved."""
    return review_unassigned_work(
        work_order_manager=work_order_manager,
        execution_queue=execution_queue,
        message_intake_ledger=message_intake_ledger,
        account_ids=db_manager.get_all_cookies().keys(),
        limit=limit,
    )


@app.post("/dashboard/account-queues/repair-attribution")
def post_dashboard_account_attribution_repair(req: AccountAttributionRepairRequest) -> Dict[str, Any]:
    """Preview or explicitly claim/quarantine selected unassigned WorkOrders."""
    with account_attribution_action_lock:
        result = repair_work_order_account_attribution(
            work_order_manager=work_order_manager,
            execution_queue=execution_queue,
            message_intake_ledger=message_intake_ledger,
            account_ids=db_manager.get_all_cookies().keys(),
            target_cookie_id=req.cookie_id,
            dry_run=req.dry_run,
            limit=req.limit,
            mode=req.mode,
            work_order_ids=req.work_order_ids,
            preview_token=req.preview_token,
            confirmation=req.confirmation,
        )
    if not result.get("ok"):
        detail = {
            "error": "unassigned_work_resolution_blocked",
            "reason": result.get("skipped_reason") or "unknown",
        }
        for key in ("expected_confirmation", "blocked_work_order_ids", "blocked_job_ids"):
            if result.get(key):
                detail[key] = result[key]
        raise HTTPException(status_code=409, detail=detail)
    return result


@app.post("/dashboard/weekly-review")
async def post_weekly_review() -> Dict[str, Any]:
    """Generate and save a weekly review Markdown snapshot. Local file only."""
    reviews_dir = settings.data_dir / "reviews"
    return _opc_dashboard().generate_weekly_review(reviews_dir)


@app.post("/dashboard/daily-review")
async def post_daily_review() -> Dict[str, Any]:
    """Generate and save a daily review snapshot including account queues."""
    reviews_dir = settings.data_dir / "reviews"
    return _opc_dashboard().generate_daily_review(
        reviews_dir,
        account_queues=_account_queues_snapshot(),
    )


# ---------------------------------------------------------------------------
# Legacy Trial Run (Phase 7, deprecated by evidence-derived Phase 19)
# ---------------------------------------------------------------------------

from .trial_run import TrialRun, save_trial_state

_trial: Optional[TrialRun] = None


@app.get("/trial-run/status", deprecated=True)
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


@app.post("/trial-run/init", deprecated=True)
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


@app.post("/trial-run/complete-step/{step}", deprecated=True)
async def complete_trial_step(step: int, output: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Mark a trial run step as completed. Does not execute the step."""
    global _trial
    if _trial is None:
        raise HTTPException(status_code=400, detail="Trial run not initialized")
    _trial.complete_step(step, output)
    save_trial_state(settings.data_dir / "trial_run_state.json", _trial)
    return {"ok": True, "trial": _trial.get_progress()}


@app.post("/trial-run/report", deprecated=True)
async def generate_trial_report() -> Dict[str, Any]:
    """Generate trial run report. Read-only aggregation."""
    global _trial
    if _trial is None:
        raise HTTPException(status_code=400, detail="Trial run not initialized")

    candidates = resale_manager.list_candidates() if resale_manager else []
    orders = resale_manager.list_resale_orders() if resale_manager and hasattr(resale_manager, "list_resale_orders") else []
    reviews_dir = settings.data_dir / "reviews"
    return _trial.generate_report(candidates, orders, reviews_dir)


# ---------------------------------------------------------------------------
# Commerce pipeline (WP03/WP05/WP06/WP09): supplier quotes -> match ->
# contribution economics -> opportunity -> decision card.
# Namespaced under /commerce to avoid colliding with the legacy
# /opportunities virtual-product routes above.
# ---------------------------------------------------------------------------


def _commerce_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    import base64

    encoded = str(payload.get("content_b64") or "")
    if encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid content_b64: {exc}") from exc
    text = payload.get("content")
    if text is None:
        raise HTTPException(status_code=400, detail="provide content or content_b64")
    return str(text).encode("utf-8")


@app.post("/commerce/supplier-imports/preview")
async def preview_supplier_import(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    kind = str(payload.get("kind") or "")
    try:
        if kind == "pasted_text":
            text = payload.get("content")
            if text is None:
                text = _commerce_payload_bytes(payload).decode("utf-8")
            return supplier_import_service.preview_pasted_text(str(text))
        return supplier_import_service.preview_bytes(
            _commerce_payload_bytes(payload),
            kind=kind,
            filename=str(payload.get("filename") or ""),
        )
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/commerce/supplier-imports/{preview_id}/confirm")
async def confirm_supplier_import(preview_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return supplier_import_service.confirm(
            preview_id,
            content_sha256=str(payload.get("content_sha256") or ""),
            revision=int(payload.get("revision") or 0),
            supplier=str(payload.get("supplier") or ""),
            operator=str(payload.get("operator") or "operator"),
            include_needs_confirmation=bool(payload.get("include_needs_confirmation", False)),
        )
    except ConfirmMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/commerce/supplier-imports")
async def list_supplier_imports(supplier: str = "") -> Dict[str, Any]:
    return {"batches": supplier_import_service.list_batches(supplier=supplier)}


@app.post("/commerce/supplier-imports/{batch_id}/revoke")
async def revoke_supplier_import(batch_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return supplier_import_service.revoke_batch(
            batch_id,
            operator=str(payload.get("operator") or "operator"),
            reason=str(payload.get("reason") or ""),
        )
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="batch not found")


@app.get("/commerce/offers")
async def list_commerce_offers(
    provider_id: str = "",
    source_item_id: str = "",
    limit: int = 100,
) -> Dict[str, Any]:
    from .providers.freshness import classify_snapshot

    records = commerce_repositories.offers.list(
        provider_id=provider_id,
        source_item_id=source_item_id,
    )
    decorated = []
    for record in records[: max(0, limit)]:
        entry = dict(record)
        entry["freshness_meta"] = classify_snapshot(entry)
        decorated.append(entry)
    return {"offers": decorated, "total": len(records)}


@app.post("/commerce/data-providers/{provider_id}/probe")
async def probe_commerce_data_provider(provider_id: str) -> Dict[str, Any]:
    """Probe one provider.

    supplier_file performs a real local fetch (repository read) and
    reports freshness/revocation effects. Online providers stay
    UNVERIFIED: probing them without credentials is refused instead of
    fabricated.
    """
    capability = provider_capability_registry.get(provider_id)
    if capability is None:
        raise HTTPException(status_code=404, detail="provider not registered")
    if provider_id == "supplier_file":
        result = supplier_file_provider.fetch_offers()
        return {
            "provider_id": provider_id,
            "capability_status": capability.get("status"),
            "fetch": {k: v for k, v in result.items() if k != "records"},
            "sample_record_count": len(result.get("records") or []),
        }
    raise HTTPException(
        status_code=409,
        detail=f"provider {provider_id} is {capability.get('status')}; probing requires verified credentials",
    )


@app.post("/commerce/product-matches")
async def create_product_match(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return product_matching_service.match_snapshots(
            str(payload.get("left_snapshot_id") or ""),
            str(payload.get("right_snapshot_id") or ""),
        )
    except MatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/commerce/product-matches/{match_id}/correction")
async def correct_product_match(match_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return product_matching_service.correct_grade(
            match_id,
            corrected_grade=str(payload.get("corrected_grade") or ""),
            operator=str(payload.get("operator") or "operator"),
            reason=str(payload.get("reason") or ""),
        )
    except MatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="match not found")


@app.post("/commerce/opportunities")
async def create_commerce_opportunity(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return opportunity_service.create_from_match(
            market_snapshot_id=str(payload.get("market_snapshot_id") or ""),
            supplier_snapshot_id=str(payload.get("supplier_snapshot_id") or ""),
            match_id=str(payload.get("match_id") or ""),
            title=str(payload.get("title") or ""),
        )
    except OpportunityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/commerce/opportunities/{opportunity_id}/evaluate")
async def evaluate_commerce_opportunity(opportunity_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return opportunity_service.evaluate(
            opportunity_id,
            platform_fee_rate=payload.get("platform_fee_rate"),
            platform_fee_fixed=payload.get("platform_fee_fixed"),
            packaging=payload.get("packaging"),
            after_sales_reserve=payload.get("after_sales_reserve"),
            attributable_costs=payload.get("attributable_costs"),
            return_probability=payload.get("return_probability"),
            return_loss_amount=payload.get("return_loss_amount"),
            expected_return_loss=payload.get("expected_return_loss"),
            scenario_overrides=payload.get("scenario_overrides"),
            target_margin=payload.get("target_margin"),
        )
    except OpportunityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/commerce/opportunities")
async def list_commerce_opportunities(status: str = "") -> Dict[str, Any]:
    return {"opportunities": opportunity_service.list_opportunities(status=status)}


@app.get("/commerce/opportunities/funnel")
async def commerce_opportunity_funnel() -> Dict[str, Any]:
    return opportunity_service.funnel()


@app.get("/commerce/data-providers")
async def list_commerce_data_providers() -> Dict[str, Any]:
    """Provider capability registry projection (WP02).

    Statuses are UNVERIFIED/CONFIGURED/VERIFIED/DEGRADED/DISABLED; no
    source is VERIFIED without >= 20 real samples with evidence.
    """
    records = provider_capability_registry.list()
    return {
        "providers": records,
        "usable_provider_ids": [r["provider_id"] for r in provider_capability_registry.usable_sources()],
        "verified_count": sum(1 for r in records if str(r.get("status")) == "VERIFIED"),
    }


@app.get("/commerce/opportunities/{opportunity_id}/decision-card")
async def commerce_decision_card(opportunity_id: str) -> Dict[str, Any]:
    try:
        return opportunity_service.decision_card(opportunity_id)
    except OpportunityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/commerce/market-observations")
async def record_market_observation(request: Request) -> Dict[str, Any]:
    """Record one manual market-side observation as an OfferSnapshot.

    These are operator-observed display prices (未接在线 API 时的人工观
    察), stored as ``market:xianyu_observation`` snapshots. They are
    observations, not transaction facts.
    """
    from .commerce.contracts import PRICE_TYPE_LIST, build_offer_snapshot as _build, utc_now as _utc_now

    payload = await request.json()
    sku = str(payload.get("sku") or "").strip()
    price = str(payload.get("price") or "").strip()
    if not sku or not price:
        raise HTTPException(status_code=400, detail="sku and price are required")
    try:
        snapshot = _build(
            provider_id="market:xianyu_observation",
            source_item_id=sku,
            source_sku_id=sku,
            observed_at=_utc_now(),
            attributes={
                key: value
                for key, value in (
                    ("name", str(payload.get("title") or "")),
                    ("spec", str(payload.get("spec") or "")),
                    ("brand", str(payload.get("brand") or "")),
                )
                if value
            },
            condition=str(payload.get("condition") or ""),
            unit=str(payload.get("unit") or ""),
            price_type=PRICE_TYPE_LIST,
            eligible_price={"state": "known", "amount": price, "currency": "CNY"},
            payload_hash=__import__("hashlib").sha256(
                f"{sku}|{price}|{payload.get('title')}|{payload.get('spec')}|{payload.get('brand')}|{payload.get('condition')}".encode("utf-8")
            ).hexdigest(),
            account_id="",
            source_ref="market:manual_observation",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored, created = commerce_repositories.offers.add(snapshot)
    return {"snapshot": stored, "created": created}


# ---------------------------------------------------------------------------
# Agent jobs (WP07): durable leased jobs with fencing and recovery. These
# routes submit/read/cancel; execution itself stays with Python workers
# (and later the Pi runner). No route here performs a platform write.
# ---------------------------------------------------------------------------


@app.post("/commerce/agent-jobs")
async def submit_agent_job(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return agent_job_store.submit(
            role=str(payload.get("role") or ""),
            goal_id=str(payload.get("goal_id") or ""),
            account_id=str(payload.get("account_id") or ""),
            input_refs=payload.get("input_refs") or [],
            input_versions=payload.get("input_versions") or {},
            priority=int(payload.get("priority") or 5),
            model_budget=payload.get("model_budget") or {},
            idempotency_key=str(payload.get("idempotency_key") or ""),
            write_required=bool(payload.get("write_required", False)),
        )
    except AgentJobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/commerce/agent-jobs")
async def list_agent_jobs(status: str = "", role: str = "", account_id: str = "") -> Dict[str, Any]:
    jobs = agent_job_store.list(status=status, role=role, account_id=account_id)
    return {"jobs": jobs, "stats": agent_job_store.stats()}


@app.get("/commerce/agent-jobs/stats")
async def agent_job_stats() -> Dict[str, Any]:
    return agent_job_store.stats()


@app.post("/commerce/agent-jobs/{job_id}/cancel")
async def cancel_agent_job(job_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return agent_job_store.cancel(
            job_id,
            actor=str(payload.get("actor") or "operator"),
            reason=str(payload.get("reason") or ""),
        )
    except AgentJobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")


@app.post("/commerce/agent-jobs/recover")
async def recover_agent_jobs() -> Dict[str, Any]:
    recovered = agent_job_store.recover_expired_leases()
    return {"recovered_count": len(recovered), "recovered": recovered}


# ---------------------------------------------------------------------------
# Operating mandates + shadow authorization (WP10). Shadow-evaluate only
# records what the policy WOULD decide; no route in this block performs
# or queues any platform action.
# ---------------------------------------------------------------------------


@app.post("/commerce/operating-mandates")
async def issue_operating_mandate(request: Request) -> Dict[str, Any]:
    from datetime import datetime as _dt

    payload = await request.json()
    try:
        valid_until_raw = str(payload.get("valid_until") or "")
        if not valid_until_raw:
            raise HTTPException(status_code=400, detail="valid_until is required")
        valid_until = _dt.fromisoformat(valid_until_raw)
        valid_from = None
        if payload.get("valid_from"):
            valid_from = _dt.fromisoformat(str(payload["valid_from"]))
        return operating_mandate_store.issue(
            operator=str(payload.get("operator") or ""),
            account_id=str(payload.get("account_id") or ""),
            action_types=payload.get("action_types") or [],
            sku_scope=payload.get("sku_scope") or [],
            category_scope=payload.get("category_scope") or [],
            price_floor=payload.get("price_floor"),
            price_ceiling=payload.get("price_ceiling"),
            per_transaction_limit=payload.get("per_transaction_limit"),
            daily_limit=payload.get("daily_limit"),
            max_actions=payload.get("max_actions"),
            valid_until=valid_until,
            valid_from=valid_from,
        )
    except MandateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid datetime: {exc}") from exc


@app.get("/commerce/operating-mandates")
async def list_operating_mandates() -> Dict[str, Any]:
    active_ids = {m["record_id"] for m in operating_mandate_store.list_active()}
    mandates = [
        {**m, "currently_active": m["record_id"] in active_ids}
        for m in operating_mandate_store.list()
    ]
    return {"mandates": mandates, "active_count": len(active_ids)}


@app.post("/commerce/operating-mandates/{mandate_id}/revoke")
async def revoke_operating_mandate(mandate_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return operating_mandate_store.revoke(
            mandate_id,
            operator=str(payload.get("operator") or ""),
            reason=str(payload.get("reason") or ""),
        )
    except MandateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/commerce/budgets")
async def budget_usage(account_id: str, bucket: str, day: str = "") -> Dict[str, Any]:
    return budget_reservation_store.usage(account_id=account_id, bucket=bucket, day=day or None)


@app.post("/commerce/authorization/shadow-evaluate")
async def shadow_evaluate(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        action = ActionRequest(
            action_type=str(payload.get("action_type") or ""),
            account_id=str(payload.get("account_id") or ""),
            sku=str(payload.get("sku") or ""),
            category=str(payload.get("category") or ""),
            amount=payload.get("amount"),
            listing_price=payload.get("listing_price"),
        )
    except MandateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    usage_today: Dict[str, float] = {}
    for mandate in operating_mandate_store.list_active():
        mandate_id = str(mandate.get("record_id"))
        account = str(mandate.get("account_id") or "")
        usage = budget_reservation_store.usage(account_id=account, bucket="mandate_daily", day=today_key())
        usage_today[mandate_id] = float(usage.get("committed_total") or 0)

    decision = evaluate_action(
        action,
        active_mandates=operating_mandate_store.list_active(),
        budget_usage_today=usage_today,
    )
    record = shadow_authorization_log.record(
        decision=decision,
        action_summary=f"{action.action_type} {action.account_id}",
    )
    return {
        "decision": decision,
        "shadow_record_id": record["record_id"],
        "mode": "shadow",
        "note": "verdict recorded only; the operator still performs every real action",
    }


@app.get("/commerce/authorization/shadow-metrics")
async def shadow_metrics() -> Dict[str, Any]:
    return shadow_authorization_log.metrics()


@app.get("/commerce/decisions")
async def list_commerce_decisions(opportunity_id: str = "", actor_role: str = "", limit: int = 100) -> Dict[str, Any]:
    records = commerce_repositories.decisions.list(opportunity_id=opportunity_id, actor_role=actor_role)
    return {"decisions": records[: max(0, limit)], "total": len(records)}


@app.get("/commerce/ops-summary")
async def commerce_ops_summary() -> Dict[str, Any]:
    """Compact WP14 ops view: store counts, provider states, job stats.

    Counts are facts about stored records, not business results.
    """
    from pathlib import Path as _P

    commerce_dir = settings.data_dir / "commerce"
    store_files = sorted(p.name for p in commerce_dir.glob("*.json")) if commerce_dir.is_dir() else []
    return {
        "stores": store_files,
        "counts": {
            "offers": len(commerce_repositories.offers.list()),
            "matches": len(commerce_repositories.matches.list()),
            "opportunities": len(commerce_repositories.opportunities.list()),
            "decisions": len(commerce_repositories.decisions.list()),
            "agent_jobs": agent_job_store.stats()["total"],
            "mandates": len(operating_mandate_store.list()),
        },
        "agent_jobs_by_status": agent_job_store.stats()["by_status"],
        "providers": [
            {"provider_id": p["provider_id"], "status": p["status"]}
            for p in provider_capability_registry.list()
        ],
        "shadow": {k: shadow_authorization_log.metrics()[k] for k in ("total", "allowed", "distinct_days")},
        "note": "operational record counts only; no business result implied",
    }


# ---------------------------------------------------------------------------
# Reply drafts (WP11): draft-only customer replies from verified facts.
# No route in this block sends anything; adoption still goes through the
# existing reply approval/execution chain.
# ---------------------------------------------------------------------------


@app.post("/commerce/reply-drafts")
async def create_reply_draft(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return reply_draft_service.draft_reply(
            cookie_id=str(payload.get("cookie_id") or ""),
            chat_id=str(payload.get("chat_id") or ""),
            item_id=str(payload.get("item_id") or ""),
            buyer_text=str(payload.get("buyer_text") or ""),
            floor=payload.get("floor"),
            manual_takeover_until=payload.get("manual_takeover_until"),
            latest_inbound_at=payload.get("latest_inbound_at"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/commerce/reply-drafts")
async def list_reply_drafts(chat_id: str = "", item_id: str = "") -> Dict[str, Any]:
    return {"drafts": reply_draft_service.list_drafts(chat_id=chat_id, item_id=item_id)}


# ---------------------------------------------------------------------------
# Pi persistent task chain: submit -> run through the real PiRpcClient ->
# structured decision -> evidence traceable from the panel. The runner
# enforces the read-only gate and tool cap programmatically; model output
# never changes business state, it only produces DecisionRecords.
# ---------------------------------------------------------------------------

PI_JOB_SESSION_ID = os.environ.get("PI_JOB_SESSION_ID") or f"pi-jobs-{PROJECT_ROOT.name}"


def _pi_job_client_factory(token_env: Dict[str, str]):
    from .pi_runtime import PiRpcClient

    env = dict(os.environ)
    env.update(token_env)
    env["PI_SESSION_ID"] = PI_JOB_SESSION_ID
    return PiRpcClient.from_env(PROJECT_ROOT, settings.data_dir, env=env)


@app.post("/commerce/pi-tasks/quote-review")
async def submit_pi_quote_review(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return pi_task_runner.submit_quote_review_job(
        idempotency_key=str(payload.get("idempotency_key") or ""),
    )


@app.post("/commerce/pi-tasks/submit")
async def submit_pi_task(request: Request) -> Dict[str, Any]:
    """Submit a job for any supported read-only analysis role."""
    payload = await request.json()
    try:
        return pi_task_runner.submit_job(
            str(payload.get("role") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/commerce/pi-tasks/run-next")
def run_next_pi_task() -> Dict[str, Any]:
    """Execute at most one queued job through the real Pi RPC client.

    Synchronous on purpose (runs in the threadpool): one analysis
    worker, one job at a time, per the fixed run boundary.
    """
    result = pi_task_runner.run_once(pi_factory=_pi_job_client_factory)
    if result is None:
        return {"outcome": "no_claimable_job", "runner": pi_task_runner.status}
    return result


@app.post("/commerce/pi-tasks/reconcile")
def reconcile_pi_tasks() -> Dict[str, Any]:
    return pi_task_runner.reconcile_task_decisions()


@app.post("/commerce/shadow-observer/tick")
def shadow_observer_tick() -> Dict[str, Any]:
    return shadow_observer.run_tick()


@app.get("/commerce/shadow-observer/status")
async def shadow_observer_status() -> Dict[str, Any]:
    return {
        "background_thread": shadow_observer_thread is not None,
        "metrics": shadow_authorization_log.metrics(),
        "note": "shadow records only; no action is ever executed by the observer",
    }


@app.get("/commerce/pi-tasks/{job_id}/evidence")
async def pi_task_evidence(job_id: str) -> Dict[str, Any]:
    """Task -> decision -> inputs traceability for the panel.

    Invalidated (late/stale) decisions are listed separately and never
    shown as valid; the persisted evidence file replays the actual
    inputs and tool events the decision was based on.
    """
    job = agent_job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    all_decisions = commerce_repositories.decisions.list()
    # invalidated set is global: an invalidation marker may come from any
    # actor, with or without this job's source_ref
    invalidated = {
        str(d.get("target_ref"))
        for d in all_decisions
        if str(d.get("decision_type")) == "decision_invalidated" and d.get("target_ref")
    }
    decisions = [
        d
        for d in all_decisions
        if str(d.get("source_ref")) == f"agent_job:{job_id}"
    ]
    valid = [d for d in decisions if d["record_id"] not in invalidated and str(d.get("decision_type")) != "decision_invalidated"]
    latest = valid[-1] if valid else None

    evidence_file_payload = None
    evidence_file_path = str(((latest or {}).get("input_versions") or {}).get("evidence_file") or "")
    if evidence_file_path:
        candidate = Path(evidence_file_path)
        if candidate.is_file():
            try:
                evidence_file_payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - unreadable evidence is reported, not hidden
                evidence_file_payload = {"error": f"evidence file unreadable: {candidate}"}
        else:
            evidence_file_payload = {"error": f"evidence file missing: {candidate}"}

    cost = (latest or {}).get("cost") or {}
    usage_known = cost.get("total_tokens") is not None
    return {
        "job": job,
        "decision_ids": [d["record_id"] for d in valid],
        "invalidated_decision_ids": sorted(invalidated),
        "latest_decision": latest,
        "pi": {
            "session_id": ((latest or {}).get("evidence_refs") or [{}])[0].get("ref_id"),
            "model": cost.get("model"),
            "cost_display": cost if usage_known else {**cost, "total_tokens": None, "display": "unknown (not zero)"},
        },
        "evidence_file": {
            "path": evidence_file_path or None,
            "inputs": (evidence_file_payload or {}).get("inputs"),
            "tool_calls": (evidence_file_payload or {}).get("tool_calls"),
            "usage": (evidence_file_payload or {}).get("usage"),
            "inputs_sha256": ((latest or {}).get("input_versions") or {}).get("inputs_sha256"),
        },
        "inputs": {"input_refs": job.get("input_refs"), "role": job.get("role")},
        "runner": pi_task_runner.status,
    }
