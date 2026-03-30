"""Alias detection module — searches for aliases, maiden names, usernames, and alternate identities."""

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


class AliasDetectionModule(BaseModule):
    name = "alias_detection"
    description = "Alias, maiden name, username variation, and alternate identity detection"

    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping alias detection")
            return results

        # Parse name parts for username generation
        name_parts = full_name.strip().split()
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[-1] if len(name_parts) > 1 else ""

        # 1. DDG dork searches for aliases / AKAs
        results.extend(await self._search_alias_dorks(full_name))

        # 2. Username variation searches
        if first_name and last_name:
            results.extend(
                await self._search_username_variations(first_name, last_name)
            )

        # 3. Email permutation cross-reference
        email = getattr(target, "email", None) or ""
        if email:
            results.extend(await self._search_email_permutations(email, full_name))

        # 4. Check known aliases from target metadata
        metadata = getattr(target, "metadata", None) or {}
        known_aliases: list[str] = []
        if isinstance(metadata, dict):
            known_aliases = metadata.get("aliases", [])
        if known_aliases:
            results.extend(await self._search_known_aliases(known_aliases))

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

        case_results = deduped[: self.MAX_RESULTS]

        # Summary finding
        case_results.append(
            ModuleResult(
                module_name=self.name,
                source="alias_detection",
                finding_type="alias_summary",
                title=f"Alias detection summary for {full_name}",
                content=(
                    f"Found {len(case_results)} alias-related result(s) for "
                    f'"{full_name}" via DuckDuckGo dork and username variation searches.'
                ),
                data={
                    "name": full_name,
                    "total_results": len(case_results),
                },
                confidence=50,
            )
        )

        return case_results

    # ------------------------------------------------------------------
    # DDG alias / AKA dork searches
    # ------------------------------------------------------------------

    async def _search_alias_dorks(self, full_name: str) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for alias / AKA mentions."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping alias dork searches"
            )
            return []

        queries = [
            f'"{full_name}" aka OR alias OR "also known as"',
            f'"{full_name}" maiden name',
            f'"{full_name}" formerly',
        ]

        all_results: list[ModuleResult] = []

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG alias dork failed for '{query}': {exc}")
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                alias_type = self._classify_alias_type(title, snippet)
                alias_value = self._extract_alias_value(snippet, full_name)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="alias_found",
                        title=title or f"Alias result for {full_name}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "alias_type": alias_type,
                            "alias_value": alias_value,
                            "platform": "",
                        },
                        confidence=55,
                    )
                )

        self.logger.info(
            f"DDG alias dork searches found {len(all_results)} results for '{full_name}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Username variation searches
    # ------------------------------------------------------------------

    async def _search_username_variations(
        self, first_name: str, last_name: str
    ) -> list[ModuleResult]:
        """Generate common username patterns and search social platforms."""
        if not _HAS_DDGS:
            return []

        first = first_name.lower()
        last = last_name.lower()

        usernames = [
            f"{first}.{last}",
            f"{first}{last}",
            f"{first[0]}{last}",
            f"{first}_{last}",
        ]

        platforms = [
            ("twitter.com", "Twitter"),
            ("instagram.com", "Instagram"),
            ("linkedin.com", "LinkedIn"),
            ("github.com", "GitHub"),
        ]

        all_results: list[ModuleResult] = []

        for username in usernames:
            for domain, platform_name in platforms:
                query = f'site:{domain} "{username}"'
                try:
                    hits: list[dict[str, Any]] = await asyncio.to_thread(
                        self._sync_search, query
                    )
                except Exception as exc:
                    self.logger.debug(
                        f"DDG username search failed for '{query}': {exc}"
                    )
                    continue

                for hit in hits[:2]:
                    title = hit.get("title", "")
                    url = hit.get("href", "")
                    snippet = hit.get("body", "")

                    all_results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="duckduckgo",
                            finding_type="username_variation",
                            title=title or f"Username '{username}' on {platform_name}",
                            content=snippet or None,
                            data={
                                "title": title,
                                "url": url,
                                "snippet": snippet,
                                "source": "duckduckgo_dork",
                                "alias_type": "username",
                                "alias_value": username,
                                "platform": platform_name,
                            },
                            confidence=50,
                        )
                    )

                # Stop after first few results to avoid excessive queries
                if len(all_results) >= 8:
                    break
            if len(all_results) >= 8:
                break

        self.logger.info(
            f"Username variation searches found {len(all_results)} results"
        )
        return all_results

    # ------------------------------------------------------------------
    # Email permutation cross-reference
    # ------------------------------------------------------------------

    async def _search_email_permutations(
        self, email: str, full_name: str
    ) -> list[ModuleResult]:
        """Cross-reference email address for alias connections."""
        if not _HAS_DDGS:
            return []

        local_part = email.split("@")[0] if "@" in email else email
        query = f'"{local_part}" -"{full_name}"'

        results: list[ModuleResult] = []

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"DDG email permutation search failed: {exc}")
            return results

        for hit in hits[:5]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="alias_found",
                    title=title or f"Email alias result for {email}",
                    content=snippet or None,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "duckduckgo_dork",
                        "alias_type": "email_permutation",
                        "alias_value": local_part,
                        "platform": "",
                    },
                    confidence=55,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Known alias searches
    # ------------------------------------------------------------------

    async def _search_known_aliases(
        self, aliases: list[str]
    ) -> list[ModuleResult]:
        """Search for each known alias from target metadata."""
        if not _HAS_DDGS:
            return []

        all_results: list[ModuleResult] = []

        for alias in aliases[:5]:
            query = f'"{alias}"'
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(
                    f"DDG known-alias search failed for '{alias}': {exc}"
                )
                continue

            for hit in hits[:3]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="alias_found",
                        title=title or f"Known alias result for {alias}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "alias_type": "known_alias",
                            "alias_value": alias,
                            "platform": "",
                        },
                        confidence=55,
                    )
                )

        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_alias_type(title: str, snippet: str) -> str:
        """Classify the type of alias based on content clues."""
        combined = f"{title} {snippet}".lower()
        if "maiden" in combined:
            return "maiden_name"
        if "formerly" in combined:
            return "former_name"
        if "aka" in combined or "also known as" in combined:
            return "aka"
        if "alias" in combined:
            return "alias"
        return "possible_alias"

    @staticmethod
    def _extract_alias_value(snippet: str, full_name: str) -> str:
        """Try to extract the actual alias value from snippet text."""
        if not snippet:
            return ""

        import re

        # Look for patterns like: aka "SomeName", also known as SomeName
        patterns = [
            r'(?:aka|a\.k\.a\.?|alias)\s*[:\-]?\s*"([^"]+)"',
            r'(?:also known as|formerly known as)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
            r'(?:maiden name)\s*[:\-]?\s*([A-Z][a-z]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, snippet, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                if candidate.lower() != full_name.lower():
                    return candidate

        return ""

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
