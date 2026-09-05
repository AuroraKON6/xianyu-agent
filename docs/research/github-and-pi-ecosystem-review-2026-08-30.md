# GitHub 与 Pi 生态参考审查

审查日期：2026-08-30  
适用项目：`E:\闲鱼\xianyu`  
结论状态：研究完成，尚未安装任何第三方 Pi Package，也未复制任何外部项目代码

## 1. 执行结论

这次检索没有发现一个可以直接替换本项目、同时又满足当前商业运行顺序的成品。
真正有价值的是从多个项目各取一个经过验证的工程合同，再按本项目现有边界独立实现：

1. `Falses-Goofish-GuardAgent`：学习回复 Outbox、原子领取、租约、请求回放、
   不可变聊天事实流和结构化价格承诺。
2. `xianyu-pilot`：学习定时任务心跳与租约、未知发送结果必须人工关闭、
   审计留痕和会话级自动回复开关。
3. `xianyu-auto`：学习每账号持久化浏览器目录、桌面壳与后端进程边界、
   失败页面证据包；不复制其图片抓取流程。
4. `XY-Agent`：学习“结构化商品知识优先，LLM 只负责表达”、知识库摄入、
   文本与图片内容检查、候选去重；不采用其固定类目、地点和低价默认值。
5. `FishClaw`：学习把平台动作拆成小而可测的工具以及每个工具独立冒烟；
   不引入第二套 MCP/Playwright 执行路径。
6. `OpenCompany`：只学习声明式公司章程、角色所有权和明确的人类保留决策；
   不迁移其运行时或托管编排模型。

Pi 方面的决定更克制：**生产业务 Pi 当前不安装任何第三方 Package**。
项目已经有 12 个业务 Typed Tools 和 8 个业务 Skill；继续堆通用插件会重复状态、扩大权限，
还可能重新引入额外进程和终端窗口。唯一值得试验的是把 `pi-web-access` 放进
独立的只读研究会话，且不加载到正在运行的业务 RPC；`pi-lens` 只可能用于开发环境。

当前唯一主线仍是 `ZCODE_HANDOFF.md` 中的 Phase 29：先修 SQLite 数据边界并形成
可恢复 Git 基线，再接真实账号和一个真实 SKU。本文的可靠性增强从 Phase 30/31 开始，
不能打断 Phase 29。

## 2. 当前项目基线

### 2.1 已经存在

| 能力 | 当前实现 | 判断 |
|---|---|---|
| Pi 基座 | `@earendil-works/pi-coding-agent==0.84.2` | 已固定版本 |
| 项目级 Pi 扩展 | `.pi/extensions/xianyu-ops.ts` | 已有 12 个业务允许工具，无任意路径 `read` |
| 业务 Skill | `.pi/skills/` | 已有 8 个，覆盖选品、证据、上架、转化、履约、实验和复盘 |
| 入站消息去重 | `message_envelope.py`、`message_intake.py` | 已按来源、账号和源消息 ID 去重 |
| 人工接管 | `account_runtime.py` | 已有按会话人工接管状态 |
| 审批与执行 | `WorkOrder -> Approval -> ExecutionJobQueue` | 已有高风险动作门禁 |
| 执行重试 | `execution_queue.py` | 已有尝试次数、退避和失败重试 |
| 执行证据结构 | `before_screenshot`、`after_screenshot`、`page_url`、blocker | 字段与持久化已有 |
| 履约锁 | `order_runtime.py` | 已有订单级履约锁和释放 |
| 商业启动门禁 | 12 项候选证据、20 项 Day-0 检查 | 已有且测试覆盖 |

### 2.2 仍有的可靠性缺口

