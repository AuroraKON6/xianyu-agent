# Local Xianyu Tool Audit

Updated: 2026-06-30

Detailed architecture companion: `docs/research/local-xianyu-program-architecture.md`

## Purpose

This note reviews the local Xianyu-related tools under `E:\闲鱼` and turns them into a practical roadmap for the current OpenClaw/Xianyu project.

The goal is not to replace the current system. The current project already has the right backbone:

- OpenClaw router and specialist agents.
- WorkOrder, Approval, ExecutionJobQueue, and ActionExecutor.
- Browser/XianGuanJia/stub adapter layers.
- Account profiles, account queues, sync persistence, message intake, and first-run readiness gates.
- OPC dashboard and daily review snapshots.

The local tools are useful as field notes: they show which runtime problems appear once a Xianyu automation runs for real.

## Executive Conclusion

The useful lesson is not "auto-send everything".

The useful lesson is that a one-person company needs a stronger runtime operating layer:

- account connection state
- cookie and token freshness
- WebSocket heartbeat status
- reconnect and retry backoff
- manual takeover state
- message dedupe and ACK handling
- per-account prompt profile
- per-chat conversation memory
- order idempotency locks
- listing/order sync freshness
- operator-facing alerts and runbooks

So the next technical target should be:

> Build a Xianyu runtime readiness layer that converts these low-level platform states into dashboard gates, health snapshots, and manual operator actions.

## Local Tool Matrix

| Local project | What it is | Useful learning | Do not copy blindly |
| --- | --- | --- | --- |
| `xianyuapis-main` | Small Python Xianyu API/WebSocket reference. | Token refresh, Cookie refresh, WebSocket registration, heartbeat, message ACK, chat message payload shape, item detail endpoint. | Protocol details are volatile. Wrap behind adapter contracts and tests. Do not directly wire send APIs into autonomous actions. |
| `xianyu-auto-reply-main` | Full auto-reply service with admin UI, multi-account runtime, order status handling, and auto-delivery logic. | Connection state enum, pause manager, per-account instance registry, token/cookie refresh loops, order locks, item detail cache, log cleanup, run-state observability. | Its README contains serious security warnings. Do not copy auth defaults, remote telemetry, remote captcha code, external notification code, or auto-delivery behavior. |
| `mulitXianYuAugent-main` | Multi-account buyer/seller AI assistant. | MultiAccountManager, per-account prompts, account status table, conversation context DB, seller intent router, bargain count, buyer/seller mode separation. | Buyer-bot behavior is not needed for the seller-ops MVP. Keep seller operations first. |
| `XianyuAuto-master` | Old Android UI automation script using `uiautomator2`. | Shows UI path fragility and the need for operation cooldowns after listing edits. | Too old and device-specific. Do not use as the primary execution layer. |
| `fastPickUp-master` | Java Spring service for captured lead/event intake and email alerts. | Simple "external watcher -> inbox -> unread/read state -> notification" pattern. | Do not use it as a buying/抢拍 automation path. Use only the lead-inbox idea. |
| `LittleCrawler-main` | Async multi-platform crawler architecture. | Clean platform/client/extractor/storage separation and Web/API dashboard shape. | The local copy does not contain an active Xianyu implementation. Use architecture ideas only. |

## What Helps Our Project Most

### 1. Runtime State Model

The strongest missing layer is a normalized account runtime state.

Recommended fields:

- `cookie_id`
- `connection_status`: `unknown`, `disconnected`, `connecting`, `connected`, `degraded`, `manual_verification`, `error`
- `last_heartbeat_at`
- `last_message_at`
- `last_token_refresh_at`
- `last_cookie_refresh_at`
- `token_fresh`
- `cookie_fresh`
- `reconnect_count`
- `last_error`
- `manual_takeover_chats`
- `pending_verification_reason`
- `updated_at`

Current matching project areas:

- `xianyu_agent/account_profiles.py`
- `xianyu_agent/account_ops_queues.py`
- `xianyu_agent/first_run_readiness.py`
- `xianyu_agent/opc_dashboard.py`
- `web/app.js`

### 2. WebSocket Message Intake Contract

The small `xianyuapis-main` project shows a useful message envelope:

- `msg_time`
- `user_url`
- `send_user_id`
- `send_user_name`
- `item_id`
- `send_message`
- `chat_id`

Our system should normalize incoming Xianyu IM events into a provider-neutral intake record:

