# 闲鱼 Commercial Alpha 试运营手册

> 目标不是证明页面能打开，而是用一个真实卖家账号、一个品类、最多 3 个商品，在 14 天内跑通可审计的经营闭环。

## 1. Alpha 边界

- 经营范围：先做一个主品类，默认商用厨具或已验证的垂直品类。
- 账号范围：系统支持多账号，但首轮只选择一个账号作为 `selected_account`。
- 商品范围：首轮 1 个商品，稳定后最多同时测试 3 个商品。
- 资金范围：为试运营设置固定采购预算；未记录供应成本、运费和平台费的商品不得放行。
- 执行路径：所有发布、回复、发货、删除和退款统一经过 `WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor`。
- 旧接口：`/listings`、`/orders/paid` 和 `/browser/*` 写操作只创建草稿或审批，不再直接触碰平台。
- 数据隔离：真实经营数据写入 `data/live`；测试只能通过 `scripts/run_tests_isolated.ps1` 写入临时目录。

## 2. 真实经营顺序

| 阶段 | 操作者动作 | 系统证据 | 放行条件 |
|---|---|---|---|
| 0. 本地验证 | 使用隔离测试脚本和 `stub` 跑完整审批与队列 | 临时目录中的 WorkOrder、Approval、ExecutionJob、outbox | 运行 `scripts/run_tests_isolated.ps1` 全部通过，真实数据目录无变化 |
| 0a. 历史任务隔离 | 只读审阅所有缺失账号或归到未配置账号的旧任务，逐单决定认领或隔离 | unassigned-work review + preview token + resolution event | 不允许隐式批量绑定；实盘账号不得继承未经逐单确认的旧审批、执行任务或回复草稿 |
| 1. 账号接入 | 打开专用浏览器并完成平台登录；只读检测实际账号；核对后用一次性确认语句绑定 | `/accounts/browser-import/preview`、`/accounts/browser-import/commit`、`/accounts/runtime` | 平台登录探测成功；预览与提交账号/会话一致；账号存在且启用 |
| 2. 运行验证 | 启用真实 adapter，执行订单和商品同步 | `/adapter/sync-persist`、runtime state | 同一账号订单与商品同步均成功，认证证据 300 秒内有效 |
| 3. 经营配置 | 初始化账号 Prompt 和库存上下文 | prompt profile、account queue | Prompt 完整，商品上下文可用 |
| 3a. 商业条款核验 | 在当前账号的目标类目发布/费用页面核对经营身份、支付、买保、退货、保证金和实际费用 | account commercial terms snapshot | 快照按账号+类目隔离、不超过 24 小时，候选利润已按同一版本重算 |
| 4. 一品启动包 | 锁定精确商品/SKU，核对供应商、报价、库存、履约、图片权利和竞品基线，并上传五类原始证据 | 12 项 candidate activation packet + immutable evidence archive + evidence revision | 五类证据均为当前候选的活动存档且 SHA-256 完整；除最终人工决策外的 11 项门禁通过 |
| 5. 商品核算 | 使用当前账号类目的比例费率和每单固定费用，记录成本、目标售价和退款损耗 | candidate economics、commercial terms version、scoring | 毛利额和毛利率达到配置阈值，无 blocking risk，费用版本一致；条款变化使旧决策失效 |
| 6. 商品决策 | 对重算后的当前修订完成 Agent 评审，再由操作者 approve/watch/reject | agent review、manual decision、approved evidence fingerprint | approve 指纹与当前证据完全匹配；reject 建议若要反向批准，必须填写 override reason |
| 6a. Day-0 原子放行 | 选择一个账号和一个已批准候选，先预览再确认启动 | 20 项 launch gates + activation plan + preview token + `START 1` | 20/20 通过；状态未变化；系统只建立一个批次，不发布商品 |
| 7. 上架准备 | 生成结构化草稿并指定卖家账号 | listing draft、publish approval | 标题、规格、价格、履约说明和自有/授权图片已核对 |
| 8. 审批执行 | 审批、入队、单次运行任务 | approval、execution evidence | 审批载荷账号与实际执行账号一致 |
| 9. 发布确认 | 检查商品页或处理人工认证 | `/browser/publish/confirm` | 获得平台商品 ID；认证/类目/成色/图片阻断已清除 |
| 10. 流量观察 | 同步商品指标；字段不可用时录入商品页凭证 | account + item + local_date traffic snapshot | 只有真实 `browseCount/wantCount` 或操作者凭证可入账；缺字段不记为 0 |
| 11. 单变量实验 | 提交一个变量的变更审批，执行或人工修改后重新同步并确认，再开始计时 | baseline + proposal + approval + execution + activation evidence + result snapshot + decision | 未确认平台值前保持 `proposed`；同商品只允许一个待确认或活动实验；没有结果快照不能结论 |
| 12. 询盘接入 | 导入真实买家消息并去重 | account + item scoped intake ledger | 同账号、同已发布商品、发布时间后的非重复 intake |
| 13. 询盘回复 | 生成回复草稿、人工审批、入队并确认发送成功 | reply approval + completed send_message job | 只接受与该 intake 审批精确关联的成功发送任务；生成失败或只建草稿不能通过 |
| 14. 成交履约 | 同步付款订单，核对四项证据并创建履约交接，审批后人工向供应商下单 | order runtime、fulfillment lock、purchase approval、purchase task、双边物流 | 同批次/账号/商品/闲鱼订单强关联，供应商单号、供应商物流和闲鱼物流齐全 |
| 15. 利润对账 | 平台签收后核对实收、采购、运费和平台实际费用 | completed ResaleOrder + financial note/ref/time | 平台订单已签收，金额匹配，利润由系统重算，账单凭证完整 |
| 16. 复盘决策 | 每日快照、7 天中期复盘、14 天最终复盘 | daily/weekly review | 根据浏览、想要、询盘、回复、成交、退款和实际利润决定继续或淘汰 |

