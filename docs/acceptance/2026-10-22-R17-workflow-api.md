# R17 实施验收报告：版本化工作流 API

日期：2026-10-22。基线为 R16 提交 `2715101`。

**结论：R17 软件/集成层 PASS。验收 13/13 绿，全量离线 1756/1756 OK（13 skipped=PG），live 数据 247 文件不变。**

## R17

```yaml
task_id: R17
owned_files: [xianyu_agent/application/workflow_runs.py, xianyu_agent/api/v1/__init__.py, xianyu_agent/api/v1/workflows.py, contracts/workflow-api.v1.yaml, xianyu_agent/server.py(接线), tests/test_r17_workflow_api.py]
software_status: PASS   # 13/13
integration_status: PASS  # 全量 1756/1756 OK, 数据 247/247 不变
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [R18 落地 n8n W1/W2 前此 API 无外部调用方；run 存储为 JSON 单 writer（R10 PG outbox/workflow_runs 表就绪后切换）]
rollback: 停 v1 外部入口（configure 摘除），历史 run 可读
next_task: R18
```

## 交付（合同 11 §3 的 v1 子集 + 任务书验收项）

**能力注册表**（服务端版本化）：validate_evidence/match_products/calculate_economics v1（required）+ export_candidates v1（optional），`GET /api/v1/capabilities` 纯查询返回版本/schema digest/是否有成本/enabled，无预热。

**WorkflowRunService**（`application/workflow_runs.py`）+ **API 路由**（`api/v1/`，R03 认证层下）：

| 端点 | 语义 |
|---|---|
| POST `/api/v1/workflow-runs` | 幂等键 business_intent_id；输入冻结（版本+工件清单+请求摘要 digest）；202=已接受排队 |
| GET `/api/v1/workflow-runs/{id}` | 状态/步骤/阻塞/产物引用（账号作用域） |
| POST `.../steps` | 请求能力（workflow:trigger scope；能力版本入账；同能力单活步 409 live_step_exists） |
| POST `.../cancel` / `.../resume` | 显式命令（actor+expected_revision，版本冲突 409） |
| POST `.../finalize` | **服务端完成判定**：必需能力全部终态（succeeded 或合同允许的 skip+理由），manifest 服务端生成 |
| GET `.../events` | 追加式事件序列，cursor 续读 |
| GET `/api/v1/artifacts/{id}` | 产物（账号作用域；服务端写入，客户端不能自带 hash 跳过内容验证） |

**合同文件** `contracts/workflow-api.v1.yaml`：OpenAPI 3.0 + 返回码合同（401/403/409/422/503/202 语义）+ 能力合同版本 + 允许的 skip 理由白名单。

## 验收矩阵（tests/test_r17_workflow_api.py，13/13）

| 任务书验收项 | 测试 |
|---|---|
| 同键同 payload 返回同 run | `test_same_intent_same_payload_returns_same_run`（202+created true/false，同 run_id） |
| 不同 payload 409 | `test_same_intent_different_payload_is_409`（idempotency_conflict） |
| **客户端不能写 approved/succeeded** | 创建/步骤两处带 status 字段→422 client_status_forbidden；所有状态值由服务端迁移派生（finalize 只在必需能力终态后由服务端置 finalized） |
| 跨账户读产物拒绝 | run 读与 artifact 读异账号→403 account_forbidden；同账号通过 |
| finalize 前置 | 必需能力未终态→409 required_steps_incomplete；四步成功后 finalize 200+manifest |
| skip 合同 | 非白名单理由拒绝（operator_felt_like_it）；no_spread_available 允许 |
| 其他 | whole_run 外部拒绝（409 mode_not_authorized，python-owned）；重复活步 409；取消后步骤 409；事件 cursor 续读；能力注册表查询 |
