# GitHub Reference Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Absorb useful architecture from similar GitHub Xianyu projects into the current approval-gated OpenClaw/Xianyu OPC system.

**Architecture:** Keep the current FastAPI service, WorkOrder/Approval/ExecutionJobQueue safety model, and existing adapters. Add small provider-neutral modules that summarize accounts, prompts, conversations, and sync readiness without copying risky reference-project code.

**Tech Stack:** Python 3.11, FastAPI, SQLite via `DBManager`, unittest, vanilla JS console.

---

## File Structure

- Create/modify `xianyu_agent/account_profiles.py`: account capability matrix and next-action recommendations.
- Modify `xianyu_agent/server.py`: expose account profile and later prompt/context endpoints.
- Create/modify `tests/test_account_profiles.py`: account profile unit and route tests.
- Create `docs/research/github-reference-projects.md`: reference project matrix and adoption roadmap.
- Later modify `web/app.js`, `web/console.html`, and `web/style.css`: operator-facing account profile panel.
- Later create `xianyu_agent/account_prompt_profiles.py`: account prompt validation and preview helpers.
- Later create `tests/test_account_prompt_profiles.py`: prompt profile tests.
- Later create `xianyu_agent/conversation_profiles.py`: conversation summaries and bargain/intent rollups.
- Later create `tests/test_conversation_profiles.py`: context summary tests.
- Create `xianyu_agent/sync_persistence.py`: persist provider-neutral order/listing sync snapshots.
- Modify `xianyu_agent/account_profiles.py` and `xianyu_agent/account_ops_queues.py`: expose account sync freshness.
- Create `tests/test_sync_persistence.py`: sync persistence and freshness route tests.

---

### Task 1: Account Capability Profiles

Status: complete.

**Files:**
- Create: `xianyu_agent/account_profiles.py`
- Modify: `xianyu_agent/server.py`
- Test: `tests/test_account_profiles.py`

- [x] **Step 1: Write the failing tests**

Expected behavior:

```python
profiles = build_account_profiles(db)
assert profiles[0]["cookie_id"] == "seller_a"
assert profiles[0]["counts"]["keyword_rules"] == 2
assert profiles[0]["capabilities"]["seller_auto_reply"] is True
assert "raw-cookie" not in str(profiles[0])
```

- [x] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_account_profiles -v
```

Expected before implementation: module missing and route missing.

- [x] **Step 3: Implement the minimal module and route**

Implemented:

```python
from xianyu_agent.account_profiles import build_account_profiles

@app.get("/accounts/profiles")
def list_account_profiles():
    return build_account_profiles(db_manager)
```

- [x] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_account_profiles -v
```

Expected: 3 tests pass.

---

### Task 2: Console Account Profile Panel

Status: complete.

**Files:**
- Modify: `web/console.html`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Test: `tests/test_console_ui_contract.py`

- [x] **Step 1: Write failing UI contract test**

Add a test that reads `web/app.js` and `web/console.html` and asserts the account profile panel exists:

```python
def test_console_has_account_profile_panel(self):
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "console.html").read_text(encoding="utf-8")
    js = (root / "web" / "app.js").read_text(encoding="utf-8")
    self.assertIn("account-profile-panel", html)
    self.assertIn("loadAccountProfiles", js)
    self.assertIn("/accounts/profiles", js)
```

- [x] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_console_ui_contract.ConsoleUIContractTests.test_console_has_account_profile_panel -v
```

Expected: FAIL because the panel is not yet present.

- [x] **Step 3: Add panel markup**

Add a compact account profile section to `web/console.html`:

```html
<section id="account-profile-panel" class="ops-section">
  <div class="section-header">
    <h2>账号画像</h2>
    <button id="refresh-account-profiles">刷新</button>
  </div>
  <div id="account-profile-list" class="account-profile-list"></div>
</section>
```

- [x] **Step 4: Add JS loader**

Add to `web/app.js`:

```javascript
async function loadAccountProfiles() {
  const data = await api("/accounts/profiles");
  const el = $("#account-profile-list");
  if (!el) return;
  el.innerHTML = (data || []).map(renderAccountProfile).join("") || "<div class=\"empty\">暂无账号</div>";
}
```

- [x] **Step 5: Add CSS**

Add to `web/style.css`:

```css
.account-profile-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}
```

- [x] **Step 6: Run tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_console_ui_contract -v
.\.venv\Scripts\python -m unittest tests.test_account_profiles -v
```

Expected: account panel tests and account profile tests pass.

---

### Task 3: Account Prompt Profile Validation

Status: complete.

**Files:**
- Create: `xianyu_agent/account_prompt_profiles.py`
- Modify: `xianyu_agent/server.py`
- Test: `tests/test_account_prompt_profiles.py`

