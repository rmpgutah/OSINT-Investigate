"""Genealogy records module — searches FamilySearch, Find A Grave, and public family tree databases."""

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


class GenealogyRecordsModule(BaseModule):
    name = "genealogy_records"
    description = "Genealogy records, grave sites, and family tree search"

    FAMILYSEARCH_API = "https://api.familysearch.org/platform/search/persons"
    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping genealogy_records module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="genealogy_records",
                    finding_type="genealogy_summary",
                    title="Genealogy Records module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping genealogy_records")
            return []

        results: list[ModuleResult] = []
        seen_urls: set[str] = set()

        # FamilySearch API lookup
        results.extend(await self._search_familysearch(full_name))

        # DuckDuckGo dork searches
        dorks = self._generate_dorks(full_name, target)
        total_found = len(results)

        for idx, (query, finding_type, confidence) in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            for hit in hits:
                url = hit.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if total_found >= self.MAX_RESULTS:
                    break

                title = hit.get("title", "")
                snippet = hit.get("body", "")
                birth_year = self._extract_year(title, snippet, "birth")
                death_year = self._extract_year(title, snippet, "death")
                location = self._extract_location(title, snippet)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=f"Genealogy result: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo",
                            "record_type": finding_type,
                            "birth_year": birth_year,
                            "death_year": death_year,
                            "location": location,
                        },
                        confidence=confidence,
                    )
                )
                total_found += 1

            if total_found >= self.MAX_RESULTS:
                break

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="genealogy_records",
                finding_type="genealogy_summary",
                title=f"Genealogy records search for {full_name} ({total_found} results)",
                content=None,
                data={
                    "full_name": full_name,
                    "total_results": total_found,
                    "dorks_run": len(dorks),
                },
                confidence=45,
            )
        )

        return results

    # ------------------------------------------------------------------
    # FamilySearch API
    # ------------------------------------------------------------------

    async def _search_familysearch(self, name: str) -> list[ModuleResult]:
        """Query FamilySearch free API for person records."""
        results: list[ModuleResult] = []
        params = {"q.name": name, "count": "10"}
        url = f"{self.FAMILYSEARCH_API}?{urllib.parse.urlencode(params)}"

        response = await self.fetch(
            url, headers={"Accept": "application/json"}
        )
        if not response:
            return results

        try:
            payload = response.json()
        except Exception:
            self.logger.warning("Failed to parse FamilySearch JSON response")
            return results

        entries = payload.get("searchResults", []) or payload.get("entries", [])
        for entry in entries[:10]:
            content_data = entry.get("content", {}) or entry.get("score", {})
            gedcomx = content_data.get("gedcomx", {}) if isinstance(content_data, dict) else {}
            persons = gedcomx.get("persons", []) if gedcomx else []

            display_name = ""
            birth_year = ""
            death_year = ""
            person_id = entry.get("id", "")

            if persons:
                person = persons[0]
                display_info = person.get("display", {})
                display_name = display_info.get("name", "")
                birth_year = display_info.get("birthDate", "")
                death_year = display_info.get("deathDate", "")

            record_url = f"https://www.familysearch.org/tree/person/{person_id}" if person_id else ""

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="familysearch_api",
                    finding_type="genealogy_record",
                    title=f"FamilySearch: {display_name or name}",
                    content=f"{display_name} | Born: {birth_year} | Died: {death_year}",
                    data={
                        "title": display_name or name,
                        "url": record_url,
                        "snippet": f"Born: {birth_year}, Died: {death_year}",
                        "source": "familysearch_api",
                        "record_type": "genealogy_record",
                        "birth_year": birth_year,
                        "death_year": death_year,
                        "location": "",
                    },
                    confidence=55,
                )
            )

        self.logger.info(
            f"FamilySearch API returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(full_name: str, target: Any) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        state = ""
        if hasattr(target, "state") and target.state:
            state = target.state

        dorks = [
            (f'site:familysearch.org "{full_name}"', "genealogy_record", 55),
            (f'site:findagrave.com "{full_name}"', "grave_record", 65),
            (f'site:ancestry.com "{full_name}"', "genealogy_record", 55),
            (
                f'"{full_name}" genealogy OR "family tree"',
                "family_tree",
                50,
            ),
        ]

        if state:
            dorks.append(
                (f'site:findagrave.com "{full_name}" {state}', "grave_record", 65)
            )

        return dorks

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_year(title: str, snippet: str, context: str = "") -> str:
        """Try to extract a year near a birth/death keyword."""
        import re

        text = title + " " + snippet
        if context == "birth":
            match = re.search(
                r"(?:born|birth|b\.)\s*:?\s*(\b(?:19|20|18)\d{2}\b)", text, re.IGNORECASE
            )
            if match:
                return match.group(1)
        elif context == "death":
            match = re.search(
                r"(?:died|death|d\.)\s*:?\s*(\b(?:19|20|18)\d{2}\b)", text, re.IGNORECASE
            )
            if match:
                return match.group(1)

        match = re.search(r"\b(18|19|20)\d{2}\b", text)
        return match.group(0) if match else ""

    @staticmethod
    def _extract_location(title: str, snippet: str) -> str:
        """Try to extract a location from result text."""
        import re

        text = title + " " + snippet
        us_states = [
            "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
            "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
            "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
            "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
            "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
            "New Hampshire", "New Jersey", "New Mexico", "New York",
            "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
            "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
            "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
            "West Virginia", "Wisconsin", "Wyoming",
        ]
        for state in us_states:
            if state.lower() in text.lower():
                return state
        # Try city, state pattern
        match = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s*([A-Z]{2})\b", text)
        if match:
            return f"{match.group(1)}, {match.group(2)}"
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
