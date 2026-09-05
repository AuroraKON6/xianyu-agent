# 🧠 XiānYu Agent — Autonomous E-Commerce Intelligence Platform

<p align="center">
  <img src="https://img.shields.io/badge/Architecture-Multi--Agent-orange" />
  <img src="https://img.shields.io/badge/LLM-Multi--Model%20Support-blue" />
  <img src="https://img.shields.io/badge/LLM-DeepSeek%20V4-green" />
  <img src="https://img.shields.io/badge/Platform-Xianyu/Goofish-yellow" />
  <img src="https://img.shields.io/badge/Agent%20Harness-Pi-black" />
  <img src="https://img.shields.io/badge/Feishu-Bot-red" />
  <img src="https://img.shields.io/badge/Language-Python%203.11+-3776AB" />
  <img src="https://img.shields.io/badge/License-MIT-green" />
</p>

<p align="center">
  <b>A Pi-operated, evidence-driven Xianyu business system.</b><br/>
  From product evidence to realized profit, with deterministic gates and operator approval.
</p>

---

## 🚀 What Is This?

XiānYu Agent is a local operating system for a one-person Xianyu business. **Pi is the sole natural-language operator harness**. The existing Python/FastAPI application remains the business system of record for candidates, commercial terms, evidence gates, WorkOrders, approvals, execution jobs, account state, orders, and realized profit.

```
Offline one-SKU intake preflight -> Account readiness -> Candidate-bound evidence archive
-> One-product packet (12 gates)
-> Current account/category terms
-> Manual candidate decision -> 20-gate Commercial Alpha preview
-> Listing draft -> Human approval -> Execution evidence
-> Inquiry -> Paid order -> Procurement -> Dual tracking
-> Delivery -> After-sales -> Realized-profit reconciliation
```

Prepare the first real SKU while services remain stopped with
`scripts/commercial_alpha_intake.py`; the operating procedure and exact pass
criteria are in `docs/ops/commercial-alpha-intake-bundle.md`.

Pi receives only `read` plus project-specific typed Xianyu tools. It has no shell, file-edit, approval, execution, send-message, or refund tool. Platform actions still pass through `WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor`.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    🎯 Orchestrator (调度总管)                     │
│              Receives all messages, delegates to specialists     │
├─────────┬──────────┬──────────┬──────────┬──────────┬───────────┤
│ 🔍      │ 📝       │ 💬       │ 📦       │ 📊       │ 🕷️       │
│ Product │ Listing  │ Customer │ Order    │ Data     │ Crawler   │
│ Scout   │ Specialist│ Service │ Manager  │ Analyst  │ Expert    │
│ 选品专家 │ 上架专员  │ 客服专员  │ 订单专员 │ 数据分析  │ 爬虫专家  │
└─────────┴──────────┴──────────┴──────────┴──────────┴───────────┘
         ↕              ↕            ↕            ↕
    ┌────────────────────────────────────────────────────┐
    │              🔧 Shared Infrastructure                │
    │  ┌──────────┐ ┌──────────┐ ┌────────────────────┐  │
    │  │ LLM      │ │ Browser  │ │ Platform Guard     │  │
    │  │ Manager  │ │ Pool     │ │ (Anti-detection,   │  │
    │  │ LLM      │ │ Playwright│  Rate limiting,     │  │
    │  │ DeepSeek │ │ CDP      │  CAPTCHA-aware)     │  │
    │  └──────────┘ └──────────┘ └────────────────────────┘│
    │  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
    │  │ Feishu   │ │ WorkOrder│ │ Memory   │            │
    │  │ Bridge   │ │ +Approval│ │ Store    │            │
    │  └──────────┘ └──────────┘ └──────────┘            │
    └────────────────────────────────────────────────────┘
