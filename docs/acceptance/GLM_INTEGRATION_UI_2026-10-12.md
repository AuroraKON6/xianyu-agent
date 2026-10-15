# GLM 前端整合轮验收报告：采纳 / 通道 / 订单投影接入控制台

日期：2026-10-12。执行者：GLM。

## 包与范围

G 计划 §7 前端逐步整合（G3A/G2B/G2C/G4A 的 UI 面）。全量隔离回归 **1571/1571**，exit 0；live 20 文件前后一致。

## 修复的真实缺陷（本轮浏览器验收发现）

**真实 server 缺 `/console/intake-confirmation.js` 静态路由**：G1C 时脚本标签已加入 console.html，但 server.py 只有 app.js/pi-usage.js 路由——G1C 浏览器验收用的是自带夹具（有该路由），真实服务下脚本 404、资料闭环面板整块失效。本轮整合测试用真实 server.app 抓到并已修复（`console_intake_confirmation_js`）。教训：夹具路由集合必须与 server 同步——已记入边界。

## 交付

| 项 | 内容 |
|---|---|
| console.html | 决策卡区新增采纳表单（候选下拉+账号+理由）与「6. 通道与订单投影」区（通道探针/Radar 状态/销售单号查询） |
| web/app.js | `commerceRenderDecisionCard` 渲染 material_status（逐 SKU 到期+三用途实时许可表）；`commerceLoadAdoptTargets`（候选下拉从 /resale/candidates 拉取，带状态）；`commerceAdopt`（幂等键自动生成、结果区显示去重/指纹/旧链提示）；`commerceAdoptRecheck`；`commerceChannelStatus`（通道诚实状态+radar 计数）；`commerceOrderDetail`（采购+账本同视图，空态显式"无采购记录/无账本分录"） |

## 浏览器验收（真实 server.app + 临时 DATA_DIR + stub adapter）

`tests/test_commerce_integration_ui.py`（1 项端到端）：**PASS**

流程：面板导入报价 → **真实材料确认**（模板生成→填核验值→凭证→预览→确认，G1C 全流程）→ 市场观察登记 → 匹配 → 商机 → 核算 → 决策卡显示资料到期与用途许可 → 创建候选 → 采纳（显示"采纳已记录"+旧链提示）→ 复验"仍然有效" → 通道状态如实 unconfigured → 订单投影空态显式。控制台零错误。截图：[g-integration-ui-artifacts-2026-10-12/](g-integration-ui-artifacts-2026-10-12/)（3 张）。

测试过程中的**正确防线**两次拦住错误夹具：(1) 匹配 model 约束两侧未知 → COMPARABLE，采纳拒绝（夹具补型号后才可过）；(2) 材料未确认 → 采纳拒绝。均为设计行为，非缺陷。

## 命令与退出码

```text
node --check web/app.js / intake-confirmation.js     # exit 0
unittest discover -p test_commerce_integration_ui.py # 1/1 OK（真实 server 子进程 + Chromium）
powershell scripts/run_tests_isolated.ps1            # 1571/1571, 294.460s, exit 0
```

## 已知边界

1. 1440x900 单视口验收；移动端适配沿用 G1C 模式（本轮区块复用 commerce-grid，未单独截图——列入下一轮）。
2. 通道 fetch 无真实客户端（409 如实），radar tick 按钮未加（后台默认关闭，手动 tick 可经 API）。
3. pi-usage.js 面板与新区块未做联合回归（各自独立模块，无共享状态）。

## 回退方案

还原 console.html/app.js 两处区块 + server 一条静态路由即回 G4B/G5A 状态。

## 结论

G0-G5A 首批 + 前端整合全部完成隔离软件验收。剩余全部为外部输入/环境/批准缺口（真实报价确认、账号授权通道、真实首单、PostgreSQL 演练环境、真实停写切换批准），逐包报告均已列明精确清单。
