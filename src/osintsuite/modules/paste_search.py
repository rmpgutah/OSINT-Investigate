"""Paste site and code repository search module — finds leaked data and mentions."""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class PasteSearchModule(BaseModule):
    name = "paste_search"
    description = "Search paste sites and code repositories for leaked data and mentions"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def applicable_target_types(self) -> list[str]:
        return ["email", "username", "domain", "person"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        query = self._extract_search_term(target)
        if not query:
            return results

        # Search all sources concurrently
        paste_results, code_results, dork_results = await asyncio.gather(
            self._search_psbdmp(query),
            self._search_github_code(query),
            self._search_dork_pastes(query),
            return_exceptions=True,
        )

        # Collect results, handling any exceptions from gather
        total_pastes = 0
        total_code = 0
        sources_checked: list[str] = []

        if isinstance(paste_results, list):
            results.extend(paste_results)
            total_pastes += len(paste_results)
            sources_checked.append("psbdmp")
        else:
            self.logger.warning(f"psbdmp search failed: {paste_results}")
            sources_checked.append("psbdmp")

        if isinstance(code_results, list):
            results.extend(code_results)
            total_code += len(code_results)
            sources_checked.append("github_code")
        else:
            self.logger.warning(f"GitHub code search failed: {code_results}")
            sources_checked.append("github_code")

        if isinstance(dork_results, list):
            results.extend(dork_results)
            total_pastes += len(dork_results)
            sources_checked.append("dork_pastes")
        else:
            self.logger.warning(f"Dork paste search failed: {dork_results}")
            sources_checked.append("dork_pastes")

        # Summary result
        results.append(
            ModuleResult(
                module_name=self.name,
                source="paste_search",
                finding_type="paste_search_summary",
                title=f"Paste/code search summary for {query}",
                content=(
                    f"Found {total_pastes} paste mentions and "
                    f"{total_code} code mentions across {len(sources_checked)} sources"
                ),
                data={
                    "query": query,
                    "total_pastes": total_pastes,
                    "total_code_mentions": total_code,
                    "sources_checked": sources_checked,
                },
                confidence=55,
            )
        )

        return results

    def _extract_search_term(self, target: Target) -> str | None:
        """Extract the best search term from a target based on its type."""
        if target.target_type == "email":
            return target.label
        elif target.target_type == "username":
            return target.label
        elif target.target_type == "domain":
            return target.label
        elif target.target_type == "person":
            return getattr(target, "full_name", None) or target.label
        return None

    async def _search_psbdmp(self, query: str) -> list[ModuleResult]:
        """Search psbdmp.ws paste dump database."""
        results: list[ModuleResult] = []
        encoded = urllib.parse.quote(query, safe="")
        url = f"https://psbdmp.ws/api/v3/search/{encoded}"

        try:
            response = await self.fetch(url)
            if not response:
                return results

            data = response.json()
            if not isinstance(data, list):
                return results

            for entry in data[:10]:
                paste_id = entry if isinstance(entry, str) else entry.get("id", "")
                if not paste_id:
                    continue
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="psbdmp",
                        finding_type="paste_mention",
                        title=f"Paste mention found: {paste_id}",
                        content=f"Query '{query}' found in paste {paste_id}",
                        data={
                            "paste_id": paste_id,
                            "url": f"https://psbdmp.ws/api/v3/dump/{paste_id}",
                            "query": query,
                        },
                        confidence=65,
                    )
                )
        except Exception as e:
            self.logger.warning(f"psbdmp search error: {e}")

        return results

    async def _search_github_code(self, query: str) -> list[ModuleResult]:
        """Search GitHub code for mentions of the query."""
        results: list[ModuleResult] = []
        encoded = urllib.parse.quote(query, safe="")
        url = f"https://api.github.com/search/code?q={encoded}"
        headers = {"Accept": "application/vnd.github.v3+json"}

        try:
            response = await self.fetch(url, headers=headers)
            if not response:
                return results

            data = response.json()
            items = data.get("items", [])

            for item in items[:10]:
                repo = item.get("repository", {})
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="github_code",
                        finding_type="code_mention",
                        title=f"Code mention: {repo.get('full_name', 'unknown')}/{item.get('name', '')}",
                        content=f"Query '{query}' found in {item.get('path', '')}",
                        data={
                            "repo": repo.get("full_name", ""),
                            "file": item.get("name", ""),
                            "path": item.get("path", ""),
                            "url": item.get("html_url", ""),
                        },
                        confidence=60,
                    )
                )

            # Rate limit: 10 req/min unauthenticated — sleep to be courteous
            await asyncio.sleep(2)

        except Exception as e:
            self.logger.warning(f"GitHub code search error: {e}")

        return results

    async def _search_dork_pastes(self, query: str) -> list[ModuleResult]:
        """Use DuckDuckGo search to find pastes via site-specific dorks."""
        results: list[ModuleResult] = []

        try:
            from duckduckgo_search import DDGS
        except ImportError:
            self.logger.info(
                "duckduckgo_search not installed — skipping dork paste search"
            )
            return results

        dorks = [
            f'site:pastebin.com "{query}"',
            f'site:gist.github.com "{query}"',
        ]

        for dork in dorks:
            try:
                ddgs = DDGS()
                search_results = await asyncio.to_thread(
                    ddgs.text, dork, max_results=5
                )

                for sr in search_results or []:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="dork_search",
                            finding_type="paste_mention",
                            title=sr.get("title", "Paste mention"),
                            content=sr.get("body", ""),
                            data={
                                "url": sr.get("href", ""),
                                "title": sr.get("title", ""),
                                "query": query,
                                "dork": dork,
                            },
                            confidence=65,
                        )
                    )
            except Exception as e:
                self.logger.warning(f"Dork search error for '{dork}': {e}")

        return results
