# Codex 独立检验交接

日期：2026-10-12。项目根目录：`E:\闲鱼\xianyu`。检验者：Codex（含两个只读检验子代理）。修复执行者：GLM。

**唯一入口是本文件。当前结论：不通过，先修复已复现的业务不变量。** GLM 原交接保存在 `research/codex-verification-2026-10-12/handoff-glm-original.md`，历史报告保留；本轮没有修业务实现。

## 0. 实际版本与检验结果

- 实际 HEAD：`a3c7b19`，原交接误写为 `a3c2b19`。工作区包含未提交实现，不能只按 HEAD 描述版本。
- 检验前捕获 281 份受监控源码/配置与 20 份 live 文件 SHA-256；最终比较、证据摘要与范围见 `research/codex-verification-2026-10-12/verification-manifest.json`。
- 原始命令环境全量实跑：1592 项，7 errors，754.501 秒，exit 1。Windows 子进程输出实际编码与父进程指定 UTF-8 不一致。
- 明确设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 后，相关审计 5 项与备份/导出 3 项全部通过。**未将专项结果拼成第二次全量 PASS。**
- 独立检验共 53 项：核心 6、雷达 16、财务主链 17、存储 14；共 51 个期望不变量失败、2 个正向守卫通过，正式证据均无检验框架 errors。失败断言数不等于独立缺陷数量。
- live 只读审计 exit 0，但当前业务库报价/商机/采购/库存/账本未建立；任务 8、决策 4、queued 任务 1。空库审计不证明资金链或迁移正确。
- 三个前端 JS 语法检查通过。本轮未重新调用真实匿名 MCP，真实模型与平台写入为 0。
- 原先“可无外部输入的工程全部完成，剩余全是外部缺口”的表述不成立。取数接线、事实源收口、预算/会计/恢复、生产 repository 与迁移仍有内部开发工作。

## 1. 可对抗检验的不变量

| 不变量 | 攻击与判据 |
|---|---|
| live 零污染 | 前后文件集合与实际 SHA-256 一致；同大小/mtime 不足以证明一致 |
| 资料当前有效 | 确认后只撤销批次，或更改快照内容/版本，任何下游用途都必须失效 |
| 全路径出站限制 | 不只检查正常 quote/pricing 收集器；job-token GET、筛选理由、派生金额、引用缺失同样受用途限制 |
| 一笔真实事实一笔金额 | 未付款/错账号/错 SKU 不生成采购；重复回款不加收入；负数冲正不能抬高利润 |
| 新输入用于新评估 | 同 SKU 160→180 后必须引用 180 的快照；同输入与并发重放不重复建任务 |
| 配额限制实际尝试 | limit=1 只允许一次原子准入；429 重试占次数且遵守冷却；重启不清零 |
| 价格条件不被覆盖 | 数字 full_price 与“定金/券后”矛盾时，不生成无条件已知全价 |
| 规格和时间不被洗掉 | 数量/成色/材质变更应保留；未来上游时间不能替换为当前时间而获得 current/eligible |
| stop 与 claim 同边界 | 后台 tick 已进入、claim 尚未发生时 stop，释放屏障也不能新认领 |
| 恢复已接实际入口 | 主服务重启能处置孤儿预留；只有 detached 恢复函数和 store 重建不算接入 |
| 导出备份不破坏输入 | out 指向权威文件、work 位于 source 内部必须写前拒绝；全部声明权威域受核对与 hash 覆盖 |
| 合成数据不升级商业事实 | fixture/demo、公开展示卡、非空证据字符串、SQL dry-run 不替代供应关系、付款、库存或授权 |

“没有上游时间”不被一律判错：真实新观察可以明确采用本次观察时间；**未来时间矛盾与缓存重放**不能用同一方式取得资格。雷达检验保留相应正向守卫。

## 2. 单入口证据目录

下列路径以项目根目录为准。每次标准运行新建 `run-<UUID>`，保留日志、退出码及前后摘要。

