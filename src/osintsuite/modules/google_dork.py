"""Automated search engine dorking for exposed files, admin pages, and data leaks."""

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


class GoogleDorkModule(BaseModule):
    name = "google_dork"
    description = (
        "Automated search engine dorking for exposed files, admin pages, and data leaks"
    )

    def applicable_target_types(self) -> list[str]:
        return ["domain", "person", "email", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping google_dork module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="dork_summary",
                    title="Google Dork module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        dorks = self._generate_dorks(target)
        results: list[ModuleResult] = []
        total_results_found = 0
        dorks_with_results = 0

        for idx, query in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            if hits:
                dorks_with_results += 1
                total_results_found += len(hits)
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="dork_results",
                        title=f"Dork: {query} ({len(hits)} results)",
                        content=None,
                        data={
                            "dork_query": query,
                            "result_count": len(hits),
                            "results": [
                                {
                                    "title": h.get("title", ""),
                                    "url": h.get("href", ""),
                                    "snippet": h.get("body", ""),
                                }
                                for h in hits
                            ],
                        },
                        confidence=60,
                    )
                )

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="duckduckgo",
                finding_type="dork_summary",
                title=f"Dork summary for {target.label} ({total_results_found} total results)",
                content=None,
                data={
                    "total_dorks_run": len(dorks),
                    "total_results_found": total_results_found,
                    "dorks_with_results": dorks_with_results,
                },
                confidence=55,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    def _generate_dorks(self, target: Target) -> list[str]:
        """Return a list of dork query strings tailored to the target type."""
        label = target.label
        target_type = target.target_type

        if target_type == "domain":
            return self._domain_dorks(label)
        elif target_type == "person":
            return self._person_dorks(label)
        elif target_type == "email":
            return self._email_dorks(label)
        elif target_type == "organization":
            return self._organization_dorks(label)
        else:
            self.logger.warning(f"Unsupported target type for dorking: {target_type}")
            return []

    @staticmethod
    def _domain_dorks(domain: str) -> list[str]:
        return [
            f"site:{domain} filetype:pdf",
            f"site:{domain} filetype:xlsx OR filetype:docx",
            f'site:{domain} intitle:"index of"',
            f"site:{domain} inurl:admin OR inurl:login",
            f"site:{domain} ext:sql OR ext:db OR ext:log",
            f'"{domain}" filetype:conf OR filetype:env',
            f"site:{domain} inurl:api",
        ]

    @staticmethod
    def _person_dorks(full_name: str) -> list[str]:
        return [
            f'"{full_name}" site:linkedin.com',
            f'"{full_name}" resume filetype:pdf',
            f'"{full_name}" email OR contact',
            f'"{full_name}" site:facebook.com OR site:twitter.com',
        ]

    @staticmethod
    def _email_dorks(email: str) -> list[str]:
        return [
            f'"{email}"',
            f'"{email}" password OR leaked OR dump',
            f'"{email}" site:pastebin.com OR site:paste.ee',
            f'intext:"{email}" filetype:txt OR filetype:csv',
        ]

    @staticmethod
    def _organization_dorks(label: str) -> list[str]:
        # Best-effort domain guess: lowercase, strip spaces, append .com
        label_domain_guess = label.lower().replace(" ", "") + ".com"
        return [
            f'"{label}" filetype:pdf',
            f'"{label}" confidential OR internal',
            f'"{label}" employee directory',
            f"site:{label_domain_guess} filetype:xlsx",
        ]

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _search(self, query: str) -> list[dict[str, Any]]:
        """Run a single DuckDuckGo search via asyncio.to_thread."""
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
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
