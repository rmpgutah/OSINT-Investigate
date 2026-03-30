"""Bankruptcy court filings and discharge records search module."""

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


class BankruptcyRecordsModule(BaseModule):
    name = "bankruptcy_records"
    description = "Bankruptcy court filings and discharge records search"

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping bankruptcy search")
            return results

        state = target.state or ""

        # 1. CourtListener API search
        cl_results = await self._search_courtlistener(name)
        results.extend(cl_results)

        # 2. DuckDuckGo dork search
        dork_results = await self._search_dork(name, state)
        results.extend(dork_results)

        # Summary finding
        total_filings = len(
            [r for r in results if r.finding_type == "bankruptcy_filing"]
        )
        results.append(
            ModuleResult(
                module_name=self.name,
                source="courtlistener",
                finding_type="bankruptcy_summary",
                title=f"Bankruptcy records search for {name}",
                content=(
                    f"Found {total_filings} potential bankruptcy filing(s) "
                    f"for '{name}'" + (f" in {state}" if state else "")
                ),
                data={
                    "name": name,
                    "state": state,
                    "total_filings": total_filings,
                    "sources_checked": ["courtlistener", "duckduckgo_dork"],
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # CourtListener API search
    # ------------------------------------------------------------------

    async def _search_courtlistener(self, name: str) -> list[ModuleResult]:
        """Query CourtListener REST API for bankruptcy court records."""
        results: list[ModuleResult] = []

        encoded_name = urllib.parse.quote_plus(name)
        url = (
            f"https://www.courtlistener.com/api/rest/v3/search/"
            f"?q={encoded_name}&type=r&court=bankruptcy"
        )

        try:
            response = await self.fetch(
                url, headers={"Accept": "application/json"}
            )
        except Exception as exc:
            self.logger.warning(f"CourtListener API request failed: {exc}")
            return results

        if not response:
            self.logger.info("CourtListener API returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.info("CourtListener response was not valid JSON")
            return results

        records: list[dict[str, Any]] = []
        if isinstance(data, dict):
            records = data.get("results", [])
        elif isinstance(data, list):
            records = data

        for record in records[:10]:
            case_name = record.get("caseName", record.get("case_name", ""))
            court = record.get("court", record.get("court_id", ""))
            date_filed = record.get("dateFiled", record.get("date_filed", ""))
            docket_number = record.get(
                "docketNumber", record.get("docket_number", "")
            )
            absolute_url = record.get("absolute_url", "")
            detail_url = (
                f"https://www.courtlistener.com{absolute_url}"
                if absolute_url
                else ""
            )

            # Attempt to detect chapter from case name or description
            chapter = ""
            snippet = record.get("snippet", record.get("description", ""))
            for ch in ("7", "11", "13"):
                if f"chapter {ch}" in (case_name + " " + snippet).lower():
                    chapter = ch
                    break

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="courtlistener",
                    finding_type="bankruptcy_filing",
                    title=case_name or f"Bankruptcy filing — {name}",
                    content=snippet or None,
                    data={
                        "case_name": case_name,
                        "court": court,
                        "chapter": chapter,
                        "date_filed": date_filed,
                        "docket_number": docket_number,
                        "url": detail_url,
                    },
                    confidence=70,
                )
            )

        self.logger.info(
            f"CourtListener found {len(results)} bankruptcy results for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork search
    # ------------------------------------------------------------------

    async def _search_dork(self, name: str, state: str) -> list[ModuleResult]:
        """Search DuckDuckGo for bankruptcy filing references."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork search"
            )
            return []

        query = f'"{name}" bankruptcy filing'
        if state:
            query += f" {state}"

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG bankruptcy dork search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:10]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")

            # Detect chapter from snippet/title
            chapter = ""
            combined = (title + " " + snippet).lower()
            for ch in ("7", "11", "13"):
                if f"chapter {ch}" in combined:
                    chapter = ch
                    break

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="bankruptcy_filing",
                    title=title or f"Bankruptcy result for {name}",
                    content=snippet or None,
                    data={
                        "case_name": title,
                        "court": "",
                        "chapter": chapter,
                        "date_filed": "",
                        "url": url,
                    },
                    confidence=55,
                )
            )

        self.logger.info(
            f"DDG dork found {len(results)} bankruptcy results for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # Sync search helper
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
