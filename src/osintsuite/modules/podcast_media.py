"""Podcast & media module — searches for podcast appearances and media mentions."""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class PodcastMediaModule(BaseModule):
    name = "podcast_media"
    description = "Search for podcast appearances, interviews, and media mentions"

    ITUNES_SEARCH_API = "https://itunes.apple.com/search"
    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label or ""
        if not name:
            self.logger.info("No name available, skipping podcast/media search")
            return results

        # 1. iTunes Search API
        results.extend(await self._search_itunes(name))

        # 2. DuckDuckGo dork searches for media mentions
        results.extend(await self._search_media_dorks(name))

        # Deduplicate by URL
        seen_urls: set[str] = set()
        deduped: list[ModuleResult] = []
        for r in results:
            url = r.data.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            deduped.append(r)

        media_results = deduped[: self.MAX_RESULTS]

        # Summary finding
        media_results.append(
            ModuleResult(
                module_name=self.name,
                source="podcast_media",
                finding_type="media_summary",
                title=f"Podcast & media search summary for {name}",
                content=(
                    f"Found {len(media_results)} media-related result(s) for "
                    f'"{name}" across iTunes, YouTube, Spotify, and web searches.'
                ),
                data={
                    "name": name,
                    "total_results": len(media_results),
                },
                confidence=50,
            )
        )

        return media_results

    # ------------------------------------------------------------------
    # iTunes Search API
    # ------------------------------------------------------------------

    async def _search_itunes(self, name: str) -> list[ModuleResult]:
        """Query the iTunes Search API for podcast mentions."""
        results: list[ModuleResult] = []

        params = {
            "term": name,
            "entity": "podcast",
            "limit": "10",
        }
        url = f"{self.ITUNES_SEARCH_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"iTunes Search API request failed: {exc}")
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse iTunes JSON response")
            return results

        items: list[dict[str, Any]] = data.get("results", [])

        for item in items[:10]:
            track_name = item.get("trackName", item.get("collectionName", "Unknown"))
            artist_name = item.get("artistName", "")
            track_url = item.get("trackViewUrl", item.get("collectionViewUrl", ""))
            genre = item.get("primaryGenreName", "")
            artwork_url = item.get("artworkUrl100", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="itunes",
                    finding_type="podcast_mention",
                    title=f"Podcast: {track_name}",
                    content=(
                        f"Podcast \"{track_name}\" by {artist_name}. "
                        f"Genre: {genre}."
                    ),
                    data={
                        "title": track_name,
                        "url": track_url,
                        "artist": artist_name,
                        "genre": genre,
                        "artwork_url": artwork_url,
                        "source": "itunes",
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"iTunes Search returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo media dork searches
    # ------------------------------------------------------------------

    async def _search_media_dorks(self, name: str) -> list[ModuleResult]:
        """Run DDG dork queries for media appearances."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{name}" podcast OR interview OR "guest on"',
            f'"{name}" site:youtube.com',
            f'"{name}" site:spotify.com',
            f'"{name}" site:apple.com/podcast',
        ]

        all_results: list[ModuleResult] = []

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG dork search failed for '{query}': {exc}")
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                finding_type, confidence = self._classify_media_hit(url, title, snippet)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Media result for {name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "platform": self._detect_platform(url),
                        },
                        confidence=confidence,
                    )
                )

        self.logger.info(
            f"DDG media dorks found {len(all_results)} results for '{name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_media_hit(url: str, title: str, snippet: str) -> tuple[str, int]:
        """Classify finding type and confidence based on content."""
        url_lower = url.lower()
        combined = f"{title} {snippet}".lower()

        if "youtube.com" in url_lower:
            return "youtube_mention", 55
        if "spotify.com" in url_lower:
            return "podcast_mention", 60
        if "apple.com/podcast" in url_lower:
            return "podcast_mention", 60
        if any(kw in combined for kw in ("podcast", "episode", "interview", "guest")):
            return "media_appearance", 55
        return "media_appearance", 55

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Detect which media platform the URL belongs to."""
        url_lower = url.lower()
        if "youtube.com" in url_lower:
            return "youtube"
        if "spotify.com" in url_lower:
            return "spotify"
        if "apple.com" in url_lower:
            return "apple_podcasts"
        if "soundcloud.com" in url_lower:
            return "soundcloud"
        return "web"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
