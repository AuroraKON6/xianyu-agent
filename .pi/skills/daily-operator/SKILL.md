---
name: daily-operator
description: Produce the operator's single daily priority action from current read-only business evidence, with blocker IDs, timestamps, an owner, and a definition of done.
---

# Daily Operator

Use this skill when the operator asks "what should I do today" or asks for a
short daily standup. It is strictly read-only: it never approves, executes,
sends, publishes, or creates drafts.

1. Call `xianyu_business_snapshot` and state the current account, candidate,
   work-order, and verification counts with their timestamps.
2. Call `xianyu_work_orders` and list any work order that is not completed,
   quarantined, or healthy: `verification_required` executions, failed jobs,
   and pending approvals. Each unknown platform result must be reported as a
   human verification task per the execution-recovery runbook; never propose
   a retry.
3. If the snapshot's first blocker is account-side, direct the operator to the
   console account flow (dedicated login browser, detect, verify, one-time
   BIND). Never ask for a Cookie in chat.
4. If the first blocker is candidate-side, call `xianyu_candidates` and then
   `xianyu_candidate_activation_packet` for the selected candidate, and report
   the first failed gate with its evidence class and artifact status via
   `xianyu_candidate_evidence` when integrity or freshness is involved.
5. Choose exactly one priority action for today. Derive it from the first
   business blocker in the strict order: resolve verification-required
   executions, then account onboarding, then candidate evidence gates, then
   commercial terms, then launch preview. Everything else is explicitly
   declared out of scope today.
6. Output the daily brief in this exact shape:
   - `priority_action`: one sentence.
   - `blocker_evidence`: gate or job ID, status, and timestamp for every claim.
   - `owner`: operator, system, or assistant.
   - `definition_of_done`: the observable check that closes the blocker.
   - `explicitly_not_today`: at most three deferred items with reasons.
7. Refuse to report readiness from stale evidence: if sync freshness, terms
   age, or runtime heartbeats exceed their limits, say so and make refreshing
   the evidence part of the priority action.
8. Mock, synthetic, quarantined, and placeholder records (including candidate
   `T`) are never business evidence and must not be cited as progress.

Detailed contracts: `docs/ops/execution-recovery-runbook.md`,
`docs/ops/commercial-alpha-runbook.md`.
