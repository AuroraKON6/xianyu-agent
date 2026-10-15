# GLM G2C 验收报告：首张商机卡（确定性部分）

日期：2026-10-12。执行者：GLM。

## 包与范围

G2C 工程侧（A60 的确定性卡完整性 + A62/A63 的链路就绪）。真实卡（A60 R/A61/A62 R/A63 R）**WAITING_INPUT**。

## 状态总览

- 软件（S）：**PASS**（1 项专项 + 全量 1552/1552，exit 0）
- HTTP（H）：**PASS**（decision-card 路由带 material_status）
- 真实资料与来源（R）：**WAITING_INPUT**（真实报价+真实市场观察+Pi 分析预算）
- 商业执行（B）：**NOT_AUTHORIZED**

## 交付

决策卡（`GET /commerce/opportunities/{id}/decision-card`）新增 `material_status`：逐供应商快照回答"资料何时到期"（expires_at）、来源类型、三种用途（本地分析/外部模型/经营）的**实时重算**许可与原因——无确认时诚实显示不可用及原因，不静默通过。卡片因此完整覆盖计划要求的九问中的资料侧五问（规格/货源/价格类型/成本状态由 economics 与 decisions 承载，Pi 建议由 decisions 承载）。

A61（20 组真实商品对复核）与离线标注集扩展：真实样本 WAITING_INPUT；离线标注评估沿用 `product_matching.evaluate_labeled_pairs`（既有，回归全绿），不虚报线上精确率。

## 命令与退出码

```text
unittest discover -p test_g2c_decision_card.py  # 1/1 OK
powershell scripts/run_tests_isolated.ps1      # 1552/1552, 334.688s, exit 0
```

live 20 文件前后一致。

## 回退方案

material_status 为 decision-card 路由内追加字段，删除即回 G2B 状态。

## 下一工作包

G3A 商机采纳桥接（A70-A74，隔离工程）。
