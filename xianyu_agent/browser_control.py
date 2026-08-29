from __future__ import annotations

import json
import subprocess
import time
from itertools import count
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import websocket

from .config import Settings
from .models import now_iso
from .platform_guard import PlatformGuard
from .storage import JsonStorage


class EdgeBrowserController:
    def __init__(self, settings: Settings, storage: JsonStorage, guard: PlatformGuard) -> None:
        self.settings = settings
        self.storage = storage
        self.guard = guard
        self._ids = count(1)
        self._http = requests.Session()
        self._http.trust_env = False

    def status(self) -> Dict[str, Any]:
        try:
            tabs = self._tabs()
            return {
                "ok": True,
                "debug_url": self._debug_url(),
            "tabs": [
                {"id": tab.get("id"), "url": tab.get("url"), "title": tab.get("title")}
                for tab in tabs
                if tab.get("type") == "page"
            ],
            }
        except Exception as exc:
            return {"ok": False, "debug_url": self._debug_url(), "error": str(exc)}

    def open_url(self, url: str, wait_seconds: float = 3.0) -> Dict[str, Any]:
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        guard = self.guard.before_action("browser_open_url", {"url": url})
        if not guard.get("allow"):
            return {"ok": False, "guard": guard}

        self.ensure_running()
        tab = self._new_tab(url)
        ws_url = tab.get("webSocketDebuggerUrl") or self._tab_ws_url(tab.get("id"))
        time.sleep(wait_seconds)
        text = self._evaluate(ws_url, "document.body ? document.body.innerText : ''")
        title = self._evaluate(ws_url, "document.title || ''")
        verification = self.guard.observe_text(str(text), {"action": "browser_open_url", "url": url})
        result = {
            "ok": True,
            "url": url,
            "title": title,
            "text_excerpt": str(text)[:3000],
            "verification": verification,
            "tab": {"id": tab.get("id"), "webSocketDebuggerUrl": ws_url},
            "created_at": now_iso(),
        }
        self.storage.write_research("browser-open", result)
        return result

    def open_account_login(self) -> Dict[str, Any]:
        """Open the dedicated login browser without reading or persisting page text."""
        url = "https://www.goofish.com/"
        launch = self.ensure_running()
        tab = self._new_tab(url)
        return {
            "ok": True,
            "url": url,
            "debug_url": self._debug_url(),
            "browser_started": bool(launch.get("started")),
            "tab_id": str(tab.get("id") or ""),
        }

    def ensure_running(self) -> Dict[str, Any]:
        status = self.status()
        if status.get("ok"):
            return {"started": False, "status": status}

        edge_path = Path(self.settings.browser_edge_path)
        if not edge_path.exists():
            raise FileNotFoundError(f"Edge executable not found: {edge_path}")

        profile_dir = Path(self.settings.browser_user_data_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            str(edge_path),
            f"--remote-debugging-port={self.settings.browser_debug_port}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-allow-origins=http://127.0.0.1:{self.settings.browser_debug_port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        if self.settings.browser_headless:
            args.append("--headless=new")
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._wait_until_ready()
        return {"started": True, "status": self.status()}

    def _wait_until_ready(self) -> None:
        last_error: Optional[Exception] = None
        for _ in range(30):
            try:
                self._tabs()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"browser did not become ready: {last_error}")

    def _new_tab(self, url: str) -> Dict[str, Any]:
        response = self._http.put(f"{self._debug_url()}/json/new?{url}", timeout=10)
        if response.status_code == 405:
            response = self._http.get(f"{self._debug_url()}/json/new?{url}", timeout=10)
        response.raise_for_status()
        return response.json()

    def _tabs(self) -> Any:
        response = self._http.get(f"{self._debug_url()}/json", timeout=3)
        response.raise_for_status()
        return response.json()

    def _tab_ws_url(self, tab_id: str) -> str:
        for tab in self._tabs():
            if tab.get("id") == tab_id:
                return tab["webSocketDebuggerUrl"]
        raise ValueError(f"tab not found: {tab_id}")

    def _evaluate(self, ws_url: str, expression: str) -> Any:
        ws = websocket.create_connection(ws_url, timeout=10)
        try:
            message_id = next(self._ids)
            ws.send(
                json.dumps(
                    {
                        "id": message_id,
                        "method": "Runtime.evaluate",
                        "params": {"expression": expression, "returnByValue": True},
                    }
                )
            )
            while True:
                message = json.loads(ws.recv())
                if message.get("id") == message_id:
                    if "exceptionDetails" in message:
                        raise RuntimeError(str(message["exceptionDetails"]))
                    return message.get("result", {}).get("result", {}).get("value")
        finally:
            ws.close()

    def _debug_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.browser_debug_port}"
