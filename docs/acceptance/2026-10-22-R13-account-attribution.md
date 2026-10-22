# R13 实施验收报告：订单与账号归属统一

日期：2026-10-22。基线为 R12 提交 `d09a4dd`。

**结论：R13 软件/集成层 PASS。全量离线 1725/1725 OK（13 skipped=PG），live 数据 247 文件不变。**

## R13

```yaml
task_id: R13
owned_files: [xianyu_agent/db_manager.py, xianyu_agent/order_runtime.py, xianyu_agent/sync_persistence.py, tests/test_r13_account_attribution.py]
software_status: PASS   # 8/8 新验收 + 存量 order_runtime/db_boundary/account_profiles/adapter_sync 21 项不破
integration_status: PASS  # 全量 1725/1725 OK, 数据 247/247 不变
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [旧 orders 表迁移按 (cookie,order) 去重保留最新行；after_sales 归属在 R14 账本冲正一并核]
rollback: 暂停自动 sync，保留上次快照与缺口（partial 标记使缺口可见）
next_task: R14
```

## 验收矩阵（任务书四项 → 实现）

| 任务书验收项 | 实现 | 测试 |
|---|---|---|
| 同订单ID两个账号并存，不能互相覆盖 | SQLite orders 表复合主键 **(cookie_id, order_id)**（含旧单键 schema 自动迁移：按 (账号,订单) 去重保留最新行重建）；`update_order_status` 可选 cookie 作用域；`get_order` 支持 cookie 精确查询。order_runtime 复合身份 R11 已建 | `test_same_order_id_two_accounts_coexist` / `test_scoped_status_update_does_not_move_other_account` / `test_legacy_single_pk_schema_migrates` |
| 乱序事件不倒退 | `upsert_order`（同步路径）改走 `_synced_status`：非法迁移（如已 shipped 再收到 paid）**不崩不倒退**——记录 `ignored_out_of_order` 历史事件，保持当前状态；操作者路径 `update_status` 保持严格校验 | `test_out_of_order_event_does_not_regress_or_crash` / `test_forward_transition_still_applies` / `test_deliberate_invalid_operator_transition_still_raises` |
| 同步失败不刷新成功时间 | 仅有实际成功的资源写 sync state（失败资源 last_synced_at 保持旧值——新鲜度报告如实变旧） | `test_failed_resource_does_not_refresh_success_timestamp` |
| partial sync 明示不完整 | 返回体新增 `partial: bool`（一好一坏时为 true）；`ok` 仍为总成功标志 | `test_failed_resource...` / `test_both_ok_is_not_partial` |

"来源错账号拒绝"：同步以 cookie_id 为调用参数（每账号各自会话），错账号数据在复合键下天然落入对方账号行而非覆盖；采购路径的账号校验在 R11 命令层（runtime 账号匹配 409）已覆盖。
