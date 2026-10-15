# GLM G5A 报告：ADR-0002 迁移设计 + 核对器（部分交付）

日期：2026-10-12。执行者：GLM。

## 包与范围

G5A 首批：A110（ADR 设计）、A113 的**核对器**部分。A111/A112/A114/A116 的真实数据库演练 **WAITING_INPUT**（本机无 PostgreSQL/Docker，矩阵 A111 明确禁止用内存/SQLite 替身充数）。

## 状态总览

- 软件（S）：**PASS**（ADR + 5 项核对器专项 + 全量 1570/1570，exit 0）
- 进程/存储（P）：**WAITING_INPUT**（需 PostgreSQL 实例做真实并发/崩溃/迁移演练）
- 商业执行（B）：**NOT_AUTHORIZED**（真实停写切换需单独批准，本 ADR 不授权）

## 交付

| 项 | 内容 |
|---|---|
| [ADR-0002](../adr/0002-g5a-transactional-storage-and-migration.md) | 全部 12 个 commerce JSON 清单 → PostgreSQL 表映射（公共列 + 域唯一键）、SQLite 域处理、事务边界（唯一真正跨聚合点：采购创建的"采购单+库存锁"、账本冲正累计校验 → DB 事务 + FOR UPDATE）、读兼容（repository 协议不变，组合根换实现）、切换点（停写→导出→核对→切换→旧文件归档不删）、回退边界（新交易后禁止盲还原，须停写导出增量人工对账）、外部动作 outbox 语义、迁移器与演练计划、批准请求清单 |
| `scripts/audit_commerce_ledgers.py`（新） | 只读核对器：逐清单行数/类型/金额净额（分币种）、ADR-0002 唯一键冲突检测、孤儿引用（match→offer、opportunity→match/offer、adoption→opportunity、reversal→原分录、confirmation→offer）、未完成任务与 UNKNOWN 执行清单、证据 blob 哈希完整性；exit 0/2/1；报告 stdout |
| `tests/test_commerce_audit.py`（新） | 5 项专项：干净账本 exit 0；重复唯一键+孤儿+开放任务 exit 2；账本净额（102）+在途采购冲突；证据 blob 完整/篡改两态；缺目录 exit 1 |

## live 只读审计基线（2026-10-12）

```text
python scripts/audit_commerce_ledgers.py --data-dir data/live   # exit 0
rows: decisions 4, agent_jobs 8（其余清单空）
unique_key_conflicts: 0   orphan_references: 0   findings: []
open_agent_jobs: ['job_be7e79f3f16a4494']（1 个 queued 任务如实登记，迁移时须处理）
evidence artifacts: 0
```

## 矩阵逐项

| ID | 结果 | 说明 |
|---|---|---|
| A110 | PASS (S) | ADR-0002 覆盖全部共享写域（不止商机表）、schema/唯一键/锁顺序（DB 事务替代进程锁的两处点）、迁移与读兼容、回退条件明确 |
| A113（核对器部分） | PASS (S) | 核对器对合成账本与 live 均验证（行数/金额/唯一/孤儿/未完成/证据哈希）；PG 装载步骤 NOT_RUN |
| A111/A112/A114 | **WAITING_INPUT (P)** | 需要用户批准 PostgreSQL 实例方式（Docker/本机安装/现有实例）后做竞争 worker、崩溃注入、迁移装载、备份恢复演练；不以前置/内存测试冒充 |
| A115 | 部分 PASS (S) | 显式启停/停止不新认领已由 radar（A43 测试）与 pi runner 边界锁覆盖；健康检查/日志容量观察待部署轮 |
| A116 | NOT_AUTHORIZED | 真实停写切换须 A111-A114 全绿后单独批准 |

## 命令与退出码

```text
unittest discover -p test_commerce_audit.py   # 5/5 OK
python scripts/audit_commerce_ledgers.py --data-dir data/live  # exit 0（只读）
powershell scripts/run_tests_isolated.ps1     # 1570/1570, 238.610s, exit 0
```

## 需要用户提供的精确输入

1. PostgreSQL 实例方式与位置（用于隔离演练，不触碰 data/live）。
2. 批准演练时间窗；演练全绿后另行批准真实切换窗口（本 ADR §4）。

## 回退方案

ADR 为设计稿可整篇撤回；核对器为只读脚本可删；无业务数据变更。

## 下一工作包

前端整合轮（采纳按钮/通道状态卡/订单详情面板，API 全就绪）或按用户输入启动 G5A 演练。
