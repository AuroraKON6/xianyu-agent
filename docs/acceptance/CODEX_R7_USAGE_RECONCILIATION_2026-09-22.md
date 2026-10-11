# R7 人工消费对账：实现与本地验收

日期：2026-09-22。项目：`E:\闲鱼\xianyu`。

## 交付

人工消费对账API已经实现，默认关闭。承接原有未提交R6改动，本轮没有提交、暂存、修改实际`.env`或生成真实凭证，没有调用真实模型、处理真实消费或操作平台。

新增 `xianyu_agent/pi_usage_reconciliation.py`，在 `xianyu_agent/server.py` 挂载三个接口；新增17项隔离测试。`.env.example`仅增加空配置；`requirements.txt`明确Pydantic v2要求，未安装或升级环境依赖。使用说明：`docs/PI_USAGE_RECONCILIATION.md`。

## 业务与权限顺序

1. 原服务没有通用操作者认证，新接口不能把“没有job token”视为operator。采用独立Bearer凭证，服务端仅配置SHA-256与操作者标识；未配置503、无凭证401、job会话403。
2. 审计身份来自服务端配置，不接受请求体operator。真实令牌不入账本、日志或Pi环境；测试只用无真实权限的固定样例。
3. 查询unknown→提交绑定job/attempt/tokens的证据摘要与原因→只读预览→携带状态摘要、提案摘要与confirmed=true明确确认。
4. 账本、job或提案改变则旧确认失效。runner执行中或job仍leased/running时拒绝对账。不会自动取消任务。
5. 只允许pending/unknown进入settled/released。release要求未出站证据及0 tokens，且不能与已有模型信息、结果引用或决策产物矛盾。已结算消费不能通过此接口降低。
6. 原状态、操作者、时间、证据及其摘要、请求摘要、幂等键和receipt，与消费更新在同一账本条目、同一次原子文件替换中持久化。
7. 相同请求重放返回原receipt；同键不同内容/身份409。落盘失败未知负债仍在；文件已替换但响应丢失时重新加载并识别原receipt。
8. 其他unknown、原业务日预算、job状态与决策有效性不被放宽；不自动调用模型或解锁平台操作。

## 验证

先添加测试，首次运行因新模块尚未实现而ImportError，exit 1。这不是声称17个业务断言都在旧代码下执行失败。

实现后17项针对性测试三连通过：3.775s、3.698s、3.667s，均exit 0。覆盖认证、身份伪造、缺证据/确认、预览只读、状态变化、幂等重启、持久化失败、回执丢失、并发争抢、预算继续生效、悬空决策拒绝release及真实应用路由挂载。应用挂载测试使用独立进程/临时数据根，提供商工厂禁止调用。

第一次完整隔离回归：

```text
Ran 1403 tests in 46.558s
OK
LIVE_FILES=20 LIVE_UNCHANGED=True TEST_EXIT=0
```

1403为当时工作区实际测试数，本批新增17项；其他R6测试变化不计作本批新增。该结果不是当前全仓库最终通过声明。

随后两次源码稳定性复验未通过，保留原始失败，不覆盖成全绿：

| 复验 | 实际结果 | 源码与数据校验 |
| --- | --- | --- |
| 第2轮 | 1406项，52.568s，errors=1，测试exit 1 | SOURCE_STABLE=False；真实数据未变；封装exit 203 |
| 第3轮 | 1407项，50.674s，errors=1，测试exit 1 | SOURCE_STABLE=False；真实数据未变；封装exit 203 |

第2轮失败为 `test_candidate_activation.CandidateActivationRouteTests.test_raw_upload_binds_canonical_ref_and_invalidates_old_decision` 的fixture文本被凭证/个人标识过滤拒绝；本轮未修改该模块，未断言它与并行写入有因果关系。

第3轮测试发现 `tests/test_pi_validation.py` 时，所需 `xianyu_agent.pi_validation` 尚不存在；测试结束后的源码清单显示该模块刚被新增。这证明期间基线变化，不能用混合时点的测试宣称最终验收。原始输出留在 `C:\Users\轻音\AppData\Local\Temp\xianyu-r7-validation-3ada3117696745f9b3101a6653993e4a.log`。

最终单独核验本批实现、应用挂载和测试文件的前后SHA-256：

```text
Ran 17 tests in 6.078s
OK
RECONCILIATION_SOURCE_STABLE=True TEST_EXIT=0
```

因此：**R7本模块实现及隔离测试通过；整个持续变更中的工作区最终全量验收暂未闭环。** 不覆盖其他并行修改、不修补无关模块来制造绿色结果。待这些修改稳定后，须再跑完整隔离回归和源码/真实数据双清单校验。

`data/live`比较覆盖20个文件的路径集合与内容SHA-256。既有`test_wp08_script.py` ResourceWarning未被修饰为无警告；本轮未改无关测试。`git diff --check`通过。

## 边界与下一步

- 证据为认证操作者核对后的摘要，标记 `operator-attested; not provider-verified`。哈希不证明外部报告真实，原件需操作者留存；API不下载URL或读取任意文件。
- 仍是单进程JSON存储和单操作者凭证，不是跨进程数据库事务、不可篡改审计、SSO/MFA或生产高可用。
- 本批没有浏览器对账表单、供应商自动核验或自动释放unknown策略，默认配置仍关闭。
- 下一阶段可在本API上增加隔离人工面板：展示unknown、脱敏证据、前后差异、显式确认与回执，禁止浏览器持久化凭证。真实Pi与真实商业执行仍单独审批。
