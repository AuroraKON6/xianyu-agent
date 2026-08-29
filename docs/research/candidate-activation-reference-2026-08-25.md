# Candidate Activation Reference Audit

Updated: 2026-08-25

## Decision

A high score, a supplier link, or an attractive estimated margin is not enough
to make a product commercially launchable. One exact domestic Xianyu candidate
must have a current, attributable evidence packet, and the operator's approval
must refer to the same evidence revision that will enter publication preflight.

The implemented order is:

`exact product -> supplier source -> quote/stock -> fulfillment -> image rights -> market baseline -> account-priced economics -> current manual decision`

Account onboarding and account/category terms remain separate gates. The
candidate packet can be prepared before login, but a real launch still requires
the selected account's current terms, runtime, sync, approval, and execution
evidence.

## Sources Reviewed

| Source | Current evidence used | Project decision |
|---|---|---|
| [闲鱼社区卖家服务协议](https://terms.alicdn.com/legal-agreement/terms/platform_service/20220929192826912/20220929192826912.html) | The current agreement requires seller account responsibility, truthful and current listing information/evidence, seller rights over listing content, and descriptions consistent with the actual goods. | Require an exact product identity, traceable supplier, current evidence references, usable images with recorded rights, and a version-bound manual decision. |
| [闲鱼社区买家保障服务协议](https://terms.alicdn.com/legal-agreement/terms/platform_service/20220929192101114/20220929192101114.html) | Seller obligations include accurate description and active handling of returns, refunds, and buyer-protection responsibilities. | Require supplier dispatch, return, wrong-item, and damage-liability evidence before the first Alpha item. Account-level buyer-protection and return terms remain separate current-page gates. |
| [闲鱼软件许可使用协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230509172204596/20230509172204596.html) | Actual service fees and scope depend on current platform rules/pages; off-platform or category-mismatch fee avoidance is not a reliable commercial model. | Keep candidate prices provisional until the selected account/category terms snapshot reprices economics. Any terms change invalidates the old candidate decision. |
| [harringtonwalker/xianyu-automation](https://github.com/harringtonwalker/xianyu-automation) | The Apache-2.0 reference separates source type and external SKU, records rights confirmation and facts, rechecks page evidence within 24 hours, supersedes stale revisions, and requires approval plus strict execution receipts. It does not claim that its real publish path is complete. | Adopted the evidence separation, freshness, superseding-revision, and strict-receipt design ideas. No upstream publishing code or account automation was copied. |

Platform agreements and page state can change. This record explains the
architecture; execution-day account, category, fee, guarantee, and return pages
remain authoritative.

## Implemented Twelve-Gate Packet

1. `channel_scope`: `sales_platform=xianyu` and `market_scope=domestic`.
2. `product_identity`: non-placeholder title, platform category, exact supplier
   SKU, and at least two meaningful specifications.
3. `supplier_source`: `pdd` or `1688`, a non-placeholder external product URL,
   and a recognizable internal supplier alias.
4. `supplier_verified`: explicit verification plus a substantive note naming
   what was checked and the conclusion.
5. `supplier_quote_evidence`: positive purchase price, recorded shipping, MOQ,
   evidence reference, and a quote no older than 24 hours.
6. `supplier_stock_evidence`: current in-stock/limited state, confirmed positive
   quantity, evidence reference, and a stock check no older than 24 hours.
7. `supplier_fulfillment_evidence`: normal shipment without price material,
   dispatch within seven days, return policy, wrong-item/damage handling,
   substantive note, and evidence reference.
8. `image_assets`: at least one real local or external image reference; example
   domains and blank values are rejected.
9. `image_rights`: owned, supplier-authorized, or licensed use, with a
   substantive conclusion and evidence reference.
10. `market_baseline`: two distinct comparable listing URLs, displayed-price
    range, observation no older than 72 hours, demand-signal note, and evidence
    reference. Displayed price is not treated as a completed sale price.
11. `unit_economics`: target sale price, finite gross profit, and finite gross
    margin above configured floors after current known costs.
12. `manual_decision_current`: `approve` and `approved` must match the SHA-256
    fingerprint of every material fact in the current evidence revision.

The packet validates structure, freshness, and revision consistency. It does
not prove that a screenshot or supplier statement is truthful; that remains an
operator evidence review.

## Revision And Operating Rules

- Any material fact change increments `evidence_revision`, recalculates
  economics/scoring, clears the old manual decision, and records why it became
  stale.
- Saving identical values preserves the current decision and revision.
- Recording a new account/category fee version reprices the candidate and
  invalidates approval before the next review and decision.
- Once a listing draft exists, material facts cannot be rewritten in place;
  create a new candidate revision instead of silently changing an approved
  draft's source facts.
- `GET /resale/candidates/{id}/activation-packet` is read-only. The console
  saves evidence through `PATCH` and shows the first failed gate.
- Publication preflight embeds the packet revision/fingerprint and requires
  `candidate_activation_ready`; the older individual checks remain as defense
  in depth.
- Pi receives a read-only activation-packet tool. It cannot approve, publish,
  execute, send, delete, refund, or alter the packet.

## Rejected Shortcuts

- Do not treat a placeholder title, example URL, generic “已确认”, or a score as
  product evidence.
- Do not infer stock, sales, conversion, quality, or final SKU price from a
  search result.
- Do not approve before account/category repricing and then carry that approval
  forward.
- Do not edit supplier facts underneath an existing listing draft.
- Do not call local tests, a browser click, or a queued approval a real publish.

## Current Live Result

The current candidate `rc_e54c573384a7` is the placeholder `T`. Its read-only
packet reports `1/12`; the first blocker is `product_identity`. It has not been
represented as a commercially qualified candidate. The global business flow is
also blocked at `account_configured` because no seller account is currently
bound.