## 3. 开跑门禁

调用 `GET /dashboard/first-run-readiness`，只有 `ready_for_first_run=true` 才进入真实发布。

门禁按同一个 `selected_account` 计算，不允许把 A 账号的 Prompt、B 账号的登录状态和 C 账号的同步结果拼成通过。必须同时满足：

1. 账号已配置且启用。
2. adapter 是真实 adapter；`stub` 只能用于模拟。
3. adapter 同时支持订单和商品同步。
4. 选中账号的 Prompt 已完整配置。
5. 选中账号有商品上下文。
6. 选中账号的订单和商品同步都处于 fresh 状态。
7. 选中账号运行连接可用。
8. 心跳、token 和 cookie 的认证证据有效，心跳不超过 300 秒。
9. 无登录、人工验证或未解决运行错误。
10. 没有无法归属账号的审批、执行任务或回复草稿。

同步失败不能更新 freshness。认证类失败会写入 `pending_verification_reason`，必须由操作者处理后重新同步。

### 3a. 历史任务审阅与隔离

首个真实账号接入前，先调用：

```text
GET /dashboard/account-queues/unassigned-work
```

系统把缺少 `cookie_id`、使用 `default` 或指向当前未配置账号的工作单统一视为未归属。返回结果只包含工作单 ID、动作、年龄、状态和建议，不返回买家、聊天、消息原文或 cookie 值。

处理规则：

1. 先保存本次 `preview_token`，再明确勾选 `work_order_ids`；空 ID 列表不能应用。
2. `claim` 只用于能证明实际属于目标卖家账号的任务，并且必须指定当前已配置账号。
3. 旧发布、发消息、退款、采购等平台动作默认建议 `quarantine`，不能因为系统只有一个新账号就自动认领。
4. `quarantine` 会拒绝尚未执行的审批、取消排队/失败执行任务、关闭待回复 intake，并保留审计事件；不会删除历史记录。
5. 已运行或已完成的执行任务不能认领或隔离，必须人工核查平台结果。
6. 应用时必须提交与所选数量一致的确认短语，例如 `CLAIM 2` 或 `QUARANTINE 2`。预览后数据有变化会返回 `stale_preview_token`，必须重新审阅。

控制台的「审阅历史任务」实现上述流程。未经操作者逐单选择，系统不会修改任何旧工作。

### 3b. 浏览器账号两阶段接入

历史任务审阅完成后，在控制台「账号画像」按以下顺序接入首个真实账号：

