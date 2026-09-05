# 回复发件箱运行手册

Updated: 2026-09-02

## 1. 目的

回复发件箱把一条买家回复从“生成草稿”一直追踪到“平台已确认发送”，避免把草稿、审批、排队和真实发送混成一个状态。

它不是第二套发送系统。唯一平台写路径仍然是：

`WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor -> Adapter`

`GET /reply-outbox` 只把这条链上的记录投影为操作者视图。真实事实分别保存在 WorkOrder、Approval、ExecutionJob 和 MessageIntake 中，不另外复制一份可独立执行的发件箱队列。

## 2. 正确业务顺序

1. 导入或录入买家消息。
2. 系统生成回复候选，但不发送。
3. 创建 `send_message` 或 `sensitive_reply` Approval。
4. 操作者核对账号、买家、会话、商品、文本和回复版本。
5. 操作者批准或拒绝。
6. 已批准回复由操作者单独加入执行队列。
7. Worker 领取一个带 lease 和 fencing token 的任务。
8. Adapter 尝试发送并返回平台结果。
9. 有明确成功回执时，发件箱显示 `sent`。
10. 结果不确定时，显示 `verification_required`，操作者先到目标账号的聊天记录核验。
11. 只有确认平台未发送后，任务才会变成可显式重试的 `failed`。

批准不等于发送，入队也不等于发送。控制台中的“已发送”必须来自成功执行结果或带证据的人工确认。

## 3. 状态及动作

| 发件箱状态 | 来源 | 操作者动作 |
|---|---|---|
| `pending_approval` | Approval 为 `pending` | 审阅后批准或拒绝 |
| `rejected` | Approval 为 `rejected` | 结束；需要新文本时创建新修订 |
| `ready_to_enqueue` | Approval 已批准且没有执行任务 | 再次核对绑定后入队 |
| `queued` | ExecutionJob 为 `queued` | 等待执行，必要时可在执行前取消 |
| `running` | 有效租约持有者正在发送 | 等待，不创建第二条回复任务 |
| `verification_required` | 发送结果未知或租约过期 | 核对目标账号聊天记录，禁止直接重试 |
| `failed` | 已知失败，或人工确认未发送 | 修正原因后显式重试，受最大次数限制 |
| `cancelled` | 入队后、执行前取消 | 无平台写入；重新发送需新任务 |
| `sent` | 成功回执或人工确认已发送 | 复核一次且只出现一次，进入转化跟踪 |
| `completed_without_receipt` | 历史完成记录缺少有效成功结果 | 人工审计，不得据此声明已发送 |

## 4. 强绑定字段

每个新回复 Approval 保存：

- `cookie_id`：目标卖家账号；
- `buyer_id`：目标买家；
- `chat_id`：目标会话；
- `item_id`：关联商品；
- `order_id` / `dispute_id`：存在时绑定订单或争议；
- `reply_source_event_key`：来源入站事件；
- `reply_source_message_id`：平台来源消息；
- `reply_revision`：回复修订号；
- `reply_message_sha256`：完整批准文本哈希；
- `reply_action_digest`：账号、买家、会话、商品、来源、修订和文本的整体摘要；
- `reply_idempotency_key`：执行幂等键。

入站请求中的自由 `context` 不能覆盖账号、买家、会话、商品、入站文本、回复文本和幂等元数据。执行前会重新计算 action digest；绑定发生变化时发送失败，并要求重新创建 Approval。

## 5. 幂等规则

### 5.1 平台入站消息

同一个来源事件、回复修订、回复文本和动作生成确定性幂等键。重复导入同一消息：

- 不创建第二个 WorkOrder；
- 不创建第二个 Approval；
- 返回原 Reply Outbox 记录；
- 不发送任何消息。

重试生成回复时使用新的 `reply_revision`。新修订必须重新审批。

### 5.2 人工录入回复

调用方可以提供显式 `idempotency_key`。建议每次操作使用稳定的请求 ID，例如 `reply:operator:20260902-001`。

