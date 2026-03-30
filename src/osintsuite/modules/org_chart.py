"""Org chart module — discovers organizational leadership and executives."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class OrgChartModule(BaseModule):
    name = "org_chart"
    description = "Discover organizational leadership, executives, and key personnel"

    TITLE_PATTERNS = re.compile(
        r"\b(CEO|CTO|CFO|COO|CIO|CISO|CMO|CPO|CSO|"
        r"President|Vice\s+President|VP|Director|"
        r"Founder|Co-?Founder|Managing\s+Director|"
        r"Chief\s+\w+\s+Officer|Head\s+of\s+\w+|"
        r"General\s+Manager|Partner|Chairman|Chairwoman)\b",
        re.IGNORECASE,
    )

    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        org_name = target.label
        if not org_name:
            self.logger.info("No organization name available, skipping org chart")
            return results

        if not _HAS_DDGS:
            self.logger.warning("duckduckgo_search not installed — skipping org chart")
            return results

        # 1. Search for executives
        results.extend(await self._search_executives(org_name))

        # 2. Search LinkedIn company page
        results.extend(await self._search_linkedin(org_name))

        # 3. Search leadership team pages
        results.extend(await self._search_leadership(org_name))

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

        # Extract unique executives found
        executives = self._extract_executives(trimmed)

        # Summary finding
        trimmed.append(
            ModuleResult(
                module_name=self.name,
                source="org_chart",
                finding_type="org_chart_summary",
                title=f"Org chart summary for {org_name}",
                content=(
                    f"Found {len(trimmed)} leadership-related result(s) for "
                    f'"{org_name}". Identified {len(executives)} potential executive(s).'
                ),
                data={
                    "organization": org_name,
                    "total_results": len(trimmed),
                    "executives_found": executives,
                },
                confidence=50,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # DDG searches
    # ------------------------------------------------------------------

    async def _search_executives(self, org_name: str) -> list[ModuleResult]:
        """Search for executive mentions."""
        results: list[ModuleResult] = []
        query = f'"{org_name}" CEO OR president OR "chief" OR director OR founder'

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG executive search failed: {exc}")
            return results

        for hit in hits[:5]:
            title = hit.get("title", "")
            snippet = hit.get("body", "")
            titles_found = self.TITLE_PATTERNS.findall(f"{title} {snippet}")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="executive_mention",
                    title=title or f"Executive mention for {org_name}",
                    content=snippet or None,
                    data={
                        "url": hit.get("href", ""),
                        "snippet": snippet,
                        "titles_found": titles_found,
                        "source": "duckduckgo_dork",
                    },
                    confidence=55,
                )
            )

        return results

    async def _search_linkedin(self, org_name: str) -> list[ModuleResult]:
        """Search LinkedIn company page mentions."""
        results: list[ModuleResult] = []
        query = f'site:linkedin.com/company "{org_name}"'

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG LinkedIn search failed: {exc}")
            return results

        for hit in hits[:3]:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="linkedin",
                    finding_type="leadership_record",
                    title=hit.get("title", f"LinkedIn result for {org_name}"),
                    content=hit.get("body", None),
                    data={
                        "url": hit.get("href", ""),
                        "snippet": hit.get("body", ""),
                        "source": "linkedin_search",
                    },
                    confidence=60,
                )
            )

        return results

    async def _search_leadership(self, org_name: str) -> list[ModuleResult]:
        """Search for leadership team pages."""
        results: list[ModuleResult] = []
        query = f'"{org_name}" leadership team OR executive OR "our team"'

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG leadership search failed: {exc}")
            return results

        for hit in hits[:5]:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="leadership_record",
                    title=hit.get("title", f"Leadership page for {org_name}"),
                    content=hit.get("body", None),
                    data={
                        "url": hit.get("href", ""),
                        "snippet": hit.get("body", ""),
                        "source": "duckduckgo_dork",
                    },
                    confidence=60,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_executives(self, results: list[ModuleResult]) -> list[str]:
        """Extract unique executive titles from result data."""
        executives: set[str] = set()
        for r in results:
            titles = r.data.get("titles_found", [])
            executives.update(titles)
        return sorted(executives)

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
