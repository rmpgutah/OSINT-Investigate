"""News article and press coverage search module."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False

try:
    import xml.etree.ElementTree as ET

    _HAS_XML = True
except ImportError:
    _HAS_XML = False


class NewsMonitorModule(BaseModule):
    name = "news_monitor"
    description = "News article and press coverage search"

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization", "domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        query = target.label

        # 1. Google News RSS
        rss_findings = await self._search_google_news_rss(query)
        results.extend(rss_findings)

        # 2. DDG news search
        ddg_findings = await self._search_ddg_news(query)
        results.extend(ddg_findings)

        # 3. Summary
        total = len(rss_findings) + len(ddg_findings)
        results.append(
            ModuleResult(
                module_name=self.name,
                source="aggregated",
                finding_type="news_summary",
                title=f"News summary for {query} ({total} articles)",
                content=f"Found {len(rss_findings)} via Google News RSS and {len(ddg_findings)} via DDG news.",
                data={
                    "total_articles": total,
                    "google_news_results": len(rss_findings),
                    "ddg_news_results": len(ddg_findings),
                },
                confidence=60,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Google News RSS
    # ------------------------------------------------------------------

    async def _search_google_news_rss(self, query: str) -> list[ModuleResult]:
        """Parse Google News RSS feed for articles mentioning the query."""
        url = (
            f"https://news.google.com/rss/search?q={quote_plus(query)}"
            "&hl=en-US&gl=US&ceid=US:en"
        )
        resp = await self.fetch(url)
        if resp is None:
            self.logger.warning("Google News RSS returned no response")
            return []

        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> list[ModuleResult]:
        """Parse RSS XML into ModuleResult list."""
        results: list[ModuleResult] = []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            self.logger.warning("Failed to parse Google News RSS XML: %s", exc)
            return results

        # RSS items live under <channel><item>
        channel = root.find("channel")
        if channel is None:
            return results

        for item in channel.findall("item")[:15]:
            title = (item.findtext("title") or "Untitled").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source_tag = item.find("source")
            source_name = (
                source_tag.text.strip() if source_tag is not None and source_tag.text else ""
            )
            description = (item.findtext("description") or "").strip()
            # Strip HTML from description
            snippet = re.sub(r"<[^>]+>", "", description)[:500]

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="google_news_rss",
                    finding_type="news_article",
                    title=title[:200],
                    content=snippet or None,
                    data={
                        "title": title[:200],
                        "url": link,
                        "source": source_name,
                        "published_date": pub_date,
                        "snippet": snippet,
                    },
                    confidence=65,
                )
            )

        return results

    # ------------------------------------------------------------------
    # DDG news search
    # ------------------------------------------------------------------

    async def _search_ddg_news(self, query: str) -> list[ModuleResult]:
        """Search DuckDuckGo news via duckduckgo_search library."""
        if not _HAS_DDGS:
            self.logger.info("duckduckgo_search not installed — skipping DDG news")
            return []

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_ddg_news, query
            )
        except Exception as exc:
            self.logger.warning("DDG news search failed: %s", exc)
            return []

        results: list[ModuleResult] = []
        for hit in hits[:15]:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo_news",
                    finding_type="news_article",
                    title=hit.get("title", "Untitled")[:200],
                    content=hit.get("body", "")[:500] or None,
                    data={
                        "title": hit.get("title", "")[:200],
                        "url": hit.get("url", hit.get("href", "")),
                        "source": hit.get("source", ""),
                        "published_date": hit.get("date", ""),
                        "snippet": hit.get("body", "")[:500],
                    },
                    confidence=60,
                )
            )

        return results

    @staticmethod
    def _sync_ddg_news(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo news search (called in a thread)."""
        return list(DDGS().news(query, max_results=15))
