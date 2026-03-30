"""Reputation score module — checks domain/IP/email reputation across free sources."""

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


class ReputationScoreModule(BaseModule):
    name = "reputation_score"
    description = "Composite reputation scoring for domains, IPs, and emails"

    PHISHTANK_CHECK_URL = "https://checkurl.phishtank.com/checkurl/"
    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["domain", "ip", "email"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        identifier = target.label or ""
        if not identifier:
            self.logger.info("No identifier on target, skipping reputation score")
            return results

        # 1. PhishTank check
        results.extend(await self._check_phishtank(identifier))

        # 2. DuckDuckGo dork searches for reputation signals
        results.extend(await self._search_reputation_dorks(identifier))

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

        signal_results = deduped[: self.MAX_RESULTS]

        # Compute composite score
        score = self._compute_composite_score(signal_results)

        signal_results.append(
            ModuleResult(
                module_name=self.name,
                source="reputation_score",
                finding_type="reputation_score",
                title=f"Reputation score for {identifier}",
                content=(
                    f"Composite reputation score for \"{identifier}\": {score}/100. "
                    f"Based on {len(signal_results)} signal(s) from PhishTank and "
                    f"DuckDuckGo dork searches."
                ),
                data={
                    "identifier": identifier,
                    "score": score,
                    "total_signals": len(signal_results),
                },
                confidence=65,
            )
        )

        return signal_results

    # ------------------------------------------------------------------
    # PhishTank check
    # ------------------------------------------------------------------

    async def _check_phishtank(self, identifier: str) -> list[ModuleResult]:
        """Check PhishTank for known phishing URLs."""
        results: list[ModuleResult] = []

        try:
            response = await self.fetch(
                self.PHISHTANK_CHECK_URL,
                method="POST",
                data={
                    "url": identifier,
                    "format": "json",
                },
            )
        except Exception as exc:
            self.logger.warning(f"PhishTank request failed: {exc}")
            return results

        if not response:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse PhishTank JSON response")
            return results

        phish_results = data.get("results", {})
        in_database = phish_results.get("in_database", False)
        is_phish = phish_results.get("valid", False)

        if in_database:
            finding_type = "phishing_detected" if is_phish else "reputation_signal"
            confidence = 85 if is_phish else 70

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="phishtank",
                    finding_type=finding_type,
                    title=f"PhishTank result for {identifier}",
                    content=(
                        f"{'PHISHING DETECTED' if is_phish else 'Found in database'} "
                        f"— {identifier} is {'a known phishing URL' if is_phish else 'in PhishTank database'}."
                    ),
                    data={
                        "url": identifier,
                        "in_database": in_database,
                        "is_phish": is_phish,
                        "source": "phishtank",
                        "phishtank_detail_url": phish_results.get("phish_detail_page", ""),
                    },
                    confidence=confidence,
                )
            )

        self.logger.info(f"PhishTank check complete for '{identifier}'")
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_reputation_dorks(self, identifier: str) -> list[ModuleResult]:
        """Run DDG dork queries for reputation signals."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'site:transparencyreport.google.com "{identifier}"',
            f'site:mywot.com "{identifier}"',
            f'"{identifier}" phishing OR malware OR scam',
            f'"{identifier}" blacklist OR blocklist OR spam',
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

                is_phishing = self._is_phishing_signal(title, snippet, url)
                finding_type = "phishing_detected" if is_phishing else "reputation_signal"
                confidence = 85 if is_phishing else 70

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Reputation signal for {identifier}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "signal_type": "phishing" if is_phishing else "reputation",
                        },
                        confidence=confidence,
                    )
                )

        self.logger.info(
            f"DDG reputation dorks found {len(all_results)} results for '{identifier}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_phishing_signal(title: str, snippet: str, url: str) -> bool:
        """Determine if the result indicates a phishing/malware signal."""
        combined = f"{title} {snippet} {url}".lower()
        bad_keywords = ["phishing", "malware", "scam", "fraud", "blacklisted", "blocked"]
        return any(kw in combined for kw in bad_keywords)

    @staticmethod
    def _compute_composite_score(signals: list[ModuleResult]) -> int:
        """Compute a composite reputation score from 0 (worst) to 100 (best)."""
        if not signals:
            return 50  # neutral when no data

        score = 100
        for signal in signals:
            if signal.finding_type == "phishing_detected":
                score -= 30
            elif signal.finding_type == "reputation_signal":
                combined = f"{signal.title} {signal.content or ''}".lower()
                if any(kw in combined for kw in ("phishing", "malware", "scam", "fraud")):
                    score -= 15
                elif any(kw in combined for kw in ("safe", "trusted", "clean")):
                    score += 5

        return max(0, min(100, score))

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
