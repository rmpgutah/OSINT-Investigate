"""Voter registration record search module."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class VoterRegistrationModule(BaseModule):
    name = "voter_registration"
    description = "Voter registration record and party affiliation search"

    MAX_RESULTS = 15

    # Regex patterns for extracting party affiliation from text
    _PARTY_PATTERNS = [
        re.compile(r"\b(democrat(?:ic)?)\b", re.IGNORECASE),
        re.compile(r"\b(republican)\b", re.IGNORECASE),
        re.compile(r"\b(independent)\b", re.IGNORECASE),
        re.compile(r"\b(libertarian)\b", re.IGNORECASE),
        re.compile(r"\b(green\s*party)\b", re.IGNORECASE),
        re.compile(r"\b(no\s*party\s*(?:affiliation|preference))\b", re.IGNORECASE),
    ]

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping voter registration")
            return results

        state = target.state or ""
        city = target.city or ""

        # DuckDuckGo dork searches
        results.extend(await self._search_dorks(full_name, state, city))

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

        trimmed = deduped[: self.MAX_RESULTS]

        # Summary finding
        trimmed.append(
            ModuleResult(
                module_name=self.name,
                source="voter_registration",
                finding_type="voter_summary",
                title=f"Voter registration summary for {full_name}",
                content=(
                    f"Found {len(trimmed)} voter-registration-related result(s) for "
                    f'"{full_name}" across DuckDuckGo dork searches.'
                ),
                data={
                    "name": full_name,
                    "state": state,
                    "city": city,
                    "total_results": len(trimmed),
                },
                confidence=50,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(
        self, full_name: str, state: str, city: str
    ) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for voter registration records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" voter registration' + (f" {state}" if state else ""),
            f'"{full_name}" registered voter' + (f" {city}" if city else ""),
            f'site:voterrecords.com "{full_name}"',
            f'"{full_name}" party registration' + (f" {state}" if state else ""),
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

                combined_text = f"{title} {snippet}"
                party = self._extract_party(combined_text)
                registration_status = self._extract_status(combined_text)

                finding_type = (
                    "voter_record"
                    if "voterrecords.com" in url or "voter" in title.lower()
                    else "voter_mention"
                )

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Voter registration result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "party_affiliation": party,
                            "registration_status": registration_status,
                        },
                        confidence=60 if finding_type == "voter_record" else 50,
                    )
                )

        self.logger.info(
            f"DDG voter searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_party(self, text: str) -> str:
        """Attempt to extract party affiliation from text."""
        for pattern in self._PARTY_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1).strip().title()
        return ""

    @staticmethod
    def _extract_status(text: str) -> str:
        """Attempt to extract registration status from text."""
        lower = text.lower()
        if "active" in lower:
            return "active"
        if "inactive" in lower:
            return "inactive"
        if "cancelled" in lower or "canceled" in lower:
            return "cancelled"
        if "purged" in lower:
            return "purged"
        return ""

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
