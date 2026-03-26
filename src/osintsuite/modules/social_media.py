"""Social media username enumeration module.

Checks if a username exists across popular platforms by probing profile URLs.
Similar to tools like Sherlock but integrated into the suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

PLATFORMS = {
    "github": "https://github.com/{username}",
    "twitter": "https://x.com/{username}",
    "instagram": "https://www.instagram.com/{username}/",
    "linkedin": "https://www.linkedin.com/in/{username}",
    "reddit": "https://www.reddit.com/user/{username}",
    "pinterest": "https://www.pinterest.com/{username}/",
    "tiktok": "https://www.tiktok.com/@{username}",
    "youtube": "https://www.youtube.com/@{username}",
    "facebook": "https://www.facebook.com/{username}",
    "medium": "https://medium.com/@{username}",
    "dev.to": "https://dev.to/{username}",
    "hackernews": "https://news.ycombinator.com/user?id={username}",
    "keybase": "https://keybase.io/{username}",
    "gitlab": "https://gitlab.com/{username}",
    "bitbucket": "https://bitbucket.org/{username}/",
    "stackoverflow": "https://stackoverflow.com/users/?tab=Reputation&filter=all&search={username}",
    "flickr": "https://www.flickr.com/people/{username}/",
    "vimeo": "https://vimeo.com/{username}",
    "soundcloud": "https://soundcloud.com/{username}",
    "spotify": "https://open.spotify.com/user/{username}",
    "twitch": "https://www.twitch.tv/{username}",
    "mastodon": "https://mastodon.social/@{username}",
}


class SocialMediaModule(BaseModule):
    name = "social_media"
    description = "Check username existence across social media platforms"

    def applicable_target_types(self) -> list[str]:
        return ["username", "person"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        username = self._get_username(target)
        if not username:
            return results

        for platform, url_template in PLATFORMS.items():
            url = url_template.format(username=username)
            result = await self._check_platform(platform, url, username)
            if result:
                results.append(result)

        found = sum(1 for r in results if r.data.get("exists"))
        self.logger.info(
            f"Username '{username}' found on {found}/{len(PLATFORMS)} platforms"
        )
        return results

    def _get_username(self, target: Target) -> str | None:
        """Extract username from target."""
        if target.target_type == "username":
            return target.label
        # For person targets, check metadata for username
        return target.metadata_.get("username")

    async def _check_platform(
        self, platform: str, url: str, username: str
    ) -> ModuleResult | None:
        """Check if a profile exists at the given URL."""
        response = await self.fetch(url, follow_redirects=False)
        if not response:
            return None

        exists = response.status_code == 200
        return ModuleResult(
            module_name=self.name,
            source=platform,
            finding_type="social_profile",
            title=f"{'Found' if exists else 'Not found'}: {platform}/{username}",
            content=url if exists else None,
            data={
                "platform": platform,
                "username": username,
                "url": url,
                "exists": exists,
                "status_code": response.status_code,
            },
            confidence=70 if exists else 30,
        )
