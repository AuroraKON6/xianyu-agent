# 闲鱼自动化落地接入手册

更新时间：2026-06-12

## 当前结论

本项目的落地执行层优先使用闲管家/第三方开放接口，浏览器自动化只作为备用执行层，`stub` 作为本地排练层。

推荐顺序：

1. `stub`：本地生成草稿、审批单、执行负载，不触达真实平台。
2. `xianguanjia`：通过闲管家类 API 执行上架、擦亮、发货。
3. `browser`：只有 API 缺口无法覆盖时再用本机浏览器适配器补位。

OpenClaw 暂时作为多 agent 编排和任务路由，不在本阶段改核心代码。

## 已可使用能力

- `XianGuanJiaAdapter.publish()`：把结构化草稿提交到创建商品接口。
- `XianGuanJiaAdapter.polish()`：把商品 ID 提交到擦亮接口。
- `XianGuanJiaAdapter.fulfill()`：把订单信息提交到发货接口。
- `xianguanjia_health()`：输出不含密钥的配置健康检查。
- `python -m xianyu_agent.cli config-check`：展示当前适配器、闲管家配置、鉴权模式和接口路径。

## 环境变量

最小配置：

```env
XIANYU_ADAPTER=xianguanjia
XIANGUANJIA_BASE_URL=https://your-provider.example.com
XIANGUANJIA_TOKEN=
XIANGUANJIA_APP_ID=
XIANGUANJIA_API_KEY=
XIANGUANJIA_CREATE_ENDPOINT=/api/listings
XIANGUANJIA_POLISH_ENDPOINT=/api/listings/{listing_id}/polish
XIANGUANJIA_FULFILL_ENDPOINT=/api/orders/{order_id}/fulfill
```

如果对方文档写的是 `appKey/appSecret`，也可以这样填：

```env
XIANGUANJIA_APP_KEY=your-app-key
XIANGUANJIA_APP_SECRET=your-app-secret
```

系统会把它们作为 `XIANGUANJIA_APP_ID` 和 `XIANGUANJIA_API_KEY` 的别名读取。

## 自检命令

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli config-check
.\.venv\Scripts\python -m unittest tests.test_xianguanjia_adapter -v
```

`config-check` 里重点看：

- `adapter` 是否为 `xianguanjia`
- `xianguanjia.ready` 是否为 `true`
- `xianguanjia.missing` 是否为空
- `xianguanjia.auth_modes` 是否包含 `bearer_token` 或 `legacy_sha1_headers`

## 需要用户提供

继续接真实闲鱼执行层前，需要你提供这些信息：

1. 闲管家/第三方开放接口的后台地址或文档链接。
2. `BASE_URL`，例如 `https://api.xxx.com`。
3. 鉴权参数：`TOKEN`，或 `APP_ID/API_KEY`，或 `APP_KEY/APP_SECRET`。
4. 创建商品、擦亮商品、发货接口的真实路径。
5. 一份脱敏后的成功请求/响应样例。

拿到这些后，下一步是补真实字段映射测试：把 `ListingDraft` 转成对方 API 要求的 payload，把响应里的商品 ID 映射回 `PublishResult.external_id`。

## 给 ClaudeCode 的下一步

从这里继续：

```text
工作目录：E:\闲鱼\xianyu

先不要改 OpenClaw 核心。继续完成闲管家/第三方 API 落地：
1. 阅读 docs/ops/xianyu/automation-integration.md、xianyu_agent/adapters.py、tests/test_xianguanjia_adapter.py。
2. 等用户提供 BASE_URL、鉴权方式、create/polish/fulfill 接口路径和脱敏样例。
3. 用 TDD 新增字段映射测试，不要直接真实发请求。
4. 实现 ListingDraft -> provider payload 映射，以及 provider response -> PublishResult 映射。
5. 更新 .env.example 和本手册。
6. 跑 tests.test_xianguanjia_adapter 和相关 CLI/config 测试。
```

## 参考项目吸收进度

已新增账号画像 API：

```http
GET /accounts/profiles
```

用途：

- 汇总每个闲鱼账号的启用状态、关键词规则数、AI 回复配置、商品数、订单数和能力状态。
- 给出每个账号的下一步运营建议。
- 不返回原始 Cookie、API Key 或其他密钥。

相关文件：

- `xianyu_agent/account_profiles.py`
- `tests/test_account_profiles.py`
- `docs/research/github-reference-projects.md`
- `docs/superpowers/plans/2026-06-12-github-reference-integration.md`

## 商品/订单同步预览

已新增统一适配器契约：

```python
adapter.sync_orders(cookie_id="default", status="")
adapter.sync_my_listings(cookie_id="default")
```

当前可用接口：

```http
GET /adapter/sync-preview?cookie_id=default&status=
```

用途：

- 通过当前 `XIANYU_ADAPTER` 预览订单和已发布商品同步结果。
- `browser` 适配器委托已有的 `get_orders()` 和 `get_my_listings()`。
- `xianguanjia` 适配器预留 `/api/orders` 和 `/api/listings` GET 调用。
- `stub` 适配器返回空结果和 `not implemented`，用于本地排练。

边界：

- 该接口只读，不会写入数据库。
- 不会发布、擦亮、发货、退款、删除或发送消息。
- 下一步在真实接口验证后，再做 sync-and-persist 工作流，把外部商品/订单映射进本地 dashboard。

## 发布字段映射层

已新增 `XianGuanJiaAdapter._build_listing_payload()` 和 `_extract_publish_external_id()`：

- `ListingDraft` 会先映射成 provider-facing payload，再提交给创建商品接口。
- payload 显式包含 `title`、`category`、`price`、`stock`、`description`、`tags`、`images`、`delivery`、`source` 等字段。
- `delivery` 同时保留结构化子对象和兼容性的 `delivery_type` / `delivery_payload`。
- 发布响应支持从顶层或 `data` / `result` / `item` / `listing` / `goods` 等嵌套对象提取 `external_id`。
- 测试覆盖：`tests.test_xianguanjia_adapter`。

拿到真实闲管家/第三方接口样例后，优先改这个映射层和对应测试，而不是改 WorkOrder、Approval 或 ExecutionJobQueue 主流程。
