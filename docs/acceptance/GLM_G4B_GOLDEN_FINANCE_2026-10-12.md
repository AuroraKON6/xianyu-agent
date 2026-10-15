# GLM G4B 验收报告：黄金样本财务贯通

日期：2026-10-12。执行者：GLM。

## 包与范围

G4B 工程侧（A100、A101、A102 S/H 层）。A103（真实首单）与 A104（真实复盘）**B/R 层 WAITING_INPUT**。

## 状态总览

- 软件（S）：**PASS**（3 项专项 + 全量 1570/1570，exit 0）
- HTTP（H）：**PASS**（账本 summary 路由、订单详情投影）
- 真实首单（B）：**WAITING_INPUT**（真实买家、平台订单、付款/回款凭证）
- 商业执行（B）：**NOT_AUTHORIZED**

## 矩阵逐项

| ID | 结果 | 证据（tests/test_g4b_golden_finance.py，全走真实 HTTP/服务） |
|---|---|---|
| A100 | PASS (S/H) | 黄金样本：预计贡献利润 90（300-160-20-5-9-4-12，经商机 evaluate 路由）；结算链：回款 300（settled）+ 支出 198（settled）→ 净额 102；部分退款 50 → **52**；准备金 12 未被二次扣（无 reserve 分录）；summary 路由与订单详情投影（3 笔分录逐笔可追）一致 |
| A101 | PASS (S) | 合法部分冲正精确落地；重复全额冲正拒（exceeds original）；超额部分冲正拒；错币种拒；未知原分录拒；原分录永远不变；恰好归零后任何再冲正拒；**并发** 70+70 竞争恰好一笔胜出（净额 30 而非 -40），锁内累计校验成立 |
| A102 | PASS (S/H) | 无回款凭证 → provisional（120），settled=0；summary 注明 "provisional tier has unreconciled entries; not money in hand"——未回款不进在手现金、不进最终利润；HTTP 分层一致 |

## 命令与退出码

```text
unittest discover -p test_g4b_golden_finance.py  # 3/3 OK
powershell scripts/run_tests_isolated.ps1        # 1570/1570, 238.610s, exit 0
```

live 20 文件前后一致；零真实模型/平台调用。

## 已知边界

1. A103 真实首单链（平台订单 → 采购 → 付款 → 物流 → 回款）需要真实买家与凭证；order detail 投影与账本已就绪，等输入。
2. 平台签收但未回款/退款在途的状态机细节（A102 的 U 层视图）归前端整合轮。
3. 税费仍保持 unknown（等待用户确认适用口径）。

## 下一工作包

G5A：ADR-0002 已交付（见 [G5A 报告](GLM_G5A_ADR_AND_AUDIT_2026-10-12.md)）；真实 PostgreSQL 演练 WAITING_INPUT 环境。
