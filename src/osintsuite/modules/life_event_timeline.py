"""Life event timeline module — searches for key life events and builds a chronological timeline."""

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


class LifeEventTimelineModule(BaseModule):
    name = "life_event_timeline"
    description = "Life event timeline — birth, education, marriage, employment, legal, death"

    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping life_event_timeline module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="life_event_timeline",
                    finding_type="life_timeline_summary",
                    title="Life Event Timeline module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping life_event_timeline")
            return []

        state = ""
        if hasattr(target, "state") and target.state:
            state = target.state

        dorks = self._generate_dorks(full_name, state)
        raw_events: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for idx, (query, event_type) in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            for hit in hits:
                url = hit.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title = hit.get("title", "")
                snippet = hit.get("body", "")
                year = self._extract_year(title, snippet)
                date_extracted = self._extract_date(title, snippet)

                raw_events.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "event_type": event_type,
                    "year": year,
                    "date_extracted": date_extracted,
                })

        # Sort chronologically by year (events without year go to end)
        raw_events.sort(key=lambda e: (int(e["year"]) if e["year"].isdigit() else 9999))

        # Build ModuleResult list, capped at MAX_RESULTS
        results: list[ModuleResult] = []
        for event in raw_events[: self.MAX_RESULTS]:
            year_display = event["year"] or "unknown"
            event_type = event["event_type"]

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="life_event",
                    title=f"[{year_display}] {event_type}: {event['title'][:100]}",
                    content=event["snippet"][:500] if event["snippet"] else None,
                    data={
                        "title": event["title"],
                        "url": event["url"],
                        "snippet": event["snippet"],
                        "source": "duckduckgo",
                        "event_type": event_type,
                        "year": event["year"],
                        "date_extracted": event["date_extracted"],
                    },
                    confidence=50,
                )
            )

        # Summary finding
        event_types_found = list({e["event_type"] for e in raw_events if e["event_type"]})
        years_found = sorted({e["year"] for e in raw_events if e["year"].isdigit()})

        results.append(
            ModuleResult(
                module_name=self.name,
                source="life_event_timeline",
                finding_type="life_timeline_summary",
                title=f"Life event timeline for {full_name} ({len(results)} events)",
                content=None,
                data={
                    "full_name": full_name,
                    "total_events": len(results) - 1,
                    "event_types_found": event_types_found,
                    "year_range": f"{years_found[0]}-{years_found[-1]}" if years_found else "",
                    "dorks_run": len(dorks),
                },
                confidence=45,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(full_name: str, state: str) -> list[tuple[str, str]]:
        """Return list of (query, event_type) tuples."""
        location_suffix = f" {state}" if state else ""
        return [
            (f'"{full_name}" born OR birth{location_suffix}', "birth"),
            (f'"{full_name}" married OR wedding', "marriage"),
            (f'"{full_name}" graduated', "education"),
            (f'"{full_name}" hired OR appointed', "employment"),
            (f'"{full_name}" arrested OR charged', "legal"),
            (f'"{full_name}" died OR obituary', "death"),
        ]

    # ------------------------------------------------------------------
    # Date extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_year(title: str, snippet: str) -> str:
        """Extract a 4-digit year from the text."""
        text = title + " " + snippet
        match = re.search(r"\b(18|19|20)\d{2}\b", text)
        return match.group(0) if match else ""

    @staticmethod
    def _extract_date(title: str, snippet: str) -> str:
        """Extract a full date (MM/DD/YYYY or similar) from text if available."""
        text = title + " " + snippet

        # Try MM/DD/YYYY or MM/DD/YY
        match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", text)
        if match:
            return match.group(1)

        # Try Month DD, YYYY
        match = re.search(
            r"\b((?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+\d{1,2},?\s+\d{4})\b",
            text,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)

        # Try YYYY-MM-DD
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if match:
            return match.group(1)

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
