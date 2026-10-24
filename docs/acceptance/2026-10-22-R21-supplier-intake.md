# R21（文件路径）实施验收报告：供应商报价入口

日期：2026-10-22。基线为 R19 提交 `0e65cce`。

**结论：R21-file 软件/集成层 PASS。新增验收 5/5 绿；存量 intake 23 + import 20 + intake_confirmation 19 不破；全量离线 1788/1788 OK（13 skipped=PG），live 数据 247 文件不变。**

## R21（文件路径）

```yaml
task_id: R21-file
owned_files: [xianyu_agent/supplier_intake.py, config/category-profiles/example-stainless-steel-worktable.json, tests/test_r21_supplier_intake.py, tests/test_supplier_intake.py(fixture升级), tests/test_intake_confirmation.py(fixture升级)]
software_status: PASS
integration_status: PASS  # 全量 1788/1788 OK, 数据 247/247 不变
real_data_status: WAITING_INPUT  # 真实供应商资料进入 R33/R34（B0-B1 待用户）
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [离线文件入口不依赖网络注册表（R19 连接器层只在网络 connector 启用时介入）；1688/供应商 API 通道未实现——首版按任务书走人工原件+结构化导入]
rollback: 保留人工输入与原件；网络 connector 停用（R19 disable）
next_task: R23
```

## 现状盘点（大部分已在前轮建成）

任务书要求的 fixture/replay 验证矩阵——缺字段、版本、到期、原件哈希、重导幂等——**已由存量 42 项测试覆盖**（test_supplier_intake 23 项：原件字节必须真实提供不能只给 hash 声明、demo 不可改标真实、批次撤销/重复失败关闭、过期/未来/朴素时间戳拒绝、单位/币种/运费不假设、数值化库存/MOQ/金额失败关闭、逐快照证据要求、只读 HTTP；test_supplier_import 20 项：CSV/XLSX/粘贴解析、别名映射、缺运费标记、重复 SKU/表头拒绝、确认哈希不匹配拒绝、同文件重导幂等、同 SKU 新版本追加且 latest 移动）。显式规格/MOQ/运费/库存/售后/有效期在 OfferEvidence 合同中逐字段强制。

## 本轮新增：五主体差异核对 + 类目档案样例

**`SupplyChainEntities`**（supplier_intake.py 新合同）——供货关系背后的五个主体：**报价主体**（quote_entity）、**承诺主体**（promising_entity）、**收款主体**（collection_entity）、**发货主体**（shipping_entity）、**退货接收方**（returns_entity）：

| 规则 | 行为 |
|---|---|
| 未申报 | `supply_entities_declared` 检查项 unknown → 报告 needs_evidence，不能 ready |
| 与供应商一致 | `same_as_supplier`——但 detail 明示"a declaration, not a verification"；`provider_verified` 恒 False |
| 与供应商不同 | `different_from_supplier`——detail 点名双方并说明"实体差异（如平台担保收款、异地仓发货）必须先理解，是 review item，永不静默接受" |

存量 fixture 升级为声明五主体（等同供应商）——这是新合同的正确升级，不是放松测试。

**类目档案样例** `config/category-profiles/example-stainless-steel-worktable.json`：商用厨具/不锈钢工作台的规格必填字段（长宽高/材质/层架/轮）、计价与运费惯例、有效期建议（钢材波动 ≤7 天）、售后要求、五主体核对清单、类目特有风险注记（同名不同规格替换陷阱、201/304 价差）。评估保持通用，档案引导操作者检查单与后续逐类目门。

## 验收矩阵（tests/test_r21_supplier_intake.py，5/5）

| 验收项 | 测试 |
|---|---|
| 主体一致是声明非验证 | 五主体全同 → same_as_supplier + provider_verified=False |
| 差异表面化为 review item | 收款=平台担保、发货=异地仓 → different_from_supplier，detail 点名双方 |
| 未申报是缺口 | 默认声明 → supply_entities_declared 进 gaps，状态降为 needs_evidence |
| 合同严格 | 未知实体字段 → ValidationError（extra=forbid） |
| 类目档案 | 样例解析 + 五主体清单齐备性校验 |