1. 点击「打开登录浏览器」。系统只启动项目专用 Edge Profile 和闲鱼首页，不创建账号记录。
2. 在闲鱼页面完成人工登录。验证码、扫码和平台风险提示都在浏览器内处理，控制台不收集手机号、密码或验证码。
3. 点击「检测当前账号」。系统读取当前浏览器上下文中的 Goofish/Taobao 会话，要求至少存在 `unb`、`cookie2`、`_m_h5_tk`，并调用当前平台登录探测。
4. 核对脱敏预览中的账号 ID、显示名、操作类型、会话摘要和有效期。预览不返回 Cookie 值，也不写数据库。
5. 在五分钟内点击「确认绑定」，输入页面显示的 `BIND ...`。服务端会重新读取同一浏览器，只有账号 ID 和会话指纹都未变化才建档或刷新。
6. 成功后刷新账号画像。新账号默认启用；刷新已有账号时保留原 enabled/auto-confirm 设置。BrowserAdapter 随后断开当前会话，下一次操作从账号数据库加载该账号凭据。

任何一项登录探测失败、必要 Cookie 缺失、预览过期、浏览器账号变化或确认语句不一致，都必须重新检测，且不得创建或刷新账号。手工 Cookie 表单只用于浏览器接入确实不可用时的人工恢复；不能用虚构账号 ID 代替实际 `unb`。

账号建档只是阶段 1 完成，不代表可经营。下一步依次初始化 Prompt、执行同账号订单/商品真实同步、清除 runtime blocker，再记录账号与类目的当日商业条款。

### 3c. 账号商业条款与费率证据

候选创建后、人工批准前，必须使用目标卖家账号和候选真实类目完成：

```text
GET  /accounts/{cookie_id}/commercial-terms?category={category}&candidate_id={candidate_id}
POST /accounts/{cookie_id}/commercial-terms
```

控制台从「Commercial Alpha 试运行」选择一个账号和一个候选后，点击「记录账号商业条款」。逐项读取执行当天平台页面，不能根据历史经验或其他账号代填。

快照只保存以下脱敏结论：经营身份、账号所有权/运营授权确认、当前卖家协议确认、支付服务、类目发布权限、退货规则、类目例外依据、退货地址是否配置、售后能力、保证金/买保要求与金额、比例费率、每单固定费用、核验时间、证据引用和备注。禁止填写完整退货地址、手机号、身份证号、Cookie、token 或支付凭据。

保存时系统会：

1. 按 `cookie_id + category` 写入独立快照，生成内容哈希版本。
2. 用 `平台费用 = 目标售价 * 比例费率 + 每单固定费用` 重算候选毛利和毛利率。
3. 将条款版本写回候选。另一个账号、另一个类目或旧版本不能匹配。
4. 如果重算后商品变成高风险、低于毛利下限或出现负利润，后续发布预检继续阻断。

商业条款子门禁：

1. 快照存在且账号、类目完全匹配。
2. 核验时间有效且不超过 24 小时。
3. 账号所有权或运营授权、经营性卖家身份和当前协议已确认。
4. 收款/支付服务和目标类目发布权限可用。
5. 七日无理由或有依据的依法类目例外、有效退货地址和售后能力均已确认。
6. 当前页面要求保证金/买保时，状态已满足且金额大于 0；页面不要求时记录不要求和 0 元。
7. 比例费率、固定费用、证据备注和证据引用完整。
8. 候选费用数值与条款版本一致。

任何子门禁失败，普通 listing-draft 和 Commercial Alpha 启动入口都会在创建 WorkOrder/Approval 前返回 409 或 blocked 预览。

### 3d. Day-0 原子启动

完成历史任务处理、账号接入和候选证据后，必须按下面两步启动首批次：

```text
POST /commercial-launch/preview
POST /commercial-launch/start
```

预览请求只提交一个 `cookie_id`、一个 `candidate_id` 和有界试运营参数。系统按固定顺序检查：

1. `historical_work_resolved`：未归属历史工作单和阻断队列均为 0。
2. `account_configured`：显式选择当前已配置的卖家账号。
3. `account_enabled`：该账号已启用。
4. `live_adapter`：当前不是 `stub/unknown`，且真实 adapter 健康。
5. `sync_capabilities`：adapter 同时具备订单和商品同步能力。
6. `prompt_ready`：同一账号的 Prompt 配置完整。
7. `sync_current`：同一账号的订单和商品同步都为 fresh。
8. `runtime_connected`：同一账号运行时已连接。
9. `runtime_fresh`：心跳、token 和 cookie 证据仍在有效期内。
10. `runtime_unblocked`：没有登录、验证码或运行错误阻断。
11. `candidate_selected`：明确选择一个候选。
12. `candidate_manual_approved`：候选状态为 `approved` 且人工决策为 `approve`。
13. `candidate_category_valid`：候选属于闲鱼国内项目（`sales_platform=xianyu`、`market_scope=domestic`）且类目非空。
14. `account_commercial_terms_ready`：当前账号与类目商业条款快照有效，买保/保证金已满足，候选已按同一费率版本重算。
15. `candidate_preflight_ready`：Phase 18 发布前全部证据门禁通过。
16. `launch_controls_valid`：预算、止损、观察周期等参数在允许范围内。
17. `procurement_cost_known`：供应价大于 0，供应商运费已记录且不小于 0。
18. `cash_budget_covers_unit`：采购预算至少覆盖一件商品的供应价加运费。
19. `launch_scope_one_item`：`max_candidates=1`、`target_candidates=1`、`max_published=1`。
20. `no_active_trial`：全局没有尚未结束的活动或暂停批次。

