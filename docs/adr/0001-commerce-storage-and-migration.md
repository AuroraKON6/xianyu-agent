# ADR-0001：WP01 商业实体的存储与迁移选择

日期：2026-09-18。状态：已接受（阶段一实施依据）。

## 问题

WP01 新增 OfferSnapshot、ProductMatch、Opportunity、DecisionRecord 等商业实体（后续 WP07/10/13 继续加 AgentJob、Mandate、采购与账务）。现有系统 JSON 账本与 SQLite（账号/商品/订单）混用。新实体需要：唯一事实源、追加不可变历史、修订冲突可见、单机 Alpha 可跑、生产多 worker 可迁移。

## 候选方案

1. **直接写入现有 SQLite**：表结构迁移要 Alembic；当前服务单进程、SQLite 仅由 db_manager 拥有，混入新域表会耦合旧初始化顺序问题（2026-09-05 刚修过）。
2. **JSON 文件 + repository 接口**（选中）：与现有 store 约定一致（thread lock + 原子替换），零迁移成本，测试隔离简单；接口后面替换实现即可。
3. **立即上 PostgreSQL + SQLAlchemy/Alembic**：生产正确目标，但阶段一没有任何多 worker/并发写需求，先引入会拖慢第一条薄闭环且增加运维面。

## 决策

- 阶段一：`xianyu_agent/commerce/repositories.py` 提供抽象 repository 协议 + JSON 实现（`data_dir/commerce/*.json`，原子写、线程锁、追加为主）。业务服务只依赖协议。
- 生产目标：PostgreSQL + SQLAlchemy/Alembic（计划 §5.2 已定）。触发条件：引入第二个写入进程（真实 worker/多账号并发）或任一 JSON 文件超过性能阈值时，先写 ADR-0002 迁移方案再动。
- SQLite 继续只服务既有账号/商品/订单域；新旧域不双写、不互相散落读写。一次只有一个事实源。
- 回退：JSON 实现可整体停用（新功能默认关闭，旧业务不依赖 `data/commerce/` 存在）；文件可整目录备份/删除重建。

## 后果

- 优点：阶段一零迁移风险；接口稳定，替换实现不动业务代码。
- 代价：暂无跨进程事务；并发多进程写需等 Postgres 迁移。WP07 的持久任务表如果先于 Postgres 需要 worker，必须先评 ADR。
