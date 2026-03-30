"""Paste monitor module — enhanced paste site search across multiple platforms."""

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


SENSITIVE_KEYWORDS = [
    "password", "passwd", "secret", "token", "apikey", "api_key",
    "credential", "private", "ssh", "-----BEGIN",
]


class PasteMonitorModule(BaseModule):
    name = "paste_monitor"
    description = "Enhanced paste site monitoring across multiple platforms"

    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["email", "domain", "ip"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        label = target.label
        if not label:
            self.logger.info("No label available on target, skipping paste monitor")
            return results

        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping paste monitor"
            )
            return results

        # Search across multiple paste sites
        results.extend(await self._search_paste_sites(label))

        # Search IntelX mentions
        results.extend(await self._search_intelx(label))

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

        sensitive_count = sum(
            1 for r in trimmed if r.finding_type == "paste_sensitive"
        )

        # Summary finding
        trimmed.append(
            ModuleResult(
                module_name=self.name,
                source="paste_monitor",
                finding_type="paste_monitor_summary",
                title=f"Paste monitor summary for {label}",
                content=(
                    f"Found {len(trimmed)} paste result(s) for \"{label}\". "
                    f"{sensitive_count} potentially sensitive paste(s) detected."
                ),
                data={
                    "target": label,
                    "total_results": len(trimmed),
                    "sensitive_count": sensitive_count,
                },
                confidence=55,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # Paste site searches
    # ------------------------------------------------------------------

    async def _search_paste_sites(self, target: str) -> list[ModuleResult]:
        """Search multiple paste sites via DDG dorks."""
        results: list[ModuleResult] = []

        paste_sites = [
            "pastebin.com",
            "ghostbin.com",
            "paste.ee",
            "dpaste.org",
        ]

        for site in paste_sites:
            query = f'site:{site} "{target}"'
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(
                    f"DDG paste search failed for {site}: {exc}"
                )
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                snippet = hit.get("body", "")
                url = hit.get("href", "")

                is_sensitive = self._check_sensitive(title, snippet)
                finding_type = "paste_sensitive" if is_sensitive else "paste_hit"
                confidence = 70 if is_sensitive else 60

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source=site,
                        finding_type=finding_type,
                        title=title or f"Paste hit on {site} for {target}",
                        content=snippet or None,
                        data={
                            "url": url,
                            "snippet": snippet,
                            "paste_site": site,
                            "is_sensitive": is_sensitive,
                            "source": "duckduckgo_dork",
                        },
                        confidence=confidence,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # IntelX search
    # ------------------------------------------------------------------

    async def _search_intelx(self, target: str) -> list[ModuleResult]:
        """Search DDG for IntelX paste mentions."""
        results: list[ModuleResult] = []
        query = f'site:intelx.io "{target}" paste'

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG IntelX search failed: {exc}")
            return results

        for hit in hits[:5]:
            title = hit.get("title", "")
            snippet = hit.get("body", "")
            url = hit.get("href", "")

            is_sensitive = self._check_sensitive(title, snippet)
            finding_type = "paste_sensitive" if is_sensitive else "paste_hit"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="intelx",
                    finding_type=finding_type,
                    title=title or f"IntelX paste hit for {target}",
                    content=snippet or None,
                    data={
                        "url": url,
                        "snippet": snippet,
                        "paste_site": "intelx.io",
                        "is_sensitive": is_sensitive,
                        "source": "duckduckgo_dork",
                    },
                    confidence=70 if is_sensitive else 60,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_sensitive(title: str, snippet: str) -> bool:
        """Check if paste content contains sensitive keywords."""
        combined = f"{title} {snippet}".lower()
        return any(kw in combined for kw in SENSITIVE_KEYWORDS)

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
