# 第二批 R08–R09 实施验收报告

日期：2026-10-20。实施者：GLM（单 writer）。基线为第一批提交 `e403fea`。验收依据：`E:\闲鱼\research\resale-rebuild-review-2026-10-20\10-GLM-TASKBOOK.md`。

**结论：R08、R09 软件/集成层 PASS；真实数据/商业层 NOT_RUN。全量离线 1681/1681 OK（exit 0），live 数据 247 文件前后一致。**

## 总体验证

| 验证 | 结果 | 证据 |
|---|---|---|
| 全量离线（第二批后） | **1681/1681 OK（0 失败/0 错误），exit 0**；数据 247 前后一致；沙箱自动清理 | `/tmp/xianyu-offline-batch2b.log` |
| live 数据完整性 | 247/247 SHA-256 与开工清单一致（本批全程零事故，未再发生第一批的绕过入口污染） | `sha256sum -c` 批中多次复核 |
| 凭据/网络 | 离线入口照常清除 7 个环境凭据键；loopback-only；子进程白名单 | 同第一批 R01 机制 |

## R08｜取数额度与重试

```yaml
task_id: R08
owned_files: [xianyu_agent/source_quotas.py, xianyu_agent/market_channel.py, tests/test_quota_combinations.py, tests/test_market_channel.py(受管账本适配), tests/test_source_quotas.py(既有,未改而全绿)]
software_status: PASS
integration_status: PASS  # 审查5个失败探针(Q1-Q5)全部转绿并入库为常驻测试
real_data_status: NOT_RUN
commercial_status: NOT_RUN
commands_and_exit_codes: ["test_quota_combinations -> 8/8 OK", "test_source_quotas -> 5/5 OK", "test_market_channel -> 15/15 OK"]
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [锁为独占文件锁(msvcrt/fcntl)，无等待队列；两进程竞态测试覆盖实际唯一准入]
rollback: disable source（人工导入继续）；未使用"无限额度"或关检查回退
next_task: R10
```

修复对照（审查探针 → 实现）：

| 探针 | 根因 | 修复 |
|---|---|---|
| Q1 重开后 status 崩溃 | `_contract_limit` 把行 dict 当列表取 `._items` | 账本重写：读写均基于磁盘行列表 |
| Q2 耗尽源仍被新会话取数 | "首次请求免计"是会话内存标志，跨会话可重复白嫖 | 单次预授权票据：`check_and_consume` 签发并持久化，会话首请求必须 `claim_precharge` 原子认领；无票会话自行计量 |
| Q3 未知合同自动取数 | 未受管源在会话内不设防 | 未受管 + 非手动 → 任何传输尝试前拒绝（status=quota_unknown，0 次调用） |
| Q4 重启复活被调低限额 | 限额只在建行时写入，重启回读旧值 | 每次变更都持久化当前有效限额（收紧/撤销跨重启生效） |
| Q5 429 零冷却重试 | `sleep(0)` | 429 强制正冷却（2s×尝试序数递增）；重试全部计数 |

任务书追加验收：**两进程抢最后 1 次额度**（子进程实测恰好一个 ADMITTED、账面 attempts 无超发/丢更新——账本改为 OS 文件锁 + 每事务重读磁盘 + 原子替换）；**响应丢失**（传输已计量、预授权被消耗、无免费重试）；**撤销权限**（`revoke` 持久化 limit=0，跨重启手动/自动全拒）。

## R09｜单一完整成本表

```yaml
task_id: R09
owned_files: [xianyu_agent/unit_economics.py, xianyu_agent/opportunity_service.py, tests/test_unit_economics.py(R09验收类), tests/{test_g1b_cost_eligibility,test_recovery_drills,test_g4b_golden_finance,test_commerce_api}.py(费率补全fixed="0")]
software_status: PASS   # 31/31
integration_status: PASS  # 全量1681绿；商业条款路径(rate+fixed)不受影响
known_limits: [税费缺省按"未评估"记录（消费口径含税假设），报价为不含税时调用方必须传 tax 条款；税率仅登记永不套算]
rollback: 暂停新资格判定（unknown 不改 0）
next_task: R10
```

变更（rule_version → `unit-economics-v2`）：

| 项 | 行为 |
|---|---|
| 部分费率 | 只给 rate 或只给 fixed → 平台费 unknown（"declare the other explicitly"），利润连带 unknown；真无固定费须显式 `"0"` |
| 盈亏平衡诚实 | 平台费 unknown 时 break_even/min_viable 为 unknown 并注明"不得把剔除平台费的结构当保本价"（审查 03 号发现） |
| 场景采购/运费覆盖 | procurement_cost/supplier_freight 纳入 scenario_overrides（审查"场景采购/运费覆盖问题"——保守采购涨价此前实际到不了保守利润）；现金占用仍按基准值 |
| 退货运费 | `return_freight` 一等参数：有概率 → 期望损失=概率×退货运费；无概率 → unknown（不猜） |
| 税费适用 | `tax={applicability, source, amount?, rate?}`：not_applicable 跳过、applies 无金额 unknown（税率仅登记）、缺省输出 `tax_applicability: not_assessed` 注记；绝不假设税率 |
| 同入口同结果 | 计算唯一入口 compute_unit_economics；同输入两次调用逐字段相等（测试断言）；金额全程 Decimal |

反例覆盖：缺费率、缺固定费、退货运费单独/组合、阶梯价（A22 既有）、MOQ 现金占用、采购涨价压测、币种冲突（EconomicsError）、税费四态、盈亏平衡 unknown、确定性。

## 文件清单（提交边界）

修改（9）：tests/{test_commerce_api,test_g1b_cost_eligibility,test_g4b_golden_finance,test_market_channel,test_recovery_drills,test_unit_economics}.py、xianyu_agent/{market_channel,opportunity_service,source_quotas,unit_economics}.py
新增（1）：tests/test_quota_combinations.py

## 下一批建议

R10（PostgreSQL 全域合同与 UoW，依赖 R02/R03 已就绪）→ R11（采购/预算/库存原子命令——审查 finance 反例在此批修复）→ R12–R15。并行：B0–B1 经营资料准备；R16 前置环境核验（WSL2/虚拟化/磁盘）可提前做只读检查。
