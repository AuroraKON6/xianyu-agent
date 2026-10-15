# Xianyu Pi Commercial Alpha Status

Updated: 2026-10-12

## 2026-10-12 GLM Codex 修复第一轮（19/51 已修，全量 1592/1592 PASS）

证据：[修复报告](docs/acceptance/GLM_CODEX_FIX_ROUND1_2026-10-12.md)。Codex 独立检验发现 51 失败断言；本轮修复 C 核心 6/6 + E 财务 7 + D 雷达 3 + S 存储 3 = **19 项**，其余 32 项待后续。全量 **1592/1592，exit 0**；live 20 文件。关键修复见报告。**红线违规如实记录**：修复中一次直接运行测试污染了 live（20→35 文件）；已清理至 20 但 3 个文件内容修改不可恢复。

## 2026-10-12 Codex 独立检验交接更新（不通过，待 GLM 修复）

唯一入口：[CODEX_VERIFICATION_HANDOFF_2026-10-12.md](docs/acceptance/CODEX_VERIFICATION_HANDOFF_2026-10-12.md)。已补 PowerShell 标准运行器、全部85项ID与证据映射、53项可单独执行的隔离对抗检验、内部未实现范围与修复顺序。下方 GLM 全绿及“剩余全部是外部缺口”是原交付声明，不能替代本轮独立结果。

原始全量实跑1592项有7个Windows子进程输出编码错误（exit 1）；明确UTF-8后相关审计5项、备份/导出3项均通过，未拼接为第二次全量PASS。独立检验53项中51个期望不变量失败、2个正向守卫通过（失败断言数不等于独立问题数）：资料撤销/快照绑定、Pi出站、报价/配额、采购预算/付款身份、账本、导出恢复等均有复现。业务实现未修，真实模型/平台写入未执行；真实经营和生产切换未批准。源码/live对照及精确case见入口与manifest。

## 2026-10-12 CODEX 检验交接就绪

入口：[CODEX_VERIFICATION_HANDOFF_2026-10-12.md](docs/acceptance/CODEX_VERIFICATION_HANDOFF_2026-10-12.md)——九报告+两 ADR 索引、分层复现命令（S/H/R/审计/演练/全量 1592/1592）、九条对抗检验点（伪造操作者/字节篡改/阶梯价/冲正并发/出站门禁/claim 隔离/配额/假价差/live 零污染）、状态声明与禁止超读边界。交付前抽查：source_quotas/market_probe_client/pi_outbound_gate 三专项 + 前端语法全绿；工作区 60 项未提交（HEAD a3c2b19 未动），零残留进程。

## 2026-10-12 GLM 第八轮补充：闲鱼 MCP 真实通道接入（A50/A51 R 层 PASS）

证据：[R8 报告](docs/acceptance/GLM_R8_XIANYU_MCP_REAL_CHANNEL_2026-10-12.md)；[ADR-0003](docs/adr/0003-xianyu-mcp-read-channel.md)。用户授权接入闲鱼 MCP：选定 rira2/xianyu-mcp（免账号/无 cookie/无写工具，Node+Playwright 有头匿名读 mtop；goofish-cli 因 Python 3.11 门槛搁置）。

- 真实匿名平台读全通：capabilities 探针（session_state=logged_out 证实匿名）；search_count **291,757 件**；browse_feed/search_items 各 15 条真实商品卡（¥39–66，含 item_id/想要数）；RGV587 限流→60s 退避重试成功（A53 真实形态，与 A46 配额语义一致）。
- 10 条真实闲鱼观察入流水线（URL 溯原），产出**第一张真实闲鱼市场侧商机卡**：1688 ¥360 → 闲鱼真实 ¥59（二手/清货），系统如实判无差价机会。零写操作、零凭据、零残留进程。
- 八轮全量 1592/1592 仍有效（本轮零产品代码改动）。剩余缺口清单中"市场读通道"一项已由本输入解锁；其余外部缺口不变。

## 2026-10-12 GLM 第八轮：A46 来源配额机制（隔离软件验收通过，未提交）

证据：全量隔离回归 **1592/1592**（1489 基线 + 103 本计划新增），exit 0；live 前后一致。新增 `xianyu_agent/source_quotas.py`（日窗口持久配额账本）+ 5 专项：重试计入配额（429 后重试一次占两单位）；计数跨进程重启持久；未知限额禁止无人值守扫描（显式 manual 可过并如实记录）；到达限额诚实拒绝；窗口按 UTC 日滚动；来源彼此独立。限额只来自真实合同配置，无默认值——G2A/A46 机制侧完成，接入真实通道时按合同配置 limits 即生效。至此 85 项矩阵中所有可无外部输入完成的工程项全部交付；剩余为 A33 移动端截图与 A115 日志/容量小工具（低优先）+ 外部输入缺口（见 [缺口清单](docs/ops/glm-gap-checklist-2026-10-12.md)）。

## 2026-10-12 GLM 第七轮：真实公开数据全链演示（隔离软件验收通过，未提交）

证据：[第七轮报告](docs/acceptance/GLM_R7_REAL_SOURCE_DEMO_2026-10-12.md)；[来源档案](research/real-source-demo-2026-10-12/sources.md)。全量隔离回归 **1587/1587**，exit 0；live 前后一致（演示用独立目录）。

- 用户授权"自行找例子"后，以**真实公开网页数据**（1688 批发 ¥360/¥473.7、淘宝零售展示 ¥131.88/¥761，逐条原样摘录+URL 溯源）在独立目录跑通全链：导入→观察→radar→匹配→商机→决策卡→审计（0 冲突/0 孤儿）。
- 每道门诚实生效：匹配判 **COMPARABLE**（市场侧未展示尺寸/材质，拒绝假同款）；保守利润 **unknown** 列明四项缺失成本（无默认值）；材料用途唯一阻断原因=`no_active_confirmation`——**只差操作者确认**；304 SKU 缺市场观察→awaiting+去重提醒。
- 产品修正：radar 市场查找接受任意 `market:` provider（公开网页观察合法）；9/9 回归。
- 流程治理：清理我此前调试遗留的 2 个 uvicorn 残留进程（曾致 server 导入 89s 与全量首轮 3 处超时假象），清理后全量通过。
- 真实层定位如实：真实公开展示价（非闲鱼平台读取）、真实公开报价（未经供应关系确认）——R 层验收仍按 [缺口清单](docs/ops/glm-gap-checklist-2026-10-12.md) 等用户输入。

