"""Digital footprint module — searches forums, blogs, web archives, and online mentions."""

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


class DigitalFootprintModule(BaseModule):
    name = "digital_footprint"
    description = "Digital footprint — forums, blogs, web archives, and online mentions"

    WAYBACK_CDX_API = "https://web.archive.org/cdx/search/cdx"
    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["person", "email", "username"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping digital_footprint module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="digital_footprint",
                    finding_type="digital_footprint_summary",
                    title="Digital Footprint module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        results: list[ModuleResult] = []
        seen_urls: set[str] = set()
        total_found = 0

        target_type = getattr(target, "target_type", "person")

        # Build dorks based on target type
        dorks = self._generate_dorks(target, target_type)

        for idx, (query, finding_type, confidence) in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            for hit in hits:
                url = hit.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if total_found >= self.MAX_RESULTS:
                    break

                title = hit.get("title", "")
                snippet = hit.get("body", "")
                platform = self._detect_platform(url)
                content_type = self._detect_content_type(title, snippet, url)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=f"Digital footprint: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo",
                            "platform": platform,
                            "content_type": content_type,
                            "date_found": "",
                        },
                        confidence=confidence,
                    )
                )
                total_found += 1

            if total_found >= self.MAX_RESULTS:
                break

        # Wayback Machine CDX search
        wayback_results = await self._search_wayback(target, target_type)
        for r in wayback_results:
            url = r.data.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(r)
                total_found += 1
            if total_found >= self.MAX_RESULTS:
                break

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="digital_footprint",
                finding_type="digital_footprint_summary",
                title=f"Digital footprint search ({total_found} results)",
                content=None,
                data={
                    "target_type": target_type,
                    "total_results": total_found,
                    "dorks_run": len(dorks),
                },
                confidence=45,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Wayback Machine CDX API
    # ------------------------------------------------------------------

    async def _search_wayback(
        self, target: Any, target_type: str
    ) -> list[ModuleResult]:
        """Query the Wayback Machine CDX API for archived pages."""
        results: list[ModuleResult] = []

        search_term = ""
        if target_type == "email":
            email = getattr(target, "email", "") or target.label or ""
            if "@" in email:
                search_term = email.split("@")[1]
        elif target_type == "username":
            search_term = getattr(target, "username", "") or target.label or ""
        else:
            search_term = (target.full_name or target.label or "").replace(" ", "+")

        if not search_term:
            return results

        params = {
            "url": f"*{search_term}*",
            "output": "json",
            "limit": "10",
        }
        url = f"{self.WAYBACK_CDX_API}?{urllib.parse.urlencode(params)}"

        response = await self.fetch(url)
        if not response:
            return results

        try:
            rows = response.json()
        except Exception:
            self.logger.warning("Failed to parse Wayback CDX JSON response")
            return results

        if not isinstance(rows, list) or len(rows) < 2:
            return results

        # First row is the header
        headers = rows[0] if rows else []
        url_idx = headers.index("original") if "original" in headers else 2
        ts_idx = headers.index("timestamp") if "timestamp" in headers else 1

        for row in rows[1:11]:
            try:
                original_url = row[url_idx] if len(row) > url_idx else ""
                timestamp = row[ts_idx] if len(row) > ts_idx else ""
                archive_url = f"https://web.archive.org/web/{timestamp}/{original_url}"

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="wayback_machine",
                        finding_type="web_archive",
                        title=f"Archived: {original_url[:100]}",
                        content=f"Wayback Machine snapshot from {timestamp[:8]}",
                        data={
                            "title": original_url,
                            "url": archive_url,
                            "snippet": f"Archived snapshot from {timestamp[:8]}",
                            "source": "wayback_machine",
                            "platform": "web_archive",
                            "content_type": "archive",
                            "date_found": timestamp[:8] if timestamp else "",
                        },
                        confidence=60,
                    )
                )
            except (IndexError, ValueError):
                continue

        self.logger.info(
            f"Wayback CDX returned {len(results)} result(s)"
        )
        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(target: Any, target_type: str) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        dorks: list[tuple[str, str, int]] = []

        if target_type == "email":
            email = getattr(target, "email", "") or target.label or ""
            if email:
                dorks.append((f'"{email}"', "blog_mention", 50))
                dorks.append((f'"{email}" site:reddit.com', "forum_post", 55))
                dorks.append((f'"{email}" profile OR account', "blog_mention", 50))
            return dorks

        if target_type == "username":
            username = getattr(target, "username", "") or target.label or ""
            if username:
                dorks.append(
                    (
                        f'"{username}" site:reddit.com OR site:stackoverflow.com',
                        "forum_post",
                        55,
                    )
                )
                dorks.append(
                    (f'"{username}" site:github.com', "forum_post", 55)
                )
                dorks.append(
                    (f'"{username}" blog OR profile', "blog_mention", 50)
                )
            return dorks

        # Default: person
        full_name = target.full_name or target.label or ""
        if full_name:
            dorks = [
                (f'"{full_name}" site:reddit.com', "forum_post", 55),
                (f'"{full_name}" site:medium.com', "blog_mention", 50),
                (f'"{full_name}" site:quora.com', "forum_post", 55),
                (f'"{full_name}" blog OR profile', "blog_mention", 50),
            ]

        return dorks

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Identify the platform from the URL."""
        platforms = {
            "reddit.com": "reddit",
            "medium.com": "medium",
            "quora.com": "quora",
            "stackoverflow.com": "stackoverflow",
            "github.com": "github",
            "twitter.com": "twitter",
            "x.com": "twitter",
            "linkedin.com": "linkedin",
            "facebook.com": "facebook",
            "web.archive.org": "wayback_machine",
        }
        url_lower = url.lower()
        for domain, platform in platforms.items():
            if domain in url_lower:
                return platform
        return "web"

    @staticmethod
    def _detect_content_type(title: str, snippet: str, url: str) -> str:
        """Guess the content type from the result."""
        text = (title + " " + snippet + " " + url).lower()
        if any(kw in text for kw in ["forum", "thread", "comment", "reply", "discussion"]):
            return "forum_post"
        if any(kw in text for kw in ["blog", "article", "post", "medium.com"]):
            return "blog_post"
        if any(kw in text for kw in ["profile", "about", "bio"]):
            return "profile"
        if "archive.org" in text:
            return "archive"
        return "web_mention"

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _search(self, query: str) -> list[dict[str, Any]]:
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
        return list(DDGS().text(query, max_results=10))
