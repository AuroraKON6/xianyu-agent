# Chat 事件事实流 Runbook

更新：2026-09-06。适用模块：`xianyu_agent/chat_events.py`、`/chat-events*` 路由。

## 定位

Chat 事件流是**追加式、哈希链、只写一次**的消息事实表，位于 intake ledger 旁边，不替换 envelope 去重契约，也不替代 Reply Outbox 的发送状态机。

- 入站：`POST /workflows/messages/inbound`、`/message-intake/envelope` 在创建或去重 intake 记录时同步追加一条 `inbound` 事件，并绑定 `intake_id`。
- 出站：`POST /execution-jobs/run-next`、`/execution-jobs/run-pending` 在 `send_message` / `sensitive_reply` 任务到达 `completed` 或 `verification_required` 终态时追加一条 `outbound` 事件，绑定 `execution_job_id`、回复提案幂等键和来源消息。
- 一条消息一个事实：入站按 envelope id 幂等；出站按执行 job id 幂等。同一 envelope/job 以不同内容或结果重复追加会硬失败。

## 操作者命令

```powershell
# 读取（按账号/会话/商品过滤，sequence 倒序分页：before_sequence + limit）
Invoke-RestMethod 'http://127.0.0.1:8788/chat-events?cookie_id=<acc>&chat_id=<chat>&limit=50'

# 计数与链完整性
Invoke-RestMethod 'http://127.0.0.1:8788/chat-events/metrics'
```

`chain.ok=false` 表示存储被改坏：停止使用该读模型，按 `execution-recovery-runbook` 的证据流程处置，不得手改 `data/chat_events.json`。

## 边界

1. 事件是事实记录，不是成功回执；`send_outcome=verification_required` 仍须按恢复 runbook 人工核验平台。
2. 已确认发送失败（job `failed`）不产生出站事件；失败事实在 Reply Outbox 投影中查看。
3. 事件不可编辑、不可删除；修正只能追加新事件。
4. 重放（read-model 重建）必须先通过链校验：`replay(from_sequence)` 在校验失败时直接抛错。
