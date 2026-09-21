# 2026-09-18 第三次复验问题修复轮（2 项 P1：对账×创建交错 / 取消×供应商单登记交错）

输入：操作者第三次复验报告（"仍有 2 项 P1，暂不进入 Pi 阶段"，单进程线程交错稳定复现）。
流程：先写确定性失败测试跑出红 → 再修实现 → 3 连跑全绿 → 重跑全部演练。
执行者：Zcode。起点 `e39d3df` → 终点 `91a85d0`。全量隔离回归：**1296/1296 通过**；静态检查全过；data/live 未变。

## 红灯证据（修复前）

| # | 失败测试 | 红的症状（与操作者复现一致） |
|---|---|---|
| 1 对账×创建 | `test_reconcile_cannot_release_a_reservation_being_created`（事件钩子把创建精确停在"库存已锁、采购未落盘"，此时运行对账） | 对账释放了在途预留；创建继续成功 → 采购单待审批 4 件、锁表空、可用 10 |
| 2 取消×登记 | `test_cancel_cannot_overwrite_a_registered_supplier_order`（钩子只挂起 cancel 的那次转移，期间另一线程登记供应商单） | 终态 cancelled 却带供应商订单号且库存已释放（`'SO-RACE-1' != ''` 断言失败） |

## 修复内容

| # | 根因 | 修复 |
|---|---|---|
| 1 | `reconcile_orphan_reservations` 的"检查采购单是否存在"与"释放锁"不在采购变更的互斥域内 | 新增 `ProcurementService.mutation_guard()`（同一把 `_mutation_lock` 的公开上下文管理器）；对账**全程**持该守卫——检查与释放处于同一受保护区间；创建停在锁内时对账等待而非释放 |
| 2 | `approve/record_supplier_order/record_receipt` 不持业务锁：cancel 读到 approved 后，登记可插入并落盘 ordered，cancel 再以 `require_status=None` 覆盖 | **全部状态变更方法（create/approve/record_supplier_order/record_receipt/cancel）统一进入同一 `_mutation_lock` 域**；cancel 的状态读取本就在锁内（二次复验轮已收口），配合登记同锁后，交错不可能出现。修复后语义：谁先取得锁谁原子完成——cancel 先 → 登记被 `require_status=approved` 拒绝；登记先 → cancel 被 `ordered 不可取消` 拒绝。测试断言两种合法终态并显式排除非法组合（cancelled 且带供应商单号） |

## 验证

- 两条新交错回归：修复前红（症状与复现一致）→ 修复后绿，**3 连跑全绿**。
- 全部 8 条演练（A 跨文件失败 / B 重启恢复 / C 完整业务流 / 新交错组）重跑通过。
- 全量隔离回归 **1296/1296**（1294 + 2）；`node --check`×2、`git diff --check` 通过；data/live 哈希未变；无新功能面。

## 范围确认（与操作者对齐）

- 本轮只修两个同步缺口；进程内线程级互斥。
- 重启测试仍证明"文件重载一致性"，非进程强杀恢复——如实保持该边界表述。
- 未进入 Pi 阶段；等本轮复验通过后按序安排 Pi 持久任务与只读影子运行。
