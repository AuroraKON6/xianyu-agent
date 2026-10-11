# R6 Reliability Implementation

## Scope

Implemented locally against HEAD 4bd2cf7 (fix baseline 92b644a).
Changes are uncommitted. No real model calls or platform transactions were run.
Existing review and execution-handoff documents were preserved.

## Implemented

1. Model calls persist a pending usage intent before constructing the client.
   Any unresolved intent or historical unknown usage blocks subsequent calls,
   including after file reload and on a later date. Known usage settles the
   pending record. Failed settlement leaves the durable pending intent intact.
2. The default usage ledger follows the job store data root instead of a shared
   relative data directory.
3. Shadow logs persist the allowance amount alongside the original business day
   and mandate. Replay restores missing allowance before advancing the cursor,
   using the original facts rather than reevaluating authorization.
4. Existing allowance events remain idempotent. Legacy allowed events missing
   both allowance and reconstruction evidence stop for manual reconciliation.
5. Retry coverage now uses a known-cost invalid response before a successful
   retry. The evidence endpoint test uses an isolated usage ledger.

## Verification

- New test module: tests/test_pi_recovery_round6.py, six tests passed (0.116s).
- Coverage: unknown usage after restart; pending intent across dates; known
  settlement; failed settlement persistence; shadow log/usage/cursor failure
  windows; replay with the original date, mandate and amount.
- Full command: powershell -NoProfile -ExecutionPolicy Bypass -File
  .\scripts\run_tests_isolated.ps1
- Full result: 1360 tests, OK, 53.266s, exit code 0.
- git diff --check: passed before this documentation addition.
- Existing ResourceWarning messages from test_wp08_script.py remain.
- Tests used isolated temporary storage. No before/after live-data hash audit
  was performed; do not describe this run as a verified live-data hash match.

## Boundaries and Remaining Work

This is the conservative stop-on-unknown fix, not completion of every work
package in WP_PI_R6_EXECUTION_HANDOFF_2026-09-22.md.

1. Add explicit attempt-key settlement idempotency and conflicting-settlement
   rejection. Current record() settlement is not a general replay API.
2. Move known usage persistence ahead of evidence-file writing. Currently an
   evidence write failure retains a blocking pending intent, not known usage.
3. Add provider-entry subprocess termination recovery and pre-call intent-write
   failure tests. Current restart tests reload service instances, not kill a
   process during execution.
4. Add evidence-backed manual consumption reconciliation; do not delete or zero
   unknown entries to resume operation.
5. Validate nonempty source=demo quotes through the real Pi read-only chain in
   an isolated data root, separately from mock tests and live business facts.

No quantitative upstream token reservation/cap or multi-process transaction
guarantee is claimed. Automatic trading remains unopened. Passing this suite
does not establish production or commercial readiness.
