from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict

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
        data = self._post(self.settings.xianguanjia_create_endpoint, draft.to_dict())
        external_id = str(data.get("id") or data.get("listing_id") or data.get("item_id") or "")
        return PublishResult(
            ok=True,
            adapter=self.name,
            listing_id=draft.id,
            external_id=external_id,
            message=data.get("message", "Published through xianguanjia adapter"),
            raw=data,
        )

    def polish(self, external_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = self.settings.xianguanjia_polish_endpoint.format(listing_id=external_id)
        return self._post(endpoint, {"listing_id": external_id, "record": record})

    def fulfill(self, order: Order) -> Dict[str, Any]:
        endpoint = self.settings.xianguanjia_fulfill_endpoint.format(order_id=order.order_id)
        return self._post(endpoint, order.raw)

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = endpoint if endpoint.startswith("http") else f"{self.settings.xianguanjia_base_url}{endpoint}"
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.settings.xianguanjia_token:
            headers["Authorization"] = f"Bearer {self.settings.xianguanjia_token}"
        if self.settings.xianguanjia_app_id and self.settings.xianguanjia_api_key:
            timestamp = str(int(time.time() * 1000))
            sign_source = f"{timestamp}{body}{self.settings.xianguanjia_api_key}"
            headers.update(
                {
                    "Sign": hashlib.sha1(sign_source.encode("utf-8")).hexdigest(),
                    "Timestamp": timestamp,
                    "UserId": self.settings.xianguanjia_app_id,
                }
            )
        response = requests.post(url, headers=headers, data=body.encode("utf-8"), timeout=60)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}


def build_adapter(settings: Settings, storage: JsonStorage, browser_pool=None) -> ListingAdapter:
    if settings.xianyu_adapter == "xianguanjia":
        return XianGuanJiaAdapter(settings)
    if settings.xianyu_adapter == "browser":
        from .browser_adapter import BrowserAdapter
        return BrowserAdapter(settings, storage, browser_pool)
    return StubAdapter(storage)
