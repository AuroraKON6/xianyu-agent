# 闲鱼审批驱动渠道 Agent 工作流

这个项目是一个本地闲鱼/OpenClaw 多 Agent 操作系统。OpenClaw、控制台或飞书 CLI 长连接消息进来后，本地工具层可以整理商品信息、生成草稿、创建任务单、进入审批和执行队列。当前定位是审批驱动的渠道执行适配层，不是无审批自动上架系统；高风险动作必须先进入 `WorkOrder` + `Approval`，再由人工确认后才允许显式排队执行。

## 已搭好的能力

- `DeepSeekClient`：OpenAI-compatible chat completions，后续只填 `.env`。
- `飞书 CLI 长连接`：通过飞书 CLI 建立长连接，事件自动桥接到本地 `/feishu/events`，无需公网地址。
- `Xianyu adapter`：默认 stub，预留闲管家 HTTP adapter。
- `发货审批流`：支持数字商品交付草稿；实体商品返回待人工物流处理，高风险发货动作仍走审批和队列。
- `每日擦亮`：服务启动后按 `POLISH_AT` 定时调用 adapter。
- `每日 GitHub 机会雷达`：服务启动后按 `GITHUB_DISCOVERY_AT` 自动扫描高星项目，只生成报告和建议。
- `GitHub 机会雷达`：按 stars 搜索资料/agent 项目，并让 DeepSeek V4 判断能否合法产品化或作为服务售卖。
- `虚拟产品比价 Agent`：评估课程/资料/知识产品是否有授权、能否回本、利润空间和市场风险，通过飞书问你是否购入。
- `URL 下载候选爬虫`：你发一个网址，自动提取可下载候选，评估闲鱼利润空间；只有授权明确时才下载到本地。
- `风控守卫`：不绕过验证码/人机验证；自动限速，遇到验证或风控提示就暂停，并通过飞书通知你手动处理。
- `OpenClaw skill`：`skills/xianyu-commerce-agent/SKILL.md`，可作为 OpenClaw agent 的技能说明。
- `OpenClaw manifest`：`openclaw/xianyu-commerce-agent.openclaw.yaml`，把 DeepSeek V4、飞书和本地工具层串起来。
- `ecommerce 桥接入口`：`POST /integrations/ecommerce/listing-drafts` 接收 ecommerce 候选商品，只创建闲鱼草稿审批，不发布、不排队、不执行。
- `图片策略`：默认只搜索参考候选，不把第三方商品图当作自有图片发布；可接入授权图库或自己的图片源。
- `商品发现 Agent`：Playwright 浏览器自动化 + API 拦截，搜索闲鱼商品并用 AI 分析市场机会。支持定时监控、价格区间过滤、飞书通知。
- `多专家自动回复`：基于意图分类的 AI 客服系统。关键词规则优先 → AI 兜底。支持价格/技术/默认/不回复四类意图路由，安全过滤拦截平台外联系方式。
- `WebSocket IM 实时聊天`：直连闲鱼 IM 服务 `wss://wss-goofish.dingtalk.com/`，实时接收消息并自动回复。支持心跳保活、Token 刷新、消息解密。
- `AI 客服自动回复`：基于 DeepSeek V4 Flash 的智能客服系统，支持意图分类、安全过滤和多轮对话。

## 快速启动

```powershell
cd E:\闲鱼\xianyu
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 和 MIMO_API_KEY
.\.venv\Scripts\python -m xianyu_agent.cli config-check
.\.venv\Scripts\python -m xianyu_agent.cli server
```

服务默认启动在 `http://localhost:8788`。如果你的机器上端口不同，只改 `.env` 里的 `SERVER_PORT`。

启动 WebSocket 自动回复：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli ws-start --cookies "你的cookies"
```

也可以用一键脚本启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_xianyu_workflow.ps1
```

打开本地控制台：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\open_xianyu_console.ps1
```

控制台地址：

```text
http://127.0.0.1:8788/console
```

## OpenClaw 多 Agent 协作

OpenClaw 现在是总入口，6 个专业 agent 通过 `AgentRegistry` 可以相互发消息和派发任务。普通明确指令会由 OpenClaw 路由到单个 agent；带 `协作` 的上架指令会让 `listing` 主动向其他 agent 请求上下文。

协作上架示例：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agent/message -ContentType 'application/json; charset=utf-8' -Body '{"text":"协作上架：蓝牙耳机，成本70，售价98，库存3，成色很新","publish":false}'
```

