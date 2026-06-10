# DeepSeek V4 Flash and Feishu CLI Long Connection Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Ollama, move customer service to DeepSeek V4 Flash, and document Feishu as a CLI-managed long-connection event channel.

**Architecture:** The local FastAPI service remains the runtime host for OpenClaw-facing tools and internal specialist agents. `customer_service` uses DeepSeek V4 Flash, while the other five specialist agents continue using MiMo through the existing `LLMManager`. Feishu events arrive through a Feishu CLI long connection and are bridged to the existing `/feishu/events` route.

**Tech Stack:** Python 3, FastAPI, unittest, local JSON persistence, browser adapter, OpenClaw manifest YAML, frontend JavaScript.

---

## File Structure

- Modify: `xianyu_agent/llm_manager.py`
  - Change `customer_service` backend to `deepseek`.
  - Force customer service model to `deepseek-v4-flash`.
  - Keep MiMo configuration for the five specialist agents.

- Modify: `xianyu_agent/llm.py`
  - Remove or disable Ollama runtime branches.
  - Ensure `DeepSeekClient` never calls `/api/generate`.
  - Keep DeepSeek OpenAI-compatible chat completions and MiMo Anthropic-compatible calls.

- Modify: `xianyu_agent/config.py`
  - Remove `ollama_base_url` and `ollama_model` from settings if practical.
  - If compatibility forces temporary fields, do not use them anywhere.
  - Remove `FEISHU_WEBHOOK_URL` if not needed by runtime.
  - Keep `FEISHU_VERIFICATION_TOKEN` if `/feishu/events` still verifies events.

- Modify: `xianyu_agent/cli.py`
  - Remove `ollama_configured`, `ollama_base_url`, `ollama_model` from config-check output.
  - Do not encourage `FEISHU_WEBHOOK_URL`.

- Modify: `xianyu_agent/agents/customer_service.py`
  - Update docstring and description to DeepSeek V4 Flash.

- Modify: `xianyu_agent/agent_registry.py`
  - Update architecture comments from `Customer Service (Ollama)` to `Customer Service (DeepSeek V4 Flash)`.

- Modify: `xianyu_agent/server.py`
  - Update endpoint docstrings that mention Ollama.
  - Keep `/feishu/events` route.

- Modify: `.env.example`
  - Remove Ollama env examples.
  - Remove or deprecate `FEISHU_WEBHOOK_URL`.
  - Add Feishu CLI long-connection notes if the file contains comments.

- Modify: `README.md`
  - Replace Ollama text with DeepSeek V4 Flash.
  - Replace webhook/public tunnel Feishu setup with Feishu CLI long connection.

- Modify: `skills/xianyu-commerce-agent/SKILL.md`
  - Replace Ollama text with DeepSeek V4 Flash.
  - Replace Feishu webhook setup with Feishu CLI long connection.

- Modify: `openclaw/xianyu-commerce-agent.openclaw.yaml`
  - Change Feishu channel to `connection: feishu_cli_long_connection`.
  - Remove `event_endpoint`.

- Modify: docs under `docs/`
  - Replace webhook/public tunnel wording.
  - Remove Ollama references.

- Test: `tests/test_llm_manager.py`
  - Add model/backend assertions.

- Test: add `tests/test_deepseek_feishu_migration.py`
  - Add route, config, docs, and source guard tests.

---

### Task 1: Lock Customer Service Backend Behavior

**Files:**
- Modify: `tests/test_llm_manager.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_llm_manager.py`:

```python
    def test_customer_service_uses_deepseek_v4_flash(self):
        settings = Settings.from_env()
        llm_manager.init(settings)

        client = llm_manager.get_client("customer_service").client

        self.assertEqual(AGENT_DEFINITIONS["customer_service"]["backend"], "deepseek")
        self.assertEqual(client.settings.deepseek_model, "deepseek-v4-flash")
        self.assertEqual(client.settings.mimo_api_key, "")
        self.assertEqual(client.settings.mimo_base_url, "")

    def test_list_agents_has_no_ollama_backend(self):
        settings = Settings.from_env()
        llm_manager.init(settings)

        agents = llm_manager.list_agents()

        self.assertNotIn("ollama", {agent["backend"] for agent in agents})
        customer_service = next(agent for agent in agents if agent["name"] == "customer_service")
        self.assertEqual(customer_service["backend"], "deepseek")
        self.assertEqual(customer_service["model"], "deepseek-v4-flash")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_llm_manager
```

Expected: FAIL because `customer_service` still uses `ollama`.

