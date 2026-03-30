"""Dating profile module — searches for dating site profiles and mentions."""

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


class DatingProfileModule(BaseModule):
    name = "dating_profile"
    description = "Search for dating site profiles and mentions"

    MAX_RESULTS = 15

    DATING_PLATFORMS = [
        "match.com", "pof.com", "okcupid.com", "tinder.com",
        "bumble.com", "hinge.co", "zoosk.com", "eharmony.com",
    ]

    def applicable_target_types(self) -> list[str]:
        return ["person", "email", "username", "phone"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label or ""
        username = getattr(target, "username", "") or ""
        email = getattr(target, "email", "") or ""
        phone = getattr(target, "phone", "") or ""

        search_terms: list[str] = [t for t in [name, username, email, phone] if t]
        if not search_terms:
            self.logger.info("No search terms available, skipping dating profile search")
            return results

        # DuckDuckGo dork searches for dating profiles
        results.extend(await self._search_dating_dorks(search_terms))

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

        dating_results = deduped[: self.MAX_RESULTS]

        # Summary finding
        platforms_found = set()
        for r in dating_results:
            platform = r.data.get("platform", "")
            if platform:
                platforms_found.add(platform)

        primary_term = search_terms[0]
        dating_results.append(
            ModuleResult(
                module_name=self.name,
                source="dating_profile",
                finding_type="dating_summary",
                title=f"Dating profile search summary for {primary_term}",
                content=(
                    f"Found {len(dating_results)} dating-related result(s) for "
                    f'"{primary_term}". '
                    f"Platforms referenced: {', '.join(platforms_found) if platforms_found else 'none'}."
                ),
                data={
                    "search_terms": search_terms,
                    "total_results": len(dating_results),
                    "platforms_found": list(platforms_found),
                },
                confidence=45,
            )
        )

        return dating_results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dating_dorks(
        self, search_terms: list[str]
    ) -> list[ModuleResult]:
        """Run DDG dork queries for dating profile mentions."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries: list[str] = []
        for term in search_terms:
            queries.append(
                f'"{term}" site:match.com OR site:pof.com OR site:okcupid.com'
            )
            queries.append(
                f'"{term}" tinder OR bumble OR hinge'
            )
            queries.append(
                f'"{term}" dating profile'
            )

        # Deduplicate queries
        queries = list(dict.fromkeys(queries))

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

                platform = self._detect_platform(url)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="dating_profile_mention",
                        title=title or f"Dating profile result for {search_terms[0]}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "platform": platform,
                        },
                        confidence=50,
                    )
                )

        self.logger.info(
            f"DDG dating dorks found {len(all_results)} results for {search_terms}"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_platform(self, url: str) -> str:
        """Detect which dating platform the URL belongs to."""
        url_lower = url.lower()
        for platform in self.DATING_PLATFORMS:
            if platform in url_lower:
                return platform.split(".")[0]
        return "unknown"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
