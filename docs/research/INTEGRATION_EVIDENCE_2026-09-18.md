# 本地与 GitHub 项目集成依据

核验日期：2026-09-18。范围：仓库文件、README、公开文档及关键代码阅读；没有运行第三方项目，没有验证其生产稳定性，也没有完成许可证法律审查。

## 1. 结论

优先保留当前项目，用适配器和可测试的小模块吸收外部设计。不要整包合并多个闲鱼项目的账号管理、数据库和消息循环，否则容易出现同一消息被多个机器人处理、重复发送和账本不一致。

当前未找到并验证“无需申请、无限额、跨平台、可商用、实时准确”的统一免费差价 API。第一条可交付路径是供应商报价资料导入 + 合法可用的市场观察 + 精确规格匹配 + 完整贡献利润判断。在线 API 是增强来源，不应是系统能否运行的唯一条件。

## 2. 当前项目真实基线

| 项目 | 本次观察 | 规划影响 |
|---|---|---|
| 主仓库 | `E:\闲鱼\xianyu`，HEAD `790dff8`，初始工作区干净 | 所有后续结论基于这个快照 |
| Pi | package.json 锁定 `@earendil-works/pi-coding-agent` 0.84.2，Node >=22.19 | 插件先验证兼容，不直接升级基座 |
| 已有聊天能力 | 交接文档有 GH-04/05；代码有 chat_events、price_commitments、reply-trace | 复用并补语义测试，不能当空白重写 |
| 测试 | 最新交接记录 1026 通过；旧 PROJECT_STATUS 仍为 996 | 都是历史值，本次规划没有重跑 |
| 多代理 | 注册器同步分发、内存历史；Pi 当前操作者请求串行 | 需要持久任务和真实 worker，不只是新增角色名字 |
| 商品发现 | 已有浏览器采集闲鱼信息 | 不等于已接 PDD/1688 供应报价 |
| 部署 | 本次查询 8000/8788 无监听 | 不是在运行生产商店的证据 |

本次未重新完整阅读原始书籍和 `闲鱼赚钱路子.docx`；相关经营方法以现有项目文档为输入，不能声称重新逐页核实原文。

## 3. 本地参考项目

| 本地目录 | 观察到的结构 | 可学习的设计 | 接入方式与限制 |
|---|---|---|---|
| `E:\闲鱼\xianyuapis-main` | Start.py、XianyuAutoAsync.py、utils、MIT LICENSE；README 指向 IAMLZY2018/XianYuAssistant 并提及 cv-cat/XianYuApis | 消息连接、协议封装、上下文恢复 | 先登记真实来源和版本，再把必要能力收敛到 Adapter；文件夹名不能作为上游身份 |
| `E:\闲鱼\xianyu-auto-reply-main` | cookie_manager、db_manager、ai_reply_engine、order_status_handler、reply_server、frontend | 账号状态、消息去重、订单状态、客服操作台 | 只参考边界和状态机；README 含历史安全问题及学习用途说明，复制前查当前快照许可证 |
| `E:\闲鱼\mulitXianYuAugent-main` | account_manager、buyer/seller agents、heartbeat/token/manual-mode | 多账号隔离、心跳、人工接管 | 合并到自己的账号上下文和 worker，不启动第二套回复服务 |
| `E:\闲鱼\fastPickUp-master` | Java/Spring，FiddlerController 保存采集信息、读标记、分页 | 采集收件箱与待处理状态 | 仅借鉴结构；未见足够许可证依据，不复制代码；不新增抓包服务依赖 |
| `E:\闲鱼\LittleCrawler-main` | main.py 的 CrawlerFactory 当前只有 xhs/zhihu，带 MIT 文件 | CrawlerFactory、来源适配模式 | 不是现成 PDD/1688 爬虫；上游链接未成功读取，不臆测更多平台 |
| `E:\闲鱼\XianyuAuto-master` | xy_main2.py，uiautomator2，安卓编辑/发布，存在无限等待式循环 | 人工手机操作的候补接入路径 | 暂不作为生产主路径；需要超时、状态识别、幂等、回读和异常接管才可能使用 |

以上是结构阅读结论，不是逐行完整审计或部署验收。可复用前必须记录仓库 URL、commit、许可证路径、引入文件、改动和测试。

## 4. GitHub 候选与明确取舍

### 4.1 必须保留：Pi 官方运行时

