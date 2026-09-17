# 2026-09-18 最小贯通验收：报价 → 同款 → 成本 → 商机 → 决策卡

执行者：Zcode。基线 HEAD：`790dff8` → 本轮终点 `da6882f`（8 个独立提交）。
演示证据：[THIN_LOOP_DEMO_EVIDENCE_2026-09-18.json](THIN_LOOP_DEMO_EVIDENCE_2026-09-18.json)（合成数据，复算总计划 8.3 黄金样例）。

## 分包状态

| 工作包 | 状态 | 范围与证据 |
|---|---|---|
| WP00 基线刷新 | PASS | [BASELINE_2026-09-18.md](BASELINE_2026-09-18.md)：全量 1026/1026 实测通过，data/live 15 文件哈希不变，GH-04/05 调用路径逐行核验，skills 实测 9 个 |
| WP01 契约与仓储 | PASS（最小版） | `xianyu_agent/commerce/contracts.py`（10 个实体契约 + Money 三态 + 上海营业日）、`repositories.py`（协议 + JSON 实现：不可变快照、修订冲突、追加式决策）；ADR-0001；39 测试 |
| WP02 来源资格 | PASS（无外部凭证范围） | `providers/base.py`（适配器协议 + pdd/pinduoduo 等别名边界归一化）、`capabilities.py`（能力登记表；VERIFIED 强制 ≥20 真实样本+证据+费用依据，代码级拒绝无证据 VERIFIED）；种子如实记录 5 个来源：2 CONFIGURED / 3 UNVERIFIED，0 VERIFIED；11+1 测试 |
| WP03 供应商导入 | PASS（最小版） | `supplier_import.py`：preview→confirm 两步、CSV/XLSX（标准库解析）/粘贴文本、确定性列映射+置信度、整箱/单件换算、阶梯价、包邮/到付三态运费、负数拒绝、重复表头拒绝、同文件幂等、新版本追加不覆盖、批次撤销不删历史、半途失败留痕；XLSX 公式单元格无缓存值→待确认；20 测试 |
| WP05 同款匹配 | PASS（最小版，样本未达100） | `product_matching.py`：EXACT/COMPARABLE/NOT_MATCH/UNKNOWN 四级，硬约束（成色/品牌/型号/尺寸/材质/单位类）判级，标题相似度仅召回报告，mm/cm 归一，二手vs全新必拒；人工纠错追加新记录；28 对标注集（含硬负例与单位歧义）EXACT 精确 6/6，已知限制 1 项（层数未建模）如实记为错误；**未达 100 对保留集，不宣称 ≥95% 目标**；13 测试。RapidFuzz 未安装，召回用 stdlib difflib（可替换单点） |
| WP06 利润与商机 | PASS（最小版） | `unit_economics.py` + `opportunity_service.py`：三情景（仅用调用方显式输入，无区间输入时三情景相同并注明）、盈亏平衡/最低可售价、现金占用含 MOQ、缺成本→unknown 不填零、平台费仅凭当期条款参数计算并标记 estimated；商机状态机 discovered→researching→ready_for_test（确定性门禁：EXACT+保守利润>0+输入新鲜（报价24h/市场72h，与既有阈值同源））；NOT_MATCH 拒建；COMPARABLE 仅研究；决策卡记录证据引用/门禁/未知项/授权类型=human；黄金样例 104.03/34.79% 手算复核通过；19 测试 |
| WP09 面板最小版 | PASS（API+console 最小版；浏览器视觉 QA 未做） | server.py 增 13 条 `/commerce/*` 路由（与遗留 `/opportunities` 路由共存不冲突）；console 新增「商机雷达」五步面板（导入→观察→匹配→核算→决策卡+漏斗）；HTTP 全链路集成测试 6 条；Playwright 1440/390 视觉验收 NOT_RUN（属完整 WP09 范围） |

全量隔离回归（本轮终点实测）：**1122/1122 通过**（基线 1026 + 新增 96），`node --check` app.js / xianyu-ops.ts、`git diff --check` 通过，data/live 前后 15 文件 SHA-256 不变。

## 演示流程（合成数据，非真实经营）

```
1. 导入   CSV 报价（GZ-1200, 150元/件, 运费15, 全新, 众商）→ preview 1行可确认 → confirm → OfferSnapshot impb_*
2. 观察   人工市场观察 GZ-1200 @299 全新（明确标记 manual observation，非成交事实）
3. 匹配   EXACT（品牌/型号/尺寸/材质/成色/单位 全部已知且相等；recall 0.48 仅报告）
4. 核算   基准利润 104.03（黄金样例复算一致）；保守(售价270) 75.90；现金占用 (150+15)×MOQ2=330
5. 状态   ready_for_test（确定性门禁全过）
6. 决策卡 pricing_agent：建议+门禁+未知项+4条证据引用+授权=human（人工）
```

## 诚实声明（不声称的能力）

- 无真实供应商文件、无真实账号、无平台动作、无真实订单或利润；演示数字为合成。
- 三外部来源（PDD 多多进宝/1688/闲鱼浏览器采集）UNVERIFIED，无凭证不伪造接入。
- 匹配标注集 28 对 < 100 对保留集门槛；≥95% 精确率目标不宣称。
- WP09 完整版（SSE、分页、移动端/桌面 Playwright、断线恢复）与 WP04/07/08/10+ 未开始（NOT_RUN）。
- XLSX 依赖标准库解析（无 openpyxl），公式单元格无缓存值时停为待确认，不估值。

## 回退方式

- 新功能全部隔离在 `/commerce/*` 路由 + `xianyu_agent/commerce|supplier_import|product_matching|unit_economics|opportunity_service|providers` 模块；旧业务零依赖新表（`data/commerce/` 不存在时旧功能不受影响）。
- console 面板独立挂载，移除即回退；8 个提交按包独立可回滚。
