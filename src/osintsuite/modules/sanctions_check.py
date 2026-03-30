"""Sanctions check module — OFAC, UN, EU, and OpenSanctions screening."""

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


class SanctionsCheckModule(BaseModule):
    name = "sanctions_check"
    description = "Sanctions screening — OFAC SDN, UN, EU, and OpenSanctions"

    OPENSANCTIONS_API = "https://api.opensanctions.org/match/default"
    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping sanctions check")
            return results

        target_type = getattr(target, "target_type", "person")

        # 1. OpenSanctions API
        results.extend(await self._search_opensanctions(name, target_type))

        # 2. DuckDuckGo dork searches (OFAC, UN, EU)
        results.extend(await self._search_dorks(name))

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

        capped = deduped[: self.MAX_RESULTS]

        # Summary finding
        capped.append(
            ModuleResult(
                module_name=self.name,
                source="sanctions_check",
                finding_type="sanctions_summary",
                title=f"Sanctions screening summary for {name}",
                content=(
                    f"Found {len(capped)} sanctions-related result(s) for "
                    f'"{name}" across OpenSanctions, OFAC, UN, and EU databases.'
                ),
                data={
                    "name": name,
                    "total_results": len(capped),
                },
                confidence=60,
            )
        )

        return capped

    # ------------------------------------------------------------------
    # OpenSanctions API
    # ------------------------------------------------------------------

    async def _search_opensanctions(
        self, name: str, target_type: str
    ) -> list[ModuleResult]:
        """Query the OpenSanctions free matching API."""
        results: list[ModuleResult] = []

        schema = "Person" if target_type == "person" else "Organization"
        params = {
            "schema": schema,
            "properties.name": name,
        }
        url = f"{self.OPENSANCTIONS_API}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"OpenSanctions API request failed: {exc}")
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse OpenSanctions JSON response")
            return results

        matches: list[dict[str, Any]] = []
        if isinstance(data, dict):
            matches = data.get("results", data.get("responses", []))
        elif isinstance(data, list):
            matches = data

        for match in matches[:10]:
            entity_id = match.get("id", "")
            entity_name = match.get("caption", match.get("name", "Unknown"))
            score = match.get("score", 0)
            datasets = match.get("datasets", [])
            properties = match.get("properties", {})
            country = properties.get("country", [""])[0] if isinstance(properties.get("country"), list) else ""
            entity_url = f"https://www.opensanctions.org/entities/{entity_id}/" if entity_id else ""

            dataset_str = ", ".join(datasets) if isinstance(datasets, list) else str(datasets)

            confidence = 90 if score > 0.8 else 65

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="opensanctions",
                    finding_type="sanction_match",
                    title=f"Sanctions match: {entity_name}",
                    content=(
                        f"Match score {score:.2f}. Datasets: {dataset_str}. "
                        f"Country: {country}."
                    ),
                    data={
                        "entity_id": entity_id,
                        "entity_name": entity_name,
                        "score": score,
                        "datasets": datasets,
                        "country": country,
                        "url": entity_url,
                        "source": "opensanctions",
                    },
                    confidence=confidence,
                )
            )

        self.logger.info(
            f"OpenSanctions returned {len(results)} result(s) for '{name}'"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(self, name: str) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for sanctions mentions."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'site:sanctionssearch.ofac.treas.gov "{name}"',
            f'site:un.org sanctions "{name}"',
            f'site:sanctionsmap.eu "{name}"',
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

                # Classify source
                finding_type = "sanction_mention"
                confidence = 65
                source_label = "duckduckgo_dork"

                if "ofac" in url.lower():
                    finding_type = "sanction_match"
                    confidence = 90
                    source_label = "ofac_sdn"
                elif "un.org" in url.lower():
                    source_label = "un_sanctions"
                elif "sanctionsmap.eu" in url.lower():
                    source_label = "eu_sanctions"

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Sanctions result for {name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": source_label,
                        },
                        confidence=confidence,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} sanctions results for '{name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
