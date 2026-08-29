# Xianyu Pi Commercial Alpha Status

Updated: 2026-08-26

## Status

- Technical system: `READY` for the first real seller-account and one-product
  evidence onboarding.
- Commercial operation: `BLOCKED` until an owned or authorized seller account
  is logged in and bound from the dedicated browser.
- Real Commercial Alpha: not started and not claimed complete.

## Verified Now

- FastAPI service: healthy at `http://127.0.0.1:8788`.
- Pi: running with DeepSeek V4 Flash and 13 allowlisted tools (`read` plus 12
  typed Xianyu tools).
- Execution adapter: `browser`.
- Candidate evidence: five evidence types are now stored as immutable,
  candidate-bound, SHA-256 verified artifacts. Free-text paths and URLs no
  longer satisfy production activation gates.
- Full isolated regression: 926 tests passed with a temporary `DATA_DIR` and
  `DB_PATH`; no test used `data/live`.
- Real Pi acceptance: the live DeepSeek-backed session called only
  `xianyu_candidate_evidence`, reported zero archived evidence for the
  placeholder candidate and the five missing kinds, and made no external
  action.
- Browser UI: desktop `1280x720` and mobile `390x844` evidence archive passed
  with five read-only canonical reference fields, stacked mobile upload
  controls, no horizontal overflow, and 0 console errors or warnings. QA sent
  only GET requests; it did not upload or save evidence.
- Account preview with no login browser: correctly blocked with HTTP 409 and
  created no account.
- Startup scripts reject stale PIDs by checking the recorded process command
  line. A stale PID that belonged to FlClash was removed without stopping that
  unrelated process; repeated start is idempotent.
- Windows startup audit: the three matching OpenClaw scheduled tasks are
  disabled; no matching registry Run item, Startup-folder item, or running
  OpenClaw process was found.

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
| Active Commercial Alpha trials | 0 |
| First global readiness blocker | `account_configured` |

## Next Operating Sequence

1. Operator opens `http://127.0.0.1:8788/console`.
2. In `账号画像`, click `打开登录浏览器` and finish the platform login.
3. Click `检测当前账号`; verify the numeric account ID and displayed name.
4. Click `确认绑定`; enter the exact one-time `BIND ...` phrase.
5. System refreshes account profiles. Operator initializes Prompt, performs
   same-account listing/order sync, and clears runtime verification blockers.
6. Do not approve the placeholder `T`. Create or complete one exact domestic
   Xianyu candidate, then open its `一品启动包`.
7. Complete product identity, exact SKU, supplier and economics facts. Upload
   the five actual evidence files: quote, stock, fulfillment, image rights and
   market baseline. Verify each artifact is `active` and integrity is intact;
   the system generates the reference fields.
8. Record current account/category terms and fees. This reprices the candidate
   and deliberately invalidates any older decision or evidence fingerprint.
9. Run Agent review against the repriced evidence, then manually approve the
   current revision. Require the packet to report 12/12 and `ready=true`.
10. Run account-scoped publish preflight, then require a real 20/20 Day-0 launch
    preview before one batch can start. Starting the batch still does not publish.
11. Only after one real publish receipt, inquiry, paid order, procurement, dual
    tracking, delivery, and actual-profit reconciliation may the scope expand.

The complete procedure is `docs/ops/commercial-alpha-runbook.md`; the file
collection SOP is `docs/ops/xianyu/candidate-evidence-archive.md`; the design
audit is `docs/research/candidate-activation-reference-2026-08-25.md`.
