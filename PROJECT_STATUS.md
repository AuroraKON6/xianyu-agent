# Xianyu Pi Commercial Alpha Status

Updated: 2026-09-18

## 2026-09-18 第二次复验修复轮（4 项 P1：深拷贝/并发采购/并发冲正/重复取消）

输入：操作者第二次复验"仍不通过"。证据：[docs/acceptance/WP_RELIABILITY_FIXES_R2_2026-09-18.md](docs/acceptance/WP_RELIABILITY_FIXES_R2_2026-09-18.md)。全量回归 **1288/1288**。

- 按指定流程执行：先写稳定复现的失败测试（红灯留证，含冲正 3 连稳定红）→ 再修实现 → 3 连跑全绿。
- 深快照回滚（既有记录+嵌套字段+读路径深拷贝）；采购创建/取消全程 mutation 锁 + 预留绑定具体采购单（`proc:{ID}`）+ 取消幂等；冲正校验+写入原子化。
- 未做（按操作者排序）：跨文件失败、重启恢复、完整业务流程组合演练；未进入 Pi 扩展阶段。

---

## 2026-09-18 可靠性修复轮（验收问题 1-8 全部修复）

输入：操作者验收"暂不通过"报告。证据：[docs/acceptance/WP_RELIABILITY_FIXES_2026-09-18.md](docs/acceptance/WP_RELIABILITY_FIXES_2026-09-18.md)。全量回归 **1283/1283**（+14 条按复现路径编写的回归）。

- P1-1 存储失败：锁必然释放 + 内存回滚快照（幽灵写入不可落盘）。
- P1-2 采购：同销售订单幂等拒绝 + 原子库存预留（先锁后存、失败释放、取消归还）。
- P1-3 撤销批次隔离：仅 committed 批次快照可驱动采购。
- P1-4 资格联动：评估取最新匹配（纠正即生效）；复核失败自动 ready_for_test→paused。
- P1-5 预算按实际结算额累计（100 预留/140 实付 → 占 140）。
- P1-6 库存读取/锁单时重校验时效（过期=0 可售+拒绝新锁）。
- P1-7 冲正累计上限+同币种（重复冲正无法制造正收益）。
- P2-8 润色只允许草稿正文已有金额（底价永不对外）。
- 未加新功能；WP08 直连模型的范围性说明如实记录；Pi 链路整合按操作者排序等可靠性验收通过后进行。

---

## 2026-09-18 第五轮增量（门槛达标轮：WP05 100对 / WP11 200条+润色 / WP09 视觉验收）

证据：[docs/acceptance/WP_ROUND5_2026-09-18.md](docs/acceptance/WP_ROUND5_2026-09-18.md)。全量回归 **1269/1269**。

- WP11 润色层 PASS：真实模型（deepseek-flash）只改措辞，金额/承诺/断言三类护栏 + 确定性兜底；mock 6/6 + 真实运行证据。
- WP05 PASS：标注集 102 对（tune 72/holdout 30 分离），新增结构计数与钢材等级约束；**holdout 零错误**，EXACT 精确 6/6。
- WP11 场景集 PASS：215 条，意图/出价 100%，标记归属位置感知修复两类真实误判。
- WP09 视觉验收 PASS：Playwright 1440/390 双档零溢出零报错，UI 实操 导入→预览→确认→刷新持久。
- 真实经营状态未变：账号 0、真实供应商报价 0、平台动作 0、真实利润 0。外部欠账：供应商报价文件、账号登录、PDD/1688 申请决定、每日备份调度决定。

---

## 2026-09-18 第四轮增量（WP08 解锁 + WP14 最小版）

证据：[docs/acceptance/WP_ROUND4_2026-09-18.md](docs/acceptance/WP_ROUND4_2026-09-18.md)。全量回归 **1259/1259**（两次通过）。

- WP08 BLOCKED→PASS（最小版）：操作者提供新 DeepSeek key；平台无 v4.1 后缀 ID，flash 档即 `deepseek-flash` 已设为默认；修复 llm.py 推理烧 token 缺陷（thinking 显式 disabled）；真实无写入任务成功落库（job_9ab5eaae56f74263 / dec_727bc140fb7d4611），面板可读。pi-subagents 插件试验仍待批准。
- WP14 PASS（最小版）：data/commerce 备份/校验/恢复演练（CLI+runbook+测试）；首份真实备份已建并校验通过；GET /commerce/ops-summary 运维摘要；RPO/RTO 目标未实现未宣称；每日调度待操作者决定。
- 修复：repository 同秒时间戳平局按追加序裁决。
- 已知非复现 flake：preflight 敏感文本误报一次，记录待查。
- 真实经营状态仍未变：账号 0、真实供应商报价 0、平台动作 0、真实利润 0。剩余外部欠账：真实供应商报价文件、闲鱼账号登录、PDD/1688 申请决定。

