# GLM G2B 验收报告：单一市场观察通道（只读）

日期：2026-10-12。执行者：GLM。

## 包与范围

G2B（矩阵 A50-A55）。前置：G2A。

## 状态总览

- 软件（S）：**PASS**（15 项专项 + 全量 1551/1551，exit 0）
- HTTP 集成（H）：**PASS**（status/fetch 路由真实挂载；未配置通道结构化诚实应答）
- 浏览器（U）：NOT_RUN（面板自动通道状态展示归后续前端整合；人工观察面板已有明确"人工观察，非成交事实"标注）
- 真实资料与来源（R）：**WAITING_INPUT**（真实账号授权、实际获准请求与人工逐条核对）
- 商业执行（B）：**NOT_AUTHORIZED**

## 交付

| 文件 | 内容 |
|---|---|
| `xianyu_agent/market_channel.py`（新） | `probe_channel`（无通道→unconfigured、无凭证→no_credentials、401→auth_invalid、只有真实应答才 available，且注明只证明读路径非写授权）；`classify_price` 价格类型纪律（精确全价才 known；起售/区间/定金/券后保留原值+类型、eligible unknown；soldPrice 字段仅为提示非成交事实）；`normalize_market_item`（上游时间为观察时间→缓存重读不刷新报价；未来时钟回退抓取时间并注明；缺失成色=显式 unknown；缺失商品绝不推断下架/零库存）；`ChannelFetchSession`（按 product_id 去重、空页=正常结束、401 立即停止不重试、429/5xx 有界重试后 degraded、部分结果保存、resume_token 断点续取） |
| `xianyu_agent/server.py` | `GET /commerce/market-channel/status`（诚实探针）；`POST /commerce/market-channel/fetch`（无授权客户端→409 `channel_not_configured` 并指向人工观察回退） |

通道快照 provider=`market:xianyu_channel`，与人工观察 `market:xianyu_observation` 恒可区分（A55 来源类型清晰）。

## 矩阵逐项

| ID | 结果 | 证据（tests/test_market_channel.py） |
|---|---|---|
| A50 | PASS (S/H) | unconfigured/no_credentials 不假成功；probe ok 才 available 且带"非写授权"注记；401/429 → 结构化 unavailable+error_kind |
| A51 | PASS (S) | full/starting/range/deposit/post_coupon/sold_hint 六类价格分类；原值保留于 attributes.market_channel；unknown 不冒充 |
| A52 | PASS (S) | 成色/数量变体保留进快照字段供匹配硬约束使用；缺失成色=unknown；标题相似不产生 EXACT 由既有 product_matching 硬约束保证（回归全绿） |
| A53 | PASS (S) | 分页去重、空页≠失败、401 单次调用即停、429 重试后 degraded 且部分结果保存、resume_token 断点续取并跳过已见 ID |
| A54 | PASS (S) | 上游旧时间为观察时间（5 天前→freshness expired，不复活）；同内容缓存重读返回同一存储记录（无假更新）；未来上游时间回退抓取时间并注明 |
| A55 | PASS (H) | GET status 诚实应答；POST fetch 无客户端 409+人工回退指引；人工观察路由可用且 provider 不同 |

## 命令与退出码

```text
unittest discover -p test_market_channel.py   # 15/15 OK
powershell scripts/run_tests_isolated.ps1     # 1551/1551, 350.844s, exit 0
```

live 20 文件前后哈希一致；零真实平台请求（全部为合成通道客户端）。

## 已知边界

1. `_market_channel_client()` 返回 None：真实 browser_adapter 读客户端需账号授权与读路径能力核验后接入（R 层）；接入点已留好（探针/会话均以客户端协议注入）。
2. 旧 `_parse_product` 的 `soldPrice` 兜底属旧自有商品同步路径（非市场观察通道），市场通道模块已按新纪律实现；旧路径留待统一预算轮处理。
3. 每来源配额/刷新间隔/退避参数需真实合同配置（A46），当前仅结构就绪。
4. A55 U 层（面板自动通道状态卡片）待真实通道接入时一并呈现。

## 需要用户提供的精确输入（R 层）

1. 一个获权读通道：指定账号登录 + 允许的只读查询范围与额度（先少量只读探针）。
2. 对首批真实响应的人工逐条核对结果（价格含义/规格/成色/时间）。

## 回退方案

无客户端时两个路由均为诚实只读应答，无行为副作用；删除 market_channel.py 与两路由即回 G2A 状态。

## 下一工作包

G2C 首张真实商机卡（本包先补决策卡资料状态完整性，见后续报告；真实卡 WAITING_INPUT 真实报价+观察）。
