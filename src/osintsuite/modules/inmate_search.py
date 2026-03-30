"""Inmate search module — searches federal BOP and state inmate databases."""

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


class InmateSearchModule(BaseModule):
    name = "inmate_search"
    description = "Federal BOP and state inmate / incarceration search"

    BOP_SEARCH_URL = "https://www.bop.gov/PublicInfo/execute/inmateloc"
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
            self.logger.info("No name available on target, skipping inmate search")
            return results

        state = target.state or ""

        # 1. Federal BOP inmate lookup
        results.extend(await self._search_bop(full_name))

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
        federal_count = len(
            [r for r in case_results if r.finding_type == "federal_inmate"]
        )
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="inmate_search",
                finding_type="inmate_summary",
                title=f"Inmate search summary for {full_name}",
                content=(
                    f"Found {len(case_results)} inmate-related result(s) for "
                    f'"{full_name}" ({federal_count} federal BOP match(es)).'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "total_results": len(case_results),
                    "federal_count": federal_count,
                },
                confidence=55,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # Federal BOP inmate lookup
    # ------------------------------------------------------------------

    async def _search_bop(self, full_name: str) -> list[ModuleResult]:
        """Query the Federal Bureau of Prisons inmate locator."""
        results: list[ModuleResult] = []

        parts = full_name.split(maxsplit=1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        params = {
            "nameFirst": first_name,
            "nameLast": last_name,
            "output": "json",
        }
        url = f"{self.BOP_SEARCH_URL}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"BOP inmate search request failed: {exc}")
            return results

        if not response:
            self.logger.info("BOP inmate search returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse BOP JSON response")
            return results

        inmates: list[dict[str, Any]] = []
        if isinstance(data, list):
            inmates = data
        elif isinstance(data, dict):
            inmates = data.get("InmateLocator", data.get("inmates", data.get("results", [])))

        for inmate in inmates[:10]:
            name = inmate.get("inmateDisplayName", inmate.get("name", full_name))
            register_num = inmate.get("registerNumber", inmate.get("id", ""))
            facility = inmate.get("facility", inmate.get("institution", ""))
            release_date = inmate.get("projectedReleaseDate", inmate.get("release_date", ""))
            status = inmate.get("status", "")
            detail_url = f"https://www.bop.gov/inmateloc/InmateFinderServlet?Transaction=IDSearch&ESSION=default&ID={register_num}" if register_num else ""

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="bop_federal",
                    finding_type="federal_inmate",
                    title=f"Federal inmate: {name}",
                    content=(
                        f"{name} | Register: {register_num} | "
                        f"Facility: {facility} | Release: {release_date}"
                    ),
                    data={
                        "title": name,
                        "url": detail_url,
                        "snippet": f"Register #{register_num} at {facility}",
                        "source": "bop_federal",
                        "facility": facility,
                        "inmate_id": register_num,
                        "status": status or "federal_custody",
                    },
                    confidence=80,
                )
            )

        self.logger.info(
            f"BOP inmate search returned {len(results)} result(s) for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for inmate records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'site:vinelink.com "{full_name}"',
            f'"{full_name}" inmate',
            f'"{full_name}" prison',
            f'"{full_name}" incarcerated',
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

                facility = self._extract_facility(title, snippet)
                inmate_id = self._extract_inmate_id(title, snippet)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="inmate_record",
                        title=title or f"Inmate result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "facility": facility,
                            "inmate_id": inmate_id,
                            "status": "unknown",
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
    def _extract_facility(title: str, snippet: str) -> str:
        """Attempt to extract facility name from text."""
        combined = f"{title} {snippet}".lower()
        facility_keywords = [
            "correctional", "prison", "penitentiary", "detention",
            "jail", "facility", "institution",
        ]
        for keyword in facility_keywords:
            if keyword in combined:
                return keyword.title()
        return ""

    @staticmethod
    def _extract_inmate_id(title: str, snippet: str) -> str:
        """Attempt to extract inmate ID from text."""
        import re

        combined = f"{title} {snippet}"
        # Match common inmate ID patterns like #12345 or ID: 12345
        match = re.search(r"(?:#|ID[:\s]*|Register[:\s]*)(\d{4,})", combined, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
