"""Lawsuit and litigation search module."""

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


class LawsuitSearchModule(BaseModule):
    name = "lawsuit_search"
    description = "Lawsuit, litigation, and court docket search"

    COURTLISTENER_API = "https://www.courtlistener.com/api/rest/v3/dockets/"
    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping lawsuit search")
            return results

        # 1. CourtListener docket API
        results.extend(await self._search_courtlistener(name))

        # 2. DuckDuckGo dork searches
        results.extend(await self._search_dorks(name))

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
                source="lawsuit_search",
                finding_type="lawsuit_summary",
                title=f"Lawsuit search summary for {name}",
                content=(
                    f"Found {len(trimmed)} lawsuit-related result(s) for "
                    f'"{name}" across CourtListener and DuckDuckGo dork searches.'
                ),
                data={
                    "name": name,
                    "total_results": len(trimmed),
                },
                confidence=55,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # CourtListener API
    # ------------------------------------------------------------------

    async def _search_courtlistener(self, name: str) -> list[ModuleResult]:
        """Query CourtListener free docket API."""
        results: list[ModuleResult] = []

        params = {"q": name, "format": "json"}
        url = f"{self.COURTLISTENER_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"CourtListener API request failed: {exc}")
            return results

        if not response:
            self.logger.info("CourtListener API returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse CourtListener JSON response")
            return results

        records: list[dict[str, Any]] = []
        if isinstance(data, dict):
            records = data.get("results", [])
        elif isinstance(data, list):
            records = data

        for record in records[:10]:
            case_name = record.get("case_name", record.get("caseName", "Unknown Case"))
            docket_url = record.get(
                "absolute_url",
                record.get("resource_uri", ""),
            )
            if docket_url and not docket_url.startswith("http"):
                docket_url = f"https://www.courtlistener.com{docket_url}"
            court = record.get("court", record.get("court_id", ""))
            date_filed = record.get("date_filed", "")
            nature_of_suit = record.get("nature_of_suit", "")

            case_type = self._classify_case_type(case_name, nature_of_suit)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="courtlistener",
                    finding_type="docket_entry",
                    title=case_name,
                    content=f"Court: {court} | Filed: {date_filed}" if date_filed else None,
                    data={
                        "title": case_name,
                        "url": docket_url,
                        "source": "courtlistener",
                        "court": court,
                        "date_filed": date_filed,
                        "nature_of_suit": nature_of_suit,
                        "case_type": case_type,
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"CourtListener returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(self, name: str) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for lawsuit records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{name}" plaintiff OR defendant',
            f'"{name}" lawsuit OR litigation OR sued',
            f'site:pacermonitor.com "{name}"',
            f'site:docketbird.com "{name}"',
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

                case_type = self._classify_case_type(title, snippet)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="lawsuit_record",
                        title=title or f"Lawsuit result for {name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "case_type": case_type,
                        },
                        confidence=65,
                    )
                )

        self.logger.info(
            f"DDG lawsuit searches found {len(all_results)} results for '{name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_case_type(title: str, description: str) -> str:
        """Classify the case type based on content clues."""
        combined = f"{title} {description}".lower()
        if "bankruptcy" in combined:
            return "bankruptcy"
        if "criminal" in combined:
            return "criminal"
        if "family" in combined or "divorce" in combined or "custody" in combined:
            return "family"
        if any(
            kw in combined
            for kw in ("civil", "plaintiff", "defendant", "lawsuit", "sued")
        ):
            return "civil"
        return "unknown"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
