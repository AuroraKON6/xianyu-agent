# GitHub Reference Projects for Xianyu OPC Automation

Updated: 2026-06-16

## Purpose

This note turns similar GitHub projects into an implementation roadmap for the current `E:\闲鱼\xianyu` project. The goal is to learn architecture and operating patterns, not to copy unknown credentials, risky shortcuts, or large unreviewed code.

## Current Project Baseline

The current project already has these important building blocks:

- OpenClaw-style agent routing through `OpenClawRouter` and `AgentRegistry`.
- WorkOrder, Approval, ActionExecutor, and ExecutionJobQueue gates.
- Multi-account storage through `xianyu_agent/db_manager.py` and `xianyu_agent/cookie_manager.py`.
- Xianyu IM WebSocket client through `xianyu_agent/xianyu_ws.py`.
- Multi-expert auto-reply through `xianyu_agent/auto_reply.py`.
- Browser adapter and XianGuanJia adapter execution layers.
- Daily OPC dashboard and resale candidate scoring.

So the right move is not to replace the project. The right move is to absorb missing operating structure from reference projects into this existing approval-gated system.

## Reference Project Matrix

| Project | URL | Local Copy | Best Learning Value | Do Not Copy Blindly |
| --- | --- | --- | --- | --- |
| xianyu-auto-reply | https://github.com/zhinianboke/xianyu-auto-reply | `E:\闲鱼\xianyu-auto-reply-main` | Complete FastAPI + WebSocket + multi-account + auto-reply + auto-delivery + admin console structure. | README reports serious historical security/backdoor risks; do not copy auth, remote captcha, backup download, or external telemetry code. |
| XianYuApis | https://github.com/cv-cat/XianYuApis | `E:\闲鱼\xianyuapis-main` | Small API/WebSocket reference for token refresh, chat creation, message send, and item info retrieval. | Treat protocol details as volatile; wrap behind adapters and tests. |
| mulitXianYuAugent | https://github.com/MannersMakesMan/mulitXianYuAugent | `E:\闲鱼\mulitXianYuAugent-main` | Multi-account orchestration, per-account prompts, seller/buyer mode separation, chat context persistence. | Buyer-bot behavior is not part of the current seller-ops MVP; keep seller mode first. |
| XianyuAutoAgent | https://github.com/shaxiu/XianyuAutoAgent | Not local | Intent router and expert-agent reply architecture: price, tech, default, classification. | Current project already has a similar `AutoReplyEngine`; use only prompt/route ideas. |
| myfish | https://github.com/Kaguya233qwq/myfish | Not local | Async bot/adapter shape closer to OpenClaw-style tool boundaries. | Do not rewrite the current FastAPI service around it. |
| goofish-watcher | https://github.com/Microck/goofish-watcher | Not local | Keyword monitoring and discovery loop ideas. | Current project already has discovery tasks; use only for monitoring cadence ideas. |

## What Was Absorbed Now

Implemented safe operating slices from the reference projects without replacing the current approval-gated architecture.

Files added or changed:

- `xianyu_agent/account_profiles.py`
- `xianyu_agent/account_prompt_profiles.py`
- `xianyu_agent/conversation_profiles.py`
- `xianyu_agent/account_ops_queues.py`
- `xianyu_agent/account_attribution.py`
- `xianyu_agent/sync_persistence.py`
- `xianyu_agent/server.py`
- `tests/test_account_profiles.py`
- `tests/test_account_prompt_profiles.py`
- `tests/test_conversation_profiles.py`
- `tests/test_account_ops_queues.py`
- `tests/test_account_attribution.py`
- `tests/test_sync_persistence.py`

New APIs:

```http
GET /accounts/profiles
GET /accounts/{cookie_id}/prompt-profile
GET /accounts/{cookie_id}/conversation-summary
GET /dashboard/account-queues
POST /dashboard/account-queues/repair-attribution
GET /adapter/sync-preview
POST /adapter/sync-persist
```

These return operator-facing, sanitized account and operations state:

- enabled state
- remark
- auto-confirm state
- pause duration
- seller/buyer role modes
- keyword, item, and order counts
- order status distribution
- AI reply status and prompt keys
- capability flags
- recommended next actions
- prompt readiness
- conversation summary
- pending approvals, execution jobs, failed jobs, and reply drafts by account
- adapter sync capabilities
- synced item/order counts and freshness metadata

Credential values are intentionally excluded.

## Adoption Roadmap

### Phase R1: Account Capability Matrix

Status: implemented.

Source inspiration:

- `mulitXianYuAugent-main/multi_account_manager.py`
- `mulitXianYuAugent-main/context_manager.py`
- `xianyu-auto-reply-main/cookie_manager.py`

Current implementation:

- Aggregate existing DB tables into account profiles.
- Expose `/accounts/profiles`.
- Keep raw cookies and API keys out of responses.

UI implementation:

- Added an account profile panel to `web/console.html`, `web/app.js`, and `web/style.css`.
- The panel renders `/accounts/profiles`, refreshes with the main console status refresh, and keeps the empty state readable when no accounts are configured.

### Phase R2: Account-Specific Prompt Profiles

Status: implemented.

Source inspiration:

- `mulitXianYuAugent-main/prompts/`
- `mulitXianYuAugent-main/MULTI_ACCOUNT_GUIDE.md`
- `xianyu-auto-reply-main/ai_reply_engine.py`

Implementation direction:

- Reuse existing `ai_reply_settings.custom_prompts`.
- Add server helpers to validate prompt keys: `classify`, `price`, `tech`, `default`, `after_sales`.
- Add account-level prompt preview endpoint.
- Keep current DeepSeek/MiMo policy unchanged.