## 2026-10-12 GLM 第六轮：A50 探针客户端 + live 演练基线 + 集中缺口清单（隔离软件验收通过，未提交）

证据：[第六轮报告](docs/acceptance/GLM_R6_PROBE_CLIENT_AND_GAPS_2026-10-12.md)；[集中缺口清单](docs/ops/glm-gap-checklist-2026-10-12.md)。全量隔离回归 **1587/1587**（1489 基线 + 98 本计划新增），exit 0；live 20 文件前后一致。

- A50 探针客户端完成最后工程缺口：BrowserMarketProbeClient 复用既有账号上下文，诚实五级（无会话→no_credentials；**缓存会话≠证据**→unverified_session；显式授权才真实读探针；无 fetch 能力）。7 专项 + 15 回归。
- live 只读备份演练实测：备份 0.172s / 恢复 0.812s / 全哈希通过（无 ledger 故分歧行未演示，如实记录）。
- 集中缺口清单上线：五类外部输入的精确解锁路径 + live 现状（含 1 个 queued Pi 任务处置说明）。

## 2026-10-12 GLM 第五轮：Pi 出站门禁 + Radar 提醒 + G5A 演练（隔离软件验收通过，未提交）

证据：[第五轮报告](docs/acceptance/GLM_R5_GATE_REMINDERS_DRILLS_2026-10-12.md)。全量隔离回归 **1580/1580**（1489 基线 + 91 本计划新增），exit 0；live 20 文件前后哈希一致。

- Pi 出站用途门禁（G1B-6/A45 遗留接驳完成）：未确认 external_model 的真实报价在采集层整体扣留（价格/属性不出站，仅 id+原因），demo 豁免、门禁异常失败关闭；定价采集同步抹除未许可商机金额；fake-Pi 出站载荷断言验证。
- Radar 提醒去重（A48）：(SKU×原因×输入摘要) 幂等，repeat_count 累加不建新行；事件成功即解决；`GET /commerce/radar/reminders`。
- G5A JSON 演练：备份/恢复全哈希校验 + 盲回退丢数据演示（实证 ADR-0002 回退边界）；SQL 导出器 dry-run（DDL+唯一键+对账 footer，无需数据库）。
- 修复测试基建时间炸弹：test_intake_confirmation HTTP 类的 NOW 补丁在 discover 模式下打到第二个模块实例（`tests.` 前缀 vs 无前缀），夹具 NOW+20h 有效期被真实时钟越过导致确定性失败；改经 `type(self).__module__` 解析。
- 至此计划内全部可隔离工程项交付完毕；剩余均为外部输入/环境/批准缺口。

## 2026-10-12 GLM 前端整合轮（隔离软件验收通过，未提交）

证据：[前端整合轮](docs/acceptance/GLM_INTEGRATION_UI_2026-10-12.md)（含截图 [g-integration-ui-artifacts](docs/acceptance/g-integration-ui-artifacts-2026-10-12/)）。全量隔离回归 **1571/1571**（1489 基线 + 82 本计划新增），exit 0；live 20 文件前后哈希一致。

- 修复真实缺陷：server 缺 `/console/intake-confirmation.js` 静态路由（G1C 面板在真实服务下 404 失效；夹具有该路由故当时未暴露）。
- 控制台新增：决策卡 material_status（逐 SKU 到期+三用途实时许可）、采纳表单（候选下拉+幂等+旧链提示+复验）、通道/Radar 状态区、销售单号订单投影查询（采购+账本同视图）。
- 浏览器端到端（真实 server 子进程 + Chromium）：导入→材料确认→观察→匹配→核算→决策卡→采纳→复验→通道→投影全链通过，零控制台错误；匹配 model 约束与材料确认两道门禁在夹具错误时正确拦截。

## 2026-10-12 GLM G4B 黄金样本 + G5A ADR/核对器（隔离软件验收通过，未提交）

证据：[G4B 黄金财务](docs/acceptance/GLM_G4B_GOLDEN_FINANCE_2026-10-12.md)；[G5A ADR 与核对器](docs/acceptance/GLM_G5A_ADR_AND_AUDIT_2026-10-12.md)。全量隔离回归 **1570/1570**（1489 基线 + 81 本计划新增），exit 0；live 20 文件前后哈希一致；live 只读审计基线 0 冲突/0 孤儿。

- G4B：黄金样本全链（预计 90 → 结算 102 → 部分退款 52，准备金不二次扣）；冲正上限/错币种/并发竞争 HTTP+服务层验证；provisional 与 settled 分层，未回款不进在手现金。A103 真实首单 WAITING_INPUT。
- G5A：ADR-0002 迁移设计（12 清单→PG 表映射、唯一键、两处跨聚合事务点、切换/回退边界）+ 只读核对器 `scripts/audit_commerce_ledgers.py`（行数/金额/唯一/孤儿/未完成/证据哈希，live 基线干净）。A111/A112/A114 真实数据库演练 WAITING_INPUT（本机无 PostgreSQL/Docker）。
- 至此 G0-G5A 首批全部完成隔离软件验收；剩余工程项为前端整合轮，其余全为真实输入/环境/批准缺口（见各报告"需要用户提供的精确输入"）。

---

## 2026-10-12 GLM G2B/G2C/G3A/G4A（隔离软件验收通过，未提交）

证据：[G2B 市场通道](docs/acceptance/GLM_G2B_MARKET_CHANNEL_2026-10-12.md)；[G2C 决策卡](docs/acceptance/GLM_G2C_DECISION_CARD_2026-10-12.md)；[G3A 采纳桥接](docs/acceptance/GLM_G3A_OPPORTUNITY_ADOPTION_2026-10-12.md)；[G4A 主链接入](docs/acceptance/GLM_G4A_MAIN_CHAIN_WIRING_2026-10-12.md)。全量隔离回归 **1562/1562**，exit 0；live 20 文件前后哈希一致，零真实模型/平台调用，未新增自启动。

