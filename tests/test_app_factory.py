"""R02 acceptance tests: application factory and lifecycle.

Covers the AppRuntime container (idempotent start/shutdown, managed
threads, cancellation of every asyncio loop handle) and the server
composition contract: importing ``xianyu_agent.server`` must start no
threads; the lifecycle moves only through the runtime.

The server-import test runs in a child process (same pattern as
test_db_boundary) so it observes a truly fresh interpreter and never
triggers the real discovery loops in-process.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


def _fake_settings(data_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(data_dir=data_dir)


class AppRuntimeLifecycleTests(unittest.TestCase):
    def _runtime(self, tmp: Path):
        from xianyu_agent.app_factory import AppRuntime

        return AppRuntime(settings=_fake_settings(tmp), state_dir=tmp)

    def test_construction_starts_nothing_and_writes_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            before = threading.active_count()
            runtime = self._runtime(state)
            self.assertEqual(threading.active_count(), before)
            self.assertFalse(runtime.status["started"])
            self.assertEqual(runtime.status["live_tasks"], 0)
            self.assertEqual(list(state.rglob("*")), [])

    def test_boot_hooks_run_once_and_repeat_start_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(Path(tmp))
            calls = []
            runtime.on_boot(lambda: calls.append("boot"))
            self.assertTrue(runtime.start())
            self.assertFalse(runtime.start())  # repeat start: no double boot
            self.assertEqual(calls, ["boot"])
            self.assertTrue(runtime.status["started"])

    def test_lifecycle_restart_after_shutdown_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(Path(tmp))
            calls = []
            runtime.on_boot(lambda: calls.append("boot"))
            self.assertTrue(runtime.start())
            self.assertFalse(runtime.start())  # repeat within lifecycle: no-op
            asyncio.run(runtime.shutdown())
            # a shut-down runtime may boot again (re-entered ASGI lifespan):
            # fresh lifecycle, no double loops, boot hooks run once per cycle
            self.assertTrue(runtime.start())
            self.assertEqual(calls, ["boot", "boot"])
            asyncio.run(runtime.shutdown())

    def test_managed_thread_starts_and_stops_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(Path(tmp))
            events = []
            released = threading.Event()

            def _start():
                events.append("start")

            def _stop():
                events.append("stop")
                released.set()

            runtime.manage_thread("worker", start=_start, stop=_stop)
            runtime.start()
            runtime.start()
            asyncio.run(runtime.shutdown())
            asyncio.run(runtime.shutdown())  # second shutdown: no-op
            self.assertEqual(events, ["start", "stop"])

    def test_shutdown_cancels_loop_handles_and_runs_stop_hooks_reversed(self):
        from xianyu_agent.app_factory import AppRuntime

        with tempfile.TemporaryDirectory() as tmp:
            runtime = AppRuntime(settings=_fake_settings(Path(tmp)), state_dir=Path(tmp))
            stops = []
            runtime.on_stop(lambda: stops.append("first"))
            runtime.on_stop(lambda: stops.append("second"))
            cancel_seen = []

            async def _long_loop():
                try:
                    while True:
                        await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    cancel_seen.append("cancelled")
                    raise

            async def scenario():
                runtime.add_loop("long", _long_loop)
                await runtime.start_async()
                task_count = runtime.status["live_tasks"]
                # yield once so the loop coroutine actually starts before
                # shutdown cancels it (an unstarted task is cancelled
                # without ever entering its body)
                await asyncio.sleep(0.05)
                await runtime.shutdown()
                return task_count

            live = asyncio.run(scenario())
            self.assertEqual(live, 1)
            self.assertEqual(cancel_seen, ["cancelled"])
            self.assertEqual(stops, ["second", "first"])
            self.assertEqual(runtime.status["live_tasks"], 0)
            self.assertTrue(runtime.status["stopped"])


class ServerImportContractTests(unittest.TestCase):
    def _child(self, code: str, extra_env=None):
        env = dict(os.environ)
        env.pop("DB_PATH", None)
        env.pop("DATA_DIR", None)
        env.setdefault("XIANYU_TEST_ISOLATION", "1")
        env.setdefault("XIANYU_ADAPTER", "stub")
        env["PYTHONIOENCODING"] = "utf-8"
        if extra_env:
            env.update(extra_env)
        python = str(VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable)
        return subprocess.run(
            [python, "-c", code],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )

    def test_import_starts_no_threads_and_keeps_compatibility_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = (
                "import json, threading;"
                "before = threading.active_count();"
                "import xianyu_agent.server as s;"
                "after = threading.active_count();"
                "names = ['app','settings','workflow','pi_task_runner',"
                "'shadow_observer','commerce_repositories','pi_runtime',"
                "'opportunity_service','runtime'];"
                "missing = [n for n in names if not hasattr(s, n)];"
                "print(json.dumps({'before': before, 'after': after,"
                "'missing': missing, 'runtime': s.runtime.status}))"
            )
            result = self._child(code, extra_env={"DATA_DIR": tmp, "DB_PATH": str(Path(tmp) / "t.db")})
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["missing"], [])
            self.assertEqual(payload["after"] - payload["before"], 0)
            self.assertFalse(payload["runtime"]["started"])
            self.assertIn("pi-task-runner", payload["runtime"]["managed_threads"])
            self.assertIn("shadow-observer", payload["runtime"]["managed_threads"])

    def test_health_endpoint_does_not_start_pi(self):
        # /health must be passive: reading it never launches the Pi process.
        from fastapi.testclient import TestClient
        from xianyu_agent import server

        with TestClient(server.app) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("running", body["pi"])
        # passive status reports process state without spawning one
        self.assertIsNotNone(server.pi_runtime.status(active=False))


if __name__ == "__main__":
    unittest.main()