[earendil-works/pi](https://github.com/earendil-works/pi) 是本次访问到的官方仓库；旧 badlogic/pi-mono 地址重定向至此。[Packages 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md) 描述 extensions、skills、prompts、themes 的打包和项目级安装。

用途：保持当前 Pi 操作者与工具调用方式；插件安装锁定来源/版本并做实验。插件可以执行本机代码，不应仅因在 GitHub 上就直接放入真实账号进程。

### 4.2 值得验证：Pi 子代理与 MCP

- [nicobailon/pi-subagents](https://github.com/nicobailon/pi-subagents)：用于子代理委派、异步结果与产物管理的候选。尚未在本项目 Pi 0.84.2 实测；WP08 验证后才能纳入依赖。业务任务生命周期与授权仍由 Python 服务维护。
- [nicobailon/pi-mcp-adapter](https://github.com/nicobailon/pi-mcp-adapter)：需要接成熟 MCP 工具时再考虑。当前已有 typed HTTP 工具，不为“插件多”引入第二套授权通道。

不承诺这些插件替代队列、数据库、账务或平台适配器；插件说明中的功能也不是本项目已经具备的功能。

### 4.3 闲鱼监控与交易参考

- [Usagi-org/ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor)：监控任务、价格历史、筛选和前端值得参考；页面明确标记 **2026-06-09 已归档**。可提取模式，不能将它当作仍持续维护的唯一生产引擎。
- [cv-cat/XianYuApis](https://github.com/cv-cat/XianYuApis)：非官方平台协议/API 研究，可帮助理解适配层；不能称为闲鱼官方免费商业 API，实际可用性和平台要求需单独验证。
- [shaxiu/XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent)：客服意图和专家路由参考。本项目已有类似能力，优先补评测与事实约束，不重复起一个回复机器人。
- [MannersMakesMan/mulitXianYuAugent](https://github.com/MannersMakesMan/mulitXianYuAugent)：多账号和人工接管参考。运行状态与隔离能力仍需本项目测试证明。
- [uniquecolin/xianyu-auto-reply](https://github.com/uniquecolin/xianyu-auto-reply)：一个可比较的回复系统 fork，页面列出 AGPL-3.0。它不等于本地同名项目的精确版本；不能据此推定本地快照许可，也不直接整包集成。

### 4.4 通用组件

| 项目 | 用途 | 决策 |
|---|---|---|
| [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | 文本相似召回 | 可优先使用，型号/规格硬约束另做，不能靠相似度证明同款 |
| [crawlee-python](https://github.com/apify/crawlee-python) | 爬取任务与浏览器生命周期 | 现有浏览器池不足时再试点，不默认重写采集器 |
| [opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) | 标准化 trace、指标、日志关联 | 优先接入执行/模型/适配器链路 |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM 调用、评测和成本观察 | 可选，不能替代业务决策/订单账本；部署运维成本另算 |
| [Temporal](https://github.com/temporalio/temporal) | 持久工作流 | 延后，先数据库任务+outbox；发生复杂长事务需求再用 ADR 判断 |

Langfuse 的开源/商业模块范围按[官方说明](https://langfuse.com/handbook/chapters/open-source)和选定版本检查，不能笼统说所有功能都免费。上表未锁定版本的项目均不等于已批准安装。

## 5. 免费 API 的核验边界

### PDD：有历史免费证据，没有当前账号可用性证明

[PDD 官方 CDN 2021 年文档](https://commoncdn.yangkeduo.com/pdd_oms/2021-01-12/2e22638bda0b0f77479f89dab1de2988.pdf)列出 `pdd.ddk.goods.search`，历史材料标注免费且不需用户授权；[2020 年申请材料](https://funimg.pddpic.com/ddjb/2020-12-04/4f8c0c46-e2c3-40e4-bfee-5ac05ba96607.pdf)涉及应用/账号申请与绑定。

这些不是 2026 年资格、权限、费率或接口存续保证。本次未能成功读取 [PDD 开放平台](https://open.pinduoduo.com/)当前规则页，因此推荐为“优先验证候选”，不是“马上可用的免费供货接口”。联盟推广价不自动等于你的采购落地价，也不自动包含稳定库存。

### 1688：供应链重要，但本次没有证实无条件免费查询

[1688 开放平台](https://open.1688.com/)本次读取失败。WP02 要用实际账号核实应用资质、授权包、报价/库存字段、调用限额和订单能力。在此之前供应商报价文件与书面条款是可开发的数据输入。

### 抖音：只作权限与字段差异的例子，不作为第一批采购来源

[官方权限包文档](https://op.jinritemai.com/docs/guide-docs/33/4495)体现接口权限管理；[官方数据调整说明](https://buyin.jinritemai.com/developer/service-provider/help-support?docId=6199&noSearchMenu=1000&searchMenu=0)说明部分字段呈现可能调整，例如销量区间和优惠价格信息。

因此不能用“另一个平台有 API”推导“免费可拿精确销量和任意券后价”。本项目首期不把抖音接入列为必需依赖。

### 最小来源能力测试表

每个候选填写并附证据：

```text
provider / owner / docs_url / checked_at / terms_version
application_status / account_scope / required_permissions
cost_basis / quota / retry_policy / allowed_storage
search_supported / exact_sku_supported / stock_supported
freight_supported / coupon_eligibility / price_valid_until
real_sample_count / raw_evidence_refs / observed_limitations
status / next_action / accountable_owner
```

无 key 可以完成 schema 和 fixture 测试；真正的端到端接口通过必须是授权账号上的实际成功请求。凭证不写进源码、Markdown 或报告。

## 6. 整合方式和停止条件

推荐顺序：本项目已有模块 -> 标准库/成熟小依赖 -> 自己的薄适配器 -> 第三方局部代码 -> 整套系统。越往后维护和迁移成本越高。

任一候选出现以下情况先停止接入：来源/许可不清、与 Pi 版本冲突且无低成本修复、强制复制整个账号库、无可区分的失败状态、不能避免重复发送、需要隐藏常驻弹窗或不可停止任务、持续维护成本明显高于自己写薄适配器。

应学习的是任务状态、账号隔离、数据模型、重试与接管机制，而不是照搬 UI 后宣称项目工业化。
