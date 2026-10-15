# GLM 第五轮报告：Pi 出站门禁 / Radar 提醒 / G5A 演练 + 夹具时间炸弹修复

日期：2026-10-12。全量隔离回归 **1580/1580**（1571 + 9 新增），exit 0；live 20 文件前后一致；零真实模型/平台调用。

## 交付

| 项 | 内容 | 验收 |
|---|---|---|
| **Pi 出站用途门禁**（G1B-6/A45 遗留接驳） | `PiTaskRunner` 新增 `outbound_gate`；`_collect_quote_inputs` 将未确认 external_model 的**真实**报价整体扣留（仅 id/SKU/原因出站，价格与属性不出）；demo 来源明确豁免（显式合成物）；定价采集对未许可商机的利润/现金金额替换为 unknown（材料扣留标记）；门禁异常**失败关闭**。server 以 late-binding 接线 intake 确认服务（服务不可用→拒绝）。`tests/test_pi_outbound_gate.py` 5 专项：含 fake-Pi 捕获出站上下文断言价格字符串与规格文本不出现、拒绝原因如实出现；未配置门禁时行为与旧完全一致（回归） | A45（未批准输入不出站）S 层 PASS |
| **Radar 提醒去重**（A48） | `RadarReminderStore`：按 (SKU, 原因类型, 输入摘要) 幂等——重复触发累加 repeat_count 不建新行；事件成功处理时解决该 SKU 全部开放提醒（历史保留）；status 与 `GET /commerce/radar/reminders` 暴露。`tests/test_radar_dispatch.py` 新增 a48 专项（9/9） | A48 S 层 PASS |
| **G5A JSON 演练** | `scripts/commerce_backup_drill.py`（备份带 SHA-256 manifest→空目录恢复全哈希校验→**盲回退丢数据演示**：备份后新写入在盲恢复下消失，实证 ADR-0002 禁止盲回退的理由；篡改可检出）；`scripts/export_commerce_to_sql.py`（ADR-0002 表结构 DDL + 唯一键索引 + 全量 INSERT + 行数/金额对账 footer 的 dry-run，无需数据库）。`tests/test_g5a_drills.py` 3 专项 | A113（JSON→SQL 部分）/A114（JSON 部分）S 层 PASS；PG 实装仍 WAITING_INPUT |

## 修复的测试基建缺陷（时间炸弹）

`test_intake_confirmation.py` HTTP 类的 setUp 给 `tests.test_intake_confirmation` 打 NOW 补丁，但 discover 模式下模块以无前缀名加载——**两个模块实例，补丁落空**。硬编码夹具 NOW+20h 的声明有效期于 2026-10-12 04:00 UTC 被真实时钟越过后，`quote_time_order` 开始确定性失败（此前恰好掩盖）。已改用 `sys.modules[type(self).__module__]` 解析。属测试基建修复，非业务代码变更。

## 命令与退出码

```text
unittest discover -p test_pi_outbound_gate.py  # 5/5 OK
unittest discover -p test_radar_dispatch.py    # 9/9 OK
unittest discover -p test_g5a_drills.py        # 3/3 OK
unittest discover -p test_intake_confirmation.py  # 19/19 OK（修复后）
powershell scripts/run_tests_isolated.ps1      # 1580/1580, 323.919s, exit 0
```

## 边界

1. 出站门禁作用于 quote/pricing 两个采集器；match/screening 采集仅含 id/状态级信息（设计如此），后续如加入价格明细需同步门禁。
2. SQL 导出为 dry-run 文件；对真实 PostgreSQL 的装载/并发/崩溃演练（A111/A112）仍等实例。
3. 备份演练的"新交易后回退"是演示性复现（合成行），真实回退流程按 ADR-0002 须导出差异人工对账。

## 回退方案

pi_jobs 门禁为可选参数（缺省关闭、旧语义不变）；提醒与演练均为新增文件/路由；删除即回第四轮状态。

## 结论

计划内可隔离完成的工程项至此全部交付（G0-G5A 首批 + 前端整合 + 本轮三项补齐）。剩余全部为外部输入/环境/批准缺口（真实报价、账号授权、真实首单、PostgreSQL 实例、真实切换批准），逐包报告均已列明精确清单。
