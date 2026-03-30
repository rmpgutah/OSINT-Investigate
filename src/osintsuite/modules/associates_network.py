"""Associates network module — searches for known associates, business partners, and co-defendants."""

from __future__ import annotations

import asyncio
import re
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

# Pattern for extracting capitalized name pairs (First Last) from text
_NAME_PATTERN = re.compile(r"\b([A-Z][a-z]{1,20})\s+([A-Z][a-z]{1,20})\b")


class AssociatesNetworkModule(BaseModule):
    name = "associates_network"
    description = "Associate, colleague, and business partner network search"

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
            self.logger.info("No name available on target, skipping associates network")
            return results

        city = target.city or ""

        # DuckDuckGo dork searches
        results.extend(await self._search_dorks(full_name, city))

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
                source="associates_network",
                finding_type="associates_summary",
                title=f"Associates network summary for {full_name}",
                content=(
                    f"Found {len(case_results)} associate-related result(s) for "
                    f'"{full_name}" via DuckDuckGo dork searches.'
                ),
                data={
                    "name": full_name,
                    "city": city,
                    "total_results": len(case_results),
                },
                confidence=45,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, city: str
    ) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for associate connections."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" associate OR colleague OR partner'
            + (f' AND "{city}"' if city else ""),
            f'"{full_name}" co-defendant',
            f'"{full_name}" business partner',
            f'"{full_name}" co-owner OR co-signer',
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

                association_type = self._classify_association(title, snippet, url)
                associated_name = self._extract_associated_name(
                    snippet, full_name
                )

                finding_type = (
                    "business_associate"
                    if association_type in ("business_partner", "co-owner", "co-signer")
                    else "associate_mention"
                )
                confidence = 55 if finding_type == "business_associate" else 50

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Associate result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "association_type": association_type,
                            "associated_name": associated_name,
                        },
                        confidence=confidence,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} associate results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_association(title: str, snippet: str, url: str) -> str:
        """Classify the type of association based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "co-defendant" in combined:
            return "co-defendant"
        if "business partner" in combined:
            return "business_partner"
        if "co-owner" in combined:
            return "co-owner"
        if "co-signer" in combined:
            return "co-signer"
        if "colleague" in combined:
            return "colleague"
        if "partner" in combined:
            return "partner"
        return "associate"

    @staticmethod
    def _extract_associated_name(snippet: str, target_name: str) -> str:
        """Extract potential associated names from snippet text.

        Looks for capitalized word pairs (First Last) that are not the target name.
        """
        if not snippet:
            return ""

        target_parts = set(target_name.lower().split())
        matches = _NAME_PATTERN.findall(snippet)

        for first, last in matches:
            candidate = f"{first} {last}"
            candidate_parts = set(candidate.lower().split())
            # Skip if the candidate overlaps heavily with the target name
            if candidate_parts & target_parts == candidate_parts:
                continue
            # Skip common false positives
            if candidate.lower() in (
                "the court", "united states", "new york", "los angeles",
                "san francisco", "district court", "supreme court",
            ):
                continue
            return candidate

        return ""

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
