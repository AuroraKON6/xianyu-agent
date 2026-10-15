"""Opt-in anonymous read reproduction; writes only fresh review evidence."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-real-read", action="store_true")
    parser.add_argument("--mcp-dir", required=True)
    parser.add_argument("--query", default="不锈钢工作台")
    parser.add_argument("--tool", choices=("all", "search_count", "browse_feed", "search_items"), default="all")
    parser.add_argument("--retry-once", action="store_true")
    args = parser.parse_args()
    if not args.run_real_read:
        parser.error("--run-real-read is required; this command performs anonymous platform reads")
    mcp_dir = Path(args.mcp_dir).resolve()
    if not (mcp_dir / "src" / "index.ts").is_file():
        parser.error("missing MCP source entrypoint")
    out_dir = HERE / ("mcp-read-" + uuid.uuid4().hex)
    out_dir.mkdir()
    original = ROOT / "research" / "real-source-demo-2026-10-12" / "mcp_probe.py"
    spec = importlib.util.spec_from_file_location("codex_mcp_read_client", str(original))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MCP_DIR = mcp_dir
    report = {
        "source": "anonymous_platform_read",
        "client_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "production_client_integration_proven": False,
        "attempts": [],
        "success": False,
    }
    client = None
    try:
        client = module.McpStdioClient(timeout_total=600)
        report["server"] = client.initialize().get("serverInfo") or {}
        caps = module.parse_tool_result(client.call_tool("capabilities", {"probe": True}))
        report["capabilities"] = caps
        if caps.get("ok") is not True or (caps.get("data") or {}).get("session_state") != "logged_out":
            raise RuntimeError("anonymous logged-out session not demonstrated; no search calls sent")
        tools = {
            "search_count": {"query": args.query},
            "browse_feed": {"pages": 1, "limit": 15},
            "search_items": {"query": args.query, "pages": 1, "limit": 15, "attempts": 1, "timeout_s": 90},
        }
        selected = list(tools) if args.tool == "all" else [args.tool]
        all_ok = True
        for name in selected:
            succeeded = False
            for attempt in range(1, 3 if args.retry_once else 2):
                response = module.parse_tool_result(client.call_tool(name, tools[name]))
                report["attempts"].append({"tool": name, "attempt": attempt, "arguments": tools[name], "response": response})
                if response.get("ok") is True:
                    succeeded = True
                    break
                diagnostics = response.get("diagnostics") or {}
                cooldown = response.get("retry_after_s") or diagnostics.get("retry_after_s") or 0
                if not args.retry_once or attempt >= 2 or not isinstance(cooldown, (int, float)) or cooldown <= 0:
                    break
                # A longer cooldown is a pending run, not permission to retry early.
                if cooldown > 60:
                    report["pending_cooldown_seconds"] = cooldown
                    break
                time.sleep(cooldown)
            all_ok = all_ok and succeeded
        report["success"] = all_ok
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if client is not None:
            proc = client.proc
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                if os.name == "nt" and proc.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                client.close()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        (out_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("EVIDENCE_DIR=" + str(out_dir))
        print("SUCCESS=" + str(report["success"]))
    return 0 if report["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
