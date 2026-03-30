"""Employment history module — searches for career records, corporate officer roles, and job announcements."""

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


class EmploymentHistoryModule(BaseModule):
    name = "employment_history"
    description = "Employment history, corporate officer roles, and career records"

    OPENCORPORATES_API = "https://api.opencorporates.com/v0.4/officers/search"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping employment_history module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="employment_summary",
                    title="Employment History module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping employment_history")
            return []

        city = target.city or ""

        results: list[ModuleResult] = []
        seen_urls: set[str] = set()

        # DDG dork searches
        dorks = self._generate_dorks(full_name, city)
        total_found = 0

        for idx, (query, finding_type, confidence) in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            for hit in hits:
                url = hit.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if total_found >= 15:
                    break

                title = hit.get("title", "")
                snippet = hit.get("body", "")
                company = self._extract_company(title, snippet)
                position = self._extract_position(title, snippet)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=f"Employment result: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo",
                            "company": company,
                            "position": position,
                            "date_found": "",
                        },
                        confidence=confidence,
                    )
                )
                total_found += 1

            if total_found >= 15:
                break

        # OpenCorporates officer search
        oc_results = await self._search_opencorporates(full_name)
        for r in oc_results:
            url = r.data.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            results.append(r)
            total_found += 1

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="employment_history",
                finding_type="employment_summary",
                title=f"Employment history search for {full_name} ({total_found} results)",
                content=None,
                data={
                    "full_name": full_name,
                    "city": city,
                    "total_results": total_found,
                    "dorks_run": len(dorks),
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(full_name: str, city: str) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        dorks: list[tuple[str, str, int]] = []
        city_part = f" {city}" if city else ""
        dorks.append(
            (
                f'"{full_name}"{city_part} employee OR manager OR director OR CEO',
                "employment_record",
                55,
            )
        )
        dorks.append(
            (
                f'site:linkedin.com/in "{full_name}"',
                "employment_record",
                55,
            )
        )
        dorks.append(
            (
                f'site:bloomberg.com/profile "{full_name}"',
                "employment_record",
                55,
            )
        )
        dorks.append(
            (
                f'"{full_name}" hired OR appointed',
                "employment_record",
                55,
            )
        )
        return dorks

    # ------------------------------------------------------------------
    # OpenCorporates API
    # ------------------------------------------------------------------

    async def _search_opencorporates(self, name: str) -> list[ModuleResult]:
        """Query OpenCorporates free-tier officer search."""
        results: list[ModuleResult] = []
        params = {"q": name, "format": "json"}
        url = f"{self.OPENCORPORATES_API}?{urllib.parse.urlencode(params)}"

        response = await self.fetch(url)
        if not response:
            return results

        try:
            payload = response.json()
        except Exception:
            self.logger.warning("Failed to parse OpenCorporates JSON response")
            return results

        api_results = (
            payload.get("results", {}).get("officers", [])
        )

        for item in api_results[:10]:
            officer = item.get("officer", {})
            officer_name = officer.get("name", "")
            position = officer.get("position", "")
            company_obj = officer.get("company", {})
            company_name = company_obj.get("name", "")
            company_url = company_obj.get("opencorporates_url", "")
            start_date = officer.get("start_date", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="opencorporates",
                    finding_type="corporate_officer",
                    title=f"{officer_name} — {position} at {company_name}",
                    content=(
                        f"{officer_name} | Position: {position} | "
                        f"Company: {company_name} | Start: {start_date}"
                    ),
                    data={
                        "title": f"{officer_name} — {position} at {company_name}",
                        "url": company_url,
                        "snippet": f"{position} at {company_name}",
                        "source": "opencorporates",
                        "company": company_name,
                        "position": position,
                        "date_found": start_date,
                    },
                    confidence=70,
                )
            )

        self.logger.info(
            f"OpenCorporates officer search returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_company(title: str, snippet: str) -> str:
        """Attempt to identify a company name from result text."""
        text = (title + " " + snippet).lower()
        corp_indicators = ["inc", "llc", "corp", "ltd", "co.", "company", "group"]
        for indicator in corp_indicators:
            if indicator in text:
                return indicator.upper()
        return ""

    @staticmethod
    def _extract_position(title: str, snippet: str) -> str:
        text = (title + " " + snippet).lower()
        positions = [
            "ceo", "cto", "cfo", "coo", "president", "vice president",
            "director", "manager", "engineer", "analyst", "consultant",
            "officer", "partner", "founder",
        ]
        for pos in positions:
            if pos in text:
                return pos.title()
        return ""

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _search(self, query: str) -> list[dict[str, Any]]:
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            return hits
        except Exception as exc:
            self.logger.warning(f"Search failed for dork '{query}': {exc}")
            return []

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=10))