- [x] **Step 1: Write failing tests**

Expected behavior:

```python
profile = build_prompt_profile(settings)
assert profile["valid_keys"] == ["after_sales", "classify", "default", "price", "tech"]
assert profile["missing_keys"] == ["after_sales", "classify", "default"]
assert "sk-secret" not in str(profile)
```

- [x] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_account_prompt_profiles -v
```

Expected: FAIL because module is missing.

- [x] **Step 3: Implement validation helper**

Create `xianyu_agent/account_prompt_profiles.py`:

```python
VALID_PROMPT_KEYS = ["after_sales", "classify", "default", "price", "tech"]

def build_prompt_profile(ai_settings):
    prompts = dict((ai_settings or {}).get("custom_prompts") or {})
    return {
        "enabled": bool((ai_settings or {}).get("ai_enabled", False)),
        "model_name": (ai_settings or {}).get("model_name", ""),
        "valid_keys": VALID_PROMPT_KEYS,
        "configured_keys": sorted(k for k in prompts if k in VALID_PROMPT_KEYS),
        "unknown_keys": sorted(k for k in prompts if k not in VALID_PROMPT_KEYS),
        "missing_keys": sorted(k for k in VALID_PROMPT_KEYS if k not in prompts),
    }
```

- [x] **Step 4: Add route**

Add:

```python
@app.get("/accounts/{cookie_id}/prompt-profile")
def get_account_prompt_profile(cookie_id: str):
    return build_prompt_profile(db_manager.get_ai_reply_settings(cookie_id) or {})
```

- [x] **Step 5: Run tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_account_prompt_profiles -v
```

Expected: PASS.

---

### Task 4: Conversation Context Summary

Status: complete.

**Files:**
- Create: `xianyu_agent/conversation_profiles.py`
- Modify: `xianyu_agent/server.py`
- Test: `tests/test_conversation_profiles.py`

- [x] **Step 1: Write failing tests**

Expected behavior:

```python
summary = summarize_conversation(history)
assert summary["turns"] == 3
assert summary["last_intent"] == "price"
assert summary["max_bargain_count"] == 2
assert len(summary["recent_messages"]) <= 5
```

- [x] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_conversation_profiles -v
```

Expected: FAIL because module is missing.

- [x] **Step 3: Implement summary helper**

Create:

```python
def summarize_conversation(history, limit=5):
    recent = list(history or [])[-limit:]
    intents = [m.get("intent", "") for m in history or [] if m.get("intent")]
    bargain_counts = [int(m.get("bargain_count") or 0) for m in history or []]
    return {
        "turns": len(history or []),
        "last_intent": intents[-1] if intents else "",
        "max_bargain_count": max(bargain_counts) if bargain_counts else 0,
        "recent_messages": recent,
    }
```

- [x] **Step 4: Add route**

Add:

```python
@app.get("/accounts/{cookie_id}/conversation-summary")
def get_conversation_summary(cookie_id: str, chat_id: str, limit: int = 20):
    history = db_manager.get_conversation_history(cookie_id, chat_id, limit)
    return summarize_conversation(history)
```

- [x] **Step 5: Run tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_conversation_profiles -v
```

Expected: PASS.

---

### Task 4.5: OPC Daily Dashboard Readiness Queue

Status: complete.

**Files:**
- Modify: `xianyu_agent/opc_dashboard.py`
- Modify: `xianyu_agent/server.py`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Test: `tests/test_opc_dashboard.py`
- Test: `tests/test_console_ui_contract.py`

Implemented:

- `GET /dashboard/opc-daily` now includes `account_readiness`, `adapter_health`, and `next_actions`.
- `account_readiness` summarizes total accounts, enabled accounts, seller-ops-ready accounts, AI-enabled accounts, and per-account recommendations.
- `adapter_health` is read-only and sanitized before it reaches the dashboard.
- `next_actions` converts daily state into an ordered operator queue: pending approvals, failed jobs, adapter setup, account readiness, reply drafts, execution jobs, discovery, and bottleneck review.
- Console dashboard renders next actions and readiness cards.

Verified:

```powershell
.\.venv\Scripts\python -m unittest tests.test_opc_dashboard -v
.\.venv\Scripts\python -m unittest tests.test_console_ui_contract -v
```

---

### Task 5: Item and Order Sync Adapter Interface

Status: complete.

**Files:**
- Modify: `xianyu_agent/adapters.py`
- Modify: `xianyu_agent/browser_adapter.py`
- Modify: `xianyu_agent/server.py`
- Test: `tests/test_adapter_sync_contract.py`

- [x] **Step 1: Write failing adapter contract tests**

Expected behavior:

```python
assert hasattr(adapter, "sync_orders")
assert hasattr(adapter, "sync_my_listings")
```