Current implementation:

- Added `xianyu_agent/account_prompt_profiles.py`.
- Added `GET /accounts/{cookie_id}/prompt-profile`.
- The endpoint returns prompt readiness, required/missing/unknown keys, prompt lengths, and bargaining limits.
- Prompt bodies, API keys, cookies, and base URLs are not returned.

### Phase R3: Conversation Context Hardening

Status: implemented.

Source inspiration:

- `mulitXianYuAugent-main/context_manager.py`
- `xianyu-auto-reply-main/ai_reply_engine.py`

Implementation direction:

- Keep existing `ai_conversations` table.
- Add context summary API by account and chat.
- Add bargain-round summary and last buyer intent.
- Keep generated replies as approval-gated drafts by default.

Current implementation:

- Added `xianyu_agent/conversation_profiles.py`.
- Added `GET /accounts/{cookie_id}/conversation-summary?chat_id=...`.
- The summary returns turns, last intent, intent counts, role counts, max bargain count, risk flags, recent redacted messages, and next operator action.
- Buyer IDs, chat IDs, cookie values, and long contact-like numbers are not returned.

### Phase R4: Item and Order Sync Adapter Boundary

Status: implemented.

Source inspiration:

- `xianyuapis-main/XianyuAutoAsync.py`
- `xianyuapis-main/XianyuApis.py`
- `xianyu-auto-reply-main/order_status_handler.py`

Implementation direction:

- Do not scatter raw protocol calls across the project.
- Add provider-neutral adapter methods:
  - `get_item_info(item_id, cookie_id)`
  - `sync_my_listings(cookie_id)`
  - `sync_orders(cookie_id)`
- Implement with `browser` or `xianguanjia` first, then optional direct Xianyu API wrapper.

Current implementation:

- Added provider-neutral `sync_orders(cookie_id, status="")` and `sync_my_listings(cookie_id)` methods to the adapter contract.
- `StubAdapter` returns safe, empty "not implemented" previews.
- `BrowserAdapter` delegates to existing `get_orders()` and `get_my_listings()` async fetchers.
- `XianGuanJiaAdapter` exposes GET-based `/api/orders` and `/api/listings` sync previews behind its signed request helper.
- Added `GET /adapter/sync-preview?cookie_id=...&status=...`.
- `GET /dashboard/opc-daily` adapter health now reports `sync_capabilities`.

### Phase R5: Console Operator Workflow

Status: implemented for the daily operator cockpit slice.

Source inspiration:

- `xianyu-auto-reply-main/frontend/`
- `xianyu-auto-reply-main/docs/前端改造方案.md`

Implementation direction:

- Keep the current single console instead of importing another admin UI.
- Add account readiness cards.
- Add per-account queue, approvals, reply drafts, and order counts.
- Keep controls dense and operational, not landing-page style.

Current implementation:

- `GET /dashboard/opc-daily` now includes `account_readiness`, `adapter_health`, and `next_actions`.
- The console OPC dashboard renders the daily action queue and readiness cards.
- Added `xianyu_agent/account_ops_queues.py`.
- Added `GET /dashboard/account-queues`.
- The endpoint groups pending approvals, queued/running execution jobs, failed jobs, pending reply drafts, item counts, and order counts by `cookie_id`.
- The response intentionally omits raw cookies, buyer IDs, chat IDs, API keys, tokens, and prompt bodies.
- The console OPC dashboard now renders an account queue drilldown section so the operator can see which account is blocked and what to do next.
- Item/order sync adapter contract is implemented as preview-only through `GET /adapter/sync-preview`.
- Added account attribution for new WorkOrders, ecommerce listing drafts, and inbound buyer-message intake.
- Added `POST /dashboard/account-queues/repair-attribution` plus console controls to preview/apply local repair for legacy unassigned WorkOrders.
- Added `xianyu_agent/sync_persistence.py`.
- Added `POST /adapter/sync-persist` to persist provider-neutral order/listing sync results into local DB tables.
- Added `account_sync_state` freshness metadata for synced `orders` and `listings`.
- `/dashboard/account-queues` now includes `sync_freshness` and can surface stale sync state as a next action.
- The sync-persist response returns only safe counts/status/freshness, not raw adapter payloads, buyer IDs, delivery payloads, or cookies.
- The console renders account queue sync freshness badges and per-account sync buttons for `/adapter/sync-persist`.
- Added `POST /dashboard/daily-review` and console control to snapshot `/dashboard/opc-daily` plus `/dashboard/account-queues`.
- The account profile panel now includes a first-account bootstrap form backed by `POST /accounts`; successful imports refresh account profiles and OPC readiness.
- Account profile cards now include a Prompt readiness shortcut backed by `/accounts/{cookie_id}/prompt-profile`.
- Account profile cards now include a Prompt bootstrap shortcut backed by `/accounts/{cookie_id}/prompt-profile/bootstrap`.
- Account profile cards now include a conversation summary shortcut backed by `/accounts/{cookie_id}/conversation-summary?chat_id=...`.
- Added `GET /dashboard/first-run-readiness` and OPC console gates for the first controlled real-account loop.

## Suggested Priority

1. Add/import the first real seller account and run account-attribution repair dry-run before applying legacy repairs.
2. Revisit XianGuanJia/browser real payload mapping once credentials and sample responses are available.
3. Use first-run readiness gates until account, adapter, sync, Prompt, inventory, and attribution are all pass.
4. Run a controlled real account loop: sync, review approvals, publish one approved listing, monitor replies, and write the review.
5. Use daily review snapshots as inputs to weekly review and bottleneck tuning.
