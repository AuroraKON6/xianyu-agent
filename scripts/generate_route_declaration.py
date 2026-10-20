#!/usr/bin/env python
"""Generate docs/refactor/ROUTE_READWRITE_DECLARATION.md (taskbook R04).

Lists every route on the composed app with its method and declared
access class:

* ``read``      — pure query; repeated calls leave business state and
                  external systems untouched.
* ``command``   — explicit operator/worker command; may change business
                  state or perform authorized external reads.

Run from the repository root (offline checks environment)::

    .venv/Scripts/python.exe scripts/generate_route_declaration.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

OUT_PATH = REPO_ROOT / "docs" / "refactor" / "ROUTE_READWRITE_DECLARATION.md"

# Routes that keep their own legacy operator-credential gate (their auth
# is equivalent; listed so the declaration stays honest).
LEGACY_AUTH_NOTE = {
    "/commerce/pi-usage",
}


def main() -> int:
    from xianyu_agent import server  # composition root

    rows = []
    for route in server.app.routes:
        methods = sorted(getattr(route, "methods", None) or [])
        path = getattr(route, "path", "")
        if not path:
            continue
        for method in methods:
            if method in ("GET", "HEAD"):
                access = "read"
            else:
                access = "command"
            rows.append((method, path, access))

    rows.sort(key=lambda r: (r[1], r[0]))
    lines = [
        "# 路由只读声明表（R04）",
        "",
        "生成命令：`.venv/Scripts/python.exe scripts/generate_route_declaration.py`（离线环境）。",
        "",
        "- `read`：纯查询。重复调用不改变业务状态、不产生费用、不触发外部调用。",
        "- `command`：显式命令（操作者/worker）。可能改变业务状态或执行获准的外部读取。",
        "",
        "| 方法 | 路径 | 声明 |",
        "|---|---|---|",
    ]
    for method, path, access in rows:
        lines.append("| %s | `%s` | %s |" % (method, path, access))
    lines += [
        "",
        "## 已知说明",
        "",
        "- GET 副作用已迁移：`/execution-jobs` 与 `/reply-outbox` 的过期租约恢复移至显式 `POST /execution-jobs/reconcile`；启动时同步执行一次。",
        "- `GET /commerce/market-channel/status` 仅返回被动配置状态；主动探测是显式 `POST /commerce/market-channel/probe`（受 MARKET_CHANNEL_PROBE_AUTHORIZED 与认证层约束）。",
        "- `GET /commerce/opportunities/{id}/decision-card` 使用只读 `inspect_current`（重跑 ready gates 但只报告不迁移）；暂停过期 ready 走显式 `POST .../revalidate`（记录 operator）。",
        "- `/commerce/pi-usage*` 路由保留自带操作者凭据校验（legacy，等 R17 版本化 API 统一）。",
        "",
    ]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(OUT_PATH), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    print("wrote %s (%d route entries)" % (OUT_PATH, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
