---
name: xianyu-commercial-experiment
description: Design a low-volume Xianyu commercial test with one variable, a fixed observation rule, business metrics, guardrails, and an explicit continue, revise, pause, or stop decision. Use for listing, title, image, price-presentation, city, or reply experiments.
license: Project adaptation; see docs/PI_SKILL_PROVENANCE.md
metadata:
  reviewed: 2026-08-23
---

# Xianyu Commercial Experiment

Use this skill only after reading the current business state and exact candidate.

1. Call `xianyu_business_snapshot`; use `xianyu_candidates` for the exact item.
2. State one observation and one falsifiable hypothesis. Change exactly one
   controllable variable: title angle, lead image requirement, specification
   order, price presentation, location, trust paragraph, or reply approach.
3. Prefer a sequential control-then-variant test for low traffic. Do not create
   simultaneous duplicate listings unless current platform rules and the exact
   account terms permit it.
4. Predeclare the test window or exposure threshold before starting. If no
   reliable baseline exists, use the project's 7-14 day MVP window as a
   directional learning period, not a statistical-significance claim.
5. Select one primary metric nearest to revenue: qualified inquiry rate, paid
   order rate, or contribution per qualified inquiry. Record exposure as
   context, not success.
6. Set guardrails for refund/complaint rate, expected loss, fulfillment load,
   price-floor breach, verification events, and unsupported buyer promises.
7. Require the exact account, candidate, current commercial terms, manual
   candidate decision, and `xianyu_publish_preflight` before preparing a draft.
8. Call `xianyu_prepare_listing` only when the operator explicitly asks for the
   local draft. The experiment plan never approves or publishes it.
9. End with `CONTINUE`, `REVISE`, `PAUSE`, `STOP`, or `INCONCLUSIVE`. Low sample
   size must remain inconclusive even when one variant looks better.

## Experiment Brief

Return: hypothesis, control, one changed variable, variant, fixed conditions,
primary metric, secondary context metrics, guardrails, observation rule,
required evidence, owner, budget ceiling, stop condition, and decision rule.

## Provenance

Adapted for low-volume marketplace operations from the MIT-licensed
`ab-testing` skill by Corey Haines and this project's Commercial Alpha rules.