当前协作链路：

- `listing` → `discovery`：获取市场/品类建议。
- `listing` → `analytics`：计算利润和定价建议。
- 后续可以继续扩展：客服查订单、订单请求客服话术、GitHub 机会交给分析和上架 agent。

观察 agent 之间通信：

```powershell
Invoke-RestMethod http://127.0.0.1:8788/agents/collaboration
Invoke-RestMethod http://127.0.0.1:8788/agents/messages
```

让多个 agent 自己开圆桌讨论：

```powershell
# 控制台也可以直接输入：讨论：如何上架蓝牙耳机，成本70，售价98
Invoke-RestMethod -Method Post http://127.0.0.1:8788/agents/discuss -ContentType 'application/json; charset=utf-8' -Body '{"topic":"如何上架蓝牙耳机，成本70，售价98","participants":["listing","discovery","analytics"],"rounds":2}'
Invoke-RestMethod http://127.0.0.1:8788/agents/discussions
```

Agent 间消息和圆桌讨论会追加保存到 `data/agent_collaboration.jsonl`；服务重启后 `/agents/messages` 和 `/agents/discussions` 仍能读取历史。控制台 `http://127.0.0.1:8788/console` 的“协作记录”面板也会展示最近通信和讨论摘要。

## 现实工作流任务单

为了更接近真实团队协作，协作上架现在会创建 `WorkOrder` 任务单，并在圆桌讨论后生成 `ActionPlan`。如果任务里包含发布、发货、删除、退款、敏感回复等高风险动作，会自动生成 `Approval`，进入人工确认状态；例如用户勾选“允许提交”时，系统会先生成草稿和审批请求，不会绕过人工确认直接发布。

常用接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8788/work-orders

$order = Invoke-RestMethod -Method Post http://127.0.0.1:8788/work-orders -ContentType 'application/json; charset=utf-8' -Body '{"kind":"listing","title":"协作上架蓝牙耳机","owner":"listing","context":{"price":98,"cost":70}}'

Invoke-RestMethod -Method Post "http://127.0.0.1:8788/work-orders/$($order.work_order_id)/plan" -ContentType 'application/json; charset=utf-8' -Body '{"proposed_actions":[{"agent":"listing","action":"publish_listing","description":"发布商品"}]}'

Invoke-RestMethod -Method Post http://127.0.0.1:8788/approvals/{approval_id}/decision -ContentType 'application/json; charset=utf-8' -Body '{"decision":"approved","note":"人工确认可以执行"}'
```

任务单会保存到 `data/work_orders.json`，控制台的“工作流任务单”面板会展示最近任务、行动项数量和待审批数量。

## 与 ecommerce-openclaw-system 的桥接定位

合并推进时，`ecommerce-openclaw-system` 是候选池、供应商、样品验证、放量、财务和经营总账的 source of truth；本项目只作为闲鱼渠道/执行适配层，保存闲鱼 WorkOrder、Approval、ExecutionJob 和浏览器适配记录。

本项目新增 `POST /integrations/ecommerce/listing-drafts`，用于接收 ecommerce 候选商品快照并创建 `ecommerce_candidate` 任务单。该接口只生成 `publish_listing` 的 pending Approval，不会自动发布、自动发消息、自动下单、自动付款、自动退款，也不会绕过验证码或平台风控。

## 浏览器电脑端发布确认

当 `.env` 中设置 `XIANYU_ADAPTER=browser` 且 `BROWSER_CDP_URL=http://127.0.0.1:9222` 时，服务会复用你已登录的 Edge/Chrome 页面执行电脑端操作。