- [ ] **Step 3: Implement `llm_manager.py` changes**

Update `AGENT_DEFINITIONS`:

```python
AGENT_DEFINITIONS = {
    "customer_service": {
        "description": "Auto-reply to Xianyu buyers",
        "backend": "deepseek",
        "model": "deepseek-v4-flash",
        "key_field": None,
    },
    ...
}
```

Update `_create_client()` with an explicit DeepSeek branch before the MiMo branch:

```python
        if backend == "deepseek":
            agent_settings = replace(
                s,
                deepseek_api_key=s.deepseek_api_key,
                deepseek_base_url=s.deepseek_base_url,
                deepseek_model=self.definition.get("model", "deepseek-v4-flash"),
                mimo_api_key="",
                mimo_base_url="",
                mimo_model="",
                mimo_hermes_key="",
                mimo_listing_key="",
                mimo_discovery_key="",
                mimo_order_key="",
                mimo_github_key="",
                mimo_analytics_key="",
            )
        elif backend == "mimo":
            ...
        else:
            raise ValueError(f"Unsupported backend: {backend}")
```

Update `list_agents()` model selection:

```python
            if defn["backend"] == "deepseek":
                model = defn.get("model", self._settings.deepseek_model if self._settings else "deepseek-v4-flash")
            elif defn["backend"] == "mimo":
                model = self._settings.mimo_model if self._settings else "unknown"
            else:
                model = "unknown"
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_llm_manager
```

Expected: OK.

---

### Task 2: Remove Runtime Ollama Paths

**Files:**
- Modify: `xianyu_agent/llm.py`
- Test: `tests/test_deepseek_feishu_migration.py`

- [ ] **Step 1: Write failing runtime guard tests**

Create `tests/test_deepseek_feishu_migration.py`:

```python
import unittest
from pathlib import Path


class DeepSeekFeishuMigrationTests(unittest.TestCase):
    def test_llm_runtime_does_not_reference_ollama_generate(self):
        llm_py = Path("xianyu_agent/llm.py").read_text(encoding="utf-8")

        self.assertNotIn("/api/generate", llm_py)
        self.assertNotIn("_ollama_chat", llm_py)
        self.assertNotIn("_use_ollama", llm_py)

    def test_customer_service_docs_use_deepseek_flash(self):
        source = Path("xianyu_agent/agents/customer_service.py").read_text(encoding="utf-8")

        self.assertIn("DeepSeek V4 Flash", source)
        self.assertNotIn("Ollama", source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration
```

Expected: FAIL because `llm.py` still contains Ollama paths.

- [ ] **Step 3: Update `DeepSeekClient.enabled`**

Replace:

```python
return bool(self.settings.deepseek_api_key) or bool(self.settings.ollama_base_url) or bool(self.settings.mimo_api_key)
```

With:

```python
return bool(self.settings.deepseek_api_key) or (bool(self.settings.mimo_api_key) and bool(self.settings.mimo_base_url))
```

- [ ] **Step 4: Remove Ollama helpers**

Delete these members from `xianyu_agent/llm.py`:

```python
_use_ollama
_ollama_chat
_ollama_chat_json
```

Remove any branch that checks `self.settings.ollama_base_url` or calls Ollama.

- [ ] **Step 5: Update effective properties**

Use:

```python
    @property
    def _effective_base_url(self) -> str:
        if self._use_mimo:
            return self.settings.mimo_base_url
        return self.settings.deepseek_base_url

    @property
    def _effective_model(self) -> str:
        if self._use_mimo:
            return self.settings.mimo_model
        return self.settings.deepseek_model

    @property
    def _effective_api_key(self) -> str:
        if self._use_mimo:
            return self.settings.mimo_api_key
        return self.settings.deepseek_api_key
```

- [ ] **Step 6: Update chat and chat_json comments**

Use comments that match behavior:

```python
"""JSON response - uses MiMo for MiMo-configured agents, otherwise DeepSeek."""
"""Plain text chat - uses MiMo for MiMo-configured agents, otherwise DeepSeek."""
```

