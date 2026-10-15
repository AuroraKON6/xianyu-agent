# 闲鱼 GLM 计划：集中缺口清单（给用户的精确输入一览）

更新：2026-10-12。配套：[GLM_HANDOFF.md](../../GLM_HANDOFF.md)、[总计划](../strategy/GLM_PROJECT_MASTER_PLAN_2026-10-11.md)、[验收矩阵](../strategy/GLM_ACCEPTANCE_MATRIX_2026-10-11.md)。

软件工程侧：G0-G5A 首批 + 前端整合 + 出站门禁/提醒/演练共 **五轮全部交付**（全量隔离回归 1580/1580）。以下为阻塞 R/B 层验收的全部外部缺口——任一到位即可继续对应包，互不阻塞。

## 1. 真实供应商资料（解锁 G1A/G1C R 层，M1 里程碑）

| 需要 | 怎么给 |
|---|---|
| 一份真实报价原件（CSV/XLSX/粘贴文本 ≤2MiB，含观察时间/有效期） | 面板「1b. 资料闭环」粘贴或选择文件 → 预览 → 确认导入 |
| 供应商身份与使用许可材料（脱敏引用即可） | 声明 JSON 的 supplier_identity_ref / use_permission_ref（可另传文件归档） |
| 操作者凭证 | `.env` 设 `INTAKE_CONFIRM_OPERATOR=<名字>` 与 `INTAKE_CONFIRM_TOKEN_SHA256=<sha256(随机token)>`（生成命令见 .env.example 注释），由您本人走预览→确认 |

## 2. 真实商业条款（解锁 G1B R 层）

指定账号 + 目标类目当日页面核对：费率、固定费、退货/保证金状态。入口：`POST /accounts/{cookie_id}/commercial-terms`（控制台「记录账号商业条款」）。

## 3. 市场读通道授权（解锁 G2B/G2C R 层，M2）

| 需要 | 怎么给 |
|---|---|
| 账号登录 + 允许少量只读探针 | 完成账号接入后设 `MARKET_CHANNEL_CONFIGURED=1`；真实读探针另设 `MARKET_CHANNEL_PROBE_AUTHORIZED=1`（是一次真实平台读） |
| 首批真实响应的人工逐条核对 | 探针返回样本在 `GET /commerce/market-channel/status` 可审阅（不曝光凭据） |
| Pi 分析预算与 external_model 用途许可 | 确认材料时勾选"外部模型"用途；Pi 出站门禁已就位（未确认的真实报价不会出站） |

无通道期间：面板「2. 市场观察登记」人工观察始终可用（来源类型恒与自动抓取区分）。

## 4. 真实首单链（解锁 G3B/G4B R/B 层，M3/M4）

按 [commercial-alpha-runbook](../ops/commercial-alpha-runbook.md) 顺序：账号接入 → 历史任务逐单审阅 → 候选五类证据 → 条款 → Day-0 → 首次发布人工批准 → 真实询盘 → 付款订单 → 人工供应商下单/付款凭证登记（`/commerce/procurement/*` 已就绪）→ 回款与复盘。每一真实凭证到位一笔记一笔，不用演示数据填门槛。

## 5. PostgreSQL 环境（解锁 G5A A111/A112/A114 演练）

选定实例方式（Docker / 本机安装 / 现有实例）并批准隔离演练（不触碰 data/live）。已就绪：ADR-0002 全映射、SQL 导出器 dry-run、备份/恢复演练（live 实测：备份 0.172s / 恢复 0.812s，全哈希通过）。演练全绿后再单独批准真实停写切换（A116）。

## 当前 live 状态提示（只读观察）

- 账号 0、报价/商机/采购/账本文件 0；commerce 仅 agent_jobs（含 1 个 queued 任务 `job_be7e79f3f16a4494`——R7 时代的报价缺口审查任务，执行会调真实模型，未获预算授权故未运行；可忽略或在授权后 run-next）。
- 审计基线（2026-10-12）：0 唯一键冲突 / 0 孤儿引用 / 证据 0 件。

## 我们不会做的事（边界重申）

未获批准不执行真实发布/采购/付款/退款；不恢复 OpenClaw；不新增自启动（RADAR_DISPATCHER_ENABLED 默认 0）；demo 不冒充真实；三种批准（资料用途/商品决策/动作授权）不合并。
