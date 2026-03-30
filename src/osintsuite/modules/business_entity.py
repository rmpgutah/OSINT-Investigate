"""Business entity module — corporate filings via OpenCorporates and SEC EDGAR."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class BusinessEntityModule(BaseModule):
    name = "business_entity"
    description = "Business entity search: corporate filings, SEC EDGAR, OpenCorporates"

    OPENCORPORATES_API = "https://api.opencorporates.com/v0.4/companies/search"
    SEC_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"

    def applicable_target_types(self) -> list[str]:
        return ["organization", "person"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping business entity search")
            return results

        # OpenCorporates
        results.extend(await self._search_opencorporates(name))
        # SEC EDGAR
        results.extend(await self._search_sec_edgar(name))

        # Summary
        corp_count = sum(1 for r in results if r.finding_type == "corporate_filing")
        sec_count = sum(1 for r in results if r.finding_type == "sec_filing")
        results.append(
            ModuleResult(
                module_name=self.name,
                source="business_entity",
                finding_type="business_entity_summary",
                title=f"Business entity summary for {name}",
                content=(
                    f"Found {corp_count} corporate filing(s) via OpenCorporates "
                    f"and {sec_count} SEC filing(s) via EDGAR for \"{name}\"."
                ),
                data={
                    "name": name,
                    "corporate_filings": corp_count,
                    "sec_filings": sec_count,
                    "total_results": corp_count + sec_count,
                },
                confidence=60,
            )
        )

        return results

    # ------------------------------------------------------------------
    # OpenCorporates
    # ------------------------------------------------------------------

    async def _search_opencorporates(self, name: str) -> list[ModuleResult]:
        """Search OpenCorporates free API for company records."""
        params = {
            "q": name,
            "per_page": "10",
            "format": "json",
        }
        url = f"{self.OPENCORPORATES_API}?{urllib.parse.urlencode(params)}"

        response = await self.fetch(url)
        if not response:
            return []

        try:
            payload = response.json()
        except Exception:
            self.logger.warning("Failed to parse OpenCorporates JSON response")
            return []

        results: list[ModuleResult] = []
        companies = (
            payload.get("results", {}).get("companies", [])
        )

        for entry in companies:
            company = entry.get("company", {})
            company_name = company.get("name", "Unknown Company")
            jurisdiction = company.get("jurisdiction_code", "")
            company_number = company.get("company_number", "")
            status = company.get("current_status") or company.get("status", "")
            incorporation_date = company.get("incorporation_date", "")
            opencorp_url = company.get("opencorporates_url", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="opencorporates",
                    finding_type="corporate_filing",
                    title=company_name,
                    content=(
                        f"{company_name} | Jurisdiction: {jurisdiction} | "
                        f"Number: {company_number} | Status: {status} | "
                        f"Incorporated: {incorporation_date}"
                    ),
                    data={
                        "company_name": company_name,
                        "jurisdiction": jurisdiction,
                        "company_number": company_number,
                        "status": status,
                        "incorporation_date": incorporation_date,
                        "url": opencorp_url,
                    },
                    confidence=75,
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
        """Search SEC EDGAR full-text search index for filings."""
        params = {
            "q": f'"{name}"',
            "dateRange": "custom",
            "startdt": "2020-01-01",
            "enddt": "2026-12-31",
        }
        url = f"{self.SEC_EDGAR_SEARCH}?{urllib.parse.urlencode(params)}"

        # SEC EDGAR requires a User-Agent header identifying the requester
        headers = {
            "User-Agent": "OSINTSuite/1.0 (research tool)",
            "Accept": "application/json",
        }

        response = await self.fetch(url, headers=headers)
        if not response:
            # Fallback: try the EFTS search endpoint used by the EDGAR UI
            return await self._search_sec_edgar_fallback(name)

        try:
            payload = response.json()
        except Exception:
            self.logger.warning("Failed to parse SEC EDGAR JSON response")
            return await self._search_sec_edgar_fallback(name)

        results: list[ModuleResult] = []
        hits = payload.get("hits", {}).get("hits", [])

        for hit in hits[:15]:
            source = hit.get("_source", {})
            entity_name = source.get("entity_name") or source.get("display_names", [""])[0] or name
            filing_type = source.get("file_type") or source.get("form_type", "")
            filed_date = source.get("file_date") or source.get("period_of_report", "")
            file_num = source.get("file_num", "")
            # Build a URL to the filing on SEC.gov
            accession = source.get("accession_no", "").replace("-", "")
            filing_url = ""
            if accession:
                filing_url = (
                    f"https://www.sec.gov/cgi-bin/browse-edgar"
                    f"?action=getcompany&accession={accession}&type=&dateb=&owner=include&count=10"
                )

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="sec_edgar",
                    finding_type="sec_filing",
                    title=f"{entity_name} — {filing_type}" if filing_type else entity_name,
                    content=(
                        f"{entity_name} | Filing: {filing_type} | "
                        f"Filed: {filed_date} | File#: {file_num}"
                    ),
                    data={
                        "entity_name": entity_name,
                        "filing_type": filing_type,
                        "filed_date": filed_date,
                        "url": filing_url,
                    },
                    confidence=80,
                )
            )

        self.logger.info(
            f"SEC EDGAR returned {len(results)} result(s) for '{name}'"
        )
        return results

    async def _search_sec_edgar_fallback(self, name: str) -> list[ModuleResult]:
        """Fallback: use the EFTS full-text search endpoint used by EDGAR UI."""
        params = {
            "q": f'"{name}"',
            "dateRange": "custom",
            "startdt": "2020-01-01",
            "enddt": "2026-12-31",
        }
        url = f"https://efts.sec.gov/LATEST/search-index?{urllib.parse.urlencode(params)}"

        headers = {
            "User-Agent": "OSINTSuite/1.0 (research tool)",
            "Accept": "application/json",
        }

        response = await self.fetch(url, headers=headers)
        if not response:
            return []

        try:
            payload = response.json()
        except Exception:
            self.logger.warning("SEC EDGAR fallback: failed to parse JSON")
            return []

        results: list[ModuleResult] = []
        filings = payload.get("filings", [])

        for filing in filings[:15]:
            entity_name = filing.get("entity_name", name)
            filing_type = filing.get("form_type", "")
            filed_date = filing.get("filed_date", "")
            filing_url = filing.get("filing_href", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="sec_edgar",
                    finding_type="sec_filing",
                    title=f"{entity_name} — {filing_type}" if filing_type else entity_name,
                    content=(
                        f"{entity_name} | Filing: {filing_type} | "
                        f"Filed: {filed_date}"
                    ),
                    data={
                        "entity_name": entity_name,
                        "filing_type": filing_type,
                        "filed_date": filed_date,
                        "url": filing_url,
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"SEC EDGAR fallback returned {len(results)} result(s) for '{name}'"
        )
        return results