| 证据 | 路径 |
|---|---|
| 标准离线运行器 | research/codex-verification-2026-10-12/run_verification.ps1 |
| C 核心脚本 / 6 红证据 | research/codex-verification-2026-10-12/adversarial_repros.py / verified-core-initial.log |
| D 雷达脚本 / 14 红 2 绿证据 | research/codex-verification-2026-10-12/radar_repros.py / verified-radar-initial.log |
| E 财务脚本 / 17 红证据 | research/codex-verification-2026-10-12/finance_repros.py / verified-finance-initial.log |
| S 存储脚本 / 14 红证据 | research/codex-verification-2026-10-12/storage_repros.py / storage-repros.log |
| 原始整库复验 | research/codex-verification-2026-10-12/full-regression.log |
| UTF-8 审计 5/5 | research/codex-verification-2026-10-12/run-fe847c7de3e347819d4f62f5f7b3ecc2/ |
| UTF-8 演练 3/3 | research/codex-verification-2026-10-12/run-6b1fc6594c45419897b1d6822579e4f7/ |
| live 只读审计 | research/codex-verification-2026-10-12/live-audit.log |
| 版本、数据与证据清单 | research/codex-verification-2026-10-12/verification-manifest.json |
| R 层显式复现器 | research/codex-verification-2026-10-12/mcp_read_repro.py |

`adversarial-repros.log` 是早期脚本调试记录，含两个错误的测试调用及临时 DB 清理异常；已修正检验脚本并保留 v2。**该初稿不计入业务失败统计。** 本轮只改交接文档/检验证据，未修生产实现。

历史报告别名用于第 5 节映射，均在 `docs/acceptance/`：

| 别名 | 文件 |
|---|---|
| R0 | GLM_G0_BASELINE_2026-10-11.md |
| R1 | GLM_G1A_INTAKE_CONFIRMATION_2026-10-11.md |
| R2 | GLM_G1B_COST_ELIGIBILITY_2026-10-11.md |
| R3 | GLM_G1C_INTAKE_PANEL_2026-10-11.md |
| R4 | GLM_G2A_RADAR_DISPATCH_2026-10-11.md |
| R5 | GLM_G2B_MARKET_CHANNEL_2026-10-12.md |
| R6 | GLM_G2C_DECISION_CARD_2026-10-12.md |
| R7 | GLM_G3A_OPPORTUNITY_ADOPTION_2026-10-12.md |
| R8 | GLM_G4A_MAIN_CHAIN_WIRING_2026-10-12.md |
| R9 | GLM_G4B_GOLDEN_FINANCE_2026-10-12.md |
| R10 | GLM_G5A_ADR_AND_AUDIT_2026-10-12.md |
| R11 | GLM_INTEGRATION_UI_2026-10-12.md |
| R12 | GLM_R5_GATE_REMINDERS_DRILLS_2026-10-12.md |
| R13 | GLM_R6_PROBE_CLIENT_AND_GAPS_2026-10-12.md |
| R14 | GLM_R7_REAL_SOURCE_DEMO_2026-10-12.md |
| R15 | GLM_R8_XIANYU_MCP_REAL_CHANNEL_2026-10-12.md |

其他依据：`docs/adr/0002-g5a-transactional-storage-and-migration.md`（AD2）、`docs/adr/0003-xianyu-mcp-read-channel.md`（AD3）、`docs/ops/glm-gap-checklist-2026-10-12.md`（GAP）、原 85 项 `docs/strategy/GLM_ACCEPTANCE_MATRIX_2026-10-11.md`。不降低原矩阵要求来获得 PASS。

## 3. 全部可运行复现命令

### 3.1 环境与退出码

使用 PowerShell；原交接 Bash 的 mktemp/for-do 不适用。

~~~powershell
Set-Location -LiteralPath 'E:\闲鱼\xianyu'
git status --short
git rev-parse HEAD
.\.venv\Scripts\python.exe --version
node --version
~~~

