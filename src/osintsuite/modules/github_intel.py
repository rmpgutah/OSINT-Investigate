"""GitHub intelligence: repos, commits, organizations, email discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

_GITHUB_API = "https://api.github.com"


class GithubIntelModule(BaseModule):
    name = "github_intel"
    description = (
        "GitHub intelligence: repos, commits, organizations, email discovery"
    )

    def applicable_target_types(self) -> list[str]:
        return ["username", "email"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        target_type = target.target_type
        label = target.label

        if target_type == "username":
            results.extend(await self._gather_username(label))
        elif target_type == "email":
            results.extend(await self._gather_email(label))
        else:
            return results

        # Summary
        profile_count = sum(1 for r in results if r.finding_type == "github_profile")
        repo_count = sum(1 for r in results if r.finding_type == "github_repo")
        email_count = sum(1 for r in results if r.finding_type == "github_email")

        results.append(
            ModuleResult(
                module_name=self.name,
                source="github",
                finding_type="github_summary",
                title=f"GitHub summary for {label}",
                content=(
                    f"Found {profile_count} profile(s), {repo_count} repo(s), "
                    f"{email_count} commit email(s)."
                ),
                data={
                    "profiles": profile_count,
                    "repos": repo_count,
                    "commit_emails": email_count,
                },
                confidence=70,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Username gathering
    # ------------------------------------------------------------------

    async def _gather_username(self, username: str) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        # Profile
        profile = await self._fetch_profile(username)
        if profile:
            results.append(profile)

        # Repos
        repos = await self._fetch_repos(username)
        results.extend(repos)

        # Commit emails
        emails = await self._fetch_commit_emails(username)
        results.extend(emails)

        return results

    async def _fetch_profile(self, username: str) -> ModuleResult | None:
        """GET /users/{username} — profile information."""
        resp = await self.fetch(
            f"{_GITHUB_API}/users/{username}",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp is None:
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        if "login" not in data:
            return None

        return ModuleResult(
            module_name=self.name,
            source="github",
            finding_type="github_profile",
            title=f"GitHub profile: {data.get('login', username)}",
            content=data.get("bio", "") or None,
            data={
                "login": data.get("login"),
                "name": data.get("name"),
                "bio": data.get("bio"),
                "company": data.get("company"),
                "location": data.get("location"),
                "blog": data.get("blog"),
                "repos": data.get("public_repos"),
                "followers": data.get("followers"),
                "created_at": data.get("created_at"),
                "avatar_url": data.get("avatar_url"),
                "html_url": data.get("html_url"),
            },
            confidence=85,
            raw_response=resp.text,
        )

    async def _fetch_repos(self, username: str) -> list[ModuleResult]:
        """GET /users/{username}/repos — latest repos."""
        resp = await self.fetch(
            f"{_GITHUB_API}/users/{username}/repos?sort=updated&per_page=10",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp is None:
            return []

        try:
            repos: list[dict[str, Any]] = resp.json()
        except Exception:
            return []

        if not isinstance(repos, list):
            return []

        results: list[ModuleResult] = []
        for repo in repos[:10]:
            if not isinstance(repo, dict):
                continue
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="github",
                    finding_type="github_repo",
                    title=f"Repo: {repo.get('full_name', repo.get('name', 'unknown'))}",
                    content=repo.get("description") or None,
                    data={
                        "name": repo.get("name"),
                        "description": repo.get("description"),
                        "language": repo.get("language"),
                        "stars": repo.get("stargazers_count", 0),
                        "forks": repo.get("forks_count", 0),
                        "url": repo.get("html_url", ""),
                        "updated_at": repo.get("updated_at"),
                    },
                    confidence=80,
                )
            )

        return results

    async def _fetch_commit_emails(self, username: str) -> list[ModuleResult]:
        """Extract unique emails from public push events."""
        resp = await self.fetch(
            f"{_GITHUB_API}/users/{username}/events/public?per_page=30",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp is None:
            return []

        try:
            events: list[dict[str, Any]] = resp.json()
        except Exception:
            return []

        if not isinstance(events, list):
            return []

        seen_emails: set[str] = set()
        results: list[ModuleResult] = []

        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "PushEvent":
                continue

            payload = event.get("payload", {})
            commits = payload.get("commits", [])
            for commit in commits:
                if not isinstance(commit, dict):
                    continue
                author = commit.get("author", {})
                if not isinstance(author, dict):
                    continue
                email = author.get("email", "")
                if (
                    email
                    and email not in seen_emails
                    and "noreply" not in email.lower()
                ):
                    seen_emails.add(email)
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="github",
                            finding_type="github_email",
                            title=f"Commit email: {email}",
                            content=f"Email {email} found in push event commits for {username}",
                            data={
                                "email": email,
                                "author_name": author.get("name", ""),
                                "repo": event.get("repo", {}).get("name", ""),
                            },
                            confidence=75,
                        )
                    )

        return results

    # ------------------------------------------------------------------
    # Email-based search
    # ------------------------------------------------------------------

    async def _gather_email(self, email: str) -> list[ModuleResult]:
        """Search GitHub users by email and gather their data."""
        resp = await self.fetch(
            f"{_GITHUB_API}/search/users?q={email}+in:email",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp is None:
            return []

        try:
            data = resp.json()
        except Exception:
            return []

        items = data.get("items", [])
        if not isinstance(items, list) or not items:
            return []

        results: list[ModuleResult] = []
        for user in items[:5]:
            if not isinstance(user, dict):
                continue
            login = user.get("login", "")
            if login:
                results.extend(await self._gather_username(login))

        return results
