from __future__ import annotations

from typing import List

import requests

from .config import Settings
from .models import ImageCandidate


class ImageSearchClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def search_many(self, queries: List[str], limit_per_query: int = 3) -> List[ImageCandidate]:
        candidates: List[ImageCandidate] = []
        for query in queries[:3]:
            candidates.extend(self.search(query, limit=limit_per_query))
        return candidates

    def search(self, query: str, limit: int = 3) -> List[ImageCandidate]:
        provider = self.settings.image_search_provider
        if provider == "serpapi" and self.settings.serpapi_key:
            return self._serpapi(query, limit)
        if provider == "bing" and self.settings.bing_search_key:
            return self._bing(query, limit)
        return []

    def _serpapi(self, query: str, limit: int) -> List[ImageCandidate]:
        response = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_images", "q": query, "api_key": self.settings.serpapi_key},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("images_results", [])[:limit]
        return [
            ImageCandidate(
                url=item.get("original") or item.get("link") or "",
                thumbnail=item.get("thumbnail", ""),
                source=item.get("source", "serpapi"),
                title=item.get("title", ""),
                license_hint="unknown",
                usable_for_publish=self.settings.allow_external_product_images,
            )
            for item in results
            if item.get("original") or item.get("link")
        ]

    def _bing(self, query: str, limit: int) -> List[ImageCandidate]:
        response = requests.get(
            "https://api.bing.microsoft.com/v7.0/images/search",
            params={"q": query, "count": limit},
            headers={"Ocp-Apim-Subscription-Key": self.settings.bing_search_key},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return [
            ImageCandidate(
                url=item.get("contentUrl", ""),
                thumbnail=item.get("thumbnailUrl", ""),
                source=item.get("hostPageDomainFriendlyName") or item.get("hostPageUrl", "bing"),
                title=item.get("name", ""),
                license_hint="unknown",
                usable_for_publish=self.settings.allow_external_product_images,
            )
            for item in data.get("value", [])[:limit]
            if item.get("contentUrl")
        ]