- `source`: `xianyu_ws`, `feishu_cli`, `manual`, etc.
- `source_event_id`
- `cookie_id`
- `chat_id`
- `buyer_id`
- `buyer_name`
- `item_id`
- `message_text`
- `message_time`
- `raw_schema_version`
- `risk_flags`

Then feed it through existing `POST /workflows/messages/inbound`, preserving the current Approval gate.

### 3. Manual Takeover and Auto-Reply Pause

Both full auto-reply references treat manual intervention as first-class state.

Adopt the concept, not the automatic send behavior:

- If the operator manually sends a message, mark that chat paused for N minutes.
- Dashboard should show paused chats and remaining pause time.
- Reply generation can still create drafts, but should not enqueue sends while paused.

Current matching areas:

- `xianyu_agent/conversation_profiles.py`
- `xianyu_agent/message_intake.py`
- `xianyu_agent/auto_reply.py`
- `xianyu_agent/account_profiles.py`

### 4. Order Idempotency and Fulfillment Locks

The full auto-reply service keeps per-order locks and cooldowns to avoid duplicate fulfillment.

Our system already has queue idempotency. The next useful addition is fulfillment-level idempotency:

- per `order_id` fulfillment lock
- `last_delivery_attempt_at`
- `delivery_sent_orders`
- cooldown before retry
- explicit operator-visible blocked reason

Current matching areas:

- `xianyu_agent/action_executor.py`
- `xianyu_agent/execution_queue.py`
- `xianyu_agent/order_status_handler.py`
- `xianyu_agent/work_orders.py`

### 5. Multi-Account Operations

`mulitXianYuAugent-main` validates our direction: one account is not enough as a framework concept.

Useful ideas to absorb:

- per-account status row
- per-account prompt profile
- per-account running task
- per-account retry backoff
- account start/stop/reload controls
- account health dashboard

Our project already has account profiles and queues. The gap is the live runtime status row.

### 6. Discovery Watcher Inbox

`fastPickUp-master` is useful as a tiny pattern:

`external capture -> save raw event -> mark unread -> notify operator -> mark read`

For our OPC model, this becomes:

`market signal -> opportunity inbox -> score -> candidate -> listing test`

This should be implemented as a discovery inbox, not as a buying/抢拍 tool.

## Recommended Next Implementation Target

### Phase R4: Xianyu Runtime Readiness

Goal: make first-run and daily operations show live account runtime health, not only static configuration readiness.

Scope:

1. Add a local `account_runtime_state` store.
2. Add helper functions to update heartbeat, message, token/cookie refresh, connection errors, and manual takeover state.
3. Add `GET /accounts/runtime` and include runtime state in `/dashboard/account-queues`.
4. Extend `/dashboard/first-run-readiness` with runtime gates:
   - account has runtime state
   - token/cookie freshness known
   - no active manual verification blocker
   - message intake bridge is fresh or intentionally disabled
5. Add console badges for runtime status.
6. Add tests before implementation.

Suggested files:

- `xianyu_agent/account_runtime.py`
- `xianyu_agent/server.py`
- `xianyu_agent/first_run_readiness.py`
- `xianyu_agent/account_ops_queues.py`
- `web/app.js`
- `web/style.css`
- `tests/test_account_runtime.py`
- `tests/test_first_run_readiness.py`
- `tests/test_console_ui_contract.py`

## Handoff Prompt

Use this with opencode or Claude Code:

```text
Continue E:\闲鱼\xianyu. Read docs/research/local-xianyu-tools-audit.md and docs/PROJECT_PLAN.md first.

Implement Phase R4: Xianyu Runtime Readiness.

Use TDD:
1. Add tests for an account_runtime_state store with cookie_id, connection_status, heartbeat/message/token/cookie timestamps, reconnect_count, last_error, manual_takeover_chats, and pending_verification_reason.
2. Add GET /accounts/runtime returning sanitized runtime state.
3. Include runtime state in /dashboard/account-queues.
4. Extend /dashboard/first-run-readiness with runtime gates.
5. Render runtime badges in the OPC console.

Do not copy code wholesale from local reference projects. Use them only for field names and operational patterns.
Do not add autonomous sends, auto-delivery, captcha bypass, or buying/抢拍 behavior.
Preserve WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor for risky actions.

Run:
.\.venv\Scripts\python.exe -m unittest tests.test_account_runtime tests.test_first_run_readiness tests.test_console_ui_contract -v
node --check .\web\app.js
git diff --check
```
