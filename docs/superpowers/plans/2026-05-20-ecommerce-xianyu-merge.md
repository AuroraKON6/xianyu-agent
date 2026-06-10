# 电商主项目 × 闲鱼项目 合并评估与下一步计划

> 日期: 2026-05-20
> 方法: OPC 12 维度战略框架（作为阶段性决策工具）
> 约束: MVP V1.0 冻结不破坏; 不恢复 Ollama; 不引入高风险自动行为; 最小安全合并

---

## Part 1: 双项目状态对比

### 1.1 基本信息

| 维度 | ecommerce-openclaw-system | xianyu |
|------|--------------------------|--------|
| 定位 | 电商经营主后端（选品→验证→扩品→财务） | 闲鱼渠道适配器（上架→IM→订单→转售） |
| 端口 | 8000 (FastAPI) + 8501 (Streamlit) | 8788 (FastAPI) |
| 数据库 | SQLite 30+ 表, `data/openclaw.db` | SQLite, `data/xianyu.db` |
| API 数量 | 160+ | ~50 |
| 前端 | Streamlit 24 tab | Web console `/console` |
| 飞书 | 70+ 命令, WebSocket 长连接 | CLI 长连接, `/feishu/events` |
| 测试 | 39 全通过 (冻结报告 117) | 546 全通过 |
| 冻结状态 | MVP V1.0 冻结 2026-05-19 | Phase 8 进行中 |
| LLM | MiMO V2.5 Pro 主力, DeepSeek 备用 | DeepSeek V4 Flash (客服), MiMo (专家) |

### 1.2 Agent 对照

| 能力域 | ecommerce Agent | xianyu Agent | 重叠? |
|--------|----------------|-------------|-------|
| 选品 | SelectorAgent | discovery | **互补**: ecommerce 做 1688 选品验证, xianyu 做闲鱼市场发现 |
| 供应商比价 | SourceCompareAgent | resale_research | **互补**: ecommerce 做 1688 供应商对比, xianyu 做转售可行性调研 |
| 定价 | PricingAgent (规则) | resale_pricing + listing | **互补**: ecommerce 算成本利润, xianyu 算闲鱼竞品价 |
| 风控 | (内嵌于各 Agent) | resale_risk | **互补**: xianyu 有独立风险评审 Agent |
| 客服 | CustomerAgent (MiMO) | customer_service (DeepSeek) | **互补**: ecommerce 处理售后, xianyu 处理买家 IM |
| 订单 | OrderAgent | order | **互补**: ecommerce 监控异常, xianyu 处理闲鱼订单 |
| 数据分析 | DataAnalysisAgent | analytics | **互补**: ecommerce 做全链路财务, xianyu 做闲鱼维度分析 |
| GitHub | - | github | **xianyu 独有**: 项目发现和转售评估 |
| 爬虫 | BrowserOperator + CrawlerSupervisor | (Playwright discovery) | **互补**: ecommerce 受监管爬虫, xianyu 轻量抓取 |

### 1.3 能力互补矩阵

| 能力 | ecommerce 有 / xianyu 无 | xianyu 有 / ecommerce 无 | 两者都有 |
|------|-------------------------|-------------------------|---------|
| 选品验证流程 | 3-5候选/天, SOP模板, 样品验证 | - | - |
| SCALE/KILL/WATCH 分级 | 分级决策 + 止损规则 | - | - |
| 扩品监控 | 扩品计划 + 监控 + 止损 | - | - |
| 财务台账 | 全链路财务 (transactions表) | - | - |
| 每日简报 | daily_briefs 自动生成 | - | - |
| 手动执行跟踪 | manual_execution_logs + SOP | - | - |
| 闲鱼 IM | - | WebSocket 实时聊天 | - |
| 数字商品自动发货 | - | auto-fulfillment | - |
| 每日擦亮 | - | polish at 09:30 | - |
| GitHub 雷达 | - | github discovery | - |
| 转售评审 (3-agent) | - | resale_research/risk/pricing | - |
| 飞书 CLI 长连接 | - | 已验证 | - |
| 工单/审批/执行队列 | - | WorkOrder/Approval/ExecutionJobQueue | - |
| OpenClaw 技能 | bridge script 已验证 | xianyu-commerce-agent SKILL.md | - |
| 飞书集成 | 70+ 命令 (新 bot 未入群) | CLI 长连接 (已通) | - |
| 浏览器适配器 | CrawlerSupervisor (白名单) | stub/xianguanjia/browser | - |

### 1.4 文档状态

