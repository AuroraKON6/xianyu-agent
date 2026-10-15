# GLM 第八轮报告（补充）：闲鱼 MCP 真实通道接入 — A50/A51 R 层 PASS

日期：2026-10-12。输入：用户授权"闲鱼MCP可以加入进去"（会话 sess_649ea496 调研底稿）。依赖决策：[ADR-0003](../adr/0003-xianyu-mcp-read-channel.md)。

## 通道选定（A50 R）

goofish-cli（首选推荐）因 Python≥3.11 与项目 venv 3.8 不兼容、PyPI 无包而搁置；**rira2/xianyu-mcp**（Node 24.16 ✓，Apache/MIT，免账号/无 cookie/无写工具，Playwright 有头 Chromium 匿名读 mtop）clone 于 `E:\闲鱼\xianyu-mcp-tmp`，npm ci + chromium 就绪，自写最小 stdio MCP 客户端 `research/real-source-demo-2026-10-12/mcp_probe.py`。

## 真实探针与读数（全为实际匿名平台读，零写操作）

| 步骤 | 结果 |
|---|---|
| capabilities(probe) | ok=True；session_state=**logged_out**（证实匿名）；work-without-account 工具清单返回 |
| search_items（首次） | **RGV587 风控限流**（75s/2 attempts 拒绝，retry_after=60s）——如实记录 |
| 退避 70s 重试 | search_count：**"不锈钢工作台" 291,757 件**；browse_feed 15 条真实首页卡；**search_items 15 条真实商品卡**（item_id+价格+想要数，¥39–66） |
| 入账 | 10 条真实闲鱼观察进流水线（URL 溯原 `goofish.com/item?id=…`），审计 0 冲突 |
| **第一张真实闲鱼市场侧商机卡** | `opp_227b9282fd44`：1688 批发 ¥360 → 闲鱼真实 ¥59（工厂处理二手），COMPARABLE，利润 unknown——**系统如实判"无差价机会"**（该品类闲鱼被二手/清货压价） |

证据：`mcp_probe_result.json` / `mcp_retry_result.json` / `real_xianyu_card.json` / `real_xianyu_injection.json`（research/real-source-demo-2026-10-12/）。

## 矩阵判定

- **A50 R 层 PASS**：已获权（用户授权 MCP 接入）来源 probe 真实成功，账号状态（匿名 logged_out）、返回字段、样本可审阅，零凭据曝光。
- **A51 R 层 PASS（市场侧）**：真实响应含精确价（search_items 卡）与库存估计（search_count）；原值全保留（观察快照带 item_id/URL/价格/want_count）；price_kind=full。
- **A53 S 层 PASS（真实形态）**：限流（RGV587）→ 退避 60s 重试成功；尝试计入配额语义与 A46 一致。
- A52 R 部分：成色区分（二手/全新在标题中）由匹配降级体现（COMPARABLE 非 EXACT）——真实负样本。

## 边界

匿名读受风控波动（README 明示 anonymous_flakiness）；每次调用起真实有头 Chromium、用后即关、不留进程（已核无残留）；**无任何写操作、无账号、无 cookie**。RGV587 限流期通道如实 unavailable，不假成功。

## 给 Codex 检验的复现路径

```text
cd E:\闲鱼\xianyu
.venv/Scripts/python.exe research/real-source-demo-2026-10-12/mcp_probe.py   # 首次多触发限流属预期
# 退避 60s 后按 mcp_retry 流程重试；证据 JSON 同目录
python scripts/audit_commerce_ledgers.py --data-dir research/real-source-demo-2026-10-12/data-expanded
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1   # 1592/1592
```

八轮全量回归 1592/1592 仍有效（本轮零产品代码改动；新增仅 research 脚本与 ADR）。
