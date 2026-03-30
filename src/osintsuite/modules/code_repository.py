"""Code repository module — discovers public code repositories on GitHub, GitLab, etc."""

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


class CodeRepositoryModule(BaseModule):
    name = "code_repository"
    description = "Discover public code repositories on GitHub, GitLab, and Bitbucket"

    GITHUB_ORG_API = "https://api.github.com/orgs/{name}/repos?sort=updated&per_page=10"
    GITHUB_USER_API = "https://api.github.com/users/{name}/repos?sort=updated&per_page=10"
    GITLAB_SEARCH_API = "https://gitlab.com/api/v4/projects?search={name}&per_page=10"

    def applicable_target_types(self) -> list[str]:
        return ["organization", "domain", "username"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.label
        if not name:
            self.logger.info("No name available on target, skipping code repository")
            return results

        # Derive search name (strip TLD for domains)
        search_name = self._derive_search_name(name)

        # 1. GitHub API search (org + user)
        results.extend(await self._search_github(search_name))

        # 2. GitLab API search
        results.extend(await self._search_gitlab(search_name))

        # 3. DDG dork searches
        results.extend(await self._search_dorks(name, search_name))

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

        # Summary finding
        github_count = sum(1 for r in deduped if r.finding_type == "github_repo")
        gitlab_count = sum(1 for r in deduped if r.finding_type == "gitlab_repo")

        deduped.append(
            ModuleResult(
                module_name=self.name,
                source="code_repository",
                finding_type="code_repo_summary",
                title=f"Code repository summary for {name}",
                content=(
                    f"Found {github_count} GitHub repo(s), {gitlab_count} GitLab repo(s), "
                    f"and {len(deduped)} total result(s) for \"{name}\"."
                ),
                data={
                    "name": name,
                    "github_count": github_count,
                    "gitlab_count": gitlab_count,
                    "total_results": len(deduped),
                },
                confidence=60,
            )
        )

        return deduped

    # ------------------------------------------------------------------
    # GitHub API
    # ------------------------------------------------------------------

    async def _search_github(self, name: str) -> list[ModuleResult]:
        """Search GitHub for organization/user repositories."""
        results: list[ModuleResult] = []

        for url_template in (self.GITHUB_ORG_API, self.GITHUB_USER_API):
            url = url_template.format(name=name)
            try:
                response = await self.fetch(url)
                if not response or response.status_code != 200:
                    continue

                repos = response.json()
                if not isinstance(repos, list):
                    continue

                for repo in repos[:10]:
                    repo_name = repo.get("full_name", repo.get("name", ""))
                    description = repo.get("description", "") or ""
                    html_url = repo.get("html_url", "")
                    language = repo.get("language", "")
                    stars = repo.get("stargazers_count", 0)
                    forks = repo.get("forks_count", 0)
                    updated = repo.get("updated_at", "")

                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="github_api",
                            finding_type="github_repo",
                            title=f"GitHub: {repo_name} ({stars} stars)",
                            content=(
                                f"Repository {repo_name}: {description[:200]}. "
                                f"Language: {language}. Stars: {stars}. Forks: {forks}."
                            ),
                            data={
                                "repo_name": repo_name,
                                "url": html_url,
                                "description": description,
                                "language": language,
                                "stars": stars,
                                "forks": forks,
                                "updated_at": updated,
                                "source": "github_api",
                            },
                            confidence=70,
                        )
                    )

                if results:
                    break  # Found repos, no need to try user API
            except Exception as exc:
                self.logger.debug(f"GitHub API request failed for {url}: {exc}")

        return results

    # ------------------------------------------------------------------
    # GitLab API
    # ------------------------------------------------------------------

    async def _search_gitlab(self, name: str) -> list[ModuleResult]:
        """Search GitLab for public projects."""
        results: list[ModuleResult] = []
        url = self.GITLAB_SEARCH_API.format(name=name)

        try:
            response = await self.fetch(url)
            if not response or response.status_code != 200:
                return results

            projects = response.json()
            if not isinstance(projects, list):
                return results

            for project in projects[:10]:
                proj_name = project.get("path_with_namespace", project.get("name", ""))
                description = project.get("description", "") or ""
                web_url = project.get("web_url", "")
                stars = project.get("star_count", 0)
                forks = project.get("forks_count", 0)
                updated = project.get("last_activity_at", "")

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="gitlab_api",
                        finding_type="gitlab_repo",
                        title=f"GitLab: {proj_name} ({stars} stars)",
                        content=(
                            f"Project {proj_name}: {description[:200]}. "
                            f"Stars: {stars}. Forks: {forks}."
                        ),
                        data={
                            "repo_name": proj_name,
                            "url": web_url,
                            "description": description,
                            "stars": stars,
                            "forks": forks,
                            "updated_at": updated,
                            "source": "gitlab_api",
                        },
                        confidence=70,
                    )
                )
        except Exception as exc:
            self.logger.warning(f"GitLab API request failed: {exc}")

        return results

    # ------------------------------------------------------------------
    # DDG dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(self, name: str, search_name: str) -> list[ModuleResult]:
        """Search DDG for code repository mentions."""
        if not _HAS_DDGS:
            return []

        results: list[ModuleResult] = []
        queries = [
            f'site:github.com "{search_name}"',
            f'site:gitlab.com "{search_name}"',
            f'site:bitbucket.org "{search_name}"',
        ]

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG code search failed for '{query}': {exc}")
                continue

            for hit in hits[:3]:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="code_mention",
                        title=hit.get("title", f"Code mention for {name}"),
                        content=hit.get("body", None),
                        data={
                            "url": hit.get("href", ""),
                            "snippet": hit.get("body", ""),
                            "source": "duckduckgo_dork",
                        },
                        confidence=55,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_search_name(name: str) -> str:
        """Strip TLD from domain names to get a usable search name."""
        for tld in (".com", ".org", ".net", ".io", ".co", ".dev"):
            if name.endswith(tld):
                return name[: -len(tld)]
        return name

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
