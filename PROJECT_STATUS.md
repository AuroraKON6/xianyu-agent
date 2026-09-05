# Xianyu Pi Commercial Alpha Status

Updated: 2026-09-05

## Status

- Technical code baseline: `READY` for current-runtime verification and the
  first real seller-account onboarding.
- Runtime now: `STOPPED`; FastAPI and Pi are intentionally not running, so no
  current health claim is made.
- Commercial operation: `BLOCKED` on an owned or authorized seller account,
  one exact supplier SKU, five current evidence files and current commercial
  terms.
- Real Commercial Alpha: `NOT STARTED` and not claimed complete.

## Verified In This Baseline

- Full isolated regression on 2026-09-05: `996` tests passed, `0` failed, in
  37.385 seconds. The suite used temporary `DATA_DIR` and `DB_PATH` boundaries;
  all 15 files under `data/live` retained identical lengths and SHA-256 hashes.
- One-SKU Commercial Alpha intake now has a read-only offline preflight. It
  generates a non-overwriting bundle template, validates the exact five evidence
  kinds and product images with the production archive rules, blocks traversal,
  symlinks, stale/future captures, malformed files and sensitive text, and
  projects the existing candidate gates without persisting synthetic evidence.
  A passing bundle remains `commercial_ready=false` until real archive import,
  account/category terms, current manual approval and Day-0 `20/20`.
- Candidate evidence is content-addressed and candidate-bound. Integrity checks
  now hash the file on every verification; a same-size tamper with the original
  modification timestamp is detected.
- SQLite initialization is deferred and bound by the server composition root to
  `Settings.db_path`. The legacy and live database table row counts were checked
  equal, and the migration backup remains in `data/sqlite-migration-backup`.
- Core Phase 9-29 engineering work has a recoverable Git baseline on branch
  `codex/opc-dashboard-v1`; the former large uncommitted implementation is no
  longer the project source of truth.
- The only platform write path exposed by the server is the approved chain:
  `WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor -> Adapter`.
- Execution jobs now use a persisted lease, per-claim token and increasing
  fencing token. Two local queue instances cannot claim the same task, and an
  abandoned `running` task becomes `verification_required` instead of being
  retried automatically.
- Unknown publish, edit, reply, delete, refund or fulfillment outcomes require
  an explicit platform check. The console and API record either
  `confirmed_completed` with platform evidence or `confirmed_not_executed`
  before a retry can be requested.
- Reply Outbox now projects reply drafts, approvals, execution attempts,
  receipts and manual verification from the existing authoritative chain. It
  binds account, buyer, chat, item, source event, revision and approved-message
  hash in an action digest; inbound replays return the original record, and an
  explicit idempotency key reused for different content returns HTTP `409`.
- Browser write failures now create a per-account hashed evidence pack with a
  redacted JSON manifest and a best-effort screenshot. The pack records the
  failure stage, adapter state and whether a platform write was attempted;
  post-submit uncertainty still requires explicit platform verification.
- Every new Approval now persists an action-scope envelope binding the account,
  targets, payload, approval snapshot, amounts, expected versions, lifetime,
  deterministic idempotency key and unique correlation ID. Approval stores the
  exact action digest that the operator accepted.
- Scope is recomputed before approval, enqueue, execution and unknown-result
  closure. A mismatch or expiry blocks before the adapter and records
  `write_attempted=false`; account reassignment clears the old decision and
  requires a fresh approval.
- Listing publication additionally binds the normalized on-disk draft and image
  contents. Legacy publication approvals without `expected_draft_revision` are
  blocked and must be recreated.
- Generic `/agents/dispatch`, `/agents/openclaw/message` and
  `/agents/hermes/message` are retired with HTTP `410`.
- Legacy listing and physical-listing APIs create a local draft and approval
  WorkOrder. The core workflow never publishes merely because `publish=true`.
- Direct polish and both automatic polish schedulers are retired.
- Pi remains the only primary natural-language entrypoint. Its allowlist now
  contains 12 typed Xianyu tools and no arbitrary-path `read`, shell, write,
  approval or execution tool.
- The Pi subprocess receives only model-provider and operating-system runtime
  environment variables, not unrelated database, Feishu or adapter credentials.