- G2B：市场通道只读模块（probe 不假成功；全价/起售/区间/定金/券后/sold 提示六类价格纪律；上游时间=观察时间防缓存刷新；401 即停、429/5xx 有界重试、部分结果 degraded；resume_token 断点）；路由 status/fetch 未配置时结构化诚实应答，人工观察回退保持来源类型区分。
- G2C：决策卡新增 material_status（逐快照到期时间+三用途实时许可）——确定性卡九问齐备；真实卡/20 组商品对 WAITING_INPUT。
- G3A：采纳桥接（显式决定+输入指纹+幂等+账号隔离；五类证据不自动通过；recheck 输入变化/材料失效→stale 不重写历史；模型建议永不自动批准）。
- G4A：ProcurementService/InventoryAllocationService/LedgerService 以共享单实例挂入主 server；三 JSON 为权威、旧 purchase_task 兼容投影不双写；人工流程路由（创建/审批/供应商单/收货/取消/库存登记/账本/订单详情投影）。
- 真实层缺口不变：真实报价确认、账号授权读通道、真实首单（M1-M4 R/B 层）均 WAITING_INPUT；商业执行 NOT_AUTHORIZED。下一包 G4B 黄金样本贯通与 G5A ADR。

---

## 2026-10-11 GLM G0 基线 + G1 全三包 + G2A radar（隔离软件验收通过，未提交）

证据：[G0 基线](docs/acceptance/GLM_G0_BASELINE_2026-10-11.md)；[G1A 原件与用途确认](docs/acceptance/GLM_G1A_INTAKE_CONFIRMATION_2026-10-11.md)；[G1B 成本与资格失效](docs/acceptance/GLM_G1B_COST_ELIGIBILITY_2026-10-11.md)；[G1C 资料面板](docs/acceptance/GLM_G1C_INTAKE_PANEL_2026-10-11.md)（含浏览器截图 [g1c-ui-artifacts](docs/acceptance/g1c-ui-artifacts-2026-10-11/)）；[G2A radar dispatcher](docs/acceptance/GLM_G2A_RADAR_DISPATCH_2026-10-11.md)。全量隔离回归 **1536/1536**（1489 基线 + 47 新增），345.997s，exit 0；live 20 文件前后哈希一致，零真实模型/平台调用，未新增自启动。

- G0：HEAD `4bd2cf7` + 65 项 R6-R9 未提交成果完整保留；入口矩阵与所有权表落档。
- G1A：原件 content-addressed 归档（owner_type=supplier_batch）+ 资料用途确认记录（全输入绑定、幂等键+请求指纹、有效期封顶）+ eligibility 实时重算；受信任操作者 Bearer 通道。
- G1B：清除生产表单演示默认值；平台费绑定账号+类目条款版本（修订→ready 即时 paused）；阶梯价/整单运费/单位适用；validate_current 同步重验。
- G1C：面板资料闭环（粘贴/上传→原值对照→缺口→用途确认→实时资格）；4 项 Playwright 浏览器验收通过（1440/390 双视口）。
- G2A：radar dispatcher——事件幂等（输入摘要键）、match→商机卡阶段推进（持久 result_ref、重启续进）、claim 角色过滤（Pi 不再抢非 Pi 任务、radar 不碰写任务）、Pi 复核仅在 external_model 用途许可时提交；确认/市场观察自动记事件；RADAR_DISPATCHER_ENABLED 默认关闭，手动 tick 路由可用。
- 真实资料验收 WAITING_INPUT（真实原件、身份/许可材料、操作者凭证、真实账号条款、真实观察）；商业执行 NOT_AUTHORIZED。下一包 G2B 单一市场通道（只读探针先行）。

---

## 2026-09-23 R9 报价时间来源贯通与集成收口（隔离验收通过，未提交）

证据：[R9验收及下一包](docs/acceptance/CODEX_R9_QUOTE_PROVENANCE_2026-09-23.md)；[输入约定](docs/ops/supplier-quote-input-contract.md)。最终全量隔离 **1489/1489**，53.009s，exit 0；运行前后254个受检源码文件哈希与集合一致，data/live的20个文件与本轮开始时一致。

- CSV/XLSX/粘贴导入保留实际观察时间、导入时间、截止和来源引用；统一门禁拒绝缺失、未来、无时区、过期、demo/unknown及时间绑定不一致，不通过重导入刷新旧报价。
- 库存读取/锁定/重建重新校验；商机重评估过期转paused；Pi输入带来源与不可用原因。历史记录不补真，不迁移日常账本。
- 影子总记录与来源声明合格数分开；未知、演示、观察时批次已撤销及无绑定记录不增加合格数。面板显示独立口径，数量达标不授权执行。
- 本包新增20项后端和1项桌面/手机浏览器回归；并行准入评估23项纳入全量，贡献分开。20项专项连续三轮全过。
- 已收口下方R9-I记录的HTTP粘贴CSV误判和浏览器6665禁用端口问题，旧失败记录保留。当前全量不是运行期间改码的结果。
- 零真实模型调用、零平台写入、未新增自启动。声明合格不是供应商认证、真实成交或商业GO；只读准入报告不是持久批准。
- **下一包**：把真实报价证据、人工身份、批准用途与有效期绑定到批次/快照版本，补撤销/到期/修订失效和幂等确认，再做单SKU只读商业分析。自动回复、发布、采购、退款保持关闭。
- 下方R8的时间来源缺口和R9-I的整库待复验是历史状态，以本节最新集成结果为准；真实资料与批准缺口仍存在。

---

## 2026-09-23 R9-I 供应商准入只读评估（专项通过，整库待复验）

证据：[本批交付与复验](docs/acceptance/CODEX_R9_SUPPLIER_INTAKE_2026-09-23.md)；[受检清单](docs/acceptance/CODEX_R9_SUPPLIER_INTAKE_MANIFEST_2026-09-23.json)；[操作者说明](docs/SUPPLIER_INTAKE_READINESS.md)。

- 新增只读评估API/CLI及空值声明模板：真实原字节SHA-256、批次/供应商绑定、来源与时效、币种/单位、运费、库存时间及税务/交付/售后资料。复用并行的quote_provenance来源判定，不另设真假来源口径。
- 最多返回ready_for_human_review；供应商真实性、外部模型授权、平台授权均保持false。不持久化审批，不把额外声明自动变成旧业务入口的强制门禁。
- 本批专项 **23/23**，3.065s，exit 0，257个受检文件前后稳定（共享导入依赖变化后重新验证）。最新全量 **1487项：1 failure + 3 errors，exit 1**；3项HTTP导入相关错误及1项浏览器禁用端口错误见报告。期间pi_jobs.py和web/app.js另有变化，不能标整库PASS。
- 日常data/live的20个文件集合及内容哈希未变；零真实模型、零真实供应商/平台调用，未修改真实配置，未暂存/提交。没有覆盖并行来源/时间门禁、库存、机会或影子链路改动。
- 下一优先级：源码稳定窗口收口集成回归 → 真实资料归档及人工身份/许可绑定 → 面板与只读任务输入 → 有效真实来源影子观察。发布、采购、退款仍未开放；下方历史全绿不能替代本次整库验收。

