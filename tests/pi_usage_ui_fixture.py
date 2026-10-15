"""Temporary-ledger UI fixture: real reconciliation routes, no model routes.

The unrelated legacy app entrypoint is intentionally empty. The page markup,
styles and reconciliation script are the real console assets.
"""
import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

from xianyu_agent.agent_jobs import AgentJobStore
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.pi_jobs import PiTaskRunner
from xianyu_agent.pi_usage_reconciliation import build_usage_reconciliation_router


TOKEN = "ui-fixture-only-not-a-real-credential-123456789"
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def build_fixture(root):
    runner = PiTaskRunner(AgentJobStore(root), JsonCommerceRepositories(root), data_dir=root)
    runner.start()
    job = runner.submit_quote_review_job()
    claimed = runner._jobs.claim_next(worker_id="ui-fixture")
    runner._jobs.start(job["record_id"], worker_id="ui-fixture", fencing_token=claimed["fencing_token"])
    runner.model_usage.begin_attempt(day="2026-09-22", job_id=job["record_id"], attempt=1)
    runner.model_usage.record(day="2026-09-22", job_id=job["record_id"], attempt=1, total_tokens=None)
    runner._jobs.fail(job["record_id"], worker_id="ui-fixture", fencing_token=claimed["fencing_token"],
                      error="isolated fixture: provider usage unknown", retryable=False)
    app = FastAPI()
    app.include_router(build_usage_reconciliation_router(
        runner, token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(), operator="ui-fixture-operator"))

    @app.get("/console")
    def console():
        return FileResponse(WEB_ROOT / "console.html")

    @app.get("/console/style.css")
    def styles():
        return FileResponse(WEB_ROOT / "style.css")

    @app.get("/console/pi-usage.js")
    def script():
        return FileResponse(WEB_ROOT / "pi-usage.js")

    @app.get("/console/app.js")
    @app.get("/console/intake-confirmation.js")
    def unrelated_entrypoint():
        return Response(content="", media_type="application/javascript")

    return app, runner, job
