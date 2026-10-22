# R12 实施验收报告：审批、任务和外部执行持久化

日期：2026-10-22。基线为 R11 提交 `8d7545c`。

**结论：R12 软件/集成层 PASS。全量离线 1717/1717 OK（13 skipped=PG），live 数据 247 文件不变。**

## R12

```yaml
task_id: R12
owned_files: [xianyu_agent/action_scope.py, xianyu_agent/action_executor.py, xianyu_agent/resale.py, xianyu_agent/server.py(run_next context), tests/test_r12_approval_persistence.py]
software_status: PASS   # 9/9 新验收 + 存量 scope 17/17 executor 11/11 不破
integration_status: PASS  # 全量 1717/1717 OK, 数据 247/247 不变
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [JSON 单进程内互斥+fencing；PG lease/outbox 合同在 R10 schema 就绪，R15 全域迁移切换]
rollback: 停止 claim（保留 UNKNOWN 与已认领任务）
next_task: R13
```

## 现状盘点（大部分已在前轮建成，本轮补齐最后一类失效条件）

任务书四项要求 vs 代码现状：

| 要求 | 现状 | 本轮动作 |
|---|---|---|
| 审批绑定 account/payload_hash/input_version/期限/额度 | **已有**：scope envelope 绑 cookie_id、payload_sha256、VERSION_KEYS(条款版本/快照版本/草稿修订×7)、AMOUNT_KEYS(金额×7)、按动作分级 TTL(15min-24h)、确定性幂等键、HMAC 式 action_digest；三层拦截（决策前/批准后/入队后）已有 17 项存量测试 | 补齐 supplier_snapshot_id 进 TARGET_KEYS、supplier_snapshot_payload_hash 进 VERSION_KEYS |
| PG lease/fencing | **已有**（JSON 层）：claim 递增 fencing_token，renew/execute/finalize 三处 `_require_current_claim` 校验 worker+lease_token+fencing；租约过期→verification_required（UNKNOWN 不盲重试）；PG 侧 lease/outbox 表在 R10 schema | 验收测试确认 |
| outbox 与业务意图同事务 | R10 PG outbox 表 + UoW.write_outbox 同事务就绪（R11 采购命令层是 JSON 侧"同命令"实现）；JSON 生产切换属 R15 | 无需新代码 |
| 未知结果先回读 | **已有**：verification_required + resolve_verification + verify_platform_before_retry；executor 异常对外部写动作标记 outcome_uncertain | 验收确认 |

## 本轮新修复：报价变更使旧批准失效（审查验收项"修改载荷/报价/账号"中的"报价"缺口）

载荷变更（digest 拦截）、账号变更（account_scope_conflict + digest）此前已覆盖；**报价变更**缺口：`prepare_purchase_draft` 执行时不检查候选绑定的供应商快照是否仍是最新——价格已变而批准的经济性是旧的，照样创建采购任务。

修复链：
1. `resale.create_candidate` 接受并存储 `supplier_snapshot_id`/`supplier_snapshot_payload_hash`（候选经济性绑定到确切快照）；
2. `action_scope` 把快照 ID 纳入 target_refs、快照哈希纳入版本指纹（绑定受 digest 保护，篡改即失效）；
3. `handle_prepare_purchase_draft` 执行时报价新鲜度门：绑定快照必须仍存在且仍是该 provider+SKU 的**最新已提交快照**；更新快照已到 → `stale_supplier_quote` 结构化失败，**零草稿零任务**，操作者重建计划重批；仓储未接线时拒绝盲执行；
4. server `run-next` 执行上下文注入 commerce_repositories。

## 验收矩阵（tests/test_r12_approval_persistence.py，9/9）

| 任务书验收项 | 测试 |
|---|---|
| 修改报价使旧批准失效 | 更新快照到达 → 执行前拦截（stale_supplier_quote），无草稿无任务 |
| （绑定缺失同理） | 绑定快照被删 → supplier_snapshot_missing 拦截 |
| 修改载荷使旧批准失效 | 篡改 payload.supplier_snapshot_id → digest 不匹配（ActionScopeValidationError） |
| 绑定完整性 | envelope target_refs/versions 含快照绑定；绑定但无仓储 → 拒绝盲执行 |
| 重复 webhook 不重复外部动作 | 二次 execute → "Active job already exists" 结构化拒绝，恰好一个 job；同幂等键的新批准在首个 job 完成后也被拒 |
| 过期 worker 回写 | fencing_token 在 claim/renew/finalize 三点校验（存量测试覆盖，验收确认） |
| 未失效路径 | 快照仍最新 → 草稿+任务正常创建；无绑定候选不受新门影响 |