| 缺口 | 当前现实 | 外部项目提供的启示 |
|---|---|---|
| 执行任务原子领取 | `run_next()` 在内存列表中把任务改为 `running`，没有跨进程 lease/fencing | GuardAgent 的 owner token、续租和过期回收 |
| 回复专用 Outbox | Stub adapter 会写文件，但真实回复没有“源消息 -> 固化回复 -> 发送状态”的专用事务状态机 | GuardAgent 的 Reply Outbox |
| 未知结果 | 多数执行结果落入成功或失败，缺少“请求可能已到平台，禁止盲重试”的独立终态 | xianyu-pilot 的未知结果人工关闭 |
| 不可变聊天事实流 | 当前有 intake ledger 和 conversation context，但不是长期 append-only ChatEvent 事实流 | GuardAgent 的 ChatEvent + 读模型 |
| 价格承诺状态 | 已有策略和回复草稿，但没有单调更新的“我方最低承诺价/买方最高出价”事实表 | GuardAgent 的结构化议价记忆 |
| 实际失败证据采集 | 执行队列支持截图路径字段，真实 adapter 的全链路截图、页面摘要和哈希仍需实机验收 | xianyu-auto 的失败证据包 |
| 多账号浏览器隔离 | 账号业务状态已隔离，真实浏览器 profile 生命周期尚未完成 | xianyu-auto 的账号独立 Chrome profile |

## 3. 商业顺序适配原则

外部项目只有在符合以下真实顺序时才进入本项目：

1. **工程边界先稳定**：数据路径、Git 基线、启动唯一性和恢复能力。
2. **账号先证明可用**：登录、账号归属、同步 freshness、Prompt 和库存准备。
3. **商品证据先于发布**：供应、规格、成本、交期、售后、素材权利和利润。
4. **发布必须形成可追溯回执**：批准快照、实际页面、平台商品 ID 和时间。
5. **消息先入事实流，再做决策**：源消息去重、商品事实、价格规则、草稿、审批。
6. **发送先固化再执行**：Outbox 领取、发送、ACK/人工核验、记忆提交。
7. **订单先确认再采购**：订单归属、付款状态、规格、收件信息和采购预算。
8. **履约完成才记利润**：供应商付款、运费、退款、平台费用和售后准备金。
9. **按证据复盘再扩张**：一次只改变一个变量，不以曝光或 README 声明代替利润。

因此，任何“自动上架、自动回复、自动发货”的外部功能，都不能绕过现有
候选证据、审批、执行证据和人工止损机制。

## 4. GitHub 相似项目审查

### 4.1 Falses-Goofish-GuardAgent

- 地址：<https://github.com/falses00/Falses-Goofish-GuardAgent>
- 快照：GitHub 页面在审查日显示 150 次提交、GPL-3.0。
- 定位：本地优先的闲鱼客服和议价防守 Agent。
- 匹配度：高，尤其适合 Phase 31 的询盘与转化闭环。

值得学习：

1. LLM 只负责自然表达，价格、商品事实和卖家承诺由确定性代码控制。
2. 工作记忆、长期高信号语义和结构化状态三层分离。
3. `lowest_price_committed` 只向更低值更新，`buyer_highest_offer` 只向更高值更新。
4. 每条平台消息先进入不可变 `ChatEvent`，再进入聚合、Agent 和 Outbox。
5. Outbox 用 SQLite 原子事务领取发送权；失败重试复用已固化回复，不重新调用模型。
6. `sending` 记录有租约，可在崩溃后回收；重复源消息被抑制。
7. 人工接管在决策前、模型返回后、领取前、发送前反复检查，重启后仍有效。
8. 每次决策保存路由、定价来源、知识命中、护栏和降级 Trace。
9. 明确承认无法凭本地 Outbox 获得平台级 exactly-once，保留未知结果人工审计。

不能直接复制：

- GPL-3.0 代码进入本项目会带来许可证义务；除非项目明确接受 GPL，当前只吸收思想，
  以本项目模型和测试独立实现。
- 不把该项目 README 的 live 能力当成本项目平台接入证明。
- 不直接复用其平台协议代码、Cookie 处理或提示词正文。

本项目映射：

| 外部设计 | 本项目落点 | 优先级 |
|---|---|---|
| Reply Outbox | 新增 `reply_delivery.py`，关联 `source_message_id`、WorkOrder、Approval 和 execution job | Phase 31 P0 |
| 原子租约/fencing | 扩展 `execution_queue.py` 的 claim/renew/complete 合同 | Phase 30 P1 |
| ChatEvent | 在现有 intake ledger 旁增加 append-only 事实表，不替换 envelope | Phase 31 P1 |
| 价格承诺 | 新增按 `cookie_id + chat_id + item_id` 隔离的结构化状态 | Phase 31 P1 |
| Trace 回放 | 扩展现有 execution evidence 和 incident bundle | Phase 31 P2 |

