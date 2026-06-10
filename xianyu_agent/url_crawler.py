from __future__ import annotations

import mimetypes
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import requests

from .arbitrage import evaluate_rights, normalize_market_samples, payload_from_text
from .config import Settings
from .models import now_iso
from .storage import JsonStorage


DOWNLOAD_EXTENSIONS = {
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".epub",
    ".mobi",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".csv",
    ".json",
}


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self.images: List[Dict[str, str]] = []
        self.title_parts: List[str] = []
        self.meta: Dict[str, str] = {}
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        attr = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag == "a" and attr.get("href"):
            self.links.append({"href": attr["href"], "text": attr.get("title", "")})
        if tag == "img" and attr.get("src"):
            self.images.append({"src": attr["src"], "alt": attr.get("alt", "")})
        if tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            if name and attr.get("content"):
                self.meta[name] = attr["content"]
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title_parts.append(data.strip())


class UrlDownloadCrawler:
    def __init__(self, settings: Settings, storage: JsonStorage) -> None:
        self.settings = settings
        self.storage = storage

    def crawl(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = str(payload.get("url") or extract_url(str(payload.get("text") or "")) or "").strip()
        if not url:
            raise ValueError("url is required")
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")

        crawl_id = payload.get("id") or uuid4().hex[:12]
        max_pages = min(int(payload.get("max_pages") or self.settings.crawler_max_pages), self.settings.crawler_max_pages)
        download_requested = bool(payload.get("download") or payload.get("download_authorized"))
        rights = evaluate_rights(payload)
        pages = self._crawl_pages(url, max_pages=max_pages)
        candidates = collect_download_candidates(url, pages)
        images = collect_images(url, pages)
        title = payload.get("title") or first_nonempty([page.get("title") for page in pages]) or url
        description = (
            payload.get("description")
            or first_nonempty([page.get("description") for page in pages])
            or first_nonempty([page.get("og_description") for page in pages])
            or ""
        )

        downloads: List[Dict[str, Any]] = []
        if download_requested and rights["resale_allowed"]:
            downloads = self._download_candidates(crawl_id, candidates)
        blocked_reason = ""
        if download_requested and not rights["resale_allowed"]:
            blocked_reason = "Download blocked because resale/download rights are not confirmed."

        market_samples = normalize_market_samples(payload.get("market_samples") or [])
        opportunity_payload = {
            "text": f"{title}\n{description}\nSource URL: {url}",
            "title": title,
            "source_url": url,
            "acquisition_cost": payload.get("acquisition_cost") or payload.get("cost") or 0,
            "planned_price": payload.get("planned_price") or payload.get("resale_price") or 0,
            "rights_type": payload.get("rights_type") or "unknown",
            "rights_evidence": payload.get("rights_evidence") or payload.get("license_evidence") or "",
            "asset_rights_confirmed": payload.get("asset_rights_confirmed") or False,
            "market_samples": market_samples,
        }

        crawl = {
            "id": crawl_id,
            "type": "url_crawl",
            "url": url,
            "title": title,
            "description": description,
            "rights": rights,
            "pages": pages,
            "download_candidates": candidates,
            "image_candidates": images,
            "downloads": downloads,
            "download_blocked_reason": blocked_reason,
            "opportunity_payload": opportunity_payload,
            "created_at": now_iso(),
            "saved_to": "",
        }
        path = self.storage.save_crawl(crawl)
        crawl["saved_to"] = str(path)
        self.storage.save_crawl(crawl)
        self.storage.append_event(
            "crawler.url",
            {"id": crawl_id, "url": url, "candidates": len(candidates), "downloads": len(downloads)},
        )
        return crawl

    def _crawl_pages(self, url: str, max_pages: int) -> List[Dict[str, Any]]:
        visited: Set[str] = set()
        queue = [url]
        pages: List[Dict[str, Any]] = []
        base_host = urlparse(url).netloc

        while queue and len(pages) < max_pages:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            page = self._fetch_page(current)
            pages.append(page)

            for link in page.get("links", []):
                next_url = link["url"]
                parsed = urlparse(next_url)
                if parsed.netloc == base_host and next_url not in visited and not is_download_url(next_url):
                    queue.append(next_url)
                    if len(queue) + len(pages) >= max_pages:
                        break
        return pages

    def _fetch_page(self, url: str) -> Dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        response = session.get(url, timeout=30, headers={"User-Agent": "xianyu-commerce-agent-crawler"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        body = response.text if "text/html" in content_type or "text/" in content_type else ""
        parser = LinkParser()
        if body:
            parser.feed(body[:2_000_000])
        links = [{"url": urljoin(url, item["href"]), "text": item.get("text", "")} for item in parser.links]
        images = [{"url": urljoin(url, item["src"]), "alt": item.get("alt", "")} for item in parser.images]
        return {
            "url": url,
            "status_code": response.status_code,
            "content_type": content_type,
            "title": " ".join(parser.title_parts).strip(),
            "description": parser.meta.get("description", ""),
            "og_description": parser.meta.get("og:description", ""),
            "links": links,
            "images": images,
        }

    def _download_candidates(self, crawl_id: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        target_dir = self.storage.crawl_dir(crawl_id) / "downloads"
        downloaded: List[Dict[str, Any]] = []
        max_bytes = self.settings.crawler_max_file_mb * 1024 * 1024
        for candidate in candidates[: self.settings.crawler_max_downloads]:
            try:
                saved = self._download_one(candidate["url"], target_dir, max_bytes=max_bytes)
                saved.update({"source_url": candidate["url"], "label": candidate.get("label", "")})
                downloaded.append(saved)
            except Exception as exc:
                downloaded.append({"source_url": candidate["url"], "error": str(exc)})
        return downloaded

    def _download_one(self, url: str, target_dir: Path, max_bytes: int) -> Dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        with session.get(url, stream=True, timeout=60, headers={"User-Agent": "xianyu-commerce-agent-crawler"}) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length") or 0)
            if content_length and content_length > max_bytes:
                raise ValueError("file is larger than configured crawler limit")
            filename = safe_download_name(url, response.headers.get("content-type", ""))
            target = target_dir / filename
            total = 0
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("file exceeded configured crawler limit")
                    handle.write(chunk)
        return {"local_path": str(target), "bytes": total}


def collect_download_candidates(base_url: str, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    candidates: List[Dict[str, Any]] = []
    for page in pages:
        page_url = page.get("url", "")
        if page_url and is_download_url(page_url) and page_url not in seen:
            seen.add(page_url)
            candidates.append({"url": page_url, "label": "source file", "kind": guess_kind(page_url)})
        for link in page.get("links", []):
            link_url = urljoin(base_url, link["url"])
            if link_url in seen:
                continue
            if is_download_url(link_url) or "download" in link_url.lower() or "download" in link.get("text", "").lower():
                seen.add(link_url)
                candidates.append({"url": link_url, "label": link.get("text", ""), "kind": guess_kind(link_url)})
    return candidates


def collect_images(base_url: str, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    images: List[Dict[str, Any]] = []
    for page in pages:
        for image in page.get("images", []):
            image_url = urljoin(base_url, image["url"])
            if image_url in seen:
                continue
            seen.add(image_url)
            images.append({"url": image_url, "alt": image.get("alt", ""), "usable_for_publish": False})
    return images[:50]


def is_download_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DOWNLOAD_EXTENSIONS)


def guess_kind(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in DOWNLOAD_EXTENSIONS:
        if path.endswith(ext):
            return ext.lstrip(".")
    return "download"


def extract_url(text: str) -> str:
    match = re.search(r"https?://[^\s<>\"']+", text)
    return match.group(0) if match else ""


def looks_like_url_crawl_request(text: str) -> bool:
    stripped = text.strip().lower()
    url = extract_url(text)
    return bool(url) and (stripped == url.lower() or stripped.startswith(("url:", "crawl:", "crawler:", "source:", "site:")))


def crawl_payload_from_text(text: str) -> Dict[str, Any]:
    payload = payload_from_text(text)
    payload["url"] = extract_url(text)
    payload["text"] = text
    return payload


def first_nonempty(values: Iterable[Optional[str]]) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def safe_download_name(url: str, content_type: str) -> str:
    path_name = Path(urlparse(url).path).name
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", path_name).strip("-")
    if not name:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin"
        name = f"download-{uuid4().hex[:8]}{ext}"
    return name
