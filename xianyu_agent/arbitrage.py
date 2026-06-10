from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

import requests

from .config import Settings
from .feishu import FeishuNotifier
from .llm import DeepSeekClient
from .models import now_iso
from .storage import JsonStorage


ALLOWED_RIGHTS = {
    "owned",
    "resale_license",
    "plr",
    "mrr",
    "supplier_authorized",
    "affiliate",
    "public_domain",
    "open_license",
}

SYSTEM_PROMPT = """You are a resale-rights and profit analyst for virtual products.
Return only valid JSON.
The user wants to evaluate a knowledge/course/digital product opportunity.
You must reject unauthorized copying, reselling, or reuse of seller copy/images.
Only recommend listing if resale rights and asset rights are clear.
Judge payback, profit, market size, and operational risk.
Fields: verdict, resale_allowed, rights_risk, market_size, profit_potential, confidence, reasons, questions_for_user, listing_angle, red_flags, next_steps.
verdict is buy, watch, reject, or needs_rights_proof."""


class VirtualProductArbitrageAgent:
    def __init__(
        self,
        settings: Settings,
        storage: JsonStorage,
        llm: DeepSeekClient,
        notifier: FeishuNotifier,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.llm = llm
        self.notifier = notifier

    def evaluate(self, payload: Dict[str, Any], use_deepseek: bool = True, notify: Optional[bool] = None) -> Dict[str, Any]:
        product = normalize_product(payload)
        market_samples = normalize_market_samples(payload.get("market_samples") or [])
        if not market_samples:
            market_samples = self.search_market(product["title"])

        rights = evaluate_rights(payload)
        economics = compute_economics(
            acquisition_cost=to_float(payload.get("acquisition_cost") or payload.get("cost")),
            planned_price=to_float(payload.get("planned_price") or payload.get("resale_price")),
            platform_fee_rate=to_float(payload.get("platform_fee_rate"), self.settings.default_platform_fee_rate),
            setup_cost=to_float(payload.get("setup_cost"), 0.0),
            unit_delivery_cost=to_float(payload.get("unit_delivery_cost"), 0.0),
            market_samples=market_samples,
        )
        fallback = heuristic_judgement(product, rights, economics, market_samples, self.settings)
        judgement = fallback
        if use_deepseek and self.llm.enabled:
            judgement = self.deepseek_judgement(product, rights, economics, market_samples, fallback)

        opportunity = {
            "id": payload.get("id") or uuid4().hex[:12],
            "type": "virtual_product_opportunity",
            "status": initial_status(judgement, rights),
            "product": product,
            "rights": rights,
            "economics": economics,
            "market_samples": market_samples,
            "judgement": judgement,
            "decision": {},
            "assets": [],
            "purchase": {},
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        self.storage.save_opportunity(opportunity)
        self.storage.append_event("opportunity.evaluated", {"id": opportunity["id"], "status": opportunity["status"]})

        should_notify = self.settings.ask_feishu_on_opportunity if notify is None else notify
        if should_notify:
            self.notifier.send_text(format_feishu_question(opportunity))
        return opportunity

    def decide(self, opportunity_id: str, decision: str, note: str = "") -> Dict[str, Any]:
        opportunity = self.storage.load_opportunity(opportunity_id)
        normalized = normalize_decision(decision)
        opportunity["decision"] = {"value": normalized, "note": note, "created_at": now_iso()}
        if normalized == "buy":
            if not opportunity.get("rights", {}).get("resale_allowed"):
                opportunity["status"] = "needs_rights_proof"
            else:
                opportunity["status"] = "approved_to_buy"
        elif normalized == "reject":
            opportunity["status"] = "rejected_by_user"
        else:
            opportunity["status"] = "watching"
        opportunity["updated_at"] = now_iso()
        self.storage.save_opportunity(opportunity)
        self.storage.append_event("opportunity.decided", {"id": opportunity_id, "decision": normalized})
        self.notifier.send_text(f"Opportunity {opportunity_id} decision recorded: {normalized}")
        return opportunity

    def record_purchase(self, opportunity_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        opportunity = self.storage.load_opportunity(opportunity_id)
        opportunity["purchase"] = {
            "purchased": bool(payload.get("purchased", True)),
            "receipt": payload.get("receipt") or payload.get("receipt_url") or "",
            "license_evidence": payload.get("license_evidence") or "",
            "source_url": payload.get("source_url") or opportunity.get("product", {}).get("source_url", ""),
            "created_at": now_iso(),
        }
        if payload.get("license_evidence"):
            opportunity["rights"]["evidence"] = payload["license_evidence"]
            if opportunity["rights"].get("rights_type") in ALLOWED_RIGHTS:
                opportunity["rights"]["resale_allowed"] = True
        opportunity["status"] = "purchased" if opportunity["rights"].get("resale_allowed") else "needs_rights_proof"
        opportunity["updated_at"] = now_iso()
        self.storage.save_opportunity(opportunity)
        self.storage.append_event("opportunity.purchase_recorded", {"id": opportunity_id, "status": opportunity["status"]})
        return opportunity

    def save_assets(self, opportunity_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        opportunity = self.storage.load_opportunity(opportunity_id)
        if not bool(payload.get("asset_rights_confirmed")):
            raise ValueError("asset_rights_confirmed=true is required before saving promotional assets")
        assets = normalize_assets(payload.get("assets") or [])
        product_dir = self.storage.product_dir(opportunity_id)
        saved_assets: List[Dict[str, Any]] = []
        for asset in assets:
            saved = save_asset(product_dir / "assets", asset)
            saved_assets.append(saved)
        opportunity["assets"].extend(saved_assets)
        opportunity["status"] = "assets_saved"
        opportunity["updated_at"] = now_iso()
        self.storage.save_opportunity(opportunity)
        self.storage.append_event("opportunity.assets_saved", {"id": opportunity_id, "count": len(saved_assets)})
        return opportunity

    def listing_brief(self, opportunity_id: str) -> Dict[str, Any]:
        opportunity = self.storage.load_opportunity(opportunity_id)
        can_list, reason = can_prepare_listing(opportunity)
        if not can_list:
            raise ValueError(reason)
        product = opportunity["product"]
        economics = opportunity["economics"]
        judgement = opportunity["judgement"]
        assets = [asset["local_path"] for asset in opportunity.get("assets", []) if asset.get("usable_for_publish")]
        text = (
            f"Authorized virtual product listing.\n"
            f"Title: {product['title']}\n"
            f"Category: virtual knowledge product\n"
            f"Planned price: {economics.get('planned_price')}\n"
            f"Rights: {opportunity['rights'].get('rights_type')} with evidence recorded.\n"
            f"Original selling angle to rewrite, not copy: {judgement.get('listing_angle', '')}\n"
            f"Notes: create original copy, do not copy seller text, do not promise official authorization beyond evidence."
        )
        return {"text": text, "images": assets, "opportunity": opportunity}

    def search_market(self, title: str) -> List[Dict[str, Any]]:
        provider = self.settings.market_search_provider
        if provider == "serpapi" and self.settings.serpapi_key:
            return self._serpapi_search(title)
        if provider == "bing" and self.settings.bing_search_key:
            return self._bing_search(title)
        return []

    def _serpapi_search(self, title: str) -> List[Dict[str, Any]]:
        response = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google", "q": f"{title} 闲鱼 课程 网课", "api_key": self.settings.serpapi_key},
            timeout=30,
        )
        response.raise_for_status()
        results = response.json().get("organic_results", [])[: self.settings.market_search_limit]
        return [sample_from_search(item.get("title", ""), item.get("snippet", ""), item.get("link", "")) for item in results]

    def _bing_search(self, title: str) -> List[Dict[str, Any]]:
        response = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": f"{title} 闲鱼 课程 网课", "count": self.settings.market_search_limit},
            headers={"Ocp-Apim-Subscription-Key": self.settings.bing_search_key},
            timeout=30,
        )
        response.raise_for_status()
        results = response.json().get("webPages", {}).get("value", [])
        return [sample_from_search(item.get("name", ""), item.get("snippet", ""), item.get("url", "")) for item in results]

    def deepseek_judgement(
        self,
        product: Dict[str, Any],
        rights: Dict[str, Any],
        economics: Dict[str, Any],
        market_samples: List[Dict[str, Any]],
        fallback: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = {
            "product": product,
            "rights": rights,
            "economics": economics,
            "market_samples": market_samples,
            "policy": "Reject opportunities without resale rights or promotional asset rights.",
            "fallback": fallback,
        }
        try:
            result = self.llm.chat_json(SYSTEM_PROMPT, json.dumps(prompt, ensure_ascii=False), temperature=0.2)
            if not result:
                return fallback
            result.setdefault("source", "deepseek")
            result.setdefault("verdict", fallback["verdict"])
            result.setdefault("resale_allowed", rights["resale_allowed"])
            return result
        except Exception as exc:
            fallback["source"] = "heuristic_after_deepseek_error"
            fallback["deepseek_error"] = str(exc)
            return fallback


def normalize_product(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_text = str(payload.get("text") or payload.get("raw_text") or "")
    title = str(payload.get("title") or extract_title(raw_text) or "virtual product opportunity").strip()
    return {
        "title": title,
        "source_url": str(payload.get("source_url") or ""),
        "seller_copy_reference": str(payload.get("seller_copy") or raw_text or ""),
        "category": str(payload.get("category") or "knowledge"),
        "format": str(payload.get("format") or "digital"),
        "target_buyer": str(payload.get("target_buyer") or ""),
    }


def evaluate_rights(payload: Dict[str, Any]) -> Dict[str, Any]:
    rights_type = str(payload.get("rights_type") or "unknown").lower()
    evidence = str(payload.get("rights_evidence") or payload.get("license_evidence") or "")
    resale_allowed = rights_type in ALLOWED_RIGHTS and bool(evidence or rights_type in {"public_domain", "open_license"})
    copy_assets_allowed = bool(payload.get("asset_rights_confirmed")) and resale_allowed
    risk = "low" if resale_allowed else "high"
    if rights_type in {"affiliate", "open_license"}:
        risk = "medium"
    notes = []
    if not resale_allowed:
        notes.append("No clear resale authorization. Do not buy for resale or list it yet.")
    if not copy_assets_allowed:
        notes.append("Seller copy and promotional images cannot be reused unless asset rights are confirmed.")
    return {
        "rights_type": rights_type,
        "evidence": evidence,
        "resale_allowed": resale_allowed,
        "copy_assets_allowed": copy_assets_allowed,
        "risk": risk,
        "notes": notes,
    }


def compute_economics(
    acquisition_cost: float,
    planned_price: float,
    platform_fee_rate: float,
    setup_cost: float,
    unit_delivery_cost: float,
    market_samples: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if planned_price <= 0:
        planned_price = suggested_price_from_market(market_samples)
    net_per_sale = planned_price * (1 - platform_fee_rate) - unit_delivery_cost
    total_upfront = acquisition_cost + setup_cost
    break_even_units = math.inf if net_per_sale <= 0 else math.ceil(total_upfront / net_per_sale)
    profit_after_10 = net_per_sale * 10 - total_upfront
    profit_rate = 0.0 if planned_price <= 0 else net_per_sale / planned_price
    return {
        "acquisition_cost": round(acquisition_cost, 2),
        "planned_price": round(planned_price, 2),
        "platform_fee_rate": platform_fee_rate,
        "setup_cost": round(setup_cost, 2),
        "unit_delivery_cost": round(unit_delivery_cost, 2),
        "net_per_sale": round(net_per_sale, 2),
        "break_even_units": break_even_units if break_even_units != math.inf else None,
        "profit_after_10_sales": round(profit_after_10, 2),
        "profit_rate": round(profit_rate, 4),
    }


def heuristic_judgement(
    product: Dict[str, Any],
    rights: Dict[str, Any],
    economics: Dict[str, Any],
    market_samples: List[Dict[str, Any]],
    settings: Settings,
) -> Dict[str, Any]:
    red_flags = list(rights.get("notes", []))
    if not rights["resale_allowed"]:
        verdict = "needs_rights_proof"
    elif economics["net_per_sale"] < settings.min_profit_amount:
        verdict = "watch"
        red_flags.append("Net profit per sale is below target.")
    elif economics["profit_rate"] < settings.min_profit_rate:
        verdict = "watch"
        red_flags.append("Profit rate is below target.")
    elif not market_samples:
        verdict = "watch"
        red_flags.append("No market samples were found; demand is uncertain.")
    else:
        verdict = "buy"

    market_size = "unknown"
    if len(market_samples) >= 5:
        market_size = "medium"
    if len(market_samples) >= 10:
        market_size = "large"
    profit_potential = "low"
    if economics["net_per_sale"] >= settings.min_profit_amount and economics["profit_rate"] >= settings.min_profit_rate:
        profit_potential = "medium"
    if economics["profit_after_10_sales"] >= settings.min_profit_amount * 10:
        profit_potential = "high"

    return {
        "source": "heuristic",
        "verdict": verdict,
        "resale_allowed": rights["resale_allowed"],
        "rights_risk": rights["risk"],
        "market_size": market_size,
        "profit_potential": profit_potential,
        "confidence": 0.55 if market_samples else 0.35,
        "reasons": [
            f"Net per sale: {economics['net_per_sale']}",
            f"Break-even units: {economics['break_even_units']}",
            f"Market sample count: {len(market_samples)}",
        ],
        "questions_for_user": [
            "Do you have resale rights evidence?",
            "Are promotional images and seller copy authorized for reuse?",
            "Can the product be delivered automatically after purchase?",
        ],
        "listing_angle": f"Original listing for {product['title']} focused on outcome, contents, and delivery details.",
        "red_flags": red_flags,
        "next_steps": [
            "Confirm resale and asset rights before purchase.",
            "Ask user to approve or reject in Feishu.",
            "After approval, record purchase receipt and save authorized files.",
        ],
    }


def can_prepare_listing(opportunity: Dict[str, Any]) -> tuple:
    if opportunity.get("status") not in {"purchased", "assets_saved"}:
        return False, "Opportunity must be approved and purchased before listing."
    if not opportunity.get("rights", {}).get("resale_allowed"):
        return False, "Resale rights are not confirmed."
    if opportunity.get("assets") and not all(asset.get("usable_for_publish") for asset in opportunity["assets"]):
        return False, "Some assets are not confirmed usable for publishing."
    return True, ""


def initial_status(judgement: Dict[str, Any], rights: Dict[str, Any]) -> str:
    if not rights.get("resale_allowed"):
        return "needs_rights_proof"
    if judgement.get("verdict") == "reject":
        return "rejected_by_agent"
    return "pending_user_decision"


def format_feishu_question(opportunity: Dict[str, Any]) -> str:
    economics = opportunity["economics"]
    judgement = opportunity["judgement"]
    return (
        f"Virtual product opportunity: {opportunity['product']['title']}\n"
        f"ID: {opportunity['id']}\n"
        f"Verdict: {judgement.get('verdict')} | Rights risk: {judgement.get('rights_risk')}\n"
        f"Cost: {economics.get('acquisition_cost')} | Planned price: {economics.get('planned_price')}\n"
        f"Net/sale: {economics.get('net_per_sale')} | Break-even units: {economics.get('break_even_units')}\n"
        f"Reply: buy {opportunity['id']} or reject {opportunity['id']} <reason>."
    )


def parse_decision_text(text: str) -> Optional[Dict[str, str]]:
    stripped = text.strip()
    match = re.match(r"^(buy|购入|购买|approve|同意)\s+([a-zA-Z0-9_-]+)(?:\s+(.*))?$", stripped, re.I)
    if match:
        return {"decision": "buy", "id": match.group(2), "note": match.group(3) or ""}
    match = re.match(r"^(reject|拒绝|不买|放弃)\s+([a-zA-Z0-9_-]+)(?:\s+(.*))?$", stripped, re.I)
    if match:
        return {"decision": "reject", "id": match.group(2), "note": match.group(3) or ""}
    return None


def looks_like_opportunity_request(text: str) -> bool:
    lowered = text.lower().strip()
    return lowered.startswith(("比价:", "比价：", "评估:", "评估：", "opportunity:", "arbitrage:"))


def payload_from_text(text: str) -> Dict[str, Any]:
    clean = re.sub(r"^(比价[:：]|评估[:：]|opportunity:|arbitrage:)", "", text.strip(), flags=re.I).strip()
    return {"text": clean, "title": extract_title(clean)}


def normalize_decision(decision: str) -> str:
    lowered = decision.strip().lower()
    if lowered in {"buy", "approve", "yes", "购入", "购买", "同意"}:
        return "buy"
    if lowered in {"reject", "no", "拒绝", "不买", "放弃"}:
        return "reject"
    return "watch"


def normalize_market_samples(samples: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for item in samples:
        if isinstance(item, dict):
            text = f"{item.get('title', '')} {item.get('snippet', '')}"
            normalized.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "price": to_float(item.get("price"), extract_price(text)),
                    "snippet": str(item.get("snippet") or ""),
                }
            )
        else:
            text = str(item)
            normalized.append({"title": text, "url": "", "price": extract_price(text), "snippet": ""})
    return normalized


def normalize_assets(assets: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for item in assets:
        if isinstance(item, dict):
            normalized.append(
                {
                    "url": str(item.get("url") or ""),
                    "path": str(item.get("path") or ""),
                    "name": str(item.get("name") or ""),
                    "usable_for_publish": bool(item.get("usable_for_publish", True)),
                }
            )
        else:
            value = str(item)
            normalized.append({"url": value if value.startswith("http") else "", "path": value, "name": "", "usable_for_publish": True})
    return normalized


def save_asset(asset_dir: Path, asset: Dict[str, Any]) -> Dict[str, Any]:
    source = asset.get("url") or asset.get("path")
    if not source:
        raise ValueError("asset url or path is required")
    name = safe_filename(asset.get("name") or Path(source).name or f"asset-{uuid4().hex[:8]}")
    target = asset_dir / name
    if asset.get("url"):
        session = requests.Session()
        session.trust_env = False
        response = session.get(asset["url"], timeout=60)
        response.raise_for_status()
        target.write_bytes(response.content)
    else:
        shutil.copy2(asset["path"], target)
    saved = dict(asset)
    saved["local_path"] = str(target)
    saved["saved_at"] = now_iso()
    return saved


def sample_from_search(title: str, snippet: str, url: str) -> Dict[str, Any]:
    text = f"{title} {snippet}"
    return {"title": title, "snippet": snippet, "url": url, "price": extract_price(text)}


def suggested_price_from_market(samples: List[Dict[str, Any]]) -> float:
    prices = [float(item["price"]) for item in samples if item.get("price")]
    if not prices:
        return 0.0
    prices.sort()
    median = prices[len(prices) // 2]
    return round(median * 0.9, 2)


def extract_title(text: str) -> str:
    first = re.split(r"[,，\n]", text.strip())[0]
    return first[:60]


def extract_price(text: str) -> float:
    match = re.search(r"(?:¥|￥|价格|售价|price)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:元|rmb|RMB)?", text)
    return float(match.group(1)) if match else 0.0


def to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return cleaned or f"asset-{uuid4().hex[:8]}"
