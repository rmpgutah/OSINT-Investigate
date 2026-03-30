"""Social deep-dive module — newer and alternative social platforms."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class SocialDeepModule(BaseModule):
    name = "social_deep"
    description = "Deep social media search — TikTok, Threads, Mastodon, Bluesky, Discord, Telegram"

    MAX_RESULTS = 25

    # Platform definitions: (site_domain, platform_label)
    PLATFORMS = [
        ("tiktok.com", "TikTok"),
        ("threads.net", "Threads"),
        ("mastodon.social", "Mastodon"),
        ("bsky.app", "Bluesky"),
        ("discord.me", "Discord"),
        ("telegram.me", "Telegram"),
    ]

    def applicable_target_types(self) -> list[str]:
        return ["person", "username"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label
        username = getattr(target, "username", None) or ""
        if not name and not username:
            self.logger.info("No name or username available, skipping social deep")
            return results

        search_term = username if username else name

        # DuckDuckGo platform-specific searches
        results.extend(await self._search_platforms(search_term, name or username))

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

        capped = deduped[: self.MAX_RESULTS]

        # Summary finding
        capped.append(
            ModuleResult(
                module_name=self.name,
                source="social_deep",
                finding_type="social_deep_summary",
                title=f"Social deep-dive summary for {search_term}",
                content=(
                    f"Found {len(capped)} social-media result(s) for "
                    f'"{search_term}" across TikTok, Threads, Mastodon, '
                    f"Bluesky, Discord, and Telegram."
                ),
                data={
                    "search_term": search_term,
                    "total_results": len(capped),
                    "platforms_searched": [p[1] for p in self.PLATFORMS],
                },
                confidence=55,
            )
        )

        return capped

    # ------------------------------------------------------------------
    # Platform-specific DuckDuckGo searches
    # ------------------------------------------------------------------

    async def _search_platforms(
        self, search_term: str, display_name: str
    ) -> list[ModuleResult]:
        """Run DuckDuckGo site-specific searches for each platform."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping social deep searches"
            )
            return []

        all_results: list[ModuleResult] = []

        for site_domain, platform_label in self.PLATFORMS:
            query = f'"{search_term}" site:{site_domain}'

            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(
                    f"DDG search failed for {platform_label}: {exc}"
                )
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                # Try to extract useful social signals from snippet
                bio_text = ""
                follower_info = ""
                snippet_lower = snippet.lower()

                if "followers" in snippet_lower or "following" in snippet_lower:
                    follower_info = self._extract_follower_info(snippet)

                if any(kw in snippet_lower for kw in ["bio", "about", "description"]):
                    bio_text = snippet[:200]

                # Determine if this is a profile or activity
                is_profile = any(
                    indicator in url.lower()
                    for indicator in ["/@", "/user/", "/profile/", "/u/"]
                ) or url.rstrip("/").count("/") <= 3

                finding_type = "social_profile_deep" if is_profile else "social_activity"
                confidence = 60 if is_profile else 50

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"{platform_label} result for {display_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "platform": platform_label,
                            "bio_text": bio_text,
                            "follower_info": follower_info,
                            "source": "duckduckgo_dork",
                        },
                        confidence=confidence,
                    )
                )

            self.logger.info(
                f"{platform_label}: found {min(len(hits) if 'hits' in dir() else 0, 5)} "
                f"result(s) for '{search_term}'"
            )

        self.logger.info(
            f"Social deep searches found {len(all_results)} total results for '{search_term}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_follower_info(snippet: str) -> str:
        """Try to extract follower/following counts from a snippet."""
        import re

        patterns = [
            r"([\d,.]+[KkMm]?)\s*followers?",
            r"([\d,.]+[KkMm]?)\s*following",
            r"([\d,.]+[KkMm]?)\s*likes?",
            r"([\d,.]+[KkMm]?)\s*posts?",
        ]
        parts = []
        for pattern in patterns:
            match = re.search(pattern, snippet, re.IGNORECASE)
            if match:
                parts.append(match.group(0))
        return "; ".join(parts)

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
