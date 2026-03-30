"""Nonprofit organization search: IRS exempt orgs, ProPublica Nonprofit Explorer."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class NonprofitLookupModule(BaseModule):
    name = "nonprofit_lookup"
    description = "Nonprofit organization search: IRS exempt orgs, ProPublica Nonprofit Explorer"

    PROPUBLICA_SEARCH_URL = "https://projects.propublica.org/nonprofits/api/v2/search.json"

    def applicable_target_types(self) -> list[str]:
        return ["organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        name = target.full_name or target.label
        results: list[ModuleResult] = []

        # --- ProPublica Nonprofit API ---
        api_results = await self._search_propublica(name)
        for org in api_results[:10]:
            ein = org.get("ein", "")
            org_name = org.get("name", "")
            city = org.get("city", "")
            state = org.get("state", "")
            ntee_code = org.get("ntee_code", "")
            revenue = org.get("total_revenue", 0)
            assets = org.get("total_assets", 0)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="propublica_nonprofit_explorer",
                    finding_type="nonprofit_record",
                    title=f"Nonprofit: {org_name} (EIN {ein})",
                    content=f"{org_name} — {city}, {state}" if city else org_name,
                    data={
                        "ein": str(ein),
                        "name": org_name,
                        "city": city,
                        "state": state,
                        "ntee_code": ntee_code,
                        "revenue": revenue,
                        "assets": assets,
                        "url": f"https://projects.propublica.org/nonprofits/organizations/{ein}",
                    },
                    confidence=70,
                )
            )

        # --- IRS Exempt Organizations dork ---
        irs_results = await self._search_irs_dork(name)
        irs_count = 0
        seen_urls: set[str] = set()
        for hit in irs_results:
            url = hit.get("href", "")
            if url in seen_urls or irs_count >= 5:
                break
            seen_urls.add(url)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="irs_exempt_organizations",
                    finding_type="nonprofit_record",
                    title=f"IRS Exempt Org: {hit.get('title', '')[:120]}",
                    content=hit.get("body", "")[:500] or None,
                    data={
                        "ein": "",
                        "name": hit.get("title", ""),
                        "city": "",
                        "state": "",
                        "ntee_code": "",
                        "revenue": 0,
                        "assets": 0,
                        "url": url,
                    },
                    confidence=50,
                )
            )
            irs_count += 1

        # --- Summary finding ---
        results.append(
            ModuleResult(
                module_name=self.name,
                source="propublica_nonprofit_explorer",
                finding_type="nonprofit_summary",
                title=f"Nonprofit lookup summary for {name}",
                content=None,
                data={
                    "organization_query": name,
                    "propublica_results": len(api_results[:10]),
                    "irs_dork_results": irs_count,
                    "total_results": len(api_results[:10]) + irs_count,
                },
                confidence=55,
            )
        )

        return results

    # ------------------------------------------------------------------
    # ProPublica API search
    # ------------------------------------------------------------------

    async def _search_propublica(self, name: str) -> list[dict[str, Any]]:
        encoded_name = quote_plus(name)
        url = f"{self.PROPUBLICA_SEARCH_URL}?q={encoded_name}"
        try:
            resp = await self.fetch(url)
            if resp is None:
                return []
            data = resp.json()
            return data.get("organizations", [])
        except Exception as exc:
            self.logger.warning(f"ProPublica API request failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # IRS Exempt Org dork
    # ------------------------------------------------------------------

    async def _search_irs_dork(self, name: str) -> list[dict[str, Any]]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping IRS dork"
            )
            return []

        query = f'"{name}" site:apps.irs.gov "exempt organizations"'
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            return hits
        except Exception as exc:
            self.logger.warning(f"IRS dork search failed: {exc}")
            return []

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=10))
