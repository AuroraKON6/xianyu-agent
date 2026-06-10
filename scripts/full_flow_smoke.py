from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict

import requests


BASE_URL = "http://127.0.0.1:8788"
DUMMY_PDF = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"


def request(method: str, path: str, payload: Dict[str, Any] = None, timeout: int = 90) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    response = requests.request(method, url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def retry_list_opportunity(opportunity_id: str) -> Dict[str, Any]:
    result = request("POST", f"/opportunities/{opportunity_id}/list", {"publish": True})
    guard = (((result.get("publish_result") or {}).get("raw") or {}).get("guard") or {})
    if guard.get("action") == "delay":
        time.sleep(int(guard.get("wait_seconds", 1)) + 1)
        result = request("POST", f"/opportunities/{opportunity_id}/list", {"publish": True})
    assert result.get("publish_result"), "listing publish_result missing"
    assert result["publish_result"].get("ok"), result["publish_result"]
    return result


def retry_polish() -> Dict[str, Any]:
    result = request("POST", "/tasks/polish", {})
    blocked = next((item for item in result.get("results", []) if item.get("guard", {}).get("action") == "delay"), None)
    if blocked:
        time.sleep(int(blocked["guard"].get("wait_seconds", 1)) + 1)
        result = request("POST", "/tasks/polish", {})
    return result


def main() -> int:
    summary: Dict[str, Any] = {}

    health = request("GET", "/health")
    assert health.get("ok"), health
    summary["health"] = health

    request("POST", "/guard/resume", {"note": "full flow smoke start"})
    guard = request("GET", "/guard/status")
    assert not guard.get("paused"), guard
    summary["guard_start"] = guard

    crawl = request(
        "POST",
        "/crawler/url",
        {
            "url": DUMMY_PDF,
            "acquisition_cost": 0,
            "planned_price": 9.9,
            "rights_type": "public_domain",
            "rights_evidence": "public W3C test PDF smoke fixture",
            "download": True,
            "market_samples": ["test PDF template 9.9", "digital PDF resource 19.9"],
            "use_deepseek": False,
            "notify": False,
        },
    )
    opportunity = crawl["opportunity"]
    opportunity_id = opportunity["id"]
    assert crawl["crawl"]["downloads"], "authorized crawler download did not save a file"
    assert opportunity["status"] == "pending_user_decision", opportunity["status"]
    summary["crawl_id"] = crawl["crawl"]["id"]
    summary["opportunity_id"] = opportunity_id

    decision = request("POST", f"/opportunities/{opportunity_id}/decision", {"decision": "buy", "note": "smoke approval"})
    assert decision["status"] == "approved_to_buy", decision["status"]

    purchase = request(
        "POST",
        f"/opportunities/{opportunity_id}/purchase",
        {
            "receipt": "smoke receipt",
            "license_evidence": "public W3C test PDF smoke fixture",
            "source_url": DUMMY_PDF,
        },
    )
    assert purchase["status"] == "purchased", purchase["status"]

    assets = request(
        "POST",
        f"/opportunities/{opportunity_id}/assets",
        {
            "asset_rights_confirmed": True,
            "assets": [{"url": DUMMY_PDF, "name": "smoke-authorized.pdf", "usable_for_publish": True}],
        },
    )
    assert assets["status"] == "assets_saved", assets["status"]

    listing = retry_list_opportunity(opportunity_id)
    listing_id = listing["draft"]["id"]
    external_id = listing["publish_result"]["external_id"]
    summary["listing_id"] = listing_id
    summary["external_id"] = external_id

    polish = retry_polish()
    summary["polish_count"] = polish.get("count")

    fulfill = request(
        "POST",
        "/orders/paid",
        {
            "order_id": f"smoke-{opportunity_id}",
            "listing_id": listing_id,
            "delivery_type": "digital",
            "delivery_payload": "smoke-delivery-payload",
        },
    )
    guard = fulfill.get("guard") or {}
    if guard.get("action") == "delay":
        time.sleep(int(guard.get("wait_seconds", 1)) + 1)
        fulfill = request(
            "POST",
            "/orders/paid",
            {
                "order_id": f"smoke-{opportunity_id}-retry",
                "listing_id": listing_id,
                "delivery_type": "digital",
                "delivery_payload": "smoke-delivery-payload",
            },
        )
    assert fulfill.get("ok"), fulfill

    github = request(
        "POST",
        "/research/github",
        {"queries": ["ai agent"], "min_stars": 100000, "limit": 1, "use_deepseek": False},
    )
    assert github.get("count", 0) >= 1 or github.get("errors"), github
    summary["github_count"] = github.get("count")
    summary["github_errors"] = github.get("errors")

    feishu_verify = request("POST", "/feishu/events", {"type": "url_verification", "challenge": "smoke-challenge"})
    assert feishu_verify.get("challenge") == "smoke-challenge", feishu_verify

    feishu_message = request(
        "POST",
        "/feishu/events",
        {"event": {"message": {"content": json.dumps({"text": f"crawl: {DUMMY_PDF}"})}}},
    )
    assert feishu_message.get("ok"), feishu_message
    summary["feishu_message_type"] = feishu_message["result"]["type"]

    paused = request("POST", "/guard/check", {"text": "please complete captcha verification", "context": {"source": "smoke"}})
    assert paused.get("action") == "pause", paused
    resumed = request("POST", "/guard/resume", {"note": "full flow smoke complete"})
    assert not resumed.get("paused"), resumed

    summary["status"] = "passed"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise
