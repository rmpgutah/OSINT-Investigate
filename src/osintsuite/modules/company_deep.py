"""Company deep-dive module — OpenCorporates, SEC EDGAR, and business intelligence."""

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


class CompanyDeepModule(BaseModule):
    name = "company_deep"
    description = "Deep company intelligence — filings, SEC records, profiles"

    OPENCORPORATES_API = "https://api.opencorporates.com/v0.4/companies/search"
    SEC_EDGAR_API = "https://efts.sec.gov/LATEST/search-index"
    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping company deep")
            return results

        # 1. OpenCorporates API
        results.extend(await self._search_opencorporates(name))

        # 2. SEC EDGAR full-text search
        results.extend(await self._search_sec_edgar(name))

        # 3. DuckDuckGo dork searches
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

        capped = deduped[: self.MAX_RESULTS]

        # Summary finding
        capped.append(
            ModuleResult(
                module_name=self.name,
                source="company_deep",
                finding_type="company_summary",
                title=f"Company deep-dive summary for {name}",
                content=(
                    f"Found {len(capped)} company-intelligence result(s) for "
                    f'"{name}" across OpenCorporates, SEC EDGAR, and DuckDuckGo dorks.'
                ),
                data={
                    "name": name,
                    "total_results": len(capped),
                },
                confidence=60,
            )
        )

        return capped

    # ------------------------------------------------------------------
    # OpenCorporates API
    # ------------------------------------------------------------------

    async def _search_opencorporates(self, name: str) -> list[ModuleResult]:
        """Query OpenCorporates free-tier company search."""
        results: list[ModuleResult] = []

        params = {"q": name}
        url = f"{self.OPENCORPORATES_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"OpenCorporates API request failed: {exc}")
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse OpenCorporates JSON response")
            return results

        companies: list[dict[str, Any]] = []
        if isinstance(data, dict):
            api_result = data.get("results", {})
            companies = api_result.get("companies", [])

        for entry in companies[:10]:
            company = entry.get("company", {})
            company_name = company.get("name", "Unknown Company")
            company_number = company.get("company_number", "")
            jurisdiction = company.get("jurisdiction_code", "")
            opencorp_url = company.get("opencorporates_url", "")
            status = company.get("current_status", "")
            incorporation_date = company.get("incorporation_date", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="opencorporates",
                    finding_type="company_filing",
                    title=f"{company_name} ({jurisdiction})",
                    content=(
                        f"Company #{company_number} in {jurisdiction}. "
                        f"Status: {status}. Incorporated: {incorporation_date}."
                    ),
                    data={
                        "name": company_name,
                        "company_number": company_number,
                        "jurisdiction": jurisdiction,
                        "url": opencorp_url,
                        "status": status,
                        "incorporation_date": incorporation_date,
                        "source": "opencorporates",
                    },
                    confidence=70,
                )
            )

        self.logger.info(
            f"OpenCorporates returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # SEC EDGAR full-text search
    # ------------------------------------------------------------------

    async def _search_sec_edgar(self, name: str) -> list[ModuleResult]:
        """Query SEC EDGAR full-text search for filings."""
        results: list[ModuleResult] = []

        params = {
            "q": name,
            "dateRange": "custom",
            "startdt": "2020-01-01",
        }
        url = f"{self.SEC_EDGAR_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(
                url, headers={"User-Agent": "OSINTSuite research@example.com"}
            )
        except Exception as exc:
            self.logger.warning(f"SEC EDGAR request failed: {exc}")
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse SEC EDGAR JSON response")
            return results

        hits: list[dict[str, Any]] = []
        if isinstance(data, dict):
            hits = data.get("hits", data.get("results", []))
            if isinstance(hits, dict):
                hits = hits.get("hits", [])

        for hit in hits[:10]:
            source = hit.get("_source", hit)
            filing_type = source.get("form_type", source.get("type", ""))
            entity_name = source.get("entity_name", source.get("display_names", [""])[0] if isinstance(source.get("display_names"), list) else name)
            filed_date = source.get("file_date", source.get("period_of_report", ""))
            filing_url = source.get("file_url", "")
            if not filing_url and hit.get("_id"):
                filing_url = f"https://www.sec.gov/Archives/{hit['_id']}"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="sec_edgar",
                    finding_type="sec_record",
                    title=f"SEC {filing_type} — {entity_name}",
                    content=f"Filing type {filing_type} dated {filed_date}.",
                    data={
                        "entity_name": entity_name,
                        "filing_type": filing_type,
                        "filed_date": filed_date,
                        "url": filing_url,
                        "source": "sec_edgar",
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"SEC EDGAR returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(self, name: str) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for business intelligence."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{name}" annual report OR 10-K OR SEC filing',
            f'"{name}" BBB rating',
            f'site:crunchbase.com "{name}"',
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

                finding_type = "company_profile"
                if "crunchbase" in url.lower():
                    finding_type = "company_profile"
                elif "sec.gov" in url.lower() or "10-k" in snippet.lower():
                    finding_type = "sec_record"
                elif "bbb" in url.lower() or "bbb" in snippet.lower():
                    finding_type = "company_filing"

                confidence = 65
                if finding_type == "sec_record":
                    confidence = 75
                elif finding_type == "company_filing":
                    confidence = 70

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Company result for {name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                        },
                        confidence=confidence,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} results for '{name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
