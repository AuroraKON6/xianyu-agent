# 第三批（上）R10 实施验收报告：PostgreSQL 全域合同与 UoW

日期：2026-10-22。实施者：GLM。基线为第二批提交 `e153871`。另：本次会话完成两项环境工程——GitHub 推送通道修复（系统代理 127.0.0.1:7890 经 connect.exe 隧道走 SSH-443，已固化到 ~/.ssh/config；两批提交已推远端 `origin/codex/opc-dashboard-v1`）与独立 PostgreSQL 18.4 评估环境（conda 便携安装于 `E:\pg-r10\env`，集群端口 54329，角色 pgadmin/xianyu_app，库 xianyu_r10；完全可弃，删除目录即回退）。

**结论：R10 软件/集成层 PASS（真 PG 实例验收 15 项全绿）；离线全量默认套件不受影响（无 DSN 时 PG 用例自动跳过）。生产仍为 JSON 单 writer——本批零双写。**

## R10

```yaml
task_id: R10
owned_files: [migrations/0001_core_write_domains.{up,down}.sql, xianyu_agent/persistence/, tests/test_postgres_uow.py]
software_status: PASS
integration_status: PASS  # 真 PG 18.4 建立/升级幂等/降级重升往返、约束矩阵、UoW、角色越权
real_data_status: NOT_RUN
commercial_status: NOT_RUN
commands_and_exit_codes:
  - "test_postgres_uow.py (with XIANYU_PG_TEST_{ADMIN,APP}_DSN) -> 15/15 OK (skipped=1 隔离白名单项)"
  - "run_offline_checks.py -> 1696/1696 OK (skipped=13=PG用例), exit 0, data 247/247 unchanged"
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits:
  - 0001 覆盖核心写域(账户/研究四表/任务/工单/执行/outbox/销售/采购/预算/库存/账本)；其余写域(quota/条款/trials/消息等)按任务书在 R11-R15 增量迁移
  - PG 仓储为合同镜像证明(offers/opportunities)，未接线 server——R15 全域迁移时切换
  - 评估集群为 trust 本地认证(独立端口/可弃)；生产部署时按 R31/R16 换正式认证
rollback: migrations down.sql + 删除 E:\pg-r10；生产保持旧 JSON 单 writer，无暗中双写
next_task: R11
```

## 验收要点（tests/test_postgres_uow.py，PG 18.4 实测）

| 验收项（任务书原文） | 证据 |
|---|---|
| 独立 PG 建立/升级 | MigrationRunner 0001 事务式建表；重复 upgrade 零应用（幂等） |
| 回退兼容 | downgrade 0001 清空 schema → 再 upgrade → 重新接受写入 |
| 账号复合键 | 同 external_id 两账号并存（UNIQUE(account,channel,external)）；同账号重复被拒 |
| 唯一约束/FK | 重复 (source_event_id,entry_type) 账本拒绝；孤儿 decision FK 拒绝 |
| Decimal | NUMERIC(20,6) 123.456789 精确往返；float 不进任何金额列 |
| UoW 事务边界 | 异常/回滚时业务行+outbox 同时消失；outbox 意图键去重 |
| 锁顺序 | LOCK_ORDER 固定 12 域，pg_advisory_xact_lock 按序；未知域拒绝 |
| 不同 DB 角色越权拒绝 | app 角色 CREATE TABLE 拒绝；ledger UPDATE 拒绝（只 INSERT+SELECT，账本只增）；DML 正常 |
| sqlite 不抵 PG | 全部断言在真 psycopg2 连接上执行；隔离模式下 DSN 须在精确白名单（R01 fail-closed 规则接入） |

## 环境/交付说明

- PG 评估栈：`E:\pg-r10\env`（conda postgresql 18.4，win）+ `E:\pg-r10\cluster`（initdb -A trust，端口 54329）。启动：`E:/pg-r10/env/Library/bin/pg_ctl.exe -D E:/pg-r10/cluster -o "-p 54329" start`；停止：`... stop -m fast`。验收命令：
  `XIANYU_PG_TEST_ADMIN_DSN=postgresql://pgadmin@127.0.0.1:54329/xianyu_r10 XIANYU_PG_TEST_APP_DSN=postgresql://xianyu_app@127.0.0.1:54329/xianyu_r10 .venv/Scripts/python.exe -m unittest discover -s tests -p test_postgres_uow.py`
- GitHub：推送经 `connect.exe` HTTP 代理隧道走 SSH-443（github-zcode Host 已加 ProxyCommand）。
