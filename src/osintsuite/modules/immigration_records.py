"""Immigration records module — searches for naturalization, citizenship, and passenger records."""

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


class ImmigrationRecordsModule(BaseModule):
    name = "immigration_records"
    description = "Immigration, naturalization, and citizenship records"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping immigration_records module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="immigration_summary",
                    title="Immigration Records module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping immigration_records")
            return []

        dorks = self._generate_dorks(full_name)
        results: list[ModuleResult] = []
        seen_urls: set[str] = set()
        total_found = 0

        for idx, (query, finding_type, confidence) in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            for hit in hits:
                url = hit.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if total_found >= 15:
                    break

                title = hit.get("title", "")
                snippet = hit.get("body", "")
                record_type = self._classify_record_type(title, snippet)
                country_origin = self._extract_country_origin(title, snippet)
                date_found = self._extract_date(title, snippet)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=f"Immigration result: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo",
                            "record_type": record_type,
                            "country_origin": country_origin,
                            "date_found": date_found,
                        },
                        confidence=confidence,
                    )
                )
                total_found += 1

            if total_found >= 15:
                break

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="duckduckgo",
                finding_type="immigration_summary",
                title=f"Immigration records search for {full_name} ({total_found} results)",
                content=None,
                data={
                    "full_name": full_name,
                    "total_results": total_found,
                    "dorks_run": len(dorks),
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(full_name: str) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        return [
            (
                f'"{full_name}" naturalization OR citizenship',
                "naturalization_record",
                60,
            ),
            (
                f'"{full_name}" immigration record',
                "immigration_record",
                55,
            ),
            (
                f'"{full_name}" visa',
                "immigration_record",
                55,
            ),
            (
                f'site:uscis.gov "{full_name}"',
                "immigration_record",
                55,
            ),
            (
                f'site:libertyellisfoundation.org "{full_name}"',
                "immigration_record",
                55,
            ),
            (
                f'site:archives.gov "{full_name}" immigration OR passenger',
                "immigration_record",
                55,
            ),
        ]

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_record_type(title: str, snippet: str) -> str:
        """Classify the type of immigration record."""
        text = (title + " " + snippet).lower()
        if any(kw in text for kw in ["naturalization", "citizenship", "naturalized"]):
            return "naturalization"
        if any(kw in text for kw in ["passenger", "manifest", "arrival", "ellis island"]):
            return "passenger_record"
        if any(kw in text for kw in ["visa", "h-1b", "green card", "permanent resident"]):
            return "visa"
        if any(kw in text for kw in ["immigration", "immigrant"]):
            return "immigration"
        return "unknown"

    @staticmethod
    def _extract_country_origin(title: str, snippet: str) -> str:
        """Attempt to extract country of origin from result text."""
        text = (title + " " + snippet).lower()
        # Common origin references
        countries = [
            "ireland", "germany", "italy", "england", "china", "japan",
            "mexico", "canada", "poland", "russia", "india", "france",
            "scotland", "sweden", "norway", "greece", "cuba", "philippines",
            "korea", "vietnam", "ukraine", "brazil", "colombia",
        ]
        for country in countries:
            if country in text:
                return country.title()
        return ""

    @staticmethod
    def _extract_date(title: str, snippet: str) -> str:
        """Try to extract a date from result text."""
        import re

        text = title + " " + snippet
        # Look for dates in various formats
        match = re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
            text,
        )
        if match:
            return match.group(0)
        # Fallback: just a year
        match = re.search(r"\b(18|19|20)\d{2}\b", text)
        return match.group(0) if match else ""

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _search(self, query: str) -> list[dict[str, Any]]:
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            return hits
        except Exception as exc:
            self.logger.warning(f"Search failed for dork '{query}': {exc}")
            return []

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=10))
