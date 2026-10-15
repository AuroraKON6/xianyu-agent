# GLM 第六轮报告：A50 探针客户端接驳 + live 演练基线 + 集中缺口清单

日期：2026-10-12。全量隔离回归 **1587/1580+7**，exit 0；live 前后一致（演练只读）。

## 交付

| 项 | 内容 |
|---|---|
| **A50 探针客户端**（G2B 最后工程缺口） | `BrowserMarketProbeClient`（复用既有 browser_adapter 账号上下文，不建第二浏览器系统）：诚实阶梯五级——无适配器/无会话→`no_credentials`；**缓存会话≠证据**→`unverified_session`（登录状态文件只是缓存，实测是一次需授权的真实平台读）；`MARKET_CHANNEL_PROBE_AUTHORIZED=1` 显式授权后才执行 `check_login_status` 真实读探针（成功→available 带账号与可读字段，拒绝→auth_invalid）；探针客户端**故意无 fetch_page**，fetch 路由保持 409+人工回退指引。7 专项（tests/test_market_probe_client.py）覆盖全阶梯；15 项 market_channel 回归全过 |
| **live 备份演练实测基线**（A114 计时） | 对 data/live 只读演练：备份 4 文件 0.172s / 恢复 0.812s / 全哈希通过 / 0 缺失。分歧行未演示——live 尚无 ledger.json（如实记录，非跳过） |
| **集中缺口清单** | [docs/ops/glm-gap-checklist-2026-10-12.md](../ops/glm-gap-checklist-2026-10-12.md)：五类外部输入的精确解锁路径（真实资料/条款/通道授权/首单链/PG 实例）+ live 现状提示（含 1 个 queued Pi 任务的处置说明）+ 边界重申 |

## 命令与退出码

```text
unittest discover -p test_market_probe_client.py  # 7/7 OK
unittest discover -p test_market_channel.py       # 15/15 OK（回归）
python scripts/commerce_backup_drill.py --source data/live --work <空目录>  # exit 0（只读）
powershell scripts/run_tests_isolated.ps1         # 1587/1587, 361.165s, exit 0
```

## 边界

真实读探针与 fetch 会话仍需账号授权（R 层）；探针样本的逐条人工核对属 R 层；A33 移动端视口截图未补（整合区块沿用 G1C 模式，列入前端打磨）。

## 回退方案

删 BrowserMarketProbeClient 与接线即回第五轮状态（_market_channel_client 返回 None 的行为由 409 语义等价承接）。

## 结论

至此连"最后一块工程缺口"（探针接驳）也已交付。剩余全部为清单所列外部输入；下一动作由用户任一输入触发。