### 4.2 xianyu-pilot

- 地址：<https://github.com/dameng2026/xianyu-pilot>
- 快照：GitHub 页面在审查日显示 43 次提交、Apache-2.0、35 个 fork。
- 定位：FastAPI + Vue + Playwright + MySQL/Redis 的卖家运营后台。
- 匹配度：中高，适合参考长期运行和后台可靠性，不适合整体迁移。

值得学习：

1. 定时任务同时具备心跳和租约，避免多实例重复执行。
2. 通知测试发送持久化去重；结果未知时只允许人工确认关闭。
3. 会话级自动回复开关，而不是只有全局总开关。
4. 操作日志有保留策略，运行状态和审计留痕分开。
5. 账号、消息、订单、知识库和任务各自有边界，不由一个 Agent 直接改所有状态。

有许可证但仍不直接搬运：

- Apache-2.0 允许在满足 Notice/License 条件下复用，但本项目已经有 FastAPI、控制台、
  adapter 和数据模型，整体引入会产生双数据库和双执行路径。
- 真正采用某段代码前必须单独记录文件、固定提交和许可证 Notice。
- 该项目包含滑块处理等能力；本项目不采用绕过平台验证的实现。

本项目映射：

| 外部设计 | 本项目落点 | 优先级 |
|---|---|---|
| Job lease | `ExecutionJobQueue` 持久化 owner、lease_until、fencing_token | Phase 30 |
| Unknown outcome | 新增 `verification_required`，禁止自动 retry | Phase 30 |
| Session reply switch | 扩展现有 manual takeover 公共状态和控制台 | Phase 31 |
| Audit retention | 为事件、Trace、截图定义保留周期和脱敏规则 | Phase 31 |

### 4.3 xianyu-auto

- 地址：<https://github.com/HongBaoCool/xianyu-auto>
- 快照：GitHub 页面在审查日显示 1 次提交，未显示标准开源许可证。
- 定位：Electron 前端、Python 后端、Playwright 和 SQLite 的本地桌面工具。
- 匹配度：中，价值主要在真实浏览器生命周期和操作者体验。

值得学习：

1. Electron 用 stdin/stdout 管理 Python 后端，UI 与平台执行进程职责分离。
2. 每个账号使用持久化浏览器用户目录，避免 Cookie 和页面状态串号。
3. 发布进入队列而不是从表单直接执行。
4. 代码目录和用户数据目录分离，桌面更新不应覆盖账号数据。
5. 失败时保留运行日志、页面状态和截图供操作者判断。

不采用：

- 无标准许可证时不能复制代码、图片、文案或资源文件，只能学习公开架构思想。
- 不采用竞品图片抓取或第三方素材复制。
- 当前 Web console 足够完成 Alpha，不在 Phase 29 引入 Electron 打包。

采用时机：单账号 Alpha 稳定以后，在 Phase 33 评估每账号浏览器 profile；只有需要
给非开发操作者提供安装包时才评估桌面壳。

### 4.4 XY-Agent

- 地址：<https://github.com/xiaolu-ai26/XY-Agent>
- 快照：GitHub 页面在审查日显示 5 次提交，未显示标准开源许可证。
- 定位：DrissionPage + ChromaDB/BGE + GUI 的选品、上架和 RAG 客服。
- 匹配度：中，适合参考知识摄入，不适合参考商业规则。

值得学习：

1. 商品知识从 CSV 预构建，客服先查知识，再调用 LLM 兜底。
2. 候选用 URL 精确键和 Jaccard 文本相似度双重去重。
3. 文本词典与图片 OCR 都进入内容检查。
4. 选品服务、上架服务、客服服务、知识库和 scheduler 分模块。

不采用：

- 未找到标准许可证，不复制代码或模板。
- 不采用固定“其他技能服务”类目、大学城地点、`9.9` 默认引流价。
- 浏览/想要比只能作为需求信号，不能替代可兑现毛利、交期和售后证据。
- 1.3GB 本地向量模型对当前低 SKU Alpha 过重；先用结构化商品事实和精确检索。