| 文档 | 状态 | 备注 |
|------|------|------|
| ecommerce CURRENT_IMPLEMENTATION_STATUS.md | **准确** | 冻结于 2026-05-19, 反映 V1.0 实况 |
| ecommerce ONE_PERSON_ECOMMERCE_AGENT_PLAN.md | **基本准确** | 8 agent 描述正确, OPC 定义正确 |
| ecommerce OPC_FEASIBILITY_EVALUATION.md | **已修正** | 原始 7.3 分, 修正后 6.8 分 |
| ecommerce OPC_REVIEW_ADDENDUM.md | **准确** | 明确"选品验证 MVP 可行"而非"自动经营可行" |
| ecommerce FEISHU_OPENCLAW_BRIDGE.md | **部分过时** | 新 bot 未入群, 实际群消息收发未验证 |
| xianyu PROJECT_PLAN.md | **基本准确** | Phase 8 进行中, Phase 9-12 待做 |
| xianyu deepseek-feishu-cli-migration.md | **准确** | Phase 8 实施计划, checkbox 待完成 |
| xianyu openclaw_feishu_workflow.md | **准确** | 运维工作流文档 |
| xianyu SKILL.md | **准确** | OpenClaw 技能定义完整 |

### 1.5 OPC 12 维度评分对比

| 维度 | ecommerce (修正后) | xianyu (评估) | 合并后预期 |
|------|-------------------|-------------|-----------|
| 战略编排 | 8 | 7 | 8 |
| 资源审计 | 7 | 6 | 7 |
| 利基定位 | 8 | 7 | 8 |
| 价值主张 | 6 | 6 | 7 |
| 商业模式 | 7 | 7 | 8 |
| MVP 设计 | 8 | 8 | 8 |
| 转化飞轮 | 5 | 5 | 6 |
| 资产运维 | 7 | 7 | 7 |
| Dashboard | 8 | 6 | 8 |
| 竞争策略 | 6 | 6 | 7 |
| 风控基建 | 8 | 8 | 9 |
| 核心原则 | 9 | 9 | 9 |
| **综合** | **6.8** | **6.8** | **7.6** |

合并后综合分 7.6 的来源: 两个项目互补填平了各自的短板（ecommerce 的渠道能力 + xianyu 的选品验证流程），且风控基建因双层审核而增强。

---

## Part 2: 合并架构推荐

### 2.1 总体策略: 桥接 + 状态同步, 非代码迁移

**核心判断**: 两个项目定位不同、数据库独立、Agent 体系互补。强行合并代码库会破坏 ecommerce V1.0 冻结，且引入大量回归风险。推荐 **桥接模式**:

```
用户入口
  ├── OpenClaw (统一入口)
  │     ├── ecommerce-agent-ops 技能 → ecommerce 后端 :8000
  │     └── xianyu-commerce-agent 技能 → xianyu 后端 :8788
  ├── 飞书 CLI 长连接 → xianyu :8788/feishu/events
  └── Streamlit → ecommerce :8501
```

### 2.2 主后端 vs 渠道适配器

| 角色 | 项目 | 理由 |
|------|------|------|
| **主后端** | ecommerce-openclaw-system | 选品→验证→扩品→财务全链路, 30+ 表, 160+ API, 冻结稳定 |
| **渠道适配器** | xianyu | 闲鱼上架/IM/订单/发货, WorkOrder 审批闭环, 546 测试 |

### 2.3 数据同步: 桥接 API, 非共享数据库

两个项目保持独立 SQLite 数据库，通过 HTTP API 同步关键状态:

```
xianyu :8788                    ecommerce :8000
  ┌─────────────┐    HTTP     ┌──────────────────┐
  │ resale_     │ ──────────→ │ /api/sync/       │
  │ candidates  │             │ resale_candidate  │
  │ (status,    │ ←────────── │ (status, score,   │
  │  score,     │    回写      │  decision)        │
  │  decision)  │             │                   │
  └─────────────┘             └──────────────────┘
```

同步方向:
- **xianyu → ecommerce**: 转售候选 (title, price, margin, agent_review_score, recommendation)
- **ecommerce → xianyu**: 决策结果 (approve/reject/watch + note)
- **不自动同步**: 订单、付款、发货（需人工触发）

### 2.4 Agent 连接方式

xianyu 的 3 个 resale agent 通过桥接脚本调用 ecommerce 的选品验证流程:

```
xianyu resale_research → 调用 ecommerce /api/candidates (创建候选)
xianyu resale_risk    → 调用 ecommerce /api/verification (风险验证)
xianyu resale_pricing → 调用 ecommerce /api/pricing (定价建议)
```

反过来，ecommerce 的 SelectorAgent 发现闲鱼渠道机会时，可通过桥接通知 xianyu 的 discovery agent。

### 2.5 读写边界 (安全红线)

