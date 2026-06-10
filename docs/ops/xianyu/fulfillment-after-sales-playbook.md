# Xianyu Fulfillment and After-Sales Playbook

> This file defines pre-fulfillment checklists and after-sales handling procedures.
> It guides the order agent's workflow and the operator's decision-making.

---

## Pre-Fulfillment Checklist

Before confirming any order for fulfillment, the operator or order agent must verify ALL of the following:

### Specification Confirmation

- [ ] Product specification matches buyer's order (size, material, color, function, thickness, quantity).
- [ ] If buyer chose a specific variant, confirm which variant is being ordered.
- [ ] If buyer mentioned a use case, confirm the chosen specification is appropriate.

### Address and Contact

- [ ] Shipping address is complete and valid.
- [ ] Phone number is correct and reachable.
- [ ] For large/fragile items: confirm the delivery address can accept large shipments (freight delivery, not standard courier).

### Supplier Confirmation

- [ ] Supplier has the ordered specification in stock.
- [ ] Supplier shipping timeline is confirmed.
- [ ] Supplier shipping method is confirmed (courier vs. logistics).
- [ ] Special instructions sent to supplier:
  - No Pinduoduo branding on packaging.
  - No price lists or好评返现 cards inside the package.
  - No SMS notifications to the buyer.
  - "无痕发货" (neutral packaging) if possible.

**Supplier instruction script** (send when placing order):
> 您好，已下单，麻烦快递面单不要放拼多多店铺信息，快递里面不要放价格表和好评返现小卡片，不要给我发短信打扰，无痕发货谢谢。

### Risk Notes

- [ ] Large/fragile item: note shipping damage risk. Consider adding insurance.
- [ ] High-value item: confirm supplier's return policy before shipping.
- [ ] Buyer has special requests: document them in the order notes.

---

## Fulfillment Workflow

```text
Order received
  → Run pre-fulfillment checklist
  → If all checks pass: place order with supplier
  → If any check fails: flag for operator review
  → Supplier ships
  → Update tracking information
  → Monitor delivery
  → Follow up with buyer after delivery
```

**Safety boundary**: Fulfillment requires WorkOrder + Approval + ExecutionJobQueue. No auto-fulfillment.

---

## After-Sales Handling

### Category 1: Shipping Damage

**Signals**: Buyer sends photos of damaged item, crushed packaging, missing parts.

**Response**:
1. Apologize sincerely.
2. Collect evidence (buyer's photos).
3. Forward evidence to supplier.
4. Negotiate resolution with supplier (partial refund, replacement parts, or full replacement).
5. Offer buyer a small goodwill gesture (红包, partial refund) while waiting for supplier resolution.
6. Document the incident for supplier quality tracking.

**Key principle**: The buyer's perception of your service matters more than the monetary amount. A quick, sincere response often prevents escalation.

**Profit opportunity**: If supplier gives you 20 yuan compensation, you can offer 10 yuan to the buyer. "小问题我们可以发点小红包解决，一定要诚恳的表示抱歉，很多买家其实认得是一个态度." You keep the difference — after-sales can actually be profitable when handled well.

### Category 2: Quality Issue

**Signals**: Product doesn't work, material doesn't match description, manufacturing defect.

**Response**:
1. Apologize and collect detailed evidence.
2. Forward to supplier for assessment.
3. If supplier acknowledges defect: arrange return/replacement. Supplier should cover return shipping.
4. If supplier disputes: collect more evidence. Escalate to Pinduoduo platform if needed.
5. Offer buyer resolution while pursuing supplier compensation.

### Category 3: Buyer Mistake (Wrong Spec Ordered)

**Signals**: Buyer ordered wrong size, wrong color, or wrong material.

**Response**:
1. Confirm what they ordered vs. what they needed.
2. For small items: buyer pays return shipping. You can offer to resend at a discounted price.
3. For large items: suggest buyer resell on Xianyu to avoid high return shipping cost. "运费都是几大百还要承担发出去的运费，一般我会建议买家挂在闲鱼上面处理了，能省一点是一点."
4. Document for future listing optimization (is the specification table clear enough?).

### Category 4: Non-Quality Return (Changed Mind)

**Signals**: Buyer doesn't want it anymore, found a better deal, or doesn't like it.

**Response**:
1. Xianyu has no运费险 (shipping insurance). Buyer pays return shipping.
2. If sourcing from Pinduoduo: many items have运费险 on the sourcing side, so you may recover the return shipping cost or even profit slightly. "退货我们还可以小赚个10块钱."
3. For large items: return shipping is expensive. Suggest buyer resell on Xianyu.

### Category 5: Platform Dispute

**Signals**: Buyer opens a dispute, threatens negative review, or reports to platform.

**Response**:
1. Stay calm and professional.
2. Collect all evidence (order details, communication history, product photos).
3. Respond within platform's required timeframe.
4. If the dispute is valid: offer fair resolution quickly.
5. If the dispute is invalid: present evidence clearly and let the platform decide.
6. Escalate to Approval for all dispute-related responses.

---

## Refund Decision Framework

| Situation | Refund? | Notes |
| --- | --- | --- |
| Item not shipped yet, buyer cancels | Full refund | No loss. |
| Item shipped, buyer cancels | Partial refund (minus shipping) | Or let buyer receive and return. |
| Item received, quality issue | Full refund or replacement | Supplier should compensate you. |
| Item received, buyer mistake | Buyer pays return shipping | Refund item cost only. |
| Item received, buyer changed mind | Buyer pays return shipping | Refund item cost only. |
| Dispute filed | Case by case | Approval required for all dispute resolutions. |

**Safety boundary**: All refunds require WorkOrder + Approval. No auto-refund.

---

## Supplier Quality Tracking

Maintain a running log of after-sales incidents per supplier:

| Supplier | Product | Issue | Resolution | Date | Action |
| --- | --- | --- | --- | --- | --- |
| [Name] | [Product] | [Issue type] | [What happened] | [Date] | Keep/Drop/Watch |

**Decision rules**:
- 3+ quality incidents in 30 days → Drop supplier.
- Consistent shipping damage → Review packaging requirements with supplier.
- Consistent delays → Find backup supplier.
- Good track record → Negotiate better terms.

**Supplier negotiation tips** (from operational experience):
- Start with lower margin to prove volume, then negotiate better terms.
- "有好产品就差销量" — small factories need sales volume. Offer to drive it.
- After selling a few orders successfully, approach for better pricing: "卖出去几单后再谈，只要他是一手货源，肯定愿意让利的."
- When your price is competitive across the entire platform, traffic becomes sustainable. "单个产品不会一直爆" but with price advantage, some products sell for years.

---

## Agent Integration

The order agent should:
1. Run the pre-fulfillment checklist before creating a fulfillment WorkOrder.
2. Flag any checklist failures for operator review.
3. Track after-sales incidents by supplier and product.
4. Generate after-sales response drafts based on the scenario templates.
5. All after-sales responses involving refunds, returns, or disputes require Approval.
6. Save successful resolution patterns as reusable templates.
