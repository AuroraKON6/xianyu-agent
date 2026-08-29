# Pi Skill Provenance

Updated: 2026-08-30

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