本项目采用方式：先做 `product_facts` 结构化表和 deterministic FAQ；当真实商品超过
约 50 个、FAQ 资料达到数百条并且精确检索召回不足时，再用离线评测决定是否增加向量库。

### 4.5 FishClaw

- 地址：<https://github.com/TnoobT/FishClaw>
- 快照：GitHub 页面在审查日显示 17 次提交，未显示标准开源许可证。
- 定位：LLM + Playwright 的自然语言商品管理工具，并提供闲鱼 MCP 方向。
- 匹配度：低到中，只适合工具合同参考。

值得学习：

1. 登录、搜索、填草稿、发布确认、在售查询和商品管理拆成独立工具。
2. 发布动作保留用户确认，而不是让自然语言直接触发最终写操作。
3. 为每个浏览器工具准备独立测试脚本，便于定位页面变化。

不采用：

- 本项目已经确定 Pi typed HTTP extension -> FastAPI -> approval/execution -> BrowserAdapter
  的单一写路径；引入 MCP 或另一套 Playwright 工具会破坏该合同。
- 无标准许可证时不复制实现。

### 4.6 OpenCompany

- 地址：<https://github.com/tinyhumansai/opencompany>
- 快照：GitHub 页面明确标注 GPL-3.0 和“not production-ready”。
- 定位：声明式一人公司角色编排运行时。
- 匹配度：概念高、运行时低。

值得学习：

1. 用 `company.toml` 声明角色、职责和人类保留决策。
2. 公司定义与宿主运行时分离，新增业务是新配置而不是复制整套代码。
3. 提供 `check` 命令验证公司配置。
4. 人保留资金、战略、最终批准；Agent 承担可逆、可审计的准备工作。

不采用：

- 官方 README 明确仍非生产就绪，API 和 CLI 会变化。
- 依赖 TinyHumans API/Medulla 托管模型，增加新的运行时和供应商锁定。
- 9 到 900 Agent 的设计不适合当前一个账号、一个 SKU 的商业 Alpha。
- GPL-3.0 代码不进入当前项目。

本项目吸收方式：未来把现有 `.pi/SYSTEM.md`、8 个 Skill、12 个工具和审批矩阵汇总为
一个只读 `company_contract.json`，由测试校验角色、工具和保留决策的一致性；不迁移运行时。

## 5. Pi Packages 与插件审查

### 5.1 Pi 的真实插件模型

Pi 官方称其为 Extensions、Skills 和 Pi Packages：

- Extension 是可执行 TypeScript/JavaScript，可注册工具、命令和事件钩子。
- Skill 是按需加载的操作说明，不应承担事务一致性或平台发送可靠性。
- Pi Package 可以通过 npm/git 打包 Extension、Skill、Prompt 和 Theme。
- 项目级安装应写入 `.pi/settings.json`，版本或 Git ref 必须固定。
- 官方文档明确说明第三方 Package 以完整系统权限运行，安装前必须审查源码。
- 可以用 `pi -e ...` 临时加载，而不把包写入项目设置。

这意味着 Outbox、租约、幂等和利润账本必须继续由 Python 业务核心实现，不能靠 Skill
提示词保证；第三方 Extension 也不能绕过 FastAPI 的审批和单一执行路径。

### 5.2 候选矩阵

以下版本来自 2026-08-30 的 npm 元数据查询，仅作为本次审查快照：

| Package | 快照版本 | 许可证 | 可带来的能力 | 决定 |
|---|---:|---|---|---|
| `pi-web-access` | `0.27.0` | MIT | 网页搜索、内容抽取、多搜索后端 | 仅独立只读研究会话临时试用 |
| `pi-subagents` | `0.59.0` | MIT | 子 Pi 进程、研究/开发/复核角色 | 业务运行时不安装 |
| `@harms-haus/pi-lens` | `2.1.0` | MIT | 编辑后自动 lint/LSP/tsc | 仅开发环境可选 |
| `@harms-haus/pi-tasks` | `0.1.0` | MIT | 分阶段任务板、依赖和状态门禁 | 不安装，重复 WorkOrder/Phase 计划 |
| `@harms-haus/pi-processes` | `0.1.0` | MIT | 拉起、监控、终止后台进程 | 不安装，可能增加进程/窗口副作用 |
| `@vtstech/pi-diag` | `1.3.3` | MIT | Pi/Ollama/系统诊断 | 仅临时开发诊断，当前价值低 |
| `@vtstech/pi-long-term-memory` | `1.3.5` | MIT | 跨 Session 长期记忆 | 不安装，可能破坏账号/业务事实隔离 |

