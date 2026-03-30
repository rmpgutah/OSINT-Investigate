"""Court records module — searches CourtListener and public dockets for litigation history."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class CourtRecordsModule(BaseModule):
    name = "court_records"
    description = "Court records, case dockets, and litigation search"

    COURTLISTENER_API = "https://www.courtlistener.com/api/rest/v3/search/"
    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping court records")
            return results

        # CourtListener API — opinions
        results.extend(await self._search_courtlistener(name, search_type="r"))
        # CourtListener API — dockets
        results.extend(await self._search_courtlistener(name, search_type="d"))
        # DuckDuckGo site-scoped search
        results.extend(await self._duckduckgo_dork(name))

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

        # Enforce max
        case_results = deduped[: self.MAX_RESULTS]

        # Summary
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="court_records",
                finding_type="court_records_summary",
                title=f"Court records summary for {name}",
                content=(
                    f"Found {len(case_results)} court-related result(s) for "
                    f'"{name}" across CourtListener opinions, dockets, '
                    f"and DuckDuckGo site search."
                ),
                data={
                    "name": name,
                    "total_results": len(case_results),
                },
                confidence=60,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # CourtListener REST API
    # ------------------------------------------------------------------

    async def _search_courtlistener(
        self, name: str, search_type: str
    ) -> list[ModuleResult]:
        """Query CourtListener search API.

        search_type: 'r' for opinions, 'd' for dockets.
        """
        params = {
            "q": name,
            "type": search_type,
            "format": "json",
        }
        url = f"{self.COURTLISTENER_API}?{urllib.parse.urlencode(params)}"

        response = await self.fetch(url)
        if not response:
            return []

        try:
            payload = response.json()
        except Exception:
            self.logger.warning("Failed to parse CourtListener JSON response")
            return []

        results: list[ModuleResult] = []
        items = payload.get("results", [])

        for item in items[: self.MAX_RESULTS]:
            case_name = item.get("caseName") or item.get("case_name") or "Unknown Case"
            court = item.get("court") or item.get("court_citation_string") or ""
            date_filed = item.get("dateFiled") or item.get("date_filed") or ""
            docket_number = item.get("docketNumber") or item.get("docket_number") or ""
            absolute_url = item.get("absolute_url") or ""
            result_url = (
                f"https://www.courtlistener.com{absolute_url}"
                if absolute_url
                else ""
            )

            source_label = "courtlistener_opinions" if search_type == "r" else "courtlistener_dockets"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source=source_label,
                    finding_type="court_case",
                    title=case_name,
                    content=(
                        f"{case_name} | Court: {court} | "
                        f"Filed: {date_filed} | Docket: {docket_number}"
                    ),
                    data={
                        "case_name": case_name,
                        "court": court,
                        "date_filed": date_filed,
                        "docket_number": docket_number,
                        "url": result_url,
                    },
                    confidence=70,
                )
            )

        source_desc = "opinions" if search_type == "r" else "dockets"
        self.logger.info(
            f"CourtListener {source_desc} search returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo site-scoped search (optional dependency)
    # ------------------------------------------------------------------

    async def _duckduckgo_dork(self, name: str) -> list[ModuleResult]:
        """Use duckduckgo_search library if available, else fall back to scraping."""
        results: list[ModuleResult] = []
        query = f'"{name}" site:courtlistener.com'

        # Try the duckduckgo_search library first
        try:
            from duckduckgo_search import DDGS  # type: ignore[import-untyped]

            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=10))

            for hit in hits:
                title = hit.get("title", "")
                href = hit.get("href", "")
                body = hit.get("body", "")
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="court_case",
                        title=title,
                        content=body,
                        data={
                            "case_name": title,
                            "court": "",
                            "date_filed": "",
                            "docket_number": "",
                            "url": href,
                        },
                        confidence=45,
                    )
                )

            self.logger.info(
                f"DuckDuckGo dork returned {len(results)} result(s) for '{name}'"
            )
            return results

        except ImportError:
            self.logger.debug(
                "duckduckgo_search not installed; falling back to HTML scrape"
            )
        except Exception as e:
            self.logger.warning(f"duckduckgo_search failed: {e}")

        # Fallback: direct DuckDuckGo HTML scrape
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            response = await self.fetch(url)
            if not response:
                return []

            try:
                from bs4 import BeautifulSoup  # type: ignore[import-untyped]
            except ImportError:
                self.logger.debug("bs4 not available for DDG HTML fallback")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            for link_tag in soup.select("a.result__a"):
                href = link_tag.get("href", "")
                text = link_tag.get_text(strip=True)
                if isinstance(href, str) and "courtlistener.com" in href:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="duckduckgo",
                            finding_type="court_case",
                            title=text,
                            content=text,
                            data={
                                "case_name": text,
                                "court": "",
                                "date_filed": "",
                                "docket_number": "",
                                "url": href,
                            },
                            confidence=40,
                        )
                    )

            self.logger.info(
                f"DuckDuckGo HTML fallback returned {len(results)} result(s) for '{name}'"
            )
        except Exception as e:
            self.logger.warning(f"DuckDuckGo HTML fallback failed: {e}")

        return results
