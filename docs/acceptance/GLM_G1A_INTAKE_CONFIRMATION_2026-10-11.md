# GLM G1A 验收报告：原件归档、操作者确认与用途绑定

日期：2026-10-11。执行者：GLM。设计说明：[glm-g1a-design-2026-10-11](../superpowers/plans/glm-g1a-design-2026-10-11.md)。

## 包与范围

G1A（矩阵 A10-A19）。G0 基线：[GLM_G0_BASELINE_2026-10-11.md](GLM_G0_BASELINE_2026-10-11.md)。

## 状态总览

- 软件（S）：**PASS**（19 项专项 + 全量 1508/1508）
- HTTP 集成（H）：**PASS**（真实路由 + 真实持久存储）
- 浏览器（U）：**NOT_RUN**（面板闭环属 G1C）
- 真实资料与来源（R）：**WAITING_INPUT**（需要真实供应商原件、身份/许可材料与操作者实际确认）
- 商业执行（B）：**NOT_AUTHORIZED**（本包不涉及且不授予任何交易权限）

## 变更清单

| 文件 | 变更 |
|---|---|
| `xianyu_agent/candidate_evidence_store.py` | 归档原语扩展：`OWNER_TYPES`、`SUPPLIER_INTAKE_KINDS`、`stage_owner_bytes`/`list_owner_artifacts`/`get_owner_artifact`/`resolve_owner_file`；supersede/激活按 owner 作用域；`_load` 兼容新旧记录；新增 .csv/.xlsx MIME 与 XLSX 魔数校验。候选路径 artifact_id 计算不变（幂等稳定），`EVIDENCE_KINDS` 五类门禁不动 |
| `xianyu_agent/intake_confirmation.py`（新） | 确认记录 store/service/router：preview（逐快照资格 + 原件/材料归档 + 输入摘要绑定，新预览作废旧绑定）、confirm（幂等键 + 请求指纹、有效期封顶、用途前置条件、落盘前复验）、revoke/detail/list、eligibility 实时重算；Bearer+SHA256 操作者通道，Pi job token 403，未配置 503 |
| `xianyu_agent/server.py` | 挂载 `build_intake_confirmation_router`（复用 `resale_manager.evidence_store` 与 commerce repositories，共学单一写者） |
| `.env.example` | `INTAKE_CONFIRM_OPERATOR` / `INTAKE_CONFIRM_TOKEN_SHA256`（默认空=关闭） |
| `tests/test_intake_confirmation.py`（新） | 19 项 A10-A19 契约 + HTTP + server 装配测试 |

复用模块：`supplier_intake.assess_supplier_intake`（逐快照合格性口径未改）、`quote_provenance.assess_quote_provenance`、`CandidateEvidenceStore` blob 归档、`pi_usage_reconciliation` 操作者鉴权模式、`LockedJsonList`。无 schema 迁移：新文件 `commerce/intake_confirmations.json`；证据索引新增 owner 字段向后兼容（旧读取方按 candidate_id 过滤自然排除）。

## 矩阵逐项

| ID | 结果 | 证据（`tests/test_intake_confirmation.py`） |
|---|---|---|
| A10 | PASS (S/H) | `test_a10_archive_roundtrip_and_restart`：重启（重建全部 store）后原始字节逐字节一致、字段对照（provenance/import_basis/价格）保留 |
| A11 | PASS (S) | `test_a11_owner_scoped_archive_without_fake_candidate`：owner_type=supplier_batch、candidate_id 为空、无虚假候选、候选查询不可见 |
| A12 | PASS (S) | `test_a12_newer_preview_invalidates_old_binding` + `test_a12_digest_mismatch_and_quote_deadline`：digest 不符 409；报价截止跨过 409；批次撤销后 409 |
| A13 | PASS (S) | `test_a13_same_size_byte_flip_breaks_eligibility`：同尺寸翻字节 → eligibility 报 `original_file_modified` |
| A14 | PASS (H) | `test_a14_operator_channel_only` + `test_a14_operator_identity_not_taken_from_body`：无凭证 401、错凭证 401、job token 403、body operator 字段 422、operator 身份取自配置 |
| A15 | PASS (S/H) | `test_a15_partial_sku_use_and_account_isolation` + `test_a15_business_use_requires_account_scope`：未批准 SKU/用途/账号均不继承 |
| A16 | PASS (S) | `test_a16_replay_conflict_and_concurrency`：同请求重放同一确认；同键异载 409；4 线程并发单记录 |
| A17 | PASS (S) | `test_a17_validity_never_extends_quote_terms`（有效期封顶到快照截止）、`test_a17_revoke_batch_and_confirmation`、`test_a17_reimported_old_original_stays_stale`（重导入旧原件不可确认，eligibility 报 quote_expired） |
| A18 | PASS (S) | `test_a18_persist_failure_leaves_no_ghost_and_replay_recover`：OSError 注入无幽灵记录，恢复后同键成功，重载后恰好 1 条 |
| A19 | PASS (S) | `test_a19_precise_gaps_and_partial_confirmation`（硬矛盾排除+缺口保留）、`test_a19_demo_marker_not_confirmable`、`test_a19_cross_batch_reference_rejected` |
| H 层 | PASS | `test_http_flow_readonly_and_status`、`ServerAppMountTests`（未配置 503 / 只读 200） |

P 层说明：A16/A18 的进程级证据以**进程内**失败注入（monkeypatch OSError）+ 全 store 重建模拟重启覆盖；外部子进程强杀演练未在本包执行——当前仍是 ADR-0001 单写者 JSON 边界，跨进程恢复属 G5A 范围。此为已知边界，不是已验证的跨进程恢复。

## 命令与退出码

```text
unittest discover -p test_intake_confirmation.py   # 19/19 OK, exit 0
unittest discover -p test_candidate_evidence_store.py  # 9/9 OK（回归）
unittest discover -p test_supplier_intake.py        # 23/23 OK（回归）
unittest discover -p test_commerce_api.py           # 13/13 OK（回归）
unittest discover -p test_supplier_import_precision.py  # 19/19 OK（回归）
powershell scripts/run_tests_isolated.ps1           # 1508/1508, 363.177s, exit 0
```

live 前后 20 文件 SHA-256 一致；零真实模型调用、零平台读写、未新增自启动；源码在测试期间无变化（G0 监测口径）。

## 未完成/不支持行为

1. 面板（G1C）尚未提供确认 UI；当前仅 API/CLI 面。
2. Pi 任务输入的用途门禁接驳在 G1B/G2A（eligibility API 已就绪）。
3. `superseded_by` 字段保留为空：当前语义为显式撤销 + 追加新确认，不做自动替代。
4. P 层外部子进程演练与跨进程恢复推迟至 G5A（见上）。

## 需要用户提供的精确输入（R 层 WAITING_INPUT）

1. 一份真实供应商报价原件（CSV/XLSX/粘贴文本，≤2MiB）及其取得时间、有效期。
2. 供应商身份材料与使用许可材料的脱敏引用/文件（可选上传归档）。
3. 一位真实操作者：设置 `INTAKE_CONFIRM_OPERATOR` 与 `INTAKE_CONFIRM_TOKEN_SHA256`（sha256(随机 token)），由其本人执行 preview→confirm。
4. 若要外部模型用途：明确允许提供给模型的范围与预算。

## 回退方案

删除 server.py 挂载行 + 两个新文件即回到 G0 行为；`intake_confirmations.json` 与 owner 归档记录为新增数据，不影响旧读取方；无 schema 迁移。

## 下一工作包

G1B：清除演示费率默认值、费用来源绑定账号+类目条款版本、成本修订失效联动（A20-A26）。
