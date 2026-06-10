# Xianyu Product Selection Playbook

> This file defines how to evaluate and score product candidates for Xianyu listing tests.
> It guides the discovery agent and the operator's selection decisions.
> Source: operational experience and article methodology.

---

## Selection Principles

1. **Non-standard beats standard.** Products where copywriting, specification clarity, and service influence conversion are better than commodity items competing purely on price.
2. **High-ticket beats low-ticket.** One order at 500+ yuan profit beats 100 orders at 3 yuan profit. Less effort, more margin, fewer after-sales headaches.
3. **Cold category beats hot category.** Avoid crowded consumer electronics. Commercial equipment, industrial supplies, and niche hobby products have less competition.
4. **Supplier advantage is real advantage.** If you can't get a better price than the buyer can find themselves, the product is not defensible.
5. **万物皆可卖.** Beyond prohibited items, everything can be sold. The key is matching the right product with the right demand at the right price. Don't limit your thinking to everyday consumer goods — from化粪池 (septic tanks) to母猪产床 (farrowing crates), if there's demand, there's a market.
6. **标品看价格，非标看详情.** Standard products (branded electronics, known appliances) compete on price. Non-spec products (commercial equipment, custom items) compete on detail — title, images, specification clarity, and service. Focus on non-standard.

---

## Scoring Model

Each candidate product is scored on 7 dimensions (1-5 each):

| Dimension | Question | Weight |
| --- | --- | --- |
| Cold-category potential | Does this avoid crowded consumer-electronics competition? | High |
| High-ticket potential | Can one order produce 100+ yuan gross profit? | High |
| Batch-order potential | Can buyers naturally buy multiple units? | Medium |
| Non-standard differentiation | Can copywriting, specs, service, or scene images influence conversion? | Medium |
| Supplier advantage | Is there real cost, logistics, or service advantage vs. retail? | High |
| Fulfillment risk | Size, fragility, warranty, return freight, complaint risk (lower = better) | Medium |
| Compliance risk | Prohibited goods, fake goods, rights issues, platform-sensitive claims (lower = better) | High |

**Scoring guide**: 1 = very poor, 3 = acceptable, 5 = excellent.

**Fulfillment risk and compliance risk are inverted**: 1 = very high risk, 5 = very low risk.

### Decision Threshold

| Total Score | Action |
| --- | --- |
| 28-35 | Strong candidate. Prioritize for listing test. |
| 21-27 | Worth testing. Monitor closely. |
| 14-20 | Weak candidate. Only test if nothing better available. |
| Below 14 | Reject. |

---

## Selection Methods

### Method 1: Raw-Material Expansion

Start with a raw material (stainless steel, wood, plastic, glass, metal, stone). Search Pinduoduo for products made from that material. Cross-reference with Xianyu to check demand ("我想要" counts).

**Why it works**: Bypasses Pinduoduo's recommendation algorithm, which traps you in echo chambers. Surfaces products you'd never think of.

**Example**: Input "stainless steel" → discover commercial sinks, restaurant shelving, industrial containers, laboratory equipment.

**How to execute**: Open Pinduoduo, type "不锈钢" in the search box. Browse the results — every product shown is a potential candidate. Then search the same product name on Xianyu to check if there's demand (look at "我想要" counts). This method works for any raw material: 塑料 (plastic), 木头 (wood), 玻璃 (glass), 金属 (metal).

**Real examples from operational history**:
- 养猪的母猪产床 (farrowing crates)
- 农村用的化粪池 (septic tanks)
- 工地用的液压平台 (hydraulic platforms)
- 服装厂用的拉布机 (fabric spreading machines)
- 餐饮用的串串柜 (skewer cabinets)
- 电子厂用的静电工作台 (anti-static workbenches)

### Method 2: High-Ticket Scan

Search Xianyu for products above 500 yuan in commercial/industrial categories. Filter by "我想要" count to find demand signals.

**Why it works**: High-ticket products have higher margin per order, making the operation sustainable with fewer transactions.

### Method 3: Batch-Demand Scan

Look for products that businesses buy in bulk: fences, mats, storage containers, restaurant supplies, factory equipment, office furniture sets.

**Why it works**: One buyer = multiple units = one negotiation = large revenue.

### Method 4: Regional Scan

Match listing city with likely demand cities. If supplier ships from Hebei, list in Beijing/Tianjin/Shijiazhuang. If you're in Chengdu, list in Chengdu for local餐饮 demand.

**Why it works**: Local buyers trust nearby sellers. Faster shipping for urgent needs. Platform may boost local relevance.

### Method 5: Same-Product Multi-Link Scan

Search for the same product type on Xianyu. Analyze how competitors differentiate: different titles, images, prices, cities. Identify gaps.

**Why it works**: Reveals what's working and where differentiation opportunities exist.

---

## Candidate Categories (Validated by Experience)

From operational history and article methodology:

| Category | Why | Risk Notes |
| --- | --- | --- |
| Commercial kitchen equipment | High-ticket, high demand, constant new entrants. Proven. | Large items, shipping complexity. |
| Stainless steel products | Raw-material method applies. Wide product range. | Weight, shipping cost. |
| Wood / office / factory equipment | Batch-order potential. Stable demand. | Size, delivery logistics. |
| Farming / breeding equipment | Niche, low competition. | Seasonal, regional. |
| Large-format household supplies | Everyday demand, high-ticket variants. | Returns, quality variance. |
| Hobby / niche equipment | Price non-transparent, high margin. | Smaller market, longer sales cycle. |

### Detailed Category Notes

**Commercial Kitchen Equipment** (proven, primary vertical):
- 平冷操作台 (cold-storage work tables): Every restaurant needs one. High failure rate = constant new demand. One operator sold 4-5 units per account, 200+ yuan profit per unit.
- 饮料柜 (beverage cabinets): Single-door and dual-door variants. Price ladder works well.
- Other sub-categories: 商用灶具 (commercial stoves), 排烟设备 (exhaust hoods), 食品加工台 (food prep stations), 制冷设备 (refrigeration units).

**Hobby / Niche Equipment** (high margin potential):
- 古琴 (guqin): Pinduoduo sourcing 180 yuan → Xianyu selling 360 yuan. Nearly 100% margin. Sells well because buyers don't know the "real" price.
- 乒乓球桌 (table tennis tables): Sports equipment, price non-transparent.
- Key insight: "这类的产品很多，相对冷门一些，会存在一些价格偏差" — niche products have price opacity that benefits the seller.

**Raw-Material Categories** (discovery method):
- Anything made from stainless steel, wood, plastic, glass, metal.
- The method surfaces products you'd never think of by escaping Pinduoduo's recommendation bubble.

---

## Anti-Patterns (What NOT to Select)

- **Consumer electronics**: Crowded, price-compressed, high return rate, platform scrutiny.
- **Clothing / fashion**: Size issues, high return rate, trend-dependent.
- **Cosmetics / food**: Compliance risk, expiration, platform restrictions.
- **Virtual goods / digital products**: High fraud risk, platform crackdowns.
- **Copycat / counterfeit items**: Legal risk, account ban risk.

---

## Agent Integration

The discovery agent should:
1. Accept a selection method as input (raw-material, high-ticket, batch-demand, regional, multi-link).
2. Produce a ranked candidate list with scoring evidence.
3. Include demand signals from Xianyu (inquiry counts, competitor analysis).
4. Include margin estimates based on Pinduoduo sourcing price vs. Xianyu market price.
5. Flag compliance and fulfillment risks explicitly.

Output format: structured candidate record that can feed into the listing agent's draft generation.