常用检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8788/browser/status
Invoke-RestMethod http://127.0.0.1:8788/browser/publish/confirm
```

发布过程中如果平台要求手机认证，程序不会绕过验证；完成认证后，保持浏览器停在商品详情页，再调用 `/browser/publish/confirm`，系统会从当前 URL 识别 `item id`，避免“实际发布成功但接口误判失败”。

长期记忆保存在 `data/memory/profile.json`。你可以在控制台输入 `记忆：...`，或在右侧记忆区保存偏好。

## 飞书接入

通过飞书 CLI 长连接接入，事件自动桥接到本地服务，无需公网地址。

飞书 CLI 负责登录、应用绑定和长连接事件订阅。长连接收到的事件由飞书 CLI 自动桥接到本地 FastAPI 的 `/feishu/events` 入口。

启动本地服务后，飞书消息里直接发商品描述即可，例如：

```text
上架：iPhone 15 Pro 手机壳，黑色磨砂，库存 50，售价 19.9，自动发货内容：下单后发送领取链接 https://example.com/card
```

也可以直接调用本地接口：

```powershell
Invoke-RestMethod -Method Post http://localhost:8788/listings -ContentType 'application/json' -Body '{"text":"上架：蓝牙耳机，库存20，售价59，适合通勤和运动"}'
```

## 闲管家切换

拿到闲管家接口后，在 `.env` 里切换：

```text
XIANYU_ADAPTER=xianguanjia
XIANGUANJIA_BASE_URL=https://your-xianguanjia-api.example.com
XIANGUANJIA_TOKEN=...
```

如果闲管家的路径和示例不同，只改 `XIANGUANJIA_CREATE_ENDPOINT`、`XIANGUANJIA_POLISH_ENDPOINT`、`XIANGUANJIA_FULFILL_ENDPOINT` 即可。

我自动检索到的公开接入线索：

- 闲管家/晓晓权益自研系统网关：`https://hy.ziyuanting.com/`
- 签名 Header：`Sign`、`Timestamp`、`UserId`
- 签名规则：`sha1(timestamp + bodyJson + apikey)`
- 当前适配器已支持 `XIANGUANJIA_APP_ID` / `XIANGUANJIA_API_KEY` 签名鉴权

`.env` 示例：

```text
XIANYU_ADAPTER=xianguanjia
XIANGUANJIA_BASE_URL=https://hy.ziyuanting.com
XIANGUANJIA_APP_ID=你的appid
XIANGUANJIA_API_KEY=你的apikey
```

## GitHub 机会雷达

寻找高星资料或可产品化 agent：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli discover-github --query "ai agent" --query "workflow automation" --min-stars 1000 --limit 5
```

本地 API：

```powershell
Invoke-RestMethod -Method Post http://localhost:8788/research/github -ContentType 'application/json' -Body '{"queries":["ai agent","automation agent"],"min_stars":1000,"limit":3,"use_deepseek":true}'
```

结果会保存到 `data/research`。判断逻辑会区分：

- 可售卖：部署、定制、集成、培训、托管服务、原创衍生 agent。
- 谨慎：GPL/AGPL/MPL 等许可证项目，通常不能闭源打包转卖。
- 不建议：无许可证、侵权风险高、只适合作为学习资料的项目。

GitHub token 不是必须，但建议填入 `.env` 的 `GITHUB_TOKEN`，否则 GitHub API 频率限制较低。

每日自动扫描配置：

```text
GITHUB_DISCOVERY_ENABLED=true
GITHUB_DISCOVERY_AT=10:30
```

OpenClaw 挂载配置在：

```text
openclaw/xianyu-commerce-agent.openclaw.yaml
```

本机 OpenClaw 源码目录接入：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_openclaw_integration.ps1 -OpenClawRoot E:\openclaw\openclaw-main
```

已同步到：

```text
E:\openclaw\openclaw-main\skills\xianyu-commerce-agent
E:\openclaw\openclaw-main\.agents\skills\xianyu-commerce-agent
```

本机 OpenClaw 已完成依赖安装和构建，`node E:\openclaw\openclaw-main\openclaw.mjs --help` 可正常启动。全局配置文件 `C:\Users\轻音\.openclaw\openclaw.json` 已设置：

```text
agents.defaults.model.primary = deepseek/deepseek-v4-pro
agents.defaults.skills = ["xianyu-commerce-agent"]
skills.load.extraDirs = ["E:/闲鱼/xianyu/skills"]
```

可用下面命令复查：

```powershell
node E:\openclaw\openclaw-main\openclaw.mjs config validate
node E:\openclaw\openclaw-main\openclaw.mjs skills info xianyu-commerce-agent
node E:\openclaw\openclaw-main\openclaw.mjs infer model run --local --model deepseek/deepseek-v4-flash --prompt "只回复：OK"
```