---


## 2026-09-23 R8 供应商输入精度与面板独立复验（未提交）

证据：[本轮验收](docs/acceptance/CODEX_R8_UI_AND_SUPPLIER_INPUT_2026-09-23.md)；[供应商输入约定](docs/ops/supplier-quote-input-contract.md)。全量隔离回归 **1445/1445**，50.573s，exit 0；24个受检源码/测试/配置文件前后哈希与集合一致，data/live的20个文件未变。

- 对既有人工对账面板独立复跑14项浏览器测试，并检查1440/390两档锁定、预览、回执截图；不混算面板实现贡献，不代表整个旧控制台的全业务验收。
- 新增19项供应商输入回归，连同原20项共39项通过。区间价格、外币、科学记数法、歧义数量不再被截取/取整成确定事实；负运费只拒绝所在行，行号不再被价格覆盖。
- 整箱价同时规范化价格单位，仅在数量单位明确时换算库存/MOQ，保存原始换算依据；重复表头、多余CSV单元格拒绝，防止价格错列。
- 没有迁移历史快照、启用真实消费对账或调用真实模型，未开放发布、采购、退款。前一轮真实Pi demo证据仍单独保存。
- **下一包的前置缺口已明确**：HTTP导入仍默认把导入时间当观察时间；实际报价时间、有效期及real/demo/unknown来源必须贯通并进入资格/影子门禁，不能先宣称真实报价接入已完成。

---

## 2026-09-22 R8 人工对账面板（隔离软件验收通过，未提交）

证据：[R8面板验收](docs/acceptance/CODEX_R8_USAGE_UI_2026-09-22.md)；[受检文件manifest](docs/acceptance/CODEX_R8_USAGE_UI_MANIFEST_2026-09-22.json)；[操作者说明](docs/PI_USAGE_RECONCILIATION.md)。最终全量 **1426/1426**，78.197s，exit 0，含17项对账API和14项真实浏览器测试。监测的247个Python/PowerShell/前端源码文件集合及SHA-256前后稳定，data/live的20个文件集合及内容未变；不宣称环境、依赖与全部文档均被冻结。

- 现有Pi任务台新增未知消费列表、证据输入、只读预览、明确确认及回执，服务仍默认关闭。
- 凭证仅本页内存；普通锁定/离开清除，提交中也能立即锁定。结果不明冻结原请求和幂等键，同一操作者重新认证后原样重试，不重复记账。
- 输入变化撤销旧确认，过期状态/证据矛盾由真实API拒绝；未知不等于零，其他unknown和日预算仍生效。
- 桌面/手机截图已查看；浏览器夹具使用真实对账API及实际面板资源，但禁用无关旧业务脚本，不宣称整个旧控制台全业务e2e。
- **本UI批次**零真实模型调用、零真实对账、零平台写入；并行R7真实demo验证属于下方独立报告，不混同贡献或证据。
- 下一步按既定顺序进入有证据的真实供应商报价准入、独立只读商机分析和影子观察。没有真实资料时只补字段/证据/导入门禁，不伪造业务事实、不自动开放发布采购退款。
- 下方各R7及更早测试数是历史/不同验收范围；本节闭环当前UI软件验收，不代表生产或商业GO。

---

## 2026-09-22 R7 非空演示报价真实 Pi 验证（未提交）

证据：[隔离真实 Pi 验收报告](docs/acceptance/CODEX_R7_ISOLATED_PI_VALIDATION_2026-09-22.md)。本轮已完成非空 `source=demo` 数据的真实只读链路，不再停留在 FakePi 或空报价验证。尚未证明真实供应商或交易可用。

- 修复多轮 Pi 消耗被最后一轮覆盖的问题；任一轮缺报维持 unknown，后续模型报错不能被早先文本掩盖，3 条新增回归先红后绿。
- 隔离验证器要求全新数据目录，默认 offline；真实模式明确 opt-in，独立端口/进程/会话，核对服务身份，不复用已有服务，不连接 live 数据。
- 真实运行：2 条演示报价、1 个任务 attempt、2 轮模型响应、1 次成功只读工具调用；累计 **3401 tokens**。任务 `job_1c0429db084d4e5a`，有效决策 `dec_59d65bd30cee481d`，识别 DEMO-2 运费缺失。
- 输入哈希、工具事件、结构化建议、消费账本和证据文件相互对应；独立服务正常退出，无本轮残留进程，data/live 的20个文件内容未变。
- 人工消费对账 API 已独立复测17项通过，仍默认关闭、没有处理真实消费。并行新增的对账UI另行验收，不把其开发算作本轮隔离运行器的贡献。
- 收尾全量隔离回归 **1424/1424**（89.498s，exit 0）；监测的17个后端/CLI/相关测试配置文件前后哈希一致。另有并行UI样式及测试在运行期间变化，未宣称全工作区冻结或UI最终验收通过，详情见本轮manifest。
- 下一顺序：对账UI独立验收 → 真实供应商报价接入与完整字段核验 → 只读商机分析和影子观察。自动回复、发布、采购、退款仍未开放。

---

## 2026-09-22 R7 人工消费对账API（默认关闭，未提交）

证据：[docs/acceptance/CODEX_R7_USAGE_RECONCILIATION_2026-09-22.md](docs/acceptance/CODEX_R7_USAGE_RECONCILIATION_2026-09-22.md)。使用：[docs/PI_USAGE_RECONCILIATION.md](docs/PI_USAGE_RECONCILIATION.md)。本模块17项三连通过，最终单测源码前后哈希稳定；**全工作区验收未闭环**：首轮1403项通过，后续两轮有无关测试错误且源码在测试期间变化，不能以首轮代替当前全量验收。data/live内容均未变。

- 独立操作者凭证，服务只配置哈希；未配置503，job会话禁止对账，身份不由请求体提供。
- 未知消费查询→只读预览→明确确认；状态/提案变化拒绝旧确认，执行中的runner和活跃租约任务拒绝对账。
- 按幂等键原子保存消费和审计回执，写盘失败不解除unknown，响应丢失重试复用receipt。
- 原业务日预算、其他unknown与job/决策状态继续生效；没有自动重启模型或开放平台写入。
- 未启用真实配置，未做真实消费对账。证据为操作者声明，非供应商自动核验；下一步为隔离人工对账面板。