注意：未使用无 scope 的 `pi-tasks`。审查时 npm 元数据显示它指向另一个仓库，
与本次查看的 `harms-haus/pi-tasks` 不是同一来源。任何试验都必须使用完整 scope 和固定版本。

### 5.3 推荐一：pi-web-access，仅用于隔离研究

仓库：<https://github.com/nicobailon/pi-web-access>

为什么有价值：

- 可让独立 Research Pi 查询公开供应商资料、类目趋势和网页内容。
- MIT，仓库有 LICENSE、SECURITY、测试和持续更新。
- 当前 `0.27.0` 与 Pi `0.84.2` 的 peer dependency 形态兼容。

为什么不能装进生产业务 Pi：

- 它支持多种远程搜索/抓取后端以及可选浏览器 Cookie，权限面远大于现有
  `xianyu_market_search`。
- 业务 RPC 应保持 12 个白名单业务工具；通用抓取不应与发布、回复准备共享 Session。
- 其更新频率高，不能使用浮动 latest。

试用合同：

1. 用 `pi -e npm:pi-web-access@0.27.0` 临时加载，不写 `.pi/settings.json`。
2. 使用单独空目录、临时 Session、无闲鱼 Cookie、无业务 API 密钥。
3. 禁止浏览器 Cookie 读取和远程 hosted fetch provider；只访问公开页面。
4. 只输出候选 URL、抓取时间、可观察事实和推断标签，不创建 WorkOrder。
5. 退出后确认没有遗留浏览器、Node 子进程或终端窗口。
6. 通过 5 个固定查询的可重复性和来源完整度后，才决定是否建立独立 research profile。

### 5.4 推荐二：pi-lens，仅用于开发

仓库：<https://github.com/harms-haus/pi-lens>

它可以在修改后自动运行 Ruff、Mypy、LSP 和其他诊断，对 Zcode/Pi 开发会话有帮助。
但它会启动代码诊断进程并在每次编辑后工作，不适合当前业务 RPC，也不应替代项目的
`scripts/run_tests_isolated.ps1`。只有建立独立开发配置且确认不会弹窗、不会改文件、
不会扫描 `data/live` 后才可试用。

### 5.5 明确不装的包

#### pi-subagents

当前 Python 核心已经有明确的 specialist review、候选状态和审批事实源。增加子 Pi 会产生
第二套角色状态、额外模型成本和子进程；它适合代码研究，不适合持有账号和交易上下文。

#### pi-tasks / pi-workflows / pi-til-done

当前项目已有 Phase 计划、WorkOrder、Approval、ExecutionJob 和商业批次状态机。
通用任务板只能管理 Agent 工作，不能代表真实平台状态，容易出现“双重完成”。

#### pi-processes

用户已经遇到偶发终端弹窗；该包的职责正是拉起和管理长进程。当前启动/停止脚本已有
PID、端口和单实例合同，不增加新的进程管理器。

#### pi-long-term-memory

交易事实必须按账号、会话、商品和来源隔离，并能回溯原始证据。通用跨 Session 记忆
可能把推断写成事实或跨账号召回，不能取代 SQLite/JSON 业务存储。

#### pi-mcp-adapter / browser-native packages

它们会建立第二套工具发现或浏览器写路径，违反当前单一执行合同。继续使用本地
`xianyu-ops.ts` 的 typed HTTP 工具和 Python `BrowserAdapter`。

## 6. 最值得新建的是本地能力，不是第三方插件

### 6.1 `daily-operator` Skill

用途：每天读取 business snapshot、first-run readiness、账号队列和 Commercial Alpha，
只输出“今天唯一优先动作、阻塞证据、由谁完成、完成定义”。

原因：现有 weekly review 偏复盘，缺少面向 5 分钟日常操作的轻量 Skill。

边界：只读，不创建批准，不执行平台动作；所有结论必须带 ID 和时间。

