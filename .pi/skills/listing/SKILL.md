---
name: xianyu-listing
description: Prepare evidence-backed Xianyu listing drafts with structured fields, price ladders, current return terms, image rights, and approval-gated publication.
---

# Xianyu Listing

Before preparing a listing, call `xianyu_publish_preflight` for the exact seller
account and candidate. If any gate fails, report the first failure and stop.

A complete draft contains title, search keywords, lead-image requirement,
specification table, price ladder, trust paragraph, delivery/after-sales notes,
and operator verification questions. It must use the current terms snapshot for
fees and return boundaries and must not invent stock, performance, authorization
or delivery promises.

Call `xianyu_prepare_listing` only after the user explicitly requests a draft
and preflight is ready. The result is a local WorkOrder requiring human
approval, not a platform publication.

Detailed playbook: `docs/ops/xianyu/listing-playbook.md`.
