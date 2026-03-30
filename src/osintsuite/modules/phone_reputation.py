"""Phone reputation module — DDG dorks for spam/scam reports."""

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


class PhoneReputationModule(BaseModule):
    name = "phone_reputation"
    description = "Phone reputation check — DDG dorks for spam/scam reports"

    SPAM_KEYWORDS = [
        "spam", "scam", "robocall", "telemarketer", "phishing", "fraud",
        "unwanted", "blocked", "reported", "complaint", "harassment",
        "junk call", "robo", "spoofed",
    ]

    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["phone"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping phone reputation"
            )
            return results

        phone = target.phone or target.label
        if not phone:
            self.logger.info("No phone number available, skipping phone reputation")
            return results

        # Normalise phone for searching
        phone_clean = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        phone_display = phone.strip()

        queries = [
            f'"{phone_display}" spam OR scam OR robocall',
            f'site:nomorobo.com "{phone_clean}"',
            f'site:robokiller.com "{phone_clean}"',
            f'"{phone_display}" "who called" OR "phone lookup"',
        ]

        all_hits: list[dict[str, Any]] = []
        for query in queries:
            try:
                hits = await asyncio.to_thread(self._sync_search, query)
                all_hits.extend(hits)
            except Exception as exc:
                self.logger.warning(f"DDG phone search failed for '{query}': {exc}")

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_hits: list[dict[str, Any]] = []
        for hit in all_hits:
            url = hit.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_hits.append(hit)

        # Score each hit
        spam_reports = 0
        clean_reports = 0

        for hit in unique_hits[: self.MAX_RESULTS]:
            title = hit.get("title", "")
            body = hit.get("body", "")
            url = hit.get("href", "")
            combined = f"{title} {body}".lower()

            is_spam = any(kw in combined for kw in self.SPAM_KEYWORDS)
            if is_spam:
                spam_reports += 1
            else:
                clean_reports += 1

            source = "web"
            if "nomorobo" in url:
                source = "nomorobo"
            elif "robokiller" in url:
                source = "robokiller"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source=source,
                    finding_type="phone_spam_report" if is_spam else "phone_mention",
                    title=f"{'SPAM: ' if is_spam else ''}{title[:80]}",
                    content=body[:200] if body else None,
                    data={
                        "phone": phone_display,
                        "title": title,
                        "url": url,
                        "snippet": body[:300],
                        "is_spam_report": is_spam,
                    },
                    confidence=60 if is_spam else 40,
                )
            )

        # Reputation assessment
        total = spam_reports + clean_reports
        reputation = "unknown"
        if total > 0:
            spam_ratio = spam_reports / total
            if spam_ratio > 0.6:
                reputation = "likely_spam"
            elif spam_ratio > 0.3:
                reputation = "suspicious"
            elif spam_reports == 0:
                reputation = "clean"
            else:
                reputation = "mixed"

        results.append(
            ModuleResult(
                module_name=self.name,
                source="phone_reputation",
                finding_type="phone_reputation_summary",
                title=f"Phone reputation for {phone_display}: {reputation.upper()}",
                content=(
                    f"Checked {total} online report(s) for {phone_display}. "
                    f"Spam reports: {spam_reports}, Other mentions: {clean_reports}. "
                    f"Reputation: {reputation}."
                ),
                data={
                    "phone": phone_display,
                    "total_reports": total,
                    "spam_reports": spam_reports,
                    "clean_reports": clean_reports,
                    "reputation": reputation,
                },
                confidence=60,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
