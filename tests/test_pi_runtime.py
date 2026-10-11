import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from xianyu_agent.pi_runtime import PI_TOOL_ALLOWLIST, PiRpcClient, PiRuntimeConfig, PiRuntimeError


FAKE_PI = r'''
import json
import sys
import time

for raw in sys.stdin:
    command = json.loads(raw)
    request_id = command.get("id")
    if command.get("type") == "get_state":
        print(json.dumps({
            "id": request_id,
            "type": "response",
            "command": "get_state",
            "success": True,
            "data": {
                "model": {"id": "fake-model"},
                "thinkingLevel": "low",
                "sessionFile": "fake.jsonl",
                "messageCount": 2,
            },
        }), flush=True)
    elif command.get("type") == "prompt":
        print(json.dumps({
            "id": request_id,
            "type": "response",
            "command": "prompt",
            "success": True,
        }), flush=True)
        print(json.dumps({"type": "agent_start"}), flush=True)
        print(json.dumps({
            "type": "tool_execution_start",
            "toolCallId": "tool-1",
            "toolName": "xianyu_business_snapshot",
            "args": {},
        }), flush=True)
        print(json.dumps({
            "type": "tool_execution_end",
            "toolCallId": "tool-1",
            "toolName": "xianyu_business_snapshot",
            "result": {"content": []},
            "isError": False,
        }), flush=True)
        print(json.dumps({
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "当前经营状态已读取。"}],
                "usage": {"input": 10, "output": 8, "totalTokens": 18},
            },
        }), flush=True)
        print(json.dumps({"type": "turn_end", "message": {}, "toolResults": []}), flush=True)
        print(json.dumps({"type": "agent_end", "messages": [], "willRetry": False}), flush=True)
        print(json.dumps({"type": "agent_settled"}), flush=True)
'''


FAKE_PI_TIMEOUT = r'''
import json
import sys
import time

for raw in sys.stdin:
    command = json.loads(raw)
    print(json.dumps({
        "id": command.get("id"),
        "type": "response",
        "command": command.get("type"),
        "success": True,
        "data": {},
    }), flush=True)
    if command.get("type") == "prompt":
        time.sleep(10)
'''


class PiRpcClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "fake_pi.py"
        self.script.write_text(FAKE_PI, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def make_client(self, *, script=None, timeout=3.0, provider="fake", base_env=None):
        config = PiRuntimeConfig(
            project_root=self.root,
            data_dir=self.root / "data",
            node_path=sys.executable,
            cli_path=script or self.script,
            provider=provider,
            model="fake-model",
            thinking="low",
            request_timeout_seconds=timeout,
            api_key_configured=True,
            session_id="11111111-1111-4111-8111-111111111111",
        )
        return PiRpcClient(config, base_env=base_env)

    def test_rpc_state_prompt_events_and_shutdown(self):
        client = self.make_client()
        try:
            state = client.get_state()
            self.assertEqual(state["model"]["id"], "fake-model")

            result = client.prompt(
                "读取经营状态",
                context={"api_key": "must-not-leak", "filters": {"category": "商用厨具"}},
                publish=True,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["type"], "pi_agent")
            self.assertEqual(result["entrypoint"], "pi")
            self.assertEqual(result["result"]["message"], "当前经营状态已读取。")
            self.assertEqual(result["result"]["turns"], 1)
            self.assertEqual(
                result["result"]["tool_calls"],
                [{"tool_call_id": "tool-1", "name": "xianyu_business_snapshot", "ok": True}],
            )
            self.assertFalse(result["result"]["external_action"])
            self.assertTrue(client.status()["running"])
        finally:
            client.stop()
        self.assertFalse(client.status()["running"])

    def test_active_status_performs_rpc_handshake(self):
        client = self.make_client()
        try:
            self.assertFalse(client.status()["running"])
            status = client.status(active=True)
            self.assertTrue(status["ok"])
            self.assertTrue(status["running"])
            self.assertEqual(status["model"], "fake-model")
            self.assertEqual(status["message_count"], 2)
        finally:
            client.stop()

    def test_passive_status_does_not_wait_for_an_active_turn_lock(self):
        client = self.make_client()
        lock_held = threading.Event()
        release_lock = threading.Event()

        def hold_turn_lock():
            with client._lock:
                lock_held.set()
                release_lock.wait(timeout=2)

        worker = threading.Thread(target=hold_turn_lock)
        worker.start()
        self.assertTrue(lock_held.wait(timeout=1))
        started = time.monotonic()
        try:
            status = client.status(active=False)
        finally:
            release_lock.set()
            worker.join(timeout=1)

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertFalse(status["running"])

    def test_command_is_project_local_and_strictly_allowlisted(self):
        client = self.make_client()
        command = list(client._build_command())
        tools = command[command.index("--tools") + 1].split(",")

        self.assertIn("--approve", command)
        self.assertIn("--session-dir", command)
        self.assertIn("--session-id", command)
        self.assertEqual(tuple(tools), PI_TOOL_ALLOWLIST)
        self.assertIn("xianyu_candidate_activation_packet", tools)
        self.assertIn("xianyu_candidate_evidence", tools)
        self.assertTrue({"read", "bash", "edit", "write", "grep", "find", "ls"}.isdisjoint(tools))
        self.assertTrue(all("approve" not in tool and "execute" not in tool for tool in tools))
        self.assertEqual(client._process_env()["PI_CODING_AGENT_DIR"], str(self.root / "data" / "pi_agent"))
        self.assertEqual(client._process_env()["PI_OFFLINE"], "1")

    def test_process_environment_excludes_unrelated_business_credentials(self):
        client = self.make_client(
            provider="deepseek",
            base_env={
                "PATH": "C:\\Windows\\System32",
                "DEEPSEEK_API_KEY": "model-key",
                "FEISHU_VERIFICATION_TOKEN": "must-not-reach-pi",
                "XIANGUANJIA_TOKEN": "must-not-reach-pi",
                "DB_PATH": "must-not-reach-pi",
            },
        )

        env = client._process_env()

        self.assertEqual(env["DEEPSEEK_API_KEY"], "model-key")
        self.assertEqual(env["PATH"], "C:\\Windows\\System32")
        self.assertNotIn("FEISHU_VERIFICATION_TOKEN", env)
        self.assertNotIn("XIANGUANJIA_TOKEN", env)
        self.assertNotIn("DB_PATH", env)

    def test_timeout_terminates_the_stuck_rpc_process(self):
        timeout_script = self.root / "fake_pi_timeout.py"
        timeout_script.write_text(FAKE_PI_TIMEOUT, encoding="utf-8")
        client = self.make_client(script=timeout_script, timeout=0.2)

        with self.assertRaisesRegex(PiRuntimeError, "timed out"):
            client.prompt("不会完成")

        self.assertFalse(client.status()["running"])

    def test_multiturn_usage_accumulates_every_assistant_response(self):
        client = self.make_client()
        events = [
            {"type": "message_end", "message": {"role": "assistant", "content": [],
             "usage": {"input": 10, "output": 5, "totalTokens": 15}}},
            {"type": "message_end", "message": {"role": "toolResult", "usage": {"totalTokens": 999}}},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
             "usage": {"input": 20, "output": 7, "totalTokens": 27}}},
            {"type": "agent_settled"},
        ]
        with patch.object(client, "_start_locked"), patch.object(client, "_request_locked", return_value=({}, events)):
            result = client.prompt("test")
        self.assertEqual(result["result"]["usage"], {"input": 30, "output": 12, "totalTokens": 42})

    def test_missing_usage_in_one_turn_keeps_total_unknown(self):
        client = self.make_client()
        events = [
            {"type": "message_end", "message": {"role": "assistant", "content": []}},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
             "usage": {"totalTokens": 27}}},
            {"type": "agent_settled"},
        ]
        with patch.object(client, "_start_locked"), patch.object(client, "_request_locked", return_value=({}, events)):
            result = client.prompt("test")
        self.assertNotIn("totalTokens", result["result"]["usage"])

    def test_later_model_error_cannot_be_hidden_by_earlier_text(self):
        client = self.make_client()
        events = [
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "working"}],
             "usage": {"totalTokens": 15}}},
            {"type": "message_end", "message": {"role": "assistant", "content": [], "errorMessage": "provider failed"}},
            {"type": "agent_settled"},
        ]
        with patch.object(client, "_start_locked"), patch.object(client, "_request_locked", return_value=({}, events)):
            with self.assertRaisesRegex(PiRuntimeError, "provider failed"):
                client.prompt("test")


if __name__ == "__main__":
    unittest.main()
