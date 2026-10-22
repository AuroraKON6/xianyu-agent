# 第三批（下）R11 实施验收报告：采购/预算/库存原子命令

日期：2026-10-22。实施者：GLM。基线为 R10 提交 `0ff0b77`。

**结论：R11 软件/集成层 PASS。审查 12 例 finance 探针中 11 个失败全部转绿（原探针脚本 12/12 + 常驻移植 12/12），全量离线 1708/1708 OK（13 skipped=PG 用例），live 数据 247 文件前后一致。**

## R11

```yaml
task_id: R11
owned_files: [xianyu_agent/application/purchase_commands.py, xianyu_agent/procurement.py, xianyu_agent/budget_reservations.py(未改,经命令层编排), xianyu_agent/inventory.py(未改), xianyu_agent/order_runtime.py, xianyu_agent/ledger_service.py, xianyu_agent/server.py(路由薄委托), xianyu_agent/app_factory.py(reconcile钩子), tests/test_finance_combinations.py, tests/test_procurement_ledger.py, tests/test_recovery_drills.py]
software_status: PASS   # 探针12/12绿
integration_status: PASS  # 全量1708/1708 OK, 数据247/247不变
real_data_status: NOT_RUN
commercial_status: NOT_RUN
commands_and_exit_codes:
  - "research/codex-recheck-2026-10-13/finance-combinations.py --case all -> 12/12 (invariant_failures=0)"
  - "test_finance_combinations.py -> 12/12 OK"
  - "run_offline_checks.py -> 1708/1708 OK (skipped=13=PG), exit 0"
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits:
  - JSON 存储无跨进程事务，原子性靠单writer进程内互斥锁+补偿回滚；真事务在 R10 PG UoW 已就绪，R15 全域迁移后切换
  - 订单运行时复合键改动为向后兼容实现（runtime_key 字段持久化，旧数据按 order_id 读取）
rollback: 停采购创建路由（保留只读），已完成采购继续人工履约；全部事实保留
next_task: R12
```

## 11 个失败反例 → 修复对照

| 探针（失败观测） | 根因 | 修复 |
|---|---|---|
| legacy_first（新链对已归 legacy 的售单 200 建采购） | 新链创建不检查 legacy 所有权 | 命令层 `create_procurement` 在同一互斥域内先查 legacy 活动任务，命中即 409 |
| legacy_cross_account（异账号 legacy 标记被 409） | 所有权检查只按订单号不按账号 | 所有权键 = (account, sales_order)：异账号并存，同账号冲突 |
| legacy_service_interleave（检查与提交不同步，双写成功） | legacy 检查在锁外 | 双重检查：锁外快查 + **持 procurement 互斥锁复查后提交**；交错窗口内新链提交可见 → legacy 409 |
| runtime_account_collision（B 账号 upsert 覆盖 A 的订单） | 运行时按 order_id 单键 | 复合身份 (cookie_id, order_id)：异账号同单号各自成行、互不覆盖，重载后保持 |
| cancel_crash_restart（取消后崩溃，重启+重试锁仍持有） | 取消已持久化但锁释放失败无治愈路径 | 取消幂等重试**治愈**锁释放；启动 reconcile 把已取消采购的锁视为孤儿释放 |
| cancel_budget_recreate（陈旧取消重试不释放预算，预留 360≠180） | 取消从不释放预算预留 | 取消释放 THIS 订单的预算预留（job 级键：陈旧重试不可能碰到新预留）；幂等重试不重复释放 |
| budget_rejection（预算拒绝 → HTTP 200 + 采购成立 + 库存锁） | 路由吞掉 BudgetError | 命令层在同一互斥域内先建后预留：拒绝 → `rollback_create`（删记录+释放锁）→ 409，什么都不留下 |
| receipt_replay_costs（账本成本 ["200","200","999","180"] 四条） | 路由每次重放都按请求体重写账本，缺实际值时回退报价 | 账本写入移入命令层：只用**首次收货的不可变实际成本**（175+25=200），重放幂等零写入，改金额重放被拒，首收必须带实际成本（报价永不替代） |
| receipt_ledger_failure_restart（账本失败 → 200 已收货零成本） | 路由吞掉账本异常 | 命令层补偿：账本失败 → 状态回退 ordered + 503 明示"回退待重试"，绝不假成功 |
| receipt_prefix_not_payment（"platform:" 前缀 → settled） | 前缀启发式被当付款证明 | `record_spend(verified=False)` 显式压制：供应商收货单**永不**是已核验付款证据 → 恒为 provisional |
| startup_mixed_owners（孤儿 proc: 锁未释放） | R02 幂等 start 跳过重复 startup 的恢复 | AppRuntime 新增 `on_reconcile` 钩子（幂等治愈，每次 startup 事件都跑）：租约恢复+孤儿锁回收不再被"已启动"短路 |

并发验收（原已通过的 recovery_create_serialization 保持绿）：reconcile 与 create 共享同一互斥锁，恢复扫描不可能插入创建的检查-写入窗口。

## 文件清单

修改（9）：app_factory.py、ledger_service.py、order_runtime.py、procurement.py、server.py、tests/{test_procurement_ledger,test_recovery_drills}.py
新增（2）：application/purchase_commands.py、tests/test_finance_combinations.py
