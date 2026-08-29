# 本地参考项目学习路线图

Updated: 2026-06-30

## 目标

本文回答一个问题：

> 本地这些闲鱼相关项目，相比当前主项目 `E:\闲鱼\xianyu`，分别有什么值得学习的地方？应该如何学习它们来改进我的项目？

结论先说：

当前主项目的优势是“总控架构更好”：OpenClaw 路由、专职 agent、WorkOrder、Approval、ExecutionJobQueue、ActionExecutor、OPC Dashboard 都已经有了。

本地参考项目的优势是“实战细节更多”：账号在线状态、token/cookie 刷新、WebSocket 心跳、人工接管、多账号生命周期、订单状态、线索收件箱、爬虫分层、UI 自动化冷却等。

所以学习方式不是把别人的项目合并进来，而是：

```text
别人项目的实战细节
-> 抽象成主项目的模块/字段/测试
-> 接入现有 WorkOrder / Approval / Dashboard
-> 形成一人公司可持续运行的系统
```

## 1. 总体对比

| 项目 | 别人项目强在哪里 | 你的主项目强在哪里 | 应该学习什么 | 不该学什么 |
| --- | --- | --- | --- | --- |
| `xianyuapis-main` | 闲鱼 WebSocket/token/message 最小链路清楚 | 有审批、队列、Dashboard | 协议接入、消息 envelope、心跳字段 | 直接收消息后直接发送 |
| `xianyu-auto-reply-main` | 真实运营功能全，账号/订单/通知/默认回复完整 | 架构更干净，风险动作有队列 | 账号状态、订单状态机、暂停、通知、回复资产 | 巨型单体、直接自动发货 |
| `mulitXianYuAugent-main` | 多账号 runtime 和 per-account agent 拆分好 | 有更完整业务闭环和 OPC 经营框架 | MultiAccountManager、AccountManager、prompt/context | 买家 bot、直接发送动作 |
| `XianyuAuto-master` | 手机 UI 操作路径和冷却经验 | 有 browser adapter 和执行队列 | UI fallback、cooldown、截图证据、blocker 检测 | 硬编码手机脚本 |
| `fastPickUp-master` | 外部事件 -> inbox -> 未读 -> 通知模式简单 | 有候选品、评分、复盘 | discovery inbox | Java 技术栈和抢拍逻辑 |
| `LittleCrawler-main` | 爬虫分层清楚：platform/client/extractor/storage | 有选品评分和上架验证闭环 | discovery crawler 架构 | 当成现成闲鱼爬虫 |

---

## 2. `xianyuapis-main`：学习“最小可用闲鱼协议适配器”

### 相比你的项目，它有什么优势

你的主项目已经有 `xianyu_agent/xianyu_ws.py`、`message_intake.py`、`browser_adapter.py`，但偏工程骨架，还缺一个经过真实链路启发的“账号 runtime 字段模型”。

`xianyuapis-main` 的优势是：

- 一个 `XianyuLive` 类打通 Cookie、token、WebSocket、心跳、消息、回复 API。
- 入站消息字段很清楚：`msg_time`、`send_user_id`、`send_user_name`、`item_id`、`send_message`、`chat_id`。
- 有 token refresh loop。
- 有 WebSocket heartbeat loop。
- 有手动模式概念。

### 应该学习什么

学习它的“协议边界”，不要学习它的“直接执行”。

可学习点：

1. 标准化入站消息 schema。
2. 账号 token/cookie 新鲜度字段。
3. WebSocket 心跳字段。
4. 消息 ACK / sync package 的处理概念。
5. 手动接管状态。

### 改进你的项目

建议落地到：

| 改造点 | 文件/模块 |
| --- | --- |
| 标准入站消息模型 | `xianyu_agent/message_intake.py` |
| WebSocket adapter contract | `xianyu_agent/xianyu_ws.py` |
| 账号运行时状态 | 新增 `xianyu_agent/account_runtime.py` |
| 首次运行前检查 | `xianyu_agent/first_run_readiness.py` |
| 控制台状态展示 | `web/app.js`、`web/style.css` |

### 建议任务

```text
新增 account_runtime_state：
- cookie_id
- connection_status
- last_heartbeat_at
- last_message_at
- last_token_refresh_at
- last_cookie_refresh_at
- token_fresh
- cookie_fresh
- reconnect_count
- last_error
- manual_takeover_chats
- pending_verification_reason
```

优先级：P0。

---

## 3. `xianyu-auto-reply-main`：学习“真实运营系统会长出哪些模块”

### 相比你的项目，它有什么优势