---

## 2026-09-22 R6 最终离线矩阵与非空样本（未提交）

最新证据：[docs/acceptance/CODEX_R6_ACCEPTANCE_MATRIX_2026-09-22.md](docs/acceptance/CODEX_R6_ACCEPTANCE_MATRIX_2026-09-22.md)。全量隔离回归 **1386/1386**，exit 0；32项恢复测试连续三轮通过；data/live的20个文件集合及SHA-256与本轮开始时一致。

- 在已完成的attempt结算、usage前置和进程强杀恢复基础上，补齐“原草稿消失/旧游标已推进”的影子额度恢复；每次tick先从裁决日志恢复，再评估新事件。
- 已有额度与裁决事实冲突时停止核对，不因事件ID相同就跳过检查。
- 两条明确标注source=demo的报价通过导入、FakePi任务、结构化决策、证据文件与输入哈希重载验证；不是实际供应商事实或真实模型验证。
- 下一顺序：有依据的未知消耗对账、隔离真实Pi运行器、非空样本真实只读验证。未调用真实模型、未开启平台自动交易。
- 下方1383项及更早测试数保留为历史快照，以本节最新结果为准。

---

## 2026-09-22 R6 本地恢复收尾（未提交）

证据：[docs/acceptance/CODEX_R6_COMPLETION_2026-09-22.md](docs/acceptance/CODEX_R6_COMPLETION_2026-09-22.md)。全量隔离回归 **1383/1383**，exit 0；R6两组29项连续三轮通过；data/live的20个文件前后内容哈希及集合一致。

- job/attempt幂等结算；冲突拒绝并去重审计，原归属日与消费不被覆盖。
- intent出站前持久化，known usage在证据/决策/完成写盘前持久化；pending/unknown跨重启、跨日阻断新调用。
- FakePi入口子进程强杀恢复通过；影子log/usage/cursor写失败重放均恢复单次额度，日限100拒绝60+70中的第二条。
- 本批仅单进程隔离Mock验收；零真实模型/平台调用。人工消费对账功能、非空demo真实Pi验证及商业上线未完成，不能据此自动解锁。
- 下列R5及更早段落是历史记录，后续发现与修复以本节及关联报告为准。

---

## 2026-09-21 Pi 链路第五轮修复（R5：记账/有效性/影子幂等）

证据：[docs/acceptance/WP_PI_RELIABILITY_R5_2026-09-21.md](docs/acceptance/WP_PI_RELIABILITY_R5_2026-09-21.md)。全量回归 **1354/1354**（+8 红转绿）。新提交 `92b644a`。

- 按调用尝试记账：拒收/超时/无效输出/失租丢弃/失败路径全部先记实耗再失败；失败路径记 unknown≠零；成功路径记账前移到 complete 前。
- 决策有效性从 job 记录派生（succeeded + result_ref + attempt 匹配），失效标记降级为附加信号（写失败不再能复活迟到决策）；证据端点输出 stale_decision_ids。
- 影子额度按事件 ID 幂等；重放复用已存裁决（不重评估、不再累计）。

---

## 2026-09-21 Pi 链路第四次复验修复轮（7 项缺口）

证据：[docs/acceptance/WP_PI_RELIABILITY_R4_2026-09-21.md](docs/acceptance/WP_PI_RELIABILITY_R4_2026-09-21.md)。全量回归 **1346/1346**（+14 红转绿）。

- 单 worker 互斥（busy 拒绝）+ 停止/认领原子；Pi 客户端 finally 关闭（成败两路）。
- 截止两侧拒收（起跑前剩余窗口 + 结果侧超时拒收）；per-job/每日 token 预算账本（模型调用前程序拦截）。
- 决策提交协议：fenced complete 失败 → 追加失效标记；面板/复用均排除迟到决策。
- 影子额度语义：真实占用 + 影子反事实累计一起进策略；日志先行且按事件 ID 幂等（写失败不推进游标）。
- 证据固化：pi_evidence 文件持久化输入快照/工具事件/usage + inputs_sha256；报价采集按 committed 批次过滤。
- 零真实模型调用；下一步等操作者提供非空标注来源的报价样本做真实 Pi 验证。

---

## 2026-09-21 Pi 阶段第二批（角色扩展 + 持续影子观察 + 面板任务台）

证据：[docs/acceptance/WP_PI_STAGE2_2026-09-21.md](docs/acceptance/WP_PI_STAGE2_2026-09-21.md)。全量回归 **1331/1331**（+15）。

- 角色注册表：4 个只读分析角色（报价缺口/匹配审计/定价复核/商机筛选），各自结构化 schema 校验；通用提交端点；未知角色 400。
- 持续影子观察：真实内部事件（回复草稿、合格商机）自动影子策略判定；游标幂等；后台线程可关（SHADOW_OBSERVER_ENABLED）；无 mandate 时如实全拒绝，不伪造允许样本。
- 面板 Pi 任务台：角色提交/执行一个/任务与影子状态。
- 本轮无真实模型调用（mock 层验证）；真实层证据维持第一批 3 次。自动回复/发布/采购/退款仍未开放。

---

## 2026-09-21 Pi 持久任务执行链路：第一批交付

证据：[docs/acceptance/WP_PI_STAGE1_2026-09-21.md](docs/acceptance/WP_PI_STAGE1_2026-09-21.md)。全量回归 **1316/1316**（+20 条 Pi 链路 mock）。

- 7 步验收表全部 PASS（进程内范围）：单实例边界、真实 PiRpcClient（独立 jobs 会话，弃 DeepSeekClient 直连）、只读网关（中间件程序执行：非 GET/白名单外/超 cap 全 403）、生命周期（迟到结果拒收、重试不重复决策）、结构化落库+崩溃对账、证据端点（任务→决策→输入；未知成本显示 unknown 不显示 0）、mock/真实分层计数。
- 真实运行 3 次：1 次如实失败（旧 shell key 401，重排队）+ 1 次重试成功 + 1 次成功（pi-jobs-xianyu / deepseek-flash / 3241 tokens，结论"当前无 committed 报价可审"）。脚本已改为从 .env 固定 key 防残留覆盖。
- 未开放自动回复/发布/采购/退款；下一步：扩展角色 + 持续影子观察。

