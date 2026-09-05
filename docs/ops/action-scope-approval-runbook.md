# 外部动作作用域与审批绑定运行手册

更新日期：2026-09-04

## 1. 目的

本手册适用于所有可能改变闲鱼、买家、订单或供应商状态的动作。系统必须确保操作者批准的内容与最终进入执行器的内容一致，至少绑定：

- 卖家账号；
- 商品、草稿、订单、买家、会话、纠纷或采购目标；
- 动作参数和审批快照；
- 预期版本；
- 金额和数量；
- 有效期；
- 幂等键和关联 ID。

唯一允许的外部写入链仍是：

```text
WorkOrder
  -> Approval + scope_envelope
  -> operator approval + approved_action_digest
  -> ExecutionJobQueue
  -> ActionExecutor
  -> Adapter
```

Pi、专家 Agent、控制台和 API 都不能绕过这条链直接写平台。

## 2. 当前覆盖动作

| 动作 | 典型目标 | 有效期 |
|---|---|---:|
| `send_message` | 账号、买家、会话、商品、来源消息 | 15 分钟 |
| `sensitive_reply` | 账号、买家、订单、纠纷 | 15 分钟 |
| `refund_order` | 账号、订单、退款金额 | 1 小时 |
| `reject_refund` | 账号、订单、纠纷 | 1 小时 |
| `place_supplier_order` | 采购任务、供应商订单、金额、数量 | 1 小时 |
| `fulfill_order` | 账号、订单、物流目标 | 4 小时 |
| `prepare_purchase_draft` | 订单、候选、采购参数 | 4 小时 |
| `publish_listing` | 账号、候选、草稿及草稿版本 | 24 小时 |
| `edit_listing` | 账号、商品、修改变量和值 | 24 小时 |
| `delete_item` | 账号、商品 | 24 小时 |
| 其他审批动作 | 具体 payload 和 snapshot | 24 小时 |

超过有效期后不得批准、入队、执行或重试。平台结果已经未知时，可以在过期后人工核验关闭，但内容、账号、目标和批准摘要仍必须完全一致。

## 3. `scope_envelope` 字段

| 字段 | 含义 |
|---|---|
| `schema_version` | 作用域结构版本；当前为 `1` |
| `approval_id` / `work_order_id` | 审批与工单关联 |
| `action` | 被批准的唯一动作 |
| `cookie_id` | 被批准的卖家账号标识，不是 Cookie 内容 |
| `target_refs` | 草稿、候选、商品、订单、买家、会话等目标 ID |
| `expected_versions` | payload 和 snapshot 中的修订、证据指纹及条款版本 |
| `expected_revision` | 预期版本集合或审批快照的规范化摘要 |
| `amounts` | 金额、价格和数量等财务参数 |
| `payload_sha256` | 完整执行参数摘要 |
| `draft_snapshot_sha256` | 人工审批快照摘要 |
| `requested_at` / `valid_until` | 申请时间和失效时间 |
| `correlation_id` | 每次审批唯一的追踪 ID |
| `idempotency_key` | 同一动作作用域的确定性去重键 |
| `action_digest` | 上述字段的规范化 SHA-256 摘要 |

操作者批准时，系统把当前 `action_digest` 复制到 `approved_action_digest`。这是“批准内容绑定”，不是电子签名或操作者身份认证。

## 4. 三次执行前校验

### 4.1 批准前

`POST /approvals/{approval_id}/decision` 在写入批准决定之前重新计算作用域。参数、账号、目标、版本或有效期不一致时返回 HTTP `409`，审批仍保持待处理。

### 4.2 入队前

`POST /approvals/{approval_id}/execute` 在创建 ExecutionJob 前重新计算作用域，并核对 `approved_action_digest`。校验失败时不创建任务。

队列把同一份 `scope_envelope` 和 `idempotency_key` 固化到任务。已有活动任务或已完成任务使用相同幂等键时，队列拒绝再次创建。

### 4.3 Adapter 前

Worker 领取任务后，WorkOrderManager 再次用当前 Approval 重算作用域。失败时：

- 不调用 ActionExecutor 的动作 handler；
- 不调用 BrowserAdapter；
- ExecutionJob 和 Approval 记录失败原因；
- `write_attempted=false`；
- 下一步固定为重建行动计划并重新批准。

## 5. 发布草稿的额外版本锁

`publish_listing` 必须携带 `expected_draft_revision`。该版本由最终浏览器写入字段计算，包括标题、类目、价格、库存、成色、位置、描述、标签、图片、交付方式和来源。

图片既绑定引用路径，也在文件存在时绑定当前文件内容 SHA-256。因此以下变化都会在浏览器调用前被阻断：

