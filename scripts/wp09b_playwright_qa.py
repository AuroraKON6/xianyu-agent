"""WP09b visual QA: console commerce radar at desktop + mobile.

Starts the FastAPI app on an isolated temp DATA_DIR (no live data),
loads /console with headless Chromium at 1440x900 and 390x844,
exercises the supplier-import preview through the real UI, and checks:
commerce panel presence, no horizontal document overflow, no console
errors, and screenshots for both viewports.

Usage:
    .venv/Scripts/python.exe scripts/wp09b_playwright_qa.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "playwright" / "wp09b"
QUOTE_CSV = (
    "货号,品名,单价,单位,起订量,库存,运费,规格,品牌,成色\n"
    "GZ-1200,商用不锈钢工作台,150,件,2,10,15,120x60x85cm,众商,全新\n"
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(tmp: str, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(
        {
            "DATA_DIR": str(Path(tmp) / "data"),
            "DB_PATH": str(Path(tmp) / "data" / "qa.db"),
            "XIANYU_ADAPTER": "stub",
            "XIANYU_PORT": str(port),
        }
    )
    proc = subprocess.Popen(
        [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "uvicorn",
            "xianyu_agent.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            session = requests.Session()
            session.trust_env = False
            if session.get(f"{base}/health", timeout=2).ok:
                return proc
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("server did not become healthy")


def audit_page(page, label: str) -> dict:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    commerce_visible = page.locator(".commerce-panel").count() > 0
    buttons = {
        "preview": page.locator("#commerce-preview-btn").count() > 0,
        "confirm": page.locator("#commerce-confirm-btn").count() > 0,
        "market": page.locator("#market-save-btn").count() > 0,
        "match": page.locator("#match-run-btn").count() > 0,
        "evaluate": page.locator("#opp-evaluate-btn").count() > 0,
    }
    screenshot = OUT / f"console-{label}.png"
    page.screenshot(path=str(screenshot), full_page=False)
    return {
        "viewport": label,
        "horizontal_overflow_px": overflow,
        "commerce_panel_present": commerce_visible,
        "buttons": buttons,
        "screenshot": str(screenshot),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {"viewports": [], "ui_flow": {}, "console_errors": []}
    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        server = start_server(tmp, port)
        base = f"http://127.0.0.1:{port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)

                # desktop 1440x900
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.on("console", lambda m: results["console_errors"].append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: results["console_errors"].append(str(e)))
                page.goto(f"{base}/console", wait_until="networkidle", timeout=30000)
                page.wait_for_selector(".commerce-panel", timeout=15000)
                results["viewports"].append(audit_page(page, "desktop-1440"))

                # exercise the import preview through the real UI (temp data dir)
                page.fill("#commerce-quote-text", QUOTE_CSV)
                page.fill("#commerce-supplier", "QA视觉供应商")
                page.click("#commerce-preview-btn")
                page.wait_for_selector("#commerce-preview-result table", timeout=15000)
                preview_text = page.inner_text("#commerce-preview-result")
                results["ui_flow"]["preview_shows_row"] = "GZ-1200" in preview_text
                results["ui_flow"]["preview_shows_price"] = "150" in preview_text
                page.click("#commerce-confirm-btn")
                page.wait_for_selector("#commerce-preview-result code", timeout=15000)
                confirm_text = page.inner_text("#commerce-preview-result")
                results["ui_flow"]["confirm_shows_batch"] = "impb_" in confirm_text

                # refresh keeps data (repository-backed)
                page.reload(wait_until="networkidle")
                page.wait_for_selector(".commerce-panel", timeout=15000)
                offers = requests.Session()
                offers.trust_env = False
                listing = offers.get(f"{base}/commerce/offers", timeout=5).json()
                results["ui_flow"]["offer_persisted_after_reload"] = listing["total"] >= 1
                page.screenshot(path=str(OUT / "console-desktop-1440-after-flow.png"), full_page=False)
                page.close()

                # mobile 390x844
                mobile = browser.new_page(viewport={"width": 390, "height": 844})
                mobile.on("console", lambda m: results["console_errors"].append(m.text) if m.type == "error" else None)
                mobile.on("pageerror", lambda e: results["console_errors"].append(str(e)))
                mobile.goto(f"{base}/console", wait_until="networkidle", timeout=30000)
                mobile.wait_for_selector(".commerce-panel", timeout=15000)
                results["viewports"].append(audit_page(mobile, "mobile-390"))
                mobile.close()
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)

    results["pass"] = (
        all(v["horizontal_overflow_px"] <= 0 for v in results["viewports"])
        and all(v["commerce_panel_present"] for v in results["viewports"])
        and all(all(v["buttons"].values()) for v in results["viewports"])
        and all(results["ui_flow"].values())
        and not [e for e in results["console_errors"] if "favicon" not in e.lower()]
    )
    (OUT / "wp09b_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: results[k] for k in ("pass", "viewports", "ui_flow")}, ensure_ascii=False, indent=2))
    print("console_errors:", len(results["console_errors"]), results["console_errors"][:5])
    print("artifacts:", OUT)
    return 0 if results["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