- [ ] **Step 7: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration tests.test_llm_manager
```

Expected: OK.

---

### Task 3: Clean Settings and CLI Output

**Files:**
- Modify: `xianyu_agent/config.py`
- Modify: `xianyu_agent/cli.py`
- Modify: `.env.example`
- Test: `tests/test_deepseek_feishu_migration.py`

- [ ] **Step 1: Add failing config and CLI source tests**

Append to `DeepSeekFeishuMigrationTests`:

```python
    def test_cli_config_check_does_not_advertise_ollama_or_feishu_webhook(self):
        cli_py = Path("xianyu_agent/cli.py").read_text(encoding="utf-8")

        self.assertNotIn("ollama_configured", cli_py)
        self.assertNotIn("ollama_base_url", cli_py)
        self.assertNotIn("ollama_model", cli_py)
        self.assertNotIn("feishu_webhook", cli_py.lower())

    def test_env_example_does_not_advertise_ollama_or_feishu_webhook(self):
        env_example = Path(".env.example").read_text(encoding="utf-8")

        self.assertNotIn("OLLAMA_BASE_URL", env_example)
        self.assertNotIn("OLLAMA_MODEL", env_example)
        self.assertNotIn("FEISHU_WEBHOOK_URL", env_example)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration
```

Expected: FAIL while old CLI/env references remain.

- [ ] **Step 3: Remove Ollama settings**

In `Settings`, delete fields:

```python
ollama_base_url: str
ollama_model: str
```

In `Settings.from_env`, delete:

```python
ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "").rstrip("/") or "",
ollama_model=os.environ.get("OLLAMA_MODEL", ""),
```

If other tests require temporary compatibility, stop and update those references in the same task instead of keeping live Ollama paths.

- [ ] **Step 4: Remove Feishu webhook setting if unused**

Search:

```powershell
Get-ChildItem -Path . -Recurse -File | Select-String -Pattern "feishu_webhook_url|FEISHU_WEBHOOK_URL"
```

If only docs/config mention it, remove:

```python
feishu_webhook_url: str
feishu_webhook_url=os.environ.get("FEISHU_WEBHOOK_URL", ""),
```

If runtime code uses it, keep the field temporarily but mark it deprecated and remove all user-facing setup text. The preferred outcome is full removal.

- [ ] **Step 5: Update CLI config-check**

Remove these output keys from `xianyu_agent/cli.py`:

```python
"ollama_configured"
"ollama_base_url"
"ollama_model"
"feishu_webhook_configured"
"feishu_webhook_url"
```

Keep:

```python
"deepseek_configured"
"deepseek_model"
"mimo_model"
"feishu_token_configured"
```

- [ ] **Step 6: Update `.env.example`**

Ensure it contains DeepSeek and MiMo examples, not Ollama:

```text
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

# Customer service uses DeepSeek V4 Flash internally.
# Specialist agents use MiMo.
MIMO_BASE_URL=https://token-plan-sgp.xiaomimimo.com/anthropic
MIMO_MODEL=mimo-v2.5-pro
```

Ensure Feishu says CLI long connection:

```text
# Feishu is connected by Feishu CLI long connection.
# Local event handler remains /feishu/events.
FEISHU_VERIFICATION_TOKEN=
```

- [ ] **Step 7: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration tests.test_settings_env tests.test_llm_manager
```

Expected: OK.

---

### Task 4: Update Feishu CLI Long-Connection Manifest and Docs

**Files:**
- Modify: `openclaw/xianyu-commerce-agent.openclaw.yaml`
- Modify: `README.md`
- Modify: `skills/xianyu-commerce-agent/SKILL.md`
- Modify: `docs/openclaw_feishu_workflow.md`
- Test: `tests/test_deepseek_feishu_migration.py`

- [ ] **Step 1: Add failing docs and manifest tests**

Append:

```python
    def test_manifest_uses_feishu_cli_long_connection(self):
        manifest = Path("openclaw/xianyu-commerce-agent.openclaw.yaml").read_text(encoding="utf-8")

        self.assertIn("feishu_cli_long_connection", manifest)
        self.assertIn("local_event_path: /feishu/events", manifest)
        self.assertNotIn("YOUR_PUBLIC_TUNNEL", manifest)
        self.assertNotIn("event_endpoint", manifest)

    def test_docs_do_not_request_public_feishu_webhook(self):
        paths = [
            Path("README.md"),
            Path("skills/xianyu-commerce-agent/SKILL.md"),
            Path("docs/openclaw_feishu_workflow.md"),
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())

        self.assertNotIn("YOUR_PUBLIC_TUNNEL", combined)
        self.assertNotIn("FEISHU_WEBHOOK_URL", combined)
        self.assertNotIn("内网穿透", combined)
        self.assertIn("飞书 CLI 长连接", combined)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration
```

Expected: FAIL while old webhook docs remain.

- [ ] **Step 3: Update OpenClaw manifest**

Use:

