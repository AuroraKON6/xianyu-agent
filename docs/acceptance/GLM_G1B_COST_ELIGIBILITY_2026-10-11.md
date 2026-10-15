# GLM G1B 验收报告：费用来源与资格失效联动

日期：2026-10-11。执行者：GLM。

## 包与范围

G1B（矩阵 A20-A26）。前置：G1A（[报告](GLM_G1A_INTAKE_CONFIRMATION_2026-10-11.md)）。

## 状态总览

- 软件（S）：**PASS**（16 项专项 + 全量 1524/1524）
- HTTP 集成（H）：**PASS**（空载荷无默认值、terms 绑定规则、decision-card 重验）
- 浏览器（U）：表单结构变更已实现并语法/全量回归通过；交互级浏览器验收（空态/演示开关/键盘）归 G1C 统一执行
- 真实资料与来源（R）：**WAITING_INPUT**（真实账号+类目费率条款需操作者按当日页面录入）
- 商业执行（B）：**NOT_AUTHORIZED**

## 变更清单

| 文件 | 变更 |
|---|---|
| `xianyu_agent/opportunity_service.py` | evaluate 新增 quantity/freight_basis(per_unit/per_order 整单分摊)/commercial_terms 绑定/cost_metadata/installation/demo_costs；阶梯价按数量适用（件价基础价始终有效，批发价仅在数量达阈时替换，绝不反向套用）；inputs 记录 sale_price_basis、applied_tier、tier_note、cost_metadata、demo_costs；_qualify 新增 costs_not_demo 与 platform_fee_terms_current 门禁；新增 `validate_current`（新供应商快照/最新匹配修订/过期/条款版本变化 → ready 即时转 paused，历史保留） |
| `xianyu_agent/server.py` | evaluate 路由：空串清洗（缺成本不注入默认值）；`account_terms {cookie_id,category}` 从 AccountCommercialTermsStore 取当期 rate/fixed（与显式 rate/fixed 互斥 409；快照缺失/超 24h 409）；新路由 `POST /commerce/opportunities/{id}/revalidate`；decision-card 渲染前先 validate_current |
| `xianyu_agent/account_commercial_terms.py` | 公开 `parse_terms_datetime`（原私有解析的包装），无行为变化 |
| `web/console.html` | 移除 0.03/0/5/12/4 五个演示默认值（输入框留空+提示语）；新增显式“演示成本模式”复选框 |
| `web/app.js` | evaluate 请求携带 demo_costs；演示开关仅在被勾选时填充演示数字、取消勾选清空；`node --check` 通过 |
| `web/style.css` | `.commerce-demo-toggle` 样式 |
| `tests/test_g1b_cost_eligibility.py`（新） | 16 项 A20-A26 契约（13 服务层 + 3 HTTP） |

## 矩阵逐项

| ID | 结果 | 证据 |
|---|---|---|
| A20 | PASS (S/H) | `test_a20_no_defaults_when_costs_missing`：全缺成本 → 平台费来源 missing、利润 unknown、不达标；HTTP 空载荷 `{}` 同样。`test_a20_demo_costs_flag_blocks_qualification`：demo_costs=true 时即便数字齐全也不得 ready |
| A21 | PASS (S/H) | `test_a21_terms_bound_and_revision_invalidates`：费率取自快照（300×0.03=9）、版本入 inputs；条款修订后 validate_current 判 invalid 并 paused。`test_a21_terms_of_another_account_rejected` + HTTP：错账号/类目无快照 409；与显式 rate/fixed 混用 409 |
| A22 | PASS (S) | 阶梯价：qty1 用基础价 160、qty10 才用 145；整单运费 20 元 qty4 → 单件 5；币种冲突 EconomicsError |
| A23 | PASS (S) | unknown/not_applicable/estimated 分别进入 missing/跳过/元数据；cost_metadata 记 basis/source/assumption |
| A24 | PASS (S) | 无区间输入三情景相同并注明；sale_price_basis 标“展示价假设非成交”；有保守覆盖时 scenarios_distinct |
| A25 | PASS (S/H) | 新供应商快照（涨价）→ paused+newer supplier snapshot；匹配人工改 NOT_MATCH → paused；decision-card 路由先重验（未知 id 404）；有效 ready 保持 |
| A26 | PASS (S) | 黄金样本 300-160-20-5-9-4-12=90 精确一致；after_sales_reserve not_applicable 不与显式退货损失 12 双算 |

## 命令与退出码

```text
unittest discover -p test_g1b_cost_eligibility.py   # 16/16 OK, exit 0
unittest discover -p test_commerce_api.py           # 13/13 OK（回归）
unittest discover -p test_unit_economics.py         # 21/21 OK（回归）
powershell scripts/run_tests_isolated.ps1           # 1524/1524, 380.304s, exit 0
node --check web/app.js                             # exit 0
```

live 20 文件前后哈希一致；零真实模型/平台调用。

## 未完成/不支持行为

1. 旧 analytics 代理（`/agents` 链路的 calculate_profit 回退默认 0.08）属 legacy 候选评分通道，不在本包生产表单范围；G2 统一预算时处理并记录。
2. Pi 输入的用途门禁接驳（eligibility → pi_jobs 出站过滤）在 G2A A45 执行；本包已提供 eligibility 判定入口。
3. 已批准未执行的旧链动作（work order/approval）的执行前重验在 G3A/G4 接入（A72/A93）；本包完成商机层。
4. 税费口径：tax_mode=unknown 保持 unknown（G1B 不规定税率，等用户确认适用口径）。

## 需要用户提供的精确输入（R 层）

1. 真实账号+目标类目的当日商业条款（费率、固定费、退货/保证金状态）——经控制台“记录账号商业条款”或 `POST /accounts/{cookie_id}/commercial-terms`。
2. 真实包装/运费/退货损失/可归因成本的依据（依据文本将进 cost_metadata）。

## 回退方案

evaluate 新参数全部可选且默认行为与旧调用兼容（quantity=1、per_unit、无绑定）；控制台输入框清空不影响既有 API；`validate_current` 只在显式路由/decision-card 调用。恢复 console.html 旧默认值 + 还原 3 个后端文件即回 G1A 后状态。

## 下一工作包

G1C：资料操作面板闭环（A30-A33，桌面 1440x900 / 移动 390x844 浏览器验收）。