| 操作 | 权限 | 说明 |
|------|------|------|
| 查询候选列表 | **自动** | 只读, 无需审批 |
| 创建转售候选 | **自动** | 写入候选表, 状态=evaluating |
| Agent 评审打分 | **自动** | 确定性计算 + LLM 摘要, 不执行操作 |
| 发送飞书通知 | **自动** | 只读通知, 不含敏感信息 |
| 闲鱼上架草稿 | **需审批** | WorkOrder → Approval → ActionExecutor |
| 发送买家消息 | **需审批** | WorkOrder → Approval → ActionExecutor |
| 闲鱼擦亮 | **自动** | 每日定时, 低风险 |
| 供应商下单 | **禁止** | 不实现 |
| 自动付款 | **禁止** | 不实现 |
| 自动退款 | **禁止** | 不实现 |
| 绕过验证码 | **禁止** | 遇到验证码暂停, 通知人工 |
| 无审批发布商品 | **禁止** | 必须走 WorkOrder 审批 |

### 2.6 飞书/OpenClaw 入口统一

当前状态:
- ecommerce: 70+ 飞书命令 (新 bot 未入群)
- xianyu: 飞书 CLI 长连接 (已通)

推荐: **xianyu 的飞书 CLI 作为主入口**, 因为已验证可用。ecommerce 的命令通过桥接触发:

```
飞书消息 → xianyu /feishu/events → 解析命令
  ├── xianyu 命令 → 直接处理
  └── ecommerce 命令 → HTTP 调用 ecommerce :8000 API
```

OpenClaw 侧保持两个独立技能，用户可从 OpenClaw 入口任选。

---

## Part 3: 下一步实施计划

### Phase A: 桥接基础 (1-2 天)

**目标**: 两个项目能通过 HTTP API 互相发现和同步状态。

#### A1. ecommerce 侧: 新增同步 API (不破坏 V1.0 冻结)

在 ecommerce 项目的 `api.py` 中新增 3 个只读 + 1 个写入端点:

```
GET  /api/sync/resale_candidates          → 返回候选列表 (只读)
GET  /api/sync/resale_candidates/{id}     → 返回单个候选 (只读)
POST /api/sync/resale_candidates          → 创建/更新候选 (写入)
POST /api/sync/resale_candidates/{id}/decision → 写入决策结果
```

- 这些端点不触碰现有 160+ API 的任何逻辑
- 使用独立的 `sync_` 前缀避免命名冲突
- 新增 1 个 SQLite 表 `resale_candidates` (如果 ecommerce 数据库还没有)
- 测试: 4 个新测试, 不影响现有 39 个

#### A2. xianyu 侧: 新增桥接客户端

在 xianyu 项目中新增 `xianyu_agent/bridge.py`:

```python
class EcommerceBridge:
    """HTTP client to sync with ecommerce-openclaw-system backend."""
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url

    def push_candidate(self, candidate: dict) -> dict: ...
    def pull_decision(self, candidate_id: str) -> dict: ...
    def sync_review_result(self, candidate_id: str, review: dict) -> dict: ...
```

- 使用 `httpx.AsyncClient` (已有依赖)
- 超时 10s, 失败重试 1 次, 不阻塞主流程
- 配置项: `ECOMMERCE_BRIDGE_URL` 环境变量, 空则禁用
- 测试: mock HTTP, 不调真实 ecommerce

#### A3. 配置同步

在 xianyu 的 `Settings` 中新增:
```python
ecommerce_bridge_url: str = ""  # 空=禁用桥接
ecommerce_bridge_enabled: bool = False
ecommerce_bridge_sync_interval: int = 300  # 秒
```

### Phase B: Dashboard 集成 (2-3 天)

**目标**: 在 xianyu 的 web console 中展示 ecommerce 的关键数据。

#### B1. 新增 Dashboard tab

在 `web/app.js` 中新增 "经营总览" tab:
- 从 ecommerce `/api/dashboard` 拉取: 今日候选数、活跃商品、待处理订单、财务摘要
- 从 xianyu 本地拉取: 今日 IM 数、待审批工单、擦亮状态
- 合并展示在一个页面

#### B2. 飞书通知集成

xianyu 的飞书 CLI 可以转发 ecommerce 的每日简报:
- ecommerce 生成 daily_brief → 通过桥接推送到 xianyu → xianyu 转发到飞书群
- 频率: 每日一次, 在 xianyu 的 `daily_summary` 定时任务中

### Phase C: 转售评审打通 (2-3 天)

**目标**: xianyu 的 3-agent 转售评审结果能写入 ecommerce 的候选表。

#### C1. 评审结果同步