操作契约：

1. `preview` 是只读操作，不创建批次、草稿、工单、审批、任务、消息或订单。
2. 预览返回完整 `activation_plan`、`preview_token` 和固定确认短语 `START 1`。操作者按激活工单处理剩余项并核对 20/20 后，原样提交到 `start`。
3. `start` 在锁内重新计算全部证据。任何候选、报价、账号运行、同步、历史队列或活动批次变化都会返回 `stale_preview_token`，必须重新预览。
4. 同一个 token 因网络超时被重复提交时返回原批次；服务重启后也保持幂等，不会产生第二批次。
5. 直接调用旧的 `POST /commercial-trials` 会返回 `strict_commercial_launch_required`，不能绕过 Day-0 门禁。
6. 启动成功只创建一个审计批次，不会自动上架。下一步必须从批次卡生成草稿，再单独审批和执行发布。

## 4. 首个商品的证据包

`GET /resale/candidates/{id}/activation-packet` 是不依赖账号的只读评估；
`PATCH /resale/candidates/{id}/activation-packet` 保存当前商品事实并重新评估。控制台候选卡上的
「一品启动包」使用这两个接口。包内固定 12 项，顺序就是操作者的补证顺序：

1. `channel_scope`：只能是 `sales_platform=xianyu`、`market_scope=domestic`。
2. `product_identity`：真实非占位标题、闲鱼类目、供应商精确 SKU，至少两个有值规格。
3. `supplier_source`：`pdd` 或 `1688`、真实外部商品 URL、可追溯的供应商简称。
4. `supplier_verified`：明确核验结论和实质备注；只写“已确认”不通过。
5. `supplier_quote_evidence`：实付采购价、运费、MOQ、24 小时内核验时间和证据引用。
6. `supplier_stock_evidence`：`in_stock/limited`、正数可发量、24 小时内核验时间和证据引用。
7. `supplier_fulfillment_evidence`：正常无价格信息发货、最长 168 小时出库、退换政策、错货/破损责任、实质备注和证据引用。
8. `image_assets`：至少一张真实本地或外部图片引用；空值和示例域名不计入。
9. `image_rights`：`owned`、`supplier_authorized` 或 `licensed`，并记录结论和证据引用。
10. `market_baseline`：至少两个不同的当前可比闲鱼链接、展示价区间、72 小时内观察时间、需求信号和证据引用。展示价不能当成交价。
11. `unit_economics`：售价、毛利额和毛利率为有限数值并达到配置下限；最终以目标账号和类目的当前条款重算。
12. `manual_decision_current`：人工 `approve` 对应的 SHA-256 证据指纹必须与当前修订完全一致。

生产门禁不接受手填的本地路径、内部编号、页面 URL 或泛化的“见截图”作为上述五类证据引用。操作者必须在控制台「候选证据存档」上传真实文件；系统生成 `artifact:cea_...` 规范引用，并校验候选归属、证据类型、活动状态、观察时间和 SHA-256 完整性。上传前必须移除 Cookie、token、手机号、身份证号、地址和支付凭据。完整采集和故障处理见 `docs/ops/xianyu/candidate-evidence-archive.md`。

修订规则：

- 任何商品、供应商、报价、库存、履约、图片、竞品、成本或商业条款事实变化都会增加 `evidence_revision`，重新计算经济性和评分，并清除旧人工决策。
- 保存完全相同的事实不会增加修订，也不会清除当前批准。
- 账号+类目商业条款保存后会重算平台费用；若原来已批准，旧批准必须失效，重新评审和决策。
- 已经生成 listing draft 的候选不能原地改写物料事实；创建新候选修订，避免旧草稿引用新事实。
- `evidence_ready=true` 只表示前 11 项完成；最终批准后必须再次读取并得到 `ready=true`、`12/12`。

