from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class ImageCandidate:
    url: str
    thumbnail: str = ""
    source: str = ""
    title: str = ""
    license_hint: str = "unknown"
    usable_for_publish: bool = False


@dataclass
class ListingDraft:
    id: str = field(default_factory=lambda: uuid4().hex)
    title: str = ""
    category: str = ""
    price: Optional[float] = None
    stock: Optional[int] = None
    condition: str = "new"
    location: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    image_queries: List[str] = field(default_factory=list)
    image_candidates: List[ImageCandidate] = field(default_factory=list)
    selected_images: List[str] = field(default_factory=list)
    delivery_type: str = "manual"
    delivery_payload: str = ""
    faqs: List[Dict[str, str]] = field(default_factory=list)
    source: str = "manual"
    raw_text: str = ""
    compliance_notes: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["image_candidates"] = [asdict(item) for item in self.image_candidates]
        return data


@dataclass
class PublishResult:
    ok: bool
    adapter: str
    listing_id: str
    external_id: str = ""
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Order:
    order_id: str
    listing_id: str
    buyer_id: str = ""
    delivery_type: str = "manual"
    delivery_payload: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Order":
        return cls(
            order_id=str(payload.get("order_id") or payload.get("id") or ""),
            listing_id=str(payload.get("listing_id") or payload.get("item_id") or ""),
            buyer_id=str(payload.get("buyer_id") or payload.get("buyer") or ""),
            delivery_type=str(payload.get("delivery_type") or "manual"),
            delivery_payload=str(payload.get("delivery_payload") or ""),
            raw=payload,
        )
