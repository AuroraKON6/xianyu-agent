---
name: commercial-alpha
description: Operate the real Xianyu Commercial Alpha first-item loop in strict business order using current account, candidate, terms, approval, fulfillment, and profit evidence.
---

# Commercial Alpha

Use this skill when the operator asks whether the business is ready, what to do
next, or how to run the first real item.

1. Call `xianyu_business_snapshot` and state the current counts.
2. Resolve historical WorkOrders before attaching a new seller account.
3. If no seller account exists, direct the operator to the console account
   panel in this order: open the dedicated login browser, finish login on the
   platform page, detect the current account, verify the displayed account ID,
   and enter the one-time `BIND ...` phrase. Never ask the operator to paste a
   Cookie into Pi and never treat an opened browser as a completed import.
4. Require that exact owned or authorized account to be enabled with Prompt,
   listing and order sync, fresh runtime evidence, and no verification blocker.
5. Call `xianyu_candidate_activation_packet` for exactly one domestic Xianyu
   candidate. Require every supplier identity, exact SKU, quote, stock,
   fulfillment, image-rights, current market baseline, unit-economics and
   evidence-revision gate to pass. For an evidence blocker, call
   `xianyu_candidate_evidence` and report whether its archive is missing,
   superseded or integrity-failed. File upload remains an operator console
   action. Report the first blocker exactly.
6. Read the exact account-and-category commercial terms. Current payment,
   buyer-protection/return, guarantee, percentage fee, fixed fee, and candidate
   version must match.
7. Require manual candidate approval.
8. Call `xianyu_commercial_launch_preview`. Report the first failed gate and its
   next action. Do not summarize a blocked preview as ready.
9. Only after 20/20 gates pass may the operator start one batch in the console.
   Starting a batch does not publish a listing.
10. Prepare one listing draft, then leave approval and execution to the operator.
11. Do not expand until a real inquiry, paid order, procurement, dual tracking,
    delivery and actual-profit reconciliation are observed.

Detailed contract: `docs/ops/commercial-alpha-runbook.md`.