- [x] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_adapter_sync_contract -v
```

Expected: FAIL for adapters that do not expose the contract.

- [x] **Step 3: Add provider-neutral methods**

Add methods that return safe stubs first:

```python
def sync_orders(self, cookie_id="default"):
    return {"ok": False, "adapter": self.name, "error": "sync_orders not implemented"}

def sync_my_listings(self, cookie_id="default"):
    return {"ok": False, "adapter": self.name, "error": "sync_my_listings not implemented"}
```

- [x] **Step 4: Wire browser adapter methods**

Browser adapter can delegate to existing `get_orders()` and `get_my_listings()` if available.

- [x] **Step 5: Run contract tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_adapter_sync_contract -v
```

Expected: PASS.

Implemented:

- `ListingAdapter.sync_orders()` and `ListingAdapter.sync_my_listings()` provide provider-neutral read-only sync previews.
- `StubAdapter` inherits safe empty previews.
- `BrowserAdapter` delegates to its existing async `get_orders()` and `get_my_listings()` methods.
- `XianGuanJiaAdapter` provides signed GET helpers for `/api/orders` and `/api/listings` preview sync.
- `GET /adapter/sync-preview` exposes the active adapter's preview result without persisting data.
- `GET /dashboard/opc-daily` reports `adapter_health.sync_capabilities`.

---

### Task 5.5: Per-Account Daily Operations Queue

Status: complete.

**Files:**
- Create: `xianyu_agent/account_ops_queues.py`
- Modify: `xianyu_agent/server.py`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Test: `tests/test_account_ops_queues.py`
- Test: `tests/test_console_ui_contract.py`

- [x] **Step 1: Write failing queue aggregation tests**

Expected behavior:

```python
result = build_account_ops_queues(...)
assert result["accounts"][0]["queues"]["pending_approvals"] >= 0
assert "buyer-secret" not in json.dumps(result)
```

- [x] **Step 2: Implement account queue aggregation**

Implemented:

- Groups WorkOrder approvals by `cookie_id`.
- Infers execution jobs by approval/work-order mapping.
- Infers message-intake records by approval/work-order mapping when the intake record itself does not store `cookie_id`.
- Keeps unknown work in an `unassigned` bucket.
- Sanitizes buyer IDs, chat IDs, raw cookies, tokens, API keys, secrets, and prompt bodies.

- [x] **Step 3: Add read-only dashboard route**

Implemented:

```http
GET /dashboard/account-queues
```

The route returns:

- `accounts[]`
- `unassigned`
- queue counts for pending approvals, queued execution, failed jobs, pending reply drafts, item count, and order count
- account prompt readiness
- sync capabilities
- next operator actions

- [x] **Step 4: Render account queue drilldown in console**

Implemented:

- `renderAccountQueueDrilldown`
- `renderAccountQueueCard`
- `.account-queue-grid`
- `.account-queue-card`

- [x] **Step 5: Run tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_account_ops_queues tests.test_console_ui_contract.ConsoleUIContractTests.test_console_renders_account_queue_drilldown -v
.\.venv\Scripts\python -m unittest tests.test_opc_dashboard tests.test_account_profiles tests.test_account_prompt_profiles -v
```

Result: PASS.

---

### Task 5.6: Account Attribution for New and Legacy Work

Status: complete.

**Files:**
- Create: `xianyu_agent/account_attribution.py`
- Modify: `xianyu_agent/work_orders.py`
- Modify: `xianyu_agent/message_intake.py`
- Modify: `xianyu_agent/server.py`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Test: `tests/test_account_attribution.py`
- Test: `tests/test_console_ui_contract.py`

Implemented:

- New WorkOrders infer the sole configured account when no `cookie_id` is provided.
- WorkOrder approvals inherit `cookie_id` from WorkOrder context into approval payload and approval snapshot.
- Ecommerce listing-draft WorkOrders and approvals now carry account attribution.
- Inbound buyer-message intake now carries account attribution through event-key generation, WorkOrder context, approval payload, approval snapshot, and message-intake ledger records.
- Added `POST /dashboard/account-queues/repair-attribution` for dry-run/apply repair of legacy unassigned WorkOrders.
- Added compact console controls to preview or apply local account attribution repair from the OPC dashboard.

Verified:

```powershell
.\.venv\Scripts\python -m unittest tests.test_account_attribution tests.test_account_ops_queues tests.test_ecommerce_channel_integration tests.test_automatic_message_intake -v
.\.venv\Scripts\python -m unittest tests.test_console_ui_contract.ConsoleUIContractTests.test_console_has_account_attribution_repair_controls tests.test_console_ui_contract.ConsoleUIContractTests.test_console_renders_account_queue_drilldown -v
node --check web\app.js
git diff --check
```

---

### Task 5.7: Sync Preview Persistence and Freshness

Status: complete.

**Files:**
- Create: `xianyu_agent/sync_persistence.py`
- Modify: `xianyu_agent/db_manager.py`
- Modify: `xianyu_agent/account_profiles.py`
- Modify: `xianyu_agent/account_ops_queues.py`
- Modify: `xianyu_agent/server.py`
- Test: `tests/test_sync_persistence.py`

Implemented:

- Added `account_sync_state` table for per-account resource freshness.
- Added `DBManager.save_account_sync_state()` and `DBManager.get_account_sync_state()`.
- Added `sync_account_snapshot()` to call the active adapter's `sync_orders()` and `sync_my_listings()` methods.
- Persisted synced orders into `orders` and synced listings into `items`.
- Added `POST /adapter/sync-persist?cookie_id=...&status=...`.
- Added `sync_freshness` to account profiles and per-account operator queues.
- Added a stale-sync next action when account data exists but is outside the freshness window.
- Kept `/adapter/sync-preview` as read-only.
- Kept API output sanitized: no raw cookies, buyer IDs, delivery payloads, or raw adapter payloads in the sync-persist response.

Verified:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_sync_persistence -v
.\.venv\Scripts\python.exe -m unittest tests.test_sync_persistence tests.test_adapter_sync_contract tests.test_account_profiles tests.test_account_ops_queues tests.test_opc_dashboard -v
```