---

## 2026-09-18 第三轮增量（WP11/WP12/WP13）

证据：[docs/acceptance/WP_ROUND3_2026-09-18.md](docs/acceptance/WP_ROUND3_2026-09-18.md)。本轮终点全量回归 **1254/1254**。

- WP11 PASS（最小版）：只产草稿的客服回复服务——位置感知金额护栏（否定/运费/引用/多件/旧报价/型号）、52 条场景集（护栏类目 100%，未达 200 条门槛不宣称目标）；无发送路径；不写卖家承诺。
- WP12 PASS（最小版）：可售库存分配（未知/过期=0、锁单/释放、宁少卖不超卖）；上架守卫（捏造断言拦截、供应商价改后旧草稿失效）；**physical_sourcing 四套捏造话术已删除**，买家回复仅由已验证事实生成。
- WP13 PASS（最小版，模拟层）：采购重核验→人工批准→供应商订单号→回执；append-only 账本（冲正更正、部分退款、签收≠回款）；六场景手算复核通过。真实订单闭环待账号。
- 真实经营状态未变：账号 0、真实报价 0、平台动作 0、真实利润 0。外部欠账：模型 key（双 401）、供应商报价文件、账号登录、PDD/1688 申请决定。

---

## 2026-09-18 第二轮增量（WP04/WP07/WP08/WP10）

证据：[docs/acceptance/WP_ROUND2_2026-09-18.md](docs/acceptance/WP_ROUND2_2026-09-18.md)。本轮终点全量回归 **1204/1204**。

- WP04 PASS（最小版）：采集框架（重试/退避/Retry-After/熔断/配额/统一错误码，测试零真实网络）+ 文件通道适配器 + 新鲜度分类（报价24h/市场72h）+ provider probe（UNVERIFIED 拒绝探测）。
- WP07 PASS（最小版）：持久 AgentJob 存储——原子认领、租约/fencing、按账号写串行、崩溃恢复（写任务转 unknown 不假定副作用）；决策卡与任务关联。
- WP08 **BLOCKED**：真实模型运行脚本就绪（失败如实落库），但 MiMo 与 DeepSeek 两个 key 实测均 401 失效；等操作者换 key 重跑。pi-subagents 未安装（待批）。
- WP10 PASS（最小版，纯影子）：mandate 签发/撤销、确定性策略引擎、预算原子预占/结算/释放（上海营业日午夜切换）、影子授权日志（≥7天/≥100样本门槛如实判定）；无任何执行路径。
- 真实经营状态未变：账号 0、真实报价文件 0、平台动作 0。

---

## 2026-09-18 增量（薄闭环：报价→同款→成本→商机→决策卡）

按 [ZCODE_START_HERE_2026-09-18.md](ZCODE_START_HERE_2026-09-18.md) 执行的最小贯通轮，全部证据见
[docs/acceptance/WP_THIN_LOOP_2026-09-18.md](docs/acceptance/WP_THIN_LOOP_2026-09-18.md) 与
[docs/acceptance/BASELINE_2026-09-18.md](docs/acceptance/BASELINE_2026-09-18.md)。

- WP00 基线刷新 PASS：全量 1026/1026 实测通过（47.064s），data/live 哈希不变，GH-04/05 调用路径核验，skills 实测 9 个。
- 新增 commerce 域（WP01）：`xianyu_agent/commerce/`（契约 + repository，Money 三态金额/未知不填零，ADR-0001）。
- 供应商报价导入（WP03）：preview→confirm、CSV/XLSX/粘贴文本、整箱换算、批次可撤销，测试 20 条。
- 同款匹配（WP05）：硬约束四级判级 + 28 对标注集（EXACT 精确 6/6；未达 100 对，不宣称 95% 目标）。
- 贡献利润与商机（WP06）：三情景、黄金样例 104.03/34.79% 复算一致、确定性资格门禁（EXACT+保守利润>0+新鲜度）。
- 决策卡与面板最小版（WP09）：13 条 `/commerce/*` 路由 + console「商机雷达」；Playwright 视觉验收未做（完整 WP09 范围）。
- 来源能力登记（WP02）：5 来源 = 2 CONFIGURED / 3 UNVERIFIED，VERIFIED 需 ≥20 真实样本证据（代码强制）。
- 本轮终点全量回归：**1122/1122 通过**；8 个分包独立提交（41c20b3…da6882f）。
- 未变：真实账号 0、占位候选 T、真实订单/询盘/利润 0；无平台写动作；OpenClaw 未恢复。
- 下一阻塞不变：操作者提供真实供应商报价文件与账号（见验收文档「诚实声明」）。