### 6.2 `commercial-exception-review` Skill

用途：处理 `verification_required`、发送结果未知、同步陈旧、平台要求人工认证、
履约锁未释放等异常，输出人工核验清单。

原因：真实商业系统失败时最危险的不是“报错”，而是重复发布、重复回复、重复采购。

边界：Skill 只解释并建议；未知结果的状态机和禁止自动重试必须由 Python 实现。

### 6.3 内部 Pi Package

当 Phase 29 完成且 Git 基线稳定后，可以把现有 extension、8 个 Skill、SYSTEM 和测试
整理为项目自有的、固定版本的内部 Pi Package。目的不是发布到 npm，而是：

1. 明确资源清单和版本。
2. 对资源哈希和工具白名单做启动校验。
3. 让生产 profile 与 research/development profile 彻底分开。
4. 升级 Pi 时可以针对一个包做兼容性验收和回滚。

## 7. 分阶段采用计划

### Phase 29：不吸收新运行时，只修基础边界

1. 修复 `.env DB_PATH` 初始化顺序。
2. 备份并验证 SQLite 迁移，保证可重复执行和回滚。
3. 建立 Git 可恢复基线。
4. 保持生产 Pi 的 12 个业务工具和 8 个 Skill 不变。
5. 不安装任何第三方 Pi Package。

验收：与 `ZCODE_HANDOFF.md` Phase 29 定义一致，926 个测试或新的完整基线全部通过，
真实数据哈希不因测试改变。

### Phase 30：平台写操作可靠性

1. 为 ExecutionJob 增加原子 claim、owner、`lease_until` 和 fencing token。
2. 增加 `verification_required` 状态；平台可能已执行但无确认时禁止自动 retry。
3. 将批准快照哈希、请求开始时间、页面 URL、前后截图哈希和平台回执归档到一个证据包。
4. 保留人工认证和人工确认，不实现验证绕过。
5. 给 publish 做崩溃窗口测试：调用前崩溃、调用中超时、调用后未落库。

验收：两个 worker 竞争同一 job 只有一个能领取；旧 owner 完成会被 fencing 拒绝；
未知结果不会产生第二次平台调用。

### Phase 31：消息与转化可靠性

1. 新建 append-only ChatEvent，沿用现有 envelope 去重键。
2. 新建 Reply Outbox，状态至少包含：`pending`、`sending`、`sent`、`failed`、
   `verification_required`、`cancelled`、`manual_takeover`。
3. 回复在模型生成后先固化；重试发送同一回复，不重复调用模型。
4. 只有 `sent` 或经人工核验已发送后，才提交助手消息和价格承诺。
5. 建立结构化 `product_facts`、`seller_lowest_committed`、`buyer_highest_offer`。
6. 增加 deterministic FAQ；LLM 不得创造商品参数、交期和价格。
7. 增加 Trace 回放和一组固定询盘评测。

验收：重复源消息不产生第二条回复；进程在发送前后崩溃可恢复；人工接管重启后有效；
商品事实和价格承诺不会被买家文本覆盖。

### Phase 32：订单与履约

1. 将现有 fulfillment lock 升级为可审计 owner/lease 合同。
2. 采购草稿、付款和平台发货保持分开；任何未知结果都进入人工核验。
3. 订单、供应商回执、物流和利润账本使用同一个 `order_id` 链接。
4. 售后和退款使用独立 Outbox/证据，不复用客服回复状态。

验收：重复订单事件不重复采购；未确认付款不进入采购；利润只使用实际费用和回执。

### Phase 33：有限扩展

1. 14 天真实数据证明闭环后，再评估第二账号。
2. 第二账号前实现浏览器 persistent profile 隔离和 profile 健康检查。
3. 只有商品知识规模达到阈值且精确检索评测不足，才引入向量检索。
4. 只有操作者需要安装包，才评估 Electron/Tauri 桌面壳。
5. 可临时试验 `pi-web-access` research profile；生产 profile 仍不加载。

## 8. 实施任务与验收清单