候选操作顺序（HTTP）：

```text
POST /resale/candidates
PATCH /resale/candidates/{id}/activation-packet              # 补商品、SKU、供应商和核算事实
POST /resale/candidates/{id}/evidence-artifacts               # 五类文件逐个上传并自动绑定
GET  /resale/candidates/{id}/evidence-artifacts               # 确认 active + integrity ok
PATCH /resale/candidates/{id}/activation-packet              # 补齐其余事实到 evidence_ready
POST /accounts/{selected_account}/commercial-terms             # 传 candidate_id 并重算利润
GET  /resale/candidates/{id}/activation-packet                # 确认只剩 manual decision
POST /resale/candidates/{id}/agent-review                     # 评审重算后的当前修订
POST /resale/candidates/{id}/decision                         # 操作者 approve 当前指纹
GET  /resale/candidates/{id}/activation-packet                # 必须 ready=true, 12/12
GET  /resale/candidates/{id}/publish-preflight?cookie_id={selected_account}
POST /resale/candidates/{id}/listing-draft?cookie_id={selected_account}
```

多账号环境中缺少 `cookie_id` 会返回 409；不存在或停用的账号不会创建发布审批。

候选操作顺序（控制台）：

1. 在「倒卖机会验证」只录入一个具体候选；不要用 `T/test`、示例 URL 或不确定 SKU 代替事实。
2. 候选卡片点「一品启动包」，从顶部第一项开始填写。页面显示 `通过数/12`、证据修订和当前第一阻塞。
3. 先完成商品/SKU和供应商事实，再在「候选证据存档」上传报价、库存、履约、图片权利和市场基线五类文件。确认每类只有一个 `active` 且完整性通过；引用框由系统生成，不可手填。
4. 补齐报价/库存数值、履约结论、图片权利和两个竞品链接。未来时间、过期证据、重复竞品链接、无效规格 JSON、证据时间不一致或哈希损坏都会阻断。
5. 绑定真实账号后记录该账号+类目的当前商业条款，让系统重算平台费和毛利；此时旧批准失效是正确行为。
6. 对重算后的当前修订跑 Agent 评审，再点「人工决策」批准。重新打开启动包，确认 `12/12`。
7. 点「发布前检查」选择同一个账号；全部门禁通过后才能生成上架草稿。409 响应会显示失败门禁且不创建草稿、工单或审批。

## 4a. 发布前检查门禁

`GET /resale/candidates/{id}/publish-preflight?cookie_id={selected_account}` 只读返回每个门禁的
`pass/fail`、证据摘要和下一步。只有 `ready=true` 时，`POST /resale/candidates/{id}/listing-draft`
才会创建草稿和审批单；任一门禁失败时返回 HTTP 409，且不会产生草稿/工单/审批副作用。

门禁按业务顺序逐项检查：

1. `account_configured`：显式指定、已配置且启用的卖家账号。
2. `account_enabled` / `prompt_ready` / `sync_current` / `runtime_connected` /
   `runtime_fresh` / `runtime_unblocked`：同一账号的账号就绪证据（不要求库存上下文）。
3. `commercial_terms_ready`：账号+类目快照有效，买保/保证金和支付条件已满足，候选利润与当前费用版本一致。
4. `candidate_channel_valid`：候选必须属于闲鱼国内项目；跨境或其他销售平台候选不能进入闲鱼发布链路。
5. `candidate_not_blocked`：候选未被阻断且不是高风险。
6. `candidate_activation_ready`：12 项一品启动包通过，人工批准指纹与当前修订一致。
7. `supplier_verified`：供应商已验证且验证备注非空。
8. `supplier_quote_fresh` / `stock_checked_fresh`：报价与库存核验时间可解析、不是未来时间且在 24 小时内（24 小时整仍通过，超过 1 秒即失败）。
9. `min_profit_amount` / `min_profit_rate`：毛利额和毛利率达到配置下限（NaN/Infinity 视为不可用，直接失败）。
10. `fulfillment_verified`：履约已验证且备注非空。
11. `candidate_images`：至少一张非空图片 URL（空白字符串不计入）。
12. `image_rights_type` / `image_rights_evidence`：图片授权类型有效且授权证据非空。
13. `structured_fields`：标题、类目非空，规格表至少有一组非空键值（数字/布尔值算有效）。

