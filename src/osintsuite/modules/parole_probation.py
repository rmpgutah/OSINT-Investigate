"""Parole and probation module — searches offender registries and supervision records."""

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


class ParoleProbationModule(BaseModule):
    name = "parole_probation"
    description = "Parole, probation, and offender registry search"

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
            self.logger.info(
                "No name available on target, skipping parole/probation search"
            )
            return results

        state = target.state or ""

        # 1. Parole and probation DDG dork searches
        results.extend(await self._search_parole_dorks(full_name, state))

        # 2. Offender registry DDG dork searches (NSOPW, state registries)
        results.extend(await self._search_offender_registry(full_name, state))

        # 3. State DOC offender search via DDG dork
        results.extend(await self._search_state_doc_offender(full_name, state))

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
        parole_count = len(
            [r for r in case_results if r.finding_type == "parole_record"]
        )
        probation_count = len(
            [r for r in case_results if r.finding_type == "probation_record"]
        )
        registry_count = len(
            [r for r in case_results if r.finding_type == "offender_registry"]
        )
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="parole_probation",
                finding_type="parole_summary",
                title=f"Parole/probation summary for {full_name}",
                content=(
                    f"Found {len(case_results)} parole/probation result(s) for "
                    f'"{full_name}" ({parole_count} parole, {probation_count} probation, '
                    f"{registry_count} registry)."
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "total_results": len(case_results),
                    "parole_count": parole_count,
                    "probation_count": probation_count,
                    "registry_count": registry_count,
                },
                confidence=50,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # Parole / probation DDG dork searches
    # ------------------------------------------------------------------

    async def _search_parole_dorks(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Search DuckDuckGo for parole and probation records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping parole dork searches"
            )
            return []

        queries = [
            f'"{full_name}" parole' + (f" {state}" if state else ""),
            f'"{full_name}" probation',
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

                supervision_type = self._classify_supervision_type(
                    title, snippet, url
                )
                finding_type = (
                    "probation_record"
                    if supervision_type == "probation"
                    else "parole_record"
                )

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Parole/probation result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "supervision_type": supervision_type,
                            "jurisdiction": state,
                            "status": "unknown",
                        },
                        confidence=60,
                    )
                )

        self.logger.info(
            f"Parole dork searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Offender registry DDG dork searches
    # ------------------------------------------------------------------

    async def _search_offender_registry(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Search DuckDuckGo for offender registry entries (NSOPW, state)."""
        if not _HAS_DDGS:
            return []

        queries = [
            f'site:nsopw.gov "{full_name}"',
            f'site:*.gov offender "{full_name}"' + (f" {state}" if state else ""),
        ]

        all_results: list[ModuleResult] = []

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG registry search failed for '{query}': {exc}")
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="offender_registry",
                        title=title or f"Offender registry result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "supervision_type": "registry",
                            "jurisdiction": state,
                            "status": "registered",
                        },
                        confidence=65,
                    )
                )

        self.logger.info(
            f"Offender registry searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # State DOC offender search via DDG
    # ------------------------------------------------------------------

    async def _search_state_doc_offender(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Search state DOC offender search portals via DuckDuckGo dork."""
        if not _HAS_DDGS:
            return []

        query = f'site:*.gov "offender search" "{full_name}"'
        if state:
            query += f" {state}"

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG state DOC offender search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:5]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")

            supervision_type = self._classify_supervision_type(title, snippet, url)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="parole_record",
                    title=title or f"DOC offender result for {full_name}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "state_doc_dork",
                        "supervision_type": supervision_type,
                        "jurisdiction": state,
                        "status": "unknown",
                    },
                    confidence=60,
                )
            )

        self.logger.info(
            f"State DOC offender dork found {len(results)} results for '{full_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_supervision_type(title: str, snippet: str, url: str) -> str:
        """Classify supervision type based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "probation" in combined:
            return "probation"
        if "parole" in combined:
            return "parole"
        if "supervised release" in combined:
            return "supervised_release"
        if "sex offender" in combined or "nsopw" in combined:
            return "registry"
        return "parole"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
