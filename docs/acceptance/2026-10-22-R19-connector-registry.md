# R19 实施验收报告：连接器与来源目录

日期：2026-10-22。基线为 R18 提交 `08771fa`。

**结论：R19 软件/集成层 PASS。验收 17/17 绿，全量离线 1783/1783 OK（13 skipped=PG），live 数据 247 文件不变。**

## R19

```yaml
task_id: R19
owned_files: [xianyu_agent/connectors/__init__.py, xianyu_agent/connectors/contracts.py, xianyu_agent/connectors/registry.py, xianyu_agent/source_registry.py, config/source-registry.example.json, tests/test_r19_connector_registry.py]
software_status: PASS   # 17/17
integration_status: PASS  # 全量 1783/1783 OK, 数据 247/247 不变
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true
known_limits: [fetch 的真实网络路径在本批用 mock 连接验证（离线环境封锁真实外联）；R20/R33 真实市场通道走本层时做真网验收]
rollback: registry.disable(connector) 停单连接器，其余来源继续；未注册连接器永不自动替换
next_task: R21-file
```

## 交付

**connectors/contracts.py**：ConnectorSpec/ToolSpec 合同（能力 schema 版本+digest、所需 scope、配额键、超时、字节数上限、出站主机白名单、upstream_family、probe 是否产生传输调用）+ **SSRF 出站守卫** `assert_public_http_url`（仅 http/https；解析全部地址候选——字面量与 DNS——拒绝 private/loopback/link-local/reserved/multicast/unspecified，含云元数据地址 169.254.169.254 与 IPv6 ::1；主机白名单强制；DNS 不可用时降级为字面量检查，连接期仍兜底）。

**connectors/registry.py**：
- **schema 钉死**：工具调用携带已批准版本；注册版本/digest 变化 → `schema_drift_stopped` 拒绝，**受影响工具停用**直到操作者显式 `accept_schema`；scope 缺失 → `scope_forbidden`
- **有界抓取** `fetch()`：每跳 SSRF 校验 + **手工重定向跟随（每跳重新验证，≤3 跳）**；响应体 > `max_response_bytes` → `response_too_large` 拒绝（读 max+1 字节判定，不撑内存）；任何违规抛结构化异常，无假成功
- **probe 诚实**：默认 `transport_calls=0` + `verified_fetch=false`（probe≠已核验取数，审查 A50 家族）；连接器可声明 probe 有传输调用，此时如实计数

**source_registry.py**：来源（业务数据源）↔ 连接器（受批传输）映射；**来源默认 disabled**（启用是显式决定）；`connector_for` 解析链路或拒绝（unknown_source / source_disabled / connector_unavailable——连接器停用后其来源拒绝服务，**永不自动换成未审核连接器**）。

**config/source-registry.example.json**：候选样例（市场通道 disabled、供应商文件 enabled）。

## 验收矩阵（tests/test_r19_connector_registry.py，17/17）

| 任务书验收项 | 测试 |
|---|---|
| schema 改变停止受影响工具 | v1 钉死后升级 v2 → `schema_drift_stopped`；其他连接器不受影响；显式 accept 后恢复 |
| 恶意 URL | 127.0.0.1/localhost/10.x/192.168.x/169.254.169.254/[::1]/file:///ftp:// 全拒；白名单外主机 host_forbidden |
| 重定向 | 公网站 302 → 127.0.0.1 第二跳被拦（per-hop 校验在第二次连接发出**之前**执行）；白名单内重定向成功跟随；>3 跳 too_many_redirects |
| 超大响应 | >1KiB 上限 → response_too_large（不缓冲全量） |
| 假成功 | probe 恒 transport_calls=0 + verified_fetch=false；fetch 违规全部抛异常；未注册连接器 unknown_connector |
| 启动预热 | 构造+注册+check_tool+probe 全程 socket.connect 打桩断言零调用（含 DNS 被隔离守卫封锁的降级路径——真机首跑暴露后修复） |
| 回退粒度 | disable 单连接器只停其来源；另一来源继续；未注册连接器拒绝替换 |