| ID | 任务 | 输入 | 输出 | 自动验收 | 人工/外部阻塞 |
|---|---|---|---|---|---|
| GH-01 | ExecutionJob lease/fencing 设计 | 现有 queue JSON | 迁移兼容的新 schema | 并发、过期、旧 owner、重启测试 | 无，Phase 29 后可做 |
| GH-02 | Unknown-result 状态 | adapter 结果合同 | `verification_required` + UI 动作 | 无自动 retry、人工关闭测试 | 真实页面回执样本 |
| GH-03 | Reply Outbox | envelope + approved reply | 持久化发送状态机 | 重复、崩溃、失败、接管测试 | 真实消息发送 adapter |
| GH-04 | ChatEvent 事实流 | inbound/outbound events | append-only store + inbox read model | 顺序、去重、分页、重放测试 | 无 |
| GH-05 | 商品事实和议价承诺 | candidate/listing/order facts | 结构化只增量状态 | 错误数字、反向更新、跨账号隔离测试 | 一个真实 SKU 事实包 |
| GH-06 | 失败证据包 | browser adapter | 截图、URL、摘要、哈希、时间 | 敏感信息脱敏和文件完整性测试 | 实机页面 |
| PI-01 | web-access 隔离试验 | 公共网页固定查询 | 来源化研究结果 | 无业务文件变化、无遗留进程 | 是否提供搜索 API 可后置 |
| PI-02 | 内部 Pi Package | 稳定 Git 基线 | 固定资源 manifest | 12 tools、8+ skills、哈希、回滚 | Phase 29 完成 |
| PI-03 | daily-operator Skill | dashboard/readiness APIs | 每日唯一动作摘要 | 只读、ID/时间、无副作用测试 | 无 |
| PI-04 | exception-review Skill | unknown/failed/stale 状态 | 人工核验清单 | 不自动重试、不执行写动作 | GH-02 先完成 |

## 9. 不做清单

1. 不整体替换为任何外部闲鱼项目。
2. 不同时运行两套浏览器写 adapter。
3. 不把 MCP 当成绕过现有审批队列的快捷路径。
4. 不复制无许可证仓库的代码、文案、图片或配置模板。
5. 不把 GPL 项目代码并入当前仓库，除非项目主动接受 GPL-3.0。
6. 不学习或实现滑块/风控验证绕过。
7. 不抓取并复用第三方商品图片和文案。
8. 不把低价、曝光、想要数或模型评分当作实际利润。
9. 不在真实账号前用浮动 latest 安装 Pi Package。
10. 不让通用长期记忆保存 Cookie、买家隐私、未核验价格或跨账号事实。

## 10. 来源

### Pi 官方与 Packages

- Pi Packages 官方说明：
  <https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md>
- Pi Web Access：<https://github.com/nicobailon/pi-web-access>
- Pi Subagents：<https://github.com/pi-packages/nicobailon-pi-subagents>
- Harms Haus Pi extensions 索引：<https://github.com/harms-haus/pi-extensions>
- Pi Lens：<https://github.com/harms-haus/pi-lens>
- Pi Tasks：<https://github.com/harms-haus/pi-tasks>
- Pi Processes：<https://github.com/harms-haus/pi-processes>
- VTSTech Pi extensions：<https://github.com/VTSTech/pi-coding-agent>

### 一人公司与闲鱼系统

- OpenCompany：<https://github.com/tinyhumansai/opencompany>
- Falses Goofish GuardAgent：
  <https://github.com/falses00/Falses-Goofish-GuardAgent>
- xianyu-pilot：<https://github.com/dameng2026/xianyu-pilot>
- xianyu-auto：<https://github.com/HongBaoCool/xianyu-auto>
- XY-Agent：<https://github.com/xiaolu-ai26/XY-Agent>
- FishClaw：<https://github.com/TnoobT/FishClaw>

## 11. 给接手者的唯一行动建议

先完成 Phase 29，不安装插件。Phase 29 完成后，第一项外部架构吸收不是 RAG、Subagent
或桌面壳，而是 **ExecutionJob 的 lease/fencing 与 unknown-result 人工闭环**。它直接保护
第一次真实发布不被超时重试变成重复发布。随后再实现 Reply Outbox，保护第一条真实自动
回复不被重连和崩溃变成重复承诺。只有这两个执行合同稳定后，研究插件和更多 Agent 才有
商业价值。
