# 2026-09-21 第四次复验问题修复轮（Pi 链路 7 项：互斥/清理/预算/提交协议/影子语义/日志一致/证据固化）

输入：操作者第四次复验报告（"新增链路尚不能验收通过"，6 P1 + 1 P2，均独立复现）。
流程：先写 14 条稳定复现的红灯测试（`tests/test_pi_reliability.py`，Mock 层）→ 修复 → 3 连跑全绿。
执行者：Zcode。起点 `85234c8` → 终点 `65e24da`。全量隔离回归：**1346/1346 通过**（1332 + 14）；静态检查全过；data/live 未变；无真实模型调用、无新功能面。

## 逐项对照

| # | 操作者复现 | 修复 | 回归测试 |
|---|---|---|---|
| P1-1 单 worker 未串行 | `run_once` 两个调用同时进入模型执行且都成功 | `_execute_lock` 非阻塞获取：第二次并发执行直接返回 `{"outcome":"busy"}`；**停止与认领在 boundary 锁内原子判定**（停止后绝不认领，即使与认领竞争）；HTTP 端点经线程池也不会并发执行 | `test_second_run_once_returns_busy_while_first_executes`（首执行停在 prompt 中，第二调用 busy）；`test_stop_wins_over_pending_claim`（执行中 stop → 后续 run 返回 None） |
| P1-2 客户端不关闭 | 成功/失败路径均无 `stop()`，RPC 子进程泄漏 | prompt 包裹 `finally`：`client.stop()` 存在即调用（清理异常不掩盖结果）；失败路径同样覆盖 | `test_client_stopped_on_success` / `test_client_stopped_on_failure` |
| P1-3 截止/预算未完整执行 | 截止后结果仍标记成功；token 无预算控制 | **截止**：开始前剩余 < min_execution_seconds(5s) 拒绝起跑；**结果侧拒收**——返回时刻（可注入 `now_post`）超过 deadline → 非重试失败 `rejected_deadline_result`。**预算**：新增 `ModelUsageLedger`（model_usage.json 持久按日累计）；per-job token 上限超支 → 非重试失败 `budget_exceeded`；每日 token 上限（默认 200k，env `PI_DAILY_TOKEN_BUDGET`）耗尽 → 每次尝试在**任何模型调用之前**被拒绝（`budget_exhausted_day`，重排队直到 deadline 兜底，零模型消耗） | `test_result_after_deadline_rejected`；`test_per_job_token_budget_rejects_overspend`（1000>100 拒收且无决策）；`test_daily_token_budget_blocks_next_job`（15 tokens 后 10 上限阻塞第二任务，不产生决策） |
| P1-4 取消与决策写入竞争 | 心跳校验后取消 → 决策仍落库 → 面板读它 | **提交协议**：决策写入 → fenced complete；complete 抛 FencingError（租约已失/已取消）→ 追加 `decision_invalidated` 失效标记（append-only），迟到产物永不作为有效决策展示；证据端点失效集取**全域决策**计算（标记可来自任何 actor），valid 列表排除之；`_decision_for_attempt` 复用检查同样跳过已失效记录 | `test_cancel_after_heartbeat_invalidates_late_decision`（心跳后立刻取消 → discarded_stale + 失效标记 + 有效决策为 0）；`test_evidence_endpoint_hides_invalidated_decisions`（端点 decision_ids 不含失效 ID，invalidated_decision_ids 单列） |
| P1-5 影子未传额度/次数 | 每日一次的授权对三条草稿全记允许 | `_usage_projection`：**真实占用**（预算存储 mandate_daily 桶当日 committed）+ **影子累计**（shadow_usage.json 只累计"影子允许"的动作，反事实、不占真金）；额度与次数一起传入策略引擎 | `test_max_actions_one_allows_only_first_of_three`（3 草稿恰 1 允许）；`test_daily_amount_shadow_accumulation_denies_second`（60 允许、60+70>100 第二条拒） |
| P1-6 影子日志写失败丢样本 | 游标先写 → 日志失败 → 重试跳过，日志零条 | **日志先行且按事件 ID 幂等**：`ShadowAuthorizationLog.record` 增 `event_id` 去重；`_record` 先写日志（失败即中止 tick，**游标未推进**）→ 成功后才 mark cursor；两个方向的崩溃窗口都靠事件 ID 幂等收敛 | `test_log_write_failure_does_not_advance_cursor`（首写盘失败 → tick 异常 → 重试补录 1 条 → 三次 tick 无重复） |
| P2-7 证据不可回放 + committed 过滤 | 决策只存泛化查询引用；job_inputs 空；采集器不查批次状态 | **证据文件**：`data/commerce/pi_evidence/{job_id}__attempt{n}.json` 持久化**实际输入快照 + 工具事件 + usage + 模型消息**；决策 evidence_refs 增 `pi_evidence_file`，input_versions 增 `inputs_sha256` 与 `evidence_file`；证据端点回放 inputs/tool_calls/usage 并校验哈希。**committed 过滤**：采集器按 `import_batch:` → `batch_status()=="committed"` 过滤，撤销/失败批次残留进 `excluded_non_committed` 单列，不计入有效报价数 | `test_decision_persists_inputs_and_tool_events`（文件存在、inputs/tool_calls 可回放、sha256 在案）；`test_quote_inputs_filter_committed_batches`（撤销批次 → committed=0、excluded=1） |

## 验证

- 红灯：14 条测试按操作者复现路径编写，修复前大面积红（含导入级 API 缺口）。
- 绿灯：`test_pi_reliability` **3 连跑全绿**；关联套件（pi_jobs/pi_stage2/operating_mandates）75/75。
- 全量隔离回归 **1346/1346**（81.2s）；`node --check`×2、`git diff --check` 通过；data/live 哈希未变。
- 预算语义澄清：`budget_exhausted_day` 为可重试失败——每次尝试在模型调用前被拒、零消耗，由 deadline 收尾；这是程序执行约束而非延迟放行。

## 范围与下一步

- 本轮零真实模型调用（全部 Mock 层）；真实层证据维持此前 3 次留档。
- 按操作者指示：修完本轮后，**用非空、明确标注来源的报价样本做真实 Pi 验证**（等操作者提供样本或批准用演示样本标注来源后执行）。
- 自动回复、发布、采购、退款仍未开放。
