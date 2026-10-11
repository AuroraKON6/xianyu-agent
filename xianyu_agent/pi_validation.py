"""One isolated demo task through the application HTTP and Pi task chain.

Offline is the default. Real mode requires --allow-model; neither mode may use
an existing run directory, a live directory, or an already-running server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CONTROL_HEADER = "X-Pi-Validation-Run"
DEMO_QUOTES = (
    "sku,name,price,unit,moq,stock,freight,spec,brand,condition,notes\n"
    "DEMO-1,Demo table,150,pc,1,10,15,120x60x85cm,DEMO,new,source=demo\n"
    "DEMO-2,Demo rack,200,pc,1,5,,120x60x180cm,DEMO,new,source=demo\n"
)


def validate_run_root(path: Path, *, fresh: bool = True) -> Path:
    root = Path(path).resolve()
    live = (ROOT / "data" / "live").resolve()
    if root == live or live in root.parents or root in live.parents:
        raise ValueError("validation must not use live storage or its ancestors")
    if fresh and root.exists():
        raise ValueError("validation needs a new directory; existing data cannot be reused")
    return root


def _write_json(path: Path, value) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def _live_hashes() -> dict:
    live = ROOT / "data" / "live"
    return {str(path.relative_to(live)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(live.rglob("*")) if path.is_file()}


def build_worker_env(root: Path, run_id: str, mode: str, base_env=None) -> dict:
    env = dict(os.environ if base_env is None else base_env)
    env.update({
        "DATA_DIR": str(root / "data"), "DB_PATH": str(root / "data" / "validation.db"),
        "XIANYU_ADAPTER": "stub", "PI_VALIDATION_RUN_ID": run_id,
        "PI_VALIDATION_ALLOW_MODEL": "1" if mode == "real" else "0",
        "PI_ENABLED": "1" if mode == "real" else "0",
        "PI_REQUEST_TIMEOUT_SECONDS": "120", "PI_JOB_TOKEN_BUDGET": "20000",
        "PI_DAILY_TOKEN_BUDGET": "20000", "SHADOW_OBSERVER_ENABLED": "0",
        "GITHUB_DISCOVERY_ENABLED": "0", "DISCOVERY_ENABLED": "0", "AUTO_REPLY_ENABLED": "0",
        "PI_USAGE_RECONCILE_TOKEN_SHA256": "", "PI_USAGE_RECONCILE_OPERATOR": "",
        "BROWSER_USER_DATA_DIR": str(root / "browser-unused"),
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
    })
    if mode != "real":
        env["DEEPSEEK_API_KEY"] = ""
    return env


def check_identity(actual: dict, expected: dict) -> None:
    mismatched = [key for key, value in expected.items() if actual.get(key) != value]
    if mismatched:
        raise RuntimeError(f"validation server identity mismatch ({', '.join(mismatched)}); refusing to submit a job")


def _json_request(session, method: str, base: str, path: str, **kwargs):
    response = session.request(method, base + path, timeout=kwargs.pop("timeout", 15), **kwargs)
    if response.status_code != 200:
        raise RuntimeError(f"validation request {method} {path} returned {response.status_code}")
    return response.json()


def _check_evidence(evidence: dict, job_id: str, data_dir: Path) -> dict:
    job = evidence.get("job") or {}
    decision = evidence.get("latest_decision") or {}
    saved = evidence.get("evidence_file") or {}
    if job.get("record_id") != job_id or job.get("status") != "succeeded":
        raise RuntimeError("submitted job did not succeed")
    if decision.get("record_id") not in evidence.get("decision_ids", []):
        raise RuntimeError("valid decision missing")
    inputs = saved.get("inputs") or {}
    offers = inputs.get("offers") or []
    if inputs.get("committed_offer_count") != 2 or len(offers) != 2:
        raise RuntimeError("nonempty demo quotes did not reach the job")
    if any((offer.get("attributes") or {}).get("notes") != "source=demo" for offer in offers):
        raise RuntimeError("quote provenance is not source=demo")
    digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if digest != saved.get("inputs_sha256"):
        raise RuntimeError("input evidence hash mismatch")
    path = Path(saved.get("path") or "").resolve()
    if data_dir.resolve() not in path.parents or not path.is_file():
        raise RuntimeError("evidence file outside isolated data directory or missing")
    persisted = json.loads(path.read_text(encoding="utf-8"))
    if persisted.get("inputs") != inputs:
        raise RuntimeError("evidence endpoint differs from persisted inputs")
    total = (saved.get("usage") or {}).get("totalTokens")
    if type(total) is not int or total < 0:
        raise RuntimeError("model consumption remains unknown; validation not accepted")
    return persisted


def run_validation(path: Path, *, mode: str = "offline", allow_model: bool = False) -> dict:
    if mode not in ("offline", "real"):
        raise ValueError("mode must be offline or real")
    if mode == "real" and not allow_model:
        raise ValueError("real mode requires explicit --allow-model")
    root = validate_run_root(path)
    before = _live_hashes()
    root.mkdir(parents=True, exist_ok=False)
    run_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    spec = {"run_id": run_id, "mode": mode, "session_id": session_id,
            "data_dir": str(root / "data"), "source": "demo"}
    _write_json(root / "run.json", spec)
    (root / "quotes.csv").write_text(DEMO_QUOTES, encoding="utf-8")
    report = dict(spec, passed=False, real_model_calls=0, forced_shutdown=False)
    env = build_worker_env(root, run_id, mode)
    env["PI_JOB_SESSION_ID"] = session_id
    base = ""
    process = None
    with requests.Session() as session, (root / "worker.log").open("w", encoding="utf-8") as log:
        session.trust_env = False
        session.headers[CONTROL_HEADER] = run_id
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "xianyu_agent.pi_validation", "--worker", "--data-dir", str(root)],
                cwd=str(ROOT), env=env, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 45
            expected = {key: spec[key] for key in ("run_id", "mode", "session_id", "data_dir")}
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("owned validation worker exited; inspect isolated worker.log")
                ready = root / "ready.json"
                if ready.exists():
                    identity = json.loads(ready.read_text(encoding="utf-8"))
                    # Windows venv redirectors may launch the actual interpreter
                    # as their direct child. Verify lineage before trusting HTTP.
                    if process.pid not in (identity.get("pid"), identity.get("parent_pid")):
                        raise RuntimeError("validation worker is not the owned process or its direct child")
                    expected.update(pid=identity["pid"], parent_pid=identity["parent_pid"])
                    check_identity(identity, expected)
                    base = f"http://127.0.0.1:{int(identity['port'])}"
                    try:
                        actual = _json_request(session, "GET", base, "/__pi_validation/identity", timeout=1)
                        check_identity(actual, expected)
                        report["server_port"] = identity["port"]
                        break
                    except requests.RequestException:
                        pass
                time.sleep(0.05)
            else:
                raise RuntimeError("owned validation server did not become ready")

            token = _json_request(session, "POST", base, "/__pi_validation/probe-token")["token"]
            headers = {"X-Xianyu-Job-Token": token}
            report["gate_probe"] = {
                "read": session.get(base + "/health", headers=headers, timeout=5).status_code,
                "write": session.post(base + "/commerce/pi-tasks/quote-review", json={}, headers=headers, timeout=5).status_code,
                "unlisted": session.get(base + "/commerce/pi-usage/unresolved", headers=headers, timeout=5).status_code,
            }
            if report["gate_probe"] != {"read": 200, "write": 403, "unlisted": 403}:
                raise RuntimeError("read-only gate validation failed")
            preview = _json_request(session, "POST", base, "/commerce/supplier-imports/preview", json={
                "kind": "csv", "filename": "source-demo.csv", "content": DEMO_QUOTES})
            confirmed = _json_request(session, "POST", base, f"/commerce/supplier-imports/{preview['preview_id']}/confirm", json={
                "content_sha256": preview["content_sha256"], "revision": preview["revision"],
                "supplier": "DEMO-NOT-A-REAL-SUPPLIER", "operator": "demo-validation",
                "include_needs_confirmation": True})
            report["quote_count"] = len(confirmed["snapshots"])
            submitted = _json_request(session, "POST", base, "/commerce/pi-tasks/quote-review", json={"idempotency_key": run_id})
            job_id = report["submitted_job_id"] = submitted["record_id"]
            # Exactly one task execution request; never retry on HTTP/provider failure.
            outcome = _json_request(session, "POST", base, "/commerce/pi-tasks/run-next", timeout=160)
            report["outcome"] = outcome
            if outcome.get("job_id") != job_id:
                raise RuntimeError("worker executed an unexpected job")
            evidence = _json_request(session, "GET", base, f"/commerce/pi-tasks/{job_id}/evidence")
            report["evidence"] = evidence
            persisted = _check_evidence(evidence, job_id, root / "data")
            report["reported_model_turns"] = persisted.get("turns")
            if mode == "real" and not any(event.get("name") == "xianyu_business_snapshot" and event.get("ok") is True
                                           for event in persisted.get("tool_calls", [])):
                raise RuntimeError("real Pi read-only tool evidence missing")
            report["passed"] = True
        except Exception as exc:
            report["failure"] = f"{type(exc).__name__}: {exc}"
        finally:
            if process is not None:
                if process.poll() is None and base:
                    try:
                        _json_request(session, "POST", base, "/__pi_validation/shutdown", timeout=3)
                    except (requests.RequestException, RuntimeError):
                        pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    report["forced_shutdown"] = True
                    process.kill()
                    process.wait(timeout=10)
                report["worker_exit_code"] = process.returncode
    after = _live_hashes()
    report["live_unchanged"] = before == after
    report["live_file_count"] = len(before)
    if mode == "real":
        # A pending intent means a request may have gone out; do not report it as zero.
        usage_path = root / "data" / "commerce" / "model_usage.json"
        entries = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else []
        report["real_model_calls"] = 0 if not entries else report.get("reported_model_turns")
        if any(e.get("total_tokens") is None for e in entries):
            report["real_model_calls"] = None
        report["model_attempt_intents"] = len(entries)
    report["passed"] = bool(report["passed"] and report["live_unchanged"]
                            and not report["forced_shutdown"] and report.get("worker_exit_code") == 0)
    _write_json(root / "live-hashes.json", {"before": before, "after": after})
    _write_json(root / "validation.json", report)
    return report


def _worker(root: Path) -> None:
    root = validate_run_root(root, fresh=False)
    spec = json.loads((root / "run.json").read_text(encoding="utf-8"))
    if spec["run_id"] != os.environ.get("PI_VALIDATION_RUN_ID") or spec["data_dir"] != str(root / "data"):
        raise ValueError("worker identity does not match its isolated run")
    real = spec["mode"] == "real"
    if real and os.environ.get("PI_VALIDATION_ALLOW_MODEL") != "1":
        raise ValueError("worker model enablement missing")
    listener = socket.socket()
    if os.name == "nt":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    os.environ["XIANYU_API_BASE_URL"] = base
    os.environ["PI_JOB_SESSION_ID"] = spec["session_id"]
    from fastapi.responses import JSONResponse
    import uvicorn
    from . import server as application
    from .pi_runtime import PiRpcClient

    if application.settings.data_dir.resolve() != (root / "data").resolve():
        raise RuntimeError("application escaped validation data directory")
    application.db_manager.init_db()
    workspace = root / "pi_workspace"
    (workspace / ".pi").mkdir(parents=True)
    _write_json(workspace / ".pi" / "settings.json", {
        "retry": {"enabled": False, "maxRetries": 0, "provider": {"maxRetries": 0}},
        "compaction": {"enabled": False}, "enableSkillCommands": False})

    class IsolatedPiClient(PiRpcClient):
        def _build_command(self):
            return (*super()._build_command(), "--no-extensions", "-e",
                    str(ROOT / ".pi" / "extensions" / "xianyu-ops.ts"), "--no-skills", "--no-prompt-templates")

        def prompt(self, message, context=None, publish=False):
            return super().prompt(
                "This is an isolated source=demo validation. First call xianyu_business_snapshot once. "
                "Then review only the supplied demo quote inputs. Return the requested JSON, "
                "without claiming real supplier verification or performing any trading action.\n" + message,
                context=context, publish=False,
            )

    clients = []

    class OfflineClient:
        def prompt(self, message, context=None):
            findings = [{"sku": offer["sku"], "field": "freight", "severity": "block",
                         "note": "source=demo; freight missing"}
                        for offer in context["inputs"]["offers"] if offer["freight"].get("state") != "known"]
            return {"ok": True, "result": {
                "message": json.dumps({"findings": findings, "summary": "source=demo; offline FakePi only"}),
                "model": "offline-fake", "provider": "offline", "session_id": spec["session_id"],
                "tool_calls": [], "turns": 1, "usage": {"input": 10, "output": 5, "totalTokens": 15}}}

        def stop(self):
            pass

    def factory(token_env):
        if not real:
            return OfflineClient()
        env = dict(os.environ, **token_env, PI_SESSION_ID=spec["session_id"])
        original = PiRpcClient.from_env(ROOT, root / "data", env=env)
        if not original.config.api_key_configured:
            raise RuntimeError("model credential is not configured")
        config = replace(original.config, project_root=workspace, tools=("xianyu_business_snapshot",))
        client = IsolatedPiClient(config, base_env=env)
        clients.append(client)
        return client

    application._pi_job_client_factory = factory
    application.pi_task_runner._max_tool_calls = 8
    identity = dict(spec, pid=os.getpid(), parent_pid=os.getppid(), port=listener.getsockname()[1])
    app = application.app
    # No application startup schedulers in the validation process. SQLite is
    # initialized above; the shadow thread is disabled before server import.
    service = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning", access_log=False))

    @app.middleware("http")
    async def validation_boundary(request, call_next):
        if request.headers.get("X-Xianyu-Job-Token"):
            if request.method != "GET" or request.url.path.startswith("/__pi_validation/"):
                return JSONResponse(status_code=403, content={"detail": "validation jobs are read-only"})
        elif request.headers.get(CONTROL_HEADER) != spec["run_id"]:
            return JSONResponse(status_code=403, content={"detail": "validation owner required"})
        return await call_next(request)

    @app.get("/__pi_validation/identity")
    def get_identity():
        return identity

    @app.post("/__pi_validation/probe-token")
    def probe_token():
        return {"token": application.pi_job_gate.issue("offline-gate-probe", max_tool_calls=2)}

    @app.post("/__pi_validation/shutdown")
    def shutdown():
        service.should_exit = True
        return {"stopping": spec["run_id"]}

    _write_json(root / "ready.json", identity)
    try:
        service.run(sockets=[listener])
    finally:
        for client in clients:
            client.stop()
        application.pi_runtime.stop()
        listener.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="new isolated run directory (never data/live)")
    parser.add_argument("--mode", choices=("offline", "real"), default="offline")
    parser.add_argument("--allow-model", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        _worker(args.data_dir)
        return 0
    try:
        report = run_validation(args.data_dir, mode=args.mode, allow_model=args.allow_model)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"passed": report["passed"], "mode": report["mode"],
                      "real_model_calls": report["real_model_calls"],
                      "report": str(args.data_dir.resolve() / "validation.json")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
