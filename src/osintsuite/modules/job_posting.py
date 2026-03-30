"""Job posting module — DDG dorks for job listings revealing company intel."""

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


class JobPostingModule(BaseModule):
    name = "job_posting"
    description = "Job posting analysis — reveals company size, tech stack, locations"

    TECH_KEYWORDS = [
        "python", "java", "javascript", "typescript", "react", "angular",
        "vue", "node", "golang", "rust", "kubernetes", "docker", "aws",
        "azure", "gcp", "terraform", "postgresql", "mysql", "mongodb",
        "redis", "elasticsearch", "kafka", "spark", "machine learning",
        "ai", "deep learning", "microservices", "graphql", "rest api",
    ]

    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping job posting search"
            )
            return results

        org_name = target.organization_name or target.label
        if not org_name:
            self.logger.info("No organization name available, skipping job postings")
            return results

        queries = [
            f'site:indeed.com "{org_name}" jobs',
            f'site:glassdoor.com "{org_name}" jobs',
            f'site:linkedin.com/jobs "{org_name}"',
            f'"{org_name}" hiring OR "open positions" OR "we are looking"',
        ]

        all_hits: list[dict[str, Any]] = []
        for query in queries:
            try:
                hits = await asyncio.to_thread(self._sync_search, query)
                all_hits.extend(hits)
            except Exception as exc:
                self.logger.warning(f"DDG job search failed for '{query}': {exc}")

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_hits: list[dict[str, Any]] = []
        for hit in all_hits:
            url = hit.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_hits.append(hit)

        # Analyse each listing
        tech_found: set[str] = set()
        locations: set[str] = set()

        for hit in unique_hits[: self.MAX_RESULTS]:
            title = hit.get("title", "")
            body = hit.get("body", "")
            url = hit.get("href", "")
            combined = f"{title} {body}".lower()

            # Extract tech keywords
            hit_tech = [kw for kw in self.TECH_KEYWORDS if kw in combined]
            tech_found.update(hit_tech)

            # Try to extract location hints
            hit_locations = self._extract_locations(combined)
            locations.update(hit_locations)

            source = "indeed"
            if "glassdoor" in url:
                source = "glassdoor"
            elif "linkedin" in url:
                source = "linkedin"
            else:
                source = "web"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source=source,
                    finding_type="job_posting",
                    title=title[:100] or f"Job posting at {org_name}",
                    content=body[:200] if body else None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": body[:300],
                        "tech_mentioned": hit_tech,
                        "organization": org_name,
                    },
                    confidence=55,
                )
            )

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="job_posting",
                finding_type="job_posting_summary",
                title=f"Job posting analysis for {org_name}",
                content=(
                    f"Found {len(unique_hits)} job listing(s) for \"{org_name}\". "
                    f"Tech stack: {', '.join(sorted(tech_found)) or 'N/A'}. "
                    f"Locations: {', '.join(sorted(locations)) or 'N/A'}."
                ),
                data={
                    "organization": org_name,
                    "total_listings": len(unique_hits),
                    "tech_stack": sorted(tech_found),
                    "locations": sorted(locations),
                },
                confidence=55,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_locations(text: str) -> list[str]:
        """Extract common location patterns from text."""
        locs: list[str] = []
        markers = ["remote", "hybrid", "on-site", "onsite"]
        for m in markers:
            if m in text:
                locs.append(m)
        return locs

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
