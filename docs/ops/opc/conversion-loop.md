# OPC Conversion Loop

> This file maps the full Xianyu conversion path from discovery to repeat purchase.
> It is a reference document for agents and dashboards, not runtime code.

---

## The Xianyu Conversion Loop

```text
Discovery → Listing → Exposure → Inquiry → Reply → Order → Fulfillment → Review → Repeat
```

Each stage has a specific bottleneck to optimize.

---

## Stage 1: Discovery

**Goal**: Find products with real demand and supplier advantage.

**Methods**:
- Raw-material expansion (stainless steel, wood, plastic → find products made from these).
- High-ticket scan (products above 500 yuan).
- Batch-demand scan (fences, mats, restaurant supplies, factory supplies).
- Regional scan (match listing city with supplier warehouse city).
- Same-product multi-link scan (detect competitor strategies).

**Bottleneck**: Finding products that are not already saturated. Optimize by focusing on cold categories and non-standard products.

**Agent behavior**: Discovery agent produces ranked candidate list with evidence (demand signals, margin estimates, competition analysis), not just keyword matches.

---

## Stage 2: Listing

**Goal**: Create listings that generate clicks and inquiries.

**Key principles**:
- Non-standard products compete on detail, not price.
- Title must contain searchable keywords + differentiation.
- Lead image must be scene-based, high-quality, showing the product in use.
- Specification table must be clear (size, material, function, color, thickness).
- Price ladder anchors high but offers a low-entry option.
- Trust paragraph addresses buyer concerns proactively.

**Bottleneck**: Listings that get views but no clicks. Optimize by improving title keywords, lead image quality, and price positioning.

**Agent behavior**: Listing agent produces structured drafts with all required fields. Drafts enter WorkOrder → Approval before publishing.

---

## Stage 3: Exposure

**Goal**: Get the listing in front of the right buyers.

**Factors controlled by operator**:
- Listing city (match with demand city or supplier shipping origin).
- Keywords and tags.
- Listing freshness (regular new listings boost account activity).
- Account reputation (response rate, shipping speed, review score).

**Factors controlled by platform**:
- Algorithm ranking.
- Category competition.
- Seasonal demand.

**Bottleneck**: Low exposure despite good listing quality. Optimize by adjusting listing city, refreshing titles, or testing different keyword combinations.

---

## Stage 4: Inquiry

**Goal**: Convert exposure into buyer messages.

**Signals that listing is working**:
- "我想要" count increasing.
- Buyer messages arriving.
- Questions about specification, price, or delivery.

**Signals that listing needs work**:
- Views but no "我想要".
- Views and "我想要" but no messages.
- Messages that are just price-checking with no follow-up.

**Bottleneck**: Buyers view but don't engage. Optimize listing copy, add urgency signals, improve trust paragraph.

---

## Stage 5: Reply

**Goal**: Convert inquiries into orders through conversation.

**Reply strategy** (see conversion-reply-playbook):
- Identify buyer intent: price, specification, delivery, quality, comparison, after-sales.
- Clarify buyer's use case, not just the literal question.
- Recommend the most suitable specification.
- Protect margin while offering controlled concessions.
- Avoid external-contact guidance.
- Escalate risky replies into Approval.

**Bottleneck**: Inquiries that don't convert. Optimize reply speed, reply quality, and specification guidance.

**Agent behavior**: Customer-service agent generates reply drafts. Drafts with risk classification enter Approval before sending.

---

## Stage 6: Order

**Goal**: Fulfill orders correctly and efficiently.

**Pre-fulfillment checklist** (see fulfillment-after-sales-playbook):
- Confirm product specification (size, color, material, function, thickness, quantity).
- Confirm address and phone for large/fragile items.
- Confirm supplier shipping constraints.
- Capture after-sales risk notes.

**Bottleneck**: Fulfillment errors causing returns or disputes. Optimize by enforcing the pre-fulfillment checklist.

---

## Stage 7: Review and Repeat

**Goal**: Build buyer loyalty and generate repeat business.

**Actions**:
- Follow up after delivery to confirm satisfaction.
- Handle issues proactively (small problems → small compensation, avoid escalation).
- Request reviews.
- Save successful reply templates and listing patterns as reusable assets.

**Bottleneck**: One-time buyers only. Optimize by building a product range that encourages repeat purchase (same buyer needs multiple items for their business).

---

## Conversion Metrics

| Metric | Target | How to Measure |
| --- | --- | --- |
| Listings per week | 10-20 new listings | WorkOrder count |
| Inquiry rate | 5-10% of listings get inquiries | Buyer message count / active listings |
| Conversion rate | 20-30% of inquiries become orders | Orders / inquiries |
| Average order value | 500+ yuan | Revenue / order count |
| After-sales rate | < 5% | Disputes / orders |

---

## Growth Progression Model

### Phase 1: 杂货铺 Testing (Month 1-2)

- List many different products across categories.
- Goal: learn what gets inquiries and what doesn't.
- Optimizewhat works, kill what doesn't.
- Build initial reply templates and listing patterns.

### Phase 2: Vertical Focus (Month 3+)

- When a product keeps selling, expand to related products in the same category.
- Example: cold-storage work tables → all commercial kitchen equipment.
- Package the store as a specialist (e.g., "商用厨具厂家").
- This builds professional reputation and algorithm trust.

### Phase 3: Multi-Store Expansion

- Open additional Xianyu accounts for different verticals.
- Keep one miscellaneous account for testing.
- "把鸡蛋放在不同的篮子里" — spread risk across categories and accounts.

### Phase 4: Supplier Advantage Lock-In

- Once volume is proven, negotiate factory-direct pricing.
- Price advantage across the entire platform = sustained traffic.
- Some products can sell for years with the right supplier relationship.
- Eventually consider opening a Pinduoduo store as a secondary channel.

---

## Key Insight from Experience

> "自从我拿到价格优势后，我有几个产品从前年一直卖到现在，还是不断在出单。"

Price advantage compounds over time. The goal of the conversion loop is not just to sell one product — it's to build a repeatable system where supplier relationships, listing templates, and reply scripts create compounding returns.
