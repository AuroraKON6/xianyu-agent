---
name: xianyu-commerce-agent
description: Operate the local Xianyu commerce workflow for product listing drafts, Feishu-triggered publishing, DeepSeek listing optimization, image reference search, digital auto-fulfillment, daily polish tasks, GitHub opportunity discovery, resale-rights-aware virtual product arbitrage evaluation, authorized URL crawling for downloadable candidates, and platform guardrails that pause on verification/risk-control signals. Use when the user wants an agent to list goods on Xianyu, connect Feishu to commerce automation, integrate XianGuanJia, discover GitHub agent projects, crawl a URL for downloadable virtual-product candidates, evaluate virtual products for resale profitability, or run the local xianyu_agent service.
---

# Xianyu Commerce Agent

## Overview

Use the local Python package at `E:\闲鱼\xianyu`. The workflow accepts product descriptions, generates optimized Xianyu listing drafts, publishes through the configured adapter, fulfills digital orders, and polishes published items on a schedule.

When running inside OpenClaw, treat `E:\闲鱼\xianyu` as the fixed project root unless the user explicitly gives a different path.

## Quick Commands

Check configuration:

```powershell
cd E:\闲鱼\xianyu
.\.venv\Scripts\python -m xianyu_agent.cli config-check
```

Create a draft or publish through the configured adapter:

```powershell
cd E:\闲鱼\xianyu
.\.venv\Scripts\python -m xianyu_agent.cli draft "上架：蓝牙耳机，库存20，售价59，自动发货内容：https://example.com"
```

Start the Feishu/API server:

```powershell
powershell -ExecutionPolicy Bypass -File E:\闲鱼\xianyu\scripts\start_xianyu_workflow.ps1
```

Open the local console:

```powershell
powershell -ExecutionPolicy Bypass -File E:\闲鱼\xianyu\scripts\open_xianyu_console.ps1
```

Run polish once:

```powershell
cd E:\闲鱼\xianyu
.\.venv\Scripts\python -m xianyu_agent.cli polish
```

Find high-star GitHub projects or agent opportunities:

```powershell
cd E:\闲鱼\xianyu
.\.venv\Scripts\python -m xianyu_agent.cli discover-github --query "ai agent" --min-stars 1000 --limit 5
```

Daily GitHub discovery is scheduled by `GITHUB_DISCOVERY_ENABLED` and `GITHUB_DISCOVERY_AT`.

Evaluate a licensed virtual product opportunity:

```powershell
cd E:\闲鱼\xianyu
.\.venv\Scripts\python -m xianyu_agent.cli evaluate-product "AI prompt pack" --cost 99 --price 39 --rights-type resale_license --rights-evidence "license proof saved"
```

Crawl a URL for downloadable candidates and profit evaluation:

```powershell
cd E:\闲鱼\xianyu
.\.venv\Scripts\python -m xianyu_agent.cli crawl-url "https://example.com/product" --cost 99 --price 39
```

Check or resume platform guard:

```powershell
cd E:\闲鱼\xianyu
.\.venv\Scripts\python -m xianyu_agent.cli guard-status
.\.venv\Scripts\python -m xianyu_agent.cli guard-resume --note "manual verification complete"
```

## OpenClaw Agent Bridge

Use the HTTP bridge when OpenClaw needs to operate the agent as a tool layer:

```powershell
# Health
Invoke-RestMethod -Uri http://127.0.0.1:8788/health

# One natural-language command, routed by OpenClaw into the local agents
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agent/message -ContentType 'application/json; charset=utf-8' -Body '{"text":"上架：测试商品，库存1，售价9.9，自动发货内容：https://example.com/test","publish":false}'

# Explicit OpenClaw entrypoint
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agents/openclaw/message -ContentType 'application/json; charset=utf-8' -Body '{"message":"状态","publish":false}'

# Collaborative listing: listing agent asks discovery + analytics before drafting
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agent/message -ContentType 'application/json; charset=utf-8' -Body '{"text":"协作上架：蓝牙耳机，成本70，售价98，库存3，成色很新","publish":false}'

# Observe agent-to-agent collaboration
Invoke-RestMethod -Uri http://127.0.0.1:8788/agents/collaboration
Invoke-RestMethod -Uri http://127.0.0.1:8788/agents/messages

# Multi-agent roundtable discussion
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agents/discuss -ContentType 'application/json; charset=utf-8' -Body '{"topic":"如何上架蓝牙耳机，成本70，售价98","participants":["listing","discovery","analytics"],"rounds":2}'
Invoke-RestMethod -Uri http://127.0.0.1:8788/agents/discussions

# Collaboration history persists across service restarts
Get-Content .\data\agent_collaboration.jsonl -Tail 5

# Realistic work-order workflow: task ticket + action plan + human approval
Invoke-RestMethod -Uri http://127.0.0.1:8788/work-orders
$order = Invoke-RestMethod -Method Post http://127.0.0.1:8788/work-orders -ContentType 'application/json; charset=utf-8' -Body '{"kind":"listing","title":"协作上架蓝牙耳机","owner":"listing","context":{"price":98,"cost":70}}'
Invoke-RestMethod -Method Post "http://127.0.0.1:8788/work-orders/$($order.work_order_id)/plan" -ContentType 'application/json; charset=utf-8' -Body '{"proposed_actions":[{"agent":"listing","action":"publish_listing","description":"发布商品"}]}'
Invoke-RestMethod -Method Post http://127.0.0.1:8788/approvals/{approval_id}/decision -ContentType 'application/json; charset=utf-8' -Body '{"decision":"approved","note":"人工确认可以执行"}'

# Remember a preference
Invoke-RestMethod -Method Post http://127.0.0.1:8788/memory -ContentType 'application/json; charset=utf-8' -Body '{"text":"以后标题更短，强调自动发货和授权证明","source":"openclaw"}'
```