你的项目架构更适合长期演进，但 `xianyu-auto-reply-main` 的实战功能更全。它已经处理了很多真实运营会遇到的问题：

- Cookie 多账号管理。
- 账号启用/停用。
- AI 回复设置。
- 默认回复。
- 商品级回复。
- 订单状态。
- 卡密/发货规则。
- 通知渠道。
- 风控日志。
- token cache。
- 暂停/人工接管。

这说明真正运营起来之后，系统会自然长出“运营资产库”和“运行时可观测性”。

### 应该学习什么

重点学习 4 类东西：

1. 账号状态：账号是否在线、是否启用、是否暂停、是否需要验证。
2. 订单状态：订单不是一个字段，而是状态机。
3. 回复资产：默认回复、商品回复、关键词回复应沉淀成资产。
4. 通知/日志：异常要进入操作者视野。

### 改进你的项目

| 改造点 | 文件/模块 |
| --- | --- |
| 账号 runtime readiness | 新增 `xianyu_agent/account_runtime.py` |
| 订单状态机增强 | `xianyu_agent/order_status_handler.py` |
| 履约幂等锁 | `xianyu_agent/action_executor.py`、`xianyu_agent/execution_queue.py` |
| 回复模板资产 | 新增 `data/assets/reply_templates.json` 或 asset store |
| 商品级回复策略 | `xianyu_agent/auto_reply.py`、`conversation_profiles.py` |
| Dashboard alert | `xianyu_agent/opc_dashboard.py`、`web/app.js` |

### 建议任务

先不要做“全自动发货”。先做：

```text
订单状态机：
- pending_payment
- paid
- pending_shipment
- shipped
- delivered
- refund_requested
- disputed
- closed

每个订单增加：
- last_status_at
- last_operator_action
- fulfillment_lock
- pending_blocker
- history
```

优先级：P1。

---

## 4. `mulitXianYuAugent-main`：学习“多账号运行时”

### 相比你的项目，它有什么优势

这是最值得学习的项目之一。

你的主项目有账号 profile、账号队列、prompt profile，但还缺“活着的账号运行时”。`mulitXianYuAugent-main` 的结构刚好补这个短板：

```text
MultiAccountManager
-> AccountManager per account
-> heartbeat/token refresh/WebSocket
-> chat context
-> seller/buyer agent
-> web server controls
```

它把“账号”当成一个运行中的对象，而不只是数据库里的一条配置。

### 应该学习什么

重点学习：

1. `MultiAccountManager`：管理所有账号生命周期。
2. `AccountManager`：每个账号一个 runtime。
3. `get_status()`：每个账号随时能输出状态快照。
4. per-account prompt：不同账号可以有不同话术策略。
5. chat context：聊天历史、议价次数、会话阶段。
6. retry backoff：账号出错后不要无限重试。

### 改进你的项目

| 改造点 | 文件/模块 |
| --- | --- |
| 多账号 runtime store | 新增 `xianyu_agent/account_runtime.py` |
| 账号启动/停止控制 | `xianyu_agent/server.py` 后续新增 runtime API |
| 账号状态进入队列看板 | `xianyu_agent/account_ops_queues.py` |
| 账号 prompt profile 加强 | `xianyu_agent/account_prompt_profiles.py` |
| 会话议价次数/阶段 | `xianyu_agent/conversation_profiles.py` |
| 回复策略使用会话状态 | `xianyu_agent/auto_reply.py` |

### 建议任务

第一步不要急着写真正 WebSocket 连接管理器，先做 runtime state 和 API：

```text
GET /accounts/runtime
POST /accounts/{cookie_id}/runtime/heartbeat
POST /accounts/{cookie_id}/runtime/token-refresh
POST /accounts/{cookie_id}/runtime/error
POST /accounts/{cookie_id}/runtime/manual-takeover
```

优先级：P0。

---

## 5. `XianyuAuto-master`：学习“UI 自动化为什么必须保守”

### 相比你的项目，它有什么优势

这个项目很旧，代码不可直接用，但它证明了一件事：有些闲鱼动作最终可能只能靠 UI 自动化完成。

它的价值是经验：

- 从 App 首页进入闲鱼。
- 找“我的”。
- 找“我发布的”。
- 进入商品编辑。
- 确认发布/刷新。
- 操作太快要等待。

### 应该学习什么

学习“UI 自动化的防御性设计”：

1. 每步动作前确认页面状态。
2. 每步动作后保存证据。
3. 操作之间加 cooldown。
4. 页面不符合预期时进入 blocker，而不是继续乱点。
5. UI 自动化只执行队列里已批准的动作。

