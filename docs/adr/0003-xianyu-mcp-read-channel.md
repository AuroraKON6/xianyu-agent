# ADR-0003：引入 xianyu-mcp（免账号只读闲鱼通道）作为市场观察源

日期：2026-10-12。状态：已接受（用户 2026-10-12 明示"闲鱼MCP可以加入进去"）。

- **缺什么**：G2B 需要一条真实闲鱼市场读通道；goofish 无公开 SSR（本轮已实证 JS 空壳），自有 browser_adapter 需账号登录。
- **为何现有模块不满足**：匿名访问必须走有头浏览器 + goofish 自有 `window.lib.mtop.request`（token/签名不可离线复刻）；本项目 stub/browser 均面向账号会话。
- **上游**：`github.com/rira2/xianyu-mcp`（npm `xianyu-mcp`，clone 于 `E:\闲鱼\xianyu-mcp-tmp`，Apache-2.0 项目本体 MIT 条款见仓库 LICENSE/NOTICE）。纯**子进程调用**（stdio MCP/JSON-RPC），零代码复制、零凭据输入（免账号、无 cookie、无写工具）。
- **兼容性**：Node ≥22.18（本机 24.16 ✓）；Playwright Chromium（已装）；有头窗口（Windows 桌面 ✓）。备选 goofish-cli 因 Python≥3.11 与本 venv 3.8 不兼容暂不采用。
- **接入点**：`research/real-source-demo-2026-10-12/mcp_probe.py`（最小 stdio 客户端）；观察经 `market_observation()` 入账（provider `market:public_web_observation`，URL 溯原 `goofish.com/item?id=…`）。
- **风控与配额**：匿名读受 RGV587 限流（实测 75s 内 2 次尝试被拒，60s 退避后成功）——对应 A46 配额语义（尝试计数/退避/重启不清零）；正式接入 fetch 会话时挂 `SourceQuotaLedger`。
- **可替换性**：market_channel.py 的客户端协议不变；换 goofish-cli（若升 Python）只换适配层。
- **风险**：第三方工具随平台改版可能失效（failure 形态为结构化 SearchUnavailableError，不假成功）；匿名窗口为真实浏览器实例，用后即关，不留后台。
