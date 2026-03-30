"""Criminal records module — searches criminal record databases and JudyRecords."""

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


class CriminalRecordsModule(BaseModule):
    name = "criminal_records"
    description = "Criminal record, arrest record, and conviction search"

    JUDYRECORDS_API = "https://www.judyrecords.com/api/search"
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
            self.logger.info("No name available on target, skipping criminal records")
            return results

        state = target.state or ""

        # 1. JudyRecords API search
        results.extend(await self._search_judyrecords(full_name))

        # 2. DuckDuckGo dork searches
        results.extend(await self._search_dorks(full_name, state))

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
                source="criminal_records",
                finding_type="criminal_records_summary",
                title=f"Criminal records summary for {full_name}",
                content=(
                    f"Found {len(case_results)} criminal-record-related result(s) for "
                    f'"{full_name}" across JudyRecords and DuckDuckGo dork searches.'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "total_results": len(case_results),
                },
                confidence=55,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # JudyRecords API
    # ------------------------------------------------------------------

    async def _search_judyrecords(self, full_name: str) -> list[ModuleResult]:
        """Query JudyRecords free API for criminal court records."""
        results: list[ModuleResult] = []

        params = {"query": full_name, "page": "1"}
        url = f"{self.JUDYRECORDS_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"JudyRecords API request failed: {exc}")
            return results

        if not response:
            self.logger.info("JudyRecords API returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse JudyRecords JSON response")
            return results

        records: list[dict[str, Any]] = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = data.get("results", data.get("records", []))

        for record in records[:10]:
            title = record.get("title", record.get("case_name", "Unknown Record"))
            record_url = record.get("url", record.get("link", ""))
            snippet = record.get("snippet", record.get("description", ""))
            jurisdiction = record.get("jurisdiction", record.get("court", ""))
            record_type = record.get("type", record.get("record_type", "court"))

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="judyrecords",
                    finding_type="criminal_record",
                    title=title,
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": record_url,
                        "snippet": snippet,
                        "source": "judyrecords",
                        "record_type": record_type,
                        "jurisdiction": jurisdiction,
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"JudyRecords returned {len(results)} result(s) for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for criminal records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" criminal record' + (f" {state}" if state else ""),
            f'"{full_name}" arrest record',
            f'site:judyrecords.com "{full_name}"',
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

                record_type = self._classify_record_type(title, snippet, url)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="criminal_record",
                        title=title or f"Criminal record result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "record_type": record_type,
                            "jurisdiction": state,
                        },
                        confidence=60,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_record_type(title: str, snippet: str, url: str) -> str:
        """Classify the type of criminal record based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "conviction" in combined:
            return "conviction"
        if "arrest" in combined:
            return "arrest"
        if "charges" in combined or "charged" in combined:
            return "charges"
        return "court"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
