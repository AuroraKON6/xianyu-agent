# R16 实施验收报告：可重建镜像与最小 Compose

日期：2026-10-22。基线为 R15 提交 `74d9911`。

**结论：R16 软件/集成层 PASS。全量离线套件不变（1743 基线），容器四项验收全部实测通过。**

## R16

```yaml
task_id: R16
owned_files: [deploy/docker/Dockerfile, deploy/docker/IMAGE_DIGESTS.md, deploy/compose.yml, deploy/.env.example, .dockerignore(仓库根)]
software_status: PASS
integration_status: PASS  # 详见下方实测矩阵
real_data_status: NOT_RUN
commercial_status: NOT_RUN
source_calls: {count: 0, actual_cost: null}
model_calls: {count: 0, tokens: 0, actual_cost: null}
platform_effects: {read: 0, write: 0, unknown: 0}
data_before_after_equal: true   # live data 247 未挂载进任何容器
known_limits:
  - 单 writer 离线 profile（XIANYU_TEST_ISOLATION=1 + stub adapter 是镜像默认）；PG api/worker 拆分与 n8n 服务在后续 profile
  - Playwright 浏览器不在本镜像（R30 独立 profile）
  - Docker Desktop 数据在 E:\Docker（安装时已定）；生产磁盘策略（F 盘/迁移 VHDX）留给 R30/R31 运维任务
rollback: docker compose -f deploy/compose.yml down（保留卷）；不做删卷全局清理
next_task: R17
```

## 环境核验（任务书前置要求，全部实测）

| 项 | 结果 |
|---|---|
| 虚拟化 | HypervisorPresent=True（Hyper-V/WSL2 后端就绪） |
| WSL2 | Ubuntu + docker-desktop（均 v2，Stopped→按需启动） |
| Docker | CLI 29.8.2 / Compose v5.5.1，daemon 就绪（7.6GiB 内存分配） |
| 磁盘 | E: 可用 65GiB（94% 已用——空间预警已记，F: 未挂载于本机当前盘点） |
| 网络 | docker.io 直连被拦（text/html 假 manifest）→ daemon.json 配置 registry-mirrors（daocloud）后拉取正常 |
| 最小无业务容器 | python:3.8-slim 拉取+运行成功（hello-world 镜像源异常，改用基础镜像冒烟） |

## 部署工件

- `deploy/docker/Dockerfile`：python:3.8-slim（与已验证运行时 3.8.5 对齐）；非 root uid 10001；依赖仅构建期安装（--no-cache-dir --target，无运行时 pip）；代码层与依赖层分离；`/state` 预建属主 10001（命名卷 root 挂载陷阱实测修复）；WORKDIR=/state 使遗留相对路径 `data` 落入卷内；PYTHONPATH=/app:/app/deps；默认隔离入口（TEST_ISOLATION=1+stub，误 `docker run` 无害）；HEALTHCHECK 打 /health
- `.dockerignore`（仓库根）：.env*、data/、backups/、output/、node_modules、.venv、.git、浏览器 profile、日志、锁文件、docs/research/tests/scripts 全排除
- `deploy/compose.yml`：单服务 commerce-api + 命名卷 xianyu-state；`deploy.replicas: 1`（单 writer 硬声明）；端口 `${XIANYU_BIND:-127.0.0.1}:8788`（默认 loopback）；后台链默认关（SHADOW/RADAR=0）
- `deploy/.env.example`：bind/adapter/isolation/local-operator 四项，注明真实凭据永不入镜像
- `deploy/docker/IMAGE_DIGESTS.md`：应用镜像 id + 基础镜像 digest + 依赖锁说明

## 验收矩阵（全部真机实测）

| 任务书验收项 | 实测 |
|---|---|
| 干净环境构建 | `docker build` 从基础镜像一次成功（修复卷属主+WORKDIR 后最终版 0.9s 缓存命中） |
| 镜像无 secret/data | 容器内遍历 /app：2149 文件，forbidden=[]（data/.env/backups/node_modules/.venv 零命中）；非 root uid=10001 断言通过 |
| 端口默认 loopback | compose 默认绑定 127.0.0.1:8788；curl 从宿主机访问 health=200 |
| 重建不丢允许持久数据 | compose down→up 后 /state/data 标记文件原样读回；docker volume 独立验证同一结论 |
| 浏览器依赖版本匹配 | 本 profile 无浏览器（R30 独立处理，manifest 已声明） |

过程中发现并修复的两个真实容器缺陷：① 命名卷 root 属主导致非 root 进程 PermissionError（chown 预建目录）；② WORKDIR=/app 下遗留相对路径 `data` 不可写（WORKDIR 移至 /state + PYTHONPATH 补 /app）。这两个问题只有真启动容器才能暴露——正是任务书要求实测而非只写 Dockerfile 的原因。
