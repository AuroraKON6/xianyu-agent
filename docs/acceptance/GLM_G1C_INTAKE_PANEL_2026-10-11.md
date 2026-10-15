# GLM G1C 验收报告：资料操作面板闭环

日期：2026-10-11。执行者：GLM。

## 包与范围

G1C（矩阵 A30-A33）。前置：G1A/G1B（见 docs/acceptance/GLM_G1A_*、GLM_G1B_*）。

## 状态总览

- 软件（S）：**PASS**（模块语法 + 服务层由 G1A/G1B 覆盖）
- HTTP 集成（H）：**PASS**（夹具服务走真实导入/确认/资格路由）
- 浏览器（U）：**PASS**（4 项 Playwright 真实浏览器验收，Chromium headless，真实 console 资产 + 真实隔离后端）
- 真实资料与来源（R）：**WAITING_INPUT**（真实操作者凭证 + 真实供应商原件）
- 商业执行（B）：**NOT_AUTHORIZED**
- 全量隔离回归：**1528/1528**（+4 浏览器），308.670s，exit 0；live 20 文件前后一致

## 交付

一条用户流程（商机雷达面板新增「1b. 资料闭环」区）：

**粘贴/上传原件 → 预览（每行原值 vs 标准化对照 + 问题）→ 确认导入批次 → 按供应商/时间选择批次 → 逐 SKU 报价资格与既有确认 → 生成声明模板（快照 ID 自动填入，操作者只填核验值）→ 确认预览（可确认 SKU/排除原因/缺口/范围期限说明）→ 勾选用途确认 → 实时资格查询（按 SKU×用途）→ 撤销确认**。

| 文件 | 内容 |
|---|---|
| `web/console.html` | 新增 #intake-panel 区（含演示开关无关的成本表单修复见 G1B）；引入 intake-confirmation.js |
| `web/intake-confirmation.js`（新，372 行） | 自包含模块：busy 双击守卫、错误分级（error/warning tone）、声明草稿 sessionStorage、hash 恢复批次、幂等键自动生成并展示、凭证仅本页内存可一键清除 |
| `web/style.css` | 面板样式（含 tone 色与移动端） |
| `tests/intake_ui_fixture.py`（新） | 真实服务+真实路由夹具（导入路由为对同一 SupplierImportService 的薄包装，与生产同层）；app.js/pi-usage.js 置空隔离 |
| `tests/test_intake_confirmation_ui.py`（新） | 4 项浏览器验收 |
| `tests/pi_usage_ui_fixture.py` | 夹具为新脚本标签补空路由（不改旧断言语义；见下） |
| `tests/test_pi_usage_ui.py` | 页面资源计数 4→5（console.html+style+pi-usage.js+空 app.js+空 intake-confirmation.js；页面合法新增静态资产，安全意图不变：无额外网络动作、无 token 泄漏断言全部保留） |

## 矩阵逐项

| ID | 结果 | 证据（tests/test_intake_confirmation_ui.py） |
|---|---|---|
| A30 | PASS (H/U) | `test_a30_full_loop_without_copying_ids_and_reload`：全流程无复制内部 ID（批次按供应商+时间选择、SKU 按名称显示、模板自动填 ID）；确认前显示范围与期限；刷新后 hash 回到同批次并可见已存确认；截图 a30-*.png |
| A31 | PASS (H/U) | `test_a31_double_click_guard_and_partial_exclusion`：dblclick 只产生一次预览；部分 SKU 不合格（单位冲突）被排除且原因可见；确认一次后按钮禁用；被排除 SKU 资格 no_active_confirmation；截图 a31-partial.png |
| A32 | PASS (U) | `test_a32_network_failure_preserves_draft_and_empty_state`：空态文案、500 列表请求报错、声明草稿刷新/失败后保留、预览请求中断不丢已填内容；截图 a32-network-error.png |
| A33 | PASS (U) | `test_a33_viewports_no_overflow_and_console_clean`：1440x900 与 390x844 无横向溢出（文档与面板级）、按钮文本可读、控制台零错误；截图 a33-desktop.png / a33-mobile.png |

截图证据：[g1c-ui-artifacts-2026-10-11/](g1c-ui-artifacts-2026-10-11/)（7 张，来自真实 Chromium 渲染 + 真实隔离后端，非演示数据）。

## 命令与退出码

```text
unittest discover -p test_intake_confirmation_ui.py   # 4/4 OK（浏览器）
unittest discover -p test_pi_usage_ui.py              # 15/15 OK（回归，语义变更见上）
node --check web/intake-confirmation.js               # exit 0
powershell scripts/run_tests_isolated.ps1             # 1528/1528, 308.670s, exit 0
```

## 已知边界

1. 浏览器验收加载真实 console 标记/样式/本模块脚本，但 app.js/pi-usage.js 在夹具中置空（沿用 R8 确立的模式）；整页多模块联合 e2e 未宣称。
2. 文件上传路径经 FileReader 前端读字节（xlsx/csv），无服务端持久化上传通道；粘贴路径与生产 step1 一致。
3. 声明编辑为 JSON 文本框 + 模板；字段级表单待真实操作者使用反馈后细化。
4. 键盘提交（Enter 触发按钮）由原生 button 语义保证；焦点/滚动断言含于 A33 溢出检查，未逐元素做 Tab 遍历（列入 G2 前端打磨项）。

## 需要用户提供的精确输入（R 层）

1. `INTAKE_CONFIRM_OPERATOR` 与 `INTAKE_CONFIRM_TOKEN_SHA256`（sha256(随机 token)）配置后由真实操作者走一遍真实报价确认。
2. 一份真实供应商报价原件与配套声明事实（ issued/received/valid_until、身份/许可引用、逐 SKU 条款）。

## 回退方案

移除 console.html 的 1b 区块与 script 标签、删 web/intake-confirmation.js 与两个测试文件即回到 G1B 状态；无后端依赖。

## G1 收口与下一步

G1（A10-A33）软件/HTTP/UI 全部 PASS；M1 里程碑的「至少一份经核验资料」待真实输入（WAITING_INPUT）。下一包 G2A：持久增量调度（radar dispatcher，复用 agent_jobs/pi_jobs，单写进程内生命周期管理）。
