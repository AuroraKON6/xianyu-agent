"""GitHub Agent - Discover opportunities from GitHub repositories.

Delegates to workflow.github_scanner for real GitHub API calls,
then uses MiMo V2.5 Pro for analysis.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base import BaseAgent

logger = logging.getLogger(__name__)


class GitHubAgent(BaseAgent):
    name = "github"
    description = "GitHub project discovery and analysis"

    def find_opportunities(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Scan GitHub for opportunities using the real GitHub API.

        Uses workflow.discover_github_opportunities() which:
        1. Searches GitHub API with configured queries
        2. Scores repos by stars, license, asset type
        3. Optionally analyzes with LLM for commercial viability
        """
        p = params or {}
        queries = p.get("queries")
        min_stars = p.get("min_stars")
        limit = p.get("limit")
        result = self.workflow.discover_github_opportunities(
            queries=queries,
            min_stars=min_stars,
            limit_per_query=limit,
            use_deepseek=True,
            include_readme=p.get("include_readme", False),
        )
        return {"ok": True, "results": result}

    def analyze_repo(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a specific GitHub repo for commercial potential.

        First checks if we have data from previous scans, then uses LLM.
        """
        repo_name = params.get("repo", "")
        description = params.get("description", "")
        stars = params.get("stars", 0)

        system = """你是GitHub项目商业分析师。分析项目的商业潜力和变现可能。
返回JSON: {"project":"","commercial_potential":"high/medium/low","monetization_ideas":[],"target_market":"","recommendation":""}"""
        result = self.chat_json(system, f"项目: {repo_name}\n描述: {description}\nStars: {stars}")
        return {"ok": True, "analysis": result} if result else {"ok": False, "error": "Analysis failed"}

    def get_handlers(self):
        return {
            "find_opportunities": self.find_opportunities,
            "analyze_repo": self.analyze_repo,
        }
