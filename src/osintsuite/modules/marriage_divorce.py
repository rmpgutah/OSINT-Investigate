"""Marriage and divorce records module — searches for vital records, wedding announcements, and family history."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class MarriageDivorceModule(BaseModule):
    name = "marriage_divorce"
    description = "Marriage and divorce records, wedding announcements, and vital records"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping marriage_divorce module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="vital_record_summary",
                    title="Marriage/Divorce Records module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping marriage_divorce")
            return []

        state = target.state or ""

        dorks = self._generate_dorks(full_name, state)
        results: list[ModuleResult] = []
        seen_urls: set[str] = set()
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
                record_type = self._classify_record_type(title, snippet)
                jurisdiction = self._extract_jurisdiction(title, snippet, state)
                date_found = self._extract_date(title, snippet)
                spouse_name = self._extract_spouse_name(title, snippet, full_name)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=f"Vital record result: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo",
                            "record_type": record_type,
                            "jurisdiction": jurisdiction,
                            "date_found": date_found,
                            "spouse_name": spouse_name,
                        },
                        confidence=confidence,
                    )
                )
                total_found += 1

            if total_found >= 15:
                break

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="duckduckgo",
                finding_type="vital_record_summary",
                title=f"Marriage/divorce records search for {full_name} ({total_found} results)",
                content=None,
                data={
                    "full_name": full_name,
                    "state": state,
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
    def _generate_dorks(full_name: str, state: str) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        state_part = f" {state}" if state else ""
        return [
            (
                f'"{full_name}" marriage record{state_part}',
                "marriage_record",
                55,
            ),
            (
                f'"{full_name}" divorce{state_part}',
                "divorce_record",
                55,
            ),
            (
                f'"{full_name}" wedding announcement',
                "marriage_record",
                55,
            ),
            (
                f'site:ancestry.com "{full_name}" marriage',
                "marriage_record",
                55,
            ),
            (
                f'site:familysearch.org "{full_name}" marriage',
                "marriage_record",
                55,
            ),
        ]

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_record_type(title: str, snippet: str) -> str:
        """Classify whether this is a marriage or divorce record."""
        text = (title + " " + snippet).lower()
        if any(kw in text for kw in ["divorce", "dissolution", "separated"]):
            return "divorce"
        if any(kw in text for kw in ["marriage", "wedding", "married", "bride", "groom", "nuptial"]):
            return "marriage"
        return "unknown"

    @staticmethod
    def _extract_jurisdiction(title: str, snippet: str, state: str) -> str:
        """Attempt to extract jurisdiction from result text."""
        if state:
            return state
        text = (title + " " + snippet).lower()
        # Check for common state references or county mentions
        if "county" in text:
            return "county_referenced"
        return ""

    @staticmethod
    def _extract_date(title: str, snippet: str) -> str:
        """Try to extract a date from result text."""
        import re

        text = title + " " + snippet
        # Look for dates in various formats
        match = re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
            text,
        )
        if match:
            return match.group(0)
        # Fallback: just a year
        match = re.search(r"\b(19|20)\d{2}\b", text)
        return match.group(0) if match else ""

    @staticmethod
    def _extract_spouse_name(title: str, snippet: str, full_name: str) -> str:
        """Attempt to extract a spouse name from snippet if possible."""
        import re

        text = title + " " + snippet
        # Look for patterns like "John Smith and Jane Doe" or "John Smith married Jane Doe"
        name_lower = full_name.lower()
        patterns = [
            rf"(?i){re.escape(full_name)}\s+(?:and|married|wed)\s+([A-Z][a-z]+\s+[A-Z][a-z]+)",
            rf"(?i)([A-Z][a-z]+\s+[A-Z][a-z]+)\s+(?:and|married|wed)\s+{re.escape(full_name)}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1)
                if candidate.lower() != name_lower:
                    return candidate
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
