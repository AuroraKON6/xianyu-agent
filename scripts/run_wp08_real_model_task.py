"""WP08 minimal: one real-model no-write task through the job store.

Runs a single read-only analysis task on the configured model provider
(DeepSeek-compatible API from .env), persisted through the real
AgentJobStore and DecisionRecordRepository under the live data dir so
the console can read it (/commerce/agent-jobs, /commerce/decisions).

Mock runs and this real run are reported separately; this script
refuses to pretend success when the model is not configured or errors
(exit code 1 with the failure).

Usage:
    .venv/Scripts/python.exe scripts/run_wp08_real_model_task.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xianyu_agent.agent_jobs import AgentJobStore  # noqa: E402
from xianyu_agent.config import Settings  # noqa: E402
from xianyu_agent.decision_records import decision_from_job  # noqa: E402
from xianyu_agent.llm import DeepSeekClient  # noqa: E402

SYSTEM_PROMPT = (
    "你是闲鱼经营系统的质检子任务。只做分类，不做任何平台动作。"
    "把供应商备注分类为 JSON：{\"topic\": \"运费|库存|规格|其他\", \"urgent\": true|false, \"reason\": \"一句话\"}"
)
USER_PROMPT = "供应商备注：「这批工作台本周五前只有 8 张现货，运费到付，具体金额发货时确认。」"


def main() -> int:
    settings = Settings.from_env()
    # Pin the DeepSeek channel: the configured MiMo key currently returns
    # 401 (verified 2026-09-18). The client prefers MiMo when both keys
    # exist, so blank it for this run and report the pin honestly.
    # object.__setattr__ works for the frozen Settings dataclass and for
    # test doubles alike.
    object.__setattr__(settings, "mimo_api_key", "")
    client = DeepSeekClient(settings)
    if not client.enabled:
        print("REAL_RUN_FAILED: model provider not enabled (missing API key/base URL)")
        return 1

    job_store = AgentJobStore(settings.data_dir)
    repos = __import__(
        "xianyu_agent.commerce", fromlist=["JsonCommerceRepositories"]
    ).JsonCommerceRepositories(settings.data_dir)

    from xianyu_agent.commerce.contracts import utc_now

    run_key = f"wp08-smoke-{settings.deepseek_model}-{utc_now().strftime('%Y%m%d%H%M%S')}"
    job = job_store.submit(
        role="supplier_note_triage",
        goal_id="wp08-real-model-smoke",
        input_refs=["supplier_note:demo"],
        priority=5,
        idempotency_key=run_key,
        model_budget={"max_tokens": 500},
    )
    claimed = job_store.claim_next(worker_id="wp08_script")
    if claimed is None or claimed["record_id"] != job["record_id"]:
        print(f"REAL_RUN_FAILED: could not claim the submitted job (job={job['record_id']})")
        return 1
    job_store.start(job["record_id"], worker_id="wp08_script", fencing_token=claimed["fencing_token"])

    try:
        result = client.chat_json(SYSTEM_PROMPT, USER_PROMPT, temperature=0.0, max_tokens=300)
    except Exception as exc:  # noqa: BLE001 - report real failures verbatim
        job_store.fail(
            job["record_id"],
            worker_id="wp08_script",
            fencing_token=claimed["fencing_token"],
            error=f"model call failed: {exc}",
            retryable=False,
        )
        print(f"REAL_RUN_FAILED: {exc}")
        return 1

    if not isinstance(result, dict):
        job_store.fail(
            job["record_id"],
            worker_id="wp08_script",
            fencing_token=claimed["fencing_token"],
            error="model returned unparsable JSON",
            retryable=False,
        )
        print("REAL_RUN_FAILED: unparsable model output")
        return 1

    decision = decision_from_job(
        claimed,
        summary=f"supplier note triaged as topic={result.get('topic')!r} urgent={result.get('urgent')!r}",
        decision_type="supplier_note_triage",
        recommendation=str(result.get("reason") or ""),
        unknowns=["token usage not surfaced by the current client; cost unknown"],
        model_version=str(settings.deepseek_model),
        result=result,
    )
    stored_decision = repos.decisions.append(decision)

    done = job_store.complete(
        job["record_id"],
        worker_id="wp08_script",
        fencing_token=claimed["fencing_token"],
        result_ref=f"decision:{stored_decision['record_id']}",
        cost={"model": str(settings.deepseek_model), "tokens": None, "note": "usage not surfaced"},
    )

    print("REAL_RUN_OK (no platform write performed)")
    print(json.dumps({
        "job_id": done["record_id"],
        "job_status": done["status"],
        "model": settings.deepseek_model,
        "model_output": result,
        "decision_id": stored_decision["record_id"],
        "read_via": ["/commerce/agent-jobs", "/commerce/decisions"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