第 7-13 项保留为对旧接口和旧记录的重复防线，不能用它们绕过更严格的 `candidate_activation_ready`。

清除门禁的顺序建议：先账号就绪（启用、Prompt、同步、运行连接），确认候选属于闲鱼国内项目，再补候选证据（供应商、
报价/库存时效、履约、图片授权、结构化字段），最后用 `GET .../publish-preflight` 复核后再上架。

## 5. 审批到执行

```text
POST /approvals/{approval_id}/decision
POST /approvals/{approval_id}/execute
POST /execution-jobs/run-next
GET  /browser/publish/confirm?cookie_id={selected_account}
POST /adapter/sync-persist?cookie_id={selected_account}
```

每一步完成后检查：

- Approval 的 `payload.cookie_id` 与 `draft_snapshot.cookie_id` 相同。
- Execution result 的 `cookie_id` 与批准账号相同。
- 发布成功必须返回平台 `external_id`，仅按钮点击成功不算发布成功。
- 页面仍停留在发布页时必须检查 blocker，不能把它记为成功。
- 队列失败先查看 evidence；禁止重新创建同一审批绕过幂等规则。

## 6. 询盘与履约 SLA

付款订单的系统操作顺序：

```text
POST /adapter/sync-persist?cookie_id={selected_account}
GET  /commercial-trials/{trial_id}
POST /commercial-trials/{trial_id}/orders/{order_id}/fulfillment-handoff
POST /approvals/{purchase_approval_id}/decision
POST /approvals/{purchase_approval_id}/execute
POST /execution-jobs/run-next
POST /resale/purchase-tasks/{purchase_task_id}/mark-ordered
POST /resale/purchase-tasks/{purchase_task_id}/create-order
POST /adapter/sync-persist?cookie_id={selected_account}
POST /resale/orders/{resale_order_id}/status              # shipped + supplier/xianyu tracking
POST /commercial-trials/{trial_id}/orders/{order_id}/fulfillment-release
POST /adapter/sync-persist?cookie_id={selected_account}    # wait for delivered evidence
POST /resale/orders/{resale_order_id}/status              # completed + actual finance evidence
```

`fulfillment-handoff` 必须同时确认规格、收货地、供应商库存和报价。它会获取幂等履约锁并创建采购审批，但不会自动付款或下单。重复请求返回同一个交接；订单不属于该账号/商品、未付款、已进入对账、被其他操作者锁定或预计采购敞口超预算时，会在产生副作用前返回 409。

商业订单状态门禁：

1. `purchased -> shipped`：必须保留 `commercial_trial_id`、`cookie_id`、`item_id`、`candidate_id`、`xianyu_order_id`、采购任务/草稿和 `commercial_trial:{trial_id}` 履约锁关联，并填写供应商订单号、供应商物流号和闲鱼物流号。
2. `shipped -> completed`：平台 OrderRuntime 必须已出现 `delivered`；不得用本地手改状态代替平台签收证据。
3. 完成时重新填写实际实收、供应商实际付款、实际运费和平台实际费用，同时填写至少 8 个字符的核对备注及账单截图/文件引用。`actual_profit` 和毛利率由系统重算。
4. 缺少任一强关联或凭证时接口返回 `commercial_order_transition_blocked`，控制台订单卡和批次卡会列出具体缺口。
5. 未完整对账的正利润不计入已实现利润；任何已关联完成订单的负利润会先进入保守止损口径，不能因缺凭证而逃避亏损止损。

| 事项 | Alpha 目标 | 超时后的动作 |
|---|---|---|
| 新询盘进入系统 | 5 分钟内 | 检查 message intake health 和账号 runtime |
| 回复草稿复核 | 工作时段 10 分钟内 | 保留人工回复，不自动跳过审批 |
| 已付款订单核对 | 15 分钟内 | 核对规格、地址、库存和利润 |
| 供应商下单 | 核对完成后 30 分钟内 | 若库存或价格变化，暂停履约并重算利润 |
| 物流单号回填 | 供应商出单后 30 分钟内 | 标记订单异常并联系供应商 |
| 破损/错货首次响应 | 30 分钟内 | 创建售后记录，保存图片和责任证据 |

## 6a. 发布后日观察与实验

