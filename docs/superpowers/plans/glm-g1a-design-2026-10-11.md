# G1A 设计说明：原件归档、操作者确认与用途绑定

日期：2026-10-11。范围：G1A（矩阵 A10-A19）。依据：总计划 §G1A、GLM_HANDOFF §3。

## 目标

操作者导入报价批次后，可将**原始文件归档**、声明与不可变快照集合绑定成一条**资料用途确认记录**；下游任何使用（本地分析 / 外部模型 / 经营）通过 `eligibility` 实时判定资格，输入变更、到期、撤销立即失效。不新建供应商订单/审批系统，不发放交易权限。

## 复用与新增

| 复用 | 用途 |
|---|---|
| `supplier_intake.assess_supplier_intake` | 逐快照结构合格性（不重写口径） |
| `quote_provenance.assess_quote_provenance` | 时间/来源门禁 |
| `candidate_evidence_store.CandidateEvidenceStore` | blob 归档原语，扩展 owner_type/owner_id |
| `pi_usage_reconciliation` 的 Bearer+sha256 操作者模式 | 受信任操作者通道（不引入 SSO） |
| `commerce/repositories.LockedJsonList` | 确认记录 JSON 存储（原子写、线程锁、回滚） |

新增一个模块 `xianyu_agent/intake_confirmation.py`（store + service + router），不新建第二套状态机。

## 归档原语扩展（candidate_evidence_store.py）

- 新增 `OWNER_TYPES = ("candidate", "supplier_batch")` 与 `SUPPLIER_INTAKE_KINDS = ("supplier_quote_original", "supplier_identity_material", "supplier_use_permission_material")`。
- 新方法 `stage_owner_bytes(owner_type=..., owner_id=..., kind=...)`；supplier_batch 记录 `candidate_id=""`、`owner_type/owner_id` 另存。artifact_id 派生串加 `supplier_batch:` 前缀，与候选记录不碰撞；候选路径 artifact_id 计算保持不变（幂等稳定）。
- `_load` 接受两种合法记录；候选侧所有现有查询（按 candidate_id 过滤）不受影响（supplier 记录 candidate_id="" 被自然排除）。supersede/activate 语义按 owner 作用域。
- `EVIDENCE_KINDS`（5 类候选门禁）**不动**，commercial_alpha/server 的 12 项门禁不受影响。

## 确认记录（intake_confirmations.json）

```
confirmation_id ic_<hex16>；record_type intake_use_confirmation
batch_id / provider_id / supplier；snapshot_ids（被批准的子集，允许部分 SKU）
original_artifact_id / original_sha256（归档原件；使用前重算 hash）
declaration_sha256；supplier_identity_ref；use_permission_ref
allowed_uses ⊆ {local_analysis, external_model, business_use}
account_scope {mode: none|all|list, accounts: []}
operator（仅来自受信任通道）、confirmed_at、valid_until、policy_version
status confirmed|revoked；revoked_at/by/reason；superseded_by
idempotency_key；input_sha256（batch+snapshots+declaration+原件摘要全绑定）
```

- `valid_until = min(声明期限, 各快照 expires_at, 观察时间+24h)`：人工确认**不能**延长供应商原报价期限（A17）。
- 幂等：同 idempotency_key + 同 input_sha256 返回同一记录；同键不同载荷 409（A16）。

## 服务流程

1. **preview**（operator 鉴权）：批次必须 committed；对声明覆盖的每个快照运行 `assess_supplier_intake`，batch 级 fail 阻止整批，snapshot 级 fail 仅排除该快照并返回精确缺口（A19、A31 部分合格语义）；归档原件（stage_owner_bytes）；返回一次性 preview（30 分钟 TTL）绑定全输入 digest 与每快照资格。
2. **confirm**（operator 鉴权）：preview 未过期、digest 一致（A12：预览后修改文件/声明/快照 → 409）；重算归档 blob hash（A13）；原子写确认。写失败无记录（A18）。
3. **revoke / detail / list**。
4. **eligibility(snapshot_id, use, account, now)**：确认存在且未撤销未过期 ∧ 批次 committed ∧ 快照 provenance 合格（24h/到期）∧ blob 重算一致 ∧ use ∈ allowed_uses ∧ 账号 scope 匹配。返回 eligible + reasons + confirmation_id。只读、可重算、不缓存 ready（A15/A17）。

## HTTP 路由（新列明）

```
GET  /commerce/supplier-imports/{batch_id}/confirmation            当前确认状态（只读）
POST /commerce/supplier-imports/{batch_id}/confirmation/preview     operator
POST /commerce/supplier-imports/{batch_id}/confirmation/confirm     operator
POST /commerce/intake-confirmations/{confirmation_id}/revoke        operator
GET  /commerce/intake-confirmations/{confirmation_id}               只读
GET  /commerce/intake-confirmations?batch_id=                       只读
GET  /commerce/intake-confirmations/eligibility?snapshot_id&use&account  只读
```

operator 通道：`INTAKE_CONFIRM_TOKEN_SHA256` + `INTAKE_CONFIRM_OPERATOR` 环境变量；Bearer 匹配 sha256；带 Pi job token 头一律 403（A14）；未配置 mutating 路由 503。伪造 operator 字段/请求体身份不被读取。

## 边界

- 资料确认 ≠ 供应商认证 ≠ 商品批准 ≠ 平台动作授权：记录只写已核验事实，不写 provider_verified。
- demo/unknown 来源在 assess 阶段已被排除，不能进入确认。
- 恢复：preview 内存态不落盘；确认单条原子写；blob 与确认跨文件失败 → 无确认记录 = 无资格，pending artifact 不影响门禁。