标准入口自动设置临时 DATA_DIR、stub、UTF-8，关闭后台雷达/观察/自动回复/真实探针，禁用主模型配置。生成 output.log、result.json、state-before/after.json。

旧回归应 exit 0；对抗脚本当前期望 assertion failures、exit 1，修复后才应转绿。ERROR、缺 Chromium、找不到脚本不能算业务红灯。运行器发现源码/live 变化时 exit 2 作废运行，保留 command_exit_code。

### 3.2 完整回归与 15 专项

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode full
~~~

可以单跑相应 suite；下列循环提供全部专项命令，避免重复整库：

~~~powershell
$suites = @(
 'test_intake_confirmation',
 'test_g1b_cost_eligibility',
 'test_intake_confirmation_ui',
 'test_radar_dispatch',
 'test_market_channel',
 'test_market_probe_client',
 'test_g2c_decision_card',
 'test_opportunity_adoption',
 'test_g4a_main_chain_wiring',
 'test_g4b_golden_finance',
 'test_pi_outbound_gate',
 'test_g5a_drills',
 'test_commerce_audit',
 'test_source_quotas',
 'test_commerce_integration_ui'
)
foreach ($suite in $suites) {
 powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode suite -Suite $suite
 if ($LASTEXITCODE -ne 0) { throw "Failed suite: $suite" }
}
node --check web/app.js
node --check web/intake-confirmation.js
node --check web/pi-usage.js
git diff --check
~~~

H 层使用实际路由。U 层：intake_confirmation_ui 使用真实资产+夹具后端；commerce_integration_ui 使用真实 server 子进程。Chromium 不可用的 skip 不能算 U PASS。G1C 双视口证据不覆盖后加整合页面的移动端。

### 3.3 独立对抗检验

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe adversarial_repros
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe radar_repros
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe finance_repros
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe storage_repros
~~~

第 4 节每个 Case 都可替换 -Case 值：C/D/S 用完整 test_ 方法名，E 用不含 test_ 的别名。

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe adversarial_repros -Case test_batch_revocation_invalidates_active_confirmation
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe radar_repros -Case test_a46_concurrent_quota_admission_is_atomic
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe finance_repros -Case ledger_negative_reversal
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode adversarial -Probe storage_repros -Case test_backup_nested_destination
~~~

不要把固定初始证据日志改成修复后结果；新结果引用 run-UUID。财务/存储脚本会刷新其工作日志，初次验证副本/manifest 保留用于修前修后对照。

### 3.4 只读审计与备份/导出工具复现

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File research\codex-verification-2026-10-12\run_verification.ps1 -Mode audit
~~~

下面只演示工具当前行为，不代表生产迁移通过。输出始终位于检验目录、source 之外；危险路径攻击只用 S 脚本临时夹具。