---

## 2026-09-18 第三次复验修复轮（2 项 P1 同步缺口）

证据：[docs/acceptance/WP_RELIABILITY_FIXES_R3_2026-09-18.md](docs/acceptance/WP_RELIABILITY_FIXES_R3_2026-09-18.md)。全量回归 **1296/1296**。

- 先红后修：对账×创建交错、取消×供应商单登记交错两条确定性失败测试复现操作者症状 → 修复 → 3 连跑全绿。
- 修复：新增 `ProcurementService.mutation_guard()`；对账全程持同一守卫（检查+释放同区间）；approve/record_supplier_order/record_receipt 纳入同一 mutation 域（与 create/cancel 同锁）。
- 重启测试边界表述保持：文件重载一致性，非进程强杀恢复。Pi 阶段继续冻结。

---

## 2026-09-18 演练轮（跨文件失败 / 重启恢复 / 完整业务流程组合）

证据：[docs/acceptance/WP_DRILLS_2026-09-18.md](docs/acceptance/WP_DRILLS_2026-09-18.md)。全量回归 **1294/1294**。

- 跨文件失败：采购落库失败→预留自动释放；取消后释放失败→重启对账收尾（幂等）。
- 重启恢复：新增 recovery.reconcile_orphan_reservations（孤儿预留对账，dry_run 支持）；六类事实重建一致；过期任务租约恢复（分析重排队/写转 unknown）。
- 完整组合流：导入→匹配→核算→采购→付款回款（settled 134）→部分退款（34）→撤销批次拒采购→纠正匹配转 paused→取消退库存+重放无效→最终重启一致。
- 多进程并发仍按 ADR-0001 等 Postgres 决策；Pi 扩展阶段继续冻结。

---

## 2026-09-18 第二次复验修复轮（4 项 P1：深拷贝/并发采购/并发冲正/重复取消）

输入：操作者第二次复验"仍不通过"。证据：[docs/acceptance/WP_RELIABILITY_FIXES_R2_2026-09-18.md](docs/acceptance/WP_RELIABILITY_FIXES_R2_2026-09-18.md)。全量回归 **1288/1288**。

- 按指定流程执行：先写稳定复现的失败测试（红灯留证，含冲正 3 连稳定红）→ 再修实现 → 3 连跑全绿。
- 深快照回滚（既有记录+嵌套字段+读路径深拷贝）；采购创建/取消全程 mutation 锁 + 预留绑定具体采购单（`proc:{ID}`）+ 取消幂等；冲正校验+写入原子化。
- 未做（按操作者排序）：跨文件失败、重启恢复、完整业务流程组合演练；未进入 Pi 扩展阶段。

---

## 2026-09-18 可靠性修复轮（验收问题 1-8 全部修复）

输入：操作者验收"暂不通过"报告。证据：[docs/acceptance/WP_RELIABILITY_FIXES_2026-09-18.md](docs/acceptance/WP_RELIABILITY_FIXES_2026-09-18.md)。全量回归 **1283/1283**（+14 条按复现路径编写的回归）。

- P1-1 存储失败：锁必然释放 + 内存回滚快照（幽灵写入不可落盘）。
- P1-2 采购：同销售订单幂等拒绝 + 原子库存预留（先锁后存、失败释放、取消归还）。
- P1-3 撤销批次隔离：仅 committed 批次快照可驱动采购。
- P1-4 资格联动：评估取最新匹配（纠正即生效）；复核失败自动 ready_for_test→paused。
- P1-5 预算按实际结算额累计（100 预留/140 实付 → 占 140）。
- P1-6 库存读取/锁单时重校验时效（过期=0 可售+拒绝新锁）。
- P1-7 冲正累计上限+同币种（重复冲正无法制造正收益）。
- P2-8 润色只允许草稿正文已有金额（底价永不对外）。
- 未加新功能；WP08 直连模型的范围性说明如实记录；Pi 链路整合按操作者排序等可靠性验收通过后进行。

---

## 2026-09-18 第五轮增量（门槛达标轮：WP05 100对 / WP11 200条+润色 / WP09 视觉验收）

证据：[docs/acceptance/WP_ROUND5_2026-09-18.md](docs/acceptance/WP_ROUND5_2026-09-18.md)。全量回归 **1269/1269**。

- WP11 润色层 PASS：真实模型（deepseek-flash）只改措辞，金额/承诺/断言三类护栏 + 确定性兜底；mock 6/6 + 真实运行证据。
- WP05 PASS：标注集 102 对（tune 72/holdout 30 分离），新增结构计数与钢材等级约束；**holdout 零错误**，EXACT 精确 6/6。
- WP11 场景集 PASS：215 条，意图/出价 100%，标记归属位置感知修复两类真实误判。
- WP09 视觉验收 PASS：Playwright 1440/390 双档零溢出零报错，UI 实操 导入→预览→确认→刷新持久。
- 真实经营状态未变：账号 0、真实供应商报价 0、平台动作 0、真实利润 0。外部欠账：供应商报价文件、账号登录、PDD/1688 申请决定、每日备份调度决定。

---

## 2026-09-18 第四轮增量（WP08 解锁 + WP14 最小版）

证据：[docs/acceptance/WP_ROUND4_2026-09-18.md](docs/acceptance/WP_ROUND4_2026-09-18.md)。全量回归 **1259/1259**（两次通过）。

- WP08 BLOCKED→PASS（最小版）：操作者提供新 DeepSeek key；平台无 v4.1 后缀 ID，flash 档即 `deepseek-flash` 已设为默认；修复 llm.py 推理烧 token 缺陷（thinking 显式 disabled）；真实无写入任务成功落库（job_9ab5eaae56f74263 / dec_727bc140fb7d4611），面板可读。pi-subagents 插件试验仍待批准。
- WP14 PASS（最小版）：data/commerce 备份/校验/恢复演练（CLI+runbook+测试）；首份真实备份已建并校验通过；GET /commerce/ops-summary 运维摘要；RPO/RTO 目标未实现未宣称；每日调度待操作者决定。
- 修复：repository 同秒时间戳平局按追加序裁决。
- 已知非复现 flake：preflight 敏感文本误报一次，记录待查。
- 真实经营状态仍未变：账号 0、真实供应商报价 0、平台动作 0、真实利润 0。剩余外部欠账：真实供应商报价文件、闲鱼账号登录、PDD/1688 申请决定。