```

---

## ✨ Core Capabilities

| Module | What It Does | Intelligence |
|--------|-------------|-------------|
| **Product Scout** | Scans 1688 + web for arbitrage opportunities | AI scoring: margin, demand, competition |
| **Listing Specialist** | Generates optimized titles, descriptions, pricing | Multi-agent roundtable discussion for strategy |
| **Customer Service** | Real-time auto-reply via WebSocket IM | Intent classification → rule-based + AI fallback |
| **Order Manager** | Tracks fulfillment, handles after-sales | Automated status tracking + exception alerts |
| **Data Analyst** | Sales metrics, profit analysis, trend detection | Dashboard with real-time collaboration logs |
| **Crawler** | Web scraping, product research, price monitoring | Platform-aware rate limiting, CAPTCHA evasion |
| **GitHub Radar** | Discovers trending AI/agent repos for monetization | Star-based filtering + LLM viability analysis |

---

## 🛡️ Safety & Compliance

- **Human-in-the-Loop**: All publish/delete/refund actions go through `WorkOrder → Approval → Human Confirm`
- **Platform Guard**: Configurable rate limits (actions/hour, daily caps, minimum interval between actions)
- **CAPTCHA-Aware**: Never bypasses verification — pauses and notifies human via Feishu
- **Cookie Isolation**: Multi-account support with per-session cookie management
- **Image Policy**: Only uses authorized images; third-party product images are flagged, not published

---

## 🔌 Integrations

| Platform | Integration | Purpose |
|----------|------------|---------|
| **Xianyu/Goofish** | Browser Automation (CDP/Playwright) | Listing, messaging, order management |
| **1688** | CLI + API | Wholesale sourcing and price comparison |
| **Feishu/Lark** | WebSocket Bot | Real-time notifications, approval workflows |
| **Pi** | RPC agent harness | Natural-language planning and typed-tool orchestration |
| **Edge/Chrome** | CDP Remote Debugging | Reuse logged-in browser sessions |

---

## 🧰 Tech Stack

- **Runtime**: Python 3.11+ and Node.js 22.19+
- **LLM Engines**: Pluggable multi-model support (DeepSeek V4, MiMo, Ollama, any OpenAI-compatible API)
- **Browser Automation**: Playwright + Chrome DevTools Protocol (CDP)
- **API Framework**: FastAPI + Uvicorn
- **Agent Harness**: project-local `@earendil-works/pi-coding-agent` 0.84.2
- **Messaging**: Feishu/Lark WebSocket, Xianyu IM WebSocket
- **Frontend**: Vanilla JS dashboard with real-time updates
- **Storage**: JSON-based persistence (SQLite planned)

---

## 🚀 Quick Start

```powershell
# Clone & setup
git clone https://github.com/AuroraKON6/xianyu-agent.git
cd xianyu-agent
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
npm install

# Configure
Copy-Item .env.example .env
# Edit .env with DEEPSEEK_API_KEY and the account/adapter settings you use.

# Verify config
.\.venv\Scripts\python -m xianyu_agent.cli config-check

# Start the service and verify the Pi RPC handshake
powershell -ExecutionPolicy Bypass -File .\scripts\start_xianyu_workflow.ps1
```

Server runs at `http://localhost:8788`. Console at `http://localhost:8788/console`.

For the XianGuanJia/third-party execution adapter, see `docs/ops/xianyu/automation-integration.md`.

### First Seller Account

Use the console account panel in this order:

1. Click `打开登录浏览器` and complete the Xianyu login in the dedicated Edge profile.
2. Click `检测当前账号` and verify the displayed numeric account ID.
3. Click `确认绑定` and enter the one-time `BIND ...` phrase.
4. Bootstrap the account Prompt, then run real listing and order sync.
5. Record current account/category commercial terms before approving a candidate.
6. For the first exact SKU, upload all five evidence types in its one-product packet and verify every active artifact reports intact SHA-256 integrity.

The preview is read-only and never returns Cookie values. The manual Cookie
form is a collapsed recovery path, not the default onboarding method. See
`docs/ops/commercial-alpha-runbook.md` for the complete operating order and
`docs/ops/xianyu/candidate-evidence-archive.md` for the evidence upload SOP.

### WebSocket Auto-Reply (Xianyu IM)

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli ws-start --cookies "your_cookies_here"
```

### One-Click Scripts

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_xianyu_workflow.ps1
```

---

## Pi Operator

Agents can request context from each other before making decisions:

```powershell
# Read current state through Pi and the typed local tools.
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agent/message `
  -ContentType 'application/json; charset=utf-8' `
  -Body '{"text":"读取当前经营状态，指出第一个阻塞项，不创建任何记录","publish":false}'

# Active Pi lifecycle check
Invoke-RestMethod http://127.0.0.1:8788/agents/pi/status?active=true

# Read the exact first blocker for one candidate; this is read-only.
Invoke-RestMethod http://127.0.0.1:8788/resale/candidates/{candidate_id}/activation-packet

# Read immutable evidence metadata and integrity for that candidate; this does not download files.
Invoke-RestMethod http://127.0.0.1:8788/resale/candidates/{candidate_id}/evidence-artifacts
```

---

## 📁 Project Structure

```
xianyu-agent/
├── .pi/                    # Pi system contract, extension, and business skills
├── package.json            # Project-local pinned Pi runtime
├── xianyu_agent/           # Core agent system
│   ├── agents/             # Specialized AI agents
│   ├── pi_runtime.py       # Persistent Pi RPC lifecycle client
│   ├── candidate_activation.py # Versioned one-product evidence gates
│   ├── candidate_evidence_store.py # Immutable candidate-bound evidence archive
│   ├── browser_adapter.py  # Playwright/CDP browser automation
│   ├── llm_manager.py      # Multi-provider LLM orchestration
│   ├── platform_guard.py   # Rate limiting & anti-detection
│   ├── xianyu_ws.py        # Xianyu IM WebSocket client
│   └── server.py           # FastAPI application
├── web/                    # Dashboard UI
├── tests/                  # Test suite (30+ test files)
├── scripts/                # PowerShell automation scripts
├── docs/                   # Documentation & playbooks
├── skills/                 # Legacy compatibility resources
└── openclaw/               # Deprecated rollback-only OpenClaw resources
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  <b>Built with 🧠 by <a href="https://github.com/AuroraKON6">AuroraKON6</a></b><br/>
  <sub>Powered by Pi · DeepSeek · FastAPI · Playwright</sub>
</p>
