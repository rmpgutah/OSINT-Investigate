"""Email deep analysis module — validates, checks Gravatar, disposable domains, and web mentions."""

from __future__ import annotations

import asyncio
import hashlib
import socket
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


# Common disposable email domains
_DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwaway.email",
    "yopmail.com", "sharklasers.com", "guerrillamailblock.com", "grr.la",
    "dispostable.com", "trashmail.com", "trashmail.me", "trashmail.net",
    "maildrop.cc", "fakeinbox.com", "temp-mail.org", "tempail.com",
    "mohmal.com", "discard.email", "getnada.com", "emailondeck.com",
    "10minutemail.com", "minutemail.com", "tempr.email", "tempmailo.com",
    "burnermail.io", "mailnesia.com", "spamgourmet.com", "mytemp.email",
    "harakirimail.com", "jetable.org", "crazymailing.com", "tmail.ws",
    "guerrillamail.info", "guerrillamail.net", "guerrillamail.de",
    "guerrillamail.org", "spam4.me", "trash-mail.com", "bugmenot.com",
    "mailexpire.com", "mailcatch.com", "mailforspam.com", "safetymail.info",
    "filzmail.com", "inboxalias.com", "meltmail.com", "spaml.com",
    "nospam.ze.tc", "kurzepost.de", "objectmail.com", "proxymail.eu",
    "rcpt.at", "hulapla.de", "slaskpost.se",
}


class EmailDeepModule(BaseModule):
    name = "email_deep"
    description = "Deep email analysis — validation, Gravatar, disposable check, and web mentions"

    GRAVATAR_API = "https://en.gravatar.com"
    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["email"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        email = getattr(target, "email", "") or target.label or ""
        if not email or "@" not in email:
            self.logger.info("No valid email available on target, skipping email_deep")
            return []

        email = email.strip().lower()
        local_part, domain = email.split("@", 1)

        # Phase 1: Email analysis (disposable check, MX check, Gravatar)
        is_disposable = domain in _DISPOSABLE_DOMAINS
        mx_exists = await self._check_mx(domain)
        has_gravatar, gravatar_data = await self._check_gravatar(email)

        results.append(
            ModuleResult(
                module_name=self.name,
                source="email_analysis",
                finding_type="email_analysis",
                title=f"Email analysis: {email}",
                content=(
                    f"Domain: {domain} | Disposable: {is_disposable} | "
                    f"MX exists: {mx_exists} | Gravatar: {has_gravatar}"
                ),
                data={
                    "title": f"Email analysis: {email}",
                    "url": "",
                    "snippet": f"Domain: {domain}, Disposable: {is_disposable}",
                    "source": "email_analysis",
                    "domain": domain,
                    "is_disposable": str(is_disposable),
                    "has_gravatar": str(has_gravatar),
                    "mx_exists": str(mx_exists),
                },
                confidence=70,
            )
        )

        # Gravatar profile result if found
        if has_gravatar and gravatar_data:
            display_name = gravatar_data.get("displayName", "")
            profile_url = gravatar_data.get("profileUrl", "")
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="gravatar",
                    finding_type="email_registration",
                    title=f"Gravatar profile: {display_name or email}",
                    content=f"Gravatar profile found for {email}",
                    data={
                        "title": f"Gravatar: {display_name or email}",
                        "url": profile_url,
                        "snippet": f"Display name: {display_name}",
                        "source": "gravatar",
                        "domain": domain,
                        "is_disposable": str(is_disposable),
                        "has_gravatar": "True",
                        "mx_exists": str(mx_exists),
                    },
                    confidence=65,
                )
            )

        # Phase 2: DuckDuckGo dork searches
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping DDG dorks for email_deep"
            )
        else:
            dorks = self._generate_dorks(email)
            seen_urls: set[str] = set()
            total_found = len(results)

            for idx, (query, finding_type, confidence) in enumerate(dorks):
                if idx > 0:
                    await asyncio.sleep(3)

                hits = await self._search(query)
                for hit in hits:
                    url = hit.get("href", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    if total_found >= self.MAX_RESULTS:
                        break

                    title = hit.get("title", "")
                    snippet = hit.get("body", "")

                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="duckduckgo",
                            finding_type=finding_type,
                            title=f"Email mention: {title[:120]}",
                            content=snippet[:500] if snippet else None,
                            data={
                                "title": title,
                                "url": url,
                                "snippet": snippet,
                                "source": "duckduckgo",
                                "domain": domain,
                                "is_disposable": str(is_disposable),
                                "has_gravatar": str(has_gravatar),
                                "mx_exists": str(mx_exists),
                            },
                            confidence=confidence,
                        )
                    )
                    total_found += 1

                if total_found >= self.MAX_RESULTS:
                    break

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="email_deep",
                finding_type="email_deep_summary",
                title=f"Email deep analysis for {email} ({len(results)} results)",
                content=None,
                data={
                    "email": email,
                    "domain": domain,
                    "is_disposable": str(is_disposable),
                    "has_gravatar": str(has_gravatar),
                    "mx_exists": str(mx_exists),
                    "total_results": len(results),
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # MX record check
    # ------------------------------------------------------------------

    async def _check_mx(self, domain: str) -> bool:
        """Check if the domain has MX records via DNS lookup."""
        try:
            result = await asyncio.to_thread(self._sync_check_mx, domain)
            return result
        except Exception as exc:
            self.logger.debug(f"MX check failed for {domain}: {exc}")
            return False

    @staticmethod
    def _sync_check_mx(domain: str) -> bool:
        try:
            socket.getaddrinfo(domain, 25, socket.AF_INET, socket.SOCK_STREAM)
            return True
        except socket.gaierror:
            return False

    # ------------------------------------------------------------------
    # Gravatar check
    # ------------------------------------------------------------------

    async def _check_gravatar(self, email: str) -> tuple[bool, dict[str, Any]]:
        """Check if a Gravatar profile exists for the email."""
        md5_hash = hashlib.md5(email.encode("utf-8")).hexdigest()  # noqa: S324
        url = f"{self.GRAVATAR_API}/{md5_hash}.json"

        response = await self.fetch(url)
        if not response:
            return False, {}

        try:
            data = response.json()
            entries = data.get("entry", [])
            if entries:
                return True, entries[0]
        except Exception:
            pass

        return False, {}

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(email: str) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        return [
            (f'"{email}" breach OR leaked OR exposed', "email_breach_mention", 60),
            (f'"{email}" registered OR account', "email_registration", 55),
            (f'"{email}" site:pastebin.com', "email_breach_mention", 60),
            (f'"{email}" resume OR cv', "email_registration", 55),
        ]

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _search(self, query: str) -> list[dict[str, Any]]:
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            return hits
        except Exception as exc:
            self.logger.warning(f"Search failed for dork '{query}': {exc}")
            return []

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=10))
