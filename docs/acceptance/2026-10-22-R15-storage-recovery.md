# R15 实施验收报告：迁移和存储恢复

日期：2026-10-22。基线为 R14 提交 `53f68b9`。

**结论：R15 软件/集成层 PASS。审查 storage 3 缺口全部转绿（原探针的"复现缺陷"断言全部失败=缺陷不复存在）。全量离线 1743/1743 OK（13 skipped=PG），live 数据 247 文件不变。S2"事务与经济性"阶段（R08–R15）收官。**

## R15

```yaml
task_id: R15
owned_files: [scripts/export_commerce_to_sql.py, scripts/commerce_backup_drill.py, scripts/freeze_manifest.py(新增), tests/test_r15_storage_recovery.py(新增)]
software_status: PASS   # 7/7 新验收 + 存量 g5a_drills 3 + commerce_audit 5 不破
integration_status: PASS  # 全量 1743/1743 OK, 数据 247/247 不变
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [PG 真实导入加载属 R15 后置的真库演练（R10 评估集群可用），本轮以 SQLite immediate-FK 作见证 + DDL/排序正确性]
rollback: 切换前放弃影子库（freeze manifest 保留冻结点）；切换后有新写入须对账/前滚，不能直接用旧 JSON 覆盖
next_task: R16（Docker 镜像与 Compose，S3 开始）
```

## 审查 3 缺口 → 修复 → 验收（探针行为反转证明）

探针（storage-combinations.py）的断言设计是"**复现缺陷**"——修复后三例全部从 CONFIRMED_GAP 变为复现失败（HARNESS_ERROR），这正是缺陷消除的直接证据。同时移植为常驻测试断言修复后的行为：

| 审查缺口 | 根因 | 修复 | 常驻测试 |
|---|---|---|---|
| **hardlink 覆写权威 JSON**（--out 在数据目录外但硬链接到 ledger.json，导出 SQL 直接毁掉账本） | 路径重叠检查只看路径不看 inode | 写出前对数据目录下每个文件做 `samefile()` 同 inode 检查，命中即拒绝（exit 1） | `test_hardlinked_output_refused_authority_untouched`：拒写 + 权威文件字节不变 |
| **最终副本验真缺口**（备份演练只验第一次恢复；divergence 回滚后的最终树从不验哈希；未列文件混入不查） | 验证只挂在第一次 copytree 后 | 新增 `final_verification` 阶段：**全量遍历**（整个 commerce/ 目录 rglob + 顶层 + evidence，不只已知清单）对 manifest 逐文件验哈希；未列文件列入 unexpected_files；任一失败 exit 2 | `test_corrupted_final_restore_fails_closed`（注入损坏→exit2）+ `test_unlisted_file_in_final_tree_flagged`（影子文件→exit2）+ `test_clean_run_exits_zero_with_final_verification` |
| **FK 导入顺序**（账本冲正行在文件中先于原件→立即 FK 加载失败） | INSERT 按文件行序生成 | `_ordered_ledger_rows` 拓扑排序：原件必先于其冲正（稳定序保留；孤儿冲正保持原序留给审计器报告） | `test_reversal_first_json_loads_under_immediate_fk`：冲正在前的 JSON→导出排序后 SQLite immediate-FK 两行全部载入 |

## 全域冻结 manifest（任务书"冻结点覆盖全部权威域"）

新增 `scripts/freeze_manifest.py`：枚举全部权威写域（BASELINE 域普查：14 个 commerce/物理域目录 + 8 个顶层账本 + live 子树），产出 path→{sha256,bytes} 冻结清单；`--verify` 对当前树核对：mismatch/missing/unexpected 三分类，**脏数据只报告不猜修**，不一致 exit 2。迁移切换前的等价性检查（记录数、总额、证据哈希一致）以此为入口。

PG 真库恢复演练（真库行数/总额/预留/证据哈希对账）在 R10 的评估集群（E:\pg-r10，端口 54329）已具备条件，作为 R15 后置项随 R16-R18 的部署工作一起跑——本轮以 SQLite immediate-FK 见证 + 冻结 manifest 完成离线侧验收。
