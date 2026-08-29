---
name: xianyu-competitor-evidence
description: Collect and compare current Xianyu listings for one exact product using observable evidence. Use for competitor scans, same-product multi-link research, displayed-price ranges, title angles, specification coverage, trust signals, and evidence-backed listing hypotheses.
license: Project adaptation; see docs/PI_SKILL_PROVENANCE.md
metadata:
  reviewed: 2026-08-23
---

# Xianyu Competitor Evidence

Use this skill for one domestic Xianyu product phrase at a time.

1. Call `xianyu_market_search` with the narrowest exact phrase that matches the
   candidate. Start with one page; use a second page only when the first result
   set is too small or obviously mixed.
2. Keep only genuinely comparable listings. Separate exact products, adjacent
   substitutes, accessories, damaged goods, used goods, and lead-price variants.
3. Treat product ID, title text, displayed price, location, seller name, image
   URL, and observation time as observed fields. Sales, conversion, stock,
   quality, final SKU price, ranking causes, and seller reliability remain
   unknown unless separate evidence proves them.
4. Compare controllable listing signals: title angle, buyer/use case, disclosed
   material and dimensions, price presentation, delivery wording, after-sales
   wording, trust evidence, and image subject. Do not copy competitor text or
   images.
5. Produce no more than three hypotheses. Each hypothesis must identify the
   observation, the proposed change, the expected buyer response, the metric,
   and the evidence that would disconfirm it.
6. A search result is research evidence, not permission to create or publish a
   listing. Use `xianyu_publish_preflight` before any draft work.

## Output

Return scope and observation time, comparable-result count, displayed-price
range with the lead-price caveat, an evidence table, unknowns, and up to three
single-variable tests. Clearly label the result `directional` when real inquiry
or order data is absent.

## Provenance

Adapted for this project from evidence-separation patterns in Nexscope's CC0
ecommerce SEO/GEO skills and the project's same-product multi-link playbook.