### 改进你的项目

| 改造点 | 文件/模块 |
| --- | --- |
| 浏览器动作前后截图 | `xianyu_agent/browser_adapter.py` |
| 页面 blocker 检测 | `xianyu_agent/browser_adapter.py` |
| 执行冷却策略 | `xianyu_agent/action_executor.py` |
| 失败证据进入 dashboard | `xianyu_agent/execution_queue.py`、`opc_dashboard.py` |
| 控制台显示失败截图/原因 | `web/app.js` |

### 建议任务

```text
给 ExecutionJob 增加 evidence：
- before_screenshot
- after_screenshot
- page_url
- detected_blocker
- cooldown_seconds
- operator_next_action
```

优先级：P2。

---

## 6. `fastPickUp-master`：学习“线索收件箱”

### 相比你的项目，它有什么优势

你的项目已经有候选品、评分和复盘，但“外部线索进入系统”的入口还可以更清晰。

`fastPickUp-master` 的模式非常简单：

```text
外部事件
-> 保存 raw params
-> 未读
-> 邮件提醒
-> 操作者查看
-> 标记已读
```

这个模式可以直接变成你的 discovery inbox。

### 应该学习什么

学习 inbox 模式：

1. 先保存 raw event，不急着结构化。
2. 每条线索有 unread/read 状态。
3. 每条线索可 promote 成 candidate。
4. 每条线索保留来源。
5. Dashboard 告诉你今天有哪些新线索。

### 改进你的项目

| 改造点 | 文件/模块 |
| --- | --- |
| 发现线索 inbox | 新增 `xianyu_agent/discovery_inbox.py` |
| 外部线索 API | `xianyu_agent/server.py` |
| 线索转候选品 | `xianyu_agent/resale.py`、`product_discovery.py` |
| Dashboard 新线索卡片 | `xianyu_agent/opc_dashboard.py`、`web/app.js` |
| 每周复盘记录线索来源 | `xianyu_agent/opc_dashboard.py` |

### 建议任务

```text
新增 DiscoveryEvent：
- event_id
- source
- raw_payload
- title
- summary
- category_hint
- supplier_hint
- price_hint
- status: unread/read/promoted/rejected
- created_at
- promoted_candidate_id
```

优先级：P1。

---

## 7. `LittleCrawler-main`：学习“发现层爬虫架构”

### 相比你的项目，它有什么优势

你的项目已经有选品逻辑和 ProductScorer，但如果后续要做规模化市场发现，需要一个清晰的 crawler 分层。

`LittleCrawler-main` 的结构很值得学：

```text
platform
-> core crawler
-> client
-> login
-> extractor
-> storage
-> cleanup
```

本地副本不是闲鱼爬虫，但它的框架可以作为未来 discovery crawler 的模板。

### 应该学习什么

学习分层，不学习具体平台签名：

1. 平台适配器独立。
2. HTTP/client 和 extractor 分开。
3. 原始数据和结构化数据分开。
4. 存储层可替换。
5. 爬取结束有 flush/cleanup。

### 改进你的项目

| 改造点 | 文件/模块 |
| --- | --- |
| discovery platform adapter | 新增 `xianyu_agent/discovery_platforms/` |
| crawler client | `xianyu_agent/discovery_platforms/*/client.py` |
| extractor | `xianyu_agent/discovery_platforms/*/extractor.py` |
| raw event storage | `xianyu_agent/discovery_inbox.py` |
| score -> candidate | `xianyu_agent/product_discovery.py`、`resale.py` |

### 建议任务

先不要做复杂爬虫，先定义接口：

```text
class DiscoverySource:
    def search(keyword) -> list[RawDiscoveryEvent]
    def normalize(raw_event) -> DiscoveryEvent
```

优先级：P3。

---

## 8. 如何学习别人项目来改进你的项目

### 原则 1：不搬代码，搬“问题模型”

不要问“这段代码能不能复制”，而要问：

```text
它解决了什么真实问题？
这个问题在我的系统里属于哪一层？
我的系统已有哪个模块应该吸收它？
```

例子：

- 别人有 `AccountManager`，不是让你复制文件，而是说明你需要 `account_runtime_state`。
- 别人有默认回复表，不是让你复制 SQLite，而是说明你需要“回复资产库”。
- 别人有 Fiddler inbox，不是让你做 Java 服务，而是说明你需要 discovery inbox。

### 原则 2：先写测试，再吸收设计

每次学习一个项目，都转成测试：

```text
参考项目发现的问题
-> 当前项目应该具备的行为
-> 新测试
-> 新模块/字段/API
```

例子：

