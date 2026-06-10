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
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _safe_count(items: list, predicate) -> int:
    """Count items matching predicate, handling missing keys gracefully."""
    return sum(1 for item in items if predicate(item))


class OPCDashboard:
    """Read-only dashboard aggregation for OPC daily review."""

    def __init__(
        self,
        resale_manager: Any = None,
        work_order_manager: Any = None,
        execution_queue: Any = None,
        message_intake_ledger: Any = None,
    ) -> None:
        self._resale = resale_manager
        self._work_orders = work_order_manager
        self._exec_queue = execution_queue
        self._intake = message_intake_ledger

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

        return {
            "today_actions": today_actions,
            "funnel": funnel,
            "category_performance": category_performance,
            "bottleneck": bottleneck,
            "asset_updates": asset_updates,
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
