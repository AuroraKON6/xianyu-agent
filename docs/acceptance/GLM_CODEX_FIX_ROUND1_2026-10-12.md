# GLM 修复报告：Codex 独立检验发现修复（全部完成）

日期：2026-10-12 起，2026-10-13 完成。全量隔离回归 **1592/1592，exit 0**；
对抗脚本 **53/53 断言通过**（51 修复 + 2 绿灯守卫保持）；live 数据无污染（git data/ 零变更）。

## 修复进度：51/51 ✅

| 层 | 修复数/总数 | 状态 |
|---|---|---|
| C 核心 | **6/6** | 全部完成 |
| D 雷达 | **16/16** | 全部完成（含 D7 组合根接线） |
| E 财务 | **17/17** | 全部完成（含 legacy_purchase + recovery_orphan） |
| S 存储 | **14/14** | 全部完成 |

对抗证据（2026-10-13，均为 OK / 0 failures）：

- `research/codex-verification-2026-10-12/adversarial_repros.py --case all` → Ran 6, OK
- `research/codex-verification-2026-10-12/radar_repros.py --case all` → Ran 16, OK
- `research/codex-verification-2026-10-12/finance_repros.py --case all` → Ran 17, OK
- `research/codex-verification-2026-10-12/storage_repros.py --case all` → Ran 14, OK

## 本轮（第 3 批）新增修复（14 项）

### S 层 11 项

| Case | 修复 |
|---|---|
| S audit_budget_authority | 审计文件清单补 `budget_reservations.json`（并补 `radar_reminders.json`） |
| S audit_execution_authority | 审计读数据根 `execution_jobs.json`，报告 `unknown_execution_jobs`（UNKNOWN 事实不再消失于 agent_jobs 之后） |
| S export_budget_authority | 导出表清单补 `budget_reservations`（DDL+INSERT+footer 计数） |
| S export_execution_authority | 顶层 `execution_jobs.json` 以独立表迁移（job_id 主键，专用 DDL，UNKNOWN 状态原样保留） |
| S audit_primary_key_collision | 全部 commerce 账本 record_id 碰撞 + execution job_id 碰撞 → finding → exit 2 |
| S backup_budget_integrity | 备份清单补 `commerce/budget_reservations.json`，恢复校验覆盖 |
| S backup_execution_integrity | 备份清单补顶层 `execution_jobs.json`，恢复校验覆盖 |
| S export_radar_unique_constraint | DDL 补 `CREATE UNIQUE INDEX radar_events_idem …(payload->>'idempotency_key')`（WHERE 非空） |
| S export_adoption_unique_constraint | DDL 补 `opportunity_adoptions_idem` 唯一索引（同上口径） |
| S export_reversal_foreign_key | ledger DDL 增列 `reversal_of TEXT REFERENCES ledger(record_id)`，INSERT 显式携带，悬挂冲正在载入时被 FK 拒绝 |
| S export_mixed_currency_footer | `ledger_cny_net` 只累计 CNY；非 CNY 币种列入 `ledger_non_cny_currencies` 显式披露（无换算绝不折算） |

### D7（fetch 配额组合根接线）

- `source_quotas.py`：新增 `is_managed(source)`（内存合同或持久化行任一存在即受管）与 `_contract_limit` 回退（进程重启后已记录的合同限额继续生效）。
- `market_channel.py`：`ChannelFetchSession` 未显式传入 ledger 时默认从 `repos.data_dir` 接持久化账本；**会话首个请求视为调用方已预充值**，此后每次实际尝试（重试、翻页）先 `check_and_consume`；未受管来源不在此计费（后台未值守循环由 dispatcher 拒绝）。
- `commerce/repositories.py`：`JsonCommerceRepositories` 暴露 `data_dir`。
- `server.py`：`/commerce/market-channel/fetch` 预充值首请求（manual），同一账本传给会话 → 429 重试也被计量。

### E 遗留 2 项

| Case | 修复 |
|---|---|
| E legacy_purchase_authority | 旧链 `mark-ordered` 在更新前检查新链 procurement 存在同 `sales_order_ref` 的存活（非 cancelled）采购 → 409，一单不可两次下单 |
| E recovery_orphan_lock | `ProcurementService.reconcile_orphan_locks()`：`proc:` 命名空间的锁若引用不存在的采购记录即释放（外来命名空间不动）；接入 `on_startup()` |

## 历史批次（保留）

### 第 2 批 E 层（8 项）

| Case | 修复 |
|---|---|
| E1 paid_body | 采购路由验证 order runtime：body paid/status 永远不是权威；`asserts_payment` 时强制 404/409 |
| E2 missing_order | 声明付款但平台订单不存在 → 404 |
| E3 account_owner | 运行时订单存在时，采购账号必须匹配 cookie_id |
| E4 sku_owner | 运行时订单存在时，采购 SKU 必须匹配 item_id |
| E5 cross_account | 采购幂等键从全局 ref 改为 (account, ref)；跨账号不互拦 |
| E7 budget_reservation | 采购创建时同时调用 BudgetReservationStore.reserve() |
| E9 receipt_accounting | 收货时自动记 provisional 账本分录（实际成本×数量+运费） |
| E10 receipt_replay | 同 receipt_ref 重放返回原记录（幂等）；异 receipt_ref → 409 |

### 第 1 批（29 项）：C1-C6、D1-D6/D6b、D8-D12、E-neg/E-evidence/E-replay、S1/S2/S6

## E 层核心架构逻辑（采购路由三段式验证）

```python
# 1. 声明付款（body 有 paid/status）→ 运行时订单必须存在且为 paid
# 2. 运行时订单存在（无论是否声明付款）→ 账号+SKU 必须匹配
# 3. 运行时订单不存在且未声明付款 → 手动/研究采购，允许通过
```

## 遗留

无（51/51 全部修复）。R 层 `mcp_read_repro.py` 需要真实 MCP 目录参数（--mcp-dir），属环境依赖的真实读取验证，不在 51 项软件断言内。
