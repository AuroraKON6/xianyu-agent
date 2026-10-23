# R16 镜像与基础镜像 digest 清单（2026-10-22）

| 层 | 引用 | digest / image id |
|---|---|---|
| 应用镜像 | `xianyu-commerce-api:r16` | `sha256:3bf704c5460278ecb4e3a4e3fd4817a91e68f02be90fbcce0e24f187ab25029f` |
| 基础镜像 | `python:3.8-slim` | `python@sha256:1d52838af602b4b5a831beb13a0e4d073280665ea7be7f69ce2382f29c5a613f` |

依赖锁：`requirements.txt`（fastapi 0.110.3 / pydantic 2.x / uvicorn 0.29.0 / httpx / requests 2.32.3 / websocket-client 1.8.0 / websockets / playwright / loguru），构建层 `pip install --no-cache-dir --target /app/deps`，镜像内无运行时包管理器调用。

浏览器依赖（Playwright 浏览器）：本离线 profile 未安装——浏览器属于 R30 的独立 profile；本镜像声明不含浏览器。
