"""Trial Run — guided trial execution for OPC validation.

Generates a step-by-step trial run plan, tracks progress, and produces
a final report. Does NOT auto-execute any high-risk actions.

Safety boundaries:
- Read-only plan generation and progress tracking.
- No auto-publish, auto-message, auto-order, auto-pay, auto-refund.
- All high-risk actions go through WorkOrder + Approval + ExecutionJobQueue.
- Report saves to local Markdown only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class TrialRun:
    """Guided trial run for validating OPC workflow on one category."""

    def __init__(
        self,
        category: str = "商用厨具",
        target_candidates: int = 5,
        target_approvals: int = 1,
        monitor_days: int = 14,
    ) -> None:
        self.category = category
        self.target_candidates = target_candidates
        self.target_approvals = target_approvals
        self.monitor_days = monitor_days
        self.trial_id = f"trial_{_today_str().replace('-', '')}"
        self.started_at = _now_iso()
        self.steps: List[Dict[str, Any]] = self._init_steps()
        self.results: Dict[str, Any] = {}

    def _init_steps(self) -> List[Dict[str, Any]]:
        return [
            {
                "step": 1,
                "name": "discovery",
                "description": f"运行选品发现，目标 {self.target_candidates} 个 {self.category} 候选",
                "methods": ["原材料法: 不锈钢", "高客单扫描", "同商品多链接测试"],
                "status": "pending",
                "completed_at": None,
                "output": None,
            },
            {
                "step": 2,
                "name": "scoring",
                "description": "对候选进行策略评分，筛选合格候选",
                "criteria": {
                    "cold_category": ">= 4",
                    "high_ticket": ">= 4",
                    "compliance_risk": ">= 4",
                },
                "status": "pending",
                "completed_at": None,
                "output": None,
            },
            {
                "step": 3,
                "name": "listing_drafts",
                "description": "为合格候选生成结构化上架草稿",
                "fields": ["标题", "描述", "价格", "FAQ", "图片搜索词"],
                "status": "pending",
                "completed_at": None,
                "output": None,
            },
            {
                "step": 4,
                "name": "approval",
                "description": f"人工审批，目标 {self.target_approvals} 个通过",
                "gate": "WorkOrder + Approval",
                "status": "pending",
                "completed_at": None,
                "output": None,
            },
            {
                "step": 5,
                "name": "publish",
                "description": "通过审批的候选发布到闲鱼",
                "gate": "ExecutionJobQueue + ActionExecutor",
                "status": "pending",
                "completed_at": None,
                "output": None,
            },
            {
                "step": 6,
                "name": "monitor",
                "description": f"监控 {self.monitor_days} 天，收集曝光/咨询/成交/售后数据",
                "status": "pending",
                "completed_at": None,
                "output": None,
            },
            {
                "step": 7,
                "name": "review",
                "description": "OPC 周复盘，产出试运行报告",
                "status": "pending",
                "completed_at": None,
                "output": None,
            },
        ]

    def complete_step(self, step: int, output: Optional[Dict[str, Any]] = None) -> None:
        """Mark a step as completed with optional output data."""
        for s in self.steps:
            if s["step"] == step:
                s["status"] = "completed"
                s["completed_at"] = _now_iso()
                s["output"] = output
                break

    def get_progress(self) -> Dict[str, Any]:
        """Return current trial progress."""
        completed = sum(1 for s in self.steps if s["status"] == "completed")
        return {
            "trial_id": self.trial_id,
            "category": self.category,
            "started_at": self.started_at,
            "progress": f"{completed}/{len(self.steps)}",
            "current_step": next((s["name"] for s in self.steps if s["status"] == "pending"), "completed"),
            "steps": self.steps,
        }

    def generate_report(
        self,
        candidates: List[Dict[str, Any]],
        orders: List[Dict[str, Any]],
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Generate the trial run report as Markdown."""
        today = _today_str()
        filename = f"trial_run_report_{today}.md"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename

        # Compute stats
        total_candidates = len(candidates)
        approved = sum(1 for c in candidates if c.get("manual_decision") == "approve")
        blocked = sum(1 for c in candidates if c.get("status") == "blocked")
        orders_completed = sum(1 for o in orders if o.get("status") == "completed")
        revenue = round(sum(o.get("actual_profit", 0) for o in orders if o.get("status") == "completed"), 2)

        # Scoring distribution
        scoring_dist: Dict[str, int] = {}
        for c in candidates:
            rec = c.get("scoring", {}).get("recommendation", "unknown")
            scoring_dist[rec] = scoring_dist.get(rec, 0) + 1

        md = self._render_report(
            today=today,
            total_candidates=total_candidates,
            approved=approved,
            blocked=blocked,
            orders_completed=orders_completed,
            revenue=revenue,
            scoring_dist=scoring_dist,
            candidates=candidates,
            orders=orders,
        )

        output_path.write_text(md, encoding="utf-8")

        return {
            "ok": True,
            "file": str(output_path),
            "generated_at": _now_iso(),
            "stats": {
                "candidates": total_candidates,
                "approved": approved,
                "orders": orders_completed,
                "revenue": revenue,
            },
        }

    def _render_report(
        self,
        *,
        today: str,
        total_candidates: int,
        approved: int,
        blocked: int,
        orders_completed: int,
        revenue: float,
        scoring_dist: Dict[str, int],
        candidates: List[Dict[str, Any]],
        orders: List[Dict[str, Any]],
    ) -> str:
        lines = [
            f"# 试运行报告 — {self.category}",
            "",
            f"> 试运行 ID: {self.trial_id}",
            f"> 开始时间: {self.started_at}",
            f"> 报告生成: {_now_iso()}",
            "",
            "## 目标",
            "",
            f"- 品类: {self.category}",
            f"- 候选目标: {self.target_candidates}",
            f"- 审批目标: {self.target_approvals}",
            f"- 监控期: {self.monitor_days} 天",
            "",
            "## 结果摘要",
            "",
            f"| 指标 | 实际 | 目标 | 达标 |",
            f"|---|---|---|---|",
            f"| 候选数 | {total_candidates} | {self.target_candidates} | {'✅' if total_candidates >= self.target_candidates else '❌'} |",
            f"| 审批通过 | {approved} | {self.target_approvals} | {'✅' if approved >= self.target_approvals else '❌'} |",
            f"| 成交数 | {orders_completed} | 1 | {'✅' if orders_completed >= 1 else '❌'} |",
            f"| 利润 | ¥{revenue} | >0 | {'✅' if revenue > 0 else '❌'} |",
            "",
            "## 评分分布",
            "",
        ]

        if scoring_dist:
            lines.append("| 推荐 | 数量 |")
            lines.append("|---|---|")
            for rec, count in sorted(scoring_dist.items()):
                lines.append(f"| {rec} | {count} |")
        else:
            lines.append("暂无评分数据。")

        lines.extend([
            "",
            "## 候选列表",
            "",
        ])

        if candidates:
            lines.append("| ID | 标题 | 品类 | 评分 | 推荐 | 状态 |")
            lines.append("|---|---|---|---|---|---|")
            for c in candidates[:20]:
                scoring = c.get("scoring", {})
                lines.append(
                    f"| {c.get('candidate_id', '-')} "
                    f"| {(c.get('title', '-')[:20])} "
                    f"| {c.get('category', '-')} "
                    f"| {scoring.get('total_score', '-')} "
                    f"| {scoring.get('recommendation', '-')} "
                    f"| {c.get('status', '-')} |"
                )
        else:
            lines.append("暂无候选数据。")

        lines.extend([
            "",
            "## 成功标准检查",
            "",
            f"- [ ] {'✅' if total_candidates >= self.target_candidates else '❌'} 候选发现 ≥ {self.target_candidates} 个",
            f"- [ ] {'✅' if approved >= self.target_approvals else '❌'} 审批通过 ≥ {self.target_approvals} 个",
            f"- [ ] {'✅' if orders_completed >= 1 else '❌'} 成交 ≥ 1 单",
            f"- [ ] 安全: WorkOrder + Approval 全程门控",
            f"- [ ] 安全: 无自动执行高风险动作",
            "",
            "## 下一步决策",
            "",
            "根据试运行结果，选择下一步：",
            "",
            "- **继续测试**: 扩大候选范围，测试更多品类",
            "- **放量**: 增加链接变体，谈判供应商价格",
            "- **暂停**: 当前品类无明显优势，换品类测试",
            "- **淘汰**: 品类验证失败，归档实验资产",
            "",
        ])

        return "\n".join(lines)


def load_trial_state(path: Path) -> Optional[Dict[str, Any]]:
    """Load trial run state from JSON file."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return None


def save_trial_state(path: Path, trial: TrialRun) -> None:
    """Save trial run state to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "trial_id": trial.trial_id,
        "category": trial.category,
        "started_at": trial.started_at,
        "target_candidates": trial.target_candidates,
        "target_approvals": trial.target_approvals,
        "monitor_days": trial.monitor_days,
        "steps": trial.steps,
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
