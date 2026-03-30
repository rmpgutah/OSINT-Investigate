"""Property ownership, deeds, and real estate records search via DuckDuckGo dorking."""

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


class PropertyRecordsModule(BaseModule):
    name = "property_records"
    description = "Property ownership, deeds, and real estate records search"

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping property_records module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="property_search_summary",
                    title="Property Records module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping property search")
            return []

        state = target.state or ""
        city = target.city or ""

        dorks = self._generate_dorks(name, city, state)
        results: list[ModuleResult] = []
        total_hits = 0

        for idx, query in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            for hit in hits[:10 - total_hits]:
                if total_hits >= 10:
                    break
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")
                source = self._classify_source(url)
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="property_record",
                        title=title or "Property record result",
                        content=snippet,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": source,
                            "query": query,
                        },
                        confidence=45,
                    )
                )
                total_hits += 1

            if total_hits >= 10:
                break

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="duckduckgo",
                finding_type="property_search_summary",
                title=f"Property search summary for {name} ({total_hits} results)",
                content=None,
                data={
                    "target_name": name,
                    "city": city,
                    "state": state,
                    "total_dorks_run": len(dorks),
                    "total_results_found": total_hits,
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(name: str, city: str, state: str) -> list[str]:
        """Build property-related DuckDuckGo dork queries."""
        dorks: list[str] = []

        # General property records with state
        if state:
            dorks.append(f'"{name}" property records {state}')
        else:
            dorks.append(f'"{name}" property records')

        # Deed search with city
        if city:
            dorks.append(f'"{name}" deed {city}')
        else:
            dorks.append(f'"{name}" deed records')

        # Zillow search
        dorks.append(f'"{name}" site:zillow.com')

        # County assessor / government property tax sites
        dorks.append(f'"{name}" site:*.gov "property tax" OR "assessor"')

        return dorks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_source(url: str) -> str:
        """Attempt to classify a URL into a known property data source."""
        url_lower = url.lower()
        if "zillow.com" in url_lower:
            return "zillow"
        if "realtor.com" in url_lower:
            return "realtor"
        if "redfin.com" in url_lower:
            return "redfin"
        if ".gov" in url_lower:
            return "government"
        if "trulia.com" in url_lower:
            return "trulia"
        return "web"

    async def _search(self, query: str) -> list[dict[str, Any]]:
        """Run a single DuckDuckGo search via asyncio.to_thread."""
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            return hits
        except Exception as exc:
            self.logger.warning(f"Search failed for property dork '{query}': {exc}")
            return []

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
