# 2026-09-21 Pi 链路第五轮修复（R5：记账/有效性/影子幂等 三项）

输入：操作者 R5 指令（`0d0cce2` 未通过复验；三项缺口；先失败测试再修复；不调用真实模型）。
流程：8 条新失败测试（`tests/test_pi_reliability.py` Round5* 类）→ 修复 → 3 连跑全绿。
执行者：Zcode。起点 `0d0cce2` → 终点 `92b644a`。全量隔离回归：**1354/1354 通过**（1346 + 8）；静态检查全过；data/live 未变；零真实模型调用。

## 红灯证据（修复前实跑：22 测 7 红）

| # | 红测试 | 症状 |
|---|---|---|
| 1 | `test_overspend_rejection_still_records_usage` / `test_second_rejected_attempt_accumulates` / `test_invalid_output_records_usage` | 拒收/无效输出路径日账本仍为 0 |
| 1 | `test_prompt_failure_records_unknown_not_zero`（ERROR：`entries_today` 不存在） | 失败路径无记账 API |
| 2 | `test_cancelled_job_dangling_decision_hidden_after_marker_write_failure`（ERROR） | 无 job 状态派生有效性 |
| 2 | `test_succeeded_job_only_validates_result_ref_decision`（ERROR） | result_ref 不参与判定 |
| 3 | `test_cursor_write_failure_retry_does_not_double_accrue` | 游标写失败重试后同一事件累计两次 |

（`test_record_replay_reuses_saved_verdict` 修复前因日志幂等碰巧通过，保留为守卫。）

## 修复

| # | 修复 |
|---|---|
| 1 记账 | **按调用尝试记账**：prompt 异常路径记 `total_tokens=None` 条目（未知≠零，`used_today` 不计但 `entries_today` 可见）；截止后拒收、per-job 超支拒收、无效输出、失租丢弃四条路径都先记实耗再失败；**成功路径记账前移到 complete 之前**（崩溃窗口不丢账）。新增 `ModelUsageLedger.entries_today`。 |
| 2 有效性 | 新增 `pi_jobs.decision_validity(job, all_decisions)`：有效 ⟺ 非失效标记目标 ∧ 非失效标记 ∧ **job.status==succeeded** ∧ **job.result_ref==decision:{id}** ∧ **attempt 匹配**。失效标记降级为附加信号（写失败不再能复活迟到决策）；证据端点复用该函数并输出 `stale_decision_ids`；`_invalidate_decision` 改为尽力而为（写失败不掩盖 discard 结果）。测试以"重启后新 store 实例"直接调用该函数验证。 |
| 3 影子幂等 | `_record` **先查 `find_by_event`：已存裁决直接复用，不重评估、不再累计**；`_record_shadow_allowance` 按事件 ID 去重后才追加；`ShadowAuthorizationLog.find_by_event` 新增。游标写失败 → 重试复用已存"允许"裁决且额度只记一次，后续事件按单次累计判定。 |

## 验证

- Round5 测试 3 连跑全绿（22 条含此前 14 条）。
- 全量隔离回归 **1354/1354**（61.6s）；`node --check`×2、`git diff --check`；data/live 哈希未变。
- 无真实模型调用；新提交号：**`92b644a`**。
