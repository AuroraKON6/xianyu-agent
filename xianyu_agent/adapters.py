from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from .config import Settings
from .models import ListingDraft, Order, PublishResult
from .storage import JsonStorage


class ListingAdapter(ABC):
    name = "base"

    @abstractmethod
    def publish(self, draft: ListingDraft) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    def polish(self, external_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def fulfill(self, order: Order) -> Dict[str, Any]:
        raise NotImplementedError

    def sync_orders(self, cookie_id: str = "default", status: str = "") -> Dict[str, Any]:
        """Provider-neutral read-only order sync preview."""
        return {
            "ok": False,
            "adapter": self.name,
            "cookie_id": cookie_id,
            "status": status,
            "orders": [],
            "error": "sync_orders not implemented",
        }

    def sync_my_listings(self, cookie_id: str = "default") -> Dict[str, Any]:
        """Provider-neutral read-only listing sync preview."""
        return {
            "ok": False,
            "adapter": self.name,
            "cookie_id": cookie_id,
            "items": [],
            "error": "sync_my_listings not implemented",
        }


class StubAdapter(ListingAdapter):
    name = "stub"

    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage

    def publish(self, draft: ListingDraft) -> PublishResult:
        external_id = f"stub_{draft.id[:10]}"
        path = self.storage.write_outbox("publish", {"draft": draft.to_dict(), "external_id": external_id})
        return PublishResult(
            ok=True,
            adapter=self.name,
            listing_id=draft.id,
            external_id=external_id,
            message=f"Saved publish payload to {path}",
        )

    def polish(self, external_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        path = self.storage.write_outbox("polish", {"external_id": external_id, "record": record})
        return {"ok": True, "external_id": external_id, "message": f"Saved polish payload to {path}"}

    def fulfill(self, order: Order) -> Dict[str, Any]:
        path = self.storage.write_outbox("fulfill", {"order": order.raw, "delivery_payload": order.delivery_payload})
        return {"ok": True, "order_id": order.order_id, "message": f"Saved fulfillment payload to {path}"}

    def refund_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self.storage.write_outbox("refund", {"refund": payload})
        return {
            "ok": True,
            "order_id": payload.get("order_id", ""),
            "message": f"Saved refund payload to {path}",
        }


class XianGuanJiaAdapter(ListingAdapter):
    name = "xianguanjia"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.xianguanjia_base_url:
            raise ValueError("XIANGUANJIA_BASE_URL is required for xianguanjia adapter")

    def publish(self, draft: ListingDraft) -> PublishResult:
        payload = self._build_listing_payload(draft)
        data = self._post(self.settings.xianguanjia_create_endpoint, payload)
        external_id = self._extract_publish_external_id(data)
        ok = bool(data.get("ok", True)) and not data.get("error")
        return PublishResult(
            ok=ok,
            adapter=self.name,
            listing_id=draft.id,
            external_id=external_id,
            message=str(data.get("message") or data.get("msg") or "Published through xianguanjia adapter"),
            raw=data,
        )

    def polish(self, external_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = self.settings.xianguanjia_polish_endpoint.format(listing_id=external_id)
        return self._post(endpoint, {"listing_id": external_id, "record": record})

    def fulfill(self, order: Order) -> Dict[str, Any]:
        endpoint = self.settings.xianguanjia_fulfill_endpoint.format(order_id=order.order_id)
        return self._post(endpoint, order.raw)

    def sync_orders(self, cookie_id: str = "default", status: str = "") -> Dict[str, Any]:
        params = {"cookie_id": cookie_id}
        if status:
            params["status"] = status
        data = self._get("/api/orders", params=params)
        orders = data.get("orders") or data.get("items") or data.get("data") or []
        return {
            "ok": True,
            "adapter": self.name,
            "cookie_id": cookie_id,
            "status": status,
            "orders": orders if isinstance(orders, list) else [],
            "raw": data,
        }

    def sync_my_listings(self, cookie_id: str = "default") -> Dict[str, Any]:
        data = self._get("/api/listings", params={"cookie_id": cookie_id})
        items = data.get("items") or data.get("listings") or data.get("data") or []
        return {
            "ok": True,
            "adapter": self.name,
            "cookie_id": cookie_id,
            "items": items if isinstance(items, list) else [],
            "raw": data,
        }

    def preflight(self) -> Dict[str, Any]:
        return xianguanjia_health(self.settings)

    def _build_listing_payload(self, draft: ListingDraft) -> Dict[str, Any]:
        """Map internal ListingDraft to a provider-facing listing payload."""
        return {
            "title": draft.title,
            "category": draft.category,
            "price": draft.price,
            "stock": draft.stock,
            "condition": draft.condition,
            "location": draft.location,
            "description": draft.description,
            "tags": list(draft.tags or []),
            "images": list(draft.selected_images or []),
            "delivery": {
                "type": draft.delivery_type,
                "payload": draft.delivery_payload,
            },
            "delivery_type": draft.delivery_type,
            "delivery_payload": draft.delivery_payload,
            "source": draft.source,
            "raw_text": draft.raw_text,
        }

    def _extract_publish_external_id(self, data: Dict[str, Any]) -> str:
        """Extract a provider item id from common flat or nested response shapes."""
        if not isinstance(data, dict):
            return ""
        for key in ("id", "listing_id", "item_id", "itemId", "goods_id", "goodsId"):
            value = data.get(key)
            if value:
                return str(value)
        for key in ("data", "result", "item", "listing", "goods"):
            nested = data.get(key)
            if isinstance(nested, dict):
                found = self._extract_publish_external_id(nested)
                if found:
                    return found
        return ""

    def _build_url(self, endpoint: str) -> str:
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        if endpoint.startswith("/"):
            return f"{self.settings.xianguanjia_base_url}{endpoint}"
        return f"{self.settings.xianguanjia_base_url}/{endpoint}"

    def _build_body(self, payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _build_headers(self, body: str, timestamp: str | None = None) -> Dict[str, str]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.settings.xianguanjia_token:
            headers["Authorization"] = f"Bearer {self.settings.xianguanjia_token}"
        if self.settings.xianguanjia_app_id and self.settings.xianguanjia_api_key:
            signed_at = timestamp or str(int(time.time() * 1000))
            sign_source = f"{signed_at}{body}{self.settings.xianguanjia_api_key}"
            headers.update(
                {
                    "Sign": hashlib.sha1(sign_source.encode("utf-8")).hexdigest(),
                    "Timestamp": signed_at,
                    "UserId": self.settings.xianguanjia_app_id,
                }
            )
        return headers

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self._build_url(endpoint)
        body = self._build_body(payload)
        headers = self._build_headers(body)
        response = requests.post(url, headers=headers, data=body.encode("utf-8"), timeout=60)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self._build_url(endpoint)
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(params)}"
        body = ""
        headers = self._build_headers(body)
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}


def xianguanjia_health(settings: Settings) -> Dict[str, Any]:
    auth_modes = []
    if settings.xianguanjia_token:
        auth_modes.append("bearer_token")
    if settings.xianguanjia_app_id and settings.xianguanjia_api_key:
        auth_modes.append("legacy_sha1_headers")

    missing = []
    if not settings.xianguanjia_base_url:
        missing.append("XIANGUANJIA_BASE_URL")
    if not auth_modes:
        missing.append("XIANGUANJIA_TOKEN or XIANGUANJIA_APP_ID+XIANGUANJIA_API_KEY")

    return {
        "adapter": "xianguanjia",
        "configured": bool(settings.xianguanjia_base_url),
        "ready": bool(settings.xianguanjia_base_url and auth_modes),
        "base_url": settings.xianguanjia_base_url,
        "auth_modes": auth_modes,
        "missing": missing,
        "endpoints": {
            "create": settings.xianguanjia_create_endpoint,
            "polish": settings.xianguanjia_polish_endpoint,
            "fulfill": settings.xianguanjia_fulfill_endpoint,
        },
        "notes": [
            "Set XIANYU_ADAPTER=xianguanjia to use this execution layer.",
            "Secrets are intentionally omitted from this health payload.",
        ],
    }


def build_adapter(settings: Settings, storage: JsonStorage, browser_pool=None) -> ListingAdapter:
    if settings.xianyu_adapter == "xianguanjia":
        return XianGuanJiaAdapter(settings)
    if settings.xianyu_adapter == "browser":
        from .browser_adapter import BrowserAdapter
        return BrowserAdapter(settings, storage, browser_pool)
    return StubAdapter(storage)
