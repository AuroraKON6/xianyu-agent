# OPC MVP Validation

> This file defines how to run minimal listing experiments before committing to a category or product.
> It is a reference document for agents and dashboards, not runtime code.

---

## What "MVP" Means in Xianyu Context

MVP is NOT building a product. It is running the smallest possible listing test to validate whether a product category can generate inquiry and conversion on Xianyu.

The goal is to answer: **Can this product get inquiries from real buyers with reasonable effort?**

### Psychological Baseline (from operational experience)

Before running MVP tests, internalize these truths:

- **Not every inquiry converts.** Buyers browse like they walk through a mall — entering a store doesn't mean buying. Handle inquiries well, but don't despair when they don't convert.
- **Not one or two listings will produce orders.** Xianyu is like ground-sales (地推). Volume + iteration = results. Start by listing many products, then optimize what works.
- **High exposure ≠ high profit.** Electronics get instant views but thin margins. Commercial equipment gets fewer views but 200+ yuan per order. Focus on profit, not vanity metrics.
- **标品看价格，非标看详情.** Standard products compete on price. Non-standard products compete on specification detail, copywriting, images, and service. Sell non-standard.
- **The same product at different prices can all sell.** One operator had 3 links for the same product — the buyer purchased the most expensive one. Copywriting, service态度, and trust signals matter more than being cheapest.

---

## Validation Cycle

### Step 1: Select Candidate Product

Source: Pinduoduo, raw-material expansion, or category scanning.

Selection criteria (from product-selection-playbook):
- Cold-category potential (avoid crowded consumer electronics).
- High-ticket potential (one order = meaningful gross profit).
- Supplier advantage (real cost or logistics edge).
- Low compliance risk.

### Step 2: Generate Listing Draft

The listing agent produces a structured draft:
- Main title (keyword-rich, differentiated).
- Search keywords (5-10 relevant terms).
- Lead image requirement (scene-based, not copied).
- Specification table.
- Price ladder or specification ladder.
- Trust paragraph.
- Delivery and after-sales notes.
- Questions the operator must verify.

**Draft does NOT go live.** It enters WorkOrder + Approval.

### Step 3: Approve and Publish

Operator reviews the draft, verifies supplier pricing, confirms images are owned/authorized, and approves.

Approved listing enters ExecutionJobQueue → ActionExecutor → publish.

### Step 4: Monitor (7-14 days)

Track:
- Exposure (views).
- Clicks ("我想要" / inquiries).
- Buyer messages (quality and intent).
- Conversion (orders placed).

### Step 5: Decision

| Signal | Action |
| --- | --- |
| Good exposure, inquiries coming in | Keep testing. Optimize listing and reply. |
| Exposure but no inquiry | Fix listing: title, image, price, or specification clarity. |
| Inquiries but no conversion | Fix reply strategy. Check pricing. Check trust signals. |
| No exposure after 7 days | Kill. Try different title/keywords or different product. |
| Orders coming in, low after-sales | Scale: add more listing variants, negotiate better supplier pricing. |
| High after-sales or complaint rate | Pause. Investigate supplier quality or fulfillment process. |

---

## Success Criteria

A product passes MVP validation if:
- It generates at least 3 genuine buyer inquiries within 14 days.
- At least 1 inquiry converts to an order.
- After-sales risk is manageable (no pattern of quality complaints).
- Supplier can fulfill reliably.

A category passes MVP validation if:
- At least 2 out of 5 tested products generate inquiries.
- The category has room for 10+ listing variants.
- Reply templates can be standardized across products.

---

## Boundaries

- MVP listing drafts are always approval-gated.
- No auto-publishing.
- No copying third-party images or copy.
- No bypassing platform rules for exposure (e.g., keyword stuffing, misleading pricing).
