"""OPC Dashboard — read-only aggregation for one-person company daily review.

Aggregates data from:
- Resale candidates (scoring, status)
- Work orders (pending, completed, failed)
- Execution jobs (queued, running, failed)
- Message intake (metrics)
- Resale orders (revenue, margin)

Safety boundaries:
- Read-only. No write, execute, publish, message, refund, or delete actions.
- No secrets, raw buyer IDs, chat IDs, cookies, or tokens exposed.
- Missing data sources return 0, empty list, or source_notes.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .message_import_scheduler import build_scheduler_health_actions


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _safe_count(items: list, predicate) -> int:
    """Count items matching predicate, handling missing keys gracefully."""
    return sum(1 for item in items if predicate(item))


def _redact_secret_fields(value: Any) -> Any:
    """Recursively redact values under secret-like keys."""
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if any(part in key_str.lower() for part in ("token", "secret", "password", "cookie", "api_key")):
                redacted[key_str] = "[redacted]"
            else:
                redacted[key_str] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


class OPCDashboard:
    """Read-only dashboard aggregation for OPC daily review."""

    def __init__(
        self,
        resale_manager: Any = None,
        work_order_manager: Any = None,
        execution_queue: Any = None,
        message_intake_ledger: Any = None,
        discovery_inbox_store: Any = None,
        order_runtime_store: Any = None,
        account_profile_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        adapter_health_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        message_import_scheduler_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        self._resale = resale_manager
        self._work_orders = work_order_manager
        self._exec_queue = execution_queue
        self._intake = message_intake_ledger
        self._discovery_inbox = discovery_inbox_store
        self._order_runtime = order_runtime_store
        self._account_profile_provider = account_profile_provider
        self._adapter_health_provider = adapter_health_provider
        self._message_import_scheduler_provider = message_import_scheduler_provider

    def get_opc_daily(self) -> Dict[str, Any]:
        """Aggregate all dashboard data into a single read-only snapshot."""
        source_notes: List[str] = []

        # 1. Today actions
        today_actions = self._aggregate_today_actions(source_notes)

        # 2. Funnel metrics
        funnel = self._aggregate_funnel(source_notes)

        # 3. Category performance
        category_performance = self._aggregate_category_performance(source_notes)

        # 4. Bottleneck analysis
        bottleneck = self._analyze_bottleneck(funnel, category_performance)

        # 5. Asset updates (placeholder — future: read from data/assets/)
        asset_updates = {
            "new_reply_templates": 0,
            "new_title_patterns": 0,
            "supplier_notes_updated": 0,
            "note": "资产统计功能待实现（G2/G3 增强项）",
        }

        # 6. Account and execution readiness
        account_readiness = self._aggregate_account_readiness(source_notes)
        adapter_health = self._adapter_health_snapshot(source_notes)
        message_import_scheduler = self._message_import_scheduler_snapshot(source_notes)
        order_runtime = self._aggregate_order_runtime(source_notes)
        execution_evidence = self._aggregate_execution_evidence(source_notes)
        next_actions = self._build_next_actions(
            today_actions=today_actions,
            funnel=funnel,
            bottleneck=bottleneck,
            account_readiness=account_readiness,
            adapter_health=adapter_health,
            message_import_scheduler=message_import_scheduler,
            order_runtime=order_runtime,
            execution_evidence=execution_evidence,
        )

        return {
            "today_actions": today_actions,
            "funnel": funnel,
            "category_performance": category_performance,
            "bottleneck": bottleneck,
            "asset_updates": asset_updates,
            "account_readiness": account_readiness,
            "adapter_health": adapter_health,
            "message_import_scheduler": message_import_scheduler,
            "order_runtime": order_runtime,
            "execution_evidence": execution_evidence,
            "next_actions": next_actions,
            "source_notes": source_notes,
            "generated_at": _now_iso(),
        }

    def generate_weekly_review(self, output_dir: Path) -> Dict[str, Any]:
        """Generate a weekly review Markdown snapshot and save to output_dir."""
        daily = self.get_opc_daily()

        today = _today_str()
        filename = f"weekly_review_{today}.md"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename

        md = self._render_weekly_markdown(daily, today)
        output_path.write_text(md, encoding="utf-8")

        return {
            "ok": True,
            "file": str(output_path),
            "generated_at": _now_iso(),
        }

    def generate_daily_review(self, output_dir: Path, *, account_queues: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate a daily operating snapshot with dashboard and account queue state."""
        daily = self.get_opc_daily()
        queues = account_queues or {"accounts": [], "unassigned": {}}

        today = _today_str()
        filename = f"daily_review_{today}.md"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename

        md = self._render_daily_markdown(daily, queues, today)
        output_path.write_text(md, encoding="utf-8")

        return {
            "ok": True,
            "file": str(output_path),
            "generated_at": _now_iso(),
        }

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def _aggregate_today_actions(self, source_notes: List[str]) -> Dict[str, int]:
        """Count today's pending actions across all subsystems."""
        result = {
            "pending_approvals": 0,
            "pending_execution": 0,
            "pending_reply_drafts": 0,
            "failed_jobs": 0,
            "verification_required": 0,
            "new_discoveries": 0,
        }

        # Work orders with pending approvals
        if self._work_orders is not None:
            try:
                orders = self._work_orders.list_work_orders(limit=500)
                for wo in orders:
                    for approval in wo.get("approvals", []):
                        if approval.get("status") == "pending":
                            result["pending_approvals"] += 1
            except Exception:
                source_notes.append("work_orders: 数据读取失败")
        else:
            source_notes.append("work_orders: 数据源未配置")

        # Execution queue
        if self._exec_queue is not None:
            try:
                jobs = self._exec_queue.list_jobs(limit=500)
                result["pending_execution"] = _safe_count(jobs, lambda j: j.get("status") == "queued")
                result["failed_jobs"] = _safe_count(jobs, lambda j: j.get("status") == "failed")
                result["verification_required"] = _safe_count(
                    jobs,
                    lambda j: j.get("status") == "verification_required",
                )
            except Exception:
                source_notes.append("execution_queue: 数据读取失败")
        else:
            source_notes.append("execution_queue: 数据源未配置")

        # Message intake
        if self._intake is not None:
            try:
                metrics = self._intake.metrics()
                result["pending_reply_drafts"] = metrics.get("manual_reply_fallbacks", 0)
            except Exception:
                source_notes.append("message_intake: 数据读取失败")
        else:
            source_notes.append("message_intake: 数据源未配置")

        # Resale candidates (new discoveries today)
        if self._resale is not None:
            try:
                candidates = self._resale.list_candidates()
                today_str = _today_str()
                result["new_discoveries"] = sum(
                    1 for c in candidates
                    if c.get("created_at", "").startswith(today_str)
                )
            except Exception:
                source_notes.append("resale_candidates: 数据读取失败")
        else:
            source_notes.append("resale_candidates: 数据源未配置")

        # Discovery inbox counts only unread event envelopes. Raw payloads stay in the store.
        if self._discovery_inbox is not None:
            try:
                events = self._discovery_inbox.list_events(status="unread", limit=500)
                result["new_discoveries"] += len(events or [])
            except Exception:
                source_notes.append("discovery_inbox: read failed")
        else:
            source_notes.append("discovery_inbox: data source not configured")

        return result

    def _aggregate_funnel(self, source_notes: List[str]) -> Dict[str, Any]:
        """Aggregate the candidate-to-order funnel."""
        funnel = {
            "new_candidates_this_week": 0,
            "testing": 0,
            "with_inquiries": 0,
            "converted": 0,
            "after_sales": 0,
        }

        if self._resale is None:
            source_notes.append("funnel: resale 数据源未配置")
            return funnel

        try:
            candidates = self._resale.list_candidates()
            orders = self._resale.list_resale_orders() if hasattr(self._resale, "list_resale_orders") else []

            # Candidates by status
            funnel["new_candidates_this_week"] = len(candidates)  # Simplified: all candidates
            funnel["testing"] = _safe_count(candidates, lambda c: c.get("status") == "approved")
            funnel["with_inquiries"] = _safe_count(candidates, lambda c: c.get("manual_decision") == "approve")

            # Orders
            funnel["converted"] = _safe_count(orders, lambda o: o.get("status") == "completed")
            funnel["after_sales"] = _safe_count(
                orders, lambda o: o.get("status") in ("refund_risk", "refunded")
            )
        except Exception as e:
            source_notes.append(f"funnel: 聚合失败 ({e})")

        return funnel

    def _aggregate_category_performance(self, source_notes: List[str]) -> List[Dict[str, Any]]:
        """Group candidates by category and compute per-category stats."""
        if self._resale is None:
            source_notes.append("category_performance: resale 数据源未配置")
            return []

        try:
            candidates = self._resale.list_candidates()
            orders = self._resale.list_resale_orders() if hasattr(self._resale, "list_resale_orders") else []
        except Exception as e:
            source_notes.append(f"category_performance: 数据读取失败 ({e})")
            return []

        # Group candidates by category
        cat_candidates: Dict[str, List[Dict]] = defaultdict(list)
        for c in candidates:
            cat = c.get("category") or "未分类"
            cat_candidates[cat].append(c)

        # Group orders by candidate category
        candidate_cat_map = {c.get("candidate_id"): c.get("category", "未分类") for c in candidates}
        cat_orders: Dict[str, List[Dict]] = defaultdict(list)
        for o in orders:
            cat = candidate_cat_map.get(o.get("candidate_id", ""), "未分类")
            cat_orders[cat].append(o)

        result = []
        for cat, cat_cands in cat_candidates.items():
            cat_order_list = cat_orders.get(cat, [])
            revenue = round(sum(o.get("actual_profit", 0) for o in cat_order_list), 2)
            order_count = _safe_count(cat_order_list, lambda o: o.get("status") == "completed")

            result.append({
                "category": cat,
                "candidates": len(cat_cands),
                "approved": _safe_count(cat_cands, lambda c: c.get("manual_decision") == "approve"),
                "orders": order_count,
                "revenue": revenue,
                "action": self._suggest_category_action(cat_cands, cat_order_list),
            })

        result.sort(key=lambda x: x["candidates"], reverse=True)
        return result

    def _aggregate_account_readiness(self, source_notes: List[str]) -> Dict[str, Any]:
        """Summarize whether configured accounts are ready for daily seller ops."""
        result: Dict[str, Any] = {
            "total_accounts": 0,
            "enabled_accounts": 0,
            "ready_for_seller_ops": 0,
            "ai_enabled_accounts": 0,
            "needs_attention": 0,
            "recommendations": [],
        }
        if self._account_profile_provider is None:
            source_notes.append("account_profiles: data source not configured")
            return result

        try:
            profiles = list(self._account_profile_provider() or [])
        except Exception as exc:
            source_notes.append(f"account_profiles: read failed ({exc})")
            return result

        result["total_accounts"] = len(profiles)
        for profile in profiles:
            capabilities = profile.get("capabilities") or {}
            counts = profile.get("counts") or {}
            ai = profile.get("ai") or {}
            enabled = bool(profile.get("enabled"))
            seller_auto_reply = bool(capabilities.get("seller_auto_reply"))
            has_inventory_context = int(counts.get("items") or 0) > 0
            ready = enabled and seller_auto_reply and has_inventory_context

            if enabled:
                result["enabled_accounts"] += 1
            if ready:
                result["ready_for_seller_ops"] += 1
            if ai.get("enabled"):
                result["ai_enabled_accounts"] += 1

            recommendations = list(profile.get("recommended_next_actions") or [])
            if not ready:
                result["needs_attention"] += 1
                result["recommendations"].append({
                    "cookie_id": str(profile.get("cookie_id") or ""),
                    "actions": recommendations[:3],
                })

        return result

    def _adapter_health_snapshot(self, source_notes: List[str]) -> Dict[str, Any]:
        """Return sanitized execution adapter health."""
        if self._adapter_health_provider is None:
            source_notes.append("adapter_health: data source not configured")
            return {
                "adapter": "unknown",
                "configured": False,
                "ready": False,
                "missing": [],
                "notes": ["Adapter health source is not configured."],
            }
        try:
            health = dict(self._adapter_health_provider() or {})
        except Exception as exc:
            source_notes.append(f"adapter_health: read failed ({exc})")
            return {
                "adapter": "unknown",
                "configured": False,
                "ready": False,
                "missing": [],
                "notes": [f"Adapter health read failed: {exc}"],
            }
        return _redact_secret_fields(health)

    def _message_import_scheduler_snapshot(self, source_notes: List[str]) -> Dict[str, Any]:
        """Return message import scheduler state without exposing raw cookie fields."""
        fallback = {
            "enabled": False,
            "account": "",
            "interval_seconds": 0,
            "due": False,
            "run_count": 0,
            "consecutive_failures": 0,
            "backoff_seconds": 0,
            "total_profiles": 0,
            "due_profiles": [],
            "profiles": {},
            "last_run_at": "",
            "last_error": "",
            "last_result": {},
            "health": build_scheduler_health_actions([], {}),
        }
        if self._message_import_scheduler_provider is None:
            source_notes.append("message_import_scheduler: data source not configured")
            return fallback
        try:
            raw = dict(self._message_import_scheduler_provider() or {})
        except Exception as exc:
            source_notes.append(f"message_import_scheduler: read failed ({exc})")
            return fallback

        last = raw.get("last_result") if isinstance(raw.get("last_result"), dict) else {}
        raw_profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
        profiles: Dict[str, Any] = {}
        for key, value in raw_profiles.items():
            profile = value if isinstance(value, dict) else {}
            profile_last = profile.get("last_result") if isinstance(profile.get("last_result"), dict) else {}
            profiles[str(key)] = {
                "account": str(profile.get("account") or profile.get("cookie_id") or key),
                "enabled": bool(profile.get("enabled")),
                "due": bool(profile.get("due")),
                "run_count": int(profile.get("run_count") or 0),
                "consecutive_failures": int(profile.get("consecutive_failures") or 0),
                "backoff_seconds": int(profile.get("backoff_seconds") or 0),
                "last_run_at": str(profile.get("last_run_at") or ""),
                "last_error": str(profile.get("last_error") or ""),
                "last_result": {
                    "created": int(profile_last.get("created") or 0),
                    "duplicates": int(profile_last.get("duplicates") or 0),
                    "failed": int(profile_last.get("failed") or 0),
                },
            }
        due_profiles = raw.get("due_profiles") if isinstance(raw.get("due_profiles"), list) else []
        if not due_profiles:
            due_profiles = [key for key, value in profiles.items() if value.get("enabled") and value.get("due")]
        fallback.update(
            {
                "enabled": bool(raw.get("enabled")),
                "account": str(raw.get("account") or raw.get("cookie_id") or ""),
                "interval_seconds": int(raw.get("interval_seconds") or 0),
                "due": bool(raw.get("due")),
                "run_count": int(raw.get("run_count") or 0),
                "consecutive_failures": int(raw.get("consecutive_failures") or 0),
                "backoff_seconds": int(raw.get("backoff_seconds") or 0),
                "total_profiles": int(raw.get("total_profiles") or len(profiles)),
                "due_profiles": [str(item) for item in due_profiles],
                "profiles": profiles,
                "last_run_at": str(raw.get("last_run_at") or ""),
                "last_error": str(raw.get("last_error") or ""),
                "last_result": {
                    "created": int(last.get("created") or 0),
                    "duplicates": int(last.get("duplicates") or 0),
                    "failed": int(last.get("failed") or 0),
                    "conversations_selected": int(last.get("conversations_selected") or 0),
                    "conversations_total": int(last.get("conversations_total") or 0),
                },
            }
        )
        account_profiles: List[Dict[str, Any]] = []
        if self._account_profile_provider is not None:
            try:
                account_profiles = list(self._account_profile_provider() or [])
            except Exception as exc:
                source_notes.append(f"message_import_scheduler_health: account read failed ({exc})")
        fallback["health"] = build_scheduler_health_actions(account_profiles, raw)
        return fallback

    def _aggregate_order_runtime(self, source_notes: List[str]) -> Dict[str, Any]:
        """Summarize order operating state without exposing buyer details."""
        fallback = {
            "total_orders": 0,
            "operational_orders": 0,
            "synthetic_orders": 0,
            "needs_fulfillment": 0,
            "after_sales": 0,
            "blocked": 0,
            "locked": 0,
            "by_status": {},
            "next_actions": [],
        }
        if self._order_runtime is None:
            source_notes.append("order_runtime: data source not configured")
            return fallback
        try:
            summary = dict(self._order_runtime.summary() or {})
        except Exception as exc:
            source_notes.append(f"order_runtime: read failed ({exc})")
            return fallback
        fallback.update(_redact_secret_fields(summary))
        return fallback

    def _aggregate_execution_evidence(self, source_notes: List[str]) -> Dict[str, Any]:
        """Summarize failed execution jobs with captured blocker evidence."""
        result = {
            "failed_with_blocker": 0,
            "verification_with_evidence": 0,
            "artifact_count": 0,
            "recent_blockers": [],
        }
        if self._exec_queue is None:
            source_notes.append("execution_evidence: execution queue not configured")
            return result
        try:
            jobs = self._exec_queue.list_jobs(limit=500)
        except Exception as exc:
            source_notes.append(f"execution_evidence: read failed ({exc})")
            return result
        blockers = []
        verification_with_evidence = 0
        artifact_count = 0
        for job in jobs:
            evidence = job.get("evidence") if isinstance(job.get("evidence"), dict) else {}
            blocker = str(evidence.get("detected_blocker") or "").strip()
            has_artifact = bool(evidence.get("after_screenshot") or evidence.get("evidence_manifest"))
            if has_artifact:
                artifact_count += 1
            if job.get("status") == "verification_required" and has_artifact:
                verification_with_evidence += 1
            if job.get("status") in {"failed", "verification_required"} and blocker:
                blockers.append(
                    {
                        "job_id": str(job.get("job_id") or ""),
                        "action": str(job.get("action") or ""),
                        "status": str(job.get("status") or ""),
                        "detected_blocker": blocker,
                        "operator_next_action": str(job.get("operator_next_action") or ""),
                        "evidence_manifest": str(evidence.get("evidence_manifest") or ""),
                        "after_screenshot": str(evidence.get("after_screenshot") or ""),
                    }
                )
        result["failed_with_blocker"] = sum(1 for item in blockers if item["status"] == "failed")
        result["verification_with_evidence"] = verification_with_evidence
        result["artifact_count"] = artifact_count
        result["recent_blockers"] = blockers[-5:]
        return result

    @staticmethod
    def _build_next_actions(
        today_actions: Dict[str, Any],
        funnel: Dict[str, Any],
        bottleneck: Dict[str, Any],
        account_readiness: Dict[str, Any],
        adapter_health: Dict[str, Any],
        message_import_scheduler: Optional[Dict[str, Any]] = None,
        order_runtime: Optional[Dict[str, Any]] = None,
        execution_evidence: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build an operator action queue ordered by daily impact."""
        actions: List[Dict[str, Any]] = []

        def add(priority: str, kind: str, title: str, detail: str = "", count: int = 0) -> None:
            actions.append({
                "priority": priority,
                "kind": kind,
                "title": title,
                "detail": detail,
                "count": int(count or 0),
            })

        pending_approvals = int(today_actions.get("pending_approvals") or 0)
        failed_jobs = int(today_actions.get("failed_jobs") or 0)
        verification_required = int(today_actions.get("verification_required") or 0)
        pending_replies = int(today_actions.get("pending_reply_drafts") or 0)
        pending_execution = int(today_actions.get("pending_execution") or 0)
        new_discoveries = int(today_actions.get("new_discoveries") or 0)
        total_candidates = int(funnel.get("new_candidates_this_week") or 0)
        order_runtime = order_runtime or {}
        execution_evidence = execution_evidence or {}
        message_import_scheduler = message_import_scheduler or {}
        scheduler_health = (
            message_import_scheduler.get("health")
            if isinstance(message_import_scheduler.get("health"), dict)
            else {}
        )
        order_blockers = int(order_runtime.get("blocked") or 0)
        needs_fulfillment = int(order_runtime.get("needs_fulfillment") or 0)
        after_sales = int(order_runtime.get("after_sales") or 0)
        failed_with_blocker = int(execution_evidence.get("failed_with_blocker") or 0)

        if pending_approvals:
            add("high", "pending_approvals", "Review pending approvals", count=pending_approvals)
        if verification_required:
            add(
                "high",
                "verification_required",
                "Verify unknown platform outcomes before retrying",
                count=verification_required,
            )
        if failed_jobs:
            add("high", "failed_jobs", "Inspect failed execution jobs", count=failed_jobs)
        if failed_with_blocker:
            add(
                "high",
                "execution_blocker",
                "Resolve execution blockers with captured evidence",
                count=failed_with_blocker,
            )
        failed_scheduler_profiles = list(scheduler_health.get("failed_profiles") or [])
        if failed_scheduler_profiles:
            first_failure = failed_scheduler_profiles[0]
            add(
                "high",
                "message_import_scheduler_error",
                "Repair failed message import scheduler profiles",
                detail=str(first_failure.get("last_error") or first_failure.get("account") or ""),
                count=len(failed_scheduler_profiles),
            )
        elif message_import_scheduler.get("last_error"):
            add(
                "high",
                "message_import_scheduler_error",
                "Fix message import scheduler error",
                detail=str(message_import_scheduler.get("last_error") or ""),
            )
        if order_blockers:
            add("high", "order_blocker", "Resolve blocked orders", count=order_blockers)
        if not bool(adapter_health.get("ready")):
            missing = ", ".join(str(item) for item in adapter_health.get("missing") or [])
            add("high", "adapter_not_ready", "Complete execution adapter setup", detail=missing)
        if int(account_readiness.get("total_accounts") or 0) == 0:
            add("high", "no_accounts", "Add the first Xianyu account")
        elif int(account_readiness.get("needs_attention") or 0) > 0:
            add(
                "medium",
                "account_attention",
                "Fix account readiness issues",
                count=int(account_readiness.get("needs_attention") or 0),
            )
        if pending_replies:
            add("medium", "pending_reply_drafts", "Review buyer reply drafts", count=pending_replies)
        if pending_execution:
            add("medium", "pending_execution", "Run approved execution jobs", count=pending_execution)
        missing_scheduler_profiles = list(scheduler_health.get("missing_profiles") or [])
        if missing_scheduler_profiles:
            add(
                "medium",
                "scheduler_profile_missing",
                "Bootstrap missing message import scheduler profiles",
                detail=", ".join(str(item) for item in missing_scheduler_profiles[:3]),
                count=len(missing_scheduler_profiles),
            )
        due_scheduler_profiles = list(scheduler_health.get("due_profiles") or [])
        if due_scheduler_profiles:
            add(
                "medium",
                "message_import_due",
                "Run due buyer-message import profiles",
                detail=", ".join(str(item) for item in due_scheduler_profiles[:3]),
                count=len(due_scheduler_profiles),
            )
        elif message_import_scheduler.get("enabled") and message_import_scheduler.get("due"):
            add("medium", "message_import_due", "Run due buyer-message import polling")
        if needs_fulfillment:
            add("medium", "order_fulfillment", "Verify and fulfill paid orders", count=needs_fulfillment)
        if after_sales:
            add("medium", "order_after_sales", "Review after-sales orders", count=after_sales)
        if new_discoveries:
            add("medium", "review_discovery_inbox", "Review new discovery leads", count=new_discoveries)
        elif total_candidates == 0:
            add("medium", "run_discovery", "Run product discovery for today's test batch")

        stale_scheduler_profiles = list(scheduler_health.get("stale_profiles") or [])
        if stale_scheduler_profiles:
            add(
                "low",
                "message_import_scheduler_stale",
                "Review stale message import scheduler profiles",
                count=len(stale_scheduler_profiles),
            )
        orphaned_scheduler_profiles = list(scheduler_health.get("orphaned_profiles") or [])
        if orphaned_scheduler_profiles:
            add(
                "low",
                "message_import_scheduler_orphaned",
                "Pause schedulers for disabled accounts",
                count=len(orphaned_scheduler_profiles),
            )

        bottleneck_stage = str(bottleneck.get("stage") or "")
        if bottleneck_stage:
            add(
                "low",
                "bottleneck_review",
                "Review current funnel bottleneck",
                detail=str(bottleneck.get("description") or ""),
                count=int(bottleneck.get("drop_off") or 0),
            )

        priority_rank = {"high": 0, "medium": 1, "low": 2}
        actions.sort(key=lambda item: priority_rank.get(item["priority"], 9))
        return actions[:8]

    @staticmethod
    def _suggest_category_action(candidates: List[Dict], orders: List[Dict]) -> str:
        """Suggest action for a category based on its performance."""
        order_count = _safe_count(orders, lambda o: o.get("status") == "completed")
        approved = _safe_count(candidates, lambda c: c.get("manual_decision") == "approve")

        if order_count > 0:
            return "继续测试，考虑增加链接变体"
        if approved > 0:
            return "已有审批候选，等待成交反馈"
        if len(candidates) > 3:
            return "候选较多但无成交，检查选品或标题"
        return "继续探索"

    @staticmethod
    def _analyze_bottleneck(
        funnel: Dict[str, Any],
        category_performance: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Identify the biggest bottleneck in the conversion funnel."""
        candidates = funnel.get("new_candidates_this_week", 0)
        testing = funnel.get("testing", 0)
        inquiries = funnel.get("with_inquiries", 0)
        converted = funnel.get("converted", 0)

        # Find the stage with the biggest drop-off
        stages = [
            ("discovery", candidates, testing, "候选多但审批少：选品质量或供应商验证不足"),
            ("listing", testing, inquiries, "审批多但无咨询：标题、首图或价格需要优化"),
            ("inquiry", inquiries, converted, "有咨询但无成交：回复策略或定价需要调整"),
        ]

        worst_stage = "discovery"
        worst_drop = 0
        worst_suggestion = stages[0][3]

        for stage_name, input_count, output_count, suggestion in stages:
            if input_count > 0:
                drop = input_count - output_count
                if drop > worst_drop:
                    worst_drop = drop
                    worst_stage = stage_name
                    worst_suggestion = suggestion

        # If no data, default
        if candidates == 0:
            worst_stage = "discovery"
            worst_suggestion = "暂无候选数据，建议先运行选品发现"

        # Find affected candidates (those stuck at the bottleneck stage)
        affected = []
        if worst_stage == "listing":
            # Candidates approved but no orders
            for cat in category_performance:
                if cat.get("approved", 0) > 0 and cat.get("orders", 0) == 0:
                    affected.append(cat.get("category", ""))
        elif worst_stage == "discovery":
            # Categories with few candidates
            for cat in category_performance:
                if cat.get("candidates", 0) < 3:
                    affected.append(cat.get("category", ""))

        return {
            "stage": worst_stage,
            "description": worst_suggestion,
            "drop_off": worst_drop,
            "affected_categories": affected[:5],
        }

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _render_weekly_markdown(daily: Dict[str, Any], date_str: str) -> str:
        """Render the weekly review as Markdown."""
        actions = daily.get("today_actions", {})
        funnel = daily.get("funnel", {})
        categories = daily.get("category_performance", [])
        bottleneck = daily.get("bottleneck", {})
        assets = daily.get("asset_updates", {})
        notes = daily.get("source_notes", [])

        lines = [
            f"# OPC 周复盘 — {date_str}",
            "",
            "> 本文件由系统自动生成，仅供参考。请结合实际运营判断。",
            "",
            "## 今日待办",
            "",
            f"| 指标 | 数值 |",
            f"|---|---|",
            f"| 待审批 | {actions.get('pending_approvals', 0)} |",
            f"| 待执行 | {actions.get('pending_execution', 0)} |",
            f"| 待回复草稿 | {actions.get('pending_reply_drafts', 0)} |",
            f"| 待核验外部结果 | {actions.get('verification_required', 0)} |",
            f"| 失败任务 | {actions.get('failed_jobs', 0)} |",
            f"| 今日新发现 | {actions.get('new_discoveries', 0)} |",
            "",
            "## 漏斗指标",
            "",
            f"| 阶段 | 数值 |",
            f"|---|---|",
            f"| 全部候选 | {funnel.get('new_candidates_this_week', 0)} |",
            f"| 测试中 | {funnel.get('testing', 0)} |",
            f"| 有咨询 | {funnel.get('with_inquiries', 0)} |",
            f"| 已成交 | {funnel.get('converted', 0)} |",
            f"| 售后/退款 | {funnel.get('after_sales', 0)} |",
            "",
            "## 品类表现",
            "",
        ]

        if categories:
            lines.append("| 品类 | 候选数 | 已审批 | 成交 | 利润 | 建议 |")
            lines.append("|---|---|---|---|---|---|")
            for cat in categories:
                lines.append(
                    f"| {cat.get('category', '未分类')} "
                    f"| {cat.get('candidates', 0)} "
                    f"| {cat.get('approved', 0)} "
                    f"| {cat.get('orders', 0)} "
                    f"| ¥{cat.get('revenue', 0)} "
                    f"| {cat.get('action', '')} |"
                )
        else:
            lines.append("暂无品类数据。")

        lines.extend([
            "",
            "## 瓶颈分析",
            "",
            f"- **当前瓶颈阶段**: {bottleneck.get('stage', '未知')}",
            f"- **说明**: {bottleneck.get('description', '暂无数据')}",
            f"- **漏斗缺口**: {bottleneck.get('drop_off', 0)}",
        ])

        affected = bottleneck.get("affected_categories", [])
        if affected:
            lines.append(f"- **受影响品类**: {', '.join(affected)}")

        lines.extend([
            "",
            "## 资产更新",
            "",
            f"- 本周新增回复模板: {assets.get('new_reply_templates', 0)}",
            f"- 本周新增标题模式: {assets.get('new_title_patterns', 0)}",
            f"- 供应商备注更新: {assets.get('supplier_notes_updated', 0)}",
        ])

        if notes:
            lines.extend([
                "",
                "## 数据源说明",
                "",
            ])
            for note in notes:
                lines.append(f"- {note}")

        lines.extend([
            "",
            "---",
            "",
            "## 下一步优先级",
            "",
            "**请填写本周的一个具体行动**：",
            "",
            "```",
            "例如：",
            "- 优化 3 个候选的首图和标题",
            "- 为商用厨具品类补充 5 个新候选",
            "- 测试不锈钢制品的回复模板",
            "- 与供应商谈判降低操作台成本",
            "```",
            "",
        ])

        return "\n".join(lines)

    @staticmethod
    def _render_daily_markdown(daily: Dict[str, Any], account_queues: Dict[str, Any], date_str: str) -> str:
        """Render a concise daily operating snapshot as Markdown."""
        actions = daily.get("today_actions", {})
        funnel = daily.get("funnel", {})
        bottleneck = daily.get("bottleneck", {})
        next_actions = daily.get("next_actions", [])
        accounts = account_queues.get("accounts") or []
        unassigned = account_queues.get("unassigned") or {}

        lines = [
            f"# OPC Daily Review - {date_str}",
            "",
            "## Today Actions",
            "",
            "| Metric | Count |",
            "|---|---:|",
            f"| pending_approvals | {actions.get('pending_approvals', 0)} |",
            f"| pending_execution | {actions.get('pending_execution', 0)} |",
            f"| pending_reply_drafts | {actions.get('pending_reply_drafts', 0)} |",
            f"| verification_required | {actions.get('verification_required', 0)} |",
            f"| failed_jobs | {actions.get('failed_jobs', 0)} |",
            f"| new_discoveries | {actions.get('new_discoveries', 0)} |",
            "",
            "## Funnel",
            "",
            "| Stage | Count |",
            "|---|---:|",
            f"| new_candidates_this_week | {funnel.get('new_candidates_this_week', 0)} |",
            f"| testing | {funnel.get('testing', 0)} |",
            f"| with_inquiries | {funnel.get('with_inquiries', 0)} |",
            f"| converted | {funnel.get('converted', 0)} |",
            f"| after_sales | {funnel.get('after_sales', 0)} |",
            "",
            "## Account Queues",
            "",
            "| Account | Approvals | Execution | Verify | Failed | Replies | Items | Orders | sync_freshness |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]

        if accounts:
            for account in accounts:
                queues = account.get("queues") or {}
                lines.append(
                    "| "
                    + str(account.get("cookie_id") or "-")
                    + f" | {queues.get('pending_approvals', 0)}"
                    + f" | {queues.get('queued_execution', 0)}"
                    + f" | {queues.get('verification_required', 0)}"
                    + f" | {queues.get('failed_jobs', 0)}"
                    + f" | {queues.get('pending_reply_drafts', 0)}"
                    + f" | {queues.get('items', 0)}"
                    + f" | {queues.get('orders_total', 0)}"
                    + f" | {OPCDashboard._format_sync_freshness(account.get('sync_freshness') or {})} |"
                )
        else:
            lines.append("| none | 0 | 0 | 0 | 0 | 0 | 0 | 0 | none |")

        unassigned_queues = unassigned.get("queues") if isinstance(unassigned, dict) else {}
        if unassigned_queues:
            lines.extend([
                "",
                "## Unassigned Queue",
                "",
                f"- pending_approvals: {unassigned_queues.get('pending_approvals', 0)}",
                f"- queued_execution: {unassigned_queues.get('queued_execution', 0)}",
                f"- verification_required: {unassigned_queues.get('verification_required', 0)}",
                f"- pending_reply_drafts: {unassigned_queues.get('pending_reply_drafts', 0)}",
            ])

        lines.extend([
            "",
            "## Bottleneck",
            "",
            f"- stage: {bottleneck.get('stage', '')}",
            f"- description: {bottleneck.get('description', '')}",
            "",
            "## Next Actions",
            "",
        ])

        if next_actions:
            for action in next_actions[:8]:
                lines.append(
                    f"- [{action.get('priority', 'low')}] {action.get('title', action.get('kind', '-'))}: "
                    f"{action.get('detail', '')}"
                )
        else:
            lines.append("- No generated next actions.")

        lines.extend([
            "",
            "## Operator Notes",
            "",
            "- What changed today:",
            "- What to test next:",
            "- What to stop doing:",
            "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _format_sync_freshness(sync_freshness: Dict[str, Any]) -> str:
        if not sync_freshness:
            return "none"
        parts: List[str] = []
        for resource in sorted(sync_freshness):
            state = sync_freshness.get(resource) or {}
            status = "fresh" if state.get("fresh") else "stale"
            parts.append(
                f"{resource}:{status},count={state.get('last_count', 0)},adapter={state.get('adapter', '')}"
            )
        return "; ".join(parts)
