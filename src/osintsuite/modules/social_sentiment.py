"""Social sentiment module — DDG search + keyword-based sentiment scoring."""

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


class SocialSentimentModule(BaseModule):
    name = "social_sentiment"
    description = "Social sentiment analysis via DuckDuckGo search and keyword scoring"

    POSITIVE_KEYWORDS = [
        "excellent", "great", "amazing", "wonderful", "fantastic", "love",
        "recommend", "best", "outstanding", "trusted", "reliable", "honest",
        "helpful", "professional", "friendly", "satisfied", "positive",
        "five star", "5 star", "highly rated", "top rated",
    ]

    NEGATIVE_KEYWORDS = [
        "scam", "fraud", "terrible", "awful", "worst", "avoid", "ripoff",
        "rip-off", "dishonest", "complaint", "lawsuit", "warning", "beware",
        "fake", "unethical", "unprofessional", "horrible", "disappointing",
        "one star", "1 star", "poor", "negative", "bad review",
    ]

    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping social sentiment"
            )
            return results

        name = target.full_name or target.organization_name or target.label
        if not name:
            self.logger.info("No name available on target, skipping social sentiment")
            return results

        queries = [
            f'"{name}" review',
            f'"{name}" opinion',
            f'"{name}" reputation',
        ]

        all_hits: list[dict[str, Any]] = []
        for query in queries:
            try:
                hits = await asyncio.to_thread(self._sync_search, query)
                all_hits.extend(hits)
            except Exception as exc:
                self.logger.warning(f"DDG sentiment search failed for '{query}': {exc}")

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_hits: list[dict[str, Any]] = []
        for hit in all_hits:
            url = hit.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_hits.append(hit)

        # Score sentiment for each hit
        positive_count = 0
        negative_count = 0
        neutral_count = 0

        for hit in unique_hits[: self.MAX_RESULTS]:
            title = hit.get("title", "")
            body = hit.get("body", "")
            url = hit.get("href", "")
            combined = f"{title} {body}".lower()

            sentiment = self._classify_sentiment(combined)

            if sentiment == "positive":
                positive_count += 1
            elif sentiment == "negative":
                negative_count += 1
            else:
                neutral_count += 1

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type=f"sentiment_{sentiment}",
                    title=f"[{sentiment.upper()}] {title[:80]}",
                    content=body[:200] if body else None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": body[:300],
                        "sentiment": sentiment,
                        "name": name,
                    },
                    confidence=50,
                )
            )

        total = positive_count + negative_count + neutral_count
        overall = "neutral"
        if total > 0:
            if positive_count > negative_count * 2:
                overall = "positive"
            elif negative_count > positive_count * 2:
                overall = "negative"
            elif negative_count > positive_count:
                overall = "leaning_negative"
            elif positive_count > negative_count:
                overall = "leaning_positive"

        results.append(
            ModuleResult(
                module_name=self.name,
                source="social_sentiment",
                finding_type="sentiment_summary",
                title=f"Sentiment analysis for {name}: {overall.upper()}",
                content=(
                    f"Analysed {total} result(s) for \"{name}\". "
                    f"Positive: {positive_count}, Negative: {negative_count}, "
                    f"Neutral: {neutral_count}. Overall: {overall}."
                ),
                data={
                    "name": name,
                    "total_results": total,
                    "positive": positive_count,
                    "negative": negative_count,
                    "neutral": neutral_count,
                    "overall_sentiment": overall,
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _classify_sentiment(self, text: str) -> str:
        pos_score = sum(1 for kw in self.POSITIVE_KEYWORDS if kw in text)
        neg_score = sum(1 for kw in self.NEGATIVE_KEYWORDS if kw in text)
        if pos_score > neg_score:
            return "positive"
        if neg_score > pos_score:
            return "negative"
        return "neutral"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