- 修改标题、价格、库存、描述或交付方式；
- 增删或替换图片；
- 保持图片文件名不变但修改图片内容；
- 使用没有预期草稿版本的旧审批。

业务层的 `images` 与执行层的 `selected_images` 会先规范化成同一浏览器字段，合法的落盘转换不会误报。

## 6. 控制台操作

审批卡片会显示：

- `已绑定批准内容`、`待批准绑定` 或 `批准作用域已过期`；
- account；
- 最多三个目标引用；
- expected revision、action digest 和 correlation ID 的短摘要。

执行任务卡片显示 scope digest 和 correlation ID。短摘要只用于人工快速比对，API 和账本保存完整值。

批准前逐项核对账号、目标、金额、版本和有效期。任何一项不是当前经营对象，都应拒绝审批并重建，不要直接编辑 JSON 文件。

## 7. 错误码与恢复动作

| 错误码 | 含义 | 操作者动作 |
|---|---|---|
| `scope_envelope_missing` | 旧审批没有作用域 | 作废旧审批，按当前业务对象重建行动计划 |
| `scope_schema_unsupported` | 作用域版本不受支持 | 升级后重建行动计划 |
| `account_scope_conflict` | 创建时 payload、snapshot、context 指向不同账号 | 修正来源数据后重新创建 |
| `action_scope_mismatch` | 审批内容、账号、目标、版本或摘要在创建后改变 | 不重试旧任务，重建并重新批准 |
| `approval_signature_missing` | 尚未把当前摘要绑定到人工批准 | 回到审批卡片处理 |
| `approval_signature_mismatch` | 批准摘要与当前作用域不一致 | 作废并重建 |
| `action_scope_expired` | 已超过动作有效期 | 刷新业务事实，重建并重新批准 |
| `draft_revision_missing` | 发布审批没有草稿版本 | 从当前落盘草稿重建发布审批 |
| `draft_revision_mismatch` | 当前草稿或图片与批准版本不同 | 检查差异，重建发布审批 |

作用域失败不是网络错误。不要点击旧任务“重试”，因为旧任务绑定的事实已经失效。

## 8. 账号归属修复

历史未归属工单通过 account attribution 的 `claim` 修复账号时：

1. 只允许处理没有任何 ExecutionJob 的工单；
2. payload、snapshot 和 context 改为目标账号；
3. 原批准决定和 `approved_action_digest` 清空；
4. Approval、ActionPlan 和 WorkOrder 回到待审批；
5. 生成新的 scope envelope；
6. 操作者必须重新核对并批准。

已有任务的历史工单不能改绑账号。应先隔离或人工审计，再按正确账号重建。

## 9. 未知结果核验

`verification_required` 表示平台可能已经接受动作，不能当作普通失败。核验流程见 `docs/ops/execution-recovery-runbook.md`。

核验关闭前仍校验 action digest。只允许忽略有效期，因为操作者可能在动作过期后才完成平台检查；绝不忽略账号、目标、参数或版本变化。若校验失败，ExecutionJob 保持 `verification_required`，不会先写成完成。

即使作用域完全通过，也不能据此判断平台已经执行。平台商品 ID、消息 ID、订单 ID、页面 URL 或截图仍是完成核验所需的外部证据。

## 10. 能力边界

当前首轮实现提供应用层防错和审计绑定，但不应被扩大解释：

- SHA-256 摘要不是具有独立密钥的数字签名；
- 它不证明批准者真实身份，也不抵御可同时修改代码和全部本地 JSON 的管理员；
- 除发布草稿本地版本外，当前未对所有平台对象做执行前实时版本读取；
- 幂等键阻止本地重复入队，不证明平台没有在断连前执行；
- 测试、模拟 adapter 和本地回执都不是商业成交证据。

生产增强方向是把 Approval、ExecutionJob 和事件账本迁入事务数据库，增加操作者身份、不可变审计日志和平台对象版本读取。在首次真实单账号、单商品 Alpha 前，不为此增加第二条执行链。

## 11. 验收命令

服务保持关闭时执行：

```powershell
cd E:\闲鱼\xianyu
python -m pytest -q tests/test_action_scope.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_tests_isolated.ps1
python -m py_compile xianyu_agent\action_scope.py xianyu_agent\work_orders.py xianyu_agent\execution_queue.py xianyu_agent\action_executor.py xianyu_agent\server.py
node --check web\app.js
git diff --check
```

验收必须同时满足：

1. 批准前、入队前和执行前的篡改都被阻断；
2. 发布草稿和图片内容变化在 Adapter 前被阻断；
3. 作用域失败留下 `write_attempted=false`；
4. 未知结果核验发生作用域冲突时不提前提交任务状态；
5. 账号改绑后原批准失效；
6. 全量测试没有写入 `data/live`；
7. 没有启动服务或执行真实平台写入。
