# n8n 工作流版本说明（R18）

## W1-source-intake（v1，2026-10-22）

来源接入链：人工触发 → 幂等建 run（`source-intake:{account}:{input_digest}:v{N}`）→ `validate_evidence` → 轮询 → 服务端 finalize → 事件摘要。

## W2-candidate-review（v1，2026-10-22）

候选评审链（合同 11 §4 示例落地）：人工触发 → 幂等建 run（`candidate-review:{account}:{input_digest}:v{N}`）→ 断点续跑循环（跳过已终态步骤，只请求下一个待办能力）→ 全部终态 → 服务端 finalize → 取 manifest 首产物 → 事件摘要。负向研究结论（零候选/无价差）是合法的成功 finalize。

## 节点白名单（两链通用）

`manualTrigger`、`httpRequest`、`wait`、`if`、`function`（仅做本地断点判断，不外呼）。**没有**代码节点直连数据库、没有凭证节点、没有自动重试无限循环——重试语义全部由后端幂等键 + `live_step_exists` 409 承载。

## Owner 过渡规则（whole-run → stepwise）

| 链 | 当前 owner | 过渡条件 |
|---|---|---|
| github-discovery / product-discovery / agent-scheduler / message-import | python（R05 环境变量 `XIANYU_CHAIN_OWNER_*`） | 对应链的 W1/W2 在 n8n 中通过 R17 API 断点续跑验收后，逐链切 `XIANYU_CHAIN_OWNER_<CHAIN>=n8n`；同一时刻一条链只有一个 owner |
| radar | disabled | R18 之后按需评估 |

切换顺序固定：先在 n8n 侧导入并 dry-run 该链 → 停 python 侧 owner（R05 claim 屏障保证无双推进）→ 切环境变量 → 观察 n8n 执行历史与后端 `workflow_runs` 事件一一对应。回退按任务书：关闭两个 workflow 及触发器，owner 切回 manual/python，已 finalize 的 run 事实不撤销。

## 断点续跑语义（验收依据）

1. **重复触发**：同一 `business_intent_id` 重复 POST `/workflow-runs` 返回同一 run（`created=false`），不产生第二个 run。
2. **中断恢复**：n8n 执行被杀后重新执行，已完成步骤是终态（succeeded/failed/skipped），W2 的 `Next Pending Capability` 跳过它们；服务端 `live_step_exists` 409 兜底防双活步。
3. **部分失败**：某能力 failed 是终态——不自动无限重试；进入错误队列由人工决定 reconcile/retry/放弃。
4. **结果不重复**：产物（artifacts）由服务端在 `complete_step` 时写入 run，客户端只读；finalize 生成唯一 manifest。

## Credentials

不在 JSON 中导出任何凭证。`XIANYU_API_BASE`（后端 API 基址）与 `XIANYU_ERROR_WEBHOOK`（错误队列）走 n8n 环境变量；后端 API 认证走 R03 token（`workflow:trigger` scope），token 由部署环境注入 n8n credential store，不入库。
