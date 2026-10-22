# R14 实施验收报告：账本、回款与冲正

日期：2026-10-22。基线为 R13 提交 `1e13cdb`。

**结论：R14 软件/集成层 PASS。全量离线 1736/1736 OK（13 skipped=PG），live 数据 247 文件不变。**

## R14

```yaml
task_id: R14
owned_files: [xianyu_agent/ledger_service.py, xianyu_agent/application/reconciliation.py, tests/test_r14_reconciliation.py]
software_status: PASS   # 11/11 新验收 + 存量 procurement_ledger 21 + g4b 3 不破
integration_status: PASS  # 全量 1736/1736 OK, 数据 247/247 不变
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [JSON 单进程内锁；真并发约束在 R10 PG UNIQUE(source_event_id, entry_type) 已就绪，R15 切换]
rollback: 冻结新财务分录；修复采用冲正/补录，历史已记账数值不可编辑（append-only + reverse 机制）
next_task: R15
```

## 验收矩阵（任务书 → 实现 → 测试）

| 任务书验收项 | 实现 | 测试 |
|---|---|---|
| 重复 receipt 幂等 | 凭证幂等按**分录行**（evidence+category）：同凭证同类别同金额重放返回原行；一张付款允许多条费用线（货款+运费）——日记账 lines 语义 | `test_duplicate_receipt_is_idempotent` + 存量 test_procurement_ledger 双线场景 |
| 同凭证不同金额冲突拒绝 | `_assert_same_fact`：同 (凭证,类别) 重放金额或币种不同 → `LedgerError("a fact cannot have two amounts")`，不静默返回旧账不重复入账；income 同理（不再静默） | `test_same_evidence_different_amount_is_conflict` / `test_income_replay_conflict_rejected_not_silent` |
| 退款不双扣 | 退款走 adjustment（凭证幂等：同退款凭证重放=一行）或 reversal（金额封顶原额，超冲正拒绝）——两条路径都只计一次 | `test_refund_adjustment_evidence_idempotent` / `test_reversal_chain_capped_no_double_count` |
| 币种不混算 | `profit_summary` 输出 `net_by_currency`（每层每币种独立净额，跨币种永不相加，多币种时显式注记） | `test_profit_summary_groups_by_currency` |
| 总账平衡 | 新 `application/reconciliation.ledger_balance`：按层按币种 credits−debits（冲正按自身方向计入） | `test_ledger_balance_per_currency` |
| 预留转实付不双占现金 | （存量已建，本轮视图化+验收）settle 替换 reserve：reserved 0 + settled_actual 140 = committed 140（非 100+140） | `test_settle_replaces_reservation_no_double_commit` |
| 未退回款项不恢复可用余额 | settle 后 release 被拒（BudgetError）；released 停止计数 | `test_settled_reservation_cannot_be_released` / `test_released_reservation_stops_counting` |
| 账与实收一致 / 未售库存 | `order_reconciliation` 视图：每订单分列收货收据（provisional 成本）、已核验回款（settled）、退款/冲正，**收据无已核验付款显式列入待对账**；未售库存成本标注为资金占用事实不计入已售订单利润 | `test_receipt_without_payment_stays_visibly_pending` |

"receipt 不由前缀决定可信"在 R11 已实现（`verified=False` 恒 provisional），本轮验收确认。

## 设计说明：凭证幂等为什么按 (evidence, category) 行而非整凭证

一张真实付款（`pay://wx/001`）合法地同时支付货款 150 与运费 15——这是**一张付款凭证两条分录行**，不是冲突。冲突的是：同一行（同凭证同类别）重放时金额变了。幂等键=（凭证,类别），行内金额/币种不一致才拒绝。与 R10 PG 端 `UNIQUE(source_event_id, entry_type)` 合同同构（category≈entry_type 粒度）。

## 文件清单

修改（1）：xianyu_agent/ledger_service.py
新增（2）：xianyu_agent/application/reconciliation.py、tests/test_r14_reconciliation.py