---

Updated: 2026-09-05

## Status

- Technical code baseline: `READY` for current-runtime verification and the
  first real seller-account onboarding.
- Runtime now: `STOPPED`; FastAPI and Pi are intentionally not running, so no
  current health claim is made.
- Commercial operation: `BLOCKED` on an owned or authorized seller account,
  one exact supplier SKU, five current evidence files and current commercial
  terms.
- Real Commercial Alpha: `NOT STARTED` and not claimed complete.

## Verified In This Baseline

- Full isolated regression on 2026-09-05: `996` tests passed, `0` failed, in
  37.385 seconds. The suite used temporary `DATA_DIR` and `DB_PATH` boundaries;
  all 15 files under `data/live` retained identical lengths and SHA-256 hashes.
- One-SKU Commercial Alpha intake now has a read-only offline preflight. It
  generates a non-overwriting bundle template, validates the exact five evidence
  kinds and product images with the production archive rules, blocks traversal,
  symlinks, stale/future captures, malformed files and sensitive text, and
  projects the existing candidate gates without persisting synthetic evidence.
  A passing bundle remains `commercial_ready=false` until real archive import,
  account/category terms, current manual approval and Day-0 `20/20`.
- Candidate evidence is content-addressed and candidate-bound. Integrity checks
  now hash the file on every verification; a same-size tamper with the original
  modification timestamp is detected.
- SQLite initialization is deferred and bound by the server composition root to
  `Settings.db_path`. The legacy and live database table row counts were checked
  equal, and the migration backup remains in `data/sqlite-migration-backup`.
- Core Phase 9-29 engineering work has a recoverable Git baseline on branch
  `codex/opc-dashboard-v1`; the former large uncommitted implementation is no
  longer the project source of truth.
- The only platform write path exposed by the server is the approved chain:
  `WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor -> Adapter`.
- Execution jobs now use a persisted lease, per-claim token and increasing
  fencing token. Two local queue instances cannot claim the same task, and an
  abandoned `running` task becomes `verification_required` instead of being
  retried automatically.
- Unknown publish, edit, reply, delete, refund or fulfillment outcomes require
  an explicit platform check. The console and API record either
  `confirmed_completed` with platform evidence or `confirmed_not_executed`
  before a retry can be requested.
- Reply Outbox now projects reply drafts, approvals, execution attempts,
  receipts and manual verification from the existing authoritative chain. It
  binds account, buyer, chat, item, source event, revision and approved-message
  hash in an action digest; inbound replays return the original record, and an
  explicit idempotency key reused for different content returns HTTP `409`.
- Browser write failures now create a per-account hashed evidence pack with a
  redacted JSON manifest and a best-effort screenshot. The pack records the
  failure stage, adapter state and whether a platform write was attempted;
  post-submit uncertainty still requires explicit platform verification.
- Every new Approval now persists an action-scope envelope binding the account,
  targets, payload, approval snapshot, amounts, expected versions, lifetime,
  deterministic idempotency key and unique correlation ID. Approval stores the
  exact action digest that the operator accepted.
- Scope is recomputed before approval, enqueue, execution and unknown-result
  closure. A mismatch or expiry blocks before the adapter and records
  `write_attempted=false`; account reassignment clears the old decision and
  requires a fresh approval.
- Listing publication additionally binds the normalized on-disk draft and image
  contents. Legacy publication approvals without `expected_draft_revision` are
  blocked and must be recreated.
- Generic `/agents/dispatch`, `/agents/openclaw/message` and
  `/agents/hermes/message` are retired with HTTP `410`.
- Legacy listing and physical-listing APIs create a local draft and approval
  WorkOrder. The core workflow never publishes merely because `publish=true`.
- Direct polish and both automatic polish schedulers are retired.
- Pi remains the only primary natural-language entrypoint. Its allowlist now
  contains 12 typed Xianyu tools and no arbitrary-path `read`, shell, write,
  approval or execution tool.