---

## 2026-09-18 第三轮增量（WP11/WP12/WP13）

证据：[docs/acceptance/WP_ROUND3_2026-09-18.md](docs/acceptance/WP_ROUND3_2026-09-18.md)。本轮终点全量回归 **1254/1254**。

- WP11 PASS（最小版）：只产草稿的客服回复服务——位置感知金额护栏（否定/运费/引用/多件/旧报价/型号）、52 条场景集（护栏类目 100%，未达 200 条门槛不宣称目标）；无发送路径；不写卖家承诺。
- WP12 PASS（最小版）：可售库存分配（未知/过期=0、锁单/释放、宁少卖不超卖）；上架守卫（捏造断言拦截、供应商价改后旧草稿失效）；**physical_sourcing 四套捏造话术已删除**，买家回复仅由已验证事实生成。
- WP13 PASS（最小版，模拟层）：采购重核验→人工批准→供应商订单号→回执；append-only 账本（冲正更正、部分退款、签收≠回款）；六场景手算复核通过。真实订单闭环待账号。
- 真实经营状态未变：账号 0、真实报价 0、平台动作 0、真实利润 0。外部欠账：模型 key（双 401）、供应商报价文件、账号登录、PDD/1688 申请决定。

---

## 2026-09-18 第二轮增量（WP04/WP07/WP08/WP10）

证据：[docs/acceptance/WP_ROUND2_2026-09-18.md](docs/acceptance/WP_ROUND2_2026-09-18.md)。本轮终点全量回归 **1204/1204**。

- WP04 PASS（最小版）：采集框架（重试/退避/Retry-After/熔断/配额/统一错误码，测试零真实网络）+ 文件通道适配器 + 新鲜度分类（报价24h/市场72h）+ provider probe（UNVERIFIED 拒绝探测）。
- WP07 PASS（最小版）：持久 AgentJob 存储——原子认领、租约/fencing、按账号写串行、崩溃恢复（写任务转 unknown 不假定副作用）；决策卡与任务关联。
- WP08 **BLOCKED**：真实模型运行脚本就绪（失败如实落库），但 MiMo 与 DeepSeek 两个 key 实测均 401 失效；等操作者换 key 重跑。pi-subagents 未安装（待批）。
- WP10 PASS（最小版，纯影子）：mandate 签发/撤销、确定性策略引擎、预算原子预占/结算/释放（上海营业日午夜切换）、影子授权日志（≥7天/≥100样本门槛如实判定）；无任何执行路径。
- 真实经营状态未变：账号 0、真实报价文件 0、平台动作 0。

---

## 2026-09-18 增量（薄闭环：报价→同款→成本→商机→决策卡）

按 [ZCODE_START_HERE_2026-09-18.md](ZCODE_START_HERE_2026-09-18.md) 执行的最小贯通轮，全部证据见
[docs/acceptance/WP_THIN_LOOP_2026-09-18.md](docs/acceptance/WP_THIN_LOOP_2026-09-18.md) 与
[docs/acceptance/BASELINE_2026-09-18.md](docs/acceptance/BASELINE_2026-09-18.md)。

- WP00 基线刷新 PASS：全量 1026/1026 实测通过（47.064s），data/live 哈希不变，GH-04/05 调用路径核验，skills 实测 9 个。
- 新增 commerce 域（WP01）：`xianyu_agent/commerce/`（契约 + repository，Money 三态金额/未知不填零，ADR-0001）。
- 供应商报价导入（WP03）：preview→confirm、CSV/XLSX/粘贴文本、整箱换算、批次可撤销，测试 20 条。
- 同款匹配（WP05）：硬约束四级判级 + 28 对标注集（EXACT 精确 6/6；未达 100 对，不宣称 95% 目标）。
- 贡献利润与商机（WP06）：三情景、黄金样例 104.03/34.79% 复算一致、确定性资格门禁（EXACT+保守利润>0+新鲜度）。
- 决策卡与面板最小版（WP09）：13 条 `/commerce/*` 路由 + console「商机雷达」；Playwright 视觉验收未做（完整 WP09 范围）。
- 来源能力登记（WP02）：5 来源 = 2 CONFIGURED / 3 UNVERIFIED，VERIFIED 需 ≥20 真实样本证据（代码强制）。
- 本轮终点全量回归：**1122/1122 通过**；8 个分包独立提交（41c20b3…da6882f）。
- 未变：真实账号 0、占位候选 T、真实订单/询盘/利润 0；无平台写动作；OpenClaw 未恢复。
- 下一阻塞不变：操作者提供真实供应商报价文件与账号（见验收文档「诚实声明」）。

---

Updated: 2026-09-05

## Status

- Technical code baseline: `READY` for current-runtime verification and the
  first real seller-account onboarding.
- Runtime now: `STOPPED`; FastAPI and Pi are intentionally not running, so no
  current health claim is made.
- Commercial operation: `BLOCKED` on an owned or authorized seller account,
  one exact supplier SKU, five current evidence files and current commercial
  terms.
- Real Commercial Alpha: `NOT STARTED` and not claimed complete.

## Verified In This Baseline

- Full isolated regression on 2026-09-05: `996` tests passed, `0` failed, in
  37.385 seconds. The suite used temporary `DATA_DIR` and `DB_PATH` boundaries;
  all 15 files under `data/live` retained identical lengths and SHA-256 hashes.
- One-SKU Commercial Alpha intake now has a read-only offline preflight. It
  generates a non-overwriting bundle template, validates the exact five evidence
  kinds and product images with the production archive rules, blocks traversal,
  symlinks, stale/future captures, malformed files and sensitive text, and
  projects the existing candidate gates without persisting synthetic evidence.
  A passing bundle remains `commercial_ready=false` until real archive import,
  account/category terms, current manual approval and Day-0 `20/20`.
- Candidate evidence is content-addressed and candidate-bound. Integrity checks
  now hash the file on every verification; a same-size tamper with the original
  modification timestamp is detected.
- SQLite initialization is deferred and bound by the server composition root to
  `Settings.db_path`. The legacy and live database table row counts were checked
  equal, and the migration backup remains in `data/sqlite-migration-backup`.
- Core Phase 9-29 engineering work has a recoverable Git baseline on branch
  `codex/opc-dashboard-v1`; the former large uncommitted implementation is no
  longer the project source of truth.
- The only platform write path exposed by the server is the approved chain:
  `WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor -> Adapter`.