检查本机 OpenClaw 相关目录是否接入完整：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli components-check
```

该检查覆盖：

- `E:\openclaw\openclaw-main`：OpenClaw core、gateway、`xianyu-commerce-agent` skill。
- `E:\openclaw-control-center-main\openclaw-control-center-main`：Control Center 源码。
- `E:\openclaw-mission-control-master\openclaw-mission-control-master`：Mission Control approvals/task backend。
- `E:\OpenClaw-RL-main\OpenClaw-RL-main`：RL 实验/评测环境；只用于离线训练和评估，不直接控制真实闲鱼账号。
- `E:\awesome-openclaw-skills-main\awesome-openclaw-skills-main`：可融合 skills 索引。

如果本机代理导致 GitHub 请求失败，可以在 `.env` 里设：

```text
GITHUB_TRUST_ENV=false
```

DeepSeek 请求如果也被系统代理影响，可以保持：

```text
DEEPSEEK_TRUST_ENV=false
```

## 虚拟产品比价 Agent

这个流程只处理你有权转售的虚拟产品：自有产品、供应商授权、PLR/MRR、明确可转售许可、公开许可、联盟分销等。没有授权的课程、网课、资料包、卖家宣传图和卖家文案不会自动进入上架流程。

CLI 评估：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli evaluate-product "AI 提示词资料包，适合自媒体运营" --cost 99 --price 39 --rights-type resale_license --rights-evidence "供应商授权截图/合同已保存" --market-sample "闲鱼同类资料 49元"
```

飞书触发：

```text
比价：AI 提示词资料包，源售价99，计划卖39，已有供应商转售授权
```

Agent 会通过飞书询问是否购入，回复：

```text
购入 机会ID
拒绝 机会ID 原因
```

API 入口：

```text
POST /opportunities/evaluate
POST /opportunities/{opportunity_id}/decision
POST /opportunities/{opportunity_id}/purchase
POST /opportunities/{opportunity_id}/assets
POST /opportunities/{opportunity_id}/list
```

上架门禁：

- 先评估利润、回本销量、市场样本和授权风险。
- 你在飞书批准后，只进入 `approved_to_buy`。
- 必须记录购买凭证/授权证据后，才允许生成闲鱼上架。
- 宣传图只有在 `asset_rights_confirmed=true` 时才会保存并用于发布。
- 文案会生成原创版本，不复制卖家的原文。

## URL 下载候选爬虫

你可以发一个网址，让 agent 自动抓取页面标题、描述、图片候选和可下载链接，并把结果送进虚拟产品利润评估。

CLI：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli crawl-url "https://example.com/product" --cost 99 --price 39 --market-sample "闲鱼同类资料 49元"
```

如果你确认有下载和转售授权，可以开启下载：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli crawl-url "https://example.com/product.zip" --rights-type resale_license --rights-evidence "供应商授权已保存" --download
```

飞书触发：

```text
crawl: https://example.com/product
```

API：

```text
POST /crawler/url
```

重要门禁：

- 没有 `rights_type` 和授权证据时，不会下载文件，只保存候选链接和风险报告。
- 下载文件保存到 `data/crawls/<crawl_id>/downloads`。
- 爬虫只做浅层同域抓取，默认最多 `CRAWLER_MAX_PAGES=3`。
- 单次最多下载 `CRAWLER_MAX_DOWNLOADS=5` 个文件，单文件默认上限 `CRAWLER_MAX_FILE_MB=200`。
- 页面图片默认只是候选，不会当作可发布素材，除非你单独确认素材使用权。

## 风控守卫

系统不会尝试绕过闲鱼的人机验证、验证码、滑块、设备指纹或风控检测。正确策略是：动作限速、异常暂停、人工接管、走授权接口。

CLI：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli guard-status
.\.venv\Scripts\python -m xianyu_agent.cli guard-check "请完成人机验证后继续操作"
.\.venv\Scripts\python -m xianyu_agent.cli guard-resume --note "我已手动完成验证"
```

API：

```text
GET  /guard/status
POST /guard/check
POST /guard/resume
```

它会在上架、擦亮、自动发货前检查：

- `PLATFORM_GUARD_MIN_SECONDS_BETWEEN_ACTIONS`：动作之间最小间隔，默认 20 秒。
- `PLATFORM_GUARD_MAX_ACTIONS_PER_HOUR`：小时动作上限，默认 60。
- `PLATFORM_GUARD_DAILY_ACTION_LIMIT`：日动作上限，默认 300。
- `PLATFORM_GUARD_PAUSE_ON_VERIFICATION`：检测到验证/风控文本时暂停，默认开启。

暂停后不会继续发请求；你手动处理完验证，再调用 `guard-resume` 恢复。

## 商品发现 Agent

基于 Playwright 浏览器自动化，拦截闲鱼内部搜索 API (`mtop.taobao.idlemtopsearch.pc.search`) 获取商品数据，用 DeepSeek/MiMo 分析市场机会。

CLI 搜索：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli discover "蓝牙耳机" --max-pages 3 --min-price 50 --max-price 200
```

