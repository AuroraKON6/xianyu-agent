# GLM 接手入口：闲鱼自动经营项目

日期：2026-10-11。状态：规划已交付，新增工作包尚未实施。

> **2026-10-20 起的最新入口**：全面审查与重构计划见 `E:\闲鱼\research\resale-rebuild-review-2026-10-20\00-MASTER-PLAN.md` 与 `10-GLM-TASKBOOK.md`（R00–R36 任务卡）。当前实施基线与能力账本见 `docs/refactor/BASELINE_20261020.md` 与 `docs/refactor/capabilities.json`。本文的 G0–G5 计划保留为历史与领域背景；两份计划冲突时以 2026-10-20 任务书为准。

工作目录：`E:\闲鱼\xianyu`。总负责人：用户。方案与验收参考：Codex。代码、测试、运行证据执行者：GLM。

本文件是 GLM 的开工入口，不是商业上线批准。GLM 是开发执行者，不代表生产模型也要切换为 GLM。

## 1. 唯一目标与阅读顺序

目标：建设 Pi 驱动的国内闲鱼经营系统，在已确认的供应商、SKU、账号和预算内自动发现可交付的差价机会，逐步处理常规运营；用户负责经营范围、资金边界、重要承诺和异常。最终通过真实订单、履约、回款、售后和已结算贡献利润验证价值。

按顺序阅读：

1. [项目总计划与工作包](docs/strategy/GLM_PROJECT_MASTER_PLAN_2026-10-11.md)。
2. [验收矩阵与证据要求](docs/strategy/GLM_ACCEPTANCE_MATRIX_2026-10-11.md)。
3. `PROJECT_STATUS.md` 顶部 R9 集成结果，以及该段引用的验收文档。
4. `docs/SUPPLIER_INTAKE_READINESS.md`、`docs/ops/supplier-quote-input-contract.md`。
5. `docs/adr/0001-commerce-storage-and-migration.md`。
6. `docs/ops/commercial-alpha-runbook.md`，特别是账号、候选证据、Day-0、发布回读和真实经营顺序。

09-18 总计划保留为历史与领域背景；后续实施顺序以本次 G0-G5 计划为准。不得将历史 WP 的服务层 PASS 当成本次端到端 PASS。

## 2. 不得误读的当前状态

- 2026-10-11 复核 HEAD 为 `4bd2cf7`，但工作区有大量 R6-R9 未提交成果。HEAD 不是全部当前代码，禁止 reset、checkout 或清理这些改动。
- 09-23 清单记录隔离回归 `1489/1489`；10-11 复核清单内 254 份源码哈希一致，但未在该次评审重跑测试。你开工时必须重新建立基线。
- 主入口已采用 Pi；四个持久分析角色是报价缺口、匹配审计、定价复核、商机筛选。单 worker 串行，当前仍有手动 run-next 入口。
- R7 证明过真实 Pi 调用和证据持久化，但用的是 `source=demo` 报价，不是实际供应商经营。
- 商机 API/面板已有；持续市场采集、调度、新商机到旧试卖、新采购账本到主服务均未完整贯通。
- 当前配置的 live 账号表 0 条，新 commerce 目录未见报价/匹配/商机/采购/库存/账本文件。SQLite 有一行订单，未核实来源，不按真实成交计数。
- PDD、1688、浏览器观察登记为 UNVERIFIED。不能把开源客户端、类名、HTTP 200、历史免费 API 说明写成已接通。
- 没有新增平台动作授权。自动采购不是开关问题，现有执行器明确不支持自动提交供应商订单。

## 3. 开工指令

执行 G0，然后执行 G1A、G1B、G1C。每个子包完成后按验收矩阵运行对应测试并记录证据，不必为低风险实现细节反复询问用户。

第一阶段的具体交付：

> 操作者导入一份报价，在面板核对原始字段、来源、用途、规格与成本；确认绑定到不可变版本；系统能够使用这份已确认资料，不让操作者逐步复制内部 ID。输入变更、到期或撤销会立即影响后续使用资格。

G1 软件验收通过后继续 G2，目标是资料更新自动触发一张可复核的商机卡。没有真实输入时可完成隔离工程，但必须把真实资料验收标为 WAITING_INPUT，不能用 demo 补齐。

