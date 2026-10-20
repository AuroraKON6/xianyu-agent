"""R05 acceptance tests: single stage-advancer per chain.

* every chain has exactly one owner (python/n8n/manual/disabled) and
  loops are only registered for python-owned chains;
* a claim held by a live pid is never stolen — concurrent ticks, a
  redeployed process or a timeout-retry cannot double-advance;
* release frees the barrier and the stopped holder never re-claims;
* stale claims (dead pid) are reclaimable;
* github re-scanning stays research-only (no path into work orders).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

from xianyu_agent.run_coordination import (  # noqa: E402
    CHAIN_SPECS,
    ChainClaimError,
    ChainCoordination,
)


class OwnerConfigTests(unittest.TestCase):
    def test_default_owners(self):
        coord = ChainCoordination(Path(tempfile.mkdtemp()))
        self.assertEqual(coord.owner_for("github-discovery"), "python")
        self.assertEqual(coord.owner_for("message-import"), "python")
        self.assertEqual(coord.owner_for("radar"), "disabled")

    def test_env_override_selects_single_owner(self):
        coord = ChainCoordination(Path(tempfile.mkdtemp()))
        saved = os.environ.get("XIANYU_CHAIN_OWNER_MESSAGE_IMPORT")
        try:
            os.environ["XIANYU_CHAIN_OWNER_MESSAGE_IMPORT"] = "n8n"
            self.assertEqual(coord.owner_for("message-import"), "n8n")
        finally:
            if saved is None:
                os.environ.pop("XIANYU_CHAIN_OWNER_MESSAGE_IMPORT", None)
            else:
                os.environ["XIANYU_CHAIN_OWNER_MESSAGE_IMPORT"] = saved

    def test_invalid_owner_refused(self):
        coord = ChainCoordination(Path(tempfile.mkdtemp()))
        saved = os.environ.get("XIANYU_CHAIN_OWNER_AGENT_SCHEDULER")
        try:
            os.environ["XIANYU_CHAIN_OWNER_AGENT_SCHEDULER"] = "both"
            with self.assertRaises(ValueError):
                coord.owner_for("agent-scheduler")
        finally:
            if saved is None:
                os.environ.pop("XIANYU_CHAIN_OWNER_AGENT_SCHEDULER", None)
            else:
                os.environ["XIANYU_CHAIN_OWNER_AGENT_SCHEDULER"] = saved

    def test_github_chain_is_research_only(self):
        self.assertEqual(CHAIN_SPECS["github-discovery"]["kind"], "research")
        source = (PROJECT_ROOT / "xianyu_agent" / "github_discovery.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "work_order",
            source,
            "github discovery must never create work orders (physical chain)",
        )


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coord-"))
        self.coord = ChainCoordination(self.tmp)

    def test_second_claim_same_process_refused(self):
        self.coord.claim("github-discovery")
        with self.assertRaises(ChainClaimError):
            self.coord.claim("github-discovery")

    def test_live_holder_blocks_another_instance(self):
        # a second coordinator over the same state dir (= another process)
        other = ChainCoordination(self.tmp)
        self.coord.claim("message-import")
        with self.assertRaises(ChainClaimError):
            other.claim("message-import")

    def test_release_frees_barrier_and_release_is_idempotent(self):
        other = ChainCoordination(self.tmp)
        self.coord.claim("message-import")
        self.assertTrue(self.coord.release("message-import"))
        self.assertFalse(self.coord.release("message-import"))  # idempotent
        other.claim("message-import")  # barrier freed

    def test_stale_dead_pid_claim_is_reclaimed(self):
        lock = self.tmp / "coordination" / "product-discovery.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps(
                {
                    "chain": "product-discovery",
                    "pid": 4000000,  # not a live pid
                    "claimed_at": time.time() - 3600,
                    "heartbeat_at": time.time() - 3600,
                }
            ),
            encoding="utf-8",
        )
        claim = self.coord.claim("product-discovery")
        self.assertEqual(claim["pid"], os.getpid())

    def test_fresh_dead_pid_claim_is_reclaimed(self):
        lock = self.tmp / "coordination" / "product-discovery.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps(
                {
                    "chain": "product-discovery",
                    "pid": 4000000,
                    "claimed_at": time.time(),
                    "heartbeat_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        self.coord.claim("product-discovery")  # fresh but dead -> reclaimable


class ServerLoopGatingTests(unittest.TestCase):
    def _child(self, code, extra_env=None):
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

    def test_default_registers_python_owned_loops_and_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = (
                "import json;"
                "import xianyu_agent.server as s;"
                "print(json.dumps({'loops': s.runtime.status['loops'],"
                "'threads': s.runtime.status['managed_threads']}))"
            )
            result = self._child(code, extra_env={"DATA_DIR": tmp})
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            for chain in (
                "github-discovery",
                "product-discovery",
                "agent-scheduler",
                "message-import",
            ):
                self.assertIn(chain, payload["loops"])
            self.assertIn("pi-task-runner", payload["threads"])

    def test_foreign_owner_removes_loop_and_runner_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = (
                "import json;"
                "import xianyu_agent.server as s;"
                "print(json.dumps({'loops': s.runtime.status['loops'],"
                "'threads': s.runtime.status['managed_threads']}))"
            )
            result = self._child(
                code,
                extra_env={
                    "DATA_DIR": tmp,
                    "XIANYU_CHAIN_OWNER_MESSAGE_IMPORT": "n8n",
                    "XIANYU_CHAIN_OWNER_PI_TASK_RUNNER": "manual",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertNotIn("message-import", payload["loops"])
            self.assertIn("github-discovery", payload["loops"])
            self.assertNotIn("pi-task-runner", payload["threads"])


if __name__ == "__main__":
    unittest.main()