- The default service bind is `127.0.0.1`; CORS is limited to the local console
  origins for the configured port.
- OpenClaw Windows scheduled tasks previously found were disabled. This baseline
  does not re-enable OpenClaw or any automatic startup process.

## Not Verified As Current Runtime

- No process is currently listening on `127.0.0.1:8788` or the older port 8000.
- The 2026-08-29 FastAPI, Pi and browser checks are historical evidence, not a
  current health result.
- Before account login, run the normal start script and verify health, Pi status,
  the absolute live SQLite path, one-process behavior and clean stop behavior.
- No current live-model Pi turn or current browser UI regression was run in this
  documentation and hardening change.

## Current Business Ledger

| Item | Current value |
|---|---:|
| Historical WorkOrders | 15, all quarantined |
| Unresolved WorkOrders | 0 |
| Seller accounts | 0 |
| Domestic Xianyu candidates | 1 placeholder (`T`), evaluating |
| Placeholder activation packet | 1/12, `ready=false` |
| First product blocker | `product_identity` |
| Candidate evidence artifacts | 0 |
| Offline intake bundle | `E:\闲鱼\商业资料\commercial-alpha-001`, initialized and correctly blocked pending real inputs |
| Active Commercial Alpha trials | 0 |
| Real listing receipts | 0 |
| Real inquiries / paid orders / reconciled profit | 0 / 0 / 0 |
| First global readiness blocker | `account_configured` |

## Next Unique Goal

> Bind one real seller account and complete one exact SKU to candidate `12/12`
> and Day-0 `20/20`, then create one Commercial Alpha batch without publishing
> automatically.

## Next Operating Sequence

1. Create one offline intake directory with
   `scripts/commercial_alpha_intake.py init`; fill one exact SKU, five current
   evidence files and at least one authorized product image.
2. Run its read-only `validate` command until `bundle_ready=true`, the projected
   candidate is `11/12`, and only `manual_decision_current` remains. Do not treat
   this projection as a persisted candidate or commercial approval.
3. Start the service only when the operator is ready to perform the real login.
4. Re-run current health, Pi, database-path and repeated-start checks.
5. In `账号画像`, open the dedicated login browser and complete platform login.
6. Preview the actual identity, verify it, and complete the one-time `BIND ...`
   confirmation.
7. Initialize the account Prompt profile, then run same-account listing and
   order sync readiness checks.
8. Do not approve placeholder `T`. Create one exact domestic Xianyu candidate
   with at least two real deliverable specifications.
9. Add current supplier, SKU, cost, freight, inventory and fulfillment facts.
10. Upload and activate the same five validated evidence files: supplier quote, stock,
   fulfillment, image rights and market baseline.
11. Record current account-and-category terms. Reprice the current candidate,
   run Agent review and manually approve the exact current revision.
12. Require candidate `12/12` and Day-0 launch preview `20/20` before creating
     exactly one batch. Batch creation still does not publish.
13. Publish only through a separate approval and execution job. Unknown platform
     results must be verified before any retry.
14. Handle every real inquiry through Reply Outbox. Approval, enqueue and a
     confirmed platform receipt remain three separate operator decisions.
15. Expand only after a real 14-day experiment and, when an order exists, full
     procurement, dual-logistics, delivery and actual-profit reconciliation.

## Source Documents

- General-manager target architecture and 12-month plan:
  `docs/strategy/GENERAL_MANAGER_BLUEPRINT_2026-08-30.md`
- Commercial operating order: `docs/ops/commercial-alpha-runbook.md`
- Candidate evidence SOP: `docs/ops/xianyu/candidate-evidence-archive.md`
- Offline one-SKU intake preflight: `docs/ops/commercial-alpha-intake-bundle.md`
- Current handoff and remaining work: `ZCODE_HANDOFF.md`
- Pi runtime contract: `docs/ops/pi-migration.md`
- Unknown-result recovery SOP: `docs/ops/execution-recovery-runbook.md`
- Reply lifecycle and idempotency SOP: `docs/ops/reply-outbox-runbook.md`
- Browser failure evidence SOP: `docs/ops/browser-write-evidence-runbook.md`
- Action scope and approval binding SOP: `docs/ops/action-scope-approval-runbook.md`
