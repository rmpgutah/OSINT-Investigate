"""Social Security Death Index and obituary search module."""

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


class SsdiLookupModule(BaseModule):
    name = "ssdi_lookup"
    description = "Social Security Death Index and obituary search"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        if not target.full_name:
            self.logger.info("No full_name on target, skipping SSDI/obituary search")
            return results

        full_name = target.full_name
        city = target.city or ""
        state = target.state or ""

        # 1. Obituary search
        obit_results = await self._search_obituaries(full_name, city, state)
        results.extend(obit_results)

        # 2. SSDI / death record search
        ssdi_results = await self._search_ssdi(full_name, city, state)
        results.extend(ssdi_results)

        # 3. FamilySearch death records
        fs_results = await self._search_familysearch_death(full_name)
        results.extend(fs_results)

        # Summary finding
        death_records = len(
            [r for r in results if r.finding_type == "death_record"]
        )
        obituaries = len(
            [r for r in results if r.finding_type == "obituary"]
        )
        results.append(
            ModuleResult(
                module_name=self.name,
                source="ssdi_lookup",
                finding_type="ssdi_summary",
                title=f"SSDI/obituary search for {full_name}",
                content=(
                    f"Found {death_records} death record(s) and "
                    f"{obituaries} obituary result(s) for '{full_name}'"
                    + (f" in {city}, {state}" if city or state else "")
                ),
                data={
                    "full_name": full_name,
                    "city": city,
                    "state": state,
                    "death_records": death_records,
                    "obituaries": obituaries,
                    "sources_checked": [
                        "duckduckgo_obituary",
                        "duckduckgo_ssdi",
                        "familysearch",
                    ],
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Obituary search
    # ------------------------------------------------------------------

    async def _search_obituaries(
        self, full_name: str, city: str, state: str
    ) -> list[ModuleResult]:
        """Search DuckDuckGo for obituaries."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping obituary search"
            )
            return []

        location = f"{city} {state}".strip()
        query = f'"{full_name}" obituary'
        if location:
            query += f" {location}"

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"Obituary search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:10]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")

            # Classify as obituary vs generic death record
            combined_lower = (title + " " + snippet).lower()
            is_obituary = any(
                kw in combined_lower
                for kw in ("obituary", "obituaries", "memorial", "tribute", "legacy.com")
            )

            finding_type = "obituary" if is_obituary else "death_record"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type=finding_type,
                    title=title or f"Obituary result for {full_name}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "duckduckgo_obituary",
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"Obituary search found {len(results)} results for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # SSDI / death record search
    # ------------------------------------------------------------------

    async def _search_ssdi(
        self, full_name: str, city: str, state: str
    ) -> list[ModuleResult]:
        """Search DuckDuckGo for Social Security Death Index references."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping SSDI search"
            )
            return []

        query = f'"{full_name}" "social security death"'
        location = f"{city} {state}".strip()
        if location:
            query += f" {location}"

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"SSDI search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:10]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="death_record",
                    title=title or f"SSDI result for {full_name}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "duckduckgo_ssdi",
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"SSDI search found {len(results)} results for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # FamilySearch death records
    # ------------------------------------------------------------------

    async def _search_familysearch_death(
        self, full_name: str
    ) -> list[ModuleResult]:
        """Search DuckDuckGo for FamilySearch death records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping FamilySearch death search"
            )
            return []

        query = f'"{full_name}" site:familysearch.org death'

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"FamilySearch death search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:10]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="familysearch",
                    finding_type="death_record",
                    title=title or f"FamilySearch death record for {full_name}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "familysearch",
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"FamilySearch death search found {len(results)} results for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # Sync search helper
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
