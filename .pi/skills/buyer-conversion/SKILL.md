---
name: xianyu-buyer-conversion
description: Draft Xianyu buyer replies based on intent, product evidence, current price and delivery facts while keeping sending under operator approval.
---

# Buyer Conversion

Classify the buyer's concrete intent: price, specification, delivery, quality,
comparison, after-sales or complaint. Answer the requested fact first, then ask
one useful closing question. Never answer “the details already say it”.

Use only current product, stock, price, delivery and after-sales evidence. Do
not invent discounts, gifts, warranties or off-platform contact methods. Call
`xianyu_prepare_customer_reply` only when the operator explicitly asks for a
reply draft. Treat the returned text as unsent until platform-send evidence is
recorded through the approved execution path.

Detailed playbook: `docs/ops/xianyu/conversion-reply-playbook.md`.
