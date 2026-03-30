"""Review search module — searches for reviews, complaints, and BBB records."""

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


class ReviewSearchModule(BaseModule):
    name = "review_search"
    description = "Search reviews, complaints, and business reputation records"

    MAX_RESULTS = 20

    POSITIVE_KEYWORDS = [
        "great", "excellent", "recommended", "outstanding", "trustworthy",
        "reliable", "professional", "satisfied", "5 stars", "highly rated",
    ]
    NEGATIVE_KEYWORDS = [
        "scam", "fraud", "terrible", "avoid", "worst", "ripoff", "rip-off",
        "complaint", "lawsuit", "warning", "beware", "deceptive",
    ]

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.full_name or target.label or ""
        if not name:
            self.logger.info("No name available, skipping review search")
            return results

        # DuckDuckGo dork searches for reviews and complaints
        results.extend(await self._search_review_dorks(name))

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

        review_results = deduped[: self.MAX_RESULTS]

        # Compute sentiment summary
        positive_count = 0
        negative_count = 0
        for r in review_results:
            sentiment = r.data.get("sentiment", "neutral")
            if sentiment == "positive":
                positive_count += 1
            elif sentiment == "negative":
                negative_count += 1

        # Summary finding
        review_results.append(
            ModuleResult(
                module_name=self.name,
                source="review_search",
                finding_type="review_summary",
                title=f"Review search summary for {name}",
                content=(
                    f"Found {len(review_results)} review-related result(s) for "
                    f'"{name}". Sentiment breakdown: {positive_count} positive, '
                    f"{negative_count} negative, "
                    f"{len(review_results) - positive_count - negative_count} neutral."
                ),
                data={
                    "name": name,
                    "total_results": len(review_results),
                    "positive_count": positive_count,
                    "negative_count": negative_count,
                },
                confidence=50,
            )
        )

        return review_results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_review_dorks(self, name: str) -> list[ModuleResult]:
        """Run DDG dork queries for reviews and complaints."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{name}" review OR complaint',
            f'site:bbb.org "{name}"',
            f'site:yelp.com "{name}"',
            f'site:glassdoor.com "{name}"',
            f'site:trustpilot.com "{name}"',
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

                finding_type, confidence = self._classify_review_hit(url)
                sentiment = self._classify_sentiment(title, snippet)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Review result for {name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "platform": self._detect_platform(url),
                            "sentiment": sentiment,
                        },
                        confidence=confidence,
                    )
                )

        self.logger.info(
            f"DDG review dorks found {len(all_results)} results for '{name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_review_hit(url: str) -> tuple[str, int]:
        """Classify finding type and confidence based on the URL."""
        url_lower = url.lower()
        if "bbb.org" in url_lower:
            return "bbb_record", 65
        if any(site in url_lower for site in ("yelp.com", "trustpilot.com", "glassdoor.com")):
            return "review_mention", 50
        if "complaint" in url_lower:
            return "complaint_record", 55
        return "review_mention", 50

    def _classify_sentiment(self, title: str, snippet: str) -> str:
        """Classify sentiment of a review hit as positive, negative, or neutral."""
        combined = f"{title} {snippet}".lower()
        pos_score = sum(1 for kw in self.POSITIVE_KEYWORDS if kw in combined)
        neg_score = sum(1 for kw in self.NEGATIVE_KEYWORDS if kw in combined)

        if neg_score > pos_score:
            return "negative"
        if pos_score > neg_score:
            return "positive"
        return "neutral"

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Detect which review platform the URL belongs to."""
        url_lower = url.lower()
        for platform in ("bbb.org", "yelp.com", "glassdoor.com", "trustpilot.com"):
            if platform in url_lower:
                return platform.split(".")[0]
        return "web"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
