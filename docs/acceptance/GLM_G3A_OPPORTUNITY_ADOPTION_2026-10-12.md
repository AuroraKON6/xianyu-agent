# GLM G3A 验收报告：商机采纳桥接

日期：2026-10-12。执行者：GLM。

## 包与范围

G3A（矩阵 A70-A74，S/H 层）。前置：G1/G2 全部。

## 状态总览

- 软件（S）：**PASS**（7 项专项 + 全量 1559/1559，exit 0）
- HTTP（H）：**PASS**（adopt/adoptions/recheck 路由真实挂载，经 server 导入回归）
- 浏览器（U）：NOT_RUN（采纳按钮归控制台前端整合轮；API 已就绪）
- 真实资料与来源（R）：**WAITING_INPUT**（真实采纳决定需真实商机与操作者）
- 商业执行（B）：**NOT_AUTHORIZED**

## 交付

| 文件 | 内容 |
|---|---|
| `xianyu_agent/opportunity_adoption.py`（新） | `OpportunityAdoptionService`：显式采纳（记录操作者决定、理由、幂等键、商机 revision 与输入指纹=匹配/双快照/条款版本/核算引用）；同(商机,候选,账号)作用域去重；账号隔离（各账号独立记录，不共用授权）；采纳前置校验（商机当前有效 + 材料 local_analysis 许可——材料确认不冒充商品批准）；`recheck` 对比实时输入指纹→输入变化/材料失效/商机失效即标 `stale` 并给出精确原因，旧记录与候选历史不重写（新评估需新采纳） |
| `xianyu_agent/server.py` | `POST /commerce/opportunities/{id}/adopt`、`GET .../adoptions`、`POST /commerce/adoptions/{id}/recheck`；服务与 intake 确认服务共享实例 |

## 矩阵逐项

| ID | 结果 | 证据（tests/test_opportunity_adoption.py，供应商快照走真实导入、材料确认走真实 preview/confirm） |
|---|---|---|
| A70 | PASS (S) | `test_a70_adoption_maps_and_records_input_version`：映射与输入版本落档；五类证据不自动通过（候选 supplier_verified=False、evidence_revision=1、无人工决策、无新证据 artifact） |
| A71 | PASS (S) | 同作用域重复采纳返回同一记录；异账号独立记录；同幂等键异载荷 409 |
| A72 | PASS (S) | 供应商涨价（新快照）→ recheck 标 stale（newer supplier snapshot/inputs changed），候选历史不动；材料确认撤销 → stale（material confirmation lapsed） |
| A73 | PASS (S) | 采纳后候选仍 evaluating、无人工决策、无草稿；activation/Day-0 旧门禁不在采纳路径上（资料确认≠商品批准，Day-0≠发布） |
| A74 | PASS (S) | 决策记录 authorization_type 无 auto；模型/确定性建议不改变候选状态 |

A70/A73 的 U 层（控制台采纳按钮与状态分层显示）与 A71 的 P 层（进程级并发）留待前端整合轮与 G5A（当前单写进程内 LockedJsonList 串行化已覆盖线程级）。

## 命令与退出码

```text
unittest discover -p test_opportunity_adoption.py  # 7/7 OK
powershell scripts/run_tests_isolated.ps1         # 1559/1559, 304.998s, exit 0
```

live 20 文件前后一致；零真实模型/平台调用。

## 已知边界

1. adopt 路由的 operator 为调用方声明字段（本地单用户控制台语义）；需要强身份时复用 G1A 操作者凭证通道（材料确认已用）。
2. 采纳记录的 `superseded_by` 保留为空：语义为显式 stale + 重新采纳新记录。
3. 商机→候选五类证据的**半自动映射**（如从已归档原件预填报价证据引用）未做——计划明确"导入一个文件不等于五类证据全部通过"，留人工上传为主。

## 回退方案

删除三个路由与 opportunity_adoption.py/测试即回 G2C 状态；新数据文件 `commerce/opportunity_adoptions.json` 不影响旧读取方。

## 下一工作包

G3B 受控真实试卖（真实账号/发布链全部 WAITING_INPUT/NOT_AUTHORIZED，软件侧已由旧链承载）→ G4A 采购库存账本接入主链。