当 xianyu 的 `resale-agent-review` 完成后:
1. 将 `agent_review_score`, `agent_review_recommendation`, `blocking_flags` 通过桥接写入 ecommerce
2. ecommerce 侧的 Dashboard 展示评审状态
3. 决策仍由人工在 ecommerce 或飞书中做出

#### C2. 决策回写

当 ecommerce 侧对候选做出 approve/reject/watch 决策后:
1. 通过桥接回写到 xianyu 的 `resale_candidates.json`
2. xianyu 的 console 展示最新决策状态
3. 飞书通知决策结果

### Phase D: 工单流程对齐 (3-5 天, 可选)

**目标**: 统一两个项目的审批流程。

#### D1. 评估是否将 xianyu 的 WorkOrder 体系暴露给 ecommerce

- 当前 ecommerce 使用 `manual_execution_logs` 做手动执行跟踪
- xianyu 使用 `WorkOrder/Approval/ExecutionJobQueue` 做完整审批
- 选项: 让 ecommerce 的手动执行也走 xianyu 的审批队列

#### D2. 飞书审批统一

- 当前两个项目各自有飞书审批入口
- 统一为一个飞书审批流, 根据操作类型路由到对应后端

### Phase E: 飞书 bot 统一 (1 天)

**目标**: 只用一个飞书 bot 入口。

#### E1. 切换 ecommerce 到 xianyu 的飞书 CLI

- xianyu 的飞书 CLI 长连接已验证
- ecommerce 的 70+ 命令通过 xianyu 的 `/feishu/events` 路由
- 需要: 在 xianyu 的飞书处理逻辑中新增命令解析, 转发到 ecommerce API

#### E2. 旧 bot 处理

- ecommerce 的新 bot (cli_aa80522c0cb8dbed) 尚未入群
- 建议: 暂不入群, 等统一方案验证后再决定

---

## 实施优先级

| 优先级 | Phase | 工作量 | 价值 | 风险 |
|--------|-------|--------|------|------|
| P0 | A: 桥接基础 | 1-2 天 | 打通数据流 | 低: 不破坏现有功能 |
| P1 | B: Dashboard 集成 | 2-3 天 | 一站式查看 | 低: 只读展示 |
| P1 | C: 转售评审打通 | 2-3 天 | 评审→决策闭环 | 低: 写入用新端点 |
| P2 | D: 工单流程对齐 | 3-5 天 | 统一审批 | 中: 需评估 WorkOrder 兼容性 |
| P2 | E: 飞书 bot 统一 | 1 天 | 减少入口混乱 | 中: 命令路由需测试 |

**建议先做 A + B + C (约 1 周)**，验证桥接可行后再决定是否做 D + E。

---

## 关键约束 (不变)

1. **不恢复 Ollama** — 所有 LLM 走 DeepSeek V4 / MiMo V2.5 Pro 云端
2. **不绕过验证码/风控** — 遇到暂停, 通知人工
3. **不自动付款/退款/下单** — 需人工审批
4. **不自动发布闲鱼商品** — 必须走 WorkOrder → Approval → ActionExecutor
5. **Agent 不能直接执行高风险操作** — `publish_listing`, `prepare_purchase_draft`, `place_supplier_order` 等需审批
6. **API Key 只从环境变量读取** — 不写入源码/测试/文档/前端
7. **ecommerce V1.0 冻结** — 新增端点用 `sync_` 前缀, 不修改现有逻辑
8. **OPC 是阶段性决策工具** — 不是 runtime agent, 只在关键节点调用

---

## OPC 战略判断

调用 OPC 12 维度评估后的结论:

1. **战略编排**: 合并方向正确, 两个项目互补而非竞争
2. **资源审计**: 一人公司资源有限, 桥接模式比代码迁移节省 80% 工作量
3. **利基定位**: ecommerce 做"选品验证引擎", xianyu 做"闲鱼渠道执行器"
4. **价值主张**: 合并后 = "从发现到验证到上架的全链路, 人工审批保安全"
5. **商业模式**: 不变, 轻库存/代发, 低风险
6. **MVP 设计**: 桥接 MVP 只需 A1+A2+A3, 约 2 天可验证
7. **转化飞轮**: 飞书统一入口后, 一个 bot 管两个后端, 降低操作摩擦
8. **资产运维**: 两个独立数据库各自备份, 桥接层无状态
9. **Dashboard**: 合并展示是最高 ROI 的可视化改进
10. **竞争策略**: 闲鱼渠道 + 1688 选品 = 差异化
11. **风控基建**: 双层审核 (xianyu resale_risk + ecommerce 验证) = 更强
12. **核心原则**: 不破坏冻结, 不引入高风险, 最小安全合并

**最终判断: 合并可行, 推荐桥接模式, 先做 A+B+C (1 周), 验证后决定后续。**
