# GLM G4A 验收报告：采购/库存/账本接入主链

日期：2026-10-12。执行者：GLM。

## 包与范围

G4A（矩阵 A90、A92、A96、A99 的组合层 + H 级路由）。前置：G1-G3A。服务级行为（A91/A93-A95/A97/A98）已由既有 test_procurement_ledger / test_inventory_and_listing / test_recovery_drills 验收，本轮将它们接入主 server 组合。

## 状态总览

- 软件（S）：**PASS**（3 项组合专项 + 全量 1562/1562，exit 0）
- HTTP（H）：**PASS**（真实路由：采购创建/审批/供应商单登记/收货/取消、库存登记与分配、账本条目/汇总、订单详情投影）
- 真实资料与来源（R）：**WAITING_INPUT**（真实平台付款订单、真实供应商单号/付款/物流凭证）
- 商业执行（B）：**NOT_AUTHORIZED**（无任何自动供应商提交；下单/付款/退款保持人工）

## 交付

| 项 | 内容 |
|---|---|
| server 组合 | `inventory_service` / `procurement_service` / `ledger_service` 作为**单一共享实例**挂入主组合（API 与恢复器同源同锁，A90）；`ProcurementService` 注入 `supplier_import_service`（批次 committed 校验） |
| 事实所有权 | `commerce/procurement_orders.json`、`commerce/inventory.json`、`commerce/ledger.json` 为各自域权威；旧 resale `purchase_task` 保持兼容投影，本组合**不双写**（代码注释+本报告声明，A90/G4A-2） |
| 路由 | `GET/POST /commerce/procurement*`（列表/详情/from-sales-order/approve/supplier-order/receipt/cancel）、`GET /commerce/inventory`、`POST /commerce/inventory/register`、`GET /commerce/ledger/entries|summary`、`GET /commerce/orders/{order_ref}/detail`（采购+账本同订单投影，G4A-10） |

## 矩阵逐项（本轮组合层）

| ID | 结果 | 证据（tests/test_g4a_main_chain_wiring.py） |
|---|---|---|
| A90 | PASS (H) | 共享实例断言（procurement._inventory is inventory_service）；黄金人工流程：导入→库存登记→采购创建(awaiting_approval)→审批→登记供应商单→收货(received)→账本 settled 支出→订单详情聚合 |
| A92 | PASS (H) | 同销售单重复创建 409；同 ref 跨账号也 409（业务键=平台销售单，防止一单拆两采） |
| A96 | PASS (H) | 供应商单号人工登记（ordered）；API 成功不等于付款/发货——账本 settled 仅凭 payment_evidence_ref |
| A99 | PASS (H) | 部分冲正保留原分录（amount 不变），reversal_of/category/direction 正确；订单详情同时投影采购与账本 |

## 命令与退出码

```text
unittest discover -p test_g4a_main_chain_wiring.py  # 3/3 OK
powershell scripts/run_tests_isolated.ps1           # 1562/1562, exit 0（两轮）
node --check web/app.js / intake-confirmation.js    # exit 0
```

live 20 文件前后一致；零真实模型/平台调用。

## 已知边界

1. `create_from_sales_order` 的"凭平台付款事实"前置：当前调用方声明 sales_order_ref；接入真实订单 runtime 的付款核验（不信手写 paid）在 G4B 首单链完成（A91 服务级已有 paid-source 语义测试）。
2. 一单一 SKU 一采购（首版范围）；拆/合单保持 awaiting_human（服务级已实现）。
3. 恢复器未挂后台循环（单写进程内同步恢复语义已由服务锁保证；定时恢复属 G5A 生命周期）。

## 回退方案

删除组合块与路由即回 G3A 状态；三个 JSON 权威文件为新增数据，不影响旧读取方。

## 下一工作包

G4B 首单与财务复核（A100-A102 黄金样本贯通 + A103/A104 真实首单 WAITING_INPUT）；G5A 生产事务 ADR 与迁移设计。
