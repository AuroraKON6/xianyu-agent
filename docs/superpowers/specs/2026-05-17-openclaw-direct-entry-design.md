# OpenClaw Direct Entry Design

Date: 2026-05-17

## Goal

Remove Hermes from the primary user-facing path. The user should talk directly to OpenClaw, and OpenClaw should use this project as a local Xianyu tool layer through HTTP endpoints and the `xianyu-commerce-agent` skill.

## Current State

The repository currently contains two overlapping orchestration ideas:

- The docs and `AgentRegistry` comments describe `User -> Hermes -> OpenClaw -> Agent Registry -> agents`.
- The actual implementation routes `HermesAgent` directly to `AgentRegistry`.
- The project already exposes local HTTP endpoints on `http://127.0.0.1:8788`, including `/health`, `/browser/*`, `/agents/dispatch`, `/listings`, `/discovery/*`, `/orders/*`, and `/research/github`.
- The OpenClaw gateway is available on this machine, and `qwen-orchestrator` is the default OpenClaw agent.
- The project has `skills/xianyu-commerce-agent/SKILL.md` and `openclaw/xianyu-commerce-agent.openclaw.yaml`, but the current OpenClaw config has `agents.defaults.skills = []`, so the skill is not guaranteed to be loaded by default.

## Target Architecture

```text
User
  -> OpenClaw channel / TUI / CLI
  -> OpenClaw default agent
  -> xianyu-commerce-agent skill
  -> Local Xianyu API at http://127.0.0.1:8788
  -> browser/listing/discovery/order/github/analytics modules
```

Hermes remains in the codebase only as a legacy compatibility endpoint during this phase. It is not the recommended conversation entrypoint and should not be shown as the main control path.

## Components

### OpenClaw Configuration

OpenClaw must be able to see and use the Xianyu skill. The implementation should:

- Sync `skills/xianyu-commerce-agent` into the OpenClaw skill locations already used by `scripts/install_openclaw_integration.ps1`.
- Add or repair OpenClaw config so `xianyu-commerce-agent` is available to the default OpenClaw agent.
- Avoid hardcoding secrets into the repository or generated config.
- Prefer configurable paths for OpenClaw CLI, gateway URL, and skill root.

### Xianyu Local Tool Layer

The Python FastAPI service remains the execution surface. The implementation should keep these stable:

- `GET /health`
- `GET /browser/status`
- `GET /browser/publish/confirm`
- `POST /browser/search`
- `GET /browser/orders`
- `GET /browser/my-listings`
- `POST /agents/dispatch`
- Existing business endpoints such as `/listings`, `/discovery/*`, `/orders/*`, and `/research/github`

The service should expose a small OpenClaw-oriented status endpoint so OpenClaw can quickly understand tool readiness without knowing every internal route.

### Console

The local web console should stop presenting itself as a chat-first Hermes/Agent console. It should become a local tool-layer console with:

- Xianyu service health.
- OpenClaw gateway health.
- Active adapter and browser login state.
- Quick actions for browser status, publish confirmation, my listings, orders, and search.
- Clear text that conversations should happen in OpenClaw, not this page.

### Hermes

Hermes should be marked legacy:

- Keep `/agents/hermes/message` for backward compatibility.
- Do not use it from the console.
- Update docs to say OpenClaw is the primary conversation entry.
- Do not delete Hermes in this phase, because removing it may break existing routes or tests.

## Data Flow

For a user request such as "帮我确认发布状态":

1. User sends the message to OpenClaw.
2. OpenClaw default agent uses `xianyu-commerce-agent`.
3. The skill instructs OpenClaw to call `GET http://127.0.0.1:8788/browser/publish/confirm`.
4. The Xianyu service checks the currently connected browser page.
5. OpenClaw summarizes the result to the user.

For a user request such as "搜索蓝牙耳机":

1. User sends the message to OpenClaw.
2. OpenClaw chooses the Xianyu search tool from the skill.
3. The local service executes `/browser/search` or `/discovery/search`.
4. OpenClaw summarizes candidates and asks for the next action.

## Error Handling

The implementation should make failures actionable:

- If OpenClaw gateway is down, the console and status endpoint should report the expected gateway URL and startup script.
- If the Xianyu API is down, OpenClaw instructions should tell the user to start `scripts/start_xianyu_workflow.ps1`.
- If the browser is not logged in, the tool layer should return login state without navigating away from active browser work.
- If platform verification appears, automation must stop and ask the user to complete verification manually.
- If OpenClaw config cannot be safely edited, report the exact config path and the required manual change.

## Testing

Automated tests should cover:

- OpenClaw status model and config parsing without requiring a live gateway.
- `/openclaw/status` or equivalent local readiness endpoint.
- Console no longer posts to `/agent/message`.
- Skill/docs contain the correct project path `E:\闲鱼\xianyu`, not mojibake paths.
- Existing browser publish confirmation tests continue to pass.

Manual verification should cover:

- `openclaw gateway health --json` returns `ok: true`.
- OpenClaw can list or load `xianyu-commerce-agent`.
- Local service returns healthy at `/health`.
- Console loads and shows OpenClaw/Xianyu status.
- A direct OpenClaw prompt can call the local Xianyu tool layer.

## Scope Boundaries

This phase does not:

- Delete Hermes files.
- Build mobile control.
- Replace the existing FastAPI service.
- Bypass Xianyu verification or risk controls.
- Introduce new external credentials.

## Spec Self-Review

- Marker scan: no unresolved markers remain.
- Consistency check: the target architecture consistently uses OpenClaw as the user entrypoint and Xianyu API as the tool layer.
- Scope check: this is one focused migration of entrypoint and configuration, not a rewrite of all agents.
- Ambiguity check: Hermes is explicitly retained as legacy compatibility and excluded from the main flow.
