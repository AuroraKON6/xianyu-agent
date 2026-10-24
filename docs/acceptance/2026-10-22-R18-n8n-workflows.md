# R18 实施验收报告：n8n 首条离线候选链

日期：2026-10-22。基线为 R17 提交 `3639606`。

**结论：R18 软件/集成层 PASS（离线范围：结构白名单 + 断点续跑语义对真实 WorkflowRunService 验证）。全量离线 1766/1766 OK（13 skipped=PG），live 数据 247 文件不变。n8n 实例未安装（真实引擎验收随 S3 后续部署批次），本批交付可导入的两条工作流定义 + 驱动它们的服务端语义已全部实测。**

## R18

```yaml
task_id: R18
owned_files: [workflows/n8n/W1-source-intake.json, workflows/n8n/W2-candidate-review.json, workflows/n8n/VERSIONS.md, tests/test_r18_n8n_workflows.py]
software_status: PASS   # 10/10
integration_status: PASS  # 全量 1766/1766 OK, 数据 247/247 不变（离线语义层）
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [n8n 引擎内执行未跑（未安装）；语义验收对真实 R17 服务执行，与节点行为一一对应；owner 仍 python，切换按 VERSIONS.md 流程]
rollback: 关闭两个 workflow 及触发器（当前仅 manualTrigger 可触发），owner 回 manual/python；已 finalize 的 run 事实不撤销
next_task: R19
```

## 交付

**W1-source-intake.json / W2-candidate-review.json**（n8n 可导入格式）：节点只用 manualTrigger/httpRequest/wait/if/function；全部 HTTP 指向 `$env.XIANYU_API_BASE`（R17 API）或错误队列 webhook；幂等键 `source-intake:{account}:{input_digest}:v{N}` 与 `candidate-review:...`；W2 内置断点续跑函数（跳过终态能力只请求下一个待办）；轮询带 Wait 退避上限、无节点内无限重试；409 语义按合同分支（幂等冲突终止、live_step_exists 进轮询、finalize 未就绪继续等）；负向研究=合法成功 finalize；credentials 零导出（base URL/错误钩子走环境变量，token 走 n8n credential store 部署时注入）。

**VERSIONS.md**：两链版本记录 + 节点白名单 + **owner 过渡规则**（whole-run python → stepwise n8n 的逐链切换顺序：dry-run → 停 python owner（R05 claim 屏障防双推进）→ 切 `XIANYU_CHAIN_OWNER_*` → 执行历史与 run 事件一一对应核对）+ 断点续跑四条语义。

## 验收（tests/test_r18_n8n_workflows.py，10/10）

| 任务书验收项 | 测试 |
|---|---|
| 结构约束（仅 HTTP/等待/分支/错误队列） | 节点类型白名单两链全过；HTTP 目标白名单（仅后端 API/错误钩子）；credentials 零内嵌；**无 webhook/schedule 自动触发**（R18 只有 manualTrigger） |
| 重复触发不重复 | 同 intent 重触发→同一 run（created=false）；活步重请求→409 live_step_exists；终态步骤结果不可重写（409 step_terminal） |
| 杀 n8n/worker 后续跑不重复 | 两步完成后模拟中断→续跑只请求未终态能力（W2 函数节点逻辑逐行验证）→finalize 后每能力恰好 1 个步骤（per_capability 计数断言） |
| 部分失败 | failed 是终态：finalize 保持 409 required_steps_incomplete；旧失败步骤结果不可改写；等待人工 reconcile/retry 而非自动重发 |
| 工作流 history ↔ 后台 run 对应 | orchestration_trace.execution_id 落进 run；事件序列 run_created→run_running→step_requested→step_succeeded 全程可读 |
