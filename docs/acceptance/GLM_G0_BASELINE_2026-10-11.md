# GLM G0 基线锁定报告

日期：2026-10-11。执行者：GLM。配套 manifest：[GLM_G0_BASELINE_MANIFEST_2026-10-11.json](GLM_G0_BASELINE_MANIFEST_2026-10-11.json)。

包与范围：G0 锁定基线与所有权（矩阵 A00-A03）。本轮只读核对 + 隔离回归，无业务代码改动。

## 1. 状态总览

- 软件（S）：**PASS**（1489/1489 隔离回归，exit 0）
- 进程/存储核对（P）：**PASS**（live 前后哈希一致；无遗留测试进程；无端口占用）
- 真实资料与来源（R）：**NOT_RUN**（G0 不涉及）
- 商业执行（B）：**NOT_AUTHORIZED**（G0 不涉及）

## 2. 代码版本与工作区（A00）

- HEAD：`4bd2cf79227fc32d6805d5e3f188a42ad87863b2`（与交接一致）。
- 工作区：65 项 dirty——25 个已跟踪文件修改（`xianyu_agent/server.py`、`supplier_import.py`、`pi_jobs.py` 等 + 9 个测试 + 4 个前端文件）、40 个未跟踪新文件（R6-R9 报告/manifest、`quote_provenance.py`、`supplier_intake.py`、`pi_usage_reconciliation.py`、`pi_validation.py`、10 个新测试、`web/pi-usage.js` 等）。
- 未回滚、未清理、未暂存、未提交任何文件。`git diff --check` exit 0。
- 环境与依赖：Python 3.8.5（项目 `.venv`）、Node v24.16.0、`requirements.txt` 与 `.env.example` 属已修改未提交文件（内容未审计差异细节，不阻塞 G0）。
- 配置：`.env` 存在（5579 字节，2026-09-18）；本报告只记录存在性与变量名清单（见 manifest 生成过程），不记录值。8000/8788/9223 端口均无监听；tasklist 无本项目 python/node 服务进程。

## 3. 隔离回归基线（A01/A02）

| 项 | 结果 |
|---|---|
| 命令 | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1` |
| 退出码 | 0 |
| 数量 | 1489 tests，0 failures，0 errors，474.009s |
| live 影响 | `data/live` 20 个文件运行前后集合与 SHA-256 完全一致（清单在 manifest） |
| 源码变化 | 监测的 268 个 py/js/html/css/ps1/json 文件（含未跟踪）测试期间哈希无变化 |
| 遗留进程 | 无（测试进程由脚本 finally 清理，端口复核为空） |
| Node 语法 | `node --check web/app.js` exit 0；`node --check web/pi-usage.js` exit 0 |

实际数量 1489 与 09-23 R9 清单记录一致，为本次独立重跑结果，非照抄。live `commerce/` 仅有 agent_jobs.json、decisions.json、provider_capabilities.json；无 offers/matches/opportunities/采购/库存/账本文件，与交接 §2 一致。

## 4. 入口矩阵（A03）

| 业务对象 | 服务层 | HTTP | UI（web/app.js） | 真实通道 |
|---|---|---|---|---|
| 供应商导入 | `SupplierImportService`（server.py:121，注入 commerce repositories） | `/commerce/supplier-imports/preview`、`/{preview_id}/confirm`、GET 列表、`/{batch_id}/revoke` | 有（preview/confirm 各 1 处） | 仅文件（CSV/XLSX/粘贴）；无在线来源接通 |
| 资料准入评估 | `supplier_intake.build_supplier_intake_router`（server.py:170 挂载，只读） | `/commerce/supplier-imports/{batch_id}/readiness` GET/POST | 无专用 UI | 只读计算，无持久化审批 |
| 匹配 | `product_matching.py` + repositories | `/commerce/product-matches`、`/{match_id}/correction` | 有（1 处） | 无真实市场通道 |
| 核算与商机 | `OpportunityService`（server.py:123）+ `unit_economics.py` | `/opportunities/*`（旧）与 `/commerce/opportunities/*`（evaluate/funnel/decision-card） | 有（evaluate、funnel 等 8 处） | 费率含演示默认值（G1B 修） |
| 商业任务 | `agent_jobs.py` AgentJobStore | `/commerce/agent-jobs*`（create/stats/cancel/recover） | stats 1 处 | run-next 手动；单 worker 串行 |
| Pi 分析 | `pi_jobs.py` + `pi_runtime.PiRpcClient`（server.py:230） | `/commerce/pi-tasks/*`（submit/run-next/reconcile/evidence） | 有（submit/run-next/evidence） | R7 曾 1 次真实调用（demo 报价）；当前 0 待执行 |
| 平台执行 | `execution_queue`（server.py:116 configure）+ `action_executor` | `/execution-jobs*`（run-next/run-pending/retry/cancel/verification） | 有（5 处） | 唯一执行链；当前 XIANYU_ADAPTER 隔离为 stub，live 账号 0 |
| 采购 | `ProcurementService`（procurement.py:38） | **无** | 无 | 仅服务层+测试演练，未接主链（G4A） |
| 库存 | `InventoryAllocationService`（inventory.py:34） | **无** | 无 | 同上 |
| 账本 | `LedgerService`（ledger_service.py:35） | **无** | 无 | 同上 |

两个任务队列职责：`/commerce/agent-jobs` + `/commerce/pi-tasks` 为分析任务（只读建议），`/execution-jobs` 为平台写动作（WorkOrder → Approval → ExecutionJobQueue → ActionExecutor）。无证据显示二者互抢（claim 语义见 pi_jobs/execution_queue 各自实现，A42 将在 G2A 复验）。

## 5. 事实所有权表

| 域 | 权威 | 兼容读者/投影 |
|---|---|---|
| 候选/试卖/订单（旧链） | `ResaleManager`（server.py:222 init；resale_candidates.json + SQLite 订单） | commercial_trial、commercial_launch、preflight 只读消费 |
| 报价/匹配/商机/任务/决策（新链） | `commerce/repositories.py` JSON repositories（data/live/commerce/*.json） | opportunity_service、pi_jobs、supplier_intake 只读/追加 |
| 采购/库存/账本 | 服务类已实现但**未挂主服务**，无 live 权威文件 | tests/test_procurement_ledger 等隔离演练 |
| 平台账号/商品 | SQLite（db_manager） | browser_adapter、account_runtime |

## 6. 已知限制与下一步

- 隔离脚本覆盖 unittest 发现范围；浏览器 UI 全量 e2e 与真实模型不在本轮（按矩阵分级，后续包覆盖）。
- `console.html` 演示费率默认值、商机 UI 需手填快照 ID、agent_scheduler.discovery_loop 空转等交接 §2.2 问题保留，归 G1B/G2 处理。
- 下一包：G1A 原件归档、操作者确认与用途绑定（A10-A19）。设计先行，随后实施。

## 7. 复现命令与退出码

```text
git status --short                      # 65 entries
git rev-parse HEAD                      # 4bd2cf79227fc32d6805d5e3f188a42ad87863b2
git diff --stat                         # 25 files, +697/-268（tracked 部分）
git diff --check                        # exit 0
powershell ... run_tests_isolated.ps1   # exit 0, 1489 OK, 474.009s
node --check web/app.js                 # exit 0
node --check web/pi-usage.js            # exit 0
```
