"""Trademark and patent module — USPTO, PatentsView, and Google Patents."""

from __future__ import annotations

import asyncio
import json
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


class TrademarkPatentModule(BaseModule):
    name = "trademark_patent"
    description = "Trademark and patent intelligence — USPTO, PatentsView, Google Patents"

    PATENTSVIEW_API = "https://api.patentsview.org/patents/query"
    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping trademark/patent")
            return results

        # 1. PatentsView API (free, no key required)
        results.extend(await self._search_patentsview(name))

        # 2. DuckDuckGo dork searches (USPTO TSDR, Google Patents)
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
                source="trademark_patent",
                finding_type="ip_summary",
                title=f"Trademark/patent summary for {name}",
                content=(
                    f"Found {len(capped)} intellectual-property result(s) for "
                    f'"{name}" across PatentsView, USPTO, and Google Patents.'
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
    # PatentsView API
    # ------------------------------------------------------------------

    async def _search_patentsview(self, name: str) -> list[ModuleResult]:
        """Query the PatentsView free API for patent records."""
        results: list[ModuleResult] = []

        # Try inventor search first using last name
        parts = name.strip().split()
        last_name = parts[-1] if parts else name

        query_obj = {"_contains": {"inventor_last_name": last_name}}
        fields = ["patent_title", "patent_date", "patent_number", "inventor_first_name", "inventor_last_name"]

        params = {
            "q": json.dumps(query_obj),
            "f": json.dumps(fields),
            "o": json.dumps({"per_page": 10}),
        }
        url = f"{self.PATENTSVIEW_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"PatentsView API request failed: {exc}")
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse PatentsView JSON response")
            return results

        patents: list[dict[str, Any]] = []
        if isinstance(data, dict):
            patents = data.get("patents", [])

        for patent in patents[:10]:
            patent_title = patent.get("patent_title", "Unknown Patent")
            patent_number = patent.get("patent_number", "")
            patent_date = patent.get("patent_date", "")
            patent_url = f"https://patents.google.com/patent/US{patent_number}" if patent_number else ""

            inventors = patent.get("inventors", [])
            inventor_names = []
            for inv in inventors[:5]:
                fn = inv.get("inventor_first_name", "")
                ln = inv.get("inventor_last_name", "")
                if fn or ln:
                    inventor_names.append(f"{fn} {ln}".strip())

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="patentsview",
                    finding_type="patent_record",
                    title=f"Patent: {patent_title}",
                    content=(
                        f"US{patent_number} — {patent_title}. "
                        f"Date: {patent_date}. Inventors: {', '.join(inventor_names) or 'N/A'}."
                    ),
                    data={
                        "patent_title": patent_title,
                        "patent_number": patent_number,
                        "patent_date": patent_date,
                        "inventors": inventor_names,
                        "url": patent_url,
                        "source": "patentsview",
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"PatentsView returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(self, name: str) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for trademarks and patents."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'site:tsdr.uspto.gov "{name}"',
            f'site:patents.google.com "{name}"',
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

                if "tsdr.uspto" in url.lower() or "trademark" in title.lower():
                    finding_type = "trademark_record"
                    confidence = 70
                else:
                    finding_type = "patent_record"
                    confidence = 75

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"IP result for {name}",
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
            f"DDG dork searches found {len(all_results)} IP results for '{name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
