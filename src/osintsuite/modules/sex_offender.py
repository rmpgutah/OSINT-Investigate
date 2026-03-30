"""National Sex Offender Public Website (NSOPW) registry search module."""

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


class SexOffenderModule(BaseModule):
    name = "sex_offender"
    description = "National Sex Offender Public Website (NSOPW) registry search"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        if not target.full_name:
            self.logger.info("No full_name on target, skipping sex offender search")
            return results

        full_name = target.full_name
        state = target.state or ""

        # 1. Try NSOPW API (may not be publicly accessible)
        api_results = await self._search_nsopw_api(full_name, state)
        results.extend(api_results)

        # 2. DuckDuckGo dork search
        dork_results = await self._search_dork(full_name, state)
        results.extend(dork_results)

        # Summary finding
        total_hits = len([r for r in results if r.finding_type == "sex_offender_result"])
        results.append(
            ModuleResult(
                module_name=self.name,
                source="nsopw",
                finding_type="sex_offender_summary",
                title=f"Sex offender registry search for {full_name}",
                content=(
                    f"Found {total_hits} potential registry hit(s) for "
                    f"'{full_name}'" + (f" in {state}" if state else "")
                ),
                data={
                    "full_name": full_name,
                    "state": state,
                    "total_hits": total_hits,
                    "sources_checked": ["nsopw_api", "duckduckgo_dork"],
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # NSOPW API search
    # ------------------------------------------------------------------

    async def _search_nsopw_api(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Attempt to query the NSOPW API for registry matches."""
        results: list[ModuleResult] = []

        parts = full_name.split(maxsplit=1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        params: dict[str, str] = {
            "FirstName": first_name,
            "LastName": last_name,
        }
        if state:
            params["State"] = state

        url = "https://www.nsopw.gov/api/Search"
        try:
            response = await self.fetch(
                url, params=params, headers={"Accept": "application/json"}
            )
        except Exception as exc:
            self.logger.warning(f"NSOPW API request failed: {exc}")
            return results

        if not response:
            self.logger.info("NSOPW API returned no response (may be unavailable)")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.info("NSOPW API response was not valid JSON")
            return results

        # Handle response — structure may vary
        offenders: list[dict[str, Any]] = []
        if isinstance(data, list):
            offenders = data
        elif isinstance(data, dict):
            offenders = data.get("results", data.get("offenders", []))

        for offender in offenders[:10]:
            name = offender.get("name", offender.get("Name", full_name))
            detail_url = offender.get("url", offender.get("URL", ""))
            jurisdiction = offender.get(
                "jurisdiction", offender.get("Jurisdiction", "")
            )

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="nsopw_api",
                    finding_type="sex_offender_result",
                    title=f"Registry match: {name}",
                    content=detail_url or None,
                    data={
                        "title": name,
                        "url": detail_url,
                        "snippet": jurisdiction,
                        "source": "nsopw_api",
                    },
                    confidence=65,
                )
            )

        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork search
    # ------------------------------------------------------------------

    async def _search_dork(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Search DuckDuckGo for sex offender registry results."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork search"
            )
            return []

        query = (
            f'"{full_name}" site:nsopw.gov OR site:meganslaw.ca.gov '
            f'OR "sex offender" "{full_name}"'
        )
        if state:
            query += f" {state}"

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG dork search failed: {exc}")
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
                    finding_type="sex_offender_result",
                    title=title or f"Registry result for {full_name}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "duckduckgo_dork",
                    },
                    confidence=65,
                )
            )

        self.logger.info(
            f"DDG dork found {len(results)} results for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # Sync search helper
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
