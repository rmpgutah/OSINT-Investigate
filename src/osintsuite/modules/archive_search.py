"""Archive search module — Wayback Machine CDX API and availability API."""

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


class ArchiveSearchModule(BaseModule):
    name = "archive_search"
    description = "Wayback Machine archive search — snapshot count and date range"

    CDX_API = "https://web.archive.org/cdx/search/cdx"
    AVAILABILITY_API = "https://archive.org/wayback/available"

    def applicable_target_types(self) -> list[str]:
        return ["domain", "person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        # Determine search target
        domain = target.domain or ""
        name = target.full_name or target.label or ""

        if target.target_type == "domain":
            query = domain or target.label
        else:
            query = name

        if not query:
            self.logger.info("No query available, skipping archive search")
            return results

        # 1. CDX API for domain targets
        if target.target_type == "domain":
            cdx_results = await self._search_cdx(query)
            results.extend(cdx_results)

            # 2. Availability API
            avail_result = await self._check_availability(query)
            if avail_result:
                results.append(avail_result)

        # 3. DDG search for archived content
        if _HAS_DDGS:
            ddg_results = await self._search_ddg_archives(query)
            results.extend(ddg_results)

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="archive_search",
                finding_type="archive_summary",
                title=f"Archive search for {query}",
                content=f"Found {len(results)} archive-related result(s) for \"{query}\".",
                data={"query": query, "total_results": len(results)},
                confidence=75,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Wayback CDX API
    # ------------------------------------------------------------------

    async def _search_cdx(self, domain: str) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        url = (
            f"{self.CDX_API}?url={domain}/*&output=json"
            f"&fl=timestamp,original,statuscode&limit=500&collapse=timestamp:8"
        )
        try:
            response = await self.fetch(url)
            if not response:
                return results

            data = response.json()
            if not data or len(data) < 2:
                return results

            # First row is header
            rows = data[1:]
            snapshot_count = len(rows)

            timestamps = [row[0] for row in rows if len(row) > 0]
            oldest = min(timestamps) if timestamps else ""
            newest = max(timestamps) if timestamps else ""

            oldest_str = self._format_timestamp(oldest) if oldest else "N/A"
            newest_str = self._format_timestamp(newest) if newest else "N/A"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="wayback_cdx",
                    finding_type="archive_snapshots",
                    title=f"{domain}: {snapshot_count} Wayback snapshots",
                    content=(
                        f"Found {snapshot_count} archived snapshots for {domain}. "
                        f"Oldest: {oldest_str}. Newest: {newest_str}."
                    ),
                    data={
                        "domain": domain,
                        "snapshot_count": snapshot_count,
                        "oldest_snapshot": oldest_str,
                        "newest_snapshot": newest_str,
                        "oldest_timestamp": oldest,
                        "newest_timestamp": newest,
                    },
                    confidence=75,
                )
            )
        except Exception as exc:
            self.logger.warning(f"CDX API failed for {domain}: {exc}")

        return results

    # ------------------------------------------------------------------
    # Wayback Availability API
    # ------------------------------------------------------------------

    async def _check_availability(self, domain: str) -> ModuleResult | None:
        url = f"{self.AVAILABILITY_API}?url={domain}"
        try:
            response = await self.fetch(url)
            if not response:
                return None

            data = response.json()
            snapshots = data.get("archived_snapshots", {})
            closest = snapshots.get("closest", {})

            if closest and closest.get("available"):
                return ModuleResult(
                    module_name=self.name,
                    source="wayback_availability",
                    finding_type="archive_available",
                    title=f"Latest Wayback snapshot for {domain}",
                    content=f"Most recent archived snapshot: {closest.get('url', 'N/A')}",
                    data={
                        "domain": domain,
                        "snapshot_url": closest.get("url", ""),
                        "timestamp": closest.get("timestamp", ""),
                        "status": closest.get("status", ""),
                    },
                    confidence=75,
                )
        except Exception as exc:
            self.logger.warning(f"Availability API failed for {domain}: {exc}")
        return None

    # ------------------------------------------------------------------
    # DDG archive search
    # ------------------------------------------------------------------

    async def _search_ddg_archives(self, query: str) -> list[ModuleResult]:
        if not _HAS_DDGS:
            return []

        results: list[ModuleResult] = []
        search_query = f'site:web.archive.org "{query}"'

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, search_query
            )
        except Exception as exc:
            self.logger.warning(f"DDG archive search failed: {exc}")
            return results

        for hit in hits[:5]:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="archive_reference",
                    title=hit.get("title", "Archived page"),
                    content=hit.get("body", "")[:200] or None,
                    data={
                        "title": hit.get("title", ""),
                        "url": hit.get("href", ""),
                        "snippet": hit.get("body", "")[:300],
                    },
                    confidence=65,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_timestamp(ts: str) -> str:
        """Convert Wayback timestamp (YYYYMMDDhhmmss) to readable date."""
        if len(ts) >= 8:
            return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        return ts

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
