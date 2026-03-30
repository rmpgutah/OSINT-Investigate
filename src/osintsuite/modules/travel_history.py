"""Travel history module — searches for travel mentions, FAA pilot certs, and travel reviews."""

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


class TravelHistoryModule(BaseModule):
    name = "travel_history"
    description = "Travel history, FAA pilot certifications, and travel review search"

    FAA_AIRMEN_API = "https://api.faa.gov/services/certification/airmen/v1/"
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
            self.logger.info("No name available on target, skipping travel history")
            return results

        # Parse first/last for FAA API
        name_parts = full_name.strip().split()
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[-1] if len(name_parts) > 1 else ""

        # 1. FAA Airmen Certification API
        if first_name and last_name:
            results.extend(await self._search_faa(first_name, last_name))

        # 2. DuckDuckGo dork searches
        results.extend(await self._search_dorks(full_name))

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
                source="travel_history",
                finding_type="travel_summary",
                title=f"Travel history summary for {full_name}",
                content=(
                    f"Found {len(case_results)} travel-related result(s) for "
                    f'"{full_name}" across FAA Airmen database and DuckDuckGo dork searches.'
                ),
                data={
                    "name": full_name,
                    "total_results": len(case_results),
                },
                confidence=45,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # FAA Airmen Certification API
    # ------------------------------------------------------------------

    async def _search_faa(
        self, first_name: str, last_name: str
    ) -> list[ModuleResult]:
        """Query the FAA Airmen Certification free API."""
        results: list[ModuleResult] = []

        params = {
            "firstName": first_name,
            "lastName": last_name,
        }
        url = f"{self.FAA_AIRMEN_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"FAA Airmen API request failed: {exc}")
            return results

        if not response:
            self.logger.info("FAA Airmen API returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse FAA Airmen JSON response")
            return results

        records: list[dict[str, Any]] = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = data.get("results", data.get("data", []))

        for record in records[:10]:
            cert_type = record.get("certificateType", record.get("certificate_type", ""))
            cert_level = record.get("certificateLevel", record.get("level", ""))
            cert_date = record.get("dateOfCertification", record.get("date", ""))
            med_class = record.get("medicalClass", record.get("medical_class", ""))
            rec_first = record.get("firstName", first_name)
            rec_last = record.get("lastName", last_name)
            display_name = f"{rec_first} {rec_last}".strip()

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="faa_airmen",
                    finding_type="faa_pilot_cert",
                    title=f"FAA Certification: {display_name}",
                    content=(
                        f"Pilot: {display_name} | Type: {cert_type} | "
                        f"Level: {cert_level} | Medical: {med_class} | "
                        f"Date: {cert_date}"
                    ),
                    data={
                        "title": f"FAA Certification: {display_name}",
                        "url": "https://amsrvs.registry.faa.gov/airmeninquiry/",
                        "snippet": f"Type: {cert_type}, Level: {cert_level}",
                        "source": "faa_airmen",
                        "travel_type": "pilot_certification",
                        "location": "",
                        "date_found": cert_date,
                    },
                    confidence=80,
                )
            )

        self.logger.info(
            f"FAA Airmen API returned {len(results)} result(s) for '{first_name} {last_name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(self, full_name: str) -> list[ModuleResult]:
        """Run multiple DuckDuckGo dork queries for travel-related records."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{full_name}" travel OR flight OR airport',
            f'"{full_name}" passport',
            f'"{full_name}" customs OR border',
            f'site:tripadvisor.com "{full_name}"',
            f'site:yelp.com "{full_name}"',
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

                travel_type = self._classify_travel_type(title, snippet, url)
                finding_type = self._finding_type_for(travel_type)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Travel result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "travel_type": travel_type,
                            "location": "",
                            "date_found": "",
                        },
                        confidence=self._confidence_for(finding_type),
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} travel results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_travel_type(title: str, snippet: str, url: str) -> str:
        """Classify the type of travel record based on content clues."""
        combined = f"{title} {snippet} {url}".lower()
        if "tripadvisor" in combined or "yelp" in combined or "review" in combined:
            return "review"
        if "passport" in combined:
            return "passport"
        if "customs" in combined or "border" in combined:
            return "border"
        if "flight" in combined or "airport" in combined or "airline" in combined:
            return "flight"
        return "travel"

    @staticmethod
    def _finding_type_for(travel_type: str) -> str:
        """Map travel type to finding type."""
        if travel_type == "review":
            return "travel_review"
        return "travel_mention"

    @staticmethod
    def _confidence_for(finding_type: str) -> int:
        """Return confidence based on finding type."""
        if finding_type == "travel_review":
            return 50
        return 45

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
