# 2026-09-18 可靠性修复轮：验收问题 1-8 逐项修复

输入：操作者验收报告（2026-09-18，结论"暂不通过，先修复再扩功能"，8 项问题均由操作者用临时数据复现）。
执行者：Zcode。起点 `13b0c88` → 终点 5 个修复提交（859dc17 / 8f3aca1 / 54cc4d1 / 7bf4cd6 / 46b08b6）。
全量隔离回归：**1283/1283 通过**（1269 + 新增 14 条回归）；`node --check`×2、`git diff --check` 通过；data/live 全程未变；未新增任何功能。

## 逐项对照

| # | 操作者复现 | 修复 | 回归测试 |
|---|---|---|---|
| P1-1 存储失败后锁不释放、内存/磁盘不一致 | `repositories.py` `_LockedJsonList` | `__enter__` 建内存快照；body 异常或 `_persist` 失败时回滚内存到快照（磁盘保持旧值），`finally` 中必然释放锁；失败写入的幽灵记录不可能经后续保存落盘 | `test_persist_failure_rolls_back_memory_and_releases_lock`、`test_body_exception_rolls_back_partial_mutation`、`test_lock_released_after_persist_failure_under_threads`（3 线程失败不阻塞） |
| P1-2 同一销售订单重复生成采购单且不占库存 | `procurement.py` | 同一 `sales_order_ref` 存在非取消采购单时拒绝（报已存在单号）；创建时以 `sales:{订单号}` 为键**原子锁定库存**（先锁后存，存储失败自动释放）；取消时归还锁定量 | `test_duplicate_sales_order_rejected_and_stock_reserved`（8 件×2 拒绝、可用 10→2、取消后回 10、取消后可重建） |
| P1-3 撤销报价仍能生成采购单 | `supplier_import.py` + `procurement.py` | 新增 `batch_status()` 公开查询；采购候选快照按 `import_batch:` 来源过滤，**仅 committed 批次可用**（revoked/failed 隔离；未接批次权威时拒绝而非猜测） | `test_revoked_batch_quote_cannot_procure`、`test_committed_batch_quote_still_procures` |
| P1-4 人工纠正匹配不影响商机；负利润仍 ready_for_test | `opportunity_service.py` | 评估一律取该快照对**最新匹配记录**（人工纠正即生效，证据引用同换）；复核任一门禁失败且当前为 ready_for_test 时**自动转 paused** 并记录失效原因 | `test_human_correction_to_not_match_pauses_opportunity`（EXACT→纠正 NOT_MATCH→paused）、`test_negative_conservative_profit_pauses_ready_opportunity`（涨价→保守利润转负→paused） |
| P1-5 预算按预留额而非实付累计 | `budget_reservations.py` | 日占用对 settled 记录取**实际结算额**（reserve 100/settle 140 → 占 140）；`usage()` 投影同步 | `test_settled_actual_over_reservation_consumes_actual`（140+50>150 拒绝、140+10 放行） |
| P1-6 库存过期仍可锁单 | `inventory.py` | `allocation()`/`lock_for_order()` 在读取/锁定时按 `supplier_observed_at` **重新校验时效**（过期→可售 0 + 拒绝新锁，附原因）；采购流程支持时钟注入并传递 | `test_expired_stock_blocks_new_locks_and_allocation_reads_zero`（+30h 锁拒绝/采购拒绝/新快照恢复） |
| P1-7 重复全额冲正制造虚假利润 | `ledger_service.py` | 冲正累计上限=原分录金额（二次全额/超额部分拒绝）；显式金额必须与原分录**同币种**；未知金额不可冲正 | `test_duplicate_full_reversal_rejected`（净额保持 0）、`test_partial_reversals_capped_at_original`（60+60 拒、60+40 净 0）、`test_reversal_currency_mismatch_rejected` |
| P2-8 润色改变对外报价（200→180 因内部底价入白名单） | `reply_drafts.py` | 允许金额集合**仅取确定性草稿正文**已出现的数字；删除底价/出价白名单注入——润色只能换措辞，不能换价 | `test_polish_cannot_replace_offer_with_the_internal_floor`（200 草稿润成 180 一律拒绝） |

## 操作者指出的范围性说明（未动代码，如实记录）

- WP08 真实模型脚本走 `DeepSeekClient` 直连而非 Pi 子代理执行链路——属实；Pi 运行器整合按你指定的顺序放在可靠性验收通过之后，本轮未动。
- 全绿≠工业级：本轮 14 条新回归全部来自你的复现路径；后续仍以边界测试为准，不以测试数量验收。

## 验证

- 全量隔离回归 **1283/1283**（53.963s，退出码 0）；静态检查全过；data/live 15+2 文件哈希与上轮一致。
- 每项修复独立提交可回滚；无新依赖；无平台动作；无 schema 变更。

## 按你的下一步建议待办（未开始，等验收）

1. 重启/并发/失败重试的组合演练（本轮只做了单点回归；组合场景测试在下一轮）。
2. 完整业务流程端到端再验证（含修复后的 撤销→采购、纠正→资格失效 联动）。
3. 通过后再进入 Pi 持久任务执行链路与只读影子运行。