Before any HTTP call, if `/health` is unavailable, start the local service with:

```powershell
powershell -ExecutionPolicy Bypass -File E:\闲鱼\xianyu\scripts\start_xianyu_workflow.ps1
```

The local console is `http://127.0.0.1:8788/console`.

## Workflow

1. Read `.env` and confirm `DEEPSEEK_API_KEY`, `XIANYU_ADAPTER`, and Feishu settings.
2. For a product request, call `CommerceWorkflow.create_listing(text, source, product_images)`.
3. Provide owned or authorized product images when possible. External image search results are reference candidates unless `ALLOW_EXTERNAL_PRODUCT_IMAGES=true`.
4. Publish through `stub` while testing. Use `xianguanjia` only after the user provides the authorized XianGuanJia base URL, token, and endpoint paths.
5. For paid-order webhooks, send the order payload to `/orders/paid`. Digital delivery requires `delivery_payload`.
6. Daily polish runs while the FastAPI server is active and uses `POLISH_AT`.
7. For GitHub opportunity discovery, call `CommerceWorkflow.discover_github_opportunities(...)` or `/research/github`; treat DeepSeek output as a business/legal triage, not final legal advice.
8. For virtual products, call `CommerceWorkflow.evaluate_virtual_product_opportunity(...)` or send `比价：...` through Feishu. Require resale rights before purchase and purchase/license evidence before listing.
9. For URL crawling, call `CommerceWorkflow.crawl_url_opportunity(...)`, `/crawler/url`, or send `crawl: https://...` through Feishu. Download only when rights evidence is present.
10. For platform risk controls, use `CommerceWorkflow.platform_guard_status()`, `/guard/status`, or `guard-status`. If verification is detected, pause automation and wait for manual verification.

## Feishu CLI Long Connection

Feishu integration uses the Feishu CLI long connection model. No public webhook URL or tunnel setup is required.

- Use the Feishu CLI to log in, bind the application, and establish a long-lived event subscription.
- The Feishu CLI bridges received events to the local FastAPI event handler at `http://127.0.0.1:8788/feishu/events`.
- The local service only processes incoming events; it does not manage webhook URLs or tunnels.
- `FEISHU_VERIFICATION_TOKEN` is optional and used to verify event payloads if configured.

## OpenClaw Mounting

- Use `openclaw/xianyu-commerce-agent.openclaw.yaml` as the agent manifest.
- Start the local tool layer with `scripts/start_xianyu_workflow.ps1`.
- Stop it with `scripts/stop_xianyu_workflow.ps1`.
- Run the full integration smoke with `scripts/run_full_flow_smoke.ps1`.

## Safety Rules

- Do not claim third-party product images are owned by the seller.
- Do not invent brand authorization, warranty, certifications, scarcity, or shipping promises.
- Use official or user-authorized Xianyu/XianGuanJia access.
- Do not sell copied GitHub repositories as-is. Prefer deployment, customization, integration, support, training, hosted service, or original derivative offerings that respect the license.
- Do not resell courses, files, seller copy, or promotional images without resale and asset rights. Generate original listing copy from authorized facts.
- Do not download files from a URL for resale unless `rights_type` and `rights_evidence` confirm permission. Treat page images as reference candidates unless asset rights are explicitly confirmed.
- Do not bypass captcha, human verification, slider checks, device fingerprinting, or platform risk controls. Use throttling, authorized APIs, pause-and-notify, and manual takeover.
- Keep real API keys in `.env`, never in committed config or skill files.