之后沿总计划 G3、G4、G5 的依赖推进。代码可提前在隔离环境实现；真实来源访问、模型消耗和平台动作须满足对应批准。不要将“继续开发”理解为“自由执行交易”。

## 4. 首次操作顺序

1. 检查仓库指令、HEAD、dirty diff、数据目录与依赖；只输出配置是否存在及非敏感范围，不打印密钥/Cookie。
2. 记录源码和 live 文件摘要，确认测试不会导入生产配置后写 live。
3. 使用项目隔离脚本建立 G0 测试基线；不要直接在默认环境运行全量 unittest。
4. 对比 G1 涉及的原文归档、审批、准入、利润与 UI 代码，提交小范围设计说明，随后实施，不另造完整业务系统。
5. 缺陷先复现；新增行为先定义契约和测试。完成专项回归、HTTP 链路、必要的浏览器交互与全量隔离回归。
6. 更新独立的执行记录；本计划和矩阵中的“计划/未执行”不得直接改写成全部完成。

从仓库根目录执行，命令逐条运行并保留退出码：

```powershell
git status --short
git rev-parse HEAD
git diff --stat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1
node --check web/app.js
node --check web/pi-usage.js
git diff --check
```

这些检查通过不等于商业批准。测试脚本只承担已有隔离逻辑；GLM 仍须核对新增测试/浏览器进程/模型调用是否绕开隔离。

## 5. 每轮交付格式

在 `docs/acceptance/` 保存按工作包和实际日期命名的报告；可附 JSON manifest。报告至少包含：

```text
package_id / source_revision_or_manifest / worktree_changes
software_status / integration_status / real_data_status / commercial_status
changed_files / reused_modules / schema_or_api_changes
test_commands / exit_codes / failed_test_names / actual_test_count
input_provenance / model_calls_and_usage / platform_read_count / platform_write_count
live_hash_before_after / screenshots_and_runtime_artifacts
known_limits / rollback / next_package / exact_external_inputs_needed
```

状态：PASS、FAIL、NOT_RUN、WAITING_INPUT、NOT_AUTHORIZED。只有实际进行且证据满足要求的部分可以写 PASS。

源码在测试中途变化，需要重验受影响范围；不能把不同版本的专项与全量结果拼成一个 PASS。已有用户改动保持不动；如需要 Git 提交，只暂存本包明确拥有的文件/变更，不把所有 dirty 文件一起打包。

## 6. 执行边界

- 不改 `E:\闲鱼\codex openclaw`、跨境项目或本地第三方程序；不恢复 OpenClaw。
- 不复制第三方项目的账号库、消息循环和交易队列；只做经过验证的薄适配。
- 不为测试启用真实自动回复、发布、采购、支付、退款；不改真实授权和预算。
- 不新增系统自启动、计划任务或可见后台终端。需要服务时按显式命令启动，记录端口，测试结束清理自己启动的进程。
- 资料批准、经营决策和平台动作审批是不同含义，不合并成一个 `approved=true`。
- 不删除旧证据，不给 demo/unknown 自动补真实标签，不延长原报价有效期。
- 生产写入统一走 `WorkOrder -> Approval -> ExecutionJobQueue -> ActionExecutor -> Adapter`；Pi 分析只产生建议，不绕过该链。
- JSON 阶段只允许一个业务写入进程。新增定时任务先在同一应用生命周期内调度；拆第二个 writer 前先完成 G5A。
- 用户需要提供真实事实时，输出一个集中、精确的缺口清单，同时继续可独立开发的工作；不得虚构账号、回执、供货关系或交易来填满门槛。

## 7. 可以直接给 GLM 的任务说明

> 在 E:\闲鱼\xianyu 工作。先读 GLM_HANDOFF.md 及其链接的总计划和验收矩阵。保留全部现有未提交改动。从 G0 建立可复现基线，然后实施 G1A/G1B/G1C，再按依赖推进 G2-G5。复用 Pi、现有 repository、候选/试卖、审批与唯一执行链，不重建 Agent 框架或第二套订单系统。以“真实资料确认后自动产生可复核商机，再贯通单品试卖与回款”为目标。每包交付代码、测试、运行证据和剩余阻塞。可以自主完成隔离开发；没有真实资料或授权时只标注相应验收待输入，不伪造事实，不执行真实交易，不启用自启动。
