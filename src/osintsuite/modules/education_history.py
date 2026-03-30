"""Education history module — searches for academic records, alumni info, and dissertations."""

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


class EducationHistoryModule(BaseModule):
    name = "education_history"
    description = "Education history, alumni records, and academic publications"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping education_history module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="education_summary",
                    title="Education History module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping education_history")
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
                institution = self._extract_institution(title, snippet)
                degree_type = self._guess_degree_type(title, snippet)
                year = self._extract_year(title, snippet)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=f"Education result: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo",
                            "institution": institution,
                            "degree_type": degree_type,
                            "year": year,
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
                finding_type="education_summary",
                title=f"Education history search for {full_name} ({total_found} results)",
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
            (f'"{full_name}" alumni', "education_record", 55),
            (f'"{full_name}" graduated', "education_record", 55),
            (
                f'"{full_name}" university OR college',
                "education_record",
                55,
            ),
            (
                f'site:linkedin.com/in "{full_name}" education',
                "education_record",
                55,
            ),
            (
                f'site:ratemyprofessors.com "{full_name}"',
                "academic_publication",
                65,
            ),
            (
                f'"{full_name}" thesis OR dissertation',
                "academic_publication",
                65,
            ),
        ]

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_institution(title: str, snippet: str) -> str:
        """Attempt to identify an institution name from result text."""
        text = (title + " " + snippet).lower()
        keywords = [
            "university", "college", "institute", "school",
            "academy", "polytechnic", "seminary",
        ]
        for kw in keywords:
            if kw in text:
                return kw.title()
        return ""

    @staticmethod
    def _guess_degree_type(title: str, snippet: str) -> str:
        text = (title + " " + snippet).lower()
        mapping = {
            "phd": ["ph.d", "phd", "doctorate", "doctoral"],
            "masters": ["master's", "masters", "m.s.", "m.a.", "mba", "m.b.a."],
            "bachelors": ["bachelor's", "bachelors", "b.s.", "b.a.", "undergraduate"],
            "associate": ["associate's", "associates", "a.s.", "a.a."],
            "high_school": ["high school", "diploma", "ged"],
        }
        for degree, keywords in mapping.items():
            if any(kw in text for kw in keywords):
                return degree
        return ""

    @staticmethod
    def _extract_year(title: str, snippet: str) -> str:
        """Try to extract a 4-digit year from result text."""
        import re

        text = title + " " + snippet
        match = re.search(r"\b(19|20)\d{2}\b", text)
        return match.group(0) if match else ""

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