```text
参考项目有 heartbeat loop
-> 当前项目 Dashboard 应显示 last_heartbeat_at 和连接状态
-> tests/test_account_runtime.py
-> xianyu_agent/account_runtime.py
```

### 原则 3：所有外部能力都要接入你的主干

你的主干是：

```text
OpenClawRouter
-> Agent
-> WorkOrder
-> Approval
-> ExecutionJobQueue
-> ActionExecutor
-> Dashboard
```

学来的东西必须接到这条主干上。否则会变成一堆分散脚本。

### 原则 4：先补运行时，再补自动化

当前最重要的不是更聪明的 agent，而是更可靠的运行系统：

```text
账号是否在线？
Cookie 是否新鲜？
WebSocket 是否有心跳？
消息有没有进来？
执行失败在哪里？
操作者今天该处理什么？
```

这些问题解决后，自动化才有意义。

### 原则 5：每个参考项目只学一个主能力

| 项目 | 只学一个主能力 |
| --- | --- |
| `xianyuapis-main` | 协议/消息接入 |
| `xianyu-auto-reply-main` | 运营状态和订单状态 |
| `mulitXianYuAugent-main` | 多账号 runtime |
| `XianyuAuto-master` | UI fallback 防御性执行 |
| `fastPickUp-master` | discovery inbox |
| `LittleCrawler-main` | crawler 分层 |

---

## 9. 推荐改造优先级

### P0：账号运行时层

最先做，因为所有真实运行都依赖它。

```text
新增：
- xianyu_agent/account_runtime.py
- tests/test_account_runtime.py
- GET /accounts/runtime

增强：
- account_ops_queues.py
- first_run_readiness.py
- web/app.js
```

学习来源：

- `xianyuapis-main`
- `mulitXianYuAugent-main`
- `xianyu-auto-reply-main`

### P1：订单状态机 + Discovery Inbox

让系统既能处理交易后半段，也能持续发现新机会。

```text
增强：
- order_status_handler.py
- action_executor.py

新增：
- discovery_inbox.py
- GET /discovery/inbox
- POST /discovery/inbox/{id}/promote-candidate
```

学习来源：

- `xianyu-auto-reply-main`
- `fastPickUp-master`

### P2：执行证据和 UI fallback

让每个执行任务失败时都知道为什么。

```text
增强：
- browser_adapter.py
- execution_queue.py
- opc_dashboard.py
- web/app.js
```

学习来源：

- `XianyuAuto-master`

### P3：发现层 crawler adapter

让外部趋势、搜索、供应商线索以统一格式进入系统。

```text
新增：
- xianyu_agent/discovery_platforms/
- DiscoverySource interface
- RawDiscoveryEvent
```

学习来源：

- `LittleCrawler-main`

### P4：运营资产库

让每周复盘沉淀成可复用资产。

```text
新增：
- data/assets/reply_templates.json
- data/assets/title_patterns.json
- data/assets/supplier_notes.json

增强：
- auto_reply.py
- listing.py
- opc_dashboard.py
```

学习来源：

- `xianyu-auto-reply-main`
- `mulitXianYuAugent-main`

---

## 10. 可直接交给 ClaudeCode / OpenCode 的话

```text
继续 E:\闲鱼\xianyu。

先读：
1. docs/PROJECT_PLAN.md
2. docs/research/local-xianyu-tools-audit.md
3. docs/research/local-xianyu-program-architecture.md
4. docs/research/local-project-learning-roadmap.md

任务：按 local-project-learning-roadmap.md 的 P0 实现“账号运行时层”。

学习参考：
- xianyuapis-main 学消息接入、token/cookie、WebSocket 心跳字段。
- mulitXianYuAugent-main 学 MultiAccountManager / AccountManager / get_status。
- xianyu-auto-reply-main 学账号状态和暂停/人工接管。

不要复制参考项目代码。把参考项目能力抽象成当前项目模块。

实现要求：
1. 新增 tests/test_account_runtime.py。
2. 新增 xianyu_agent/account_runtime.py。
3. 支持 account_runtime_state 的创建、更新、查询、JSON 持久化。
4. 在 server.py 增加 GET /accounts/runtime。
5. 在 account_ops_queues.py 合并 runtime state。
6. 在 first_run_readiness.py 增加 runtime gates。
7. 在 web/app.js / web/style.css 展示账号 runtime badge。

验收：
.\.venv\Scripts\python.exe -m unittest tests.test_account_runtime tests.test_first_run_readiness tests.test_account_ops_queues tests.test_console_ui_contract -v
node --check .\web\app.js
git diff --check
```

