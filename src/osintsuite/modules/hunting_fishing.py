"""Hunting & fishing license and wildlife violation search module."""

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


class HuntingFishingModule(BaseModule):
    name = "hunting_fishing"
    description = "Hunting/fishing license and wildlife violation search"

    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping hunting/fishing")
            return results

        state = target.state or ""

        # 1. DuckDuckGo dork searches
        results.extend(await self._search_dorks(full_name, state))

        # 2. Game warden / wildlife violation searches
        results.extend(await self._search_violations(full_name, state))

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

        trimmed = deduped[: self.MAX_RESULTS]

        # Summary finding
        trimmed.append(
            ModuleResult(
                module_name=self.name,
                source="hunting_fishing",
                finding_type="outdoor_summary",
                title=f"Hunting/fishing summary for {full_name}",
                content=(
                    f"Found {len(trimmed)} hunting/fishing-related result(s) for "
                    f'"{full_name}" across DuckDuckGo dork searches.'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "total_results": len(trimmed),
                },
                confidence=50,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches — licenses
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for hunting/fishing licenses."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" hunting license' + (f" {state}" if state else ""),
            f'"{full_name}" fishing license' + (f" {state}" if state else ""),
            f'site:*.gov "{full_name}" hunting OR fishing',
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

                finding_type = self._classify_license_type(title, snippet, url)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Hunting/fishing result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "state": state,
                        },
                        confidence=55,
                    )
                )

        self.logger.info(
            f"DDG license searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Wildlife violation searches
    # ------------------------------------------------------------------

    async def _search_violations(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Search for wildlife / game warden violation records."""
        if not _HAS_DDGS:
            return []

        queries = [
            f'"{full_name}" wildlife violation',
            f'"{full_name}" game warden' + (f" {state}" if state else ""),
            f'"{full_name}" poaching OR "fish and game"',
        ]

        all_results: list[ModuleResult] = []

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(
                    f"DDG violation search failed for '{query}': {exc}"
                )
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="wildlife_violation",
                        title=title or f"Wildlife violation result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "state": state,
                        },
                        confidence=60,
                    )
                )

        self.logger.info(
            f"DDG violation searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_license_type(title: str, snippet: str, url: str) -> str:
        """Classify whether the result is hunting or fishing license related."""
        combined = f"{title} {snippet} {url}".lower()
        if "fishing" in combined:
            return "fishing_license"
        if "hunting" in combined:
            return "hunting_license"
        return "hunting_license"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
