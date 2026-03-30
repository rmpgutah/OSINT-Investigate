"""Academic papers and publications search module."""

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


class AcademicPapersModule(BaseModule):
    name = "academic_papers"
    description = "Academic publications, papers, and author profile search"

    SEMANTIC_SCHOLAR_AUTHOR_SEARCH = (
        "https://api.semanticscholar.org/graph/v1/author/search"
    )
    SEMANTIC_SCHOLAR_AUTHOR_PAPERS = (
        "https://api.semanticscholar.org/graph/v1/author/{author_id}/papers"
    )
    CROSSREF_WORKS = "https://api.crossref.org/works"
    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping academic papers")
            return results

        # 1. Semantic Scholar author search + papers
        results.extend(await self._search_semantic_scholar(full_name))

        # 2. CrossRef API
        results.extend(await self._search_crossref(full_name))

        # Deduplicate by URL or title
        seen: set[str] = set()
        deduped: list[ModuleResult] = []
        for r in results:
            key = r.data.get("url", "") or r.title
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduped.append(r)

        trimmed = deduped[: self.MAX_RESULTS]

        # Summary finding
        trimmed.append(
            ModuleResult(
                module_name=self.name,
                source="academic_papers",
                finding_type="academic_summary",
                title=f"Academic papers summary for {full_name}",
                content=(
                    f"Found {len(trimmed)} academic result(s) for "
                    f'"{full_name}" across Semantic Scholar and CrossRef.'
                ),
                data={
                    "name": full_name,
                    "total_results": len(trimmed),
                },
                confidence=60,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # Semantic Scholar API
    # ------------------------------------------------------------------

    async def _search_semantic_scholar(self, full_name: str) -> list[ModuleResult]:
        """Search Semantic Scholar for author matches and their papers."""
        results: list[ModuleResult] = []

        # Step 1: Find matching authors
        params = {"query": full_name, "limit": "5"}
        url = f"{self.SEMANTIC_SCHOLAR_AUTHOR_SEARCH}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"Semantic Scholar author search failed: {exc}")
            return results

        if not response:
            self.logger.info("Semantic Scholar returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse Semantic Scholar JSON")
            return results

        authors: list[dict[str, Any]] = []
        if isinstance(data, dict):
            authors = data.get("data", [])

        for author in authors[:3]:
            author_id = author.get("authorId", "")
            author_name = author.get("name", full_name)

            if not author_id:
                continue

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="semantic_scholar",
                    finding_type="academic_author",
                    title=f"Academic author: {author_name}",
                    content=f"Semantic Scholar author ID: {author_id}",
                    data={
                        "author_id": author_id,
                        "author_name": author_name,
                        "url": f"https://www.semanticscholar.org/author/{author_id}",
                        "source": "semantic_scholar",
                    },
                    confidence=70,
                )
            )

            # Step 2: Get papers for this author
            results.extend(await self._get_author_papers(author_id, author_name))

        self.logger.info(
            f"Semantic Scholar found {len(results)} result(s) for '{full_name}'"
        )
        return results

    async def _get_author_papers(
        self, author_id: str, author_name: str
    ) -> list[ModuleResult]:
        """Fetch papers for a specific Semantic Scholar author."""
        results: list[ModuleResult] = []

        params = {
            "limit": "10",
            "fields": "title,year,citationCount,url,authors",
        }
        url = (
            self.SEMANTIC_SCHOLAR_AUTHOR_PAPERS.format(author_id=author_id)
            + f"?{urllib.parse.urlencode(params)}"
        )

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(
                f"Semantic Scholar papers request failed for author {author_id}: {exc}"
            )
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse Semantic Scholar papers JSON")
            return results

        papers: list[dict[str, Any]] = []
        if isinstance(data, dict):
            papers = data.get("data", [])
        elif isinstance(data, list):
            papers = data

        for paper in papers[:10]:
            title = paper.get("title", "Untitled Paper")
            year = paper.get("year", "")
            citation_count = paper.get("citationCount", 0)
            paper_url = paper.get("url", "")

            co_authors = []
            for auth in paper.get("authors", []):
                name = auth.get("name", "")
                if name and name != author_name:
                    co_authors.append(name)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="semantic_scholar",
                    finding_type="academic_paper",
                    title=title,
                    content=(
                        f"Year: {year} | Citations: {citation_count} | "
                        f"Co-authors: {', '.join(co_authors[:5]) or 'N/A'}"
                    ),
                    data={
                        "title": title,
                        "url": paper_url,
                        "year": str(year) if year else "",
                        "citation_count": citation_count,
                        "co_authors": co_authors[:10],
                        "source": "semantic_scholar",
                    },
                    confidence=75,
                )
            )

        return results

    # ------------------------------------------------------------------
    # CrossRef API
    # ------------------------------------------------------------------

    async def _search_crossref(self, full_name: str) -> list[ModuleResult]:
        """Search CrossRef for publications by author name."""
        results: list[ModuleResult] = []

        params = {"query.author": full_name, "rows": "10"}
        url = f"{self.CROSSREF_WORKS}?{urllib.parse.urlencode(params)}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"CrossRef API request failed: {exc}")
            return results

        if not response:
            self.logger.info("CrossRef API returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse CrossRef JSON response")
            return results

        items: list[dict[str, Any]] = []
        if isinstance(data, dict):
            message = data.get("message", {})
            if isinstance(message, dict):
                items = message.get("items", [])

        for item in items[:10]:
            title_list = item.get("title", [])
            title = title_list[0] if title_list else "Untitled"
            doi = item.get("DOI", "")
            paper_url = item.get("URL", f"https://doi.org/{doi}" if doi else "")

            # Extract year
            date_parts = (
                item.get("published-print", {})
                .get("date-parts", [[]])
            )
            year = ""
            if date_parts and date_parts[0]:
                year = str(date_parts[0][0])

            # Extract journal
            journal_list = item.get("container-title", [])
            journal = journal_list[0] if journal_list else ""

            # Extract co-authors
            co_authors = []
            for auth in item.get("author", []):
                given = auth.get("given", "")
                family = auth.get("family", "")
                name = f"{given} {family}".strip()
                if name:
                    co_authors.append(name)

            citation_count = item.get("is-referenced-by-count", 0)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="crossref",
                    finding_type="academic_paper",
                    title=title,
                    content=(
                        f"Year: {year} | Journal: {journal or 'N/A'} | "
                        f"Citations: {citation_count}"
                    ),
                    data={
                        "title": title,
                        "url": paper_url,
                        "year": year,
                        "citation_count": citation_count,
                        "co_authors": co_authors[:10],
                        "journal": journal,
                        "doi": doi,
                        "source": "crossref",
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"CrossRef found {len(results)} result(s) for '{full_name}'"
        )
        return results