```yaml
channels:
  feishu:
    enabled: true
    connection: feishu_cli_long_connection
    local_service: http://127.0.0.1:8788
    local_event_path: /feishu/events
    notes:
      - Feishu CLI maintains the long-lived event connection.
      - Events are bridged to the local FastAPI event handler.
```

Remove:

```yaml
event_endpoint: http://YOUR_PUBLIC_TUNNEL/feishu/events
```

- [ ] **Step 4: Update README Feishu section**

Replace public webhook/tunnel setup with:

````markdown
## 飞书 CLI 长连接接入

飞书不再通过公网 webhook 或内网穿透接入。请先使用飞书 CLI 完成登录、应用绑定和长连接事件订阅。飞书 CLI 负责把长连接收到的消息事件桥接到本地服务。

本地服务地址：

```text
http://127.0.0.1:8788
```

本地事件入口：

```text
/feishu/events
```

项目侧只处理收到的事件，不负责维护公网 webhook URL。
```
````

- [ ] **Step 5: Update skill and docs**

In `skills/xianyu-commerce-agent/SKILL.md`, add:

````markdown
## Feishu CLI Long Connection

Use Feishu CLI to log in, bind the app, and maintain the long-lived event connection. Bridge message events to the local FastAPI handler:

```text
http://127.0.0.1:8788/feishu/events
```

Do not configure public webhook tunnels for this project.
```
````

In `docs/openclaw_feishu_workflow.md`, replace webhook wording with the same CLI long-connection model.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration
```

Expected: OK.

---

### Task 5: Verify `/feishu/events` Route and Service Compatibility

**Files:**
- Test: `tests/test_deepseek_feishu_migration.py`
- Modify: `xianyu_agent/server.py` only if docstrings mention old behavior.

- [ ] **Step 1: Add route preservation test**

Append:

```python
    def test_feishu_events_route_still_exists(self):
        from xianyu_agent.server import app

        routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}

        self.assertIn(("/feishu/events", ("POST",)), routes)
```

- [ ] **Step 2: Run test**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration.DeepSeekFeishuMigrationTests.test_feishu_events_route_still_exists
```

Expected: OK. If it fails, restore the route without changing its behavior.

- [ ] **Step 3: Update server docstrings if needed**

If `server.py` mentions webhook setup in comments/docstrings, replace with:

```python
"""Receive events bridged from the Feishu CLI long-connection channel."""
```

- [ ] **Step 4: Run route and server tests**

Run:

```powershell
.\.venv\Scripts\python -m unittest tests.test_deepseek_feishu_migration tests.test_openclaw_router
```

Expected: OK.

---

### Task 6: Full Verification

**Files:**
- No code changes unless verification finds a real bug.

- [ ] **Step 1: Run the full Python test suite**

Run:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 2: Run JavaScript syntax check**

Run:

```powershell
node --check web\app.js
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run source guard searches**

Run:

```powershell
Get-ChildItem -Path xianyu_agent,tests,web,docs,skills,openclaw -Recurse -File |
  Select-String -Pattern "Ollama|OLLAMA|/api/generate|YOUR_PUBLIC_TUNNEL|FEISHU_WEBHOOK_URL|event_endpoint"
```

Expected: no matches except generated caches or deliberately documented migration notes. Do not count `__pycache__`.

- [ ] **Step 4: Start service and check health**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_xianyu_workflow.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_xianyu_workflow.ps1
Invoke-RestMethod http://127.0.0.1:8788/health
Invoke-RestMethod http://127.0.0.1:8788/agents
```

Expected:

- health returns `ok: true`
- `/agents` shows `customer_service` backend `deepseek`, model `deepseek-v4-flash`
- no agent backend is `ollama`

---

## Self-Review

Spec coverage:

- Customer service DeepSeek V4 Flash: Task 1 and Task 2.
- Remove Ollama runtime paths: Task 2 and Task 3.
- Feishu CLI long connection: Task 4 and Task 5.
- Keep `/feishu/events`: Task 5.
- Preserve MiMo agents: Task 1 test keeps only customer service on DeepSeek and MiMo branch unchanged.
- Preserve existing work-order/execution queue behavior: Task 6 full tests.

Placeholder scan:

- No placeholder markers or unspecified test requests.
- Every task has exact files, tests, commands, and expected results.

Type consistency:

- Uses existing `Settings`, `LLMManager`, `AgentLLMClient`, `DeepSeekClient`, and FastAPI route patterns.
- Uses `deepseek-v4-flash` as the explicit customer-service model.
