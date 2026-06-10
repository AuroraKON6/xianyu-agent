from __future__ import annotations

import base64
from typing import Any, Dict, Iterable, List, Optional

import requests

from .config import Settings
from .llm import DeepSeekClient
from .storage import JsonStorage


GITHUB_API = "https://api.github.com"
PERMISSIVE_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "0BSD"}
COPYLEFT_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-2.0"}

SYSTEM_PROMPT = """You are a license-aware GitHub product opportunity analyst.
Return only valid JSON.
Judge whether a repository can inspire or support a sellable agent/productized service.
Do not recommend reselling someone else's repository as-is.
Prefer legal business models: hosted deployment, customization, integration, templates, support, training, or original derivative products that respect the license.
Fields: score, verdict, asset_type, license_risk, sellable_models, integration_ideas, target_buyers, xianyu_angle, red_flags, next_steps, reason.
asset_type is one of agent, framework, resource, template, demo, unknown.
score is 0-100. verdict is good, watch, or reject."""


class GitHubOpportunityScanner:
    def __init__(self, settings: Settings, storage: JsonStorage, llm: DeepSeekClient) -> None:
        self.settings = settings
        self.storage = storage
        self.llm = llm

    def discover(
        self,
        queries: Optional[Iterable[str]] = None,
        min_stars: Optional[int] = None,
        limit_per_query: Optional[int] = None,
        use_deepseek: bool = True,
        include_readme: bool = False,
    ) -> Dict[str, Any]:
        query_list = _clean_queries(queries) or _clean_queries(self.settings.github_discovery_queries.split(","))
        star_floor = min_stars if min_stars is not None else self.settings.github_discovery_min_stars
        per_query = limit_per_query if limit_per_query is not None else self.settings.github_discovery_limit_per_query

        seen = set()
        candidates: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        for query in query_list:
            try:
                repos = self.search(query, star_floor, per_query)
            except Exception as exc:
                errors.append({"query": query, "error": str(exc)})
                continue
            for repo in repos:
                full_name = repo["full_name"]
                if full_name in seen:
                    continue
                seen.add(full_name)
                if include_readme:
                    repo["readme_excerpt"] = self.fetch_readme_excerpt(full_name)
                repo["commercial_judgement"] = self.judge(repo, use_deepseek=use_deepseek)
                candidates.append(repo)

        candidates.sort(
            key=lambda item: (
                int(item.get("commercial_judgement", {}).get("score", 0)),
                int(item.get("stars", 0)),
            ),
            reverse=True,
        )
        report = {
            "queries": query_list,
            "min_stars": star_floor,
            "limit_per_query": per_query,
            "used_deepseek": bool(use_deepseek and self.llm.enabled),
            "include_readme": include_readme,
            "count": len(candidates),
            "errors": errors,
            "candidates": candidates,
        }
        path = self.storage.write_research("github-opportunities", report)
        report["saved_to"] = str(path)
        self.storage.append_event("research.github", {"count": len(candidates), "saved_to": str(path)})
        return report

    def search(self, query: str, min_stars: int, limit: int) -> List[Dict[str, Any]]:
        search_query = f"{query} stars:>={min_stars} archived:false"
        response = self._get(
            f"{GITHUB_API}/search/repositories",
            params={"q": search_query, "sort": "stars", "order": "desc", "per_page": max(1, min(limit, 20))},
            timeout=30,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        return [self._normalize_repo(item, query) for item in items]

    def fetch_readme_excerpt(self, full_name: str, max_chars: int = 3000) -> str:
        response = self._get(f"{GITHUB_API}/repos/{full_name}/readme", timeout=30)
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        data = response.json()
        if data.get("encoding") != "base64" or not data.get("content"):
            return ""
        raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return raw[:max_chars]

    def judge(self, repo: Dict[str, Any], use_deepseek: bool = True) -> Dict[str, Any]:
        fallback = heuristic_judgement(repo)
        if not use_deepseek or not self.llm.enabled:
            fallback["source"] = "heuristic"
            return fallback
        prompt = {
            "repo": repo,
            "business_context": (
                "The user wants to find high-star GitHub resources or agents that can be legally turned into "
                "sellable agent services/listings. Judge license risk and practical integration value."
            ),
        }
        try:
            judgement = self.llm.chat_json(SYSTEM_PROMPT, _json_prompt(prompt), temperature=0.2) or fallback
            judgement.setdefault("source", "deepseek")
            judgement.setdefault("score", fallback["score"])
            judgement.setdefault("license_risk", fallback["license_risk"])
            return judgement
        except Exception as exc:
            fallback["source"] = "heuristic_after_deepseek_error"
            fallback["deepseek_error"] = str(exc)
            return fallback

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "xianyu-commerce-agent",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        return headers

    def _get(self, url: str, **kwargs: Any) -> requests.Response:
        session = requests.Session()
        session.trust_env = self.settings.github_trust_env
        return session.get(url, headers=self._headers(), **kwargs)

    def _normalize_repo(self, item: Dict[str, Any], query: str) -> Dict[str, Any]:
        license_data = item.get("license") or {}
        return {
            "query": query,
            "full_name": item.get("full_name", ""),
            "name": item.get("name", ""),
            "html_url": item.get("html_url", ""),
            "description": item.get("description") or "",
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "watchers": item.get("watchers_count", 0),
            "open_issues": item.get("open_issues_count", 0),
            "language": item.get("language") or "",
            "topics": item.get("topics") or [],
            "license": {
                "key": license_data.get("key", ""),
                "name": license_data.get("name", ""),
                "spdx_id": license_data.get("spdx_id", ""),
            },
            "updated_at": item.get("updated_at", ""),
            "pushed_at": item.get("pushed_at", ""),
        }


def heuristic_judgement(repo: Dict[str, Any]) -> Dict[str, Any]:
    stars = int(repo.get("stars") or 0)
    spdx = (repo.get("license") or {}).get("spdx_id") or ""
    description = (repo.get("description") or "").lower()
    topics = " ".join(repo.get("topics") or []).lower()
    haystack = f"{description} {topics} {repo.get('name', '').lower()}"
    asset_type = classify_asset_type(haystack)

    score = 35
    if stars >= 20000:
        score += 25
    elif stars >= 5000:
        score += 18
    elif stars >= 1000:
        score += 12
    else:
        score += 6

    if spdx in PERMISSIVE_LICENSES:
        score += 20
        license_risk = "low"
    elif spdx in COPYLEFT_LICENSES:
        score += 5
        license_risk = "medium"
    elif spdx:
        score += 8
        license_risk = "medium"
    else:
        score -= 10
        license_risk = "high"

    if asset_type in {"agent", "framework"}:
        score += 15
    elif asset_type in {"resource", "template"}:
        score += 5
    if any(word in haystack for word in ["awesome", "list", "paper", "tutorial", "demo", "interview", "guide"]):
        score -= 5
    score = max(0, min(100, score))

    if license_risk == "low" and score >= 70:
        verdict = "good"
    elif score >= 55:
        verdict = "watch"
    else:
        verdict = "reject"

    red_flags = []
    if license_risk == "high":
        red_flags.append("No clear license; do not package or resell code until rights are confirmed.")
    if spdx in COPYLEFT_LICENSES:
        red_flags.append("Copyleft license; derivative distribution may require source disclosure.")

    return {
        "score": score,
        "verdict": verdict,
        "asset_type": asset_type,
        "license_risk": license_risk,
        "sellable_models": [
            "hosted deployment service",
            "custom integration package",
            "setup/support service",
            "original agent workflow inspired by the repo",
        ],
        "integration_ideas": [
            "wrap as an internal capability behind this workflow",
            "turn repeated setup into a service listing",
            "extract prompt/workflow patterns into an original OpenClaw skill",
        ],
        "target_buyers": ["small merchants", "content operators", "solo founders"],
        "xianyu_angle": "Sell setup, integration, and customization services; avoid selling copied source code.",
        "red_flags": red_flags,
        "next_steps": ["Read the license file", "Audit README and dependencies", "Build a small demo before listing"],
        "reason": "Heuristic score based on stars, license, and asset type relevance.",
    }


def _clean_queries(queries: Optional[Iterable[str]]) -> List[str]:
    if not queries:
        return []
    return [str(item).strip() for item in queries if str(item).strip()]


def classify_asset_type(haystack: str) -> str:
    if any(word in haystack for word in ["awesome", "guide", "interview", "tutorial", "course", "paper", "book"]):
        return "resource"
    if any(word in haystack for word in ["framework", "platform", "workflow builder", "orchestration"]):
        return "framework"
    if any(word in haystack for word in ["agent", "multiagent", "bot", "assistant", "autonomous"]):
        return "agent"
    if any(word in haystack for word in ["template", "starter", "boilerplate"]):
        return "template"
    if "demo" in haystack or "example" in haystack:
        return "demo"
    return "unknown"


def _json_prompt(payload: Dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
