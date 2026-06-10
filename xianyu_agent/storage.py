from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .models import ListingDraft, PublishResult, now_iso


class JsonStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.drafts_dir = data_dir / "drafts"
        self.outbox_dir = data_dir / "outbox"
        self.research_dir = data_dir / "research"
        self.opportunities_dir = data_dir / "opportunities"
        self.products_dir = data_dir / "products"
        self.crawls_dir = data_dir / "crawls"
        self.guard_dir = data_dir / "guard"
        self.memory_dir = data_dir / "memory"
        self.guard_state_path = self.guard_dir / "state.json"
        self.memory_profile_path = self.memory_dir / "profile.json"
        self.memory_events_path = self.memory_dir / "events.jsonl"
        self.published_path = data_dir / "published.json"
        self.events_path = data_dir / "events.jsonl"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.research_dir.mkdir(parents=True, exist_ok=True)
        self.opportunities_dir.mkdir(parents=True, exist_ok=True)
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.crawls_dir.mkdir(parents=True, exist_ok=True)
        self.guard_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def save_draft(self, draft: ListingDraft) -> Path:
        path = self.drafts_dir / f"{draft.id}.json"
        path.write_text(json.dumps(draft.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_publish_result(self, result: PublishResult, draft: ListingDraft) -> None:
        records = self.list_published()
        records.append(
            {
                "listing_id": draft.id,
                "external_id": result.external_id,
                "title": draft.title,
                "adapter": result.adapter,
                "published_at": result.created_at,
                "delivery_type": draft.delivery_type,
                "delivery_payload": draft.delivery_payload,
            }
        )
        self.published_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_published(self) -> List[Dict[str, Any]]:
        if not self.published_path.exists():
            return []
        return json.loads(self.published_path.read_text(encoding="utf-8"))

    def append_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        record = {"type": event_type, "created_at": now_iso(), "payload": payload}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_outbox(self, name: str, payload: Dict[str, Any]) -> Path:
        path = self.outbox_dir / f"{now_iso().replace(':', '-')}-{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_research(self, name: str, payload: Dict[str, Any]) -> Path:
        path = self.research_dir / f"{now_iso().replace(':', '-')}-{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_opportunity(self, opportunity: Dict[str, Any]) -> Path:
        opportunity_id = str(opportunity["id"])
        path = self.opportunities_dir / f"{opportunity_id}.json"
        path.write_text(json.dumps(opportunity, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_opportunity(self, opportunity_id: str) -> Dict[str, Any]:
        path = self.opportunities_dir / f"{opportunity_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Opportunity not found: {opportunity_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def product_dir(self, opportunity_id: str) -> Path:
        path = self.products_dir / opportunity_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "assets").mkdir(parents=True, exist_ok=True)
        (path / "source").mkdir(parents=True, exist_ok=True)
        return path

    def crawl_dir(self, crawl_id: str) -> Path:
        path = self.crawls_dir / crawl_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "downloads").mkdir(parents=True, exist_ok=True)
        return path

    def save_crawl(self, crawl: Dict[str, Any]) -> Path:
        crawl_id = str(crawl["id"])
        path = self.crawl_dir(crawl_id) / "crawl.json"
        path.write_text(json.dumps(crawl, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_guard_state(self) -> Dict[str, Any]:
        if not self.guard_state_path.exists():
            return {}
        return json.loads(self.guard_state_path.read_text(encoding="utf-8"))

    def save_guard_state(self, state: Dict[str, Any]) -> Path:
        self.guard_state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.guard_state_path