每次商品同步后，系统只在适配器返回并成功解析浏览与想要字段时生成 `adapter` 快照。字段缺失、格式错误或商品不属于当前批次的真实发布证据时不生成快照。由于闲鱼内部接口不是公开稳定合同，适配器采集失败时由操作者从商品页读取累计浏览和想要数，并附凭证备注：

```text
POST /adapter/sync-persist?cookie_id={selected_account}
GET  /commercial-trials/{trial_id}
POST /commercial-trials/{trial_id}/items/{item_id}/traffic-observations
POST /commercial-trials/{trial_id}/items/{item_id}/experiments
POST /approvals/{edit_approval_id}/decision
POST /approvals/{edit_approval_id}/execute
POST /execution-jobs/run-next
POST /adapter/sync-persist?cookie_id={selected_account}
POST /commercial-trials/{trial_id}/experiments/{experiment_id}/activate
POST /commercial-trials/{trial_id}/items/{item_id}/traffic-observations
POST /commercial-trials/{trial_id}/experiments/{experiment_id}/decision
```

观察账本规则：

1. 绑定 `trial_id + cookie_id + item_id + candidate_id + local_date`，不能用竞品、其他账号或历史商品的数据推进当前批次。
2. `views` 和 `wants` 是平台累计数，必须满足 `0 <= wants <= views`，同一商品累计数不能倒退。
3. 普通巡检同一来源同一天更新当日快照；实验启动后的结果快照另存，不能覆盖基线。
4. 操作者录入必须写 `evidence_note`，可在 `evidence_ref` 填本地截图文件名或同步批次引用。
5. 系统从消息 intake 和订单 runtime 计算咨询、付款，不允许操作者手填这两项。

瓶颈判断顺序：

| 证据状态 | 经营判断 | 下一动作 |
|---|---|---|
| 无快照 | 数据缺失 | 先记录基线，不改商品 |
| 观察窗口未满 | 样本时间不足 | 保持商品不变，等待下一快照 |
| 浏览低于批次门槛且无咨询 | 发现面不足 | 标题、首图、所在地三选一测试 |
| 浏览达到门槛但无咨询 | 商品页转化弱 | 价格、首图、详情三选一测试 |
| 有咨询无付款 | 成交阻力 | 核对规格、交付、信任、到手价和回复记录 |
| 有付款 | 已进入交易 | 停止流量优化，优先履约和利润对账 |

实验规则：

- 变量只能是 `title`、`cover`、`price`、`description`、`location`、`shipping_promise` 之一。
- 提交实验必须记录假设、原值、改后值、计划天数和当前基线；该请求只生成 `proposed` 提案和 `edit_listing` 审批，不开始计时。
- `title` 和 `price` 可在审批、入队和单次执行后由 adapter 提交，但必须再次同步商品；同步时间必须晚于提案，且平台当前值与目标值一致，才能调用 `activate`。
- `cover`、`description`、`location` 和 `shipping_promise` 由操作者按已批准提案人工修改。`activate` 必须提交人工确认、至少 8 字符的核对备注和截图/同步批次引用。
- 自动编辑失败后可以人工完成，但仍需重新同步并匹配目标值；人工完成会覆盖失败结果并写入 `execution_manually_confirmed` 审计事件。
- 一个商品同时只能有一个待确认或活动实验；实验计时从 `activate` 成功时开始，不从提案或审批时间开始；实验期间其他字段保持不变。
- 提案尚未执行时可取消，系统同时拒绝审批并撤销排队任务；编辑已经成功提交时禁止直接取消，必须先同步确认并激活，之后用实验结论决定保留或回退。
- 到期后必须有晚于实验开始的结果快照，系统才允许记录 `keep_change`、`revert` 或 `stop_listing`。
- 提前结束必须显式勾选并写明理由；实验结论只记录经营决策，不直接修改平台商品。

## 7. 14 天试运营节奏

- Day 0：完成历史任务逐单审阅/隔离；接入一个账号、adapter、Prompt 和双向同步；核验一个闲鱼国内候选并只批准 1 个商品；记录该账号+类目的当前商业条款；确认 Day-0 门禁 20/20 后启动一个批次。启动不发布。
- Day 1：从活动批次生成这个候选的结构化草稿，核对后创建并批准一个账号明确的发布审批。
- Day 2：执行一次真实发布，确认商品 ID，完成首次指标同步或人工基线快照和日报。
- Day 3-6：达到观察间隔后再判断瓶颈；只优化标题、首图、价格或规格表达中的一个变量，不同时修改全部变量。
- Day 7：中期复盘。低浏览检查标题/首图/所在地；有浏览无咨询检查价格、首图和详情；有咨询无成交检查规格、信任和履约承诺。
- Day 8-13：表现合格才增加第二、第三个商品；不新增账号。
- Day 14：按实际利润、询盘率、成交率、退款率和操作耗时决定继续、调整或淘汰。

