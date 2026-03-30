"""Real estate deep module — searches for deeds, property listings, landlord/tenant records."""

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


class RealEstateDeepModule(BaseModule):
    name = "real_estate_deep"
    description = "Deep real estate search: deeds, titles, property listings, landlord/tenant records"

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
            self.logger.info("No name available on target, skipping real estate deep")
            return results

        state = target.state or ""
        city = target.city or ""

        # DuckDuckGo dork searches
        results.extend(await self._search_dorks(full_name, state, city))

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

        case_results = deduped[: self.MAX_RESULTS]

        # Summary finding
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="real_estate_deep",
                finding_type="real_estate_summary",
                title=f"Real estate deep summary for {full_name}",
                content=(
                    f"Found {len(case_results)} real-estate-related result(s) for "
                    f'"{full_name}" via DuckDuckGo dork searches.'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "city": city,
                    "total_results": len(case_results),
                },
                confidence=50,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str, city: str
    ) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for real estate records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" deed OR title' + (f" {state}" if state else ""),
            f'"{full_name}" property owner' + (f" {city}" if city else ""),
            f'site:zillow.com "{full_name}"',
            f'site:realtor.com "{full_name}"',
            f'"{full_name}" landlord OR tenant',
        ]

        if state:
            queries.append(f'site:*.gov recorder "{full_name}" {state}')

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

                property_type = self._classify_property_type(title, snippet, url)
                finding_type = self._finding_type_for(property_type)
                address_found = self._extract_address_hint(snippet)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Real estate result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "property_type": property_type,
                            "address_found": address_found,
                            "jurisdiction": state,
                        },
                        confidence=self._confidence_for(finding_type),
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} real estate results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_property_type(title: str, snippet: str, url: str) -> str:
        """Classify the type of real estate record based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "deed" in combined or "title" in combined:
            return "deed"
        if "zillow" in combined or "realtor.com" in combined or "listing" in combined:
            return "listing"
        if "landlord" in combined or "tenant" in combined or "rental" in combined:
            return "rental"
        if "recorder" in combined or "assessor" in combined:
            return "public_record"
        return "property"

    @staticmethod
    def _finding_type_for(property_type: str) -> str:
        """Map property type to finding type."""
        mapping = {
            "deed": "deed_record",
            "listing": "property_listing",
            "rental": "landlord_tenant",
            "public_record": "deed_record",
        }
        return mapping.get(property_type, "property_listing")

    @staticmethod
    def _confidence_for(finding_type: str) -> int:
        """Return confidence based on finding type."""
        mapping = {
            "deed_record": 60,
            "property_listing": 55,
            "landlord_tenant": 50,
        }
        return mapping.get(finding_type, 55)

    @staticmethod
    def _extract_address_hint(snippet: str) -> str:
        """Try to extract a rough address hint from snippet text."""
        if not snippet:
            return ""
        # Look for common street suffixes as a simple heuristic
        import re

        match = re.search(
            r"\d{1,6}\s+\w[\w\s]{2,30}\b(?:St|Ave|Blvd|Dr|Rd|Ln|Ct|Way|Pl|Cir)\b",
            snippet,
            re.IGNORECASE,
        )
        return match.group(0).strip() if match else ""

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
