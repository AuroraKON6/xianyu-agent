# 外部执行恢复与未知结果核验 Runbook

Updated: 2026-09-04

## 1. 目的

本 Runbook 处理已经通过人工审批、并进入 `ExecutionJobQueue` 的平台写动作。
它解决的不是“怎样自动重试”，而是下面这个现实问题：

> 浏览器、网络或 worker 在点击平台写按钮后中断时，系统无法仅凭超时判断动作究竟成功还是失败。

如果把未知结果当失败直接重试，可能造成重复发布、重复回复、重复退款或重复发货。因此系统采用：

`lease + fencing token + verification_required + 人工平台核验`

本流程适用于：

- 发布商品 `publish_listing`
- 修改商品 `edit_listing`
- 发送普通或敏感回复 `send_message` / `sensitive_reply`
- 删除商品 `delete_item`
- 退款 `refund_order`
- 发货 `fulfill_order`

## 2. 状态定义

| 状态 | 含义 | 允许的下一步 |
|---|---|---|
| `queued` | 已批准，尚未被 worker 领取 | 执行或取消 |
| `running` | 一个有效租约持有者正在执行 | 等待结果；不得并行执行 |
| `completed` | 已得到成功结果，或人工核验为已执行 | 复核回执，进入后续业务步骤 |
| `failed` | 已知未成功，或人工核验为未执行 | 检查原因后显式重试；受最大次数限制 |
| `verification_required` | 平台结果未知 | 先去闲鱼核验；禁止直接重试或取消 |
| `cancelled` | 排队任务在执行前被取消 | 无平台写入 |

## 3. 系统执行协议

1. Worker 领取任务时持久化 `lease_owner`、`lease_token`、`lease_expires_at` 和递增的 `fencing_token`。
2. 同一队列文件使用进程内路径锁、操作系统文件锁和原子文件替换，避免两个本机进程同时领取同一任务。
3. 第二个 worker 看到未过期的 `running` 任务时不会执行它。
4. 租约过期时，任务不会回到 `queued`，而会进入 `verification_required`。
5. 旧 worker 恢复后，只有租约和 fencing token 仍匹配时才能保存结果。
6. 操作者关闭核验时，fencing token 再递增，使旧 worker 的迟到结果失效。
7. `verification_required` 被视为活动任务，同一 Approval 不能再创建第二个执行任务。
8. 核验关闭前重新校验 Approval 的 action scope；只允许忽略有效期，不允许忽略账号、目标、参数或版本变化。

当前保证范围是同一台本机、同一普通本地磁盘上的 JSON 队列。不要把队列文件放在网络共享盘，也不要在多台主机上共同消费。需要多主机时先迁移到具有事务和行锁的数据库队列。

## 4. 出现待核验任务时

### 4.1 立即动作

1. 不点击“重试”。
2. 不新建相同发布、回复、退款、删除或发货工单。
3. 打开任务绑定的账号，而不是其他闲鱼账号。
4. 核对目标商品、买家、订单和动作参数。
5. 在闲鱼实际页面确认动作是否发生。
6. 保存平台编号、页面 URL 或截图中的至少一种证据。
7. 在控制台执行“确认已执行”或“确认未执行”。

### 4.2 按动作核验

| 动作 | 去哪里核验 | 判断已执行的最低证据 | 判断未执行的最低证据 |
|---|---|---|---|
| 发布商品 | 我的发布/在售商品 | 新商品 ID，且标题、价格和账号匹配当前草稿 | 在目标账号中按标题和时间搜索后不存在，并记录检查时间 |
| 修改商品 | 商品详情和商品同步 | 目标字段等于批准后的值；记录商品 ID 和页面 | 目标字段仍为批准前的值；记录商品 ID 和页面 |
| 发送回复 | 与目标买家的聊天记录 | 相同内容只出现一次，买家和商品匹配 | 聊天记录中不存在该内容；记录最后消息时间 |
| 删除商品 | 在售/已下架/已删除列表 | 商品不再可售，且目标商品 ID 匹配 | 商品仍在售，且状态和修改时间未改变 |
| 退款 | 订单退款详情 | 退款状态、金额、订单 ID 与审批一致 | 订单无退款记录，资金状态未变化 |
| 发货 | 订单物流详情 | 承运商、运单号、订单 ID 与审批一致 | 订单仍未发货且没有运单号 |

