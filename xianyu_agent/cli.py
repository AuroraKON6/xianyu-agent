from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import Settings
from .workflow import CommerceWorkflow


def main(argv: Any = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="xianyu-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    draft_parser = subparsers.add_parser("draft", help="Create a listing draft and optionally publish it.")
    draft_parser.add_argument("text", help="Natural-language product description.")
    draft_parser.add_argument("--image", action="append", default=[], help="Owned or authorized image URL/path.")
    draft_parser.add_argument("--no-publish", action="store_true", help="Only create the draft JSON.")

    subparsers.add_parser("polish", help="Run polish task once.")

    discover_parser = subparsers.add_parser(
        "discover-github",
        help="Find high-star GitHub repos/agents and judge sellable opportunities.",
    )
    discover_parser.add_argument("--query", action="append", help="GitHub search query. Can be used more than once.")
    discover_parser.add_argument("--min-stars", type=int, help="Minimum stargazer count.")
    discover_parser.add_argument("--limit", type=int, help="Maximum repos per query.")
    discover_parser.add_argument("--no-deepseek", action="store_true", help="Use heuristic judgement only.")
    discover_parser.add_argument("--include-readme", action="store_true", help="Fetch README excerpts before judging.")

    eval_parser = subparsers.add_parser(
        "evaluate-product",
        help="Evaluate a virtual product resale opportunity and optionally ask in Feishu.",
    )
    eval_parser.add_argument("text", help="Product/source description.")
    eval_parser.add_argument("--title", help="Product title.")
    eval_parser.add_argument("--source-url", help="Source product URL.")
    eval_parser.add_argument("--cost", type=float, default=0.0, help="Acquisition cost.")
    eval_parser.add_argument("--price", type=float, default=0.0, help="Planned resale price.")
    eval_parser.add_argument("--rights-type", default="unknown", help="owned/resale_license/plr/mrr/supplier_authorized/etc.")
    eval_parser.add_argument("--rights-evidence", default="", help="License or authorization evidence.")
    eval_parser.add_argument("--market-sample", action="append", default=[], help="Competitor sample text with optional price.")
    eval_parser.add_argument("--no-deepseek", action="store_true", help="Use heuristic judgement only.")
    eval_parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu approval prompt.")

    crawl_parser = subparsers.add_parser(
        "crawl-url",
        help="Crawl a URL for downloadable candidates and evaluate Xianyu profit potential.",
    )
    crawl_parser.add_argument("url")
    crawl_parser.add_argument("--cost", type=float, default=0.0, help="Acquisition cost.")
    crawl_parser.add_argument("--price", type=float, default=0.0, help="Planned resale price.")
    crawl_parser.add_argument("--rights-type", default="unknown", help="owned/resale_license/plr/mrr/supplier_authorized/etc.")
    crawl_parser.add_argument("--rights-evidence", default="", help="License or authorization evidence.")
    crawl_parser.add_argument("--asset-rights-confirmed", action="store_true", help="Confirm promotional asset reuse rights.")
    crawl_parser.add_argument("--download", action="store_true", help="Download candidates only when resale rights are confirmed.")
    crawl_parser.add_argument("--market-sample", action="append", default=[], help="Competitor sample text with optional price.")
    crawl_parser.add_argument("--max-pages", type=int, help="Maximum same-domain pages to crawl.")
    crawl_parser.add_argument("--no-deepseek", action="store_true", help="Use heuristic judgement only.")
    crawl_parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu approval prompt.")

    decide_parser = subparsers.add_parser("decide-opportunity", help="Record buy/reject decision for an opportunity.")
    decide_parser.add_argument("opportunity_id")
    decide_parser.add_argument("decision")
    decide_parser.add_argument("--note", default="")

    purchase_parser = subparsers.add_parser("record-purchase", help="Record purchase/license proof for an opportunity.")
    purchase_parser.add_argument("opportunity_id")
    purchase_parser.add_argument("--receipt", default="")
    purchase_parser.add_argument("--license-evidence", default="")
    purchase_parser.add_argument("--source-url", default="")

    assets_parser = subparsers.add_parser("save-assets", help="Save authorized assets for an opportunity.")
    assets_parser.add_argument("opportunity_id")
    assets_parser.add_argument("--asset", action="append", default=[], help="Authorized local path or URL.")
    assets_parser.add_argument("--asset-rights-confirmed", action="store_true")

    list_parser = subparsers.add_parser("list-opportunity", help="Create a Xianyu listing from an approved opportunity.")
    list_parser.add_argument("opportunity_id")
    list_parser.add_argument("--no-publish", action="store_true")

    subparsers.add_parser("guard-status", help="Show platform guard status.")

    guard_check_parser = subparsers.add_parser("guard-check", help="Check text for verification/risk-control signals.")
    guard_check_parser.add_argument("text")

    guard_resume_parser = subparsers.add_parser("guard-resume", help="Resume automation after manual verification.")
    guard_resume_parser.add_argument("--note", default="")

    subparsers.add_parser("browser-status", help="Show local Edge DevTools browser status.")

    browser_open_parser = subparsers.add_parser("browser-open", help="Open a URL in Edge and run guard detection.")
    browser_open_parser.add_argument("url")
    browser_open_parser.add_argument("--wait-seconds", type=float, default=3.0)

    subparsers.add_parser("components-check", help="Check local OpenClaw component paths and integration entrypoints.")
    subparsers.add_parser("config-check", help="Print effective config health.")
    subparsers.add_parser("server", help="Run the FastAPI server.")

    # WebSocket auto-reply command
    ws_parser = subparsers.add_parser("ws-start", help="Start WebSocket auto-reply service.")
    ws_parser.add_argument("--cookies", required=True, help="Xianyu cookies string for authentication.")

    # Product discovery commands
    discover_parser = subparsers.add_parser(
        "discover",
        help="Search Xianyu for products matching a keyword.",
    )
    discover_parser.add_argument("keyword", help="Search keyword.")
    discover_parser.add_argument("--max-pages", type=int, default=3, help="Max pages to scrape.")
    discover_parser.add_argument("--min-price", type=float, help="Minimum price filter.")
    discover_parser.add_argument("--max-price", type=float, help="Maximum price filter.")

    discovery_task_parser = subparsers.add_parser(
        "discovery-task",
        help="Add a product discovery/monitoring task.",
    )
    discovery_task_parser.add_argument("keyword", help="Search keyword.")
    discovery_task_parser.add_argument("--name", help="Task name.")
    discovery_task_parser.add_argument("--max-pages", type=int, default=3)
    discovery_task_parser.add_argument("--min-price", type=float)
    discovery_task_parser.add_argument("--max-price", type=float)
    discovery_task_parser.add_argument("--no-feishu", action="store_true")

    subparsers.add_parser("discovery-tasks", help="List all discovery tasks.")
    subparsers.add_parser("discovery-run-all", help="Run all enabled discovery tasks.")

    review_parser = subparsers.add_parser(
        "resale-agent-review",
        help="Run real DeepSeek agent review on a resale candidate (manual smoke test).",
    )
    review_parser.add_argument(
        "--candidate-id", default=None, help="Candidate ID. If omitted, lists available candidates."
    )
    review_parser.add_argument(
        "--list", action="store_true", dest="list_candidates", help="List all candidates and exit."
    )

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "server":
        import uvicorn

        uvicorn.run("xianyu_agent.server:app", host=settings.server_host, port=settings.server_port, reload=False)
        return 0

    if args.command == "ws-start":
        import asyncio

        from .auto_reply import AutoReplyEngine
        from .llm import DeepSeekClient
        from .storage import JsonStorage
        from .xianyu_ws import XianyuWebSocket

        llm = DeepSeekClient(settings)
        storage = JsonStorage(settings.data_dir)
        engine = AutoReplyEngine(settings, llm, storage)
        ws_client = XianyuWebSocket(settings, engine, notifier=None)
        ws_client.set_cookies(args.cookies)

        print("Starting WebSocket auto-reply service...")
        print("Press Ctrl+C to stop.")
        try:
            asyncio.run(ws_client.start())
        except KeyboardInterrupt:
            print("\nStopped.")
        return 0

    # Commands that don't need a full CommerceWorkflow
    if args.command == "components-check":
        print(json.dumps(component_health(settings), ensure_ascii=False, indent=2))
        return 0
    if args.command == "config-check":
        health = {
            "deepseek_configured": bool(settings.deepseek_api_key),
            "deepseek_model": settings.deepseek_model,
            "deepseek_json_mode": settings.deepseek_json_mode,
            "deepseek_thinking_enabled": settings.deepseek_thinking_enabled,
            "deepseek_reasoning_effort": settings.deepseek_reasoning_effort,
            "deepseek_trust_env": settings.deepseek_trust_env,
            "github_token_configured": bool(settings.github_token),
            "github_trust_env": settings.github_trust_env,
            "github_discovery_enabled": settings.github_discovery_enabled,
            "github_discovery_at": settings.github_discovery_at,
            "github_discovery_min_stars": settings.github_discovery_min_stars,
            "github_discovery_limit_per_query": settings.github_discovery_limit_per_query,
            "market_search_provider": settings.market_search_provider,
            "market_search_limit": settings.market_search_limit,
            "min_profit_amount": settings.min_profit_amount,
            "min_profit_rate": settings.min_profit_rate,
            "crawler_max_pages": settings.crawler_max_pages,
            "crawler_max_downloads": settings.crawler_max_downloads,
            "crawler_max_file_mb": settings.crawler_max_file_mb,
            "platform_guard_enabled": settings.platform_guard_enabled,
            "platform_guard_min_seconds_between_actions": settings.platform_guard_min_seconds_between_actions,
            "platform_guard_max_actions_per_hour": settings.platform_guard_max_actions_per_hour,
            "platform_guard_daily_action_limit": settings.platform_guard_daily_action_limit,
            "browser_debug_port": settings.browser_debug_port,
            "browser_headless": settings.browser_headless,
            "adapter": settings.xianyu_adapter,
            "xianguanjia_configured": bool(settings.xianguanjia_base_url),
            "feishu_token_configured": bool(settings.feishu_verification_token),
            "feishu_connection": "feishu_cli_long_connection",
            "image_search_provider": settings.image_search_provider,
            "allow_external_product_images": settings.allow_external_product_images,
            "data_dir": str(settings.data_dir),
            "server": f"http://{settings.server_host}:{settings.server_port}",
            "discovery_enabled": settings.discovery_enabled,
            "discovery_at": settings.discovery_at,
            "auto_reply_enabled": settings.auto_reply_enabled,
            "auto_reply_model": settings.auto_reply_model,
            "auto_reply_human_toggle": settings.auto_reply_human_toggle,
        }
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return 0

    if args.command == "resale-agent-review":
        return _handle_resale_agent_review(settings, args)

    # Commands below require a full CommerceWorkflow
    workflow = CommerceWorkflow(settings)
    if args.command == "draft":
        result = workflow.create_listing(
            args.text,
            source="cli",
            product_images=args.image,
            publish=not args.no_publish,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "polish":
        print(json.dumps(workflow.polish_all(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "discover-github":
        result = workflow.discover_github_opportunities(
            queries=args.query,
            min_stars=args.min_stars,
            limit_per_query=args.limit,
            use_deepseek=not args.no_deepseek,
            include_readme=args.include_readme,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "evaluate-product":
        payload = {
            "text": args.text,
            "title": args.title,
            "source_url": args.source_url,
            "acquisition_cost": args.cost,
            "planned_price": args.price,
            "rights_type": args.rights_type,
            "rights_evidence": args.rights_evidence,
            "market_samples": args.market_sample,
        }
        result = workflow.evaluate_virtual_product_opportunity(
            payload,
            use_deepseek=not args.no_deepseek,
            notify=not args.no_notify,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "crawl-url":
        payload = {
            "url": args.url,
            "acquisition_cost": args.cost,
            "planned_price": args.price,
            "rights_type": args.rights_type,
            "rights_evidence": args.rights_evidence,
            "asset_rights_confirmed": args.asset_rights_confirmed,
            "download": args.download,
            "market_samples": args.market_sample,
            "max_pages": args.max_pages,
        }
        result = workflow.crawl_url_opportunity(
            payload,
            use_deepseek=not args.no_deepseek,
            notify=not args.no_notify,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "decide-opportunity":
        print(json.dumps(workflow.decide_virtual_product_opportunity(args.opportunity_id, args.decision, args.note), ensure_ascii=False, indent=2))
        return 0
    if args.command == "record-purchase":
        payload = {"receipt": args.receipt, "license_evidence": args.license_evidence, "source_url": args.source_url}
        print(json.dumps(workflow.record_virtual_product_purchase(args.opportunity_id, payload), ensure_ascii=False, indent=2))
        return 0
    if args.command == "save-assets":
        payload = {
            "asset_rights_confirmed": args.asset_rights_confirmed,
            "assets": [{"path": item} if not str(item).startswith("http") else {"url": item} for item in args.asset],
        }
        print(json.dumps(workflow.save_virtual_product_assets(args.opportunity_id, payload), ensure_ascii=False, indent=2))
        return 0
    if args.command == "list-opportunity":
        print(json.dumps(workflow.create_listing_from_opportunity(args.opportunity_id, publish=not args.no_publish), ensure_ascii=False, indent=2))
        return 0
    if args.command == "guard-status":
        print(json.dumps(workflow.platform_guard_status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "guard-check":
        print(json.dumps(workflow.platform_guard_check(args.text, context={"source": "cli"}), ensure_ascii=False, indent=2))
        return 0
    if args.command == "guard-resume":
        print(json.dumps(workflow.platform_guard_resume(args.note), ensure_ascii=False, indent=2))
        return 0
    if args.command == "browser-status":
        print(json.dumps(workflow.browser_status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "browser-open":
        print(json.dumps(workflow.browser_open_url(args.url, wait_seconds=args.wait_seconds), ensure_ascii=False, indent=2))
        return 0
    if args.command == "discover":
        import asyncio
        result = asyncio.run(workflow.run_discovery(
            keyword=args.keyword,
            max_pages=args.max_pages,
            min_price=args.min_price,
            max_price=args.max_price,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "discovery-task":
        task_data = {
            "keyword": args.keyword,
            "name": args.name or args.keyword,
            "max_pages": args.max_pages,
            "min_price": args.min_price,
            "max_price": args.max_price,
            "notify_feishu": not args.no_feishu,
        }
        print(json.dumps(workflow.add_discovery_task(task_data), ensure_ascii=False, indent=2))
        return 0
    if args.command == "discovery-tasks":
        print(json.dumps(workflow.list_discovery_tasks(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "discovery-run-all":
        import asyncio
        result = asyncio.run(workflow.run_all_discovery_tasks())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 1


def component_health(settings: Settings) -> dict:
    def check(name: str, root, expected: list) -> dict:
        root_path = root
        files = []
        for relative in expected:
            path = root_path / relative
            files.append({"path": str(path), "exists": path.exists()})
        return {
            "name": name,
            "root": str(root_path),
            "exists": root_path.exists(),
            "expected": files,
            "ok": root_path.exists() and all(item["exists"] for item in files),
        }

    return {
        "openclaw": check(
            "OpenClaw core",
            settings.openclaw_root,
            ["openclaw.mjs", "package.json", ".agents/skills/xianyu-commerce-agent/SKILL.md"],
        ),
        "control_center": check(
            "OpenClaw Control Center",
            settings.openclaw_control_center_root,
            ["package.json", "src/index.ts", "src/ui/server.ts"],
        ),
        "mission_control": check(
            "OpenClaw Mission Control",
            settings.openclaw_mission_control_root,
            ["README.md", "backend/app/main.py", "backend/app/api/approvals.py"],
        ),
        "rl_lab": check(
            "OpenClaw RL lab",
            settings.openclaw_rl_root,
            ["README.md", "gui-rl/README.md", "extensions/rl-training-headers/openclaw.plugin.json"],
        ),
        "skills_index": check(
            "Awesome OpenClaw Skills",
            settings.awesome_openclaw_skills_root,
            ["README.md", "categories/shopping-and-e-commerce.md", "categories/security-and-passwords.md"],
        ),
    }


def _handle_resale_agent_review(settings: Settings, args: argparse.Namespace) -> int:
    """Handle the `resale-agent-review` CLI command."""
    import os
    from pathlib import Path

    from .resale import ResaleManager

    data_dir = settings.data_dir
    rm = ResaleManager(data_dir)

    # --list: show available candidates
    if args.list_candidates or not args.candidate_id:
        candidates = rm.list_candidates()
        if not candidates:
            print("No candidates found in", data_dir / "resale_candidates.json")
            return 1
        print(f"{'ID':<40} {'Status':<15} Title")
        print("-" * 90)
        for c in candidates:
            print(f"{c['id']:<40} {c.get('status', '?'):<15} {c.get('title', '')[:40]}")
        if not args.candidate_id:
            print("\nUse --candidate-id <id> to run agent review.")
        return 0

    # Check for API key
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("ERROR: DEEPSEEK_API_KEY not set. Export it or add to .env file.")
        print("  export DEEPSEEK_API_KEY=sk-...")
        return 1

    result = run_resale_agent_review(settings, args.candidate_id, rm)
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return 1

    # Print readable report
    _print_review_report(result)

    # Save report
    report_dir = data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    import datetime

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    cid = args.candidate_id.replace("/", "_").replace("\\", "_")
    report_path = report_dir / f"resale_agent_review_{cid}_{ts}.json"

    # Strip any accidental API keys before saving
    safe_result = _strip_secrets(json.loads(json.dumps(result, default=str)))
    report_path.write_text(json.dumps(safe_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved: {report_path}")
    return 0


def run_resale_agent_review(
    settings: Settings,
    candidate_id: str,
    rm=None,
) -> dict:
    """Run the full agent review pipeline on a candidate.

    Returns a dict with: candidate, discussion, structured_reviews, aggregate, meta.
    """
    from .agent_registry import AgentRegistry
    from .agents import create_all_agents
    from .agents.base import BaseAgent
    from .llm_manager import llm_manager

    # Init LLM manager
    llm_manager.init(settings)

    # Init resale manager
    if rm is None:
        from .resale import ResaleManager

        rm = ResaleManager(settings.data_dir)

    candidate = rm.get_candidate(candidate_id)
    if not candidate:
        return {"error": f"Candidate not found: {candidate_id}"}

    # Create a fresh registry for this run
    reg = AgentRegistry()
    BaseAgent.set_registry(reg)
    agents = create_all_agents()
    for name, agent in agents.items():
        reg.register_agent(name, agent)

    # Check if LLM is actually available, and get the actual model used by agents
    from .llm import DeepSeekClient

    probe = DeepSeekClient(settings)
    used_deepseek = probe.enabled

    # Read the actual model from the agent's client (forced deepseek-v4-flash for resale agents)
    actual_model = None
    if used_deepseek:
        first_agent = reg._agents.get("resale_research")
        if first_agent and hasattr(first_agent, "llm"):
            try:
                actual_model = first_agent.llm.client._effective_model
            except Exception:
                actual_model = settings.deepseek_model

    # Run discussion
    discussion = reg.discuss(
        topic=f"倒卖候选评审: {candidate.get('title', '')}",
        participants=["resale_research", "resale_risk", "resale_pricing"],
        rounds=2,
        context={"candidate": candidate},
    )

    # Structured reviews
    structured_reviews = []
    for agent_name in ["resale_research", "resale_risk", "resale_pricing"]:
        agent = reg._agents.get(agent_name)
        if agent and hasattr(agent, "review_candidate"):
            structured_reviews.append(agent.review_candidate(candidate))

    # Aggregate
    updated = rm.save_agent_review(candidate_id, discussion, structured_reviews)
    aggregate = rm.aggregate_agent_review(candidate_id) or {}

    return {
        "candidate": updated or candidate,
        "discussion": discussion,
        "structured_reviews": structured_reviews,
        "aggregate": aggregate,
        "meta": {
            "used_deepseek": used_deepseek,
            "candidate_id": candidate_id,
            "model": actual_model if used_deepseek else None,
        },
    }


def _strip_secrets(obj):
    """Recursively remove any value that looks like an API key."""
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if isinstance(v, str) and v.startswith("sk-") else _strip_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_strip_secrets(v) for v in obj]
    return obj


def _print_review_report(result: dict) -> None:
    """Print a human-readable agent review report."""
    candidate = result.get("candidate", {})
    aggregate = result.get("aggregate", {})
    structured = result.get("structured_reviews", [])
    meta = result.get("meta", {})

    print("=" * 60)
    print("Resale Agent Review Report")
    print("=" * 60)
    print(f"  Candidate: {candidate.get('title', '?')}")
    print(f"  ID:        {candidate.get('id', '?')}")
    print(f"  Status:    {candidate.get('status', '?')}")
    print(f"  DeepSeek:  {'YES' if meta.get('used_deepseek') else 'NO (fallback)'}")
    if meta.get("model"):
        print(f"  Model:     {meta['model']}")
    print()

    # Per-agent reviews
    for review in structured:
        agent = review.get("agent", "?")
        print(f"--- {agent} ---")
        for k, v in review.items():
            if k == "agent":
                continue
            if isinstance(v, list):
                print(f"  {k}: {', '.join(str(x) for x in v) if v else '(none)'}")
            else:
                print(f"  {k}: {v}")
        print()

    # Aggregate
    print("--- Aggregate ---")
    print(f"  Score:         {aggregate.get('agent_review_score', '-')}/100")
    print(f"  Recommendation: {aggregate.get('agent_review_recommendation', '-')}")
    blocking = aggregate.get("blocking_flags", [])
    if blocking:
        print(f"  Blocking:      {', '.join(blocking)}")
    reasons = aggregate.get("agent_review_reasons", [])
    if reasons:
        print(f"  Reasons:")
        for r in reasons:
            print(f"    - {r}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
