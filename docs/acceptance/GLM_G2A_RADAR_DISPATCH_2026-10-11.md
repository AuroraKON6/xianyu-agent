# GLM G2A 验收报告：持久增量流程（radar dispatcher）

日期：2026-10-11。执行者：GLM。

## 包与范围

G2A 首批（矩阵 A40-A44、A47 + Pi 用途门禁接入）。前置：G1 全三包。

## 状态总览

- 软件（S）：**PASS**（8 项专项 + 全量 1536/1536，exit 0）
- HTTP 集成（H）：**PASS**（路由挂载经 test_commerce_api/server 导入回归；radar 路由为薄读/tick 操作）
- 真实资料与来源（R）：**WAITING_INPUT**（真实批次确认/真实观察触发的事件流待真实输入）
- 商业执行（B）：**NOT_AUTHORIZED**

## 交付

| 文件 | 内容 |
|---|---|
| `xianyu_agent/radar_dispatch.py`（新） | `RadarEventStore`（事件幂等：key=项目+范围+SKU+种类+输入摘要；重复事件返回同记录）；`RadarDispatcher`（阶段推进：match → opportunity/economics 卡 → 按用途许可决定是否提交 Pi 复核；失败/缺输入转 awaiting_input 并记录 next_check_at，不紧循环；stop 边界后不新认领；单实例防重入）；`RadarExecutor`（确定性执行，roles 过滤认领，无任何模型调用）；`build_radar` 复合根（显式共享进程级 AgentJobStore，防双实例漂移） |
| `xianyu_agent/agent_jobs.py` | `claim_next` 新增可选 `roles` 过滤（缺省行为不变，A42 的存储级修复） |
| `xianyu_agent/pi_jobs.py` | Pi 认领限定自身四角色：radar/写任务不再可能被 Pi worker 抢走后按"unsupported role"处死 |
| `xianyu_agent/intake_confirmation.py` | `confirm` 支持只读 `on_confirmed` 回调（观察者异常不回滚确认）；router 支持共享 service 实例 |
| `xianyu_agent/server.py` | 共享 `intake_confirmation_service`（server 级单例，radar 的 eligibility 查询与路由写路径同源）；radar 装配（eligibility=external_model 用途、Pi 提交带幂等键）；确认成功→按快照记事件；市场观察创建→记事件；`RADAR_DISPATCHER_ENABLED`（默认 0）opt-in 后台 tick（同一写进程生命周期，shutdown 停止）；路由 `GET /commerce/radar/status`、`POST /commerce/radar/tick`、`POST /commerce/radar/events` |
| `.env.example` | RADAR_DISPATCHER_ENABLED / INTERVAL_SECONDS |
| `tests/test_radar_dispatch.py`（新） | 8 项专项 |

## 矩阵逐项

| ID | 结果 | 证据 |
|---|---|---|
| A40 | PASS (S) | `test_a40_event_advances_to_opportunity_card`：事件→match 阶段（持久 result_ref）→opportunity 卡（researching，economics 未知成本如实）；缺市场观察→事件 awaiting_input + 精确 note |
| A41 | PASS (S) | 同输入重复事件=同一记录；全流程恰好 1 match+1 opportunity 任务、1 商机（自然键）；新输入版本→新事件新评估，历史保留 |
| A42 | PASS (S) | `test_a42_role_filtered_claims`：radar worker 认领不到 Pi 分析任务与平台写任务；Pi job 保持 queued；旧语义（无过滤）仍可认领（兼容） |
| A43 | PASS (S) | start 二次调用返回 False（单实例）；stop 后线程收口、手动 tick 仍可用 |
| A44 | PASS (S) | `test_a44_resume_from_persisted_state_after_restart`：磁盘重建全部 store 后阶段从持久状态续进至 processed |
| A47 | PASS (S) | match 任务 input_refs 精确等于事件的供应商+市场快照；不送全库 |
| Pi 门禁 | PASS (S) | eligibility(external_model) 不通过 → 事件 note"external-model use not permitted"、零提交；通过 → 恰好一次带幂等键的 Pi 提交 |

A45/A46/A48 未在本批展开：模型失败/预算路径沿用 PiTaskRunner 既有 per-attempt 记账与预算拦截（R4-R7 已验收），来源配额与提醒去重依赖真实合同参数，列入 G2B/G2C 前置输入。

## 命令与退出码

```text
unittest discover -p test_radar_dispatch.py      # 8/8 OK
unittest discover -p test_pi_jobs.py             # OK（认领过滤回归）
unittest discover -p test_agent_jobs.py          # OK
unittest discover -p test_intake_confirmation.py # 19/19 OK
unittest discover -p test_commerce_api.py        # 13/13 OK
powershell scripts/run_tests_isolated.ps1        # 1536/1536, 345.997s, exit 0
```

live 20 文件前后哈希一致；零真实模型调用（radar 为确定性执行）、零平台读写、未新增自启动（RADAR_DISPATCHER_ENABLED 默认关闭）。

## 已知边界

1. 后台 tick 线程为 opt-in；默认全部手动 tick（计划"用户显式启用的定时计划"）。
2. 到期检查类事件（expiry_check）与费用/规则变化触发仅有 record_event API 入口，尚无自动到期扫描（G2C 前补）。
3. Pi 提交的输入收集沿 PiTaskRunner 现有 collector（全库 committed 报价+可用计数）；A47 的逐对象输入裁剪在 Pi 侧的精细化归 G2C。

## 需要用户提供的精确输入

1. 真实供应商报价确认（G1A WAITING_INPUT 项）→ 触发真实 intake_confirmed 事件。
2. 若要后台自动 tick：显式设置 `RADAR_DISPATCHER_ENABLED=1`。
3. 外部 Pi 分析预算与用途许可（已在 eligibility 链路就位，待真实材料）。

## 回退方案

RADAR_DISPATCHER_ENABLED 保持 0 时无任何行为变化；删除 server 装配块与 radar_dispatch.py/测试即回 G1C 状态；agent_jobs/pi_jobs 的 roles 过滤为可选参数，缺省语义不变。

## 下一工作包

G2B 单一市场观察通道（先只读探针；真实通道 R 层待账号/权限），随后 G2C 首张真实商机卡。
