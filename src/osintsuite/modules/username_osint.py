"""Enhanced username OSINT module.

Checks 50+ platforms for username existence, extracts bios from select sites,
resolves Gravatar profiles, and generates email permutations for person targets.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

# ---------------------------------------------------------------------------
# Platform registry — 50+ entries
# (platforms that require a numeric ID or have no public profile URL are
#  intentionally omitted: discord, cash_app, roblox, hackthebox)
# ---------------------------------------------------------------------------
PLATFORMS: dict[str, str] = {
    # ---- carried over from social_media module (22) ----
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
    "dev_to": "https://dev.to/{username}",
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
    # ---- additional platforms (30+) ----
    "telegram": "https://t.me/{username}",
    "snapchat": "https://www.snapchat.com/add/{username}",
    "steam": "https://steamcommunity.com/id/{username}",
    "patreon": "https://www.patreon.com/{username}",
    "ko_fi": "https://ko-fi.com/{username}",
    "venmo": "https://account.venmo.com/u/{username}",
    "chess_com": "https://www.chess.com/member/{username}",
    "lichess": "https://lichess.org/@/{username}",
    "hackerone": "https://hackerone.com/{username}",
    "bugcrowd": "https://bugcrowd.com/{username}",
    "replit": "https://replit.com/@{username}",
    "codepen": "https://codepen.io/{username}",
    "dribbble": "https://dribbble.com/{username}",
    "behance": "https://www.behance.net/{username}",
    "fiverr": "https://www.fiverr.com/{username}",
    "about_me": "https://about.me/{username}",
    "linktree": "https://linktr.ee/{username}",
    "substack": "https://{username}.substack.com",
    "goodreads": "https://www.goodreads.com/{username}",
    "gravatar": "https://gravatar.com/{username}",
    "producthunt": "https://www.producthunt.com/@{username}",
    "crunchbase": "https://www.crunchbase.com/person/{username}",
    "kaggle": "https://www.kaggle.com/{username}",
    "huggingface": "https://huggingface.co/{username}",
    "npm": "https://www.npmjs.com/~{username}",
    "pypi": "https://pypi.org/user/{username}/",
    "dockerhub": "https://hub.docker.com/u/{username}",
    "tryhackme": "https://tryhackme.com/p/{username}",
}

# Domains used for email permutation generation
_EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "protonmail.com"]

# Platforms where we attempt lightweight bio extraction
_BIO_EXTRACTORS: dict[str, re.Pattern[str]] = {
    "github": re.compile(
        r'<div[^>]*class="[^"]*p-note[^"]*"[^>]*>\s*<div>(.*?)</div>',
        re.DOTALL,
    ),
    "dev_to": re.compile(
        r'<p[^>]*class="[^"]*profile-header__bio[^"]*"[^>]*>(.*?)</p>',
        re.DOTALL,
    ),
}


class UsernameOsintModule(BaseModule):
    """Enhanced username intelligence across 50+ platforms."""

    name = "username_osint"
    description = (
        "Enhanced username intelligence: 50+ platforms, email permutations, "
        "Gravatar, bio extraction"
    )

    def applicable_target_types(self) -> list[str]:
        return ["username", "person", "email"]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        username = self._get_username(target)
        if not username:
            self.logger.warning("Could not derive a username from target %s", target.id)
            return results

        # 1. Enumerate platforms
        found_platforms: list[str] = []
        for platform, url_template in PLATFORMS.items():
            url = url_template.format(username=username)
            result = await self._check_platform(platform, url, username)
            if result:
                results.append(result)
                if result.data.get("exists"):
                    found_platforms.append(platform)

        # 2. Gravatar lookup (works with email or username)
        email_for_gravatar = (
            target.label if target.target_type == "email" else f"{username}"
        )
        gravatar_result = await self._check_gravatar(email_for_gravatar)
        if gravatar_result:
            results.append(gravatar_result)

        # 3. Email permutations for person targets
        if target.target_type == "person":
            full_name = target.label
            perms = self._generate_email_permutations(full_name)
            if perms:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="email_permutation",
                        finding_type="email_permutation",
                        title=f"Generated {len(perms)} email permutations for {full_name}",
                        content=", ".join(perms[:10]) + ("..." if len(perms) > 10 else ""),
                        data={"permutations": perms},
                        confidence=30,
                    )
                )

        # 4. Summary result
        results.append(
            ModuleResult(
                module_name=self.name,
                source="summary",
                finding_type="username_osint_summary",
                title=f"Username OSINT summary for '{username}'",
                content=(
                    f"Checked {len(PLATFORMS)} platforms. "
                    f"Found on {len(found_platforms)}: "
                    f"{', '.join(found_platforms) if found_platforms else 'none'}."
                ),
                data={
                    "username": username,
                    "platforms_checked": len(PLATFORMS),
                    "platforms_found": found_platforms,
                    "platforms_found_count": len(found_platforms),
                },
                confidence=80,
            )
        )

        self.logger.info(
            "Username '%s' found on %d/%d platforms",
            username,
            len(found_platforms),
            len(PLATFORMS),
        )
        return results

    # ------------------------------------------------------------------
    # Username derivation
    # ------------------------------------------------------------------
    def _get_username(self, target: Target) -> str | None:
        """Derive a username string from various target types."""
        if target.target_type == "username":
            return target.label

        if target.target_type == "person":
            # Check metadata for an explicit username first
            meta_username = target.metadata_.get("username")
            if meta_username:
                return meta_username
            # Derive from full name: lowercase, strip spaces
            return target.label.lower().replace(" ", "")

        if target.target_type == "email":
            # Part before the @
            at_idx = target.label.find("@")
            if at_idx > 0:
                return target.label[:at_idx]
            return target.label

        return None

    # ------------------------------------------------------------------
    # Platform checking
    # ------------------------------------------------------------------
    async def _check_platform(
        self,
        platform: str,
        url: str,
        username: str,
    ) -> ModuleResult | None:
        """HTTP GET against a platform profile URL; optionally extract bio."""
        try:
            response = await self.fetch(url, follow_redirects=False)
        except Exception as exc:
            self.logger.debug("Error checking %s: %s", platform, exc)
            return None

        if response is None:
            return None

        exists = response.status_code == 200
        bio: str | None = None

        # Attempt bio extraction for supported platforms
        if exists and platform in _BIO_EXTRACTORS:
            try:
                match = _BIO_EXTRACTORS[platform].search(response.text)
                if match:
                    # Strip HTML tags from captured group
                    raw_bio = re.sub(r"<[^>]+>", "", match.group(1)).strip()
                    if raw_bio:
                        bio = raw_bio
            except Exception:
                pass  # bio extraction is best-effort

        data: dict[str, Any] = {
            "platform": platform,
            "username": username,
            "url": url,
            "exists": exists,
            "status_code": response.status_code,
        }
        if bio:
            data["bio"] = bio

        return ModuleResult(
            module_name=self.name,
            source=platform,
            finding_type="username_profile",
            title=f"{'Found' if exists else 'Not found'}: {platform}/{username}",
            content=url if exists else None,
            data=data,
            confidence=70 if exists else 30,
        )

    # ------------------------------------------------------------------
    # Gravatar
    # ------------------------------------------------------------------
    async def _check_gravatar(self, email_or_username: str) -> ModuleResult | None:
        """Resolve a Gravatar profile via the JSON API."""
        try:
            hash_input = email_or_username.strip().lower().encode("utf-8")
            md5_hash = hashlib.md5(hash_input).hexdigest()  # noqa: S324
            url = f"https://en.gravatar.com/{md5_hash}.json"

            response = await self.fetch(url)
            if response is None or response.status_code != 200:
                return None

            payload = response.json()
            entry = payload.get("entry", [{}])[0]

            display_name = entry.get("displayName", "")
            bio = entry.get("aboutMe", "")
            profile_url = entry.get("profileUrl", "")
            accounts = [
                {
                    "domain": a.get("domain", ""),
                    "display": a.get("display", ""),
                    "url": a.get("url", ""),
                }
                for a in entry.get("accounts", [])
            ]

            return ModuleResult(
                module_name=self.name,
                source="gravatar",
                finding_type="gravatar_profile",
                title=f"Gravatar profile: {display_name or email_or_username}",
                content=profile_url or None,
                data={
                    "email": email_or_username,
                    "display_name": display_name,
                    "bio": bio,
                    "profile_url": profile_url,
                    "accounts": accounts,
                },
                confidence=75,
            )
        except Exception as exc:
            self.logger.debug("Gravatar lookup failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Email permutations
    # ------------------------------------------------------------------
    @staticmethod
    def _generate_email_permutations(full_name: str) -> list[str]:
        """Generate common email permutations from a full name.

        Expects *full_name* to contain at least a first and last name
        separated by whitespace.  Returns an empty list when the name
        cannot be split.
        """
        parts = full_name.strip().lower().split()
        if len(parts) < 2:
            return []

        first = parts[0]
        last = parts[-1]

        templates = [
            f"{first}.{last}",
            f"{first[0]}.{last}",
            f"{first}{last[0]}",
            f"{last}.{first}",
            f"{first}_{last}",
            f"{first}{last}",
        ]

        permutations: list[str] = []
        for domain in _EMAIL_DOMAINS:
            for tmpl in templates:
                permutations.append(f"{tmpl}@{domain}")

        return permutations
