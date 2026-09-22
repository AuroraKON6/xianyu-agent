"""Pi job execution chain (stage: persistent tasks -> real Pi -> structured
suggestion -> decision record -> panel traceability).

First role: ``supplier_quote_gap_review`` -- review the committed supplier
offer snapshots and report missing/unknown fields per SKU as structured
findings. The model SUGGESTS; nothing here changes business state: the
only writes are AgentJob records, DecisionRecords and audit fields.

Program-enforced boundaries (constraints live in code, not prompts):
- Run boundary: one runner instance per process; ``start()`` refuses a
  second start; after ``stop()`` no further claims are made.
- Read-only tools: the Pi subprocess of a job carries a per-job gate
  token (env passthrough -> extension header). The server middleware
  rejects any request that is not GET on an allowlisted read path and
  counts every allowed call; over ``max_tool_calls`` -> rejected.
- Lease/deadline: a job past its deadline never starts; the lease
  covers the request timeout; heartbeats renew before result handling.
- Late results: completion is fenced by worker + fencing token, so a
  result arriving after cancel/lease loss is discarded, not applied.
- Retries: a decision is written at most once per (job, attempt); a
  crash between decision and completion is healed by
  ``reconcile_task_decisions`` completing the job with the stored
  decision instead of re-running it.
- Costs: usage is recorded as reported; missing usage stays ``None``
  and is rendered "unknown", never zero.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .agent_jobs import AgentJobStore, FencingError, JOB_SUCCEEDED
from .commerce.contracts import build_decision_record, new_id, utc_now
from .commerce.repositories import DecisionRecordRepository, LockedJsonList, OfferSnapshotRepository
from .decision_records import decision_from_job

ROLE_SUPPLIER_QUOTE_REVIEW = "supplier_quote_gap_review"
ROLE_MATCH_AUDIT = "match_audit"
ROLE_PRICING_REVIEW = "pricing_review"
ROLE_OPPORTUNITY_SCREENING = "opportunity_screening"
JOB_WORKER_ID = "pi_worker_1"

# Read-only route allowlist for job sessions (GET only, prefix match).
# Anything that creates/approves/publishes/purchases is absent on purpose.
READONLY_GET_PREFIXES: Sequence[str] = (
    "/health",
    "/accounts",
    "/resale/candidates",
    "/commercial-trials/active",
    "/work-orders",
    "/dashboard/opc-daily",
    "/commerce/offers",
    "/commerce/data-providers",
    "/agents/pi/status",
)

JOB_TOKEN_HEADER = "X-Xianyu-Job-Token"

DEFAULT_PER_JOB_TOKEN_BUDGET = 50_000
DEFAULT_DAILY_TOKEN_BUDGET = 200_000


class ModelUsageLedger:
    """Persistent per-day model token accounting (program-enforced budget).

    Every successful job run records its reported total tokens; before a
    job runs, today's total is checked against the daily budget. Missing
    usage stays missing -- budgets never treat unknown as zero.
    """

    def __init__(self, data_dir) -> None:
        self._store = LockedJsonList(Path(data_dir) / "commerce" / "model_usage.json")

    def used_today(self, *, day: str) -> int:
        return sum(
            int(e.get("total_tokens") or 0)
            for e in self._store.find(lambda r: str(r.get("business_day")) == day)
        )

    def entries_today(self, *, day: str) -> List[Dict[str, Any]]:
        """Every attempt entry for the day, including unknown-usage ones."""
        return self._store.find(lambda r: str(r.get("business_day")) == day)

    def record(self, *, day: str, job_id: str, total_tokens: Optional[int], model: str = "") -> Dict[str, Any]:
        entry = {
            "business_day": day,
            "job_id": job_id,
            "total_tokens": int(total_tokens) if total_tokens is not None else None,
            "model": model,
            "recorded_at": utc_now().isoformat(),
        }
        return self._store.append(entry)


class JobPolicyError(RuntimeError):
    """A job-session request violated the read-only/tool-cap policy."""


class ReadOnlyJobGate:
    """Per-job tokens enforcing the read-only tool subset and call cap.

    Enforced by the server middleware on every request carrying
    ``X-Xianyu-Job-Token``; requests without the token (operator
    sessions) are untouched.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: Dict[str, Dict[str, Any]] = {}

    def issue(self, job_id: str, *, max_tool_calls: int) -> str:
        token = f"jobtok_{uuid.uuid4().hex[:20]}"
        with self._lock:
            self._tokens[token] = {"job_id": job_id, "max_tool_calls": int(max_tool_calls), "calls": 0}
        return token

    def release(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def inspect(self, token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._tokens.get(token)
            return dict(record) if record else None

    def authorize(self, token: str, *, method: str, path: str) -> Dict[str, Any]:
        """Authorize one request; raises JobPolicyError on violation."""
        with self._lock:
            record = self._tokens.get(token)
            if record is None:
                raise JobPolicyError("unknown or expired job token")
            if str(method).upper() != "GET":
                raise JobPolicyError(f"job sessions are read-only: {method} {path} rejected")
            if not any(path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?") for prefix in READONLY_GET_PREFIXES):
                raise JobPolicyError(f"path not in the read-only allowlist: {path}")
            if record["calls"] >= record["max_tool_calls"]:
                raise JobPolicyError(
                    f"tool call cap reached ({record['max_tool_calls']}) for job {record['job_id']}"
                )
            record["calls"] += 1
            return dict(record)


# ---------------------------------------------------------------------------
# Structured output contract for the quote-gap review role
# ---------------------------------------------------------------------------

REVIEW_OUTPUT_KEYS = ("findings", "summary")


def validate_review_output(raw_text: str) -> Dict[str, Any]:
    """Parse and validate the model's structured review output.

    Accepts a JSON object (optionally inside a fenced code block) with
    ``findings`` (list of {sku, field, severity, note}) and ``summary``.
    Raises ValueError with a specific message otherwise.
    """
    text = _strip_code_fence(raw_text)
    payload = _load_json_object(text)
    for key in REVIEW_OUTPUT_KEYS:
        if key not in payload:
            raise ValueError(f"model output missing required key: {key}")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    for item in findings:
        if not isinstance(item, dict) or not item.get("sku") or not item.get("field"):
            raise ValueError("each finding needs sku and field")
        if str(item.get("severity") or "").lower() not in ("info", "warn", "block"):
            raise ValueError("finding severity must be info|warn|block")
    payload["summary"] = str(payload.get("summary") or "")[:500]
    return payload


MATCH_AUDIT_OUTPUT_KEYS = ("audits", "summary")


def validate_match_audit_output(raw_text: str) -> Dict[str, Any]:
    text = _strip_code_fence(raw_text)
    payload = _load_json_object(text)
    for key in MATCH_AUDIT_OUTPUT_KEYS:
        if key not in payload:
            raise ValueError(f"model output missing required key: {key}")
    audits = payload.get("audits")
    if not isinstance(audits, list):
        raise ValueError("audits must be a list")
    for item in audits:
        if not isinstance(item, dict) or not item.get("match_id"):
            raise ValueError("each audit needs match_id")
        if str(item.get("verdict") or "").lower() not in ("confirm", "investigate", "reject"):
            raise ValueError("audit verdict must be confirm|investigate|reject")
    payload["summary"] = str(payload.get("summary") or "")[:500]
    return payload


PRICING_REVIEW_OUTPUT_KEYS = ("reviews", "summary")


def validate_pricing_review_output(raw_text: str) -> Dict[str, Any]:
    text = _strip_code_fence(raw_text)
    payload = _load_json_object(text)
    for key in PRICING_REVIEW_OUTPUT_KEYS:
        if key not in payload:
            raise ValueError(f"model output missing required key: {key}")
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("reviews must be a list")
    for item in reviews:
        if not isinstance(item, dict) or not item.get("opportunity_id"):
            raise ValueError("each review needs opportunity_id")
        if str(item.get("verdict") or "").lower() not in ("advance", "hold", "reject"):
            raise ValueError("review verdict must be advance|hold|reject")
    payload["summary"] = str(payload.get("summary") or "")[:500]
    return payload


SCREENING_OUTPUT_KEYS = ("screenings", "summary")


def validate_screening_output(raw_text: str) -> Dict[str, Any]:
    text = _strip_code_fence(raw_text)
    payload = _load_json_object(text)
    for key in SCREENING_OUTPUT_KEYS:
        if key not in payload:
            raise ValueError(f"model output missing required key: {key}")
    screenings = payload.get("screenings")
    if not isinstance(screenings, list):
        raise ValueError("screenings must be a list")
    for item in screenings:
        if not isinstance(item, dict) or not item.get("opportunity_id"):
            raise ValueError("each screening needs opportunity_id")
        if str(item.get("verdict") or "").lower() not in ("keep", "watch", "drop"):
            raise ValueError("screening verdict must be keep|watch|drop")
    payload["summary"] = str(payload.get("summary") or "")[:500]
    return payload


def _strip_code_fence(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def _load_json_object(text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("model output must be a JSON object")
    return payload


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def decision_validity(job: Mapping[str, Any], all_decisions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Classify decisions of one job as valid / stale / invalidated.

    Validity derives from the JOB RECORD (status == succeeded,
    result_ref == decision:{id}, attempt match) -- invalidation markers
    are an additional signal, never the only one, so a marker write
    failure cannot resurrect a stale decision after a restart.
    """
    job_id = str(job.get("record_id"))
    marker_invalidated = {
        str(d.get("target_ref"))
        for d in all_decisions
        if str(d.get("decision_type")) == "decision_invalidated" and d.get("target_ref")
    }

    def _valid(d: Mapping[str, Any]) -> bool:
        if str(d.get("decision_type")) == "decision_invalidated":
            return False
        if d["record_id"] in marker_invalidated:
            return False
        if str(job.get("status")) != "succeeded":
            return False
        if str(job.get("result_ref") or "") != f"decision:{d['record_id']}":
            return False
        return int((d.get("input_versions") or {}).get("job_attempt") or -1) == int(job.get("attempt") or 0)

    own = [d for d in all_decisions if str(d.get("source_ref")) == f"agent_job:{job_id}"]
    valid = [d for d in own if _valid(d)]
    stale = [d for d in own if not _valid(d) and str(d.get("decision_type")) != "decision_invalidated"]
    return {
        "valid": valid,
        "stale": stale,
        "invalidated_ids": sorted(marker_invalidated),
    }


class PiTaskRunner:
    # role registry: read-only analysis roles, each with its input
    # collector, prompt and structured-output validator. Adding a role
    # here is the only supported way to extend the worker.
    def _collect_quote_inputs(self) -> Dict[str, Any]:
        """Read-only input assembly: only committed-batch supplier offers
        count as committed; revoked/failed-batch leftovers are listed
        separately and never presented as usable quotes."""
        records = self._repos.offers.list()
        committed = []
        excluded = 0
        for r in records:
            if not str(r.get("provider_id", "")).startswith("supplier_file"):
                continue
            source_ref = str(r.get("source_ref") or "")
            usable = True
            if source_ref.startswith("import_batch:"):
                if self._import_service is None:
                    usable = False
                else:
                    usable = self._import_service.batch_status(source_ref.split(":", 1)[1]) == "committed"
            if not usable:
                excluded += 1
                continue
            committed.append(
                {
                    "record_id": r.get("record_id"),
                    "sku": r.get("source_item_id"),
                    "observed_at": r.get("observed_at"),
                    "price": r.get("eligible_price"),
                    "freight": r.get("shipping_quote"),
                    "stock_state": r.get("stock_state"),
                    "stock_quantity": r.get("stock_quantity"),
                    "moq": r.get("moq"),
                    "condition": r.get("condition"),
                    "attributes": r.get("attributes"),
                    "source_ref": r.get("source_ref"),
                }
            )
        return {
            "committed_offer_count": len(committed),
            "excluded_non_committed": excluded,
            "offers": committed,
        }

    def _collect_match_inputs(self) -> Dict[str, Any]:
        matches = [
            {
                "match_id": m.get("record_id"),
                "grade": m.get("grade"),
                "rule_version": m.get("rule_version"),
                "conflicts": m.get("conflicts"),
                "unknowns": [
                    c.get("field")
                    for c in (m.get("hard_constraints") or [])
                    if c.get("state") == "unknown"
                ],
                "human_correction": m.get("human_correction"),
                "left_snapshot_id": m.get("left_snapshot_id"),
                "right_snapshot_id": m.get("right_snapshot_id"),
            }
            for m in self._repos.matches.list()
        ]
        return {"match_count": len(matches), "matches": matches}

    def _collect_pricing_inputs(self) -> Dict[str, Any]:
        reviewed = []
        for o in self._repos.opportunities.list():
            economics = o.get("economics") or {}
            scenarios = economics.get("scenarios") or {}
            conservative = scenarios.get("conservative") or {}
            base = scenarios.get("base") or {}
            reviewed.append(
                {
                    "opportunity_id": o.get("record_id"),
                    "sku": o.get("canonical_sku"),
                    "status": o.get("status"),
                    "match_id": o.get("match_id"),
                    "conservative_profit": conservative.get("contribution_profit"),
                    "base_profit": base.get("contribution_profit"),
                    "missing_costs": conservative.get("missing"),
                    "cash_occupied": economics.get("cash_occupied"),
                    "evidence_gaps": economics.get("evidence_gaps"),
                }
            )
        return {"opportunity_count": len(reviewed), "opportunities": reviewed}

    def _collect_screening_inputs(self) -> Dict[str, Any]:
        opportunities = [
            {
                "opportunity_id": o.get("record_id"),
                "sku": o.get("canonical_sku"),
                "status": o.get("status"),
                "status_reason": o.get("status_reason"),
                "revision": o.get("revision"),
            }
            for o in self._repos.opportunities.list()
        ]
        status_counts: Dict[str, int] = {}
        for o in opportunities:
            status_counts[str(o["status"])] = status_counts.get(str(o["status"]), 0) + 1
        return {"status_counts": status_counts, "opportunities": opportunities}

    ROLE_SPECS: Dict[str, Dict[str, Any]] = {
        ROLE_SUPPLIER_QUOTE_REVIEW: {
            "decision_type": "supplier_quote_gap_review",
            "input_refs": ["commerce:offers:committed"],
            "collector": "_collect_quote_inputs",
            "prompt": (
                "审查以下供应商报价快照，找出每个 SKU 的资料缺口（未知价格/运费/库存/MOQ/成色/规格缺失、"
                "明显不一致），输出严格 JSON：{\"findings\": [{\"sku\":..., \"field\":..., \"severity\": \"info|warn|block\", "
                "\"note\":...}], \"summary\": \"一句话总览\"}。只输出 JSON。没有缺口时 findings 为空数组。"
                "你可以调用只读工具核对，但总数受程序限制。"
            ),
            "validator": "validate_review_output",
            "recommendation_if_findings": "close the listed gaps with the supplier before any listing/procurement action",
            "recommendation_if_empty": "no gaps found in the reviewed snapshots",
        },
        ROLE_MATCH_AUDIT: {
            "decision_type": "match_audit",
            "input_refs": ["commerce:matches:all"],
            "collector": "_collect_match_inputs",
            "prompt": (
                "审计以下同款匹配记录：逐条给出裁决（confirm=证据充分；investigate=存在 unknown/conflict 需向供应商确认；"
                "reject=硬冲突确认不同款）。输出严格 JSON：{\"audits\": [{\"match_id\":..., \"verdict\": \"confirm|investigate|reject\", "
                "\"note\":...}], \"summary\": \"一句话\"}。只输出 JSON；没有匹配记录时 audits 为空数组。"
            ),
            "validator": "validate_match_audit_output",
            "recommendation_if_findings": "route investigate/reject matches to supplier confirmation before any arbitrage",
            "recommendation_if_empty": "no match records to audit",
        },
        ROLE_PRICING_REVIEW: {
            "decision_type": "pricing_review",
            "input_refs": ["commerce:opportunities:economics"],
            "collector": "_collect_pricing_inputs",
            "prompt": (
                "复核以下商机的贡献利润与缺口（保守/基准情景、缺失成本、现金占用）：逐个给出 verdict"
                "（advance=保守利润为正且缺口可解释；hold=存在未知成本或匹配不足；reject=保守利润非正）。"
                "输出严格 JSON：{\"reviews\": [{\"opportunity_id\":..., \"verdict\": \"advance|hold|reject\", "
                "\"suggested_list_price\": 数字或null, \"note\":...}], \"summary\": \"一句话\"}。只输出 JSON。"
            ),
            "validator": "validate_pricing_review_output",
            "recommendation_if_findings": "act only on advance verdicts after closing the listed gaps; suggestions are advisory",
            "recommendation_if_empty": "no opportunities with economics to review",
        },
        ROLE_OPPORTUNITY_SCREENING: {
            "decision_type": "opportunity_screening",
            "input_refs": ["commerce:opportunities:all"],
            "collector": "_collect_screening_inputs",
            "prompt": (
                "筛选以下商机（按状态与原因）：逐个给出 verdict（keep=值得继续研究；watch=信息不足观察；"
                "drop=建议拒绝）。输出严格 JSON：{\"screenings\": [{\"opportunity_id\":..., \"verdict\": \"keep|watch|drop\", "
                "\"reason\":...}], \"summary\": \"一句话\"}。只输出 JSON。"
            ),
            "validator": "validate_screening_output",
            "recommendation_if_findings": "apply drop/reject only through the operator decision flow; suggestions are advisory",
            "recommendation_if_empty": "no opportunities to screen",
        },
    }

    def __init__(
        self,
        job_store: AgentJobStore,
        repos,
        *,
        data_dir=None,
        import_service=None,
        deadline_seconds: float = 300.0,
        max_tool_calls: int = 12,
        lease_padding_seconds: float = 60.0,
        gate: Optional[ReadOnlyJobGate] = None,
        per_job_token_budget: int = DEFAULT_PER_JOB_TOKEN_BUDGET,
        daily_token_budget: int = DEFAULT_DAILY_TOKEN_BUDGET,
        min_execution_seconds: float = 5.0,
    ) -> None:
        self._jobs = job_store
        self._repos = repos
        self._decisions = repos.decisions
        self._data_dir = Path(data_dir) if data_dir else None
        self._import_service = import_service
        self._deadline_seconds = float(deadline_seconds)
        self._max_tool_calls = int(max_tool_calls)
        self._lease_padding = float(lease_padding_seconds)
        self.gate = gate or ReadOnlyJobGate()
        self.per_job_token_budget = int(per_job_token_budget)
        self.daily_token_budget = int(daily_token_budget)
        self.min_execution_seconds = float(min_execution_seconds)
        self.model_usage = ModelUsageLedger(self._data_dir or Path("data"))
        self._boundary_lock = threading.Lock()
        self._execute_lock = threading.Lock()  # single-worker serialization
        self._started = False
        self._stopped = False

    # ---- run boundary (step 1) ----

    def start(self) -> None:
        with self._boundary_lock:
            if self._started:
                raise RuntimeError("PiTaskRunner already started; repeated start refused")
            self._started = True

    def stop(self) -> None:
        with self._boundary_lock:
            self._stopped = True

    @property
    def status(self) -> Dict[str, Any]:
        with self._boundary_lock:
            return {"started": self._started, "stopped": self._stopped}

    # ---- submission ----

    def submit_job(self, role: str, *, goal_id: str = "", idempotency_key: str = "") -> Dict[str, Any]:
        spec = self.ROLE_SPECS.get(role)
        if spec is None:
            raise ValueError(
                f"unsupported role {role!r}; supported: {', '.join(sorted(self.ROLE_SPECS))}"
            )
        return self._jobs.submit(
            role=role,
            goal_id=goal_id or role,
            input_refs=list(spec["input_refs"]),
            priority=5,
            deadline=utc_now() + timedelta(seconds=self._deadline_seconds),
            idempotency_key=idempotency_key,
            model_budget={"max_tool_calls": self._max_tool_calls},
        )

    def submit_quote_review_job(self, *, goal_id: str = "", idempotency_key: str = "") -> Dict[str, Any]:
        return self.submit_job(ROLE_SUPPLIER_QUOTE_REVIEW, goal_id=goal_id, idempotency_key=idempotency_key)

    # ---- execution ----

    def _collect_inputs(self, job: Mapping[str, Any]) -> Dict[str, Any]:
        spec = self.ROLE_SPECS[str(job.get("role"))]
        return getattr(self, spec["collector"])()

    def _resolve_validator(self, job: Mapping[str, Any]):
        spec = self.ROLE_SPECS[str(job.get("role"))]
        return globals()[spec["validator"]]

    def run_once(
        self,
        *,
        pi_factory,
        now: Optional[datetime] = None,
        now_post: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Claim and execute at most one job through the real Pi client.

        ``pi_factory(token_env)`` must return an object with ``prompt(
        message, context)`` matching PiRpcClient's result shape (a
        ``stop()`` method, when present, is always called in cleanup).
        Returns None when no job was claimable (or the runner stopped),
        ``{"outcome": "busy"}`` when another execution is in flight.
        """
        if not self._started:
            raise RuntimeError("runner not started; call start() first")
        with self._boundary_lock:
            if self._stopped:
                return None
        # single worker: a second concurrent execution is refused outright
        if not self._execute_lock.acquire(blocking=False):
            return {"outcome": "busy"}
        try:
            return self._run_claimed_once(pi_factory=pi_factory, now=now, now_post=now_post)
        finally:
            self._execute_lock.release()

    def _run_claimed_once(self, *, pi_factory, now, now_post) -> Optional[Dict[str, Any]]:
        # stop vs claim is decided atomically under the boundary lock
        with self._boundary_lock:
            if self._stopped:
                return None
            job = self._jobs.claim_next(worker_id=JOB_WORKER_ID, lease_seconds=self._lease_padding + 300.0)
        if job is None:
            return None
        spec = self.ROLE_SPECS.get(str(job.get("role")))
        if spec is None:
            self._jobs.fail(
                job["record_id"], worker_id=JOB_WORKER_ID, fencing_token=job["fencing_token"],
                error=f"unsupported role {job.get('role')!r}", retryable=False,
            )
            return {"job_id": job["record_id"], "outcome": "rejected_role"}

        def fail_job(error: str, retryable: bool, outcome: str, **extra):
            self._jobs.fail(
                job["record_id"], worker_id=JOB_WORKER_ID, fencing_token=job["fencing_token"],
                error=error, retryable=retryable,
            )
            return {"job_id": job["record_id"], "outcome": outcome, **extra}

        # program-enforced deadline: a job past (or nearly past) its
        # deadline never starts; execution gets the remaining window
        moment = now or utc_now()
        deadline: Optional[datetime] = None
        deadline_raw = job.get("deadline")
        if deadline_raw:
            try:
                deadline = datetime.fromisoformat(str(deadline_raw))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=moment.tzinfo)
            except ValueError:
                deadline = None
        if deadline is not None:
            remaining = (deadline - moment).total_seconds()
            if remaining <= 0:
                return fail_job("deadline exceeded before start", False, "rejected_deadline")
            if remaining < self.min_execution_seconds:
                return fail_job(
                    f"only {remaining:.0f}s left before deadline; refusing to start", False, "rejected_deadline"
                )

        # program-enforced daily model budget (recorded usage, never zero-guessed)
        day = (moment).strftime("%Y-%m-%d")
        used_today = self.model_usage.used_today(day=day)
        if used_today >= self.daily_token_budget:
            return fail_job(
                f"model daily budget exhausted ({used_today}/{self.daily_token_budget} tokens on {day})",
                True, "budget_exhausted_day",
            )

        inputs = self._collect_inputs(job)
        validator = self._resolve_validator(job)
        token = self.gate.issue(job["record_id"], max_tool_calls=self._max_tool_calls)
        try:
            client = pi_factory({"XIANYU_PI_JOB_TOKEN": token})
            try:
                result = client.prompt(spec["prompt"], context={
                    "job_id": job["record_id"],
                    "role": job.get("role"),
                    "inputs": inputs,
                    "output_contract": "structured JSON per the prompt",
                })
            finally:
                # the Pi client owns a resident RPC subprocess; always close
                stop = getattr(client, "stop", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:  # noqa: BLE001 - cleanup must never mask results
                        pass
        except Exception as exc:  # noqa: BLE001 - real failures become job failures
            self.gate.release(token)
            # the model call ATTEMPTED and failed: consumption is unknown,
            # not zero -- record the attempt before failing the job
            self.model_usage.record(day=day, job_id=job["record_id"], total_tokens=None)
            return fail_job(f"pi execution failed: {exc}", True, "failed")
        self.gate.release(token)

        payload = result.get("result") or {}
        usage = payload.get("usage") or {}
        total_tokens = usage.get("totalTokens")

        # program-enforced deadline, result side: a result landing after
        # the deadline is rejected even if the call succeeded -- but the
        # consumed tokens still count against today's budget
        if deadline is not None:
            post_moment = now_post or utc_now()
            if post_moment.tzinfo is None:
                post_moment = post_moment.replace(tzinfo=moment.tzinfo)
            if post_moment > deadline:
                self.model_usage.record(
                    day=day, job_id=job["record_id"], total_tokens=total_tokens
                )
                return fail_job("result arrived after deadline; rejected", False, "rejected_deadline_result")

        # lease re-check BEFORE accepting the result: if the lease was
        # lost or the job cancelled mid-run, the result is discarded --
        # the consumed tokens still count
        try:
            self._jobs.heartbeat(
                job["record_id"], worker_id=JOB_WORKER_ID,
                fencing_token=job["fencing_token"],
                lease_seconds=self._lease_padding + 300.0,
            )
        except Exception:  # noqa: BLE001 - lost lease or terminal state -> discard
            self.model_usage.record(day=day, job_id=job["record_id"], total_tokens=total_tokens)
            return {"job_id": job["record_id"], "outcome": "discarded_stale"}

        message_text = str(payload.get("message") or "")

        # program-enforced per-job token budget: overspend consumes the
        # daily ledger even though the result is rejected
        if total_tokens is not None and int(total_tokens) > self.per_job_token_budget:
            self.model_usage.record(day=day, job_id=job["record_id"], total_tokens=total_tokens)
            return fail_job(
                f"model usage {total_tokens} exceeded per-job budget {self.per_job_token_budget}",
                False, "budget_exceeded",
            )

        try:
            review = validator(message_text)
        except ValueError as exc:
            self.model_usage.record(day=day, job_id=job["record_id"], total_tokens=total_tokens)
            return fail_job(f"invalid structured output: {exc}", True, "invalid_output")

        cost = {
            "provider": payload.get("provider"),
            "model": payload.get("model"),
            "input_tokens": usage.get("input"),
            "output_tokens": usage.get("output"),
            "total_tokens": total_tokens,
            "note": None if usage else "usage not reported by this run; unknown, not zero",
        }
        evidence_path = self._write_evidence_file(job, inputs=inputs, payload=payload, message=message_text)
        # consumption is recorded BEFORE completion: a crash after this
        # point still leaves the attempt accounted in the daily ledger
        self.model_usage.record(day=day, job_id=job["record_id"], total_tokens=total_tokens, model=str(payload.get("model") or ""))
        decision = self._decision_for_attempt(
            job, review=review, payload=payload, cost=cost, inputs=inputs, evidence_path=evidence_path
        )
        try:
            self._jobs.complete(
                job["record_id"], worker_id=JOB_WORKER_ID, fencing_token=job["fencing_token"],
                result_ref=f"decision:{decision['record_id']}", cost=cost,
            )
        except FencingError:
            # the decision was written but the job left our hands: the late
            # product must never surface as a valid decision; the marker is
            # best-effort -- validity also derives from job state
            try:
                self._invalidate_decision(job, decision_id=decision["record_id"], reason="lease lost before completion")
            except Exception:  # noqa: BLE001 - marker write failure must not mask the discard
                pass
            return {"job_id": job["record_id"], "outcome": "discarded_stale", "decision_id": decision["record_id"]}
        return {
            "job_id": job["record_id"],
            "outcome": "succeeded",
            "decision_id": decision["record_id"],
            "findings": len(review.get("findings") or []),
            "tool_calls": len(payload.get("tool_calls") or []),
        }

    # ---- evidence persistence ----

    def _write_evidence_file(
        self,
        job: Mapping[str, Any],
        *,
        inputs: Dict[str, Any],
        payload: Mapping[str, Any],
        message: str,
    ) -> Optional[Path]:
        """Persist the actual inputs snapshot and tool events next to the
        stores so the decision basis stays replayable after data changes."""
        if self._data_dir is None:
            return None
        directory = self._data_dir / "commerce" / "pi_evidence"
        directory.mkdir(parents=True, exist_ok=True)
        attempt = int(job.get("attempt") or 0)
        path = directory / f"{job['record_id']}__attempt{attempt}.json"
        snapshot = {
            "job_id": job["record_id"],
            "role": job.get("role"),
            "attempt": attempt,
            "recorded_at": utc_now().isoformat(),
            "inputs": inputs,
            "tool_calls": list(payload.get("tool_calls") or []),
            "usage": payload.get("usage") or {},
            "model": payload.get("model"),
            "session_id": payload.get("session_id"),
            "model_message": message[:4000],
        }
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _invalidate_decision(self, job: Mapping[str, Any], *, decision_id: str, reason: str) -> None:
        """Mark a late/stale decision as invalidated (append-only marker)."""
        record = build_decision_record(
            summary=f"decision {decision_id} invalidated: {reason}",
            actor_role="system",
            decision_type="decision_invalidated",
            target_ref=decision_id,
            result={"invalidated": True, "reason": reason, "job_id": job.get("record_id")},
            source_ref=f"agent_job:{job.get('record_id')}",
        )
        self._decisions.append(record)

    @staticmethod
    def _invalidated_decision_ids(decisions: Sequence[Mapping[str, Any]]) -> set:
        return set(decision_validity({"record_id": ""}, decisions)["invalidated_ids"])

    def _decision_for_attempt(
        self,
        job: Mapping[str, Any],
        *,
        review: Dict[str, Any],
        payload: Mapping[str, Any],
        cost: Dict[str, Any],
        inputs: Optional[Dict[str, Any]] = None,
        evidence_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """At most one decision per (job, attempt); a crash between the
        decision write and completion reuses the stored decision (unless
        that stored decision was invalidated)."""
        spec = self.ROLE_SPECS[str(job.get("role"))]
        items_key = {
            "supplier_quote_gap_review": "findings",
            "match_audit": "audits",
            "pricing_review": "reviews",
            "opportunity_screening": "screenings",
        }.get(str(job.get("role")), "findings")
        items = review.get(items_key) or []
        attempt = int(job.get("attempt") or 0)
        existing_all = self._decisions.list()
        invalidated = self._invalidated_decision_ids(existing_all)
        for existing in existing_all:
            if str(existing.get("source_ref")) != f"agent_job:{job['record_id']}":
                continue
            if existing["record_id"] in invalidated or str(existing.get("decision_type")) == "decision_invalidated":
                continue
            if int((existing.get("input_versions") or {}).get("job_attempt") or -1) == attempt:
                return existing
        unknowns: List[str] = []
        for item in items:
            item_lower = {str(k).lower(): v for k, v in item.items()}
            severity = item_lower.get("severity")
            verdict = item_lower.get("verdict")
            if severity == "block" or verdict in ("reject", "investigate"):
                key = item.get("field") or item.get("match_id") or item.get("opportunity_id")
                unknowns.append(f"{key}: {severity or verdict}")
        if not cost.get("total_tokens"):
            unknowns.append("model token usage (unknown, not zero)")
        inputs_hash = ""
        if inputs is not None:
            inputs_hash = hashlib.sha256(
                json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
        evidence_refs = [
            {"ref_type": "pi_session", "ref_id": str(payload.get("session_id") or "")},
            {"ref_type": "pi_tool_events", "ref_id": f"job:{job['record_id']}"},
            {"ref_type": "role_inputs", "ref_id": ",".join(spec["input_refs"])},
        ]
        if evidence_path is not None:
            evidence_refs.append({"ref_type": "pi_evidence_file", "ref_id": str(evidence_path)})
        decision = decision_from_job(
            job,
            summary=str(review.get("summary") or spec["decision_type"])[:500],
            decision_type=spec["decision_type"],
            recommendation=(
                spec["recommendation_if_findings"] if items else spec["recommendation_if_empty"]
            ),
            evidence_refs=evidence_refs,
            unknowns=unknowns or ["none"],
            model_version=str(payload.get("model") or "unknown"),
            cost=cost,
            result=review,
        )
        if inputs_hash:
            decision["input_versions"]["inputs_sha256"] = inputs_hash
        if evidence_path is not None:
            decision["input_versions"]["evidence_file"] = str(evidence_path)
        return self._decisions.append(decision)

    # ---- restart reconciliation (step 5) ----

    def reconcile_task_decisions(self, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Heal crash windows between decision write and job completion.

        For a job whose lease expired while running: if its latest
        attempt already produced a decision, complete the job with that
        decision (no re-run, no duplicate); otherwise let the store's
        lease recovery handle it (analysis requeue / write -> unknown).
        """
        moment = now or utc_now()
        healed: List[Dict[str, Any]] = []
        for job in self._jobs.list():
            status = str(job.get("status"))
            if status not in ("leased", "running"):
                continue
            lease_until = str(job.get("lease_until") or "")
            if not lease_until:
                continue
            try:
                expiry = datetime.fromisoformat(lease_until)
            except ValueError:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=moment.tzinfo)
            if expiry > moment:
                continue
            attempt = int(job.get("attempt") or 0)
            for existing in self._decisions.list():
                if str(existing.get("source_ref")) != f"agent_job:{job['record_id']}":
                    continue
                if int((existing.get("input_versions") or {}).get("job_attempt") or -1) != attempt:
                    continue
                self._jobs.complete(
                    job["record_id"],
                    worker_id=str(job.get("lease_owner") or JOB_WORKER_ID),
                    fencing_token=int(job.get("fencing_token") or 0),
                    result_ref=f"decision:{existing['record_id']}",
                    cost=existing.get("cost") or None,
                )
                healed.append({"job_id": job["record_id"], "decision_id": existing["record_id"]})
                break
        remaining = self._jobs.recover_expired_leases(now=moment)
        return {"healed_completions": healed, "lease_recoveries": len(remaining)}
