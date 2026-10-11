# R6 本地恢复实现与验收收尾

日期：2026-09-22。项目：`E:\闲鱼\xianyu`。

## 本次结果

**本批本地隔离恢复测试通过，生产/商业上线仍未授权。** 已在已有R6工作区改动上继续实现，不再只交方案。修改未提交，已有业务、测试和文档改动均保留；本轮没有暂存、提交、更新记忆、调用真实模型或操作真实平台。

本报告更新 `CODEX_RELIABILITY_R6_2026-09-22.md` 中前3项未完成状态：attempt结算幂等、证据前持久化usage、调用入口进程强杀恢复现在均有实现和测试。旧报告中的1360项结果仅是历史快照，不代表当前测试数。

## 实现

### 1. 按job与attempt结算

`xianyu_agent/pi_jobs.py` 的 `ModelUsageLedger`：

- begin_attempt拒绝重复job/attempt，且任何历史未知消费都阻断新调用，跨日不自动释放。
- record显式接收attempt，必须找到已持久化intent；不再凭空追加未关联的消费记录。
- 兼容省略attempt的旧调用，仅限该job恰有一个带attempt的记录；有歧义直接拒绝。
- 同一attempt、原业务日、同一usage与model重复结算返回原记录，不重复累计。
- 改金额、改model、改归属日、将unknown改成0等冲突保留原事实并拒绝。冲突参数追加至settlement_conflicts，同一冲突重放不重复追加。
- usage必须是非负整数或None；负数、bool、小数、数字字符串不能清除pending。
- 原始旧格式已知/未知条目可读，不自动迁移或删除；旧unknown继续阻断。

冲突审计写入失败仍会向上报错，不会覆盖原消费。这里没有提供人工释放或自动估算接口，未知事实不能因方便重试而被归零。

### 2. 模型结果先记usage，再处理业务产物

`PiTaskRunner` 在提取模型返回usage后立即按attempt结算，早于结果截止判断、租约复核、结构校验、证据文件、决策和job完成写盘。分支不再各自重复记账。

调用前intent持久化失败：零客户端构造、零出站。调用后usage持久化失败：调用前pending仍在，重启后阻断后续调用。证据/决策/完成失败：已知usage保留，未完成job的决策仍被原有效性规则隐藏。

### 3. 保留并验证已有影子恢复

已有R6改动保存原金额、业务日、mandate，重放先补缺失额度再推进游标。本轮没有重新改写该逻辑，而是增加金额级故障测试：日限100、先60再70，log/usage/cursor三个失败窗口分别重启重放两次，均只允许第一条，额度只累计60。

旧允许裁决缺金额且额度不存在时，停止并要求核对，游标不推进，不猜测为0。原有跨日/mandate变化重放守卫继续通过。

## 故障矩阵

| 切点 | 磁盘事实 | 重启/重放结果 |
| --- | --- | --- |
| intent写失败 | 无调用intent | 客户端未构造，零模型调用 |
| FakePi入口强杀子进程 | pending已落盘 | 新runner读取pending，后续客户端不构造 |
| 模型返回、usage写失败 | pending仍在 | unknown门禁阻断，未自动重复调用 |
| 证据文件写失败 | 已知15 tokens已结算 | 重读仍15，无有效决策 |
| 决策写失败 | 已知15 tokens已结算 | 重读仍15，无有效决策 |
| job完成写失败 | 已知15 tokens已结算，可能有悬空决策 | job派生有效性隐藏决策 |
| 同attempt结算重放 | 一笔已结算事实 | 不重复计数，新attempt分别计数 |
| 冲突结算 | 原usage与业务日不变 | 拒绝并记录去重冲突审计 |
| 影子log失败 | 无已存裁决或额度 | 重启评估一次，不提前推进游标 |
| 影子log成功、usage失败 | 原裁决及不可变输入已存 | 补一次原额度，再推进游标 |
| 影子usage成功、cursor失败 | 裁决与额度已存 | 重放不重算、不重复累计 |

进程强杀仅针对测试自己创建的FakePi Python子进程，未结束用户node/python服务。并不表示做过真实提供商扣费测试或机器断电测试。

## 本次实测证据

新增 `tests/test_pi_recovery_completion.py` 13项测试。先运行未修复代码，得到 `FAILED (failures=1, errors=8)`：证据写失败usage=0，以及结算缺attempt参数等；随后修复并通过。

与工作区已有 `tests/test_pi_recovery_round6.py` 16项合并运行：

```text
Ran 29 tests in 1.160s  OK
Ran 29 tests in 1.119s  OK
Ran 29 tests in 1.175s  OK
```

全量命令：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1`。

```text
Ran 1383 tests in 48.337s
OK
LIVE_FILES=20 LIVE_UNCHANGED=True TEST_EXIT=0
```

data/live前后比较覆盖20个文件的路径集合及内容SHA-256。已有 `test_wp08_script.py` ResourceWarning继续如实保留，未修改无关测试。`git diff --check`通过。

## 保留边界与下一步

1. 单进程JSON存储，不宣称多进程事务、数据库级并发或硬件掉电持久性。
2. 日预算仍是已报告tokens的门禁，不是提供商端输出上限硬保证；unknown采用保守停止。
3. 本批没有实现人工消费对账API/UI。下一项是带依据、操作者身份、幂等键与审计的对账设计和隔离测试；不得直接改JSON解除阻断。
4. 真实Pi只读链路的非空demo样本验证是另一层验收，仍未执行；真实平台回复、发布、采购、退款仍关闭。
5. 本报告只针对闲鱼项目，不替代量化工作台状态。