Result: PASS, 27 related tests.

Next:

- Verify real XianGuanJia/browser sample payloads and extend field mapping only where necessary.
- Console sync buttons, freshness badges, and daily review snapshots are now implemented.
- Next step is a controlled real account loop with real adapter payload verification.

---

### Task 5.8: Console Sync Controls and Daily Review Snapshot

Status: complete.

**Files:**
- Modify: `xianyu_agent/opc_dashboard.py`
- Modify: `xianyu_agent/server.py`
- Modify: `web/console.html`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Test: `tests/test_daily_review_snapshot.py`
- Test: `tests/test_console_ui_contract.py`

Implemented:

- Added `OPCDashboard.generate_daily_review()` for local daily Markdown snapshots.
- Added `POST /dashboard/daily-review`.
- Daily snapshots include OPC daily metrics, account queues, unassigned queue counts, bottleneck, next actions, and per-account `sync_freshness`.
- Added a console "daily snapshot" button.
- Added a console first-account bootstrap form backed by `POST /accounts`.
- Added per-account sync buttons that call `/adapter/sync-persist`.
- Added account queue freshness badges for fresh/stale synced resources.
- Added account profile Prompt readiness shortcuts that call `/accounts/{cookie_id}/prompt-profile`.
- Added account profile Prompt bootstrap shortcuts that call `/accounts/{cookie_id}/prompt-profile/bootstrap`.
- Added account profile conversation-summary shortcuts that call `/accounts/{cookie_id}/conversation-summary?chat_id=...`.
- Added `GET /dashboard/first-run-readiness`.
- Added OPC console first-run readiness gates for account, adapter, sync, Prompt, inventory, attribution, and current platform state.

Verified:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_daily_review_snapshot tests.test_console_ui_contract tests.test_sync_persistence tests.test_account_ops_queues tests.test_opc_dashboard -v
.\.venv\Scripts\python.exe -m unittest tests.test_console_ui_contract tests.test_account_prompt_profiles tests.test_account_profiles tests.test_account_ops_queues -v
.\.venv\Scripts\python.exe -m unittest tests.test_first_run_readiness tests.test_console_ui_contract tests.test_opc_dashboard tests.test_account_ops_queues tests.test_daily_review_snapshot -v
.\.venv\Scripts\python.exe -m unittest tests.test_console_ui_contract tests.test_conversation_profiles tests.test_account_profiles tests.test_account_prompt_profiles -v
.\.venv\Scripts\python.exe -m unittest tests.test_console_ui_contract tests.test_first_run_readiness -v
.\.venv\Scripts\python.exe -m unittest tests.test_account_prompt_profiles tests.test_console_ui_contract tests.test_account_ops_queues tests.test_first_run_readiness -v
node --check .\web\app.js
git diff --check
```

Result: PASS.

---

## Self-Review

- Spec coverage: similar GitHub projects are mapped in `docs/research/github-reference-projects.md`; account profiles, prompt profiles, conversation summaries, sync preview, account queues, attribution repair, sync persistence, console sync controls, Prompt shortcuts, first-run readiness, and daily review snapshots are complete; remaining slices are broken into independent tasks.
- Placeholder scan: no TBD/TODO placeholders are used as implementation instructions.
- Type consistency: account profile, prompt profile, conversation profile, and adapter sync names are consistent across tasks.
