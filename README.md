# 🧠 XiānYu Agent — Autonomous E-Commerce Intelligence Platform

<p align="center">
  <img src="https://img.shields.io/badge/Architecture-Multi--Agent-orange" />
  <img src="https://img.shields.io/badge/LLM-Multi--Model%20Support-blue" />
  <img src="https://img.shields.io/badge/LLM-DeepSeek%20V4-green" />
  <img src="https://img.shields.io/badge/Platform-Xianyu/Goofish-yellow" />
  <img src="https://img.shields.io/badge/Integration-OpenClaw-purple" />
  <img src="https://img.shields.io/badge/Feishu-Bot-red" />
  <img src="https://img.shields.io/badge/Language-Python%203.11+-3776AB" />
  <img src="https://img.shields.io/badge/License-MIT-green" />
</p>

<p align="center">
  <b>Next-generation multi-agent e-commerce operating system.</b><br/>
  From product discovery to order fulfillment — fully autonomous, approval-gated, human-in-the-loop.
</p>

---

## 🚀 What Is This?

XiānYu Agent is an **AI-powered autonomous e-commerce platform** that orchestrates multiple specialized AI agents to run a complete second-hand marketplace business. Built on the **OpenClaw multi-agent framework** with pluggable LLM backends (DeepSeek, MiMo, Ollama, OpenAI-compatible), it handles the entire lifecycle:

```
Product Discovery → Market Analysis → Pricing Strategy → Listing Creation
      ↓                                                        ↓
  1688 Sourcing                                        Xianyu/Goofish
      ↓                                                        ↓
  Profit Calculation ← ← ← ← ← ← ← ← ← ← ← ← ← ← Auto Publishing
      ↓                                                        ↓
  Approval Gate                                         Buyer Inquiries
      ↓                                                        ↓
  Human Confirmation                              AI Customer Service
                                                          ↓
                                                    Order Fulfillment
```

> ⚠️ **Safety-First Design**: All high-risk actions (publishing, refunds, shipping) require human approval via the WorkOrder → Approval pipeline. No rogue bots.

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
| **OpenClaw** | Multi-Agent Gateway | Agent orchestration and dispatch |
| **Edge/Chrome** | CDP Remote Debugging | Reuse logged-in browser sessions |

---

## 🧰 Tech Stack

- **Runtime**: Python 3.11+ with asyncio
- **LLM Engines**: Pluggable multi-model support (DeepSeek V4, MiMo, Ollama, any OpenAI-compatible API)
- **Browser Automation**: Playwright + Chrome DevTools Protocol (CDP)
- **API Framework**: FastAPI + Uvicorn
- **Agent Framework**: OpenClaw Multi-Agent System
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

# Configure
Copy-Item .env.example .env
# Edit .env with your API keys (DEEPSEEK_API_KEY, MIMO_API_KEY)

# Verify config
.\.venv\Scripts\python -m xianyu_agent.cli config-check

# Start server
.\.venv\Scripts\python -m xianyu_agent.cli server
```

Server runs at `http://localhost:8788`. Console at `http://localhost:8788/console`.

### WebSocket Auto-Reply (Xianyu IM)

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli ws-start --cookies "your_cookies_here"
```

### One-Click Scripts

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_xianyu_workflow.ps1
```

---

## 🤖 Multi-Agent Collaboration

Agents can request context from each other before making decisions:

```powershell
# Collaborative listing — agents discuss before publishing
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agent/message `
  -ContentType 'application/json; charset=utf-8' `
  -Body '{"text":"协作上架：蓝牙耳机，成本70，售价98，库存3","publish":false}'

# Roundtable discussion — multiple agents debate strategy
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agents/discuss `
  -ContentType 'application/json; charset=utf-8' `
  -Body '{"topic":"如何上架蓝牙耳机","participants":["listing","discovery","analytics"],"rounds":2}'
```

---

## 📁 Project Structure

```
xianyu-agent/
├── xianyu_agent/           # Core agent system
│   ├── agents/             # Specialized AI agents
│   ├── browser_adapter.py  # Playwright/CDP browser automation
│   ├── llm_manager.py      # Multi-provider LLM orchestration
│   ├── platform_guard.py   # Rate limiting & anti-detection
│   ├── xianyu_ws.py        # Xianyu IM WebSocket client
│   └── server.py           # FastAPI application
├── web/                    # Dashboard UI
├── tests/                  # Test suite (30+ test files)
├── scripts/                # PowerShell automation scripts
├── docs/                   # Documentation & playbooks
├── skills/                 # OpenClaw skill definitions
└── openclaw/               # OpenClaw integration configs
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  <b>Built with 🧠 by <a href="https://github.com/AuroraKON6">AuroraKON6</a></b><br/>
  <sub>Powered by LLM · OpenClaw · Playwright</sub>
</p>
