from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, Mapping, Optional, Sequence, Tuple


PI_TOOL_ALLOWLIST: Tuple[str, ...] = (
    "read",
    "xianyu_business_snapshot",
    "xianyu_market_search",
    "xianyu_candidates",
    "xianyu_candidate_activation_packet",
    "xianyu_candidate_evidence",
    "xianyu_work_orders",
    "xianyu_commercial_terms",
    "xianyu_publish_preflight",
    "xianyu_commercial_launch_preview",
    "xianyu_review_candidate",
    "xianyu_prepare_listing",
    "xianyu_prepare_customer_reply",
)

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|bearer|cookie|password|secret|token)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(?:(?:api[_-]?key|authorization|bearer|cookie|password|secret|token)\s*[:=]\s*)([^\s,;]+)"
)
_MAX_CONTEXT_TEXT = 12000
_MAX_MESSAGE_TEXT = 20000


class PiRuntimeError(RuntimeError):
    """A sanitized Pi lifecycle or RPC failure."""


def _bool_value(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    text = _SENSITIVE_VALUE.sub(lambda match: match.group(0).replace(match.group(1), "[REDACTED]"), text)
    return text[:limit]


def _safe_context(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        cleaned: Dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:100]:
            key = str(raw_key)[:120]
            cleaned[key] = "[REDACTED]" if _SENSITIVE_KEY.search(key) else _safe_context(raw_value, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_safe_context(item, depth + 1) for item in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(value, 2000)


@dataclass(frozen=True)
class PiRuntimeConfig:
    project_root: Path
    data_dir: Path
    node_path: str
    cli_path: Path
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    thinking: str = "low"
    api_base_url: str = "http://127.0.0.1:8788"
    request_timeout_seconds: float = 180.0
    enabled: bool = True
    api_key_configured: bool = False
    session_id: str = ""
    tools: Tuple[str, ...] = PI_TOOL_ALLOWLIST

    @property
    def session_dir(self) -> Path:
        return self.data_dir / "pi_sessions"

    @property
    def agent_dir(self) -> Path:
        return self.data_dir / "pi_agent"


class PiRpcClient:
    """Own one project-local Pi RPC process and serialize operator turns."""

    def __init__(self, config: PiRuntimeConfig, base_env: Optional[Mapping[str, str]] = None) -> None:
        self.config = config
        self._base_env = dict(base_env or os.environ)
        self._process: Optional[subprocess.Popen[str]] = None
        self._events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._stderr: Deque[str] = deque(maxlen=40)
        self._lock = threading.RLock()
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

    @classmethod
    def from_env(
        cls,
        project_root: Path,
        data_dir: Path,
        env: Optional[Mapping[str, str]] = None,
    ) -> "PiRpcClient":
        values = dict(os.environ if env is None else env)
        root = project_root.resolve()
        raw_node = values.get("PI_NODE_PATH", "node").strip() or "node"
        node_path = shutil.which(raw_node) or raw_node
        cli_path = Path(
            values.get(
                "PI_CLI_PATH",
                str(root / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"),
            )
        ).resolve()
        session_id = values.get("PI_SESSION_ID", "").strip() or str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"xianyu-pi:{root.as_posix()}")
        )
        config = PiRuntimeConfig(
            project_root=root,
            data_dir=data_dir.resolve(),
            node_path=node_path,
            cli_path=cli_path,
            provider=values.get("PI_PROVIDER", "deepseek").strip() or "deepseek",
            model=(values.get("PI_MODEL") or values.get("DEEPSEEK_MODEL") or "deepseek-v4-flash").strip(),
            thinking=values.get("PI_THINKING", "low").strip() or "low",
            api_base_url=values.get("XIANYU_API_BASE_URL", "http://127.0.0.1:8788").rstrip("/"),
            request_timeout_seconds=max(5.0, float(values.get("PI_REQUEST_TIMEOUT_SECONDS", "180"))),
            enabled=_bool_value(values.get("PI_ENABLED"), True),
            api_key_configured=bool(values.get("DEEPSEEK_API_KEY", "").strip()),
            session_id=session_id,
        )
        return cls(config, base_env=values)

    def _build_command(self) -> Sequence[str]:
        return (
            self.config.node_path,
            str(self.config.cli_path),
            "--mode",
            "rpc",
            "--approve",
            "--provider",
            self.config.provider,
            "--model",
            self.config.model,
            "--thinking",
            self.config.thinking,
            "--session-dir",
            str(self.config.session_dir),
            "--session-id",
            self.config.session_id,
            "--name",
            "xianyu-operator",
            "--tools",
            ",".join(self.config.tools),
        )

    def _process_env(self) -> Dict[str, str]:
        env = dict(self._base_env)
        env.update(
            {
                "PI_CODING_AGENT_DIR": str(self.config.agent_dir),
                "PI_SKIP_VERSION_CHECK": "1",
                "PI_OFFLINE": "1",
                "PI_TELEMETRY": "0",
                "XIANYU_API_BASE_URL": self.config.api_base_url,
            }
        )
        return env

    def _running_locked(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._running_locked():
            return
        if not self.config.enabled:
            raise PiRuntimeError("Pi runtime is disabled by PI_ENABLED")
        if not self.config.cli_path.is_file():
            raise PiRuntimeError("Project-local Pi CLI is missing; run npm install in the project root")
        if not Path(self.config.node_path).is_file() and shutil.which(self.config.node_path) is None:
            raise PiRuntimeError("Node.js is unavailable; Pi requires Node.js 22.19 or newer")

        self._stop_locked()
        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        self.config.agent_dir.mkdir(parents=True, exist_ok=True)
        self._events = queue.Queue()
        self._stderr.clear()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                list(self._build_command()),
                cwd=str(self.config.project_root),
                env=self._process_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._process = None
            raise PiRuntimeError(f"Unable to start Pi RPC: {_safe_text(exc)}") from exc

        process = self._process
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process,),
            name="xianyu-pi-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process,),
            name="xianyu-pi-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        time.sleep(0.08)
        if process.poll() is not None:
            detail = self._stderr_summary()
            self._stop_locked()
            raise PiRuntimeError(f"Pi RPC exited during startup{': ' + detail if detail else ''}")

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for raw_line in stream:
                line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
                if line.endswith("\r"):
                    line = line[:-1]
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    self._events.put({"type": "_protocol_error", "message": _safe_text(line, 500)})
                    continue
                if isinstance(event, dict):
                    self._events.put(event)
        finally:
            self._events.put({"type": "_process_eof", "returncode": process.poll()})

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        stream = process.stderr
        if stream is None:
            return
        for line in stream:
            cleaned = _safe_text(line.strip(), 1000)
            if cleaned:
                self._stderr.append(cleaned)

    def _stderr_summary(self) -> str:
        return " | ".join(list(self._stderr)[-5:])[:3000]

    def _write_locked(self, command: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise PiRuntimeError("Pi RPC process is not running")
        try:
            process.stdin.write(json.dumps(command, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise PiRuntimeError(f"Pi RPC input failed: {_safe_text(exc)}") from exc

    def _next_event_locked(self, deadline: float) -> Dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PiRuntimeError("Pi RPC request timed out")
        try:
            event = self._events.get(timeout=remaining)
        except queue.Empty as exc:
            raise PiRuntimeError("Pi RPC request timed out") from exc
        if event.get("type") == "_process_eof":
            detail = self._stderr_summary()
            raise PiRuntimeError(f"Pi RPC process exited unexpectedly{': ' + detail if detail else ''}")
        if event.get("type") == "_protocol_error":
            raise PiRuntimeError(f"Pi RPC returned invalid JSONL: {event.get('message', '')}")
        return event

    def _request_locked(
        self,
        command: Mapping[str, Any],
        deadline: float,
    ) -> Tuple[Dict[str, Any], list[Dict[str, Any]]]:
        request_id = f"xianyu-{uuid.uuid4().hex}"
        payload = {**command, "id": request_id}
        self._write_locked(payload)
        preceding: list[Dict[str, Any]] = []
        while True:
            event = self._next_event_locked(deadline)
            if event.get("type") == "response" and event.get("id") == request_id:
                if not event.get("success"):
                    raise PiRuntimeError(f"Pi RPC rejected {command.get('type')}: {_safe_text(event.get('error'))}")
                return event, preceding
            preceding.append(event)

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            self._start_locked()
            deadline = time.monotonic() + min(30.0, self.config.request_timeout_seconds)
            response, _ = self._request_locked({"type": "get_state"}, deadline)
            return dict(response.get("data") or {})

    def status(self, active: bool = False) -> Dict[str, Any]:
        # `/health` is itself called by the Pi snapshot tool. Passive status must
        # therefore never wait on the turn lock held by `prompt()`.
        process = self._process
        running = process is not None and process.poll() is None
        status: Dict[str, Any] = {
            "enabled": self.config.enabled,
            "configured": bool(
                self.config.enabled
                and self.config.cli_path.is_file()
                and (Path(self.config.node_path).is_file() or shutil.which(self.config.node_path))
                and self.config.api_key_configured
            ),
            "running": running,
            "provider": self.config.provider,
            "model": self.config.model,
            "thinking": self.config.thinking,
            "session_id": self.config.session_id,
            "tool_count": len(self.config.tools),
            "external_action_tools": False,
        }
        if not active:
            return status

        with self._lock:
            try:
                state = self.get_state()
            except PiRuntimeError as exc:
                return {**status, "ok": False, "error": _safe_text(exc), "running": self._running_locked()}
            model = state.get("model") or {}
            return {
                **status,
                "ok": True,
                "running": True,
                "model": model.get("id") or self.config.model,
                "thinking": state.get("thinkingLevel") or self.config.thinking,
                "message_count": state.get("messageCount", 0),
                "session_file": bool(state.get("sessionFile")),
            }

    def prompt(
        self,
        message: str,
        context: Optional[Mapping[str, Any]] = None,
        publish: bool = True,
    ) -> Dict[str, Any]:
        clean_message = str(message or "").strip()
        if not clean_message:
            raise PiRuntimeError("message is required")
        if len(clean_message) > _MAX_MESSAGE_TEXT:
            raise PiRuntimeError(f"message exceeds {_MAX_MESSAGE_TEXT} characters")

        safe_context = _safe_context(context or {})
        context_text = json.dumps(safe_context, ensure_ascii=False, separators=(",", ":"))
        if len(context_text) > _MAX_CONTEXT_TEXT:
            context_text = context_text[:_MAX_CONTEXT_TEXT] + "...[TRUNCATED]"
        prompt = (
            f"Operator request:\n{clean_message}\n\n"
            "Request metadata (not an approval and not authorization to execute externally):\n"
            f"{context_text}\n"
            f"Legacy publish-toggle value: {str(bool(publish)).lower()}. "
            "This value records UI intent only; follow the Pi tool policy and all business gates."
        )

        with self._lock:
            self._start_locked()
            deadline = time.monotonic() + self.config.request_timeout_seconds
            try:
                _, events = self._request_locked({"type": "prompt", "message": prompt}, deadline)
                settled = any(event.get("type") == "agent_settled" for event in events)
                while not settled:
                    event = self._next_event_locked(deadline)
                    events.append(event)
                    settled = event.get("type") == "agent_settled"
            except PiRuntimeError:
                self._stop_locked()
                raise

            final_text = ""
            error_message = ""
            tool_calls: list[Dict[str, Any]] = []
            tool_by_id: Dict[str, Dict[str, Any]] = {}
            turns = 0
            usage: Dict[str, Any] = {}
            for event in events:
                event_type = event.get("type")
                if event_type == "turn_end":
                    turns += 1
                elif event_type == "tool_execution_start":
                    record = {
                        "tool_call_id": str(event.get("toolCallId") or ""),
                        "name": str(event.get("toolName") or ""),
                        "ok": None,
                    }
                    tool_calls.append(record)
                    tool_by_id[record["tool_call_id"]] = record
                elif event_type == "tool_execution_end":
                    tool_call_id = str(event.get("toolCallId") or "")
                    record = tool_by_id.get(tool_call_id)
                    if record is not None:
                        record["ok"] = not bool(event.get("isError"))
                elif event_type == "message_end":
                    candidate = event.get("message") or {}
                    if candidate.get("role") != "assistant":
                        continue
                    text_parts = [
                        str(block.get("text") or "")
                        for block in candidate.get("content") or []
                        if isinstance(block, Mapping) and block.get("type") == "text"
                    ]
                    if any(text_parts):
                        final_text = "".join(text_parts).strip()
                    if candidate.get("errorMessage"):
                        error_message = _safe_text(candidate.get("errorMessage"), 2000)
                    if isinstance(candidate.get("usage"), Mapping):
                        usage = {
                            key: candidate["usage"].get(key)
                            for key in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
                            if candidate["usage"].get(key) is not None
                        }

            if not final_text:
                if error_message:
                    raise PiRuntimeError(f"Pi agent failed: {error_message}")
                raise PiRuntimeError("Pi agent completed without an assistant response")

            return {
                "ok": True,
                "type": "pi_agent",
                "entrypoint": "pi",
                "result": {
                    "message": final_text,
                    "session_id": self.config.session_id,
                    "provider": self.config.provider,
                    "model": self.config.model,
                    "thinking": self.config.thinking,
                    "turns": turns,
                    "tool_calls": tool_calls,
                    "usage": usage,
                    "publish_requested": bool(publish),
                    "external_action": False,
                },
            }

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
