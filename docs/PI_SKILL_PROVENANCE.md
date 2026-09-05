# Pi Skill Provenance

Updated: 2026-08-31

## Local skill inventory

All eight production-loaded skills are project-local Markdown instructions. The
hashes below identify the exact reviewed files in this baseline; a later content
change requires a new review and hash update.

| Local skill | Origin | SHA-256 of `SKILL.md` |
|---|---|---|
| `buyer-conversion` | Project-original adaptation of the Xianyu conversion playbook | `efbc1581c072d84c869d07c316bc42ca5e8382e3774f42248dccf06032f0e43d` |
| `commercial-alpha` | Project-original Commercial Alpha runbook | `a4bd0d2e4808fc723160fd054d8b30195eaa5afdafed9a08403f1b7fae302fd4` |
| `commercial-experiment` | Adapted from the MIT source listed below | `dc7fdf8061d619678076ad31255371458725c1bf0342cab694b65f0c35cf16f5` |
| `competitor-evidence` | Adapted from the CC0 source listed below | `73af05c1944eebaf28ba16c49d800f35f13c7bcf59f555ffd3ab4fb543e62746` |
| `fulfillment` | Project-original adaptation of the fulfillment playbook | `d12ca02b9bf7852194f256d595228f391332e095f87cd49552eb60d55bae9208` |
| `listing` | Project-original adaptation of the listing playbook | `3fa4880492afcffa1cc83a6a7aae6379920c66496b255e1d6149bac300b3dcce` |
| `product-selection` | Project-original adaptation of the selection playbook | `e9c4036c72cbc8ca1c96480ce61bf07275f83e8be98890b3778c4b5db107f2b0` |
| `weekly-review` | Project-original adaptation of the OPC review playbook | `6ce150d8cc13ab4eff7b8655caff68b97303e1a13e6fbf3509b370eec4f440dc` |

The Pi process no longer receives the built-in arbitrary-path `read` tool.
Skills provide operating instructions; current business facts must come from
the 12 typed Xianyu tools.

## Integrated adaptations

| Local skill | Upstream source | Revision reviewed | License | What was retained |
|---|---|---|---|---|
| `xianyu-competitor-evidence` | `nexscope-ai/ecommerce-seo-geo-skills` | `71afa3302b947d9af327f514497a1ecbfabae617` | CC0-1.0 | Observable-vs-inferred evidence separation, controllable listing signals, unsupported-metric rules |
| `xianyu-commercial-experiment` | `coreyhaines31/marketingskills`, `ab-testing` | `3df87f97621e18fbed7f6aa684edba54f49779a7` | MIT | One-variable hypotheses, primary/guardrail metrics, predeclared observation and inconclusive outcomes |

The local skills were rewritten for low-volume Xianyu operations and the
project's Pi tool contract. No upstream scripts, API clients, advertising
handoffs, secrets, or external-action tools were copied.

## Reviewed but not installed

- `nexscope-ai/nexscope-ecommerce-skills` at
  `b039e0c0972825ccbc19021f27dcb85033de026a`: no repository license was found;
  most skills require `NEXSCOPE_API_KEY` and execute API helper scripts.
- `badlogic/pi-skills`: useful Pi-native browser/search skills, but they require
  general shell access and extra credentials. This project instead exposes one
  bounded local read-only search tool.
- `mfwarren/entrepreneur-claude-skills`: MIT and useful for founder decisions,
  but generic decision, pricing, and SOP skills overlap current project rules
  and were not copied.

## Pi package review

The 2026-08-30 package review is recorded in
`docs/research/github-and-pi-ecosystem-review-2026-08-30.md`.

| Package | Version snapshot | License | Decision |
|---|---:|---|---|
| `pi-web-access` | `0.27.0` | MIT | Temporary, isolated read-only research trial only; not loaded by the business RPC |
| `pi-subagents` | `0.59.0` | MIT | Not installed; duplicates the current specialist and state ownership model |
| `@harms-haus/pi-lens` | `2.1.0` | MIT | Optional development-only trial after the Git baseline is stable |
| `@harms-haus/pi-tasks` | `0.1.0` | MIT | Not installed; duplicates WorkOrder, Approval, ExecutionJob, and project phases |
| `@harms-haus/pi-processes` | `0.1.0` | MIT | Not installed; adds process-spawn behavior and is unnecessary for the business runtime |
| `@vtstech/pi-diag` | `1.3.3` | MIT | Temporary developer diagnostic only; current Windows/DeepSeek RPC already has health endpoints |
| `@vtstech/pi-long-term-memory` | `1.3.5` | MIT | Not installed; business facts must remain account-, chat-, item-, and source-scoped |

No third-party package was added to `.pi/settings.json`. Pi's official package
documentation states that packages execute with full system access, so any
future trial must pin the exact version, run outside the production RPC, and
prove that it leaves no business-data changes or background processes.

## MIT notice

Copyright (c) 2025 Corey Haines

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to inclusion of this copyright and permission notice.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