## 8. 停止条件

出现任一情况立即停止该账号或商品的真实执行：

- 登录、认证或账号状态异常。
- 同步失败但 dashboard 仍显示旧数据。
- 供应商价格、库存或发货承诺无法再次确认。
- 实际毛利低于最低毛利额或最低毛利率。
- 图片、品牌、规格或商品描述的授权与真实性无法确认。
- 同一审批出现重复执行迹象。
- 发货账号、商品账号或买家会话账号不一致。
- 售后率、取消率或亏损订单超过试运营设定上限。

## 9. Alpha 完成标准

Commercial Alpha 只有在以下证据全部存在时才算完成：

- 至少一个账号通过完整 first-run readiness。
- 至少一个候选的一品启动包在当前批准指纹下达到 12/12，并形成结构化草稿。
- 至少一次发布从审批、队列、执行到平台商品 ID 全链可追溯。
- 至少一次真实询盘形成 intake、草稿、审批和回复记录。
- 至少一笔订单形成成本、采购、物流、收入和实际利润记录；若 14 天无订单，则保留完整的无成交实验数据。
- 14 天日报和最终复盘能够解释下一轮只改变什么、停止什么、投入多少。

达到 Alpha 后才进入多账号、多品类、自动调价、成本预算和公司级目标管理。否则继续补当前闭环，不扩张功能面。

## 10. 实证来源与采用边界

- [闲鱼社区卖家服务协议](https://terms.alicdn.com/legal-agreement/terms/platform_service/20220929192826912/20220929192826912.html)：采用经营性卖家身份、账号责任、信息真实性和商品权利要求作为首批次账号与商品门禁。
- [闲鱼社区买家保障服务协议](https://terms.alicdn.com/legal-agreement/terms/platform_service/20220929192101114/20220929192101114.html)：采用依法退货、有效退货地址、售后义务和账号/类目保证金差异作为商业条款子门禁。
- [闲鱼软件许可使用协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230509172204596/20230509172204596.html)：采用“实际页面费率优先”和不得通过站外交易、错误类目规避费用；不推断统一长期费率。
- [harringtonwalker/xianyu-automation](https://github.com/harringtonwalker/xianyu-automation)：采用来源类型/外部 SKU、事实与权利确认、24 小时页面复核、旧修订 supersede、审批与严格回执分离的设计思路；未复制其发布实现，也不把其未完成的实盘路径当作本项目证明。
- [闲鱼支付服务说明函](https://terms.alicdn.com/legal-agreement/terms/b_end_product_protocol/20250909184130672/20250909184130672.html)：采用支付和收款服务就绪门禁。
- [闲鱼社区隐私政策](https://terms.goofish.com/legal-agreement/terms/suit_bu1_taobao/suit_bu1_taobao202103021554_43790.html)：当前版本标明 2026-07-17 生效；账号认证和设备/网络风险信息属于平台实际运行约束，不能把一次登录成功当作长期可用。
- [dengyie/xianyu-auto-bot](https://github.com/dengyie/xianyu-auto-bot)：采用其多账号调度、消息/订单事件、验证码人工收口、健康检查和单进程状态约束作为工程参考，不把 README 中的功能声明当作本项目经营证据。
- [laozuzhen/xianyu-openclaw-channel](https://github.com/laozuzhen/xianyu-openclaw-channel)：参考 OpenClaw 通道桥接与发布人工验证处理，平台操作仍由本项目审批和执行队列统一收口。
- [cv-cat/XianYuApis](https://github.com/cv-cat/XianYuApis)：仅用于理解非官方接口字段和适配方式；内部接口随时可能变化，因此必须保留操作者凭证兜底，不能作为稳定 SLA。

软件服务费、类目要求、保证金和发布页面提示以执行当天卖家账号在闲鱼 App/网页端实际展示为准。候选核算必须使用当日可见费率，不在代码中硬编码一个长期固定的平台费率。

本阶段的逐项来源审计与采用/拒绝边界见
`docs/research/candidate-activation-reference-2026-08-25.md`。