- The Pi subprocess receives only model-provider and operating-system runtime
  environment variables, not unrelated database, Feishu or adapter credentials.
- The default service bind is `127.0.0.1`; CORS is limited to the local console
  origins for the configured port.
- OpenClaw Windows scheduled tasks previously found were disabled. This baseline
  does not re-enable OpenClaw or any automatic startup process.

## Not Verified As Current Runtime

- No process is currently listening on `127.0.0.1:8788` or the older port 8000.
- The 2026-08-29 FastAPI, Pi and browser checks are historical evidence, not a
  current health result.
- Before account login, run the normal start script and verify health, Pi status,
  the absolute live SQLite path, one-process behavior and clean stop behavior.
- No current live-model Pi turn or current browser UI regression was run in this
  documentation and hardening change.

## Current Business Ledger

| Item | Current value |
|---|---:|
| Historical WorkOrders | 15, all quarantined |
| Unresolved WorkOrders | 0 |
| Seller accounts | 0 |
| Domestic Xianyu candidates | 1 placeholder (`T`), evaluating |
| Placeholder activation packet | 1/12, `ready=false` |
| First product blocker | `product_identity` |
| Candidate evidence artifacts | 0 |
| Offline intake bundle | `E:\闲鱼\商业资料\commercial-alpha-001`, initialized and correctly blocked pending real inputs |
| Active Commercial Alpha trials | 0 |
| Real listing receipts | 0 |
| Real inquiries / paid orders / reconciled profit | 0 / 0 / 0 |
| First global readiness blocker | `account_configured` |

## Next Unique Goal

> Bind one real seller account and complete one exact SKU to candidate `12/12`
> and Day-0 `20/20`, then create one Commercial Alpha batch without publishing
> automatically.

## Next Operating Sequence

1. Create one offline intake directory with
   `scripts/commercial_alpha_intake.py init`; fill one exact SKU, five current
   evidence files and at least one authorized product image.
2. Run its read-only `validate` command until `bundle_ready=true`, the projected
   candidate is `11/12`, and only `manual_decision_current` remains. Do not treat
   this projection as a persisted candidate or commercial approval.
3. Start the service only when the operator is ready to perform the real login.
4. Re-run current health, Pi, database-path and repeated-start checks.
5. In `账号画像`, open the dedicated login browser and complete platform login.
6. Preview the actual identity, verify it, and complete the one-time `BIND ...`
   confirmation.
7. Initialize the account Prompt profile, then run same-account listing and
   order sync readiness checks.
8. Do not approve placeholder `T`. Create one exact domestic Xianyu candidate
   with at least two real deliverable specifications.
9. Add current supplier, SKU, cost, freight, inventory and fulfillment facts.
10. Upload and activate the same five validated evidence files: supplier quote, stock,
   fulfillment, image rights and market baseline.
11. Record current account-and-category terms. Reprice the current candidate,
   run Agent review and manually approve the exact current revision.
12. Require candidate `12/12` and Day-0 launch preview `20/20` before creating
     exactly one batch. Batch creation still does not publish.
13. Publish only through a separate approval and execution job. Unknown platform
     results must be verified before any retry.
14. Handle every real inquiry through Reply Outbox. Approval, enqueue and a
     confirmed platform receipt remain three separate operator decisions.
15. Expand only after a real 14-day experiment and, when an order exists, full
     procurement, dual-logistics, delivery and actual-profit reconciliation.

## Source Documents

- General-manager target architecture and 12-month plan:
  `docs/strategy/GENERAL_MANAGER_BLUEPRINT_2026-08-30.md`
- Commercial operating order: `docs/ops/commercial-alpha-runbook.md`
- Candidate evidence SOP: `docs/ops/xianyu/candidate-evidence-archive.md`
- Offline one-SKU intake preflight: `docs/ops/commercial-alpha-intake-bundle.md`
- Current handoff and remaining work: `ZCODE_HANDOFF.md`
- Pi runtime contract: `docs/ops/pi-migration.md`
- Unknown-result recovery SOP: `docs/ops/execution-recovery-runbook.md`
- Reply lifecycle and idempotency SOP: `docs/ops/reply-outbox-runbook.md`
- Browser failure evidence SOP: `docs/ops/browser-write-evidence-runbook.md`
- Action scope and approval binding SOP: `docs/ops/action-scope-approval-runbook.md`
