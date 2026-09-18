# 商业数据备份与恢复 Runbook（WP14 最小版）

日期：2026-09-18。范围：`data/commerce/`（WP01 以来的 JSON 聚合账本：offers/matches/opportunities/decisions/agent_jobs/import_batches/provider_capabilities/operating_mandates/budget_reservations/shadow_authorizations/reply_drafts/inventory/procurement_orders/ledger）。

## 备份

```powershell
.venv\Scripts\python.exe scripts\backup_commerce.py create --label daily
```

- 生成 `backups/commerce-<UTC时间戳>[-label]/`，内含全部 `.json` 聚合文件 + `backup_manifest.json`（每文件 SHA-256 与字节数）。
- create 结束时自动做一次 verify，退出码非 0 即备份可疑，不得用于恢复。

## 校验

```powershell
.venv\Scripts\python.exe scripts\backup_commerce.py verify backups\commerce-XXXX
```

- 逐文件哈希/大小比对；缺失、篡改、多余文件都会列出。

## 恢复演练（不动 live 数据）

```powershell
.venv\Scripts\python.exe scripts\backup_commerce.py drill backups\commerce-XXXX
```

- 把备份恢复到 `backups/drill-scratch/` 并重新校验，证明备份确实可恢复。演练前后 live 目录字节不变（有测试保护）。

## 真实恢复流程（人工执行，不要自动化）

1. 停止服务（`scripts\stop_xianyu_workflow.ps1`）。
2. 把当前 `data/commerce/` 改名为 `commerce-broken-<时间>`，从备份复制 `*.json` 回 `data/commerce/`。
3. 启动前先跑一次 verify 命令确认复制完整。
4. **平台对账强制**：恢复出的执行类记录（agent_jobs 中 write 任务、执行队列状态）只能当作"恢复窗口前的本地事实"；任何"看似没执行"的平台动作必须先按 `docs/ops/execution-recovery-runbook.md` 与平台核对后才能重试，禁止自动重发。
5. 在 `PROJECT_STATUS.md` 记录恢复时间、使用的备份目录和跳过的记录。

## 当前诚实边界

- 这是文件级备份，目标 RPO 15 分钟/RTO 60 分钟**未实现也未宣称**——那需要连续归档机制（完整 WP14 范围）。
- SQLite（账号/商品/订单旧域）与 `candidate_evidence/` 证据 blob 不在本脚本范围内；前者走数据库迁移备份惯例，后者按候选证据 SOP 做一致性快照。
- 每日定时任务未配置（不擅自加 Windows 计划任务；需要操作者决定调度方式后接入）。
