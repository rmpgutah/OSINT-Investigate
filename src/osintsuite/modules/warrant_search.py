"""Warrant search module — searches for active warrants via FBI API and DDG dorks."""

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


class WarrantSearchModule(BaseModule):
    name = "warrant_search"
    description = "Active warrant and wanted person search"

    FBI_WANTED_API = "https://api.fbi.gov/wanted/v1/list"
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
            self.logger.info("No name available on target, skipping warrant search")
            return results

        state = target.state or ""

        # 1. FBI Most Wanted API
        results.extend(await self._search_fbi_wanted(full_name))

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
        fbi_hits = len([r for r in case_results if r.finding_type == "fbi_wanted_match"])
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="warrant_search",
                finding_type="warrant_summary",
                title=f"Warrant search summary for {full_name}",
                content=(
                    f"Found {len(case_results)} warrant-related result(s) for "
                    f'"{full_name}" ({fbi_hits} FBI wanted match(es)).'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "total_results": len(case_results),
                    "fbi_hits": fbi_hits,
                },
                confidence=55,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # FBI Most Wanted API
    # ------------------------------------------------------------------

    async def _search_fbi_wanted(self, full_name: str) -> list[ModuleResult]:
        """Query the FBI Most Wanted API (free, no key required)."""
        results: list[ModuleResult] = []

        params = {"title": full_name}
        url = f"{self.FBI_WANTED_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"FBI Wanted API request failed: {exc}")
            return results

        if not response:
            self.logger.info("FBI Wanted API returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse FBI Wanted JSON response")
            return results

        items: list[dict[str, Any]] = data.get("items", [])

        for item in items[:10]:
            title = item.get("title", "Unknown")
            detail_url = item.get("url", "")
            description = item.get("description", "")
            subjects = item.get("subjects", [])
            warning_message = item.get("warning_message", "")
            status = item.get("status", "")
            reward_text = item.get("reward_text", "")

            snippet = description or warning_message or ""
            warrant_type = "federal_wanted"
            if subjects:
                warrant_type = ", ".join(subjects)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="fbi_wanted_api",
                    finding_type="fbi_wanted_match",
                    title=f"FBI Wanted: {title}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": detail_url,
                        "snippet": snippet,
                        "source": "fbi_wanted_api",
                        "warrant_type": warrant_type,
                        "agency": "FBI",
                        "status": status or "wanted",
                    },
                    confidence=85,
                )
            )

        self.logger.info(
            f"FBI Wanted API returned {len(results)} result(s) for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for warrants."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" warrant' + (f" {state}" if state else ""),
            f'"{full_name}" wanted',
            f'site:*.gov warrant "{full_name}"',
            f'"US Marshals" wanted "{full_name}"',
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

                warrant_type = self._classify_warrant_type(title, snippet, url)
                agency = self._detect_agency(title, snippet, url)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="warrant_result",
                        title=title or f"Warrant result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "warrant_type": warrant_type,
                            "agency": agency,
                            "status": "unknown",
                        },
                        confidence=65,
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
    def _classify_warrant_type(title: str, snippet: str, url: str) -> str:
        """Classify warrant type based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "bench warrant" in combined:
            return "bench_warrant"
        if "arrest warrant" in combined:
            return "arrest_warrant"
        if "fugitive" in combined:
            return "fugitive"
        if "most wanted" in combined:
            return "most_wanted"
        return "warrant"

    @staticmethod
    def _detect_agency(title: str, snippet: str, url: str) -> str:
        """Detect the issuing agency from content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "fbi" in combined:
            return "FBI"
        if "marshal" in combined:
            return "US Marshals"
        if "atf" in combined:
            return "ATF"
        if "dea" in combined:
            return "DEA"
        return "unknown"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