模糊页面、账号不匹配、搜索不完整或证据无法绑定目标对象时，继续保持 `verification_required`，不要猜测。

## 5. 控制台操作

执行任务卡片进入 `verification_required` 后会显示两个命令：

- `确认已执行`：需要核验说明，并至少填写平台编号、页面 URL 或截图路径之一。
- `确认未执行`：需要核验说明。任务会变为 `failed`，此后才显示显式重试入口。

“确认未执行”不会自动重试。操作者仍需重新检查当前账号、对象、金额、文本和审批版本，再单独点击重试。

## 6. API 操作

确认平台已执行：

```powershell
$body = @{
  resolution = 'confirmed_completed'
  note = '已在目标卖家账号的在售商品中核对'
  external_id = '平台返回的对象编号'
  page_url = ''
  screenshot = ''
  checked_by = 'operator'
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8788/execution-jobs/JOB_ID/verification' `
  -ContentType 'application/json' `
  -Body $body
```

确认平台未执行：

```powershell
$body = @{
  resolution = 'confirmed_not_executed'
  note = '已检查目标账号和目标对象，平台没有该动作记录'
  checked_by = 'operator'
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:8788/execution-jobs/JOB_ID/verification' `
  -ContentType 'application/json' `
  -Body $body
```

## 7. 审计字段

每个执行任务保留：

- `idempotency_key`
- 批准时的 `scope_envelope`、`action_digest` 和 `correlation_id`
- `attempts` / `max_attempts`
- `fencing_token`
- 当前租约字段
- 原始 handler 结果和错误
- browser/adapter 证据摘要
- 自动失败证据 ID、manifest、截图哈希、失败阶段和 `write_attempted`
- `verification.required_at`
- `verification.reason`
- `verification.resolved_at`
- `verification.resolution`
- `verification.note`
- `verification.evidence`

证据不得包含 Cookie、Token、完整登录态或其他账号凭据。

BrowserAdapter 会对发布、修改、回复、删除和发货失败进行尽力截图，并保存脱敏 JSON 清单。目录、字段、完整性检查和截图自身失败的处理方法见 `docs/ops/browser-write-evidence-runbook.md`。自动截图只是失败现场，不是平台成功或未执行的最终证明。

## 8. 验收与当前边界

2026-08-31 已通过本地自动化验收：

- 两个队列实例同时运行时，同一任务只执行一次。
- 租约过期后任务进入 `verification_required`，不会再次调用 handler。
- 不确定 handler 结果进入核验状态。
- 未核验前不能重试。
- 人工确认已执行会完成 Approval，并 fence 掉旧 worker。
- 人工确认未执行后才允许一次显式重试。
- 浏览器不可用、截图失败和提交后断连的故障注入均保留脱敏证据；嵌套证据可投影到 ExecutionJob、Reply Outbox 和 OPC Dashboard。
- 作用域被修改时，核验关闭返回 HTTP `409`，ExecutionJob 继续保持 `verification_required`，不会先落成完成状态。
- 2026-09-04 完整隔离套件 `983/983` 通过，测试进程耗时 `24.128s`；`data/live` 的 15 个文件 SHA-256 变化数为 `0`。

尚未验证：

- 真实闲鱼发布、回复、退款、删除和发货的未知结果恢复。
- 浏览器进程被强制终止后的真实页面证据质量。
- 多主机或网络共享文件系统上的并发消费。

首次真实平台写入仍必须保持单账号、单商品、人工在场，并把结果记录为真实回执或 `verification_required`，不能用自动化测试替代。

消息发送还需同时遵守 `docs/ops/reply-outbox-runbook.md` 中的账号、买家、会话、商品、修订和幂等绑定规则。

所有外部动作还需遵守 `docs/ops/action-scope-approval-runbook.md`。作用域摘要不是电子签名，也不能替代真实平台核验。
