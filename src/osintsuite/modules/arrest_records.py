"""Arrest records module — searches booking records, mugshots, and arrest databases."""

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


class ArrestRecordsModule(BaseModule):
    name = "arrest_records"
    description = "Arrest records, booking records, and mugshot search"

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
            self.logger.info("No name available on target, skipping arrest records")
            return results

        state = target.state or ""

        # DuckDuckGo dork searches for arrest/booking records
        results.extend(await self._search_dorks(full_name, state))

        # State DOC inmate search via DDG dork
        results.extend(await self._search_state_doc(full_name, state))

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
        booking_count = len(
            [r for r in case_results if r.finding_type == "booking_record"]
        )
        arrest_count = len(
            [r for r in case_results if r.finding_type == "arrest_record"]
        )
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="arrest_records",
                finding_type="arrest_summary",
                title=f"Arrest records summary for {full_name}",
                content=(
                    f"Found {len(case_results)} arrest-related result(s) for "
                    f'"{full_name}" ({arrest_count} arrest(s), {booking_count} booking(s)).'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "total_results": len(case_results),
                    "arrest_count": arrest_count,
                    "booking_count": booking_count,
                },
                confidence=50,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for arrest records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" arrested',
            f'"{full_name}" booking' + (f" {state}" if state else ""),
            f'site:arrests.org "{full_name}"',
            f'"{full_name}" mugshot',
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

                arrest_type = self._classify_arrest_type(title, snippet, url)
                finding_type = (
                    "booking_record"
                    if arrest_type == "booking"
                    else "arrest_record"
                )

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Arrest result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "arrest_type": arrest_type,
                            "jurisdiction": state,
                            "date_found": "",
                        },
                        confidence=60 if finding_type == "arrest_record" else 65,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # State DOC inmate search via DDG
    # ------------------------------------------------------------------

    async def _search_state_doc(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Search state DOC inmate databases via DuckDuckGo dork."""
        if not _HAS_DDGS:
            return []

        query = f'site:*.state.*.us inmate "{full_name}"'
        if state:
            query += f" {state}"

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG state DOC search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:5]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="arrest_record",
                    title=title or f"State DOC result for {full_name}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "state_doc_dork",
                        "arrest_type": "state_doc",
                        "jurisdiction": state,
                        "date_found": "",
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"State DOC dork found {len(results)} results for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_arrest_type(title: str, snippet: str, url: str) -> str:
        """Classify arrest record type based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "booking" in combined or "booked" in combined:
            return "booking"
        if "mugshot" in combined:
            return "mugshot"
        if "jail" in combined:
            return "jail"
        return "arrest"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
