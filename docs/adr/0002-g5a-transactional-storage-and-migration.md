# ADR-0002：G5A 生产事务存储与迁移设计

日期：2026-10-12。状态：**提议（设计稿）**——落地与真实切换需要用户批准，本 ADR 本身不授权迁移执行。前置：[ADR-0001](0001-commerce-storage-and-migration.md)。

对应：总计划 G5A（矩阵 A110-A116）。触发条件（ADR-0001 已定）：引入第二个业务写入进程或任一 JSON 文件超性能阈值。本文将其细化为可执行设计。

## 1. 问题

当前权威存储为：

| 文件（data/live 或 data_dir/commerce/） | 域 | 写者 |
|---|---|---|
| `commerce/offers.json` | OfferSnapshot（供应商报价+市场观察+通道抓取） | server 进程内各服务（单写者） |
| `commerce/matches.json` | ProductMatch | 同上 |
| `commerce/opportunities.json` | Opportunity（revision 状态机） | 同上 |
| `commerce/decisions.json` | DecisionRecord（append-only） | 同上 |
| `commerce/agent_jobs.json` | AgentJob（租约/fencing，Pi+radar 共享） | 同上 |
| `commerce/import_batches.json` | 供应商导入批次 | 同上 |
| `commerce/intake_confirmations.json` | G1A 用途确认 | 同上 |
| `commerce/radar_events.json` | G2A 事件 | 同上 |
| `commerce/opportunity_adoptions.json` | G3A 采纳 | 同上 |
| `commerce/procurement_orders.json` | 采购单 | 同上 |
| `commerce/inventory.json` | 库存分配+锁 | 同上 |
| `commerce/ledger.json` | 账本分录 | 同上 |
| `resale_candidates.json`、`work_orders.json`、`execution_jobs.json` 等旧 store | 候选/工单/执行队列 | 同上 |
| SQLite `xianyu_data.db` | 账号/商品/订单/消息（db_manager） | 同上 |

`LockedJsonList` 提供：进程内线程锁 + 原子替换 + 失败回滚快照。**没有**：跨文件事务、多进程互斥、崩溃后未完成多文件写的统一恢复。当前所有写入都在**一个进程**内，所以一致；任何第二写进程（真实 worker、计划任务拆分）都会破坏它。

## 2. 决策（设计）

### 2.1 迁移域与表

生产目标 PostgreSQL（ADR-0001 既定）。每 JSON 清单 → 一张表，全部保留公共列 `record_id (text PK)`, `record_type`, `scope`, `account_id`, `source_ref`, `trace_id`, `created_at (timestamptz)`, `revision (int)`, `payload (jsonb)`；域内唯一键：

| 表 | 唯一约束 |
|---|---|
| offers | `(provider_id, source_item_id, source_sku_id, observed_at, payload_hash)`（现 offer_natural_key） |
| matches | `(left_snapshot_id, right_snapshot_id, rule_version, grade)` 去重语义改为 `(pair, created_at)` 最新判定查询 |
| opportunities | `(canonical_sku, source)`（现 opportunity_natural_key） |
| decisions | `record_id` PK，append-only（无 UPDATE 权限的 DB 角色） |
| agent_jobs | `idempotency_key` WHERE NOT NULL 部分唯一索引 |
| intake_confirmations | `idempotency_key` 唯一；`request_fingerprint` 冲突校验在服务层保留 |
| radar_events / opportunity_adoptions | `idempotency_key` 唯一 |
| procurement_orders | `sales_order_ref` 部分唯一（status <> 'cancelled'） |
| inventory | `(account_id, sku)` 唯一；locks 并入 payload jsonb |
| ledger | `record_id` PK；`(reversal_of)` 外键；累计上限校验保留在服务层（`SELECT ... FOR UPDATE` 于原分录） |
| import_batches / 旧 store | `record_id` PK + 各自业务键（批次 content+provider 等） |

SQLite 域（账号/商品/订单/消息）**原地保留 SQLite 或随切换统一入 PostgreSQL**——切换点一并决定，不搞两阶段混合写。

### 2.2 事务边界

- 单聚合写：原服务逻辑不变，repository 换实现（`JsonOfferSnapshotRepository` → `PgOfferSnapshotRepository` 等，协议不变——ADR-0001 的设计收益）。
- 跨聚合写（唯一的真正跨文件点）：`procurement.create_from_sales_order` 的"采购单+库存锁"。迁移后放进**一个 DB 事务**（`mutation_guard` 变为 `BEGIN ... SELECT ... FOR UPDATE ... COMMIT`）。账本冲正上限同理（原分录 `FOR UPDATE` + 累计校验 + 插入同事务）。
- 外部平台动作：**不用 DB 事务假装覆盖**——outbox 表与业务变更同事务提交，发送后回执未知保持 UNKNOWN，由执行链核验（G5A-4，与现状语义一致）。
- `valid_until`/资格判定仍是**读时计算**，不缓存 ready（G1B 原则延续）。

### 2.3 读兼容与切换点

- 迁移期间旧 JSON 文件变**只读投影**（切换前最后一份导出），代码读取一律走 repository 协议，故只需在组合根换实现。
- 切换点：单一停写窗口——(1) 停 server；(2) 运行导出器生成 PostgreSQL 初始数据并核对（A113 核对清单：行数、金额合计、唯一关系、未完成任务数、UNKNOWN 执行数、证据 hash）；(3) 配置切换 + 冒烟；(4) 旧文件移入 `archive/`（不删除）。
- 回退：新交易发生前——直接换回 JSON 配置（数据零损失）；新交易发生后——**禁止盲还原旧快照**：停写、导出 PostgreSQL 增量、人工对账并入 JSON、再切回（G5A-6）。不可逆边界：PostgreSQL 中已提交且未回传 JSON 的写入在简单回退中会丢，必须走对账合并。

### 2.4 迁移器与演练

- `scripts/export_commerce_to_pg.py`（待实现）：读全部 JSON 清单 → 写 PostgreSQL → 输出核对报告（每表行数/合计/唯一键冲突/孤儿引用），`--dry-run` 只出报告。
- 隔离演练（A111-A114）：pytest 起真实 PostgreSQL（testcontainers 或本地实例），跑竞争 worker（库存/预算/同任务双认领）、崩溃注入（事务中 kill -9 后重启）、迁移核对、备份恢复计时。**测试不得用 SQLite/内存冒充 PostgreSQL**（矩阵 A111 明确）。
- 全部隔离演练通过后才向用户申请真实目录切换批准（A116 B/P 层）。

## 3. 后果

- 优点：跨聚合原子性、多 worker 前置就绪、行级并发、备份/恢复工具链成熟。
- 代价：新增 PostgreSQL 运维面（本机 Windows 开发环境用 Docker 或便携实例）；迁移窗口需要停写；服务层两处锁语义要改为 DB 事务（采购创建、账本冲正）。
- 明确不做：Redis/Celery/Kafka（计划 §3.2）；不迁移 `.pi/` 会话日志与证据 blob（blob 保留内容寻址文件存储，只迁索引）。

## 4. 批准请求（未来时）

1. 选定 PostgreSQL 实例方式（Docker / 本机安装 / 现有实例）。
2. 批准隔离演练环境（不触碰 data/live）。
3. 隔离演练全绿后，单独批准真实停写切换窗口。
