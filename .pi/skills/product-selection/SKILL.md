---
name: xianyu-product-selection
description: Evaluate and prioritize domestic Xianyu resale candidates using the project's seven-dimensional score, supplier evidence, demand signals, and one-item validation discipline.
---

# Xianyu Product Selection

Use `xianyu_candidates` before recommending a product, then call
`xianyu_candidate_activation_packet` for the exact item. If a quote, stock,
fulfillment, image-rights or market gate is blocked, call
`xianyu_candidate_evidence` to distinguish missing, superseded and
integrity-failed artifacts. Keep cross-border records outside this project.

Evaluate cold-category opportunity, ticket size, batch-order potential,
differentiation, supplier advantage, fulfillment risk, and compliance risk.
Scores are discovery evidence, not permission to publish. Prefer one item that
has a verifiable supplier, exact SKU and landed cost, authorized images,
observable competing listings, manageable delivery, and enough contribution
margin after current account/category fees and expected refund loss.

The activation packet must show a real supplier URL and alias, exact SKU,
current archived quote and stock artifacts, minimum order quantity, normal
unpriced shipping, dispatch/return/damage terms, authorized images, two current
comparable listings, and an approval fingerprint matching the present evidence
revision. Each archive reference must be active, candidate-bound and hash
verified. A score cannot compensate for a failed evidence gate.

The operating sequence is broad testing, evidence collection, one-item Alpha,
then vertical focus only after observed inquiries, paid orders and actual
profit. Do not recommend multi-account expansion from exposure alone.

Detailed playbook: `docs/ops/xianyu/product-selection-playbook.md`.