- 同一 key 和同一 action digest：返回原记录，`duplicate=true`；
- 同一 key 但买家、文本、账号、商品、来源或修订不同：返回 HTTP `409`；
- 未提供 key：系统生成随机 key，只能防止执行链内重复，不能识别客户端重放。

一个已经完成的幂等键不能被另一个 Approval 再次入队。

## 6. 操作者每日处理顺序

1. 打开控制台“消息入站”中的“回复发件箱”。
2. 先处理 `verification_required`，因为它阻断是否能安全重试。
3. 再处理 `pending_approval`，逐条核对账号、买家、商品和回复摘要。
4. 对已批准的 `ready_to_enqueue` 再做一次发送前核对，然后入队。
5. 观察 `queued` 和 `running`，不要为相同买家消息创建替代任务。
6. 检查 `failed` 的明确原因；只有确认没有平台写入才重试。
7. 对 `sent` 核对平台消息编号或页面证据，并把询盘结果纳入转化复盘。

当前控制台最多展示最近 100 条投影，并在页面内最多渲染 30 条匹配记录。长期经营前需把交易状态从 JSON 迁入事务数据库，并增加分页。

## 7. 未知发送结果

以下情况不能按普通失败处理：

- 按下 Enter 后浏览器或网络中断；
- worker 租约过期但没有持久化最终结果；
- adapter 明确返回 `outcome_uncertain` 或 `verification_required`；
- 执行期间出现无法判断平台是否接收的异常。

处理步骤：

1. 保持任务为 `verification_required`。
2. 打开绑定的卖家账号和目标买家聊天记录。
3. 核对相同文本、发送时间、关联商品以及是否只出现一次。
4. 已发送时记录平台消息 ID、页面 URL 或截图证据之一，选择 `confirmed_completed`。
5. 未发送时写明检查范围和时间，选择 `confirmed_not_executed`。
6. 只有第 5 步后，才允许操作者显式重试。

完整恢复协议见 `docs/ops/execution-recovery-runbook.md`。

## 8. API

查看全部回复：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8788/reply-outbox?limit=100'
```

按状态或账号筛选：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8788/reply-outbox?status=verification_required&cookie_id=SELLER_ID'
```

人工回复请求建议携带稳定幂等键：

```powershell
$body = @{
  message = '已核对库存，当前规格可以发货。'
  cookie_id = 'SELLER_ID'
  chat_id = 'CHAT_ID'
  item_id = 'ITEM_ID'
  idempotency_key = 'reply:operator:20260902-001'
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8788/workflows/messages/BUYER_ID/send' `
  -ContentType 'application/json' `
  -Body $body
```

Approval 决策、入队、执行和未知结果核验继续使用现有 `/approvals/*` 与 `/execution-jobs/*` API。

## 9. 回执与数据最小化

Reply Outbox 只展示回复摘要、哈希和绑定信息。平台回执只投影：

- adapter 名称；
- 外部消息 ID；
- 页面 URL；
- 是否为人工确认；
- 人工核验结论。

adapter 返回的完整消息正文不会复制到回执投影。不得在核验证据中保存 Cookie、Token、完整登录态或其他账号凭据。

## 10. 当前验收与边界

2026-09-02 本地专项测试 `8/8` 通过，覆盖：

- 入站幂等元数据可重放且绑定文本；
- `pending_approval -> ready_to_enqueue -> queued -> sent` 状态投影；
- 成功回执不暴露 adapter 的原始消息正文；
- 不确定发送进入 `verification_required` 且不能直接重试；
- 已完成幂等键不能由第二个 Approval 再入队；
- 人工请求同 key 同内容返回原记录，不同内容返回 HTTP `409`；
- 入站自由上下文不能覆盖真实消息绑定；
- 重复入站事件返回同一 Outbox 记录；
- API 和控制台合同存在。

尚未验证：

- 真实闲鱼账号中的消息 ID 和页面回执字段；
- 真实浏览器在按下 Enter 后断网的核验质量；
- 长期消息量下的分页、归档和数据库事务；
- 多主机发送 worker。

因此当前状态是“代码闭环已通过本地验收”，不是“真实闲鱼回复已验证”。首次真实消息仍需单账号、人工在场，并严格按本手册关闭结果。