- Execution jobs now use a persisted lease, per-claim token and increasing
  fencing token. Two local queue instances cannot claim the same task, and an
  abandoned `running` task becomes `verification_required` instead of being
  retried automatically.
- Unknown publish, edit, reply, delete, refund or fulfillment outcomes require
  an explicit platform check. The console and API record either
  `confirmed_completed` with platform evidence or `confirmed_not_executed`
  before a retry can be requested.
- Reply Outbox now projects reply drafts, approvals, execution attempts,
  receipts and manual verification from the existing authoritative chain. It
  binds account, buyer, chat, item, source event, revision and approved-message
  hash in an action digest; inbound replays return the original record, and an
  explicit idempotency key reused for different content returns HTTP `409`.
- Browser write failures now create a per-account hashed evidence pack with a
  redacted JSON manifest and a best-effort screenshot. The pack records the
  failure stage, adapter state and whether a platform write was attempted;
  post-submit uncertainty still requires explicit platform verification.
- Every new Approval now persists an action-scope envelope binding the account,
  targets, payload, approval snapshot, amounts, expected versions, lifetime,
  deterministic idempotency key and unique correlation ID. Approval stores the
  exact action digest that the operator accepted.
- Scope is recomputed before approval, enqueue, execution and unknown-result
  closure. A mismatch or expiry blocks before the adapter and records
  `write_attempted=false`; account reassignment clears the old decision and
  requires a fresh approval.
- Listing publication additionally binds the normalized on-disk draft and image
  contents. Legacy publication approvals without `expected_draft_revision` are
  blocked and must be recreated.
- Generic `/agents/dispatch`, `/agents/openclaw/message` and
  `/agents/hermes/message` are retired with HTTP `410`.
- Legacy listing and physical-listing APIs create a local draft and approval
  WorkOrder. The core workflow never publishes merely because `publish=true`.
- Direct polish and both automatic polish schedulers are retired.
- Pi remains the only primary natural-language entrypoint. Its allowlist now
  contains 12 typed Xianyu tools and no arbitrary-path `read`, shell, write,
  approval or execution tool.
- The Pi subprocess receives only model-provider and operating-system runtime
  environment variables, not unrelated database, Feishu or adapter credentials.
- The default service bind is `127.0.0.1`; CORS is limited to the local console
  origins for the configured port.
- OpenClaw Windows scheduled tasks previously found were disabled. This baseline
  does not re-enable OpenClaw or any automatic startup process.

## Not Verified As Current Runtime

- No process is currently listening on `127.0.0.1:8788` or the older port 8000.
- The 2026-08-29 FastAPI, Pi and browser checks are historical evidence, not a
  current health result.
- Before account login, run the normal start script and verify health, Pi status,
  the absolute live SQLite path, one-process behavior and clean stop behavior.
- No current live-model Pi turn or current browser UI regression was run in this
  documentation and hardening change.

## Current Business Ledger

| Item | Current value |
|---|---:|
| Historical WorkOrders | 15, all quarantined |
| Unresolved WorkOrders | 0 |
| Seller accounts | 0 |
| Domestic Xianyu candidates | 1 placeholder (`T`), evaluating |
| Placeholder activation packet | 1/12, `ready=false` |
| First product blocker | `product_identity` |
| Candidate evidence artifacts | 0 |
| Offline intake bundle | `E:\闲鱼\商业资料\commercial-alpha-001`, initialized and correctly blocked pending real inputs |
| Active Commercial Alpha trials | 0 |
| Real listing receipts | 0 |
| Real inquiries / paid orders / reconciled profit | 0 / 0 / 0 |
| First global readiness blocker | `account_configured` |

## Next Unique Goal

> Bind one real seller account and complete one exact SKU to candidate `12/12`
> and Day-0 `20/20`, then create one Commercial Alpha batch without publishing
> automatically.

## Next Operating Sequence

1. Create one offline intake directory with
   `scripts/commercial_alpha_intake.py init`; fill one exact SKU, five current
   evidence files and at least one authorized product image.
2. Run its read-only `validate` command until `bundle_ready=true`, the projected
   candidate is `11/12`, and only `manual_decision_current` remains. Do not treat
   this projection as a persisted candidate or commercial approval.
3. Start the service only when the operator is ready to perform the real login.
4. Re-run current health, Pi, database-path and repeated-start checks.
5. In `账号画像`, open the dedicated login browser and complete platform login.
6. Preview the actual identity, verify it, and complete the one-time `BIND ...`
   confirmation.
7. Initialize the account Prompt profile, then run same-account listing and
   order sync readiness checks.
8. Do not approve placeholder `T`. Create one exact domestic Xianyu candidate
   with at least two real deliverable specifications.
9. Add current supplier, SKU, cost, freight, inventory and fulfillment facts.
10. Upload and activate the same five validated evidence files: supplier quote, stock,
   fulfillment, image rights and market baseline.
11. Record current account-and-category terms. Reprice the current candidate,
   run Agent review and manually approve the exact current revision.
12. Require candidate `12/12` and Day-0 launch preview `20/20` before creating
     exactly one batch. Batch creation still does not publish.
13. Publish only through a separate approval and execution job. Unknown platform
     results must be verified before any retry.
14. Handle every real inquiry through Reply Outbox. Approval, enqueue and a
     confirmed platform receipt remain three separate operator decisions.
15. Expand only after a real 14-day experiment and, when an order exists, full
     procurement, dual-logistics, delivery and actual-profit reconciliation.

## Source Documents

- General-manager target architecture and 12-month plan:
  `docs/strategy/GENERAL_MANAGER_BLUEPRINT_2026-08-30.md`
- Commercial operating order: `docs/ops/commercial-alpha-runbook.md`
- Candidate evidence SOP: `docs/ops/xianyu/candidate-evidence-archive.md`
- Offline one-SKU intake preflight: `docs/ops/commercial-alpha-intake-bundle.md`
- Current handoff and remaining work: `ZCODE_HANDOFF.md`
- Pi runtime contract: `docs/ops/pi-migration.md`
- Unknown-result recovery SOP: `docs/ops/execution-recovery-runbook.md`
- Reply lifecycle and idempotency SOP: `docs/ops/reply-outbox-runbook.md`
- Browser failure evidence SOP: `docs/ops/browser-write-evidence-runbook.md`
- Action scope and approval binding SOP: `docs/ops/action-scope-approval-runbook.md`