~~~powershell
$reviewRoot = Join-Path (Get-Location) 'research\codex-verification-2026-10-12'
$backupWork = Join-Path $reviewRoot ('backup-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backupWork | Out-Null
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe scripts\commerce_backup_drill.py --source data\live --work $backupWork
$sqlPath = Join-Path $reviewRoot ('export-' + [guid]::NewGuid().ToString('N') + '.sql')
.\.venv\Scripts\python.exe scripts\export_commerce_to_sql.py --data-dir data\live --out $sqlPath
~~~

导出存在 TEXT 值/约束/币种/权威范围缺陷，不能将其 exit 0/footer 当可装载或切换证明。PG repository、装载、事务/outbox、真实竞争 worker 仍待实现/验证，目前没有可诚实列为已经完成的 A111/A112 实装命令。

### 3.5 R 层真实匿名 MCP

本轮没有运行该段。需要已有 clone、Node/Chromium 和网络，会产生实际匿名读取；不混入离线计数。新复现器只允许显式 opt-in，生成独立 mcp-read-UUID/result.json，失败返回非零，不覆盖 GLM 历史结果。

~~~powershell
git -C 'E:\闲鱼\xianyu-mcp-tmp' rev-parse HEAD
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe research\codex-verification-2026-10-12\mcp_read_repro.py --run-real-read --mcp-dir 'E:\闲鱼\xianyu-mcp-tmp' --tool all --query '不锈钢工作台' --retry-once
~~~

调用 capabilities（必须实际 logged_out）、search_count、browse_feed、search_items，保存每次响应；遵守明确 retry_after，超过 60s 留待冷却而不提前重试。RGV587 可能发生，等待不保证成功。当前 clone 审阅 revision 为 d6bd8d3，下次复验重新记录。

原 mcp_probe.py 失败 exit 0 已由 C6 复现，且 main 会覆盖固定历史文件，因此不作为新复现入口。当前旧 mcp_probe_result 中 search 为 invalid_arguments，不能把所有失败均称 RGV587。

search_count 是关键词匹配数量估计，不是卖家可售库存/成交量/可返回卡片数。匿名脚本成功不证明应用 fetch 接通：当前 BrowserMarketProbeClient 没有 fetch_page，/commerce/market-channel/fetch 仍拒绝。AD3 研究脚本与主应用 Adapter 是不同验收范围。

### 3.6 进程与图像证据

U 测试打印该次截图目录，须保留实际截图与日志；不得用旧截图冒充本轮。只关闭自己启动的 server/Node/Chromium，记录 PID/端口。无新增系统自启动。真实读取、真实写入、真实模型分别计数。

## 4. 红灯目录与全部单项名称

### C 核心（6 红，Probe=adversarial_repros）

| Case | 级别 / ID | 实际观察与落点 |
|---|---|---|
| test_batch_revocation_invalidates_active_confirmation | P1 A17/A25/A45 | 确认后只撤销批次仍 eligible=true/reasons=[]；intake_confirmation.py:608 有效记录分支忽略全局 reasons |
| test_snapshot_change_after_preview_invalidates_binding | P1 A12/A17 | 隔离改价 98765.43、revision+1，旧预览仍确认且有效；digest 未绑定快照内容/版本 |
| test_job_token_cannot_read_unapproved_quote_amount | P1 A15/A45 | 未获 external_model 许可，job token GET offers 仍 200/已知价150；server.py:205/6822 |
| test_missing_supplier_ref_does_not_disable_pricing_gate | P1 A45 | 供应引用丢失，material_withheld=false，利润120/现金300仍进入输入；pi_jobs.py:471 |
| test_screening_reason_does_not_export_unapproved_profit | P1 A45/A47 | 未获许可，筛选理由含 conservative profit 120.00 CNY；pi_jobs.py:513 |
| test_mcp_probe_reports_failure_with_nonzero_exit | P2 A50/A53 | fake initialize 抛错，旧 main 仍 return 0；mcp_probe.py:140；零真实 MCP |

### D 雷达（14 红、2 正向绿，Probe=radar_repros）

| Case | 级别 / ID | 实际观察与落点 |
|---|---|---|
| test_a41_new_quote_uses_new_supplier_snapshot | P1 A41/A25 | 160→180 新事件 processed，机会/核算仍引用160；radar_dispatch.py:287、opportunity_service.py:88 |
| test_a41_implicit_supplier_selection_uses_latest_version | P2 A41/A47 | latest 取首条且缺供应来源过滤；radar_dispatch.py:477 |
| test_a41_concurrent_job_submission_is_idempotent | P2 A41 | 同键并发查空后各追加，产生两个任务；agent_jobs.py:99 |
| test_a42_radar_claim_excludes_write_required_jobs | P2 A42 | radar role + write_required=true 仍被分析 worker 认领；agent_jobs.py:158 |
| test_a43_stop_boundary_prevents_background_claim | P2 A43/A124 | 后台 tick 入内后 stop，释放屏障仍新 claim；radar_dispatch.py:335 |
| test_a44_completion_persistence_failure_is_visible_and_recoverable | P2 A44 | 完成写盘失败耗尽尝试后 job timed_out，event 永久 pending/无原因；radar_dispatch.py:247/428/455 |
| test_a46_concurrent_quota_admission_is_atomic | P1 A46 | limit1 两并发皆获准，attempts2/remaining-1；source_quotas.py:101 |
| test_a46_fetch_retries_do_not_bypass_quota | P1 A46/A53 | 429→成功的实际尝试未接配额，未尊重冷却；market_channel.py:448 |
| test_a50_non_mapping_probe_returns_structured_failure | P2 A50/A55 | list 响应逃出为 AttributeError；market_channel.py:126 |
| test_a51_conditional_price_text_is_not_full_price | P1 A51 | 数字 full_price 与定金/券后矛盾仍 known/full；market_channel.py:167 |
| test_a51_a52_quantity_change_preserves_new_payload | P2 A51/A52 | 数量1→10摘要不变，旧数量保留；market_channel.py:245 |
| test_a53_existing_snapshot_is_deduped_and_resumable | P2 A53 | 已存快照两页重复返回，duplicates0/seen_ids空；market_channel.py:416 |
| test_a53_malformed_page_is_not_successful_empty | P2 A53 | page={} 被当成功空页 complete；market_channel.py:393 |
| test_a54_future_upstream_time_is_not_silently_current | P1 A54 | 未来时间被 fetch 洗为 current/eligible；market_channel.py:218 |

绿灯守卫：test_a46_quota_persistence_failure_rolls_back、test_a54_missing_upstream_time_can_be_current。修复不应损坏这两条合法路径。

### E 财务主链（17 红，Probe=finance_repros）

| Case（不带test_） | 级别 / ID | 实际观察与落点 |
|---|---|---|
| http_paid_body_not_authority | P1 A91 | paid:false仍生成采购并锁库存；server.py:7309 |
| http_missing_order_payment_gate | P1 A91 | 不存在的销售单也可采购 |
| http_order_account_owner | P1 A91/A92 | 未核验销售单实际账号 |
| http_order_sku_owner | P1 A91/A98 | 未核验销售单实际SKU |
| http_cross_account_order_identity | P2 A92 | 不同账号同ref被全局409；procurement.py:96；原测试把拒第二账号误作隔离 |
| http_legacy_purchase_authority | P1 A90/A92 | 新采购和旧可写task各存不同供应商单；server.py:5942/resale.py:1458 |
| http_procurement_budget_reservation | P1 A92/A94 | 采购到ordered，预算预留/committed仍0；procurement.py:149 |
| http_unknown_freight_gate | P1 A93 | 运费未知仍放行 |
| http_moq_gate | P1 A93 | MOQ5买1仍放行 |
| http_supplier_stock_sku_binding | P1 A93 | SKU-X快照给SKU-OTHER登记库存成功；server.py:7384 |
| http_recovery_orphan_lock | P1 A90/A94 | 主服务重建后孤儿proc锁仍占，detached函数才发现；server.py:318 |
| http_receipt_accounting_projection | P1 A90/A96/A100 | HTTP收货含实额到received，ledger仍空；原测试直接调用service入账 |
| http_receipt_replay | P2 A96/A99 | 成功相同回执第二次409，不区分重放和异载；procurement.py:187 |
| http_missing_procurement_404 | P2 A90 | 缺记录500，后置None检查不可达；server.py:7299 |
| ledger_payment_evidence_authority | P1 A102/A103 | 不存在的凭证字符串可settled，未核金额/账号/证据关系；ledger_service.py:76 |
| ledger_payment_fact_replay | P1 A99/A100 | 同回款记录两次，100加成200；ledger_service.py:84 |
| ledger_negative_reversal | P1 A101 | 收入100冲正-50，净额150；ledger_service.py:145 |

这些是本地事实/金额污染复现，没有真实付款或采购。修复要接通事实与入口，不能只禁用全部POST来获得绿灯。

### S 存储（14 红，Probe=storage_repros）

| Case | 级别 / ID | 实际观察与落点 |
|---|---|---|
| test_sql_text_literal | P1 A113 | TEXT值被JSON编码加引号，主键与payload ID不同；export_commerce_to_sql.py:55 |
| test_export_authority_overwrite | P1 A113 | out指向临时offers.json，原JSON被SQL覆盖且exit0；export:134 |
| test_audit_budget_authority | P1 A110/A113 | budget权威未审计；audit:28 |
| test_audit_execution_authority | P1 A113 | 根目录UNKNOWN execution未审计；audit:185 |
| test_export_budget_authority | P1 A110/A113 | budget未导出；export:18 |
| test_export_execution_authority | P1 A110/A113 | execution权威未导出；export:18 |
| test_audit_primary_key_collision | P1 A113 | 两record_id相同，audit仍0/conflicts0；audit:99 |
| test_backup_nested_destination | P1 A114 | work在source内未拒绝，会递归复制；仅截获，未真递归；backup:169 |
| test_backup_budget_integrity | P1 A114 | 改恢复后的budget未被hash检查发现；backup:100 |
| test_backup_execution_integrity | P1 A114 | 改恢复后的execution未被hash检查发现；backup:100 |
| test_export_radar_unique_constraint | P2 A110/A113 | radar幂等唯一键缺失；export:94 |
| test_export_adoption_unique_constraint | P2 A110/A113 | adoption幂等唯一键缺失；export:94 |
| test_export_reversal_foreign_key | P2 A110/A113 | reversal FK缺失；export:94 |
| test_export_mixed_currency_footer | P2 A113 | CNY100+USD100报告ledger_cny_net200；export:108 |

## 5. 全部85项 ID 到证据与当前状态

层级保持原矩阵 S/H/U/P/R/B。下表“既有专项”仅表示相应代码/用例存在并在整库/专项被运行，不证明整项完整覆盖。FAIL优先于历史PASS；部分、NOT_IMPLEMENTED、WAITING_INPUT、NOT_AUTHORIZED不能合并为已完成。

| ID | 历史报告 / 证据定位 | 当前判定与边界 |
|---|---|---|
| A00 | R0；git/manifest | 版本复核；纠正HEAD，dirty实现需摘要 |
| A01 | full-regression；UTF8两专项 | 全量7 errors；后续8/8专项，不拼第二次全绿 |
| A02 | manifest/run前后state | 核对源码/live集合及字节，范围见清单 |
| A03 | R0/R8/AD2；E/S | FAIL：旧新采购双权威、迁移权威遗漏 |
| A10 | R1；test_intake_confirmation | 部分S/H；逐单元格语义/全部P未证 |
| A11 | R1；owner_scoped_archive专项 | 既有S，候选之外owner归档 |
| A12 | R1；C snapshot_change | FAIL：快照内容/版本未绑定 |
| A13 | R1；same_size_byte_flip专项 | 原件篡改S，不外推所有材料/快照 |
| A14 | R1；operator_channel/identity专项 | 既有H，仅资料确认通道 |
| A15 | R1；部分SKU账号专项；C GET | FAIL：用途出站读取旁路 |
| A16 | R1；replay_conflict/concurrency | 既有单进程S，不是跨进程 |
| A17 | R1；C batch_revocation | FAIL：只撤销批次旧确认仍有效 |
| A18 | R1；persist_failure/replay | 部分S；外部强杀和全窗口P未证 |
| A19 | R1；demo/cross_batch/gaps | 既有S；R真实材料待输入 |
| A20 | R2；test_g1b_cost_eligibility/UI | 部分S/H/U；成本来源仍需实证 |
| A21 | R2；terms_bound/revision | 部分S/H；真实条款R待输入 |
| A22 | R2；阶梯/数量；E MOQ/freight | FAIL：采购未沿用数量费用约束 |
| A23 | R2；unknown/not_applicable/estimated | 部分S；税费/估计依据R待输入 |
| A24 | R2；情景/sale_price_basis | 既有S；展示价只是假设 |
| A25 | R2；C撤销/D新报价 | FAIL：失效和新版本跨层不一致 |
| A26 | R2/R9；黄金核算；E | 部分S/H；财务不变量失败 |
| A30 | R3/R11；两UI套件 | 部分H/U，不代替真实性 |
| A31 | R3；double_click/partial U | 部分H/U，未覆盖全部丢回执/修订 |
| A32 | R3；network_failure U | 既有U，不外推全部页面 |
| A33 | R3/R11；U截图 | G1C双视口；后加整合页移动NOT_RUN |
| A40 | R4；test_radar_dispatch | 部分S，持续取数/成本串联未完整 |
| A41 | R4；D新报价/latest/并发 | FAIL：旧输入、重复任务 |
| A42 | R4；D write_required | FAIL：角色过滤不足 |
| A43 | R4；D stop_boundary | FAIL：停止后后台仍认领 |
| A44 | R4；D completion_persistence | FAIL：失败事件永久pending |
| A45 | R12；pi_outbound_gate；C | FAIL：派生/HTTP/缺引用旁路 |
| A46 | source_quotas专项；D两配额例 | FAIL：非原子/实际尝试未接线 |
| A47 | R4；D latest/C screening/Pi collector | 部分/FAIL：radar refs有范围，Pi仍全库 |
| A48 | R12；reminder专项 | 部分S，未覆盖资源优先级 |
| A50 | R5/R13/R15；C/D probe | FAIL/历史R部分；本轮真实R未跑 |
| A51 | R5/R15；D条件价/数量 | FAIL；公开卡不证明供货库存 |
| A52 | R5；D数量/既有匹配集 | FAIL/部分；真实全规格尚未验 |
| A53 | R5/R15；D retry/resume/protocol | FAIL；R15原标A53 S，不能升R |
| A54 | R5；D未来时间/缺时间守卫 | FAIL：未来矛盾洗成current |
| A55 | R5/R13/R11；HTTP fallback | 部分H；实际fetch客户端未接 |
| A60 | R6/R14/R15；card/pipeline JSON | 部分H/市场公开资料；M1真实原件待输入 |
| A61 | R14 expanded_result | WAITING_INPUT：公开配对不是已核验20组货源 |
| A62 | R6/旧Pi demo证据 | NOT_RUN：真实获准报价Pi分析/预算待输入 |
| A63 | R7/R11；adoption记录 | 部分软件；真实人类决定与结果R待输入 |
| A70 | R7；adoption专项；R11 | 部分S/H/U，受C用途失败阻断 |
| A71 | R7；duplicate_scope/account | 部分S；实际并发窗口待补 |
| A72 | R7；input_change/material_lapse；C | FAIL：撤销资格漏洞传导 |
| A73 | R7；not_product_approval | 部分S，只证明未自动批准，完整Day0 H未复验 |
| A74 | R7；model_suggestion_never_auto_approves | 既有S，人工商品决定保持 |
| A80 | 旧账号/历史任务测试+runbook | R/B WAITING_INPUT |
| A81 | 旧条款/Day0测试 | R/B WAITING_INPUT，无当前账号条款和批准 |
| A82 | 旧执行/浏览器写证据测试 | B NOT_AUTHORIZED，无真实发布回读 |
| A83 | 旧消息/承诺/接管测试 | B WAITING_INPUT，无真实上线商品链 |
| A84 | 旧observation/experiment测试 | 部分软件；上线后R/B待验 |
| A85 | runbook 7/14天 | R/B NOT_RUN，不造订单 |
| A90 | R8/R11；E权威/恢复/会计/detail | FAIL：主链未收口 |
| A91 | E付款/不存在/错身份四例 | FAIL：非付款事实可采购 |
| A92 | R8；E跨账号/预算/旧链 | FAIL：全局键、预算0、双权威 |
| A93 | E MOQ/运费/库存SKU | FAIL：条件/身份未校验 |
| A94 | 旧drills；E预算/主恢复 | FAIL：服务演练不等主入口已接 |
| A95 | 旧确定性交错drills | 部分S，新HTTP预算组合待补 |
| A96 | R8；E receipt/accounting/replay | FAIL：HTTP实额会计缺失/回执无幂等 |
| A97 | 旧服务/runbook | 部分软件；真实采购取消/追回款B未验 |
| A98 | E order_sku_owner | FAIL：实际订单SKU/数量范围未核验 |
| A99 | R8/R9；E重复事实/回执 | FAIL，外部UNKNOWN核验仍须分别验 |
| A100 | R9黄金90/102/52；E | FAIL/部分：函数手算过，HTTP未接且重复收入 |
| A101 | R9冲正竞争；E negative | FAIL：负数制造净额 |
| A102 | R9 provisional；E evidence | FAIL：非空字符串即可settled |
| A103 | E evidence；GAP | 软件前置FAIL，真实首单B WAITING_INPUT |
| A104 | R9/GAP/runbook | R/B NOT_RUN |
| A110 | R10/AD2；S权威/约束 | FAIL/部分设计，完整生产实现未交付 |
| A111 | AD2/GAP | NOT_IMPLEMENTED + WAITING_INPUT：PG实现和实例均需 |
| A112 | AD2/旧JSON drills | NOT_IMPLEMENTED：生产事务/outbox链 |
| A113 | R10/R12；S导出/审计14例 | FAIL，PG装载尚未进行 |
| A114 | R12/R13；S覆盖/嵌套路径 | FAIL/部分，不证明完整权威/PG恢复 |
| A115 | R10；D stop/health | 部分/FAIL；日志容量承诺未完整 |
| A116 | AD2/GAP | NOT_AUTHORIZED，无真实停写切换 |
| A120 | 旧mandates测试 | 部分S，真实指定mandate未签发 |
| A121 | 旧shadow/source计数测试 | 部分S，自然窗口R待输入 |
| A122 | C撤销；旧budget/execution测试 | FAIL/部分，未开真实自动动作 |
| A123 | 旧唯一写链/恢复；E | 部分H/P，新事实重复失败；B未授权 |
| A124 | 旧Pi stop；D stop | FAIL/部分，雷达停止失效 |
| A125 | 旧FAQ/回复测试 | B NOT_AUTHORIZED |
| A126 | 现有自动下单blocked | 保持未授权；不能删阻断当实现 |
| A127 | 无真实工时/订单结果 | R/B WAITING_INPUT，调用量不是价值 |

## 6. 修复顺序与边界声明

1. 修资料撤销/版本及全路径出站，丢引用失败关闭。
2. 修负数冲正/重复和虚假settled、付款与账号/SKU事实、预算/MOQ/运费；收口旧新采购权威。
3. 修新快照实际使用、并发幂等、原子配额与每次尝试计数、价格/数量/未来时间。
4. 接主恢复、HTTP会计、回执幂等；修停止边界和失败终态、协议错误。
5. 修导出/备份输入保护、完整权威覆盖、SQL类型/约束；继续可独立开发的PG repository/迁移/outbox，不把这些全称外部缺口。
6. 将必要对抗回归纳入正式tests，标准UTF8全量复验，更新版本与证据清单。不能删断言或把缺陷直接改称“边界”获得绿灯。

每轮GLM交付须有 case修前/后结果、当前源码摘要、全量/专项分开的退出码、实际模型与平台调用计数、live对照、回退方案、未实现及精确待输入。真实材料、账号条款、用途/预算、首单、PG隔离实例与真实切换批准仍按GAP收集，只解锁对应R/B，不覆盖内部失败。

当前真实自动回复/发布/采购/支付/退款和生产数据库切换继续 NOT_AUTHORIZED。没有新增系统自启动。本轮R复现器仅编译/静态检查，未证明当前真实通道可用；GLM历史匿名读取仅说明当时研究工具返回过公开数据，不替代供应商或经营批准。
