"""Document search module — finds exposed documents via DDG dork searches."""

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


class DocumentSearchModule(BaseModule):
    name = "document_search"
    description = "Find exposed documents, reports, and spreadsheets"

    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization", "domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping document search")
            return results

        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping document search"
            )
            return results

        domain = target.label if target.target_type == "domain" else None

        # Build queries
        queries = self._build_queries(name, domain)

        for query, finding_type in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG document search failed for '{query}': {exc}")
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Document result for {name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "file_type": self._detect_file_type(url, query),
                        },
                        confidence=60,
                    )
                )

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
        doc_count = sum(1 for r in trimmed if r.finding_type == "document_found")
        sheet_count = sum(1 for r in trimmed if r.finding_type == "spreadsheet_found")

        trimmed.append(
            ModuleResult(
                module_name=self.name,
                source="document_search",
                finding_type="document_summary",
                title=f"Document search summary for {name}",
                content=(
                    f"Found {doc_count} document(s) and {sheet_count} spreadsheet(s) "
                    f'for "{name}". Total results: {len(trimmed)}.'
                ),
                data={
                    "name": name,
                    "document_count": doc_count,
                    "spreadsheet_count": sheet_count,
                    "total_results": len(trimmed),
                },
                confidence=55,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_queries(
        name: str, domain: str | None
    ) -> list[tuple[str, str]]:
        """Build dork queries and their associated finding types."""
        queries = [
            (f'"{name}" filetype:pdf', "document_found"),
            (f'"{name}" filetype:doc OR filetype:docx', "document_found"),
            (f'"{name}" filetype:xls OR filetype:xlsx', "spreadsheet_found"),
            (f'"{name}" filetype:ppt OR filetype:pptx', "document_found"),
        ]
        if domain:
            queries.append((f"site:{domain} filetype:pdf", "document_found"))
            queries.append(
                (f"site:{domain} filetype:xls OR filetype:xlsx", "spreadsheet_found")
            )
        return queries

    @staticmethod
    def _detect_file_type(url: str, query: str) -> str:
        """Detect file type from URL or query context."""
        url_lower = url.lower()
        if url_lower.endswith(".pdf"):
            return "pdf"
        if url_lower.endswith((".doc", ".docx")):
            return "doc"
        if url_lower.endswith((".xls", ".xlsx")):
            return "xls"
        if url_lower.endswith((".ppt", ".pptx")):
            return "ppt"
        if "filetype:pdf" in query:
            return "pdf"
        if "filetype:xls" in query:
            return "xls"
        if "filetype:doc" in query:
            return "doc"
        return "unknown"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
