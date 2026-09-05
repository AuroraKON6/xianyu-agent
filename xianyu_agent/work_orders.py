from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .action_scope import (
    ActionScopeValidationError,
    build_action_scope_envelope,
    require_valid_action_scope,
)


RISKY_ACTIONS = {
    "publish_listing",
    "edit_listing",
    "fulfill_order",
    "send_message",
    "delete_item",
    "refund_order",
    "reject_refund",
    "sensitive_reply",
    "approve_resale_plan",
    "prepare_purchase_draft",
    "place_supplier_order",
}


class WorkOrderManager:
    """Persistent work-order layer for realistic multi-agent collaboration."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = Path(storage_path or Path("data") / "work_orders.json")
        self._work_orders: Dict[str, Dict[str, Any]] = {}
        self._load()

    def configure(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
        self._work_orders = {}
        self._load()

    def create_work_order(
        self,
        kind: str,
        title: str,
        owner: str,
        context: Optional[Dict[str, Any]] = None,
        risk_level: str = "normal",
    ) -> Dict[str, Any]:
        now = time.time()
        work_order = {
            "work_order_id": f"wo_{uuid.uuid4().hex[:12]}",
            "kind": kind,
            "title": title,
            "owner": owner,
            "status": "open",
            "risk_level": risk_level,
            "context": context or {},
            "discussion": None,
            "action_plan": None,
            "approvals": [],
            "events": [
                {"type": "created", "created_at": now, "message": "Work order created"},
            ],
            "created_at": now,
            "updated_at": now,
        }
        self._work_orders[work_order["work_order_id"]] = work_order
        self._save()
        return _copy(work_order)

    def list_work_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        orders = sorted(self._work_orders.values(), key=lambda item: item.get("created_at", 0))
        return [_copy(order) for order in orders[-limit:]]

    def get_work_order(self, work_order_id: str) -> Dict[str, Any]:
        work_order = self._work_orders.get(work_order_id)
        if not work_order:
            raise KeyError(f"Work order not found: {work_order_id}")
        return _copy(work_order)

    def attach_discussion(self, work_order_id: str, discussion: Dict[str, Any]) -> Dict[str, Any]:
        work_order = self._require(work_order_id)
        work_order["discussion"] = discussion
        work_order["events"].append(
            {
                "type": "discussion_attached",
                "created_at": time.time(),
                "discussion_id": discussion.get("discussion_id"),
            }
        )
        self._touch(work_order)
        self._save()
        return _copy(work_order)

    def create_action_plan(
        self,
        work_order_id: str,
        discussion: Optional[Dict[str, Any]] = None,
        proposed_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        work_order = self._require(work_order_id)
        if discussion:
            work_order["discussion"] = discussion
        actions = proposed_actions or self._default_actions(work_order)
        planned_actions = []
        approvals = []
        for raw_action in actions:
            action = {
                "agent": str(raw_action.get("agent") or work_order["owner"]),
                "action": str(raw_action.get("action") or ""),
                "description": str(raw_action.get("description") or ""),
                "params": dict(raw_action.get("params") or {}),
                "draft_snapshot": raw_action.get("draft_snapshot") or {},
            }
            action["requires_approval"] = self.requires_approval(action["action"], work_order.get("risk_level"))
            planned_actions.append(action)
            if action["requires_approval"]:
                approvals.append(self._new_approval(work_order, action))

        plan = {
            "plan_id": f"plan_{uuid.uuid4().hex[:12]}",
            "work_order_id": work_order_id,
            "summary": self._plan_summary(work_order, discussion),
            "actions": planned_actions,
            "status": "needs_approval" if approvals else "planned",
            "created_at": time.time(),
        }
        work_order["action_plan"] = plan
        work_order["approvals"].extend(approvals)
        work_order["status"] = plan["status"]
        work_order["events"].append(
            {
                "type": "action_plan_created",
                "created_at": time.time(),
                "plan_id": plan["plan_id"],
                "approval_count": len(approvals),
            }
        )
        self._touch(work_order)
        self._save()
        return _copy(plan)

    def requires_approval(self, action: str, risk_level: Optional[str] = None) -> bool:
        return action in RISKY_ACTIONS or str(risk_level or "").lower() in {"high", "critical"}

    def decide_approval(self, approval_id: str, decision: str, note: str = "") -> Dict[str, Any]:
        normalized = decision.strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        for work_order in self._work_orders.values():
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    decided_at = time.time()
                    if normalized == "approved":
                        require_valid_action_scope(
                            approval,
                            now=decided_at,
                            require_approval_signature=False,
                        )
                        approval["approved_action_digest"] = str(
                            (approval.get("scope_envelope") or {}).get("action_digest") or ""
                        )
                    approval["status"] = normalized
                    approval["decision_note"] = note
                    approval["decided_at"] = decided_at
                    work_order["events"].append(
                        {
                            "type": f"approval_{normalized}",
                            "created_at": time.time(),
                            "approval_id": approval_id,
                            "note": note,
                            "action_digest": approval.get("approved_action_digest", ""),
                        }
                    )
                    self._refresh_status_from_approvals(work_order)
                    self._touch(work_order)
                    self._save()
                    return _copy(approval)
        raise KeyError(f"Approval not found: {approval_id}")

    def execute_approval(
        self,
        approval_id: str,
        publish_fn=None,
        action_executor=None,
        executor_context=None,
    ) -> Dict[str, Any]:
        """Execute an approved approval. Only status=approved can be executed.
        Idempotent: completed or running approvals are rejected.

        Accepts either:
        - publish_fn (legacy): a callable(payload) -> dict
        - action_executor + context: an ActionExecutor instance that dispatches by action name
        """
        target_approval = None
        target_wo = None
        for work_order in self._work_orders.values():
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    target_approval = approval
                    target_wo = work_order
                    break
            if target_approval:
                break

        if not target_approval:
            raise KeyError(f"Approval not found: {approval_id}")

        if target_approval["status"] != "approved":
            raise ValueError(
                f"Cannot execute approval with status '{target_approval['status']}'; must be 'approved'"
            )

        exec_status = target_approval.get("execution_status")
        if exec_status == "completed":
            raise ValueError("Approval already executed and completed; cannot re-execute")
        if exec_status == "running":
            raise ValueError("Approval is currently running; cannot re-execute")

        try:
            self.validate_approval_scope(approval_id)
        except ActionScopeValidationError as exc:
            return self._record_scope_failure(target_wo, target_approval, exc)

        action = target_approval.get("action", "")
        payload = target_approval.get("payload", {})

        # Mark as running before actual execution
        target_approval["execution_status"] = "running"
        target_approval["execution_started_at"] = time.time()
        target_wo["events"].append(
            {
                "type": "execution_started",
                "created_at": time.time(),
                "approval_id": approval_id,
                "action": action,
            }
        )
        self._touch(target_wo)
        self._save()

        try:
            if action_executor is not None:
                execution_result = action_executor.execute(target_approval, executor_context or {})
            elif publish_fn is not None:
                execution_result = publish_fn(payload)
            else:
                raise ValueError("No executor or publish_fn provided")

            target_approval["execution_status"] = "completed"
            target_approval["execution_result"] = execution_result
            target_approval["executed_at"] = time.time()

            target_wo["events"].append(
                {
                    "type": "execution_completed",
                    "created_at": time.time(),
                    "approval_id": approval_id,
                    "action": action,
                    "result": execution_result,
                }
            )
            self._refresh_execution_status(target_wo)
            self._touch(target_wo)
            self._save()
            return _copy({
                "ok": True,
                "approval_id": approval_id,
                "action": action,
                "execution_status": "completed",
                "execution_result": execution_result,
            })

        except Exception as exc:
            target_approval["execution_status"] = "failed"
            target_approval["execution_result"] = {"ok": False, "error": str(exc)}
            target_approval["executed_at"] = time.time()

            target_wo["events"].append(
                {
                    "type": "execution_failed",
                    "created_at": time.time(),
                    "approval_id": approval_id,
                    "action": action,
                    "error": str(exc),
                }
            )
            target_wo["status"] = "failed"
            self._touch(target_wo)
            self._save()
            return _copy({
                "ok": False,
                "approval_id": approval_id,
                "action": action,
                "execution_status": "failed",
                "error": str(exc),
            })

    async def execute_approval_async(
        self,
        approval_id: str,
        action_executor=None,
        executor_context=None,
    ) -> Dict[str, Any]:
        """Async version of execute_approval for use with ActionExecutor that has async handlers."""
        target_approval = None
        target_wo = None
        for work_order in self._work_orders.values():
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    target_approval = approval
                    target_wo = work_order
                    break
            if target_approval:
                break

        if not target_approval:
            raise KeyError(f"Approval not found: {approval_id}")

        if target_approval["status"] != "approved":
            raise ValueError(
                f"Cannot execute approval with status '{target_approval['status']}'; must be 'approved'"
            )

        exec_status = target_approval.get("execution_status")
        if exec_status == "completed":
            raise ValueError("Approval already executed and completed; cannot re-execute")
        if exec_status == "running":
            raise ValueError("Approval is currently running; cannot re-execute")

        try:
            self.validate_approval_scope(approval_id)
        except ActionScopeValidationError as exc:
            return self._record_scope_failure(target_wo, target_approval, exc)

        action = target_approval.get("action", "")

        # Mark as running
        target_approval["execution_status"] = "running"
        target_approval["execution_started_at"] = time.time()
        target_wo["events"].append(
            {
                "type": "execution_started",
                "created_at": time.time(),
                "approval_id": approval_id,
                "action": action,
            }
        )
        self._touch(target_wo)
        self._save()

        try:
            if action_executor is None:
                raise ValueError("action_executor is required for async execution")

            execution_result = await action_executor.execute_async(target_approval, executor_context or {})

            target_approval["execution_status"] = "completed"
            target_approval["execution_result"] = execution_result
            target_approval["executed_at"] = time.time()

            target_wo["events"].append(
                {
                    "type": "execution_completed",
                    "created_at": time.time(),
                    "approval_id": approval_id,
                    "action": action,
                    "result": execution_result,
                }
            )
            self._refresh_execution_status(target_wo)
            self._touch(target_wo)
            self._save()
            return _copy({
                "ok": True,
                "approval_id": approval_id,
                "action": action,
                "execution_status": "completed",
                "execution_result": execution_result,
            })

        except Exception as exc:
            target_approval["execution_status"] = "failed"
            target_approval["execution_result"] = {"ok": False, "error": str(exc)}
            target_approval["executed_at"] = time.time()

            target_wo["events"].append(
                {
                    "type": "execution_failed",
                    "created_at": time.time(),
                    "approval_id": approval_id,
                    "action": action,
                    "error": str(exc),
                }
            )
            target_wo["status"] = "failed"
            self._touch(target_wo)
            self._save()
            return _copy({
                "ok": False,
                "approval_id": approval_id,
                "action": action,
                "execution_status": "failed",
                "error": str(exc),
            })

    def complete_approval_manually(
        self,
        approval_id: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Record an externally completed approved action with audit evidence.

        This is intentionally separate from normal execution. Callers must
        verify the platform-side evidence before using it; the manager only
        enforces approval and idempotency invariants.
        """
        target_approval = None
        target_work_order = None
        for work_order in self._work_orders.values():
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    target_approval = approval
                    target_work_order = work_order
                    break
            if target_approval:
                break
        if not target_approval or not target_work_order:
            raise KeyError(f"Approval not found: {approval_id}")
        if target_approval.get("status") != "approved":
            raise ValueError("manual completion requires an approved action")
        execution_status = str(target_approval.get("execution_status") or "")
        if execution_status == "running":
            raise ValueError("approval execution is currently running")
        require_valid_action_scope(
            target_approval,
            now=time.time(),
            require_approval_signature=True,
            allow_expired=True,
        )
        existing_result = target_approval.get("execution_result")
        if (
            execution_status == "completed"
            and isinstance(existing_result, dict)
            and bool(existing_result.get("ok"))
        ):
            return _copy(target_approval)

        now = time.time()
        clean_result = dict(result or {})
        clean_result.setdefault("ok", True)
        clean_result["manual_confirmation"] = True
        target_approval["execution_status"] = "completed"
        target_approval["execution_result"] = clean_result
        target_approval["execution_started_at"] = now
        target_approval["executed_at"] = now
        target_work_order["events"].append({
            "type": "execution_manually_confirmed",
            "created_at": now,
            "approval_id": approval_id,
            "action": target_approval.get("action", ""),
            "result": clean_result,
        })
        self._refresh_execution_status(target_work_order)
        self._touch(target_work_order)
        self._save()
        return _copy(target_approval)

    def validate_approval_scope(
        self,
        approval_id: str,
        *,
        now: Optional[float] = None,
        allow_expired: bool = False,
    ) -> Dict[str, Any]:
        for work_order in self._work_orders.values():
            for approval in work_order.get("approvals", []):
                if approval.get("approval_id") == approval_id:
                    return require_valid_action_scope(
                        approval,
                        now=now,
                        require_approval_signature=True,
                        allow_expired=allow_expired,
                    )
        raise KeyError(f"Approval not found: {approval_id}")

    def _record_scope_failure(
        self,
        work_order: Dict[str, Any],
        approval: Dict[str, Any],
        exc: ActionScopeValidationError,
    ) -> Dict[str, Any]:
        now = time.time()
        result = {
            "ok": False,
            "action": str(approval.get("action") or ""),
            "error": str(exc),
            "scope_validation": {"ok": False, "code": exc.code},
            "operator_next_action": "recreate_action_plan_and_approve",
            "evidence": {
                "detected_blocker": exc.code,
                "write_attempted": False,
                "operator_next_action": "recreate_action_plan_and_approve",
            },
        }
        approval["execution_status"] = "failed"
        approval["execution_result"] = result
        approval["executed_at"] = now
        work_order["events"].append(
            {
                "type": "execution_blocked",
                "created_at": now,
                "approval_id": approval.get("approval_id", ""),
                "action": approval.get("action", ""),
                "reason": exc.code,
            }
        )
        work_order["status"] = "failed"
        self._touch(work_order)
        self._save()
        return _copy(
            {
                "ok": False,
                "approval_id": approval.get("approval_id", ""),
                "action": approval.get("action", ""),
                "execution_status": "failed",
                "execution_result": result,
                "error": str(exc),
            }
        )

    def _refresh_execution_status(self, work_order: Dict[str, Any]) -> None:
        approvals = work_order.get("approvals", [])
        exec_statuses = [a.get("execution_status") for a in approvals if a.get("status") == "approved"]
        if not exec_statuses:
            return
        if all(s == "completed" for s in exec_statuses):
            work_order["status"] = "completed"
        elif any(s == "failed" for s in exec_statuses):
            work_order["status"] = "partially_completed"

    def _default_actions(self, work_order: Dict[str, Any]) -> List[Dict[str, Any]]:
        if work_order.get("kind") == "listing":
            return [
                {"agent": "discovery", "action": "analyze_market", "description": "分析同类商品市场和卖点"},
                {"agent": "analytics", "action": "calculate_profit", "description": "核算利润和价格区间"},
                {"agent": "listing", "action": "create_listing", "description": "生成商品草稿"},
            ]
        return [
            {
                "agent": work_order.get("owner") or "openclaw",
                "action": "execute_task",
                "description": "执行任务单目标",
            }
        ]

    def _new_approval(self, work_order: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(action.get("params") or {})
        draft_snapshot = dict(action.get("draft_snapshot") or {})
        cookie_id = _find_cookie_id(payload, draft_snapshot, work_order.get("context"))
        if cookie_id:
            payload.setdefault("cookie_id", cookie_id)
            draft_snapshot.setdefault("cookie_id", cookie_id)
        approval_id = f"approval_{uuid.uuid4().hex[:12]}"
        requested_at = time.time()
        approval = {
            "approval_id": approval_id,
            "work_order_id": work_order["work_order_id"],
            "agent": action["agent"],
            "action": action["action"],
            "reason": f"{action['action']} requires human approval",
            "payload": payload,
            "draft_snapshot": draft_snapshot,
            "status": "pending",
            "requested_at": requested_at,
            "decided_at": None,
            "decision_note": "",
            "approved_action_digest": "",
            "execution_status": None,
            "execution_result": None,
        }
        approval["scope_envelope"] = build_action_scope_envelope(
            approval_id=approval_id,
            work_order_id=work_order["work_order_id"],
            action=action["action"],
            payload=payload,
            draft_snapshot=draft_snapshot,
            context=work_order.get("context") or {},
            requested_at=requested_at,
        )
        return approval

    def _plan_summary(self, work_order: Dict[str, Any], discussion: Optional[Dict[str, Any]]) -> str:
        summary = ""
        if discussion:
            summary = str(discussion.get("summary") or "").strip()
        elif work_order.get("discussion"):
            summary = str(work_order["discussion"].get("summary") or "").strip()
        if summary:
            return f"基于圆桌讨论形成行动计划：{summary}"
        return f"为「{work_order.get('title', '')}」形成行动计划。"

    def _refresh_status_from_approvals(self, work_order: Dict[str, Any]) -> None:
        approvals = work_order.get("approvals", [])
        if any(item.get("status") == "rejected" for item in approvals):
            work_order["status"] = "rejected"
        elif approvals and all(item.get("status") == "approved" for item in approvals):
            work_order["status"] = "approved"
        elif approvals:
            work_order["status"] = "needs_approval"

    def _require(self, work_order_id: str) -> Dict[str, Any]:
        work_order = self._work_orders.get(work_order_id)
        if not work_order:
            raise KeyError(f"Work order not found: {work_order_id}")
        return work_order

    def _touch(self, work_order: Dict[str, Any]) -> None:
        work_order["updated_at"] = time.time()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw if isinstance(raw, list) else []:
            work_order_id = item.get("work_order_id")
            if work_order_id:
                self._work_orders[work_order_id] = item

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        records = sorted(self._work_orders.values(), key=lambda item: item.get("created_at", 0))
        self.storage_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _find_cookie_id(*values) -> str:
    for value in values:
        found = _find_cookie_id_in_value(value)
        if found:
            return found
    return ""


def _find_cookie_id_in_value(value) -> str:
    if isinstance(value, dict):
        direct = value.get("cookie_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for child in value.values():
            found = _find_cookie_id_in_value(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_cookie_id_in_value(child)
            if found:
                return found
    return ""


work_order_manager = WorkOrderManager()
