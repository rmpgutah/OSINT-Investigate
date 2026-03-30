"""Financial records module — searches for liens, judgments, FINRA records, and financial history."""

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


class FinancialRecordsModule(BaseModule):
    name = "financial_records"
    description = "Financial records, liens, judgments, and FINRA BrokerCheck search"

    FINRA_API = "https://api.brokercheck.finra.org/search/individual"
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
            self.logger.info("No name available on target, skipping financial records")
            return results

        state = target.state or ""

        # 1. FINRA BrokerCheck API
        results.extend(await self._search_finra(full_name))

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
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="financial_records",
                finding_type="financial_summary",
                title=f"Financial records summary for {full_name}",
                content=(
                    f"Found {len(case_results)} financial-record-related result(s) for "
                    f'"{full_name}" across FINRA BrokerCheck and DuckDuckGo dork searches.'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "total_results": len(case_results),
                },
                confidence=50,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # FINRA BrokerCheck API
    # ------------------------------------------------------------------

    async def _search_finra(self, full_name: str) -> list[ModuleResult]:
        """Query FINRA BrokerCheck free API for broker/advisor records."""
        results: list[ModuleResult] = []

        params = {
            "query": full_name,
            "filter": "active=true,prev=true",
        }
        url = f"{self.FINRA_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"FINRA BrokerCheck API request failed: {exc}")
            return results

        if not response:
            self.logger.info("FINRA BrokerCheck API returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse FINRA BrokerCheck JSON response")
            return results

        hits: list[dict[str, Any]] = []
        if isinstance(data, dict):
            hits = data.get("hits", data.get("results", []))
            if isinstance(hits, dict):
                hits = hits.get("hits", [])
        elif isinstance(data, list):
            hits = data

        for hit in hits[:10]:
            source_data = hit.get("_source", hit)
            ind_name = source_data.get("ind_firstname", "")
            ind_last = source_data.get("ind_lastname", "")
            bc_scope = source_data.get("ind_bc_scope", "")
            firm_name = source_data.get("ind_current_employer", "")
            ind_source_id = source_data.get("ind_source_id", "")

            display_name = f"{ind_name} {ind_last}".strip() or full_name
            broker_url = (
                f"https://brokercheck.finra.org/individual/summary/{ind_source_id}"
                if ind_source_id
                else ""
            )

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="finra_brokercheck",
                    finding_type="finra_record",
                    title=f"FINRA: {display_name}",
                    content=(
                        f"Broker: {display_name} | Firm: {firm_name} | "
                        f"Scope: {bc_scope}"
                    ),
                    data={
                        "title": f"FINRA: {display_name}",
                        "url": broker_url,
                        "snippet": f"Firm: {firm_name}, Scope: {bc_scope}",
                        "source": "finra_brokercheck",
                        "record_type": "broker",
                        "amount": "",
                        "jurisdiction": "",
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"FINRA BrokerCheck returned {len(results)} result(s) for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for financial records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" lien OR judgment' + (f" {state}" if state else ""),
            f'"{full_name}" tax lien',
            f'"{full_name}" foreclosure',
            f'site:unicourt.com "{full_name}"',
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

                record_type = self._classify_record_type(title, snippet, url)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=self._finding_type_for(record_type),
                        title=title or f"Financial record result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "record_type": record_type,
                            "amount": "",
                            "jurisdiction": state,
                        },
                        confidence=self._confidence_for(record_type),
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
    def _classify_record_type(title: str, snippet: str, url: str) -> str:
        """Classify the type of financial record based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "lien" in combined:
            return "lien"
        if "judgment" in combined or "judgement" in combined:
            return "judgment"
        if "foreclosure" in combined:
            return "foreclosure"
        if "bankruptcy" in combined:
            return "bankruptcy"
        return "financial"

    @staticmethod
    def _finding_type_for(record_type: str) -> str:
        """Map record type to finding type."""
        if record_type in ("lien", "judgment"):
            return "lien_judgment"
        return "financial_record"

    @staticmethod
    def _confidence_for(record_type: str) -> int:
        """Return confidence based on record type."""
        if record_type in ("lien", "judgment"):
            return 60
        return 55

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
