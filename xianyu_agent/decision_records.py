"""Decision-record helper for agent jobs (WP07).

Links a job's structured outcome to an immutable DecisionRecord so the
console decision card can trace: which job, which inputs/versions,
what the agent proposed, what was unknown and what it cost. Model cost
is recorded as given; missing cost stays missing -- never zero.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .commerce.contracts import build_decision_record


def decision_from_job(
    job: Mapping[str, Any],
    *,
    summary: str,
    decision_type: str,
    options: Optional[Sequence[Mapping[str, Any]]] = None,
    recommendation: str = "",
    evidence_refs: Optional[Sequence[Mapping[str, Any]]] = None,
    unknowns: Optional[Sequence[str]] = None,
    constraint_checks: Optional[Sequence[Mapping[str, Any]]] = None,
    model_version: str = "",
    cost: Optional[Mapping[str, Any]] = None,
    authorization_type: str = "human",
    mandate_ref: str = "",
    result: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a DecisionRecord bound to one agent job."""
    if not summary.strip():
        raise ValueError("decision summary required")
    refs: List[Mapping[str, Any]] = list(evidence_refs or [])
    refs.append({"ref_type": "agent_job", "ref_id": str(job.get("record_id") or "")})
    return build_decision_record(
        summary=summary,
        actor_role=str(job.get("role") or "agent"),
        decision_type=decision_type,
        target_ref=str(job.get("goal_id") or ""),
        options=[dict(o) for o in (options or [])],
        recommendation=recommendation,
        evidence_refs=refs,
        input_versions={
            "job_revision": int(job.get("revision") or 1),
            "job_attempt": int(job.get("attempt") or 0),
            "job_inputs": dict(job.get("input_versions") or {}),
        },
        constraint_checks=[dict(c) for c in (constraint_checks or [])],
        unknowns=list(unknowns or []),
        model_version=model_version,
        cost=dict(cost) if cost else {},
        authorization_type=authorization_type,
        mandate_ref=mandate_ref,
        result=dict(result) if result else None,
        source_ref=f"agent_job:{job.get('record_id')}",
    )
