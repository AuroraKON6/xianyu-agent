"""Real Pi run: one supplier-quote-gap review through the actual Pi RPC.

This is the REAL layer (mock runs live in tests/test_pi_jobs.py and are
counted separately). Boots the server on the live data dir, submits one
quote-review job, executes it through PiRpcClient (real pi subprocess,
real model), and archives the full evidence chain:

    job -> pi session/model/tool events -> structured findings ->
    decision record -> panel evidence endpoint

Usage:
    .venv/Scripts/python.exe scripts/run_pi_quote_review_real.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "acceptance"
PORT = 8788  # the extension's api_base_url default; tool calls land here
BASE = f"http://127.0.0.1:{PORT}"


def wait_health(session, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if session.get(f"{BASE}/health", timeout=2).ok:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def current_env_settings() -> Dict[str, str]:
    """Read DEEPSEEK_* from .env directly.

    The shell may still carry stale user-level env vars from before the
    key rotation (settings loading gives os.environ precedence), so the
    real run pins the values from the project .env explicitly.
    """
    values: Dict[str, str] = {}
    env_path = ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.startswith("DEEPSEEK_") and value.strip():
            values[key] = value.strip()
    return values


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session = requests.Session()
    session.trust_env = False

    env = dict(os.environ)
    env.update(
        {
            "DATA_DIR": str(ROOT / "data" / "live"),
            "DB_PATH": str(ROOT / "data" / "live" / "xianyu_data.db"),
            "XIANYU_ADAPTER": "stub",
            "XIANYU_API_BASE_URL": BASE,
        }
    )
    env.update(current_env_settings())  # pin the current key/model over stale shell vars
    server = subprocess.Popen(
        [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "uvicorn",
            "xianyu_agent.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_health(session):
            print("REAL_RUN_FAILED: server did not become healthy")
            return 1
        submit = session.post(f"{BASE}/commerce/pi-tasks/quote-review", json={"idempotency_key": f"real-{stamp}"}, timeout=15)
        if submit.status_code != 200:
            print("REAL_RUN_FAILED: submit", submit.status_code, submit.text[:200])
            return 1
        job_id = submit.json()["record_id"]
        print(f"job submitted: {job_id}; executing through real Pi (this can take a while)...")
        run = session.post(f"{BASE}/commerce/pi-tasks/run-next", timeout=420)
        run_body = run.json()
        # run-next claims the OLDEST queued job (FIFO); evidence follows the
        # job that actually executed, not necessarily the one just submitted
        executed_job_id = run_body.get("job_id") or job_id
        evidence = session.get(f"{BASE}/commerce/pi-tasks/{executed_job_id}/evidence", timeout=15).json()
        evidence["submitted_job_id"] = job_id
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    evidence["real_run"] = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "run_outcome": run_body,
        "layer": "REAL (PiRpcClient subprocess; mock runs counted separately in tests)",
        "server_port": PORT,
    }
    path = OUT / f"PI_REAL_RUN_{stamp}.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ("real_run", "pi", "decision_ids")}, ensure_ascii=False, indent=2))
    latest = evidence.get("latest_decision") or {}
    print("summary:", latest.get("summary"))
    print("findings:", len((latest.get("result") or {}).get("findings") or []))
    print("evidence file:", path)
    return 0 if run_body.get("outcome") == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
