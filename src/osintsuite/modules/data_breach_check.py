"""Data breach check module -- searches HIBP, LeakCheck, and breach dork databases."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class DataBreachCheckModule(BaseModule):
    name = "data_breach_check"
    description = "Data breach and credential leak detection"

    HIBP_API = "https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    LEAKCHECK_API = "https://leakcheck.io/api/public?check={email}"

    def applicable_target_types(self) -> list[str]:
        return ["email", "domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        email = target.email
        domain = target.domain or target.label
        indicator = email or domain
        if not indicator:
            self.logger.info("No email/domain available on target, skipping breach check")
            return results

        # 1. HIBP (if API key and email available)
        if email:
            results.extend(await self._query_hibp(email))

        # 2. LeakCheck free API (email only)
        if email:
            results.extend(await self._query_leakcheck(email))

        # 3. DDG dork searches for breach mentions
        results.extend(await self._search_breach_dorks(indicator))

        # Summary
        breaches = [r for r in results if r.finding_type == "breach_record"]
        leaks = [r for r in results if r.finding_type == "leak_mention"]

        results.append(
            ModuleResult(
                module_name=self.name,
                source="data_breach_check",
                finding_type="breach_summary",
                title=f"Data breach check summary for {indicator}",
                content=(
                    f"Found {len(breaches)} confirmed breach record(s) and "
                    f"{len(leaks)} leak mention(s) for \"{indicator}\"."
                ),
                data={
                    "indicator": indicator,
                    "breach_records": len(breaches),
                    "leak_mentions": len(leaks),
                    "total_findings": len(results),
                },
                confidence=65,
            )
        )

        return results

    # ------------------------------------------------------------------
    # HIBP API
    # ------------------------------------------------------------------

    async def _query_hibp(self, email: str) -> list[ModuleResult]:
        """Query Have I Been Pwned for known breaches (requires API key)."""
        results: list[ModuleResult] = []

        api_key = os.environ.get("HIBP_API_KEY", "")
        if not api_key:
            self.logger.info("HIBP_API_KEY not set, skipping HIBP check")
            return results

        url = self.HIBP_API.format(email=email)

        await self.limiter.acquire()
        try:
            resp = await self.http.get(
                url,
                headers={
                    "hibp-api-key": api_key,
                    "user-agent": "OSINT-Suite-Breach-Check",
                },
                timeout=10,
            )
        except Exception as exc:
            self.logger.warning(f"HIBP request failed: {exc}")
            return results

        if resp.status_code == 404:
            self.logger.info(f"HIBP: No breaches found for {email}")
            return results
        if resp.status_code == 401:
            self.logger.warning("HIBP: Invalid API key")
            return results
        if resp.status_code == 429:
            self.logger.warning("HIBP: Rate limited")
            return results

        try:
            resp.raise_for_status()
            breaches: list[dict[str, Any]] = resp.json()
        except Exception:
            self.logger.warning("Failed to parse HIBP JSON response")
            return results

        if not isinstance(breaches, list):
            return results

        for breach in breaches[:20]:
            name = breach.get("Name", "Unknown")
            title = breach.get("Title", name)
            breach_date = breach.get("BreachDate", "")
            pwn_count = breach.get("PwnCount", 0)
            data_classes = breach.get("DataClasses", [])
            description = breach.get("Description", "")
            is_verified = breach.get("IsVerified", False)
            domain_val = breach.get("Domain", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="hibp",
                    finding_type="breach_record",
                    title=f"Breach: {title} ({breach_date})",
                    content=(
                        f"Breach: {title}, Date: {breach_date}, "
                        f"Records: {pwn_count:,}, "
                        f"Data exposed: {', '.join(data_classes[:5])}"
                    ),
                    data={
                        "breach_name": name,
                        "breach_title": title,
                        "breach_date": breach_date,
                        "pwn_count": pwn_count,
                        "data_classes": data_classes,
                        "is_verified": is_verified,
                        "domain": domain_val,
                        "source": "hibp",
                    },
                    confidence=80,
                )
            )

        self.logger.info(f"HIBP returned {len(results)} breach(es) for '{email}'")
        return results

    # ------------------------------------------------------------------
    # LeakCheck free API
    # ------------------------------------------------------------------

    async def _query_leakcheck(self, email: str) -> list[ModuleResult]:
        """Query LeakCheck free public API."""
        results: list[ModuleResult] = []

        url = self.LEAKCHECK_API.format(email=email)

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"LeakCheck request failed: {exc}")
            return results

        if not response:
            self.logger.info("LeakCheck returned no response")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse LeakCheck JSON response")
            return results

        if not isinstance(data, dict):
            return results

        success = data.get("success", False)
        found = data.get("found", 0)
        sources = data.get("sources", [])

        if not success or found == 0:
            return results

        if isinstance(sources, list):
            for source in sources[:15]:
                if isinstance(source, dict):
                    src_name = source.get("name", "Unknown")
                    src_date = source.get("date", "")
                else:
                    src_name = str(source)
                    src_date = ""

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="leakcheck",
                        finding_type="breach_record",
                        title=f"Leak found: {src_name}",
                        content=(
                            f"Email {email} found in leak database: {src_name}"
                            + (f" (date: {src_date})" if src_date else "")
                        ),
                        data={
                            "email": email,
                            "leak_source": src_name,
                            "leak_date": src_date,
                            "source": "leakcheck",
                        },
                        confidence=80,
                    )
                )
        else:
            # Just a count was returned
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="leakcheck",
                    finding_type="breach_record",
                    title=f"LeakCheck: {found} leak(s) found for {email}",
                    content=f"Email {email} appears in {found} known leak database(s).",
                    data={
                        "email": email,
                        "found_count": found,
                        "source": "leakcheck",
                    },
                    confidence=80,
                )
            )

        self.logger.info(f"LeakCheck returned {len(results)} result(s) for '{email}'")
        return results

    # ------------------------------------------------------------------
    # DDG dork searches
    # ------------------------------------------------------------------

    async def _search_breach_dorks(self, indicator: str) -> list[ModuleResult]:
        """Search DuckDuckGo for breach/leak mentions."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed -- skipping breach dork searches"
            )
            return []

        queries = [
            f'site:dehashed.com "{indicator}"',
            f'site:intelx.io "{indicator}"',
            f'"{indicator}" breach OR leaked OR dump OR database',
        ]

        all_results: list[ModuleResult] = []
        seen_urls: set[str] = set()

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

                if url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="leak_mention",
                        title=title or f"Breach mention for {indicator}",
                        content=snippet or None,
                        data={
                            "indicator": indicator,
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                        },
                        confidence=60,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} breach-related results for '{indicator}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
