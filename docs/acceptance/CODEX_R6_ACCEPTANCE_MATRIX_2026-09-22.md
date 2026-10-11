# R6 Final Offline Acceptance Matrix

Date: 2026-09-22. Workspace: E:\闲鱼\xianyu.

## Result

PASS for the local offline recovery scope below. NOT a real-Pi, platform,
production, or commercial release approval. No real provider calls or platform
actions were made. This report supersedes earlier same-day test totals, without
rewriting their historical evidence.

Base HEAD remains 4bd2cf7. Worktree changes are uncommitted and unstaged.
Concurrent in-workspace fixes and tests were preserved and included in validation.

## Plan Progress

| Planned item | Result | Current implementation |
| --- | --- | --- |
| Durable intent before model request | PASS | Intent write failure prevents client construction |
| Settlement keyed by job and attempt | PASS | Exact replay reuses the fact; new attempt is a separate entry |
| Conflicting settlement | PASS | Reject; retain original amount/day/model; deduplicate conflict audit |
| Unknown consumption across restarts/dates | PASS | Blocks further model calls, including legacy unknown entries |
| Known usage before output persistence | PASS | Settle before evidence, decision, and job completion writes |
| Process termination recovery | PASS, FakePi only | Kill the test-owned child after provider-entry marker; reload pending intent |
| Shadow log/usage/cursor recovery | PASS | Rebuild one allowance from original saved verdict; never reauthorize replay |
| Source disappearance or old advanced cursor | PASS | Recover saved log events before evaluating new events |
| Nonempty quote evidence chain | PASS, offline only | Two source=demo quotes, one unknown freight, persisted decision and input hash |
| Real Pi with nonempty demo | NOT RUN | Separate isolated runtime gate, not counted as this test suite |
| Manual unknown-usage reconciliation | NOT IMPLEMENTED | No direct JSON edits or automatic zeroing to unblock |
| Automatic trading | NOT ENABLED | No change to existing approval and execution controls |

## Failure Matrix

| Injected window | Durable facts | Reload/replay assertions |
| --- | --- | --- |
| Intent persist fails | No consumption intent | Client factory called zero times |
| Prompt raises | Unknown entry | New runner refuses another client; cross-day block retained |
| Child killed inside FakePi prompt | Pending intent, leased job | Expired lease recovery twice; no model retry; no decision |
| Usage settlement persist fails | Pending intent survives | No output evidence; later model factory not called |
| Evidence file write fails | Known usage 15 | No valid decision; usage still 15 after reload |
| Decision persist fails | Known usage 15 | No valid decision; usage still 15 after reload |
| Completion persist fails | Known usage 15, dangling decision | Hidden before recovery; expired-lease reconciliation completes once without new model call |
| Same attempt settlement replay | One settled entry | Reload and replay twice: still 15, no duplicate |
| Conflicting total/model/day or unknown-to-zero | Original fact unchanged | Rejected; conflict audit retained and deduplicated |
| New attempt on same job | Distinct attempt records | Settling old attempt does not clear newer unknown |
| Shadow log persist fails | No new verdict or allowance | Cursor not advanced; retry evaluates once |
| Shadow usage persist fails | Saved verdict | Restore original allowance before new events or cursor advance |
| Shadow cursor persist fails | Saved verdict and allowance | Replay twice with no extra allowance |
| Saved event source removed | Saved verdict without projection | Restore old allowance first; max_actions=1 rejects new event |
| Old cursor already advanced | Saved verdict without projection | Same recovery; cursor does not override authoritative log |
| Existing projection differs from saved verdict | Conflicting evidence retained | Stop for manual reconciliation; no silent overwrite |
| Legacy allowed event lacks reconstruction data | No trustworthy amount | Stop; do not guess zero or advance cursor |
| Replay across date/mandate changes | Original day, amount, mandate | Restore original facts, not current proposal values |

Shadow amount drill: daily limit 100, events 60 then 70. All three persistence
failure windows allow only 60 after two replays. Action-count drills independently
check max_actions=1. These are counterfactual allowances, not real money.

## Red-to-Green Evidence

Before the completion implementation was present, the expanded round6 module ran
16 tests: two behavioral failures and five API errors. The behavioral failures
were known usage still None after evidence failure, and evidence existing before
failed usage settlement. The API errors exposed missing attempt-key settlement.

After the main fixes, additional shadow tests reproduced three failing assertions:
two allowed events under max_actions=1 when the old source was absent (with both
cursor states), and failure to reject a conflicting existing projection. Recovery
now iterates saved event logs before fresh evaluation and validates existing rows.

Final focused command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_pi_recovery*.py
```

Three consecutive results: 32 tests OK, in 1.070s, 1.129s, and 1.087s.
Modules: test_pi_recovery_round6.py (19) and test_pi_recovery_completion.py (13).

Full command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_tests_isolated.ps1
```

Result: **1386 tests in 44.209s, OK, exit code 0**.
The existing test_wp08_script.py ResourceWarning messages remain.
Script-test output containing REAL_RUN_OK with model=mock-model is not real
provider evidence and is not counted as such.

`git diff --check` and `python -m py_compile` for changed runtime/test modules
passed. Before/after live-data audit: 20 files, identical path set and SHA-256
contents. See CODEX_R6_VALIDATION_MANIFEST_2026-09-22.json for hash evidence.

## Code Pointers

- xianyu_agent/pi_jobs.py:69: attempt ledger, replay, validation, conflict audit.
- xianyu_agent/pi_jobs.py:704: failed-call accounting; :711 known usage before outputs.
- xianyu_agent/budget_reservations.py:247: read-only list of persisted event logs.
- xianyu_agent/shadow_observer.py:149: restore and compare allowance projections.
- xianyu_agent/shadow_observer.py:175: recover durable events before new evaluation.
- tests/test_pi_recovery_round6.py:119: actual persistence-window and child-kill drills.
- tests/test_pi_recovery_round6.py:347: missing-source and conflicting-projection regressions.
- tests/test_pi_recovery_round6.py:389: source=demo nonempty offline integration.

## Next Sequence

1. Implement evidence-backed reconciliation for unresolved model attempts as a
   separate bounded work package. Require operator, original attempt, provider
   usage evidence, evidence digest, reason and idempotency key. Preserve unknown
   history; do not treat a new successful call as proof about an old attempt.
   Test conflict, repeated submission, audit write failure, restart and cross-day
   assignment before exposing an operator control.
2. Prepare an isolated real-Pi harness before running it. The current
   scripts/run_pi_quote_review_real.py hardcodes data/live and port 8788; do not
   use it unchanged for demo. Require a non-live data root and database, owned
   server/available port, dedicated session, source=demo labels and explicit
   model-call enablement. Never attach a demo run to an unrelated existing server.
3. Run one bounded nonempty real Pi read-only task only through that isolated
   harness. Verify job -> Pi session/tool events -> actual input hash -> known
   usage -> structured decision -> evidence endpoint, and data/live isolation.
   Keep denied/stale/unknown outcomes visible; no automatic retries on unknown.
4. Only then consider longer shadow observation with real supplier/account facts.
   Demo data, empty snapshots, test counts and HTTP 200 do not qualify a commercial
   launch or permit automatic replies, listings, procurement, or refunds.

## Limits

The stores remain single-process JSON with atomic replacement, not a multi-process
transactional database. The child-kill drill is not a power-loss/fsync or real
provider-billing test. The budget gates do not enforce an upstream hard token cap.
Manual reconciliation and real runtime validation remain separate gates. No new
UI, startup entry, background service, or recurring automation was installed.
