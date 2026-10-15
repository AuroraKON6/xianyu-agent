# CODEX 检验交接：GLM 八轮交付的验收指南

日期：2026-10-12。检验对象：GLM 在 `E:\闲鱼\xianyu` 的八轮交付（全部未提交，工作区 ~59 项，HEAD `a3c2b19`）。本文件是唯一入口；逐项证据链接 + 精确复现命令 + 可对抗检验的不变量。

## 0. 环境与铁律（检验前置）

```text
Python 3.8.5（.venv）；Node 24.16；测试一律走隔离脚本（临时 DATA_DIR+stub adapter）：
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1
最后基线：1592/1592, exit 0（2026-10-12，源码未再变）。
live 校验：对 data/live 任何操作前后做 SHA-256 对比（本轮所有报告均附"live 前后一致"）。
```

**必须成立的铁律（建议逐条攻击）**：① 隔离测试不写 live；② 零真实模型调用（消费账本可查）；③ 零平台写操作（无发布/采购/付款/退款）；④ 无自启动/计划任务新增；⑤ demo/公开网页数据永不冒充真实确认（材料用途唯一阻断=`no_active_confirmation`）。

## 1. 交付索引（九份报告 + 两 ADR + 清单）

| 轮 | 报告 | 核心交付 |
|---|---|---|
| G0 | docs/acceptance/GLM_G0_BASELINE_2026-10-11.md | 基线 1489/1489、入口矩阵、所有权表 |
| G1A | GLM_G1A_INTAKE_CONFIRMATION_2026-10-11.md | intake_confirmation.py（A10-A19，19 专项） |
| G1B | GLM_G1B_COST_ELIGIBILITY_2026-10-11.md | 成本来源/失效联动（A20-A26，16 专项） |
| G1C | GLM_G1C_INTAKE_PANEL_2026-10-11.md（截图 g1c-ui-artifacts/） | 面板闭环（A30-A33，4 浏览器） |
| G2A | GLM_G2A_RADAR_DISPATCH_2026-10-11.md | radar（A40-A44/A47+Pi 门禁） |
| G2B | GLM_G2B_MARKET_CHANNEL_2026-10-12.md | 市场通道只读（A50-A55 S/H） |
| G2C | GLM_G2C_DECISION_CARD_2026-10-12.md | 决策卡 material_status |
| G3A | GLM_G3A_OPPORTUNITY_ADOPTION_2026-10-12.md | 采纳桥接（A70-A74） |
| G4A/B | GLM_G4A_MAIN_CHAIN_WIRING / GLM_G4B_GOLDEN_FINANCE_2026-10-12.md | 主链接入 + 黄金财务（A90-A102） |
| G5A | GLM_G5A_ADR_AND_AUDIT_2026-10-12.md + ADR-0002 | 迁移设计 + 核对器 |
| R5-R6 | GLM_R5_GATE_REMINDERS_DRILLS / GLM_R6_PROBE_CLIENT_AND_GAPS | Pi 出站门禁/提醒/A50 探针/缺口清单 |
| R7-R8 | GLM_R7_REAL_SOURCE_DEMO / GLM_R8_XIANYU_MCP_REAL_CHANNEL | 真实公开数据 21 观察 + **闲鱼 MCP 真实通道（A50/A51 R 层）** |

## 2. 分层复现命令（每层可独立攻击）

```text
# S 层（全部专项）
for p in test_intake_confirmation test_g1b_cost_eligibility test_intake_confirmation_ui \
         test_radar_dispatch test_market_channel test_market_probe_client test_g2c_decision_card \
         test_opportunity_adoption test_g4a_main_chain_wiring test_g4b_golden_finance \
         test_pi_outbound_gate test_g5a_drills test_commerce_audit test_source_quotas \
         test_commerce_integration_ui; do
  D=$(mktemp -d); DATA_DIR=$D/data DB_PATH=$D/t.db XIANYU_ADAPTER=stub \
    .venv/Scripts/python.exe -m unittest discover -s tests -p "$p.py"
done
#（浏览器两项需 Chromium；整合项起真实 server 子进程）

# H 层（真实路由）已含于上述 HTTP 测试类。

# R 层——真实闲鱼 MCP（A50/A51，用户已授权）：
.venv/Scripts/python.exe research/real-source-demo-2026-10-12/mcp_probe.py
#  首查常触 RGV587 限流（预期）；退避 60s 后 search_count/browse_feed/search_items 应成功
#  证据：同目录 mcp_retry_result.json（29.1万件 + 15 真实卡）

# 审计与演练
.venv/Scripts/python.exe scripts/audit_commerce_ledgers.py --data-dir data/live          # exit 0
.venv/Scripts/python.exe scripts/commerce_backup_drill.py --source data/live --work <空目录>  # 演示目录，勿指 live
.venv/Scripts/python.exe scripts/export_commerce_to_sql.py --data-dir <演示目录> --out x.sql

# 全量
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1      # 1592/1592
```

## 3. 建议的对抗检验点（按用户验收风格）

1. **伪造操作者**：对 `/commerce/supplier-imports/{id}/confirmation/preview` 发带伪造 operator 的体（应 422）；带 `X-Xianyu-Job-Token`（应 403）。
2. **同尺寸字节篡改**（A13）：改归档 blob 一个字节保持大小 → eligibility 必须 `original_file_modified`。
3. **阶梯价**（A22）：qty=1 不得套十件批发价；qty=10 才换 145。
4. **冲正竞争**（A101）：并发 70+70 只赢一笔（净 30 而非 -40）。
5. **出站门禁**（A45）：未确认 external_model 的真实报价 → fake-Pi 捕获载荷中不得出现价格串（test_pi_outbound_gate 已示范断言法）。
6. **claim 角色隔离**（A42）：radar worker 认领不到 Pi 任务，反之亦然。
7. **配额**（A46）：重试占两单位；重启（新实例同目录）不清零；未知限额拒绝无人值守。
8. **假价差**：research/real-source-demo 的 8 组公开配对中跨材质/跨规格必须 UNKNOWN，不得 EXACT。
9. **live 零污染**：任一轮复现前后 live 20 文件哈希不变。

## 4. 状态声明（禁止超读）

- PASS 仅指各报告所列 S/H/U/R 层级；**R 层新增仅 A50/A51/A53（真实匿名闲鱼读）**。
- WAITING_INPUT：真实材料确认（M1 唯一阻断）、账号条款、Pi 预算、真实首单（M3/M4）、PG 实例（A111-A114）、停写切换。
- NOT_AUTHORIZED：一切平台写、自动采购、OpenClaw、自启动。
- 已知工程尾巴（低优先）：A33 移动端截图、A115 日志/容量小工具。
- 测试基建教训：模块级 NOW 补丁必须经 `sys.modules[type(self).__module__]`（曾有双模块时间炸弹，已修）。

## 5. 一句话结论

85 项矩阵中所有可无外部输入完成的工程项 + 用户已授权的真实通道（闲鱼 MCP 匿名读）均已交付且证据可复现；其余为外部缺口（docs/ops/glm-gap-checklist-2026-10-12.md）。
