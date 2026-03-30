"""Military records module — searches for veteran records, service history, and military awards."""

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


class MilitaryRecordsModule(BaseModule):
    name = "military_records"
    description = "Military service records, veteran information, and awards"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping military_records module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="military_summary",
                    title="Military Records module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping military_records")
            return []

        dorks = self._generate_dorks(full_name)
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
                branch = self._extract_branch(title, snippet)
                rank = self._extract_rank(title, snippet)
                service_period = self._extract_service_period(title, snippet)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=f"Military result: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo",
                            "branch": branch,
                            "rank": rank,
                            "service_period": service_period,
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
                finding_type="military_summary",
                title=f"Military records search for {full_name} ({total_found} results)",
                content=None,
                data={
                    "full_name": full_name,
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
    def _generate_dorks(full_name: str) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        return [
            (
                f'"{full_name}" veteran OR military OR army OR navy OR marines OR "air force"',
                "military_record",
                55,
            ),
            (
                f'site:valor.militarytimes.com "{full_name}"',
                "military_record",
                55,
            ),
            (
                f'"{full_name}" service record',
                "military_record",
                55,
            ),
            (
                f'"{full_name}" DD-214 OR discharge',
                "military_record",
                55,
            ),
            (
                f'site:aad.archives.gov "{full_name}"',
                "military_record",
                55,
            ),
            (
                f'"{full_name}" medal OR commendation OR "purple heart"',
                "military_award",
                60,
            ),
        ]

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_branch(title: str, snippet: str) -> str:
        """Attempt to identify military branch from result text."""
        text = (title + " " + snippet).lower()
        branches = {
            "army": ["army", "us army", "u.s. army"],
            "navy": ["navy", "us navy", "u.s. navy"],
            "marines": ["marines", "marine corps", "usmc"],
            "air_force": ["air force", "usaf", "u.s. air force"],
            "coast_guard": ["coast guard", "uscg"],
            "space_force": ["space force", "ussf"],
            "national_guard": ["national guard"],
        }
        for branch, keywords in branches.items():
            if any(kw in text for kw in keywords):
                return branch
        return ""

    @staticmethod
    def _extract_rank(title: str, snippet: str) -> str:
        """Attempt to identify military rank from result text."""
        text = (title + " " + snippet).lower()
        ranks = [
            "general", "colonel", "lieutenant colonel", "major",
            "captain", "lieutenant", "sergeant", "corporal",
            "private", "admiral", "commander", "ensign",
            "petty officer", "specialist", "staff sergeant",
        ]
        for rank in ranks:
            if rank in text:
                return rank.title()
        return ""

    @staticmethod
    def _extract_service_period(title: str, snippet: str) -> str:
        """Try to extract service period years from result text."""
        import re

        text = title + " " + snippet
        # Look for year ranges like 1990-1995 or 1990 - 1995
        match = re.search(r"\b(19|20)\d{2}\s*[-–—]\s*(19|20)\d{2}\b", text)
        if match:
            return match.group(0)
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
