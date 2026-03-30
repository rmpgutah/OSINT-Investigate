"""Forum search module — searches forums, Reddit, and Stack Exchange for target mentions."""

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


class ForumSearchModule(BaseModule):
    name = "forum_search"
    description = "Search forums, Reddit, Stack Exchange, and discussion boards"

    STACKEXCHANGE_API = "https://api.stackexchange.com/2.3/users"
    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person", "email", "username"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        identifier = target.full_name or target.label or ""
        username = getattr(target, "username", "") or ""
        email = getattr(target, "email", "") or ""

        search_term = username or identifier or email
        if not search_term:
            self.logger.info("No search term available, skipping forum search")
            return results

        # 1. Stack Exchange API
        results.extend(await self._search_stackexchange(search_term))

        # 2. DuckDuckGo forum dork searches
        results.extend(await self._search_forum_dorks(search_term, username, email))

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

        forum_results = deduped[: self.MAX_RESULTS]

        # Summary finding
        forum_results.append(
            ModuleResult(
                module_name=self.name,
                source="forum_search",
                finding_type="forum_summary",
                title=f"Forum search summary for {search_term}",
                content=(
                    f"Found {len(forum_results)} forum-related result(s) for "
                    f'"{search_term}" across Stack Exchange, Reddit, and other forums.'
                ),
                data={
                    "search_term": search_term,
                    "total_results": len(forum_results),
                },
                confidence=50,
            )
        )

        return forum_results

    # ------------------------------------------------------------------
    # Stack Exchange API
    # ------------------------------------------------------------------

    async def _search_stackexchange(self, name: str) -> list[ModuleResult]:
        """Query Stack Exchange API for user profiles."""
        results: list[ModuleResult] = []

        params = {
            "inname": name,
            "site": "stackoverflow",
            "pagesize": "10",
            "order": "desc",
            "sort": "reputation",
        }
        url = f"{self.STACKEXCHANGE_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"Stack Exchange API request failed: {exc}")
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse Stack Exchange JSON response")
            return results

        items: list[dict[str, Any]] = data.get("items", [])

        for item in items[:10]:
            display_name = item.get("display_name", "Unknown")
            profile_url = item.get("link", "")
            reputation = item.get("reputation", 0)
            user_id = item.get("user_id", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="stackexchange",
                    finding_type="stack_profile",
                    title=f"Stack Overflow profile: {display_name}",
                    content=(
                        f"Stack Overflow user \"{display_name}\" with reputation "
                        f"{reputation:,}."
                    ),
                    data={
                        "title": display_name,
                        "url": profile_url,
                        "reputation": reputation,
                        "user_id": user_id,
                        "source": "stackexchange",
                    },
                    confidence=70,
                )
            )

        self.logger.info(
            f"Stack Exchange returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo forum dork searches
    # ------------------------------------------------------------------

    async def _search_forum_dorks(
        self, search_term: str, username: str, email: str
    ) -> list[ModuleResult]:
        """Run DDG dork queries across popular forums."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{search_term}" site:reddit.com',
            f'"{search_term}" site:4chan.org',
            f'"{search_term}" site:hackforums.net',
            f'"{search_term}" site:bitcointalk.org',
        ]

        if username:
            queries.append(f'"{username}" reddit comments OR posts')
        if email:
            queries.append(f'"{email}" site:reddit.com OR site:hackforums.net')

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

                finding_type, confidence = self._classify_forum_hit(url)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Forum result for {search_term}",
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
            f"DDG forum dorks found {len(all_results)} results for '{search_term}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_forum_hit(url: str) -> tuple[str, int]:
        """Classify finding type and confidence based on the URL."""
        url_lower = url.lower()
        if "reddit.com" in url_lower:
            return "reddit_activity", 60
        if "stackoverflow.com" in url_lower or "stackexchange.com" in url_lower:
            return "stack_profile", 70
        return "forum_post", 55

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Detect which platform the URL belongs to."""
        url_lower = url.lower()
        for platform in ("reddit.com", "4chan.org", "hackforums.net",
                         "bitcointalk.org", "stackoverflow.com"):
            if platform in url_lower:
                return platform.split(".")[0]
        return "unknown"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