添加监控任务：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli discovery-task "AI课程" --name "AI课程监控" --min-price 10 --max-price 100
```

查看所有任务：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli discovery-tasks
```

运行所有任务：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli discovery-run-all
```

飞书触发：

```text
搜货：蓝牙耳机
```

API：

```text
POST /discovery/search
GET  /discovery/tasks
POST /discovery/tasks
DELETE /discovery/tasks/{id}
GET  /discovery/tasks/{id}/results
POST /discovery/run-all
```

定时发现配置：

```text
DISCOVERY_ENABLED=true
DISCOVERY_AT=11:00
DISCOVERY_MAX_PAGES=3
```

## 多专家自动回复

基于意图分类的 AI 客服系统，灵感来自 XianyuAutoAgent。支持多级路由：

1. **商品专属规则**：按商品 ID 匹配的自定义回复规则
2. **商品关键词规则**：按商品标题关键词匹配
3. **通用关键词规则**：全局关键词匹配
4. **默认回复**：兜底回复
5. **AI 回复**：DeepSeek V4 Flash 生成智能回复

意图分类：

- `price`：价格相关 → 价格专家回复
- `tech`：技术问题 → 技术专家回复
- `default`：一般咨询 → 默认回复
- `no_reply`：无需回复（如系统消息）

安全过滤：自动拦截 WeChat/QQ/支付宝等平台外联系方式。

人工接管：发送配置的切换关键词（默认 `。`）可在单个会话中切换 AI/人工模式。

配置：

```text
AUTO_REPLY_ENABLED=true
AUTO_REPLY_MODEL=          # 留空则使用 DEEPSEEK_MODEL
AUTO_REPLY_HUMAN_TOGGLE=。  # 人工接管切换关键词
```

API：

```text
POST /auto-reply/generate
GET  /auto-reply/rules
POST /auto-reply/rules
DELETE /auto-reply/rules/{id}
```

## WebSocket IM 实时聊天

直连闲鱼 IM WebSocket 服务，实时接收买家消息并自动回复。基于 XianyuAutoAgent 的协议实现。

启动服务：

```powershell
.\.venv\Scripts\python -m xianyu_agent.cli ws-start --cookies "你的闲鱼cookies"
```

功能：

- WebSocket 连接到 `wss://wss-goofish.dingtalk.com/`
- 心跳保活（15 秒间隔）
- Token 自动刷新（1 小时间隔）
- 消息解密（MessagePack + Base64）
- 自动回复集成
- 人工接管切换
- 过期消息过滤（5 分钟）

需要的 cookies 格式：

```text
unb=xxx; _m_h5_tk=xxx; _m_h5_tk_enc=xxx; cookie2=xxx; sgcookie=xxx; t=xxx
```

## 参考项目

- OpenClaw：可作为上层 agent/channel/skill 容器。
- `GuDong2003/xianyu-auto-reply-fix`：闲鱼自动回复、自动发货、定时擦亮思路值得参考。
- `larksuite/oapi-sdk-python`：飞书官方 Python SDK，后续需要更完整的飞书权限流时可接入。
- `XianyuAutoAgent`：闲鱼 WebSocket IM 协议、消息加解密、多专家 AI 路由。本项目的 WebSocket 层和自动回复模块参考了此项目。

## 合规边界

系统默认不盗用相似商品图片，只生成搜索词和候选链接用于参考。建议上传自己的商品图、授权供应商图、品牌素材包，或接入可商用图库。上架、擦亮和发货应通过闲鱼/闲管家允许的接口或你已授权的账号操作，并保持人工审批和可追溯执行记录。

GitHub 项目也同理：不要把别人的开源仓库原封不动转卖。更稳的方式是卖安装部署、行业配置、飞书/闲鱼/OpenClaw 集成、私有化托管、培训文档，或在许可证允许下开发自己的衍生产品。

虚拟产品同样必须有转售权和素材使用权。可以参考别人的卖点结构，但不要复制课程内容、卖家文案或宣传图片来发布。

人机验证和平台风控不要绕过。系统只做限速、暂停、告警和人工接管，避免自动化把账号推向更高风险。
