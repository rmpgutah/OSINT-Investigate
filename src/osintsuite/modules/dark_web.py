"""Dark web and .onion site monitoring via Ahmia search engine."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class DarkWebModule(BaseModule):
    name = "dark_web"
    description = "Dark web and .onion site monitoring via Ahmia search engine"

    def applicable_target_types(self) -> list[str]:
        return ["email", "domain", "person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        query = target.label

        # 1. Ahmia clearnet search
        ahmia_findings = await self._search_ahmia(query)
        results.extend(ahmia_findings)

        # 2. DDG dork for dark web mentions
        ddg_findings = await self._search_ddg_dork(query)
        results.extend(ddg_findings)

        # 3. Summary
        total = len(ahmia_findings) + len(ddg_findings)
        results.append(
            ModuleResult(
                module_name=self.name,
                source="aggregated",
                finding_type="dark_web_summary",
                title=f"Dark web summary for {query} ({total} mentions)",
                content=f"Found {len(ahmia_findings)} Ahmia results and {len(ddg_findings)} DDG dork results.",
                data={
                    "total_mentions": total,
                    "ahmia_results": len(ahmia_findings),
                    "ddg_dork_results": len(ddg_findings),
                },
                confidence=55,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Ahmia search
    # ------------------------------------------------------------------

    async def _search_ahmia(self, query: str) -> list[ModuleResult]:
        """Search Ahmia.fi clearnet gateway for .onion references."""
        url = f"https://ahmia.fi/search/?q={quote_plus(query)}"
        resp = await self.fetch(url)
        if resp is None:
            self.logger.warning("Ahmia search returned no response")
            return []

        html = resp.text
        findings: list[ModuleResult] = []

        if _HAS_BS4:
            findings = self._parse_ahmia_bs4(html)
        else:
            findings = self._parse_ahmia_regex(html)

        return findings[:15]

    def _parse_ahmia_bs4(self, html: str) -> list[ModuleResult]:
        """Parse Ahmia results using BeautifulSoup."""
        soup = BeautifulSoup(html, "html.parser")
        results: list[ModuleResult] = []

        for item in soup.select("li.result"):
            title_tag = item.select_one("h4") or item.select_one("a")
            link_tag = item.select_one("a[href]")
            snippet_tag = item.select_one("p") or item.select_one(".description")

            title = title_tag.get_text(strip=True) if title_tag else "Untitled"
            url = link_tag["href"] if link_tag and link_tag.has_attr("href") else ""
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="ahmia.fi",
                    finding_type="dark_web_mention",
                    title=title[:200],
                    content=snippet[:500] if snippet else None,
                    data={
                        "title": title[:200],
                        "url": url,
                        "snippet": snippet[:500],
                        "source": "ahmia",
                    },
                    confidence=50,
                )
            )

        return results

    def _parse_ahmia_regex(self, html: str) -> list[ModuleResult]:
        """Fallback regex parser when bs4 is unavailable."""
        results: list[ModuleResult] = []
        # Match anchor tags within result items
        pattern = re.compile(
            r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
        )
        for match in pattern.finditer(html):
            url = match.group(1).strip()
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if not title or not url:
                continue
            # Only include results that look relevant (contain onion or redirect)
            if "onion" in url or "redirect" in url or "ahmia" in url:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="ahmia.fi",
                        finding_type="dark_web_mention",
                        title=title[:200],
                        content=None,
                        data={
                            "title": title[:200],
                            "url": url,
                            "snippet": "",
                            "source": "ahmia",
                        },
                        confidence=45,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # DDG dark web dork
    # ------------------------------------------------------------------

    async def _search_ddg_dork(self, query: str) -> list[ModuleResult]:
        """DuckDuckGo dork for dark web / paste mentions."""
        if not _HAS_DDGS:
            self.logger.info("duckduckgo_search not installed — skipping DDG dork")
            return []

        dork = f'"{query}" site:pastebin.com OR "dark web" OR "onion" OR "tor"'
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_ddg_search, dork
            )
        except Exception as exc:
            self.logger.warning("DDG dark web dork failed: %s", exc)
            return []

        results: list[ModuleResult] = []
        for hit in hits[:15]:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="dark_web_mention",
                    title=hit.get("title", "Untitled")[:200],
                    content=hit.get("body", "")[:500] or None,
                    data={
                        "title": hit.get("title", "")[:200],
                        "url": hit.get("href", ""),
                        "snippet": hit.get("body", "")[:500],
                        "source": "ddg_dork",
                    },
                    confidence=45,
                )
            )

        return results

    @staticmethod
    def _sync_ddg_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=15))
