"""Minimal MCP stdio client for xianyu-mcp (read-only channel probe).

Speaks JSON-RPC 2.0 over stdio to `node src/index.ts` of the cloned
alperien/rira2 xianyu-mcp. Read-only tools only: capabilities / search /
search_count. This performs REAL anonymous platform reads (no account,
no cookies, no writes) -- the G2B/A50 R-layer probe the user authorized.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

MCP_DIR = Path(r"E:\闲鱼\xianyu-mcp-tmp")


class McpStdioClient:
    def __init__(self, timeout_total: float = 180.0):
        self.proc = subprocess.Popen(
            ["node", "src/index.ts"],
            cwd=str(MCP_DIR),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        self.deadline = time.monotonic() + timeout_total
        self._next_id = 0

    def _send(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("MCP server deadline exceeded")
        # server messages are newline-delimited JSON-RPC
        line = self._readline_with_timeout(remaining)
        return json.loads(line)

    def _readline_with_timeout(self, seconds: float) -> str:
        # simple polling readline (Windows lacks select on pipes)
        import threading

        holder: dict = {}

        def reader():
            holder["line"] = self.proc.stdout.readline()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(seconds)
        if "line" not in holder:
            raise TimeoutError("MCP read timeout")
        line = holder["line"]
        if not line:
            raise EOFError("MCP server closed stdout")
        return line

    def initialize(self) -> dict:
        result = self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "xianyu-workbench-probe", "version": "1.0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        while True:
            message = self._recv()
            if message.get("id") == self._next_id:
                if "error" in message:
                    raise RuntimeError(f"MCP error: {message['error']}")
                return message.get("result") or {}

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def parse_tool_result(result: dict) -> dict:
    """Return structuredContent if present, else parse content[0].text JSON."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {"raw": result}


def main() -> int:
    out_path = Path(__file__).resolve().parent / "mcp_probe_result.json"
    client = McpStdioClient()
    report: dict = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        info = client.initialize()
        report["server"] = (info.get("serverInfo") or {})
        print("[1] initialized:", report["server"].get("name"), report["server"].get("version"))

        caps = parse_tool_result(client.call_tool("capabilities", {"probe": True}))
        report["capabilities"] = caps
        session_state = (caps.get("data") or {}).get("session_state")
        print("[2] capabilities: ok =", caps.get("ok"),
              "| session_state =", session_state,
              "| works_without_account =", (caps.get("data") or {}).get("works_without_account"))

        search = parse_tool_result(client.call_tool("search", {
            "query": "不锈钢工作台 商用", "pages": 1, "limit": 20,
        }))
        report["search"] = search
        items = (search.get("data") or {}).get("items") or []
        print(f"[3] search: ok={search.get('ok')} status={search.get('status')} count={search.get('count')}")
        for item in items[:8]:
            print(f"    {item.get('item_id','?'):>12}  ¥{item.get('price','?'):>8}  {str(item.get('title',''))[:38]}")
    except Exception as exc:  # noqa: BLE001
        report["error"] = repr(exc)
        print("FAILED:", exc)
    finally:
        client.close()
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved:", out_path)
    # Codex C6: failures must exit non-zero; success only when every step
    # (initialize, capabilities, search) returned ok
    search_ok = isinstance(report.get("search"), dict) and report["search"].get("ok") is True
    caps_ok = isinstance(report.get("capabilities"), dict) and report["capabilities"].get("ok") is True
    return 0 if (search_ok and caps_ok and "error" not in report) else 1


if __name__ == "__main__":
    sys.exit(main())
